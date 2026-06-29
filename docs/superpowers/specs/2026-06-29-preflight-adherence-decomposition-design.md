# Preflight Adherence Decomposition (DESIGN)

**Date:** 2026-06-29
**Status:** DESIGN — approved, ready for `writing-plans`.
**Protocol:** `code-review` (`.github/agent-factory/protocols/code-review/`)
**Engine change:** none. The shape relies only on capabilities already present
(fanout, join, `inputs[]`, root-level `conclude` + `on_blocked: halt`).

---

## Goal

Replace the `code-review` protocol's single-agent `preflight` gate — which judges
exactly two verdicts (`spec-adherence`, `plan-adherence`) against the diff in one
agent pass — with a **decomposed preflight**: a fan-out of six independent agentic
subworkflows that judge the full **issue → spec → plan → code** chain (in both the
under- and over-coverage directions), plus mental-model compliance and
docs/tests coherence, with one deterministic gate that halts the pipeline on any
blocking divergence.

The motivating defect (see `## Current state`): today's preflight collapses a
four-artifact chain into two single-direction verdicts judged *directly against the
diff*, never compares the intermediate artifacts to each other, and never fetches
the linked issue. It is therefore blind to exactly the failure the chain is meant
to catch — a change that is *locally* consistent (code matches plan) but *globally*
wrong (the plan faithfully implements a spec that does not solve the issue).

## Current state (what we are replacing)

`preflight` is one `agent` node (`protocol.json:12-34`), gpt-5.5 codex,
`max_iterations: 2`, evidence `preflight.evidence.schema.json`. In one pass the
agent emits at most two verdicts (`preflight-agent.md:103-108`):

- `spec-adherence` — "does the diff achieve what the spec requires?"
- `plan-adherence` — "does the diff follow the plan?"

Both are single-direction ("does code achieve/follow"), both judged against the
diff (the spec↔plan link is never examined), and the linked issue is never
resolved (every prefetch is `gh pr view --json number,title,body,files,headRefOid`).
The deterministic checks verify form only (`adherence-coverage`,
`traces-exist-in-diff`); `spec-present`/`plan-present`/`docs-updated`/
`tests-updated`/`local-review-evidence` are advisory. Because no preflight check is
`block` today, the only path to a halt is an agent adherence verdict of `fail`
(`conclude-preflight.py:23-24`).

`mm-compliance` is a *separate* blocking phase immediately after preflight
(`protocol.json:35-49`).

## Decisions (locked)

1. **Structure — parallel legs + one gate.** `preflight` becomes a `fanout` of six
   legs that run in parallel, an AND-barrier `join`, then a single root-level
   `preflight-gate` agent whose `conclude` hook applies the blocking policy and
   `on_blocked: halt`s. This is forced by the engine (see `## Engine grounding`):
   only a **root-level** phase runs a blocking `conclude`; fan-out legs and
   sub-pipeline nodes cannot self-block. The shape mirrors the protocol's existing
   `review → join-review → triage` pattern (`protocol.json:68-103`).

2. **Blocking policy — block gaps, warn on extras.** The only advisory (non-halting)
   outcomes are the two over-coverage directions (`overspec`, `overplan`).
   Everything else halts: spec-doesn't-solve-issue, missing spec, missing plan,
   `underspec`, `underplan`, mm-divergence, and docs/tests not updated
   appropriately.

3. **No-code PRs.** When a PR changes no code file (`_paths.is_code`), the legs that
   gate code — `plan-implements-spec`, `code-implements-plan`,
   `tests-updated-appropriately` — are **N/A**. `spec-solves-issue` (if issue-linked)
   and `docs-updated-appropriately` still run.

4. **Committed artifacts only.** Spec = a committed file under
   `docs/superpowers/specs/` (the `_paths.is_spec_path` set); plan = a committed file
   under `docs/superpowers/plans/` (`_paths.is_plan_path`). The PR-description-as-spec
   fallback (`_locate.py:85-89`) is **dropped for the chain** — with it, "no spec"
   would almost never fire (a PR body nearly always exists), silently defeating the
   block-on-no-spec rule (Decision 2).

5. **Presence is deterministic; substance is agentic.** "No issue link," "no spec,"
   "no plan," "no code change" are facts the gate/checks compute deterministically
   and never trust from an agent. The agentic legs judge substance only when the
   artifacts exist. Each leg's form-check independently recomputes the leg's scope
   flags from `PR_BODY` + changed-files (the `adherence-coverage.py` pattern) and
   fails the leg if the agent's self-reported scope disagrees, so a sabotaging agent
   cannot fake an absent artifact.

