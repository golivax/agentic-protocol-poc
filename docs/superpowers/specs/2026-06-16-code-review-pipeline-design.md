# Code-Review Pipeline: Generalize the Engine + Port the Preflight Gate

**Date:** 2026-06-16
**Status:** Approved (design) — pending spec review before planning

## Context

This PoC is a generic agentic-protocol engine (Python) that drives gh-aw agents
through evidence-schema'd protocols with deterministic checks, durable git state,
and a bounded iterate-with-feedback loop. Today it ships two example protocols —
`grumpy-review` (single-agent, the regression baseline) and `multi-grumpy`
(fan-out + join, what `orchestrator.yml` currently deploys).

A second, structurally different gate exists in a sibling repo (`custody`): the
**preflight gate** (`app/backend/component/preflight/`). It is a *one-shot
peer-merge* gate — deterministic facts about a PR (spec/plan present, docs/tests
updated) are computed in a pre-agent step, an agent judges only spec/plan
*adherence*, and a post step merges both into a `verdict.json` rolled up to
`clear`/`blocked`. It has no iteration, no durable state, and no generic
check seam: the deterministic-vs-AI orchestration is hand-wired into two JS
scripts baked into the compiled gh-aw lock.

We want to bring the preflight gate onto this engine **and** chain it in front of
the existing `multi-grumpy` review as a single pipeline — *"a sophisticated code
review pipeline"*: **preflight gate → (clear) → multi-grumpy fan-out → join →
done**; a blocked preflight halts the pipeline before any review runs.

Reaching that exposed three gaps in the engine that this project closes. The
preflight port is the *proof* that a structurally-new protocol drops in by
authoring only **(i)** the gh-aw agent markdown, **(ii)** `protocol.json`, and
**(iii)** the checks — never the orchestrator.

### The three gaps (why this is more than a port)

1. **No verdict model beyond "all checks pass."** Engine checks are binary and any
   non-pass triggers *iterate*. Preflight needs non-blocking `advisory` (warn) and
   non-iterating `block` (blocker) verdicts, plus a `clear`/`blocked` roll-up. The
   roll-up logic currently lives, by accident, inside the *publish* hook
   (`_review.py` reads evidence → `REQUEST_CHANGES`). The "decide" phase is
   implicit and misplaced.
2. **No sequential multi-phase execution.** The engine runs two *hardcoded
   topologies*, not a real state machine. `next.py` does `if is_fanout():
   start_fanout()` — a fresh start on a protocol that *contains* a fan-out jumps
   straight to the fan-out, skipping any preceding gate. `advance.py` on an agent's
   success runs a publish hook for `next`; it never *enters* `next` as a new active
   phase. Chaining preflight → review requires a cursor-based machine that starts
   at the first state and follows `next` regardless of kind, with the gate's
   conclusion controlling progression.
3. **The orchestrator is not protocol-agnostic.** `orchestrator.yml` is the
   *multi-grumpy deployment*: it hardcodes the protocol path (×4), the `/grumpy`
   command, the `multi-grumpy` check-run name, and — most importantly — the
   fan-out+join job topology. A human cannot reasonably hand-author one of these
   per protocol. The orchestrator must become an engine-owned, reusable component
   driven by `protocol.json`.

### Intended outcome

- The engine gains an explicit **VERIFY → DECIDE → CONCLUDE → PUBLISH** pipeline
  and a **cursor-based multi-phase state machine** with conditional gating.
- The orchestrator becomes a **generic, engine-owned reusable workflow** (approach
  B); each protocol carries only a thin, generatable trigger shim.
- The **preflight gate is ported** (checks + agent + evidence + conclude) and
  **chained in front of multi-grumpy** as one `code-review-pipeline` protocol.
- Behavior of `grumpy-review` and `multi-grumpy` is preserved — byte-identical at
  Milestone 1, observably-equivalent at Milestone 2.
- A **live test** in this repo drives the full pipeline end-to-end.

