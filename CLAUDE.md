# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

PoC of an **agentic protocol engine**: a generic state machine that drives gh-aw
(GitHub Agentic Workflows) agents through a porch-style protocol with
evidence schemas, deterministic transition checks, and bounded
iterate-with-feedback. A PR review runs on opening a PR or commenting `/grumpy`.

Two example protocols exercise the engine:
- **`grumpy-review`** (`protocols/grumpy/`) — the v1 single-agent PR reviewer.
  Still fully supported by the engine and used as the regression-guard baseline.
- **`multi-grumpy`** (`protocols/multi-grumpy/`) — the v2 fan-out protocol that
  reviews via two parallel agents (`grumpy` + a `security` stub) joined under a
  strict barrier. **This is the protocol `orchestrator.yml` currently deploys**
  (it hardcodes `protocols/multi-grumpy/protocol.json`), so a live `/grumpy` or
  PR-open today runs the fan-out, not the single-agent path.

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
.github/engine/        GENERIC — no protocol-specific logic (only a few grumpy
                       mentions in illustrative comments).
  lib.sh               state checkout, cas_push, status-comment upsert,
                       resolve_executable, set_check_run, match_run_by_cid
  next.sh              pure planner: (state, protocol, command) -> action JSON
  advance.sh           the SOLE writer of non-initial state: verdicts -> mutate,
                       publish, CAS-push, re-dispatch
  run-checks.sh        resolve + run a state's checks (any language) -> verdicts
  join.sh              fan-out AND-barrier (v2)

protocols/<name>/      A PROTOCOL — all protocol-specific logic lives here.
  protocol.json        states, checks, transitions, max_iterations (DATA)
  *.evidence.schema.json   the rubric the agent must fill (the CONTRACT)
  checks/*             deterministic checks (any language; see ABI below)
  publish/*            publish hook (trusted, zone 4)

.github/workflows/
  orchestrator.yml     the 4 trust zones; maps GitHub events -> engine commands
  grumpy-agent.md      gh-aw agent (v1 reviewer) -> compiled grumpy-agent.lock.yml
  security-agent.md    gh-aw agent (v2 security stub) -> security-agent.lock.yml
  protocol-join.yml    serialized join evaluator (v2)
```

**To build a new protocol you write a new `protocols/<name>/` + agent workflow;
you do NOT touch `.github/engine/`.** The engine reads the protocol id from
`protocol.json` `.name`, derives the state path `<protocol-id>/<instance-key>.yaml`,
and resolves checks/publish hooks from the protocol directory.

## The four trust zones (per iteration, in orchestrator.yml)

The invariant: **the engine and the agent never share a job or a credential.**

| Zone | Job | Holds | Runs agent code? |
|------|-----|-------|------------------|
| 1. Engine-pre | `plan` | state-branch PAT | no — `next.sh` |
| 2. Agent | `dispatch` → gh-aw workflow | read-only repo token + LLM creds | yes, sandboxed |
| 3. Checks | `checks` | nothing (read-only default token) | no — over evidence + independently re-fetched diff |
| 4. Engine-post | `advance` | state PAT + publish token | no — reads check verdicts only |

The checks job re-fetches `gh pr diff` itself — it never trusts agent-produced
data. The advance job reads only check *verdicts* to decide; it reads evidence
only to *render* the already-decided review.

**Security rule when editing the orchestrator:** agent-derived strings
(`feedback`, `verdicts`, filenames) are passed to shell steps via `env:`, NEVER
interpolated into `run:` blocks — otherwise a crafted finding could inject shell
commands into the job holding the state PAT.

## Contracts (ABIs) — keep these stable

- **Check:** an executable invoked as `<check> <evidence.json> <diff.txt> <changed-files.txt>`
  that prints one JSON object `{"check","pass","feedback"}` to stdout and **always
  exits 0** (non-zero is reserved for a genuine runner error). Resolved by
  `run-checks.sh` from `protocol.json` `.states[].checks[]`: `exec:` path, else
  `checks/<run>` or `checks/<run>.*` (extension-agnostic — a `.py` check needs no
  bash wrapper). Read the rubric from `protocol.json`, never hardcode it.
- **Publish hook:** invoked as `<hook> <evidence.json> <instance-key>` with env
  `ENGINE_LOCAL`, `GITHUB_REPOSITORY`, `PUBLISH_TOKEN`, `PR`; prints
  `{"conclusion","summary"}`. Runs **trusted in zone 4** (NOT a sandboxed check).
- **Evidence:** negative attestation with a trace — "none-found" is legal but must
  carry the `examined` identifiers (so a check confirms the agent read the code);
  findings carry verbatim `existing_code` + a `side`/`line`[/`start_line`] anchor.

## Running tests

Tests are standalone bash scripts under `tests/` — there is no Makefile, npm, or
CI test runner. Each is self-contained (spins up a bare git repo as a fake
`agentic-state` origin, runs with `ENGINE_LOCAL=1`).

```bash
# Run one suite
./tests/test-engine.sh

# Run all suites
for t in tests/test-*.sh; do echo "== $t =="; bash "$t"; done
```

Note: `tests/test-join.sh` is not chmod +x — invoke it as `bash tests/test-join.sh`.
Suites: `test-engine.sh` (planner + lib CAS), `test-checks.sh`,
`test-runchecks.sh` (check resolution/robustness), `test-publish.sh`,
`test-correlation.sh` (cid resolver, pure), `test-join.sh`, `test-fanout-e2e.sh`,
`test-status-comment.sh`.

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
- **Keep `orchestrator.yml` and the agent locks on the default branch (`main`)** —
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

## v2 — fan-out / join (the `BRANCH` seam)

v2 (`protocols/multi-grumpy/`) adds a `fanout` phase (N parallel agent branches,
each with its own iterate loop + eager publish) and a strict `join` AND-barrier
that gates merge. Design goal: **v1 stays byte-identical.** `next.sh`,
`run-checks.sh`, and `advance.sh` read one env var, `BRANCH`:

- **`BRANCH` empty/unset** → the exact v1 single-agent path (the regression guard).
- **`BRANCH=<id>` set** → the same scripts operate on one fan-out branch (its
  agent unit, check list, publish hook, and per-branch state file).

A "branch" is a parallel agent *leg*, not a git branch. Per-branch state lives at
`multi-grumpy/pr-N/<branch>.yaml` + a shared `_instance.yaml` (`joined` flag) —
each branch writes only its own file, so CAS has no write contention. Matrix legs
pass per-branch data (run-id, verdicts) via branch-named **artifacts**, not job
`outputs` (which clobber across legs). Two axes are kept orthogonal: **process**
(`done`/`failed` — did checks pass within `max_iterations`) vs. **verdict**
(APPROVE/CHANGES_REQUESTED). The join gate cares only about the process axis.
