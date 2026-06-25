# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An **agentic protocol engine**: a generic, recursive state machine that drives
gh-aw (GitHub Agentic Workflows) agents through a porch-style protocol with
evidence schemas, deterministic transition checks, bounded iterate-with-feedback,
and human gates. You declare a protocol as data; one engine interprets it for every
shape (single-agent, fan-out, multi-phase, sub-pipeline, arbitrarily-nested trees).

> The repo name still says `poc` — it has outgrown that. Treat it as a reusable
> engine + protocol library, not a throwaway. See `README.md` for the
> product-level overview.

Three protocols ship under `.github/agent-factory/protocols/`:
- **`code-review`** — the production pipeline: `preflight` (agent, pre-flight gate)
  → `review` (fanout to `grumpy` + `security` legs) → `join` (AND-barrier) →
  `approval` (human gate) → done. A live `/review` runs it via the router
  (`agentic-orchestrator.yml`), which selects the protocol through `lib.route`
  scanning `protocol.json` `triggers` blocks at runtime.
- **`recover-mental-model-stub`** — sub-pipeline branches + a data-carrying gate
  (`/recover`, then `/answer qID: value`). A capability example with stub agents.
- **`deep-review-stub`** — a depth-4 nested fan-out/sub-pipeline tree
  (`/deep-review`), exercising the recursive engine. Stub agents.

Two other distribution-facing components live beside the engine:
- **`dist/`** — an installer that drops the engine + chosen protocols into any repo
  (`dist/README.md`).
- **`api/`** — a read-only FastAPI visibility service (live status/stats of a
  `<protocol, PR>` run). It reads state as a data contract; it never imports the
  engine. Under active development on `feat/protocol-visibility-api`.

Minimal engine shapes used in capability/regression testing live under
`tests/fixtures/` (e.g. `cap-single-agent/`, `simple-fanout/`,
`cap-mp-fanout-gate/`, `deep-fanout/`, `gate-deep/`, `too-deep/`).

The deep design rationale lives in `docs/HOW-IT-WORKS.md`; what is/isn't
implemented and why (deviations from the original spec) lives in `docs/STATUS.md`.
**Read `docs/STATUS.md` before extending anything** — many "missing" pieces are
deliberate.

## The core mental model

- **A workflow run is one transition of a state machine whose state lives in git.**
  Durable state (a YAML file on the `agentic-state` branch), ephemeral compute.
- **The engine drives; the agent is dispatched.** Inverted from porch: the agent
  only exists when the engine spawns it, can only affect the world through an
  `evidence.json` artifact the engine's checks inspect, and is gone before the
  engine writes state. It cannot skip the engine.