## Decisions (locked during brainstorming)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Engine refactor scope | Full clean architecture: DECIDE + `on_fail` severities + an **optional** conclude/publish seam | The "right" shape; v1 stays byte-identical (the conclude hook is opt-in — existing combined publish hooks are untouched) |
| 2 | Pipeline shape | Approach **X** — one `protocol.json`, engine generalized to sequential multi-phase | Matches "single, simple protocol.json"; the engine becomes what it claims to be |
| 3 | Gate-on-blocked | `on_blocked: halt` — blocked preflight stops the pipeline; no review runs | "Once it passes, follow multi-grumpy" |
| 4 | Orchestrator | Approach **B** — engine-owned reusable `workflow_call` + thin per-protocol trigger shim | ~95% of the win at a fraction of the routing/concurrency complexity; shim is generatable. Full self-routing is backlog (B→A) |
| 5 | Phase transition | **Decision A** — `advance` fires `repository_dispatch protocol-advance`; orchestrator re-enters `plan` at the new phase | Reuses the "events are wake-ups" dispatch pattern (`protocol-continue`/`protocol-join`) |
| 6 | State layout | **Decision B** — multi-phase instance is a dir with `_instance.yaml` phase cursor; existing single-/fan-out layouts unchanged | Keeps the regression guard strong; generalization is additive |
| 7 | Check-runs | **Decision C** — one aggregate `code-review-pipeline` run + per-phase sub-runs | Mirrors today's aggregate-vs-sub fan-out model |
| 8 | Checks to port | All *implemented* custody checks; skip `local-review-evidence` (`todo`) | The implemented gate, faithfully |
| 9 | `verdict.json` | `conclude` also emits the custody-shaped `verdict.json` (`records[]` + `meta`) | Forward-compat with the custody app, though that path is untested here |
| 10 | LLM endpoint | Reuse the existing Claude `sonnet` endpoint + `ANTHROPIC_*` secrets | Not custody's codex/gpt-5.5 gateway |
| 11 | Delivery | One spec; implementation plan sequenced into 3 milestones | Scope is large and coupled; isolate risk (M2) from authoring (M3) |

## Architecture

### The phase pipeline (the four boxes, made explicit)

```
VERIFY (run-checks.py, zone 3)
  → DECIDE (lib.decide, pure: verdicts+severities → process, blocking)
    → CONCLUDE (zone 4, reads evidence: → conclusion, summary, payload)
      → PUBLISH (zone 4, side-effects only: POST / check-run / artifact)
        → ADVANCE PHASE (cursor follows `next`, gated by conclusion)
```

DECIDE owns the **process axis** (`iterate`/`done`/`failed`). CONCLUDE owns the
**conclusion axis** (`clear`/`blocked`, `APPROVE`/`CHANGES_REQUESTED`), the one
place allowed to read evidence *substance* — which the engine never judges in
zone 3. CONCLUDE's output feeds two consumers: the check-run **and** the phase
transition.

### Milestone 1 — DECIDE + `on_fail` severities + conclude/publish split

**`run-checks.py`** stamps each verdict with its `on_fail` from the protocol entry
(default `"iterate"`); verdict grows from `{check,pass,feedback}` to
`{check,pass,feedback,on_fail}` (additive — old readers ignore it).

**`lib.decide(results, iterations_remaining) → (process, blocking)`** — pure,
unit-testable:

```python
def decide(results, iterations_remaining):
    if not results:                                  # checks job produced nothing
        return ("iterate" if iterations_remaining else "failed"), False
    sev = lambda r: r.get("on_fail", "iterate")
    iterate_fail = any(not r.get("pass") and sev(r) == "iterate" for r in results)
    block_fail   = any(not r.get("pass") and sev(r) == "block"   for r in results)
    process = ("iterate" if iterations_remaining else "failed") if iterate_fail else "done"
    return process, block_fail
```

`advisory` fails are recorded but never affect `process` or `blocking`.

**`advance.py`** replaces the inline `all_pass`/`iter_<max` ladder with
`decide()`. Feedback fed back to the agent filters to `iterate`-severity fails
only (advisory/block fails are not things the agent can fix by re-running).

**conclude/publish seam (opt-in).** The engine gains support for an optional
`conclude` hook alongside `publish`:
- `conclude(evidence, blocking) → {conclusion, summary, payload}` — substance
  roll-up, **no GitHub writes**.
- `publish(payload) → POST review / set check-run / write artifact` — side-effects
  only.

**Back-compat is the default:** a state with no `conclude` field behaves exactly
as today — `publish` does both jobs (computes its own conclusion from evidence and
performs the side-effects), receiving the existing arguments. So `grumpy-review`
and `multi-grumpy` are **untouched** in M1: their `_review.py`/`publish-*.py`
hooks keep working as combined hooks. Only `preflight` (M3) opts into the split,
because the gate needs the conclusion *before* (and independently of) any side
effect, to drive the phase transition. Splitting grumpy's `_review.py` into
conclude + publish halves is available as optional later polish, not required
here.

