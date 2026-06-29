# Preflight Adherence Decomposition (DESIGN)

**Date:** 2026-06-29
**Status:** DESIGN — approved, revised after adversarial review, ready for `writing-plans`.
**Protocol:** `code-review` (`.github/agent-factory/protocols/code-review/`)
**Engine change:** none. The shape relies only on capabilities already present
(fanout, join, `inputs[]`, root-level `conclude` + `on_blocked: halt`).

> **Revision note (2026-06-29).** A cite-checked adversarial review (5 lenses,
> findings verified against the engine code) corrected the v1 draft. Fixes folded
> in: (1) the gate must carry a passing form-check or it never reaches `done`;
> (2) a root agent's own `publish:` key is ignored, so the consolidated comment is
> posted by `conclude-preflight`, not an engine publish hook; (3) `mrp`'s
> `{from: preflight}` input is repointed to `preflight-gate`; (4) dropped the false
> claim that `validate_protocol` checks `inputs[]`; (5) pinned the leg-2/3
> coverage-matrix shapes and the `traces-exist-in-diff` reuse binding; (6) added a
> test-migration list; (7) reworded presence (leg form-checks compute it; the gate
> only reads form-verified scope). Both strictness choices (block on missing
> spec/plan for every code PR; block on inadequate docs/tests) are **kept** and
> recorded as accepted-cost risks.

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
   appropriately. (Strictness on missing spec/plan and on docs/tests is deliberate;
   see `## Risks` for the accepted costs.)

3. **No-code PRs.** When a PR changes no code file (`_paths.is_code`), the legs that
   gate code — `plan-implements-spec`, `code-implements-plan`,
   `tests-updated-appropriately` — are **N/A**. `spec-solves-issue` (if issue-linked)
   and `docs-updated-appropriately` still run.

4. **Committed artifacts only.** Spec/plan presence is computed with
   `_paths.is_spec_path` / `is_plan_path` over the PR's changed files. Those arms
   already cover `docs/superpowers/specs/` and `docs/superpowers/plans/` (the
   primary location) plus the broader committed-artifact arms (`specs/`, `plans/`,
   `SPEC.md`, `REQUIREMENTS.md`, `PLAN.md`); we use them as-is, with no narrowing.
   The PR-description-as-spec fallback (`_locate.py:85-89`) is **dropped for the
   chain** — with it, "no spec" would almost never fire (a PR body nearly always
   exists), silently defeating the block-on-no-spec rule (Decision 2).

5. **Presence is deterministic and computed by the LEG FORM-CHECKS; the gate only
   reads it.** "No issue link," "no spec," "no plan," "no code change" are facts a
   deterministic check computes from `PR_BODY` + changed-files — the
   `adherence-coverage.py` pattern. The advance-job (zone 4) where the gate's
   `conclude` runs has **neither `PR_BODY` nor the changed-files list**, so the gate
   cannot recompute presence itself. Therefore each leg's form-check independently
   recomputes the leg's scope flags (`issue_linked` / `spec_present` / `plan_present`
   / `code_changed`) and **fails the leg if the agent's self-reported scope
   disagrees** — so a sabotaging agent cannot fake an absent artifact. The gate's
   `conclude` then *reads* these already-form-verified scope flags from the leg
   evidence (materialized via `inputs[]`); it never recomputes them. **Invariant:**
   every presence fact the rollup branches on is gated by a passing form-check on the
   leg that carries it.

## Architecture

