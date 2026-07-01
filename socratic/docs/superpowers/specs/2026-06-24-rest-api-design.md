# REST API for Protocol Visibility — Design

**Date:** 2026-06-24
**Status:** Approved (brainstorming complete; ready for implementation plan)

## Purpose

Give client projects read-only visibility into the protocol engine: what protocols
it supports, the live status and stats of a given `<protocol, PR>` run, and
engine-wide aggregate stats. A small, simple REST service — not a dashboard backend,
not a control plane (yet).

## Decisions (locked during brainstorming)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Data source | **GitHub REST API at request time** | No DB/sync to operate; always fresh. API holds no state of its own. |
| Stack | **Python 3 + FastAPI** | Matches the engine (Python 3 + PyYAML); typed responses + auto OpenAPI docs. |
| Client auth | **Shared bearer token** | Minimal friction, one env-var check; adequate gate for a PoC. |
| Action minutes | **Approximate from run wall-clock** | `sum(updated_at − run_started_at)` per run — one list call, no per-run timing calls. Good enough for a dashboard number. |
| Code organization | **Standalone `api/` package, no engine import** | Treats state files as a read-only data contract; engine stays a self-contained vendored unit with no web deps. |

## Architecture

A standalone `api/` package. **The API never imports the engine and never writes
to the `agentic-state` branch.** It interprets the state-YAML shape the engine
writes as a documented, read-only data contract.

```
api/
  app.py            FastAPI app: route defs, bearer-token dependency, error→HTTP mapping. Thin.
  github_client.py  All GitHub REST calls. Knows tokens, rate limits, retries. Returns raw JSON/blobs.
                    Methods: get_tree(branch), get_file(path, ref), list_workflow_runs(...).
  state_reader.py   Pure interpretation layer: raw YAML text → typed views of engine state.
                    Knows the state-file layout (the read contract). No HTTP, no FastAPI.
  models.py         Pydantic response models (StatusResponse, InstanceStats, GlobalStats, ...).
  config.py         Env-driven settings, validated at startup.
```

**Boundary rules:**
- Only `github_client` touches the network.
- Only `state_reader` knows the YAML shape.
- `app` orchestrates and never parses YAML itself.
- Nothing in `api/` can mutate state.

**Data flow per request:** `app` validates bearer token → `github_client` fetches the
relevant state blobs/tree (and workflow runs for stats) → `state_reader` interprets
raw YAML into plain dicts → `app` shapes them into `models` and returns JSON.

## State-file read contract (what `state_reader` understands)

Durable state lives on the `agentic-state` branch under
`<protocol-id>/<instance-key>/...`, where `instance-key` is `pr-<N>`:

- A top-level/per-node state file: `state`, `iteration`, `gates`, `head_sha`,
  `history[]`. Each history entry: `{iteration, agent_run_id, checks{name: pass|fail}, feedback}`.
- Per-leg fanout files: `<phase>.<branch>.yaml` (e.g. `review.grumpy.yaml`,
  `review.security.yaml`).
- Shared per-instance bookkeeping: `_instance.yaml` (e.g. the `joined` flag).

The status comment the engine already renders is a projection of `history[]`; the
`/status` and `/stats` endpoints produce a structured version of the same information.

Protocol definitions (for the catalog endpoints) are read from `protocol.json` on the
`PROTOCOLS_REF` branch (default `main`), **not** the state branch.

## Endpoints (in scope)

All endpoints except `/healthz` require `Authorization: Bearer <token>`.

### 1. `GET /protocols`
List the protocols the engine supports (name + version + trigger summary). Source:
`protocol.json` files on `PROTOCOLS_REF`.

### 2. `GET /protocols/{protocol}`
One protocol's definition: triggers and the phase/state graph (each state's `id`,
`kind`, `label`, `checks`, `max_iterations`, and fanout `branches`). Source: that
protocol's `protocol.json`.

### 3. `GET /protocols/{protocol}/instances`
List the instances (PRs) that have runs for this protocol. Source: one recursive
Tree API listing of `<protocol>/` on the state branch. Cheap.

### 4. `GET /protocols/{protocol}/instances/{pr}/status`
Current status of one `<protocol, PR>` — head plus a per-phase projection.

```json
{
  "protocol": "code-review",
  "pr": 75,
  "instance": "pr-75",
  "head": { "phase": "approval", "kind": "gate", "status": "running", "head_sha": "abc1234" },
  "phases": [
    { "id": "preflight", "kind": "agent", "status": "done",
      "iterations": 2, "run_id": "28110616119",
      "checks": { "preflight-schema-valid": "pass", "adherence-coverage": "pass" } },
    { "id": "review", "kind": "fanout", "status": "done",
      "branches": [
        { "id": "grumpy",   "status": "done",   "iterations": 1, "run_id": "28110972887", "checks": {"schema-valid": "pass"} },
        { "id": "security", "status": "failed", "iterations": 3, "run_id": "28110981002", "checks": {"traces-exist-in-diff": "fail"} }
      ] },
    { "id": "approval", "kind": "gate", "status": "running", "gate": { "open": true } }
  ]
}
```