**Regression guard:** every existing check defaults to `on_fail: "iterate"`, so
`decide()` reproduces today's outcomes exactly (empty results → failed attempt;
any fail with room → iterate; exhausted → failed; all pass → done); and with no
`conclude` field, publication is unchanged. `test_engine.py`/`test_publish.py`
pass unchanged; this milestone makes **no** production behavior change.

### Milestone 2 — Cursor-based multi-phase state machine + generic orchestrator (B)

**`protocol.json` schema enrichment** (all additive; absence = today's behavior):

```jsonc
{
  "name": "code-review-pipeline",
  "triggers": [                                                  // M2 (see orchestrator)
    { "on": "issue_comment", "comment_prefix": "/review", "command": "start" },
    { "on": "pull_request",  "actions": ["opened","reopened"],  "command": "start" },
    { "on": "pull_request",  "actions": ["synchronize"],        "command": "reset" }
  ],
  "states": [
    { "id": "preflight", "kind": "agent",  "workflow": "preflight-agent",
      "evidence": "preflight.evidence.schema.json", "max_iterations": 2,
      "params": { "ai_checks": ["spec-adherence","plan-adherence"] },
      "checks": [
        { "run": "schema-valid",           "on_fail": "iterate"  },
        { "run": "adherence-coverage",     "on_fail": "iterate"  },
        { "run": "traces-exist-in-diff",   "on_fail": "iterate"  },
        { "run": "spec-present",           "on_fail": "advisory" },
        { "run": "plan-present",           "on_fail": "advisory" },
        { "run": "docs-updated-with-code", "on_fail": "advisory" },
        { "run": "tests-updated-with-code","on_fail": "advisory" }
      ],
      "conclude": "conclude-preflight", "publish": "publish-verdict",
      "on_blocked": "halt", "next": "review" },

    { "id": "review", "kind": "fanout",
      "branches": [ /* grumpy + security, verbatim from multi-grumpy */ ],
      "next": "join" },

    { "id": "join", "kind": "join", "of": "review", "next": "done" }
  ]
}
```

> **Where preflight's "blocked" comes from.** All of preflight's zone-3
> fact-checks are `advisory` — faithfully reproducing custody, where a missing
> spec/plan is a non-blocking warn and the adherence checks are *skipped* (not
> failed) when no artifact exists. The actual blocking verdict is the agent's
> **adherence judgment** (spec/plan-adherence = does the code match the declared
> artifact), which is *substance* living in evidence — so it is rolled up by
> `conclude-preflight`, **not** by a zone-3 `block` check (the engine never judges
> substance in zone 3). Consequently `decide()`'s `blocking` output is `False` for
> preflight; the `clear`/`blocked` decision is conclude's. The `block` severity
> still exists in the engine (M1) for protocols with a *deterministic* blocker
> (e.g. a hard "no signed CLA" gate) — preflight simply doesn't use it.

**Planner (`next.py`)** starts at the first state and follows `next` regardless of
kind. The `is_fanout() ⇒ start_fanout()` shortcut is removed; `start_fanout`
becomes "enter a fan-out state" callable both at fresh start (if the first state
is a fan-out) and on a phase transition.

**`advance.py`** gains "enter `next` phase": on an agent gate's `done` +
`conclusion == clear`, it seeds + launches `next` (fires `protocol-advance`); on
`blocked` + `on_blocked: halt`, it terminates the pipeline (aggregate check-run →
`failure`). The fan-out and join phases behave as today within their phase.

**Decision A — phase transition.** `advance` fires
`repository_dispatch event_type=protocol-advance` with
`client_payload {protocol, instance, phase}`. The generic orchestrator routes it
into `plan` at the new phase, which seeds and dispatches it. Consistent with the
existing `protocol-continue`/`protocol-join` re-entry model.

**Decision B — state layout (additive).** A multi-phase instance is a directory
`<pid>/<instance>/` with `_instance.yaml` carrying the **phase cursor** (`phase`,
`head_sha`, `joined`, shared status-comment id). Per phase:
- agent phase → `<pid>/<instance>/<phase>.yaml`;
- fan-out phase → `<pid>/<instance>/<phase>.<branch>.yaml` (generalizes today's
  `<instance>/<branch>.yaml`).

**Single-phase protocols keep their current paths unchanged** — `grumpy-review`
stays `<pid>/<instance>.yaml`, `multi-grumpy` stays `<pid>/<instance>/<branch>.yaml`
+ `_instance.yaml`. The phase-aware layout activates only for protocols with >1
non-terminal phase. `lib.state_file()` gains an optional `phase` arg defaulting to
preserve current behavior.

**Decision C — check-runs.** One aggregate `code-review-pipeline` run stays
`in_progress` until the pipeline terminates: blocked-at-gate → `failure`; else the
review's conclusion. Per-phase sub-runs (`code-review-pipeline/preflight`,
`…/grumpy`, `…/security`) report progress. Mirrors today's aggregate-vs-sub model.