## Architecture

```
preflight  (fanout — 6 legs in parallel, each its own gh-aw agent + evidence + form-checks)
  ├─ spec-solves-issue          block on fail        · N/A if no issue link
  ├─ plan-implements-spec       block underspec / no spec / no plan · warn overspec · N/A if no code
  ├─ code-implements-plan       block underplan / no plan          · warn overplan · N/A if no code
  ├─ mm-compliance              block on diverges    (the reused existing agent)
  ├─ docs-updated-appropriately block when inadequate (new agentic leg; always applicable)
  └─ tests-updated-appropriately block when inadequate (new agentic leg; N/A if no code)
        │
        ▼
  join-preflight   (AND-barrier: every leg must reach `done` on the process axis)
        │
        ▼
  preflight-gate   (root-level noop agent; conclude reads all 6 legs via inputs[],
                    applies block-gaps/warn-extras, on_blocked: halt → /override,
                    publishes ONE consolidated preflight verdict comment)
        │
        ▼
  overview → review(×5) → join-review → triage → fix → post-fix → join-post-fix → mrp → done
```

`overview` and everything downstream are unchanged. The standalone `mm-compliance`
phase is deleted (folded in as a leg). The gate sits *before* `overview`, so the
"halt before expensive review" property is preserved; the cost is that all six
legs run in parallel even when one will block (vs. today's sequential
preflight→mm-compliance short-circuit).

## The six legs

Shared shape: each leg is its own gh-aw agent (`*-agent.md` → `.lock.yml`,
codex/gpt-5.5, read-only, safe-outputs staged), prefetches its inputs *outside* the
agent firewall (the `preflight-agent.md:35-72` pattern), writes
`/tmp/gh-aw/evidence.json` against its own `*.evidence.schema.json`, and runs
form-checks at `on_fail: iterate` (`max_iterations: 2`). Legs **publish** their own
per-leg status but never **conclude** — only the gate concludes.

### 1. `spec-solves-issue` — applicable iff issue-linked
Prefetch: linked issue title+body + spec text. Evidence: a coverage matrix over the
issue's stated problems — each problem → `addressed_by_spec` (verbatim spec quote +
location) | `not_addressed`. Verdict `solves` / `does-not-solve` (`does-not-solve`
iff any required problem is unaddressed). Form-check `spec-solves-issue-coverage`:
every enumerated problem has a cell; every quote exists verbatim in the issue/spec
text; the leg's `issue_linked`/`spec_present` scope matches the deterministic
recomputation.

### 2. `plan-implements-spec` — N/A if no code
Prefetch: spec text + plan text. **Bidirectional matrix:**
- spec→plan: each spec requirement → `covered_by_plan` (quote) | `missing` = **underspec**
- plan→spec: each plan item → `traces_to_spec` (quote) | `extra` = **overspec**

Verdict: block if any `underspec`; warn if any `overspec`; else pass. Form-check
`plan-spec-coverage`: both sides complete; quotes anchor to spec/plan text;
scope flags verified.

### 3. `code-implements-plan` — N/A if no code
Prefetch: plan text + diff. **Bidirectional matrix:**
- plan→code: each plan item → `implemented_in_diff` (`side`/`line`[/`start_line`]
  anchor + verbatim `existing_code`) | `missing` = **underplan**
- code→plan: each substantive diff change → `traces_to_plan` | `extra` = **overplan**

Verdict: block if any `underplan`; warn if any `overplan`; else pass. Form-checks:
reuse `traces-exist-in-diff` for the code side (anchors to real diff lines) +
`code-plan-coverage` for plan-item completeness and the code→plan extras.

### 4. `mm-compliance` — reused verbatim
The existing `mm-compliance-gate.md` agent + `mm-compliance.evidence.schema.json` +
`evidence-present`/`mm-questions-present` checks, repositioned from a top-level
phase to a fanout leg. Its blocking logic (`verdict: diverges`) moves out of
`conclude-mm-compliance.py` (retired) and into the gate's rollup.

### 5. `docs-updated-appropriately` — always applicable
Replaces deterministic `docs-updated-with-code.py`. The agent **self-identifies the
docs relevant to the change** (a deterministic check cannot — it only counts `.md`
files), then per relevant doc → `updated_appropriately` (diff-anchored) | `missing` |
`inadequate`. Block if any relevant doc is not handled appropriately; negative
attestation (`examined` + pass) when nothing is relevant. Form-check `docs-coverage`:
`examined` trace present; each named doc is a real repo path; each "updated" claim
anchors to the diff.