### 5. `GET /protocols/{protocol}/instances/{pr}/stats`
Per-instance stats derived from `history[]`.

```json
{
  "protocol": "code-review", "pr": 75, "instance": "pr-75",
  "state_transitions": 7,
  "total_iterations": 6,
  "iterations_by_phase": { "preflight": 2, "review": 4 },
  "phases_completed": 2, "phases_failed": 1,
  "current_phase": "approval",
  "head_sha": "abc1234"
}
```

- `state_transitions` = total `history[]` entries across the instance's state files.
- `iterations_by_phase` aggregates per phase/leg.

### 6. `GET /stats`
Engine-wide aggregate stats.

```json
{
  "protocols": ["code-review", "deep-review-stub", "recover-mental-model-stub"],
  "instances_total": 42,
  "instances_running": 3,
  "instances_completed": 35,
  "instances_failed": 4,
  "by_protocol": { "code-review": { "total": 30, "running": 2 } },
  "action_minutes_approx": 1287.5,
  "action_minutes_note": "approximate: sum of wall-clock (updated_at − run_started_at) over engine workflow runs"
}
```

- Counts come from walking the state-branch tree (one recursive Tree call) and reading
  each instance's head state to classify running/completed/failed.
- `action_minutes_approx` sums wall-clock duration over runs of the configured
  `ENGINE_WORKFLOWS`.

### 7. `GET /gates?status=open[&protocol=<name>]`
Instances paused on a human gate (approval / `/answer`). Optional `protocol` filter
bounds the scan to one protocol; without it, the scan walks every protocol's instances.

### 8. `GET /healthz`
Unauthenticated liveness/readiness. Confirms the service is up and can reach GitHub.

## Cost notes (honest limits of the request-time model)

- `/stats` reads each instance's head to classify it → one blob fetch per instance
  (N calls). Fine for a PoC; **first thing to cache** if N grows.
- `/gates` without a `protocol` filter scans all instances across all protocols — the
  most expensive endpoint. The `protocol` filter is the bound.
- `action_minutes_approx` is wall-clock, not billed minutes — it will differ from
  GitHub billing (rounding, parallel jobs). The response carries an explicit note.

## Error handling (`app.py` mapping)

| Condition | HTTP | Body |
|-----------|------|------|
| Unknown protocol / PR / instance | `404` | `{"error", "detail"}` |
| Missing/invalid bearer token | `401` | `{"error"}` |
| GitHub rate-limit | `429` | echo `Retry-After` from GitHub |
| GitHub network / 5xx | `502` | `{"error"}` |
| Malformed state YAML (defensive) | `500` | safe message, no raw YAML leaked |

`github_client` raises typed exceptions; `app` maps them. `state_reader` raises on
malformed YAML.

## Configuration (`config.py`, env-driven, validated at startup)

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `API_BEARER_TOKEN` | yes | — | Token clients must present. |
| `GITHUB_TOKEN` | yes | — | Server-side token for GitHub REST calls. |
| `GITHUB_REPO` | yes | — | `owner/repo`. |
| `STATE_BRANCH` | no | `agentic-state` | Branch holding state YAML. |
| `PROTOCOLS_REF` | no | `main` | Branch to read `protocol.json` from. |
| `ENGINE_WORKFLOWS` | no | all repo workflows | Comma-list of workflow filenames (e.g. `agentic-engine.yml,agentic-orchestrator.yml,protocol-join.yml`) counted for action minutes. When unset, all of the repo's workflow runs are summed. |
| `GITHUB_API_URL` | no | `https://api.github.com` | For GHE compatibility. |

## Testing (pytest, matching the repo convention)

- `state_reader` — core logic, tested **purely** against YAML fixtures (copies of real
  `code-review` / fanout state shapes), **zero network**. Most coverage lives here.
- `github_client` — mocked HTTP responses (e.g. `respx` / `responses`); no live GitHub
  calls in CI.
- `app` — FastAPI `TestClient` with a fake/mock client injected via dependency
  override: assert status codes, the auth gate, response schemas, and the 404/401/502/429
  paths.
- Fixtures under `tests/api/fixtures/`; no live GitHub token needed to run the suite.

## Future: write (POST) endpoints — design constraint

Write endpoints are **out of scope now** but the architecture is intentionally
write-ready: a future POST adds write methods to `github_client` and request models to
`models.py` with no restructuring.

**The rule writes must obey:** `advance.py` is the sole writer of non-initial state, and
`agentic-state` advances only by CAS fast-forward. A future POST endpoint must **never**
write YAML to the state branch directly. It acts as a *command initiator* — triggering
the engine the same way a human does today (posting a `/review`-style issue comment, or
firing a `repository_dispatch`) — and lets the engine drive. The API stays outside the
trust zones that hold the state PAT. Auth for writes likely warrants more than the shared
bearer token (a later decision).

## Out of scope

- Any state mutation / control-plane action (see "Future" above).
- Caching / a synced DB (request-time GitHub reads only).
- History-timeline endpoint — deferred to `docs/API-BACKLOG.md`.
- Per-run billed-minute precision (we approximate from wall-clock).