**Generic orchestrator (approach B):**
- Engine-owned `agentic-engine.yml` (`on: workflow_call`; inputs: protocol path,
  command, instance, phase, branch). Runs the full 4-zone graph generically:
  matrix over `branches` (one sentinel entry mapping to `BRANCH=""` for an agent
  phase; N entries for a fan-out phase), a `join` job gated by
  `if: needs.plan.outputs.is_fanout == 'true'`, and **job-level concurrency** keyed
  `protocol·instance·branch`. Trust-zone separation is preserved (jobs keep
  distinct tokens; secrets passed per the reusable-workflow contract).
- A thin, generatable **trigger shim** per protocol declares `on:` (derived from
  the `triggers` block) and calls `agentic-engine.yml`. The existing
  `orchestrator.yml` is renamed `multi-grumpy-trigger.yml` (or replaced by the
  generated shim) with behavior preserved.

**Regression proof for M2:** re-express today's `multi-grumpy` as a one-phase
pipeline run through the generic engine and assert observable equivalence (same
reviews, check-run transitions, state semantics). Because M2 generalizes the state
model, "regression" here means *observable behavior*, not byte-identical internal
state — internal-state assertions in `test_engine.py` are updated where the
representation legitimately changes, with the single-phase layouts (and thus most
assertions) preserved per Decision B.

### Milestone 3 — Preflight port + the combined pipeline + live test

**Checks** (`protocols/<...>/checks/`, Python ports honoring the check ABI):
- `spec-present`, `plan-present` (`advisory` when absent), `docs-updated-with-code`,
  `tests-updated-with-code` (`advisory`) — ports of custody's `checks.js` +
  `locate.js` logic. These read `changed-files`/`diff` and ignore evidence.
- `schema-valid`, `adherence-coverage` (every requested `ai_check` judged exactly
  once — an analogue of `rubric-coverage`), and `traces-exist-in-diff` (reused
  verbatim) — form-checks over the agent's evidence, `on_fail: iterate`.

**Agent (`preflight-agent.md`):**
- Keeps a pre-agent step that prefetches PR data + runs the `locate`/scoping logic
  → writes `spec.txt`/`plan.txt`/`ai-checks.json` (scoping which adherence checks
  to ask is part of producing evidence, and must stay in the agent because the
  engine runs checks *after* the agent).
- Reads `task-context.json` (`pr`, `iteration`, `feedback`) like `grumpy-agent.md`
  — so the iterate loop's feedback reaches it.
- Body writes `/tmp/gh-aw/evidence.json` in the rubric shape (one verdict per
  requested `ai_check`; `issues-found` findings carry verbatim `existing_code` +
  `side`/`line` so `traces-exist-in-diff` works).
- Drops custody's `merge-verdict`/`deterministic-checks`-as-checks post-steps —
  the engine owns checks + verdict. Uploads `evidence.json` only.
- `safe-outputs: staged` / read-only preserved; Claude sonnet endpoint.

**conclude/publish for preflight:**
- `conclude-preflight(evidence, blocking)` — port of `merge-verdict.js` +
  `computeVerdict`: merges the agent's adherence judgments with the trusted
  recomputed facts (the `block`/`advisory` check verdicts), produces
  `clear`/`blocked` (factoring `blocking`), and emits the custody-shaped
  `verdict.json` payload (`records[]` + `meta` echo of `{pr_number, head_sha}`).
- `publish-verdict(payload)` — writes `verdict.json` as an artifact and sets the
  `code-review-pipeline/preflight` sub check-run.

**The combined protocol** assembles the three-phase `protocol.json` (§ above),
reusing the ported preflight phase + the existing grumpy/security branches
verbatim. The `review` phase's branches and per-branch publish hooks are unchanged
from `multi-grumpy`.

