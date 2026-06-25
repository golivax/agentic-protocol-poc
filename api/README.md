# Protocol Visibility API

Read-only REST API over the protocol engine's state. Reads the `agentic-state`
branch + Actions runs via the GitHub REST API at request time. Never writes state.

## Run

    python3 -m pip install -r api/requirements.txt
    export API_BEARER_TOKEN=...   # token clients must send
    export GITHUB_TOKEN=...       # server-side GitHub token (repo read)
    export GITHUB_REPO=owner/repo
    # optional: STATE_BRANCH (agentic-state), PROTOCOLS_REF (main),
    #           ENGINE_WORKFLOWS (csv of workflow filenames), GITHUB_API_URL
    uvicorn api.main:app --port 8000

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