### 6. `tests-updated-appropriately` — N/A if no code
Replaces `tests-updated-with-code.py`. Same agentic shape: the agent identifies the
behaviors needing coverage and judges whether tests are added/updated
appropriately; block if inadequate. Form-check `tests-coverage` mirrors
`docs-coverage`.

## Artifact resolution (deterministic, shared)

Extends `_locate.py`:
- **issue-link** — closing keywords in the PR body (`Closes|Fixes|Resolves #N`)
  and/or the GraphQL `closingIssuesReferences` connection; if found, fetch the issue
  title+body. No link → `spec-solves-issue` is N/A.
- **spec / plan presence** — `_paths.is_spec_path` / `is_plan_path` over changed
  files (the `docs/superpowers/specs/` and `docs/superpowers/plans/` arms), no PR-body
  fallback (Decision 4).
- **code-changed** — any `_paths.is_code` file in the diff.

These facts are recomputed independently inside each leg's form-check (Decision 5)
and consumed by the gate's rollup.

## The gate (`preflight-gate`)

A root-level `agent` node after `join-preflight`. The agent is a **noop**
(safe-output `noop`, cheapest available engine — it does no judging) so that the
engine reaches its `conclude` after the join (`advance.py:615`). Two trusted zone-4
hooks do the work:

**`conclude-preflight` (rewritten) — deterministic rollup.** Reads all six leg
evidences via `inputs[]` (materialized into `CONCLUDE_INPUTS_DIR`, the same path
`triage` uses to read the five review legs). Applies:

```
block if:
    (issue_linked & !spec_present)            # spec-solves-issue: issue but no spec
  | (spec_present & spec-doesn't-solve-issue) # spec-solves-issue: judged fail
  | (code & !spec_present)                    # plan-implements-spec: no spec
  | (code & !plan_present)                    # plan-implements-spec / code-implements-plan: no plan
  | underspec                                 # plan-implements-spec
  | underplan                                 # code-implements-plan
  | mm.diverges                               # mm-compliance
  | docs.inadequate                           # docs-updated-appropriately
  | (code & tests.inadequate)                 # tests-updated-appropriately
warn (advisory, never blocks):  overspec | overplan
N/A (contributes nothing):  spec-solves-issue when !issue_linked;
                            plan-implements-spec / code-implements-plan /
                            tests-updated-appropriately when !code
```

Returns `{conclusion, summary, blocked, reasons[], warnings[]}`; `blocked` iff any
block reason fired. `on_blocked: halt` marks the run `failed`, writes the `halted`
marker, and posts a `/override`-instructions comment (`advance.py:615-648`).

**`publish-verdict` (adapted)** posts **one consolidated** preflight comment —
per-leg status (block reason / advisory warning / N/A-with-reason) — and writes the
`verdict.json` artifact.

**Override:** the existing `/override` trigger reads the `halted` marker and
advances the root cursor to `overview` (`next.py:do_override`). One `/override`
clears the whole gate at once (the trade for consolidating blocking into one node).

## Engine grounding (why no engine change)

Verified this iteration (cite-backed):
- Root-level `agent` runs `conclude` + honors `on_blocked: halt` — `advance.py:615`
  (behind the `is_root_child` guard), halt mechanics `advance.py:615-648`.
- Fanout legs run **publish only, never conclude** — `advance.py:684-694`; confirmed
  by STATUS.md.
- Sub-pipeline-internal agents do not conclude — `advance.py:602-607` (routes to
  `advance_node`).
- `join` is a pure AND-barrier (no hook, cannot block except via a `failed` leg) —
  `join.py:104-114, 185-206`.
- `merge` cannot halt (always proceeds to `next`) — `next.py:765-784`.
- An agent after a join can read every leg via `inputs[]`, run `conclude`, and halt —
  `lib.py:172-214` (`_resolve_input_ref_pathaware`), `advance.py:357-374` (conclude
  input materialization). This is the `review → join-review → triage` precedent.
- Single-branch fanout is legal/handled (not needed here, but confirms the model).

## File map