```
preflight  (fanout — 6 legs in parallel, each its own gh-aw agent + evidence + form-checks)
  ├─ spec-solves-issue          block on fail        · N/A if no issue link
  ├─ plan-implements-spec       block underspec / no spec / no plan · warn overspec · N/A if no code
  ├─ code-implements-plan       block underplan / no plan          · warn overplan · N/A if no code
  ├─ mm-compliance              block on diverges    (the reused existing agent, comment suppressed)
  ├─ docs-updated-appropriately block when inadequate (new agentic leg; always applicable)
  └─ tests-updated-appropriately block when inadequate (new agentic leg; N/A if no code)
        │
        ▼
  join-preflight   (AND-barrier: every leg must reach `done` on the process axis)
        │
        ▼
  preflight-gate   (root-level agent; carries >=1 passing form-check so it reaches `done`;
                    conclude reads all 6 legs via inputs[], applies block-gaps/warn-extras,
                    on_blocked: halt -> /override, and POSTS the one consolidated comment)
        │
        ▼
  overview → review(×5) → join-review → triage → fix → post-fix → join-post-fix → mrp → done
```

`overview` and everything downstream are unchanged **except** `mrp`'s `preflight`
input alias is repointed to the gate (see File map / Non-goals). The standalone
`mm-compliance` phase is deleted (folded in as a leg). The gate sits *before*
`overview`, so the "halt before expensive review" property is preserved; the cost
is that all six legs run in parallel even when one will block (vs. today's
sequential preflight→mm-compliance short-circuit).

## The six legs

Shared shape: each leg is its own gh-aw agent (`*-agent.md` → `.lock.yml`,
codex/gpt-5.5, read-only, safe-outputs staged), prefetches what it needs *outside*
the agent firewall (the `preflight-agent.md:35-72` pattern), writes
`/tmp/gh-aw/evidence.json` against its own `*.evidence.schema.json`, and runs
form-checks at `on_fail: iterate` (`max_iterations: 2`). **Legs do not post their own
PR comment** — to avoid six comments + the gate's, per-leg status is surfaced only
via the leg's per-leg sub-check-run (publish) and rendered in the gate's single
consolidated comment. Legs never `conclude` — only the gate concludes.

### 1. `spec-solves-issue` — applicable iff issue-linked
Prefetch: linked issue title+body + spec text. Evidence = a coverage matrix over the
issue's stated problems: each problem → `addressed_by_spec` (verbatim spec quote +
location) | `not_addressed`. Verdict `solves` / `does-not-solve` (`does-not-solve`
iff any required problem is unaddressed). Form-check `spec-solves-issue-coverage`:
every problem has a cell; every quote exists verbatim in the issue/spec text (the
check **self-fetches** the issue body + spec text — see Artifact resolution); the
leg's `issue_linked`/`spec_present` scope matches the deterministic recomputation.

### 2. `plan-implements-spec` — N/A if no code
Prefetch: spec text + plan text. **Bidirectional matrix** with pinned field names:
```jsonc
{
  "scope": { "code_changed": true, "spec_present": true, "plan_present": true },
  "spec_to_plan": [ { "requirement": "<verbatim spec quote>",
                      "status": "covered" | "missing",      // missing => UNDERSPEC
                      "plan_quote": "<verbatim plan quote|null>" } ],
  "plan_to_spec": [ { "plan_item": "<verbatim plan quote>",
                      "status": "traces" | "extra",          // extra => OVERSPEC
                      "spec_quote": "<verbatim spec quote|null>" } ],
  "verdict": "adheres" | "underspec" | "overspec",            // underspec wins over overspec
  "examined": [ "<artifact ids read>" ]
}
```
Form-check `plan-spec-coverage`: both arrays present and non-trivial; every
`requirement`/`plan_item`/`*_quote` exists verbatim in the fetched spec/plan text;
`verdict` is consistent with the cells; `scope` matches the deterministic recompute.

