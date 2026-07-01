# Codebase Map

**Analyzed:** 2026-07-01
**Generated At:** 2026-07-01T18:02:12Z
**Map Schema Version:** 2.0
**Analyzed Commit:** 276d7e4fbf1a19943851fe24d76d2debc1f36d0d
**Source File Count:** 357
**Source Fingerprint:** 3d7aceade88eeb9f
**Source Fingerprint Kind:** hash (sha256 over sorted path|size)
**Scope:** project-root
**Root:** /home/runner/work/agentic-protocol-poc/agentic-protocol-poc/target
**Confidence:** HIGH

## Architecture Overview

This is an **agentic protocol engine**: a generic, recursive state machine that drives
gh-aw (GitHub Agentic Workflows) agents through a "porch-style" protocol of evidence
schemas, deterministic transition checks, bounded iterate-with-feedback loops, and human
gates. A protocol is declared as **data** (`protocol.json`); one interpreter engine runs
it for every shape — single-agent, fan-out, multi-phase, sub-pipeline, and arbitrarily
nested trees. The core mental model is that a workflow run is *one transition* of a state
machine whose durable state lives as a YAML file on a dedicated `agentic-state` git
branch; compute is ephemeral, and state advances only by fast-forward (compare-and-swap)
push.

The code is almost entirely **Python 3 (>=3.10)** with no web/UI framework in the engine
itself. Three top-level units sit around the engine: (1) the **engine** under
`.github/agent-factory/engine/` — protocol-agnostic planner/writer/checker modules; (2) a
**protocol library** under `.github/agent-factory/protocols/` — six shipped protocols,
each self-contained (states + evidence schemas + deterministic checks + publish hooks);
and (3) two distribution-facing components — a stdlib-only **installer** (`dist/`) and a
read-only **FastAPI visibility service** (`api/`) that reads engine state as a data
contract without ever importing the engine. GitHub Actions workflows
(`.github/workflows/`) wire it together: a router orchestrator, a reusable engine workflow
enforcing four trust zones, and compiled gh-aw agent lock files.

The defining architectural property is a **strict engine/agent separation across four
trust zones** (plan → dispatch → checks → advance), where the engine and the agent never
share a job or a credential. The agent can only affect the world through an
`evidence.json` artifact that deterministic checks inspect; checks verify the *form* of
evidence (rubric cells filled, claims anchored to real diff lines), never the *substance*.
The engine is **one recursive code path** keyed by a variable-length `NODE_PATH`
coordinate — there is no separate single-agent vs. fan-out logic. Testing is exceptionally
thorough: 63 pytest modules exercise the planner, advance writer, CAS, checks, joins,
recursion, and end-to-end protocol runs against fixtures.

## Language Distribution

| Extension | File Count | % of Codebase |
|-----------|-----------|---------------|
| .py       | 152       | 43%           |
| .md       | 78        | 22%           |
| .json     | 65        | 18%           |
| .yml      | 23        | 6%            |
| .yaml     | 17        | 5%            |
| .txt      | 10        | 3%            |
| .js       | 3         | 1%            |
| .toml     | 1         | <1%           |
| .sh       | 1         | <1%           |

_Percentages are of the 357-file working tree (excludes `.git`)._

## Detected Stack

| Layer | Technology | Evidence |
|-------|-----------|----------|
| Runtime | Python >= 3.10 | `pyproject.toml` `requires-python = ">=3.10"` |
| API framework | FastAPI | `fastapi` in `pyproject.toml` dependencies; `create_app()` in `api/app.py` |
| ASGI server | Uvicorn | `uvicorn` in dependencies; `api/main.py` builds `app` |
| HTTP client | httpx | `httpx` in dependencies; `api/github_client.py` uses `httpx.Client` |
| Serialization | PyYAML | `PyYAML` dependency; engine state + `api/state_reader.py` parse YAML |
| Validation | Pydantic | `from pydantic import BaseModel` in `api/models.py` |
| Test | pytest | `pytest` in dev dependency-group; 63 `tests/test_*.py` modules |
| Test extras | respx, jsonschema | dev group — `respx` (httpx mocking), `jsonschema` (schema validation) |
| Package/env | uv | `uv.lock` present; `[tool.uv] package = false` |
| CI/CD | GitHub Actions | `.github/workflows/` (router + engine + gh-aw agent locks + `lint.yml`) |
| Agent runtime | gh-aw (GitHub Agentic Workflows) | `*-agent.md` sources compiled to `*-agent.lock.yml` |
| Architecture | Data-driven recursive state machine (inferred from engine modules + `protocol.json` DSL) | `next.py`/`advance.py`/`paths.py` recursion on `NODE_PATH` |

