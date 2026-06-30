# Protocol Visibility API

Read-only REST API over the protocol engine's state. Reads the `agentic-state`
branch + Actions runs via the GitHub REST API at request time. Never writes state.

## Run

    export API_BEARER_TOKEN=...   # token clients must send
    export GITHUB_TOKEN=...       # server-side GitHub token (repo read)
    export GITHUB_REPO=owner/repo
    # optional: STATE_BRANCH (agentic-state), PROTOCOLS_REF (main),
    #           ENGINE_WORKFLOWS (csv of workflow filenames), GITHUB_API_URL
    uv run uvicorn api.main:app --port 8000

`uv run` resolves the project's dependencies (from the repo-root `pyproject.toml` /
`uv.lock`) into an ephemeral environment on the fly — no manual install step.

OpenAPI docs at `/docs`. All endpoints except `/healthz` need `Authorization: Bearer $API_BEARER_TOKEN`.

## Endpoints

- `GET /protocols` — catalog
- `GET /protocols/{protocol}` — definition (state graph)
- `GET /protocols/{protocol}/instances` — PRs with runs
- `GET /protocols/{protocol}/instances/{pr}/status` — current status
- `GET /protocols/{protocol}/instances/{pr}/stats` — per-instance stats
- `GET /stats` — engine-wide stats (action minutes are wall-clock approx)
- `GET /gates?status=open[&protocol=]` — instances paused on a human gate
- `GET /healthz` — liveness/readiness (no auth)

## Notes for clients

- `status` is the instance-level rollup from the engine's `phase_label`
  (`running` · `completed` · `failed` · `blocked`) — the authoritative
  done/not-done signal. Prefer it over inferring completion from the head, since
  a terminal `merge`/`done` node (e.g. `recover-mental-model`'s `combine`) leaves
  `head.phase` pointing at the merge node.
- The `/status` projection is faithful for single-level pipelines/fanouts (e.g.
  `code-review`). `head.kind` may be absent when the head phase has no own node
  file (a `merge`/`done` node, or a deeply-nested head); `head.status` is filled
  from the instance `phase_label` once the run reaches a terminal state, but is
  otherwise absent — treat an absent value as `"unknown"` and fall back to the
  top-level `status`. See `docs/API-BACKLOG.md` (Known limitations).
- The `head` carries run identity so a client can tell a fresh run from a stale
  one: `head.head_sha` (the instance's head commit — changes when a new commit
  re-seeds the run) is always present; `head.run_id`/`head.attempt` pin the
  specific agent run/attempt when the head is a single agent node (absent on gate
  and fanout heads). Every agent leaf (agent phase or fanout branch) also carries
  `run_id`, the run that produced its latest attempt. (No `started_at` — the
  engine records no timestamps in state.)
- `action_minutes_approx` is wall-clock (`updated_at − run_started_at`), not billed
  minutes; it will differ from GitHub billing.