### 3. `code-implements-plan` — N/A if no code
Prefetch: plan text + diff. **Bidirectional matrix.** The **code side reuses
`traces-exist-in-diff`**, which iterates a fixed container — so the schema MUST emit
that exact shape (a vacuous/absent `files` key makes the check pass silently):
```jsonc
{
  "scope": { "code_changed": true, "plan_present": true },
  "plan_to_code": [ { "plan_item": "<verbatim plan quote>",
                      "status": "implemented" | "missing" } ],   // missing => UNDERPLAN
  "files": [ { "path": "<changed file>",
               "verdicts": [ { "category": "code-implements-plan",
                               "examined": [ "<identifiers in this file's diff>" ],
                               "findings": [ { "plan_item": "<plan quote|null>",  // null => OVERPLAN
                                               "status": "traces" | "extra",
                                               "side": "RIGHT|LEFT", "line": 0,
                                               "start_line": 0,
                                               "existing_code": "<verbatim diff line(s)>" } ] } ] } ],
  "verdict": "adheres" | "underplan" | "overplan",
  "examined": [ "<artifact ids read>" ]
}
```
Form-checks: reuse `traces-exist-in-diff` (validates `files[].verdicts[].findings[]`
anchors against the independently-fetched diff) + `code-plan-coverage`
(`plan_to_code` complete; every `plan_item` quote in the plan text; `verdict`
consistent; `scope` verified).

### 4. `mm-compliance` — reused, comment suppressed
The existing `mm-compliance-gate.md` agent + `mm-compliance.evidence.schema.json` +
`evidence-present`/`mm-questions-present` checks, repositioned from a top-level phase
to a fanout leg. **Two changes:** its standalone `add-comment` safe-output is dropped
(the gate renders mm status in the consolidated comment, avoiding a double-post), and
its blocking logic (`verdict: diverges`) moves out of `conclude-mm-compliance.py`
(retired) into the gate's rollup. The `_mental_model` branch checkout step is kept.

### 5. `docs-updated-appropriately` — always applicable
Replaces deterministic `docs-updated-with-code.py`. The agent **self-identifies the
docs relevant to the change** (a deterministic check cannot — it only counts `.md`
files), then per relevant doc → `updated_appropriately` (diff-anchored) | `missing` |
`inadequate`. Block if any relevant doc is not handled appropriately; negative
attestation (`examined` + pass) when nothing is relevant. Form-check `docs-coverage`:
`examined` trace present; each named doc is a real repo path; each "updated" claim
anchors to the diff.

### 6. `tests-updated-appropriately` — N/A if no code
Replaces `tests-updated-with-code.py`; same agentic shape as docs (agent identifies
behaviors needing coverage, judges whether tests are added/updated appropriately,
blocks if inadequate). Form-check `tests-coverage` mirrors `docs-coverage`.

## Artifact resolution (deterministic, shared)

Extends `_locate.py`:
- **issue-link** — closing keywords in the PR body (`Closes|Fixes|Resolves #N`)
  and/or the GraphQL `closingIssuesReferences` connection. No link →
  `spec-solves-issue` is N/A.
- **spec / plan presence** — `_paths.is_spec_path` / `is_plan_path` over changed
  files (Decision 4), no PR-body fallback.
- **code-changed** — any `_paths.is_code` file in the diff.

**Where the text comes from for the checks.** A zone-3 check is not limited to the
engine-supplied files — it self-fetches its ground truth with the checks job's
read-only token, exactly as `local-review-evidence.py` does via `_review_fetch`
(`checks/_review_fetch.py:5-10`). So `spec-solves-issue-coverage` /
`plan-spec-coverage` fetch the issue body (`gh api repos/{repo}/issues/{n}`) and the
spec/plan file text at the PR head (`gh api repos/{repo}/contents/{path}?ref={head}`,
the same call the agent prefetch uses), re-deriving the head SHA via
`gh pr view "$PR" --json headRefOid`. The scope flags each leg's form-check verifies
(Decision 5) are recomputed from `PR_BODY` + changed-files, which the checks job
already exports.

## The gate (`preflight-gate`)

A root-level `agent` node after `join-preflight`. Two engine facts shape it:
- **Conclude/halt runs only for a root-level agent that reaches `done`**
  (`advance.py:615`, inside the `process == "done"` branch at `advance.py:576`). A
  node reaches `done` only with ≥1 passing verdict; `lib.decide([])` returns
  `iterate`→`failed` (`lib.py:727-728`). **So the gate MUST declare at least one
  trivially-passing form-check** — `{ run: evidence-present, on_fail: iterate }` with
  `params: { require: [] }` — and its (noop) agent writes a minimal
  `/tmp/gh-aw/evidence.json` (e.g. `{}`). Without a check the gate would iterate to
  `max_iterations` and fail, and the rollup/halt would never run.