## Conventions Detected

- **File naming**: `kebab-case` for protocol/check/workflow files (e.g. `run-checks.py`,
  `push-mental-model.py`, `mm-socratic-phase1-agent.md`); `snake_case` for Python modules
  in `api/` and `tests/` (e.g. `state_reader.py`, `test_dynamic_fanout.py`).
- **Module structure**: strict **engine vs. protocol** separation — generic engine under
  `engine/`, all protocol-specific logic under `protocols/<name>/`. Building a new
  protocol never touches `engine/`.
- **Config location**: environment variables for the API (`api/config.py` `Settings.from_env`);
  no `.env` files committed. Engine behavior toggled via env vars (`ENGINE_LOCAL`,
  `NODE_PATH`, `STATE_BRANCH`, `STATE_REMOTE`, `PUBLISH_TOKEN`).
- **Test approach**: pytest modules under `tests/` (`test_*.py`), self-contained via
  `tmp_path`; shared fixtures in `tests/conftest.py` (bare git repo as fake
  `agentic-state` origin, `ENGINE_LOCAL=1`, `run_engine`/`run_check`/`read_state_yaml`
  helpers). Static protocol/evidence fixtures in `tests/fixtures/`.
- **Import style**: absolute stdlib + top-level package imports (`from api.config import Settings`);
  engine modules import siblings directly (`import paths as _paths`) since they run as
  scripts from the engine dir.
- **Linting/formatting**: `.github/workflows/lint.yml` present; no `.eslintrc`/`.prettierrc`
  (Python-first repo). `pyproject.toml` drives tooling via uv.
- **Contracts (ABIs)**: three stable ABIs — Check (`<check> <evidence.json> <diff.txt>
  <changed-files.txt>` → one JSON object, always exit 0), Publish hook, and Evidence
  (negative attestation with a trace). These are documented in `CLAUDE.md` and must stay
  stable.

## Entry Points

| Type | Path | Evidence |
|------|------|----------|
| API service (ASGI app) | `api/main.py` | Builds `app = create_app(settings, client=...)` for uvicorn |
| API app factory | `api/app.py` | `create_app(settings, client=None) -> FastAPI` |
| Engine planner (CLI) | `.github/agent-factory/engine/next.py` | `next.py <state_workdir> <instance-key> <protocol.json> <command>` |
| Engine writer (CLI) | `.github/agent-factory/engine/advance.py` | `advance.py <state_workdir> <instance-key> <protocol.json> <verdicts.json> <evidence.json>` |
| Engine check runner (CLI) | `.github/agent-factory/engine/run-checks.py` | `run-checks.py <protocol.json> <state-id> <evidence.json> <diff.txt> <changed-files.txt>` |
| Engine shared lib + CLI | `.github/agent-factory/engine/lib.py` | Importable module + `python3 lib.py <subcommand>` |
| Protocol linter | `.github/agent-factory/engine/protocol-lint.py` | `protocol-lint.py <protocol.json> [--no-viz]` |
| Installer | `dist/install.sh`, `dist/resolve.py`, `dist/receipt.py` | Drops engine + protocols into a repo |
| Router workflow | `.github/workflows/agentic-orchestrator.yml` | Union `on:` + route job selecting a protocol |
| Reusable engine workflow | `.github/workflows/agentic-engine.yml` | `on: workflow_call` — the 4 trust zones |

## Functionality Inventory