- **Don't trust prose — demand evidence, check it deterministically.** The
  contract for an agent step is an *evidence schema*. Checks verify the *form* of
  the evidence (every rubric cell has a verdict; every claim's `existing_code`
  anchors to a real diff line) — never the *substance* (whether a finding is
  correct; that's a future judge/human gate).
- **State advances only by fast-forward push (compare-and-swap).** Never
  force-push `agentic-state`.
- **Events are wake-ups, not state carriers.** A trigger only tells the engine to
  look; everything load-bearing is re-derived from the state file.

## Architecture: engine vs. protocol (the key separation)

```
.github/agent-factory/engine/   GENERIC — no protocol-specific logic.
  lib.py               state checkout, cas_push, status-comment upsert,
                       resolve_executable, set_check_run, match_run_by_cid,
                       route, validate_protocol, open_gate, do_answer.
                       Importable module + a `python3 lib.py <subcommand>` CLI
                       (the orchestrator calls the CLI for inline helpers).
  paths.py             the NODE_PATH coordinate: state-file paths for any node
                       at any depth (single, fanout leg, sub-pipeline, nested).
  next.py              pure planner: (state, protocol, command) -> action JSON.
                       Root is a sequence node; enters via enter_root, sequences
                       recursively on NODE_PATH.
  advance.py           the SOLE writer of non-initial state: verdicts -> mutate,
                       publish, CAS-push, re-dispatch. Recursive enter/advance.
  run-checks.py        resolve + run a node's checks (any language) -> verdicts
  join.py              recursive fan-out AND-barrier (bubbles nested joins)

.github/agent-factory/protocols/<name>/   A PROTOCOL — all protocol-specific logic lives here.
  protocol.json        states, checks, transitions, max_iterations (DATA)
  *.evidence.schema.json   the rubric the agent must fill (the CONTRACT)
  checks/*             deterministic checks (any language; see ABI below)
  publish/*            publish hook (trusted, zone 4)

.github/workflows/
  agentic-orchestrator.yml  the router: union static on:, read-only route job
                       (lib.route scans all protocols' triggers), then calls the
                       reusable engine. Replaces the per-protocol trigger shim.
  agentic-engine.yml   reusable on:workflow_call engine — the 4 trust zones
                       (plan→dispatch→checks→advance) for one protocol path.
  preflight-agent.md   gh-aw agent (preflight phase) -> compiled preflight-agent.lock.yml
  grumpy-agent.md      gh-aw agent (review/grumpy leg) -> compiled grumpy-agent.lock.yml
  security-agent.md    gh-aw agent (review/security leg) -> compiled security-agent.lock.yml
  protocol-join.yml    serialized join evaluator
```

**To build a new protocol you write a new `.github/agent-factory/protocols/<name>/` + agent workflow;
you do NOT touch `.github/agent-factory/engine/`.** The engine reads the protocol id from
`protocol.json` `.name`, derives the state path `<protocol-id>/<instance-key>.yaml`,
and resolves checks/publish hooks from the protocol directory.

## The four trust zones (per iteration, in agentic-engine.yml)

The invariant: **the engine and the agent never share a job or a credential.**

| Zone | Job | Holds | Runs agent code? |
|------|-----|-------|------------------|
| 1. Engine-pre | `plan` | state-branch PAT | no — `next.py` |
| 2. Agent | `dispatch` → gh-aw workflow | read-only repo token + LLM creds | yes, sandboxed |
| 3. Checks | `checks` | nothing (read-only default token) | no — over evidence + independently re-fetched diff |
| 4. Engine-post | `advance` | state PAT + publish token | no — reads check verdicts only |

The checks job re-fetches `gh pr diff` itself — it never trusts agent-produced
data. The advance job reads only check *verdicts* to decide; it reads evidence
only to *render* the already-decided review.

**Security rule when editing the router or engine:** agent-derived strings
(`feedback`, `verdicts`, filenames) are passed to shell steps via `env:`, NEVER
interpolated into `run:` blocks — otherwise a crafted finding could inject shell
commands into the job holding the state PAT.

## Contracts (ABIs) — keep these stable

- **Check:** an executable invoked as `<check> <evidence.json> <diff.txt> <changed-files.txt>`
  that prints one JSON object `{"check","pass","feedback"}` to stdout and **always
  exits 0** (non-zero is reserved for a genuine runner error). Resolved by
  `run-checks.py` from `protocol.json` `.states[].checks[]`: `exec:` path, else
  `checks/<run>` or `checks/<run>.*` (extension-agnostic — a `.py` check needs no
  bash wrapper). A check reads its node-scoped config (e.g. the rubric
  `categories`) from the `CHECK_PARAMS` env var the runner forwards — the value of
  the check-owning node's `params` object (the branch's when `BRANCH` is set, else
  the state's). Never hardcode the rubric and never reach into `protocol.json`.
  Each check entry may declare `on_fail` (`"iterate"` default | `"advisory"` |
  `"block"`); the runner stamps it onto the verdict, and the engine's `decide()`
  fold uses it — `iterate` drives the retry loop, `block` blocks the conclusion
  without iterating, `advisory` is recorded only.
- **Publish hook:** invoked as `<hook> <evidence.json> <instance-key>` with env
  `ENGINE_LOCAL`, `GITHUB_REPOSITORY`, `PUBLISH_TOKEN`, `PR`; prints
  `{"conclusion","summary"}`. Runs **trusted in zone 4** (NOT a sandboxed check).
- **Evidence:** negative attestation with a trace — "none-found" is legal but must
  carry the `examined` identifiers (so a check confirms the agent read the code);
  findings carry verbatim `existing_code` + a `side`/`line`[/`start_line`] anchor.

## Running tests

Tests are **pytest** modules under `tests/` (`test_*.py`). Shared fixtures live in
`tests/conftest.py` (a bare git repo as a fake `agentic-state` origin, `ENGINE_LOCAL=1`
env, and helpers `run_engine`/`run_check`/`read_state_yaml`). pytest is a dev-only
dependency — it is NOT part of the vendored `.github/agent-factory/` unit, which needs
only Python 3 + PyYAML at runtime.

```bash
# One-time: install dev deps (pytest + PyYAML)
python3 -m pip install -r tests/requirements-dev.txt

# Run the whole suite
pytest tests/ -q

# Run one module, verbose
pytest tests/test_engine.py -v
```

Modules: `test_engine.py` (planner + advance writer + lib CAS, single-agent + fanout
regression guard using `tests/fixtures/`), `test_checks.py`, `test_runchecks.py` (check resolution/robustness),
`test_publish.py`, `test_correlation.py` (cid resolver, pure), `test_join.py`,
`test_fanout_e2e.py`, `test_status_comment.py`. Each is self-contained — pytest's
`tmp_path` gives every test its own throwaway state dir.

## Editing a gh-aw agent

`*-agent.md` is the source; `*-agent.lock.yml` is the **committed compiled
output** — workflows run from the lock. After editing the `.md`, recompile and
commit the lock:

```bash
gh aw compile
```

Key frontmatter facts (see `docs/STATUS.md` for the security rationale):
- `strict: false` + `sandbox.agent: false` — the egress firewall is **deliberately
  disabled** because the custom LLM endpoint can't be carried in AWF's static
  allowlist. This is the biggest security weakening; the agent is still read-only
  and never holds the state PAT. Do not copy to production without restoring it.
- The LLM endpoint is configured under `engine.env` (`ANTHROPIC_BASE_URL` literal
  + `ANTHROPIC_AUTH_TOKEN` from a secret) — gh-aw forwards `engine.env` (not
  top-level `env:`) to the CLI subprocess. Model is pinned to `claude-sonnet-4-6`.
- `run-name` embeds `cid:[<cid>]` so the orchestrator can resolve the exact run it
  launched (the correlation-id resolver — `match_run_by_cid`).

## Operational gotchas

- **State branch is sacred:** advance `agentic-state` only by fast-forward push
  (CAS). Never force-push it. Full audit trail: `git log agentic-state -- <protocol-id>/<instance-key>.yaml`.
- **Keep `agentic-orchestrator.yml`, `agentic-engine.yml`, and the agent locks on the default branch (`main`)** —
  that's where workflows run from for `issue_comment` / `repository_dispatch`.
  Never commit them onto a demo PR branch (it pollutes the reviewed diff).
- `gh secret set NAME --body -` stores the literal `-`, not stdin. Use `--body "$VALUE"`.
- **Test scaffolding:** the `poc:sabotage` label on a PR makes an agent deliberately
  skip/fabricate to demo the failed-check → iterate loop (grumpy: sabotages
  iteration 1 then self-recovers; security: fabricates every iteration → exhausts
  to `failed`).
- Secrets the repo needs: `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`,
  `POC_DISPATCH_TOKEN` (a PAT with repo + workflow scopes — the default
  `GITHUB_TOKEN` deliberately can't trigger workflows or read PR labels).

## Recursive engine — the `NODE_PATH` coordinate

The engine is **one recursive code path** for every protocol shape. The root of a
protocol is a `sequence` node; `start`/`reset` enter via `enter_root`; every phase
transition, fan-out, join, gate, and merge/combine step is driven by the recursive
sequencer on a single variable-length **node-path** (`NODE_PATH` env). There is no
separate single-agent / multi-phase / fan-out code path — they are all the same
stack at different depths. (Historical note: the pre-unification engine used a fixed
`(BRANCH, PHASE, SUBSTATE)` triple; that machinery was deleted in Stage 4a. See
`docs/STATUS.md`.)

A fan-out **branch** is a parallel agent *leg* (or a nested sub-pipeline), not a git
branch. For `code-review`'s `review` phase the legs are `grumpy` + `security`, each
with its own iterate loop + eager publish, joined under a strict AND-barrier (`join`
node) before the `approval` gate.

- **State paths come from `paths.py`** keyed by `NODE_PATH`: a single agent phase →
  `code-review/pr-N/preflight.yaml`; fan-out legs →
  `code-review/pr-N/review.grumpy.yaml` / `review.security.yaml`; a nested fan-out's
  join marker → `<fanout>.__join.yaml`; the root cursor lives in `_instance.yaml`
  (`phase` key), nested cursors in `<seq>.yaml`. Each node writes only its own file,
  so CAS has no write contention.
- **`max_depth`** (default 5) bounds the static tree; `lib.validate_protocol` rejects
  malformed protocols (a `join` naming no in-scope fanout, an agent node missing its
  `workflow`, a gate's `questions_from` naming a non-existent sibling) with an
  actionable error before any state is written.
- **Matrix legs** pass per-leg data (run-id, verdicts) via path-keyed **artifacts**,
  not job `outputs` (which clobber across legs).
- **Two axes are kept orthogonal:** **process** (`done`/`failed` — did checks pass
  within `max_iterations`) vs. **verdict** (APPROVE/CHANGES_REQUESTED). The join gate
  cares only about the process axis.