- **A root agent's own `publish:` key is ignored** (`advance.py:284-298` resolves
  the publish action from the `.next` state's `.action`, which `overview` lacks). So
  the consolidated comment is **NOT** an engine publish hook — it is posted by
  `conclude-preflight` (which runs unconditionally on both arms and holds
  `PUBLISH_TOKEN` + `PR` in the advance-job env).

The agent itself does no judging (its only job is to reach `done`). All real work is
in the conclude hook:

**`conclude-preflight` (rewritten) — deterministic rollup + comment.** It pulls all
six leg evidences via `inputs[]` (materialized into `CONCLUDE_INPUTS_DIR`, the same
path `triage` uses to read the five review legs), reads each leg's verdict and its
form-verified scope flags, and applies:

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

`blocked = (any block reason)`; returns `{conclusion, summary, blocked, reasons[],
warnings[]}`. It also **posts the one consolidated preflight comment** (per-leg
status: block reason / advisory warning / N/A-with-reason) and writes the
`verdict.json` artifact. `on_blocked: halt` then marks the run `failed`, writes the
`halted` marker, and posts the `/override`-instructions notice (`advance.py:615-648`).

**Override:** the existing `/override` trigger reads the `halted` marker and advances
the root cursor to `overview` (`next.py:do_override`). One `/override` clears the
whole gate at once (the trade for consolidating blocking into one node — see Risks).

## Engine grounding (why no engine change)

Verified against the code (all cites confirmed accurate by the review):
- Root-level `agent` runs `conclude` + honors `on_blocked: halt`, but only in the
  `done` branch — `advance.py:576` (the `process == "done"` guard), `advance.py:615`
  (conclude call behind the `is_root_child` + `node_kind == "agent"` guard), halt
  mechanics `advance.py:615-648`.
- A node with no checks yields zero verdicts and can never be `done` —
  `lib.py:727-728`; hence the gate's mandatory passing check.
- A root agent's own `publish:` key is **not** honored — `advance.py:284-298`; hence
  the comment moves to `conclude-preflight`.
- Fanout legs run **publish only, never conclude** — `advance.py:684-694`;
  sub-pipeline-internal agents do not conclude — `advance.py:602-607`.
- `join` is a pure AND-barrier (no hook, cannot block except via a `failed` leg) —
  `join.py:104-114, 185-206`.
- `merge` cannot halt (always proceeds to `next`) — `next.py:765-784`.
- An agent after a join reads every leg via `inputs[]`, runs `conclude`, and halts —
  `lib.py:172-214` (`_resolve_input_ref_pathaware`), `advance.py:357-374` (conclude
  input materialization). This is the `review → join-review → triage` precedent.
- **Caveat the review surfaced:** the resolver matches a node's own id *before*
  scanning fanout legs (`lib.py:191-199`); since the new fanout's id is `preflight`,
  `mrp`'s `{from: preflight}` would resolve to an unwritten `preflight.evidence.json`
  — hence the mrp repoint below. `validate_protocol` does **not** validate
  `inputs[].from`, so a typo is silent; the gate's input wiring is covered by a
  dedicated pytest instead (see Testing).

## File map

