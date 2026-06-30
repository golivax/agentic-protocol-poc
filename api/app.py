from __future__ import annotations
import re
from fastapi import FastAPI, Depends, Header, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from api.config import Settings
from api import state_reader
from api.github_client import NotFound, RateLimited, UpstreamError

PROTO_DIR = ".github/agent-factory/protocols"
MINUTES_NOTE = ("approximate: sum of wall-clock (updated_at − run_started_at) "
                "over engine workflow runs")
# A protocol id is a single path segment; reject anything that could traverse
# out of the protocols/state dirs when interpolated into a GitHub API path
# (defense-in-depth — query-sourced names can otherwise contain "/" or "..").
_PROTOCOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

def create_app(settings: Settings, client=None) -> FastAPI:
    app = FastAPI(title="Protocol Visibility API")
    app.state.settings = settings
    app.state.client = client

    def cl(request: Request):
        return request.app.state.client

    def require_auth(authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ").strip()
        if not token or token != settings.api_bearer_token:
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def _validate_protocol(name: str):
        if not _PROTOCOL_RE.match(name or ""):
            raise HTTPException(status_code=400, detail="invalid protocol name")

    def _proto_json(client, name):
        _validate_protocol(name)
        try:
            return client.get_text(f"{PROTO_DIR}/{name}/protocol.json", settings.protocols_ref)
        except NotFound:
            raise HTTPException(status_code=404, detail=f"unknown protocol: {name}")

    def _resolve_instance(ident: str) -> str:
        # Back-compat: a bare integer addresses the PR instance `pr-<N>`; any other
        # string (e.g. `ref-main`, `ui-<uuid>`) is the instance dir name verbatim.
        # Our instance dirs are never bare digits, so this is unambiguous.
        return f"pr-{ident}" if str(ident).isdigit() else str(ident)

    def _instance_files(client, protocol, inst):
        _validate_protocol(protocol)
        paths = client.list_tree(f"{protocol}/{inst}/")
        files = {p.split("/")[-1]: client.get_text(p, settings.state_branch) for p in paths}
        if "_instance.yaml" not in files:
            raise HTTPException(status_code=404, detail=f"no instance {protocol} {inst}")
        return files

    def _pr_numbers(client, protocol):
        prs = set()
        for p in client.list_tree(f"{protocol}/"):
            m = re.search(rf"^{re.escape(protocol)}/pr-(\d+)/", p)
            if m:
                prs.add(int(m.group(1)))
        return sorted(prs)

    @app.exception_handler(NotFound)
    def _nf(request, exc: NotFound):
        # NotFound escaping a handler (e.g. a blob 404 mid-assembly) maps to 404,
        # honoring the global "NotFound -> 404" rule even where it isn't caught inline.
        return JSONResponse(status_code=404, content={"error": "not found"})

    @app.exception_handler(RateLimited)
    def _rl(request, exc: RateLimited):
        h = {"Retry-After": exc.retry_after} if exc.retry_after else {}
        return JSONResponse(status_code=429, content={"error": "github rate limit"}, headers=h)

    @app.exception_handler(UpstreamError)
    def _up(request, exc: UpstreamError):
        return JSONResponse(status_code=502, content={"error": "github upstream error"})

    @app.get("/healthz")
    def healthz():
        client = app.state.client
        try:
            if client is not None:
                client.list_dir(PROTO_DIR, settings.protocols_ref)
            return {"status": "ok"}
        except Exception:
            return {"status": "degraded"}

    @app.get("/protocols", dependencies=[Depends(require_auth)])
    def list_protocols(request: Request):
        client = cl(request)
        names = client.list_dir(PROTO_DIR, settings.protocols_ref)
        jsons = []
        for n in names:
            try:
                jsons.append(client.get_text(f"{PROTO_DIR}/{n}/protocol.json", settings.protocols_ref))
            except NotFound:
                continue
        return {"protocols": state_reader.list_protocols(jsons)}

    @app.get("/protocols/{protocol}", dependencies=[Depends(require_auth)])
    def protocol_detail(protocol: str, request: Request):
        return state_reader.protocol_detail(_proto_json(cl(request), protocol))

    @app.get("/protocols/{protocol}/instances", dependencies=[Depends(require_auth)])
    def list_instances(protocol: str, request: Request):
        _proto_json(cl(request), protocol)  # 404 if unknown protocol
        return {"protocol": protocol, "instances": _pr_numbers(cl(request), protocol)}

    # The path segment accepts a bare PR number (back-compat, e.g. `62` → `pr-62`)
    # OR a full instance id (e.g. `ref-main`, `ui-<uuid>`) for ref-targeted runs.
    @app.get("/protocols/{protocol}/instances/{ident}/status", dependencies=[Depends(require_auth)])
    def instance_status(protocol: str, ident: str, request: Request):
        inst = _resolve_instance(ident)
        return state_reader.status_projection(_instance_files(cl(request), protocol, inst))

    @app.get("/protocols/{protocol}/instances/{ident}/stats", dependencies=[Depends(require_auth)])
    def instance_stats(protocol: str, ident: str, request: Request):
        inst = _resolve_instance(ident)
        return state_reader.instance_stats(_instance_files(cl(request), protocol, inst))

    @app.get("/protocols/{protocol}/instances/{ident}/evidence", dependencies=[Depends(require_auth)])
    def instance_evidence(protocol: str, ident: str, request: Request):
        inst = _resolve_instance(ident)
        proj = state_reader.evidence_projection(_instance_files(cl(request), protocol, inst))
        # Keep `pr` (int or null for ref runs) and add the `instance` id (additive).
        return {"protocol": protocol, "pr": state_reader._pr_of(inst),
                "instance": inst, **proj}

    @app.get("/stats", dependencies=[Depends(require_auth)])
    def global_stats(request: Request):
        client = cl(request)
        names = client.list_dir(PROTO_DIR, settings.protocols_ref)
        counts = {"running": 0, "completed": 0, "failed": 0, "blocked": 0}
        by_protocol, total = {}, 0
        for name in names:
            prs = _pr_numbers(client, name)
            by_protocol[name] = {"total": len(prs), "running": 0}
            for pr in prs:
                total += 1
                inst_txt = client.get_text(f"{name}/pr-{pr}/_instance.yaml", settings.state_branch)
                klass = state_reader.classify_instance(inst_txt)
                counts[klass] = counts.get(klass, 0) + 1
                if klass == "running":
                    by_protocol[name]["running"] += 1
        runs = client.list_workflow_runs(settings.engine_workflows)
        return {
            "protocols": names,
            "instances_total": total,
            "instances_running": counts["running"],
            "instances_completed": counts["completed"],
            "instances_failed": counts["failed"],
            "instances_blocked": counts["blocked"],
            "by_protocol": by_protocol,
            "action_minutes_approx": state_reader.sum_run_minutes(runs),
            "action_minutes_note": MINUTES_NOTE,
        }

    @app.get("/gates", dependencies=[Depends(require_auth)])
    def gates(request: Request, status: str = Query("open"),
              protocol: str | None = Query(None)):
        client = cl(request)
        if protocol:
            _proto_json(client, protocol)  # 404 if the named protocol is unknown
        names = [protocol] if protocol else client.list_dir(PROTO_DIR, settings.protocols_ref)
        out = []
        for name in names:
            for pr in _pr_numbers(client, name):
                gv = state_reader.gate_view(_instance_files(client, name, f"pr-{pr}"))
                if gv and (status != "open" or gv["open"]):
                    out.append({"protocol": name, "pr": pr, **gv})
        return {"gates": out}

    return app
