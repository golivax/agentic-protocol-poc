# Agentic Protocol Engine

A generic state machine for running **reliable, multi-step agentic processes on
GitHub**. You declare a *protocol* as data — phases, agent steps, deterministic
checks, human gates — and a single engine drives it: it spawns each agent in a
sandbox, demands a structured **evidence** artifact, verifies that evidence with
code (never prose), and advances durable state only when the checks pass.

This repository bundles three things:

1. **The engine** — protocol-agnostic Python that plans, dispatches, checks, and
   advances state (`.github/agent-factory/engine/`).
2. **A set of ready-to-use protocols** you can install and learn from
   (`.github/agent-factory/protocols/`) — most notably a production
   **`code-review`** pipeline.
3. **A distribution installer** (`dist/`) that drops the engine + the protocols
   you choose into any repo, and a read-only **visibility API** (`api/`) for
   observing live runs.

> New here? Read [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md) for the full
> design rationale and a protocol-authoring walkthrough, and
> [`docs/STATUS.md`](docs/STATUS.md) for what is/isn't implemented and why.

---

## Why it exists

Two existing systems each solve half of the problem:

- **gh-aw** (GitHub Agentic Workflows) compiles a markdown agent into a sandboxed
  Action: read-only credentials, and anything it changes goes through
  schema-validated *safe-outputs*. Great **spatial** control (what one run may
  emit) — but each run is a single, stateless invocation. A multi-step process
  lives only as natural-language hope in the prompt.
- **porch** is a deterministic protocol engine: phases, checks, and gates declared
  as data, state in a git-committed file, a pure planner deciding the next step.
  Great **temporal** control (when a process may advance) — but the agent drives,
  so porch's determinism depends on the agent choosing to consult it.

This engine is the synthesis: keep gh-aw's sandbox for each step, put porch's
planner in charge of *when* the process advances, and **invert the control model
so the engine drives and the agent is dispatched.** The agent can't skip the
engine — it only exists when the engine spawns it, can only affect the world
through an `evidence.json` the engine's checks inspect, and is gone before the
engine writes state.

The one principle that ties it together:

> **Don't trust prose — demand evidence, and check it deterministically.**

---

## Core mental model

- **A workflow run is one transition of a state machine whose state lives in git.**
  Durable state (a YAML file on the `agentic-state` branch), ephemeral compute.
- **The engine drives; the agent is dispatched.** The agent's only output is an
  evidence artifact; the engine's checks verify its *form* (every rubric cell has
  a verdict, every claim anchors to a real diff line), never its *substance*.
- **State advances only by fast-forward push (compare-and-swap).** The
  `agentic-state` branch is append-only history; it is never force-pushed.
- **Events are wake-ups, not state carriers.** A trigger only tells the engine to
  look; everything load-bearing is re-derived from the state file.
- **The engine and the agent never share a job or a credential.** Four trust zones
  per iteration — plan (state PAT) → dispatch (read-only agent) → checks (no write
  token) → advance (state PAT) — so a compromised agent can never reach the writer.