```
.github/agent-factory/protocols/code-review/
  protocol.json                          # REWRITE: preflight -> fanout(6) + join-preflight + preflight-gate;
                                         #   delete mm-compliance phase; REPOINT mrp input {from: preflight}
                                         #   -> {from: preflight-gate, as: preflight}
  spec-solves-issue.evidence.schema.json     # NEW
  plan-implements-spec.evidence.schema.json  # NEW (bidirectional matrix; field names pinned in this doc)
  code-implements-plan.evidence.schema.json  # NEW (bidirectional; code side uses traces-exist-in-diff shape)
  docs-coherence.evidence.schema.json        # NEW
  tests-coherence.evidence.schema.json       # NEW
  preflight-gate.evidence.schema.json        # NEW (minimal; the noop agent writes {} and evidence-present passes)
  mm-compliance.evidence.schema.json         # REUSE (unchanged)
  checks/
    _locate.py                           # EXTEND: issue-link detect/fetch; drop description fallback
    spec-solves-issue-coverage.py        # NEW (self-fetches issue body + spec text)
    plan-spec-coverage.py                # NEW (self-fetches spec + plan text)
    code-plan-coverage.py                # NEW
    docs-coverage.py                     # NEW
    tests-coverage.py                    # NEW
    traces-exist-in-diff.py              # REUSE (code-implements-plan code side)
    evidence-present.py                  # REUSE (mm-compliance leg + the gate's passing check)
    mm-questions-present.py              # REUSE (mm-compliance leg)
    docs-updated-with-code.py, tests-updated-with-code.py  # RETIRE
    spec-present.py, plan-present.py     # RETIRE (folded into leg form-checks + gate)
  publish/
    conclude-preflight.py                # REWRITE: 2-verdict -> 6-leg rollup; ALSO posts the consolidated
                                         #   comment + writes verdict.json (publish: hook is dead on a root agent)
    publish-verdict.py                   # RETIRE (its responsibilities fold into conclude-preflight)
    conclude-mm-compliance.py            # RETIRE (absorbed into gate rollup)

.github/workflows/
  spec-solves-issue-agent.md (+ .lock.yml)      # NEW
  plan-implements-spec-agent.md (+ .lock.yml)   # NEW
  code-implements-plan-agent.md (+ .lock.yml)   # NEW
  docs-coherence-agent.md (+ .lock.yml)         # NEW
  tests-coherence-agent.md (+ .lock.yml)        # NEW
  preflight-gate-agent.md (+ .lock.yml)         # NEW (noop: writes {}, calls noop; cheapest engine)
  mm-compliance-gate.md (+ .lock.yml)           # REUSE, minus its add-comment safe-output

.github/agent-factory/engine/                    # UNTOUCHED
```

## Phasing (3 independently-shippable phases)

Each phase ends in pytest-green + one live `/review`.

- **Phase A — chain + gate skeleton.** Build `fanout → join-preflight →
  preflight-gate` (with the gate's mandatory passing check + noop agent + the
  consolidated comment in `conclude-preflight`); split today's two adherence verdicts
  into the three chain legs; add issue-link + drop the description fallback; write the
  rollup (block-gaps/warn-extras); **repoint `mrp`'s `preflight` input to
  `preflight-gate`**. Temporarily keep `mm-compliance` as its current phase and
  docs/tests as the current advisory checks *on the gate* so nothing regresses.
- **Phase B — fold in mm-compliance.** Move it from a top-level phase to a fanout leg
  (dropping its `add-comment`); move its block into the rollup; delete
  `conclude-mm-compliance.py` + the old phase.
- **Phase C — agentic docs/tests.** Replace the two deterministic checks with the
  `docs-coherence`/`tests-coherence` legs (now blocking).

## Testing & verification

- **pytest — `conclude-preflight` rollup (table-driven).** The harness must
  **materialize a `CONCLUDE_INPUTS_DIR` with six leg-evidence files** (+ `BLOCKING`
  env) — the rewritten hook reads the inputs dir, not a single evidence arg. Cover
  every policy branch: no-issue / issue+no-spec / solves / doesn't-solve / no-code
  N/A / underspec / overspec / underplan / overplan / mm compliant+diverges / docs
  adequate+inadequate / tests adequate+inadequate; assert `blocked` +
  `reasons`/`warnings`.
- **pytest — each new check:** coverage completeness, anchoring (incl. a
  `traces-exist-in-diff` case proving leg-3's `files[].verdicts[].findings[]` shape
  is rejected when an anchor is bad — not vacuously passed), and scope-disagreement
  detection (agent claims an artifact absent/present that the deterministic
  recompute contradicts → leg fails).