| Capability | Primary Files | Summary | Confidence |
|------------|---------------|---------|------------|
| Pure planning (state+protocol+command → action) | `engine/next.py` | Reads state/protocol/command, emits action JSON; enters via `enter_root`, sequences recursively on `NODE_PATH`. Never sniffs events. | HIGH |
| State advance / sole writer | `engine/advance.py` | Verdicts → mutate → publish → CAS-push → re-dispatch. Only writer of non-initial state; tolerates a lost init. | HIGH |
| Tree navigation / addressing | `engine/paths.py` | Pure `NODE_PATH` coordinate: state-file paths and structural relations at any depth. No I/O. | HIGH |
| Shared engine library + CLI | `engine/lib.py` | State checkout, `cas_push`, status-comment upsert, `resolve_executable`, `set_check_run`, `match_run_by_cid`, `route`, `validate_protocol`, `open_gate`, `do_answer`, `decide` fold. | HIGH |
| Check resolution + execution | `engine/run-checks.py` | Data-driven, language-agnostic check runner over the Check ABI; forwards `CHECK_PARAMS`. | HIGH |
| Fan-out AND-barrier join | `engine/join.py` | Recursive fan-out join; bubbles nested joins on all-done. | HIGH |
| Protocol validate + visualize | `engine/protocol-lint.py` | Structural (jsonschema, optional) + semantic (`validate_protocol`) validation; ASCII tree render. | HIGH |
| Code-review protocol | `protocols/code-review/` | preflight → review fan-out (grumpy ∥ security) → join → approval gate → done. Production pipeline. | HIGH |
| Mental-model recovery (auto) | `protocols/recover-mental-model/` | 4 parallel methods → join → combine → push to orphan `_mental_model` branch. Non-interactive. | HIGH |
| Mental-model recovery (interactive) | `protocols/recover-mental-model-interactive/` | Same as above but socratic `answering` is a human issue-channel gate. | HIGH |
| Deep nested stub | `protocols/deep-review-stub/` | Depth-4 nested fan-out/sub-pipeline tree exercising recursion; stub agents. | HIGH |
| Dynamic fan-out stub | `protocols/dyn-fanout-stub/` | Runtime expansion (`expand/expand-files`) of fan-out legs from `items.json`. | MEDIUM |
| Auto feature implementation | `protocols/impl-feature-auto/` | design → implement with ledger/read-these-first consistency checks. | MEDIUM |
| Visibility REST API | `api/app.py`, `api/state_reader.py`, `api/github_client.py` | Read-only status/stats over `<protocol, instance>` state; reads GitHub + state branch. | HIGH |
| Distribution installer | `dist/install.sh`, `dist/resolve.py`, `dist/receipt.py` | Resolves referenced agent workflows, installs, writes an install receipt for drift/version checks. | HIGH |

## Module Ownership

| Area | Paths | Responsibilities | Downstream Consumers |
|------|-------|------------------|----------------------|
| Engine (generic) | `.github/agent-factory/engine/` | Plan, advance, check, join, path addressing, protocol validation. No protocol-specific logic. | Every protocol; `agentic-engine.yml` |
| Protocol library | `.github/agent-factory/protocols/*/` | Per-protocol states, evidence schemas, deterministic checks, publish hooks. | Engine (reads `protocol.json`); router |
| Workflows | `.github/workflows/` | Router orchestrator, reusable engine (4 trust zones), gh-aw agent locks, join evaluator, lint. | GitHub Actions runtime |
| Visibility API | `api/` | Read-only FastAPI service over engine state; GitHub client, config, models, state reader. | External dashboards / operators |
| Installer | `dist/` | Install engine + chosen protocols into any repo; receipt-based drift/version compat. | Downstream adopting repos |
| Tests | `tests/`, `tests/fixtures/` | pytest suite + shared fixtures; capability/regression coverage. | CI (`lint.yml`), developers |
| Docs | `docs/`, `README.md`, `CLAUDE.md` | Design rationale, DSL reference, authoring hub, status/deviations. | Contributors, protocol authors |

## Risk Areas