```
.github/agent-factory/protocols/code-review/
  protocol.json                          # REWRITE: preflight → fanout(6) + join-preflight + preflight-gate; delete mm-compliance phase
  spec-solves-issue.evidence.schema.json     # NEW
  plan-implements-spec.evidence.schema.json  # NEW (bidirectional matrix)
  code-implements-plan.evidence.schema.json  # NEW (bidirectional matrix)
  docs-coherence.evidence.schema.json        # NEW
  tests-coherence.evidence.schema.json       # NEW
  mm-compliance.evidence.schema.json         # REUSE (unchanged)
  checks/
    _locate.py                           # EXTEND: issue-link detect/fetch; drop description fallback
    spec-solves-issue-coverage.py        # NEW
    plan-spec-coverage.py                # NEW
    code-plan-coverage.py                # NEW
    docs-coverage.py                     # NEW
    tests-coverage.py                    # NEW
    traces-exist-in-diff.py              # REUSE (code-implements-plan code side)
    evidence-present.py, mm-questions-present.py  # REUSE (mm-compliance leg)
    docs-updated-with-code.py, tests-updated-with-code.py  # RETIRE
    spec-present.py, plan-present.py     # RETIRE (folded into leg form-checks + gate)
  publish/
    conclude-preflight.py                # REWRITE: 2-verdict → 6-leg rollup
    publish-verdict.py                   # ADAPT: consolidated comment
    conclude-mm-compliance.py            # RETIRE (absorbed into gate rollup)

.github/workflows/
  spec-solves-issue-agent.md (+ .lock.yml)      # NEW
  plan-implements-spec-agent.md (+ .lock.yml)   # NEW
  code-implements-plan-agent.md (+ .lock.yml)   # NEW
  docs-coherence-agent.md (+ .lock.yml)         # NEW
  tests-coherence-agent.md (+ .lock.yml)        # NEW
  preflight-gate-agent.md (+ .lock.yml)         # NEW (noop)
  mm-compliance-gate.md (+ .lock.yml)           # REUSE (unchanged)

.github/agent-factory/engine/                    # UNTOUCHED
```

## Phasing (3 independently-shippable phases)

Each phase ends in pytest-green + one live `/review`.

- **Phase A — chain + gate skeleton.** Build `fanout → join-preflight →
  preflight-gate`; split today's two adherence verdicts into the three chain legs;
  add issue-link resolution + drop the description fallback; write the rollup
  (block-gaps/warn-extras). Temporarily keep `mm-compliance` as its current phase
  and docs/tests as the current advisory checks *on the gate* so nothing regresses.
- **Phase B — fold in mm-compliance.** Move it from a top-level phase to a fanout
  leg; move its block into the rollup; delete `conclude-mm-compliance.py` + the old
  phase.
- **Phase C — agentic docs/tests.** Replace the two deterministic checks with the
  `docs-coherence`/`tests-coherence` legs (now blocking).

## Testing & verification

- **pytest — `conclude-preflight` rollup (table-driven):** every policy branch —
  no-issue / issue+no-spec / solves / doesn't-solve / no-code N/A / underspec /
  overspec / underplan / overplan / mm compliant+diverges / docs adequate+inadequate
  / tests adequate+inadequate; assert `blocked` + `reasons`/`warnings`.
- **pytest — each new check:** coverage completeness, anchoring, and
  scope-disagreement detection (agent claims an artifact absent/present that the
  deterministic recomputation contradicts → leg fails).
- **`protocol-lint.py`** structural pass on the new `protocol.json`
  (`validate_protocol`: the join names the fanout, the gate's `inputs[]` reference
  real legs, depth within `max_depth`).
- **`gh aw compile`** for each new agent `.md`; commit the `.lock.yml`.
- **Live:** `/review` on a throwaway PR — clear path + each block path + `/override`.

## Risks

- **Cost.** Six parallel codex agents per preflight (vs. two sequential today). The
  gate still halts before `overview`, so review/triage/fix are still gated.
- **Coverage-matrix schemas (legs 2–3) are the hardest design content.** They must
  enumerate spec-requirement / plan-item cells in both directions with anchored
  quotes; the form-checks verify completeness + anchoring only (never correctness —
  the porch ceiling).
- **Issue-link resolution is a new primitive** (GraphQL `closingIssuesReferences` or
  body keyword parse); must degrade to N/A gracefully when absent.
- **One `/override` clears all reasons** — a maintainer cannot override mm-divergence
  while staying blocked on adherence. Accepted as the cost of one gate.

## Non-goals

- Judging *correctness* of any verdict (the engine checks form, not substance).
- Posting per-finding inline PR review comments for preflight (a possible later
  per-leg publish enhancement).
- Any change to `overview` and downstream phases.
- Any change to the generic engine.