- **pytest — gate input wiring (NEW, replaces a false lint claim).** `validate_protocol`
  does **not** validate `inputs[].from`, so assert directly (the
  `test_mm_pipeline_wiring.py:33-54` pattern) that each of the six gate inputs
  resolves to the corresponding leg's actual evidence path, and that
  `mrp`'s `preflight` input now resolves to `preflight-gate`.
- **Test migration (must land green per phase):**
  - `test_preflight_checks.py` — tests four RETIRED checks (`spec-present`,
    `plan-present`, `docs-updated-with-code`, `tests-updated-with-code`); delete the
    retired-check cases (Phase A for spec/plan, Phase C for docs/tests).
  - `test_conclude_preflight.py` — rewrite the harness from single-arg to the
    `CONCLUDE_INPUTS_DIR` shape (Phase A).
  - `test_preflight_coverage.py` — `adherence-coverage` is superseded by the per-leg
    coverage checks; migrate/retire (Phase A).
  - `test_mm_pipeline_wiring.py:54` — update the pinned `preflight` input assertion to
    the gate (Phase A).
- **`protocol-lint.py`** structural pass on the new `protocol.json`
  (`validate_protocol`: the join names the fanout, agents declare a `workflow`,
  depth within `max_depth`). *Note: it does NOT check `inputs[].from` — that is the
  dedicated pytest above.*
- **`gh aw compile`** for each new agent `.md`; commit the `.lock.yml`.
- **Live:** `/review` on a throwaway PR — clear path + each block path + `/override`.

## Risks

- **Cost.** Six parallel codex agents per preflight (vs. two sequential today). The
  gate still halts before `overview`, so review/triage/fix are still gated.
- **Accepted cost — mandatory spec+plan on every code PR.** With the description
  fallback dropped, any code-touching PR without a committed `docs/superpowers/specs|plans`
  artifact halts and needs `/override`. This repo's own routine commits (CI fixes,
  lock regens, refactors) often lack such artifacts, so they will trip the gate. This
  is **intended** (the chain's value depends on the artifacts existing); the mitigation
  is operational (`/override`), and a future per-repo opt-out could relax it if the
  override rate proves noisy.
- **Accepted cost — blocking docs/tests on a subjective agent verdict.** "Inadequate"
  is a substance judgment the form-check cannot validate (the porch ceiling), yet it
  halts the pipeline (escape: `/override`). This is **intended**; if it proves flaky,
  the per-leg `on_fail` can later default these two legs back to advisory without a
  structural change.
- **Persistent leg failure is fail-closed but un-overridable.** If a leg exhausts its
  iterations to `failed` (e.g. a form-check that keeps failing), the join finalizes
  `failure` *without* reaching the gate, so no `halted` marker is written and
  `/override` refuses (`next.py` "override only applies to a gate that ran"). The
  pipeline is safely stopped, but recovery is a re-run, not `/override` — distinct
  from the gate's blockable+overridable halt.
- **One `/override` clears all reasons** — a maintainer cannot override mm-divergence
  while staying blocked on adherence (or vice-versa). Accepted as the cost of
  consolidating blocking into one gate.
- **Coverage-matrix schemas (legs 2–3) are the hardest content.** The field names are
  pinned here; the form-checks verify completeness + anchoring only (never correctness
  — the porch ceiling). Leg 3's code side MUST emit the `traces-exist-in-diff`
  container or the check passes vacuously.
- **Issue-link resolution is a new primitive** (GraphQL `closingIssuesReferences` or
  body keyword parse); must degrade to N/A gracefully when absent.

## Non-goals

- Judging *correctness* of any verdict (the engine checks form, not substance).
- Posting per-finding inline PR review comments for preflight (a possible later
  per-leg enhancement).
- Any behavioral change to `overview` and downstream phases — **except** repointing
  `mrp`'s `preflight` input alias to the gate (required; the old single-agent
  evidence path no longer exists).
- Any change to the generic engine.