| Area | Risk Level | Why | Recommendation |
|------|-----------|-----|----------------|
| `engine/lib.py` size + churn | HIGH | 1703 lines and the #1 git hotspot (70 changes/90d) — many responsibilities (state, CAS, routing, gates, decide). | Touch carefully; consider splitting responsibilities. Coordinate parallel edits. |
| `engine/next.py` churn | MEDIUM | 794 lines, #2 hotspot (50 changes/90d) — the pure planner, load-bearing for every shape. | Keep pure; add regression tests before changes. |
| `engine/advance.py` | MEDIUM | 720 lines, the SOLE state writer + hotspot (23 changes/90d); holds the CAS/publish logic. | Any change risks state corruption; rely on CAS + tests. |
| Security: egress firewall disabled | HIGH | gh-aw agents run `strict: false` + `sandbox.agent: false` (deliberate — custom LLM endpoint can't be in AWF allowlist). | Documented weakening; restore before production. Agent stays read-only, never holds state PAT. |
| Shell injection surface | MEDIUM | Agent-derived strings (feedback/verdicts/filenames) must go via `env:`, never interpolated into `run:` blocks. | Enforce the security rule when editing router/engine. |
| Technical debt markers | LOW | 15 TODO/FIXME/HACK/XXX across the tree (~0.04/py-file) — well-maintained. | No action needed. |

## Technical Debt Signals

- **TODO/FIXME count**: 15 markers across the tree (density ≈ 0.04/py-file) — LOW.
- **Large files (>500 lines)**: `engine/lib.py` (1703), `tests/test_dynamic_fanout.py`
  (1080), `engine/next.py` (794), `engine/advance.py` (720), `tests/test_phase_labels.py`
  (611), `tests/test_checks.py` (578), `tests/test_runchecks.py` (572),
  `tests/test_cap_security.py` (525), `engine/protocol-lint.py` (507).
- **Files without tests**: engine modules are covered transitively via 63 pytest modules;
  `api/` covered by `tests/api/`. No obvious untested critical engine file.
- **Git hotspots (90d)**: `engine/lib.py` (70), `engine/next.py` (50), `docs/STATUS.md`
  (36), `tests/test-engine.sh` (26), `tests/test_dynamic_fanout.py` (24),
  `engine/advance.py` (23).

## Dependency Risk

**Ecosystem**: Python (uv / pip) — `pyproject.toml` + `uv.lock`
**Direct dependencies**: 4 runtime (`fastapi`, `httpx`, `PyYAML`, `uvicorn`) + 3 dev
(`pytest`, `respx`, `jsonschema`) | **Outdated**: not measured (no network resolution run)

### Outdated Packages
_Not measured — offline analysis; dependencies are unpinned in `pyproject.toml` (resolved via `uv.lock`)._

### Heavy Dependencies
_N/A — heavy-dependency analysis requires Node.js/npm. Skipped for Python._

### Potentially Unmaintained
_None detected — all four runtime deps (FastAPI, httpx, PyYAML, uvicorn) are actively maintained mainstream libraries._

### Dependency Risk Summary
| Metric | Value | Risk Level |
|--------|-------|-----------|
| Outdated packages | Not measured (offline) | LOW (lean, mainstream deps) |
| Major version behind | Not measured | LOW |
| Heavy dependencies | N/A (Python) | LOW |
| Potentially unmaintained | 0 | LOW |

**Note**: The vendored engine (`.github/agent-factory/`) needs only Python 3 + PyYAML at
runtime; FastAPI/httpx/uvicorn are for the `api/` service only. Very lean dependency
surface overall.

## Agent Guidance

Distilled advice for agents working on this codebase:

- **Preferred**: Keep the **engine generic** — put protocol-specific logic under
  `protocols/<name>/`, never in `engine/`. Preserve the three stable ABIs (Check, Publish
  hook, Evidence). Advance state only by CAS (fast-forward) push. Run
  `uv run pytest tests/ -q` after any engine change. Read `docs/STATUS.md` before extending
  anything — many "missing" pieces are deliberate.
- **Avoid**: Never force-push `agentic-state`. Never interpolate agent-derived strings into
  workflow `run:` blocks (use `env:`). Never commit `agentic-orchestrator.yml` /
  `agentic-engine.yml` / agent locks onto a demo PR branch — they must live on `main`. Do
  not hardcode a rubric in a check; read `CHECK_PARAMS`. Do not make a check exit non-zero
  (reserved for runner errors).
- **Touch with care**: `engine/lib.py` (1703 lines, 70 changes/90d), `engine/next.py`
  (planner purity), `engine/advance.py` (sole state writer — corruption risk). Recompile
  gh-aw locks with `gh aw compile` after editing any `*-agent.md`. Note the deliberately
  disabled egress firewall — do not copy to production without restoring it.

## Dependency Graph

**Files analyzed**: engine + api entry points | **Style**: Python `import` / `from … import`

### Fan-in (most imported)
| File | Imported By |
|------|------------|
| `engine/lib.py` | engine scripts (`advance.py`, `next.py`, `run-checks.py`, `join.py`) + `lib` CLI |
| `engine/paths.py` | `lib.py` (`import paths as _paths`), `next.py`, `advance.py`, `join.py` |
| `api/config.py` (`Settings`) | `api/main.py`, `api/app.py`, `api/github_client.py` |
| `api/state_reader.py` | `api/app.py` |
| `api/github_client.py` | `api/main.py`, `api/app.py` |

### Fan-out (most imports)
| File | Import Count |
|------|-------------|
| `api/main.py` | 3 (`config`, `app`, `github_client`) |
| `api/app.py` | 4 (`fastapi`, `config`, `state_reader`, `github_client`) |
| `engine/advance.py` | imports `lib`, `paths`, stdlib (`json/os/subprocess/shutil`) |

### Key Dependency Chains
- `api/main.py → api/app.py → api/state_reader.py` (reads engine state YAML)
- `api/main.py → api/app.py → api/github_client.py → httpx` (GitHub reads)
- `engine/advance.py → engine/lib.py → engine/paths.py` (advance → CAS/decide → addressing)

**External deps**: fastapi, httpx, uvicorn, pydantic, yaml (PyYAML); stdlib json/os/subprocess/hashlib/glob.

## Test Coverage Map

**Test convention**: pytest modules `tests/test_*.py` (63 modules) with shared
`tests/conftest.py` fixtures; static fixtures under `tests/fixtures/`.
**Coverage**: HIGH (estimated) — 63 test modules against ~9 engine/api source areas.
**Source**: Estimated from test file matching (no coverage report file present).

### Files Without Tests
| Source File | Lines | Risk Note |
|-------------|-------|-----------|
| _None critical detected_ | — | Engine covered by planner/advance/checks/join/e2e suites; `api/` covered by `tests/api/`. |

### Critical Untested Files
_No untested critical files detected._ Engine modules (`lib.py`, `next.py`, `advance.py`,
`run-checks.py`, `join.py`, `paths.py`) each have dedicated or e2e coverage
(`test_engine.py`, `test_decide.py`, `test_runchecks.py`, `test_join.py`, `test_paths.py`,
`test_unified_*`, `test_deep_fanout_e2e.py`).

## API Surface

**Framework**: FastAPI | **Routes detected**: 9 | **Resources**: protocols, instances, stats, gates, health

| Method | Path | Handler | File |
|--------|------|---------|------|
| GET | `/healthz` | health probe | `api/app.py` |
| GET | `/protocols` | list protocols | `api/app.py` |
| GET | `/protocols/{protocol}` | protocol detail | `api/app.py` |
| GET | `/protocols/{protocol}/instances` | list instances | `api/app.py` |
| GET | `/protocols/{protocol}/instances/{ident}/status` | instance status | `api/app.py` |
| GET | `/protocols/{protocol}/instances/{ident}/stats` | instance stats | `api/app.py` |
| GET | `/protocols/{protocol}/instances/{ident}/evidence` | instance evidence | `api/app.py` |
| GET | `/stats` | global stats | `api/app.py` |
| GET | `/gates` | open human gates | `api/app.py` |

All routes except `/healthz` require bearer auth (`Depends(require_auth)`). Protocol names
are validated against `^[A-Za-z0-9][A-Za-z0-9._-]*$` to prevent path traversal.

## Config & Environment

**Config files**: `pyproject.toml`, `uv.lock`, `api/requirements.txt`,
`tests/requirements-dev.txt`, `.github/workflows/*.yml`, `.gitignore`, `.gitattributes`
| **Env variables**: API `Settings.from_env` + engine toggles | **Secret exposure**: none committed

### Environment Variables
| Variable | Source | Sensitive |
|----------|--------|-----------|
| `API_BEARER_TOKEN` | `api/config.py` (required) | Yes |
| `GITHUB_TOKEN` | `api/config.py` (required) | Yes |
| `GITHUB_REPO` | `api/config.py` (required) | No |
| `ENGINE_WORKFLOWS` | `api/config.py` (comma list) | No |
| `STATE_BRANCH` / `PROTOCOLS_REF` / `GITHUB_API_URL` | `api/config.py` | No |
| `GITHUB_REPOSITORY` | engine (`os.environ`) | No |
| `ENGINE_LOCAL` | engine + tests (local mode toggle) | No |
| `NODE_PATH` | engine (the recursion coordinate) | No |
| `STATE_BRANCH` / `STATE_REMOTE` | `engine/lib.py` (default `agentic-state`) | No |
| `PUBLISH_TOKEN` / `GH_TOKEN` / `AGENT_RUN_ID` | `engine/advance.py` publish/dispatch | Yes (tokens) |

### Secret Exposure Warnings
No secret exposure issues detected. No `.env` files are committed; secrets are injected via
GitHub Actions secrets (`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `POC_DISPATCH_TOKEN`) and
API env vars at runtime. Bearer tokens and PATs are read from env, never hardcoded.

## Setup / Runbook

| Task | Command or File | Notes |
|------|-----------------|-------|
| Install (dev) | `uv run pytest tests/ -q` (auto-syncs) | uv project; deps from `uv.lock`. Non-uv: `pip install -r tests/requirements-dev.txt` |
| Run full test suite | `uv run pytest tests/ -q` | 63 modules; each self-contained via `tmp_path` |
| Run one module | `uv run pytest tests/test_engine.py -v` | verbose single module |
| Run the API | `uvicorn api.main:app` (see `api/README.md`) | Requires `API_BEARER_TOKEN`, `GITHUB_TOKEN`, `GITHUB_REPO` |
| Lint a protocol | `python3 .github/agent-factory/engine/protocol-lint.py <protocol.json>` | Structural (needs dev `jsonschema`) + semantic |
| Recompile a gh-aw agent | `gh aw compile` | After editing any `*-agent.md`; commit the `.lock.yml` |
| Install engine into a repo | `dist/install.sh` | See `dist/README.md`; writes `.install.json` receipt |

## Pattern Library

3 recurring patterns detected across the engine + api + protocol code.

### Pattern 1: Deterministic check (Check ABI)
- **Type**: service / contract
- **Canonical example**: `protocols/code-review/checks/rubric-coverage.py`
- **Usage count**: ~30 check executables across 6 protocols
- **Guidance**: A check is invoked `<check> <evidence.json> <diff.txt> <changed-files.txt>`,
  prints one JSON object `{"check","pass","feedback"}`, and **always exits 0**. It reads
  node-scoped config from `CHECK_PARAMS`; never hardcodes the rubric, never reaches into
  `protocol.json`.

### Pattern 2: Publish hook (trusted, zone 4)
- **Type**: service
- **Canonical example**: `protocols/code-review/publish/publish-verdict.py`
- **Usage count**: ~10 publish hooks across protocols
- **Guidance**: Invoked `<hook> <evidence.json> <instance-key>` with env `ENGINE_LOCAL`,
  `GITHUB_REPOSITORY`, `PUBLISH_TOKEN`, `PR`; prints `{"conclusion","summary"}`. Runs
  trusted after checks pass.

### Pattern 3: Pure planner / recursive addressing
- **Type**: module
- **Canonical example**: `engine/paths.py` + `engine/next.py`
- **Usage count**: engine-wide (single recursive code path for all shapes)
- **Guidance**: State-file paths and structural relations are derived purely from a
  `protocol` dict + a `NODE_PATH` list of ids. No I/O in `paths.py`; the planner never
  sniffs events — the workflow passes a command.

## Directory Mappings

Standard locations for different file categories:

| Category | Primary Location | Priority | Pattern |
|----------|-----------------|----------|---------|
| engine | `.github/agent-factory/engine/` | explicit | `*.py` |
| protocols | `.github/agent-factory/protocols/` | explicit | `*/protocol.json` |
| checks | `.github/agent-factory/protocols/*/checks/` | explicit | check executables |
| publish hooks | `.github/agent-factory/protocols/*/publish/` | explicit | publish executables |
| evidence schemas | `.github/agent-factory/protocols/*/` | explicit | `*.evidence.schema.json` |
| workflows | `.github/workflows/` | explicit | `*.yml`, `*-agent.md` |
| api | `api/` | explicit | `*.py` |
| tests | `tests/` | explicit | `test_*.py` |
| fixtures | `tests/fixtures/` | explicit | mixed |
| installer | `dist/` | explicit | `*.py`, `install.sh` |
| docs | `docs/` | explicit | `*.md` |
| config | root | inferred | `pyproject.toml`, `uv.lock` |

### Path Enforcement Rules
- **Strictness**: warn
- New engine code goes under `.github/agent-factory/engine/`; new protocol code under
  `.github/agent-factory/protocols/<name>/` (never in `engine/`).
- Exceptions require explicit override.

## Retrieval Artifacts

- **Index**: `.planning/codebase/index.jsonl`
- **Symbols**: `.planning/codebase/symbols.json`
- **Search protocol**: `.planning/codebase/search.md`
