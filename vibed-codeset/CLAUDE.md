# .

> A read-only FastAPI visibility service (`api/`) that projects live status, stats, evidence, and human-gate state of an agentic-protocol engine's runs by reading `protocol.json` and on-disk `agentic-state` files over the GitHub REST API — without ever importing the engine.

## ⚡ Before you edit any file: query the knowledge base

This repository ships a per-file knowledge base mined from its git history,
static analysis, test coverage, and co-change relationships. **Before reading or
modifying a source file, run:**

```bash
python .claude/docs/get_context.py <path/to/file>
```

It returns, for that exact file: past bugs and their root causes, an edit
checklist (tests to run, constants to keep consistent), pitfalls with
consequences, the file's key constructs and who calls them (with line numbers),
and the files that historically change together with it (hidden coupling that
imports alone don't reveal).

For a high-level orientation of the whole repo, run
`python .claude/docs/get_context.py .`

Treat this context as authoritative project memory. It tells you which tests to
run after a change and which related files to keep in sync — use it before you
write code, not after.


## Architecture
The service is a thin read-through projection over git-hosted state. `main.py` is the ASGI entrypoint: at import it reads `os.environ` into an immutable `Settings` (`config.py`), constructs a `GitHubClient` (`github_client.py`), and hands both to the `create_app` factory (`app.py`), which returns the FastAPI app. Every HTTP request flows: route handler → `GitHubClient` fetches raw text/tree listings from the `agentic-state` branch and protocols ref → `state_reader.py` (pure, no I/O, no engine import) parses those blobs into response dicts → Pydantic envelopes in `models.py` document the JSON shapes returned to clients. `github_client.py` wraps `httpx.Client` and normalizes upstream failures into typed exceptions (`NotFound`, `RateLimited`, `UpstreamError`) that `app.py`'s exception handlers map to 404/429/502.

## Key Files
- `api/main.py` — ASGI entrypoint — wires Settings → GitHubClient → create_app at import time
- `api/app.py` — FastAPI factory: routes, bearer-auth dependency, protocol-name validation, exception→HTTP mapping
- `api/state_reader.py` — Pure projection layer — parses protocol.json + instance state files into status/stats/evidence/gate dicts
- `api/github_client.py` — Synchronous httpx-based GitHub REST client; raises typed NotFound/RateLimited/UpstreamError
- `api/config.py` — Frozen Settings dataclass with from_env factory — single source of runtime config
- `api/models.py` — Pydantic response envelopes (ProtocolList, InstanceList, GatesResponse, etc.)
- `api/__init__.py` — Empty package marker for the api/ service

## Key Behaviors
- The API is strictly read-only and never imports the engine — it consumes `agentic-state` files as a data contract.
- Instance addressing is dual-keyed: a bare integer path segment maps to `pr-<N>`; any other string (`ref-main`, `ui-<uuid>`) is the instance dir name verbatim.
- Node status folds `state` and `gates.state`: an answered/approved gate reports `done`, an open gate reports `running`; overall status is classified from the instance's `phase_label` (✅→completed, ❌→failed, ⛔→blocked).
- A fanout phase's status is failed if any leg failed, done only if all legs done, else running.
- Upstream GitHub failures are normalized to typed exceptions mapped to HTTP 404/429 (with Retry-After)/502; a global NotFound handler catches blob 404s mid-assembly.
- All data endpoints require a Bearer token equal to `settings.api_bearer_token`; `/healthz` is unauthenticated and returns ok/degraded.

## Commands
- **test**: `uv run pytest tests/ -q` — repo is a uv project; auto-syncs dev deps from uv.lock. Note: summarized tests target the engine, not api/ specifically
- **test**: `pytest api/ -q` — if api/ ships its own tests; unverified from summaries — confirm a tests dir exists first
- **run**: `uvicorn api.main:app --reload` — main.py exposes the ASGI app; see api/README.md for required env vars

## Conventions
- state_reader.py must stay pure: no I/O, no engine import, no GitHub calls — all fetching happens in github_client.py and is passed in as text/dicts.
- Never interpolate a query-sourced protocol name into a GitHub path without `_PROTOCOL_RE` validation (single path segment; blocks `/` and `..` traversal).
- Settings is frozen and built only via `Settings.from_env`; do not read os.environ elsewhere.
- Route handlers raise the typed GitHub exceptions and let app.py's registered handlers map them to HTTP codes — don't hand-roll status codes for upstream errors.
- New response shapes get a Pydantic envelope in models.py; keep additive fields backward-compatible (e.g. `instance` added alongside existing `pr`).

## Gotchas
- `_pr_of` returns None for non-`pr-` instances — every response carrying `pr` must tolerate null for ref/ui-keyed runs.
- A gate node's top-level `state` never leaves the gate id (e.g. `answering`) — you must read `gates.state`, not `state`, or an answered gate reports as forever-running (this was a real bug fix).
- A terminal merge/`done` node (e.g. recover-mental-model's `combine`) writes no own node file; `phase` points at it but the true signal is `phase_label` on `_instance.yaml` — status_projection surfaces it on `head` so a finished run isn't reported as stuck.
- `_instance.yaml` and `*.__join.yaml` are bookkeeping, not node files — `_is_node_file` filters them; adding new sidecar suffixes requires updating those ignore lists.
- The engine records no timestamps in state, so `action_minutes_approx` is derived only from GitHub workflow-run wall-clock (updated_at − run_started_at) and is explicitly approximate.
- `/stats` and `/gates` iterate `pr-<N>` instances only (via `_pr_numbers`); ref/ui-keyed instances are invisible to those aggregate endpoints.

---
*Generated by [codeset-vibing](https://github.com/) — an open reimplementation of codeset.ai. Knowledge mined from git history, AST, tests, and co-change analysis. Query per-file context with `python .claude/docs/get_context.py <file>`.*