**Live test** (this repo):
- A crafted PR with a `## Requirements` section (so adherence fires) and code
  changes (so the fact-checks have signal).
- Trigger the **pipeline** via `/review` (and a `workflow_dispatch` path).
- Clear path: preflight passes → review fan-out runs → join → done. Assert: the
  aggregate `code-review-pipeline` check-run, the `_instance.yaml` cursor walking
  `preflight → review → join → done`, the per-phase sub-runs, the grumpy/security
  reviews posted, and a `clear` `verdict.json` artifact.
- Blocked path: a PR engineered to fail a `block` check → preflight `blocked` →
  pipeline halts, **no review runs**, aggregate check-run `failure`, `blocked`
  `verdict.json`.

## Components and interfaces (unchanged ABIs)

- **Check ABI** unchanged: `<check> <evidence.json> <diff.txt> <changed-files.txt>`
  → `{check,pass,feedback}`, exit 0. `on_fail` is protocol *data*, stamped by the
  runner, not part of the check's stdout.
- **Publish hook** splits into **conclude** (`<hook> <evidence.json>
  <instance-key>`, reads `blocking` via env, prints `{conclusion,summary,payload}`)
  and **publish** (consumes the payload, performs side-effects, prints
  `{conclusion,summary}` for compatibility). Both trusted (zone 4).
- **Evidence** contract unchanged (negative attestation + verbatim traces).

## Testing strategy

- **Unit:** `lib.decide` (all severity combinations + empty results); `on_fail`
  stamping in `run-checks.py`; each ported check (`spec-present` absent/present/
  error, `docs/tests-updated`, `adherence-coverage` missing/dup/ok); conclude/
  publish split for grumpy and preflight; `verdict.json` shape.
- **Regression:** M1 — `test_engine.py` byte-identical. M2 — multi-grumpy
  observably-equivalent through the generic engine (a new
  `test_pipeline_equivalence` module re-expressing it as a one-phase pipeline);
  single-phase state layouts preserved.
- **Multi-phase engine:** new `test_pipeline.py` — cursor progression
  (`preflight → review → join → done`), `on_blocked: halt` terminates before
  review, phase-transition dispatch payloads, `_instance.yaml` shape.
- **Live:** the clear and blocked paths above, in this repo.

## Milestones (the plan will sequence these with checkpoints)

1. **Engine foundations** — DECIDE + `on_fail` severities + the optional
   conclude/publish seam (back-compat: existing hooks untouched). Pure engine;
   byte-identical; no production change. *Checkpoint: full suite green +
   `decide()` unit tests.*
2. **Multi-phase state machine + generic orchestrator (B)** — planner/advance
   generalization, conditional gating, `triggers`, reusable `agentic-engine.yml` +
   trigger shim, both topologies. *Checkpoint: multi-grumpy equivalence + a
   minimal two-phase fixture protocol drives end-to-end in tests.*
3. **Preflight port + combined pipeline + live test** — checks/agent/evidence/
   conclude, the three-phase protocol, live clear + blocked runs. *Checkpoint: live
   test passes both paths.*

## Risks and mitigations

- **M2 touches the credential-holding jobs.** Keep the reusable workflow's trust
  zones identical to today; land multi-grumpy equivalence before authoring any new
  protocol; job-level concurrency must key on `protocol·instance·branch` to avoid
  the cross-branch eviction the current `concurrency` comment warns about.
- **State-layout change risk.** Mitigated by Decision B (existing layouts
  preserved; phase-aware paths only for genuinely multi-phase protocols).
- **Custody adherence-scoping semantics.** The "absent ⇒ advisory + adherence
  skipped, not failed" rule must be ported faithfully (M3); it is the difference
  between "no spec" (advisory) and "declared a spec, code doesn't adhere"
  (blocking).
- **`workflow_call` secret plumbing.** Verify the custom Anthropic endpoint
  secrets reach the agent through the reusable-workflow boundary; the agent lock
  is unchanged but its dispatch now originates from the generic engine.

## Non-goals

- Orchestrator **A** (single self-routing workflow) — backlog (B→A).
- The check-authoring meta-protocol — backlog.
- Custody-app integration / consuming `verdict.json` end-to-end — out of scope
  (we only emit it for forward-compat).
- The codex/gpt-5.5 endpoint — we reuse the Claude endpoint.
- The human-in-the-loop gate (v4) — separate milestone, though `on_blocked`/gating
  is a step toward it.