> **No long-lived driver.** Nothing babysits a protocol to completion. Each run
> does **one transition and exits** — to cause the next step it fires a fresh
> `repository_dispatch`; fan-out legs advance independently and re-wake a join;
> even a human gate opens and ends the run (the later `/approve` or `/answer` is a
> new wake-up). See
> [`docs/HOW-IT-WORKS.md` — execution model](docs/HOW-IT-WORKS.md#execution-model-no-long-lived-driver).

---

## The bundled protocols

Each protocol is a self-contained directory of data + checks under
`.github/agent-factory/protocols/<name>/`. The engine selects one at runtime by
scanning every protocol's `triggers` block.

| Protocol | Shape | Triggers | What it demonstrates |
|----------|-------|----------|----------------------|
| **`code-review`** | `preflight` → `review` fan-out (`grumpy` ∥ `security`) → `join` (AND-barrier) → `approval` (human gate) → done | `/review`, then `/approve` · `/request-changes` · `/reject` · `/override` | The production pipeline: multi-phase, parallel agents with bounded iterate-and-publish, a strict join gate, and a pause-and-require human approval gate. |
| **`recover-mental-model-stub`** | one automated leg ∥ one human-gated **sub-pipeline** → join → merge | `/recover`, then `/answer qID: value` | Sub-pipeline branches and a **data-carrying gate**: the agent asks questions, a human answers, and the answers feed the next step. |
| **`deep-review-stub`** | depth-4 nested fan-out / sub-pipeline tree | `/deep-review` | The recursive engine: arbitrarily-nested fan-outs and sub-pipelines on one `NODE_PATH` coordinate, bounded by `max_depth`. |

The two `-stub` protocols use trivial stand-in agents — they exist to exercise and
illustrate engine capabilities you'd compose in your own protocol. `code-review`
is the real, live-verified one.

---

## Architecture: engine vs. protocol

The hard separation that makes the engine reusable:

```
.github/agent-factory/
  engine/                GENERIC — no protocol-specific logic. You never edit this to add a protocol.
    next.py              pure planner: (state, protocol, command) -> action JSON
    advance.py           the sole writer of non-initial state: verdicts -> mutate, publish, CAS-push
    run-checks.py        resolve + run a node's checks (any language) -> verdicts
    join.py              recursive fan-out AND-barrier
    lib.py / paths.py    state checkout, CAS push, status-comment, check-runs, NODE_PATH paths

  protocols/<name>/      A PROTOCOL — all protocol-specific logic lives here.
    protocol.json        states, checks, transitions, gates, max_iterations  (DATA)
    *.evidence.schema.json   the rubric the agent must fill  (the CONTRACT)
    checks/*             deterministic checks (any language; stdin = evidence + diff)
    publish/*            trusted publish hook (zone 4)

.github/workflows/
  agentic-orchestrator.yml   the router: union of static on:, a read-only route job
  agentic-engine.yml         reusable engine — the four trust zones for one protocol path
  *-agent.md / *.lock.yml    the gh-aw agents (source .md, committed compiled lock)
```

The engine is a **single recursive code path** for every protocol shape — single
agent, fan-out, multi-phase, sub-pipeline, and arbitrarily-nested trees all enter
the same `enter_root` → recursive enter/advance/join stack, driven by one
variable-length `NODE_PATH` coordinate. **To build a new protocol you write a new
`protocols/<name>/` directory + agent workflows; you do not touch the engine.**

**Building one?** [`docs/AUTHORING.md`](docs/AUTHORING.md) is the authoring hub —
it threads the [tutorial](docs/HOW-IT-WORKS.md), the
[`protocol.json` field reference](docs/PROTOCOL-DSL.md) (with a JSON Schema you can
wire into your editor), and a `protocol-lint.py` tool that validates a protocol and
draws it — as an indented tree or a BPMN-ish fork/join flow diagram (`--view both`).

---

## Install into your repo

The `dist/` installer drops the engine + the protocol(s) you choose into any repo
(it reuses `gh aw` to compile the agents; your source `.md` is untouched).

```bash
# Prereqs: gh ≥ 2.0 (repo,workflow scopes), the gh-aw extension, Actions enabled.
gh extension install github/gh-aw

git clone https://github.com/<you>/<target> && cd <target>
curl -fsSL https://raw.githubusercontent.com/golivax/agentic-protocol-poc/main/dist/install.sh \
  | bash -s -- install code-review
```

Install several at once (`... install code-review recover-mental-model-stub`),
list what's available (`... list`), or update later (`... update`). The installer
shows exactly what it will write before doing so. Full details:
[`dist/README.md`](dist/README.md).

**Secrets the target repo needs:** `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, and
`POC_DISPATCH_TOKEN` (a PAT with `repo` + `workflow` scopes — the default
`GITHUB_TOKEN` deliberately can't trigger workflows or read PR labels).

---

## Visibility API (read-only)

`api/` is a standalone FastAPI service that gives client projects read-only
visibility into the engine: which protocols are installed, the live status and
stats of a `<protocol, PR>` run, open gates, and engine-wide aggregates. It reads
the GitHub REST API at request time and **interprets the state-YAML as a
read-only data contract** — it never imports the engine and never writes to the
state branch. _Under active development on the `feat/protocol-visibility-api`
branch (with its design spec and `docs/API-BACKLOG.md`); not yet merged to
`main`._

---

## Running tests

The engine ships with no runtime deps beyond Python 3 + PyYAML; tests use pytest
(a dev-only dependency, not part of the vendored unit).

The repo is a [uv](https://docs.astral.sh/uv/) project (`pyproject.toml` +
`uv.lock`). With uv, the dev environment is implicit — `uv run` syncs it:

```bash
uv run pytest tests/ -q              # the whole suite (auto-syncs deps from uv.lock)
uv run pytest tests/test_gate.py -v  # one module, verbose
```

Without uv, install the dev deps into your own environment first:

```bash
python3 -m pip install -r tests/requirements-dev.txt   # one-time: pytest + PyYAML
pytest tests/ -q
```

The suite (400+ tests across `tests/test_*.py`) is the engine's capability
contract: single-agent, simple fan-out, multi-phase, sub-pipeline, depth-4/5 deep
trees, approval and data-carrying gates, `/override`, restart/reset, the inputs
channel, merge/combine, the `max_depth` guard, protocol authoring-error
validation, and the agent-derived-string injection paths. Shared fixtures live in
`tests/conftest.py`.

---

## Security posture

The four-trust-zone split (engine and agent never share a job or credential) is the
core guarantee and is intact: the agent holds only a read-only repo token + the LLM
creds, never the state-branch PAT; the checks job re-fetches the diff itself and
runs with no write token; only the post-checks `advance` job writes state and
publishes.

**One deliberate weakening** in this repo: the bundled agents run with
`strict: false` + `sandbox.agent: false`, disabling gh-aw's egress firewall —
because the custom LLM endpoint can't be carried in AWF's static allowlist. The
agent is still read-only and never holds the state PAT, but its network egress is
unrestricted. **Restore the firewall before any production use with a
publicly-reachable endpoint.** See [`docs/STATUS.md`](docs/STATUS.md) §6 for the
full rationale.

---

## Documentation map

| Doc | What's in it |
|-----|--------------|
| [`docs/AUTHORING.md`](docs/AUTHORING.md) | **Authoring Protocols and Workflows** — the map of everything you need to write a protocol (tutorial, field reference, the validate-and-visualize linter, the authoring-error catalog). Start here to build one. |
| [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md) | Full design rationale + protocol-authoring tutorial. Start here to understand the engine. |
| [`docs/PROTOCOL-DSL.md`](docs/PROTOCOL-DSL.md) | `protocol.json` field reference (every key by node kind) + the JSON Schema. |
| [`docs/STATUS.md`](docs/STATUS.md) | What is/isn't implemented, deviations from the original design, and why. |
| [`docs/BACKLOG.md`](docs/BACKLOG.md) | Engine backlog. |
| [`dist/README.md`](dist/README.md) | Installer usage. |
| [`CLAUDE.md`](CLAUDE.md) | Working notes for contributors (and Claude Code). |
</content>
