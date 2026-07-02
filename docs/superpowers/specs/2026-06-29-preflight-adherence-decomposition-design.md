# Preflight Adherence Decomposition (DESIGN)

**Date:** 2026-06-29
**Status:** DESIGN — approved, revised twice after adversarial review, ready for `writing-plans`.
**Protocol:** `code-review` (`.github/agent-factory/protocols/code-review/`)
**Engine change:** one minimal, *generic* permission grant — `issues: read` on the
reusable checks job (`agentic-engine.yml`) so zone-3 checks can fetch a linked
issue's body as independent ground truth. No engine *logic* change; the grant is
protocol-agnostic, not protocol-specific logic.

> **Revision note (pass 1, 2026-06-29).** A cite-checked adversarial review (5
> lenses, verified against the engine) corrected the v1 draft: (1) the gate must
> carry a passing form-check or it never reaches `done`; (2) a root agent's own
> `publish:` key is ignored, so the consolidated comment is posted by
> `conclude-preflight`; (3) `mrp`'s `{from: preflight}` input is repointed;
> (4) dropped the false `validate_protocol` `inputs[]` claim; (5) pinned the
> leg-2/3 matrix shapes + the `traces-exist-in-diff` reuse binding; (6) added a
> test-migration list; (7) reworded presence.
>
> **Revision note (pass 2, 2026-06-29).** A second review of the revision found 5
> new issues, two introduced by pass 1: **(a)** the gate is no longer a noop —
> repointing `mrp` to a noop `{}` gate emptied its preflight input, so the gate now
> **synthesizes a consolidated evidence** from its six leg inputs (which also gives
> it a real passing coverage check and removes the noop smell); **(b)** the zone-3
> issue-body self-fetch needs `issues: read` on the checks job (added, with a
> fail-closed rule); **(c)** an explicit **N/A leg contract** (all six legs always
> dispatch and self-attest N/A — none are skipped); **(d)** `test_resolve_agent_unit.py`
> added to the migration list; **(e)** the gate must be **declared immediately
> before `overview`** (the cursor advances by sibling order, not `next`). Both
> strictness choices are **kept** and recorded as accepted-cost risks.

---

## Goal

Replace the `code-review` protocol's single-agent `preflight` gate — which judges
exactly two verdicts (`spec-adherence`, `plan-adherence`) against the diff in one
agent pass — with a **decomposed preflight**: a fan-out of six independent agentic
subworkflows that judge the full **issue → spec → plan → code** chain (in both the
under- and over-coverage directions), plus mental-model compliance and
docs/tests coherence, with one gate that synthesizes the legs and halts the
pipeline on any blocking divergence.

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
resolved. The deterministic checks verify form only; absence is advisory, so the
only path to a halt today is an agent adherence verdict of `fail`
(`conclude-preflight.py:23-24`). `mm-compliance` is a *separate* blocking phase
immediately after preflight (`protocol.json:35-49`).

## Decisions (locked)

1. **Structure — parallel legs + one synthesis gate.** `preflight` becomes a
   `fanout` of six legs (parallel), an AND-barrier `join`, then a single root-level
   `preflight-gate` agent that reads all six legs, writes a consolidated evidence,
   and whose `conclude` hook applies the blocking policy and `on_blocked: halt`s.
   This is forced by the engine (`## Engine grounding`): only a **root-level** phase
   runs a blocking `conclude`. The shape mirrors the existing
   `review → join-review → triage` pattern (`protocol.json:68-103`), where `triage`
   likewise reads all five review legs via `inputs[]`.

2. **Blocking policy — block gaps, warn on extras.** The only advisory (non-halting)
   outcomes are the two over-coverage directions (`overspec`, `overplan`).
   Everything else halts: spec-doesn't-solve-issue, missing spec, missing plan,
   `underspec`, `underplan`, mm-divergence, and docs/tests not updated
   appropriately. (Strictness on missing spec/plan and on docs/tests is deliberate;
   see `## Risks` for the accepted costs.)

3. **No-code PRs.** When a PR changes no code file (`_paths.is_code`), the legs that
   gate code — `plan-implements-spec`, `code-implements-plan`,
   `tests-updated-appropriately` — self-attest **N/A** (see the N/A leg contract).
   `spec-solves-issue` (if issue-linked) and `docs-updated-appropriately` still run.

4. **Committed artifacts only.** Spec/plan presence is computed with
   `_paths.is_spec_path` / `is_plan_path` over the PR's changed files. Those arms
   already cover `docs/superpowers/specs/` and `docs/superpowers/plans/` (the
   primary location) plus the broader committed-artifact arms; we use them as-is.
   The PR-description-as-spec fallback (`_locate.py:85-89`) is **dropped for the
   chain** — with it, "no spec" would almost never fire, silently defeating the
   block-on-no-spec rule (Decision 2).

5. **Presence is deterministic and computed by the LEG FORM-CHECKS; the gate only
   reads it.** "No issue link," "no spec," "no plan," "no code change" are facts a
   deterministic check computes from `PR_BODY` + changed-files (the
   `adherence-coverage.py` pattern). The advance-job (zone 4) where the gate's
   `conclude` runs has **neither `PR_BODY` nor the changed-files list**, so the gate
   cannot recompute presence itself. Each leg's form-check independently recomputes
   the leg's scope flags (`issue_linked` / `spec_present` / `plan_present` /
   `code_changed`) and **fails the leg if the agent's self-reported scope
   disagrees** — so a sabotaging agent cannot fake an absent artifact. The gate's
   `conclude` then *reads* these already-form-verified scope flags from the leg
   evidence; it never recomputes them. **Invariant:** every presence fact the rollup
   branches on is gated by a passing form-check on the leg that carries it.

## Architecture

```
preflight  (fanout — 6 legs ALWAYS dispatched in parallel; each its own gh-aw agent + evidence + form-checks)
  ├─ spec-solves-issue          block on fail        · self-attests N/A if no issue link
  ├─ plan-implements-spec       block underspec / no spec / no plan · warn overspec · N/A if no code
  ├─ code-implements-plan       block underplan / no plan          · warn overplan · N/A if no code
  ├─ mm-compliance              block on diverges    (reused agent, standalone comment removed)
  ├─ docs-updated-appropriately block when inadequate (new agentic leg; always applicable)
  └─ tests-updated-appropriately block when inadequate (new agentic leg; N/A if no code)
        │
        ▼
  join-preflight   (AND-barrier: every leg must reach `done` — so every leg, incl. N/A, must self-attest and pass)
        │
        ▼
  preflight-gate   (root-level agent, DECLARED IMMEDIATELY BEFORE overview; inputs[] = the 6 legs.
                    Agent reads the 6 legs -> writes a consolidated evidence (what mrp consumes).
                    Form-check `preflight-gate-coverage` requires one cell per leg -> reaches `done`.
                    conclude-preflight INDEPENDENTLY re-reads the 6 legs, applies block-gaps/warn-extras,
                    posts the one consolidated comment, writes verdict.json, on_blocked: halt -> /override.)
        │
        ▼
  overview → review(×5) → join-review → triage → fix → post-fix → join-post-fix → mrp → done
```

`overview` and everything downstream are unchanged **except** `mrp`'s `preflight`
input alias is repointed to the gate (it now reads the gate's consolidated
evidence; see File map / Non-goals). The standalone `mm-compliance` phase is deleted
(folded in as a leg). The gate sits *before* `overview`, so the "halt before
expensive review" property is preserved; the cost is that all six legs run in
parallel even when one will block (vs. today's sequential short-circuit).

## The six legs

Shared shape: each leg is its own gh-aw agent (`*-agent.md` → `.lock.yml`,
codex/gpt-5.5, read-only, safe-outputs staged), prefetches what it needs *outside*
the agent firewall (the `preflight-agent.md:35-72` pattern), writes
`/tmp/gh-aw/evidence.json` against its own `*.evidence.schema.json`, and runs
form-checks at `on_fail: iterate` (`max_iterations: 2`). **Legs do not post their own
PR comment** — per-leg status is surfaced via the leg's sub-check-run (publish) and
rendered in the gate's single consolidated comment. Legs never `conclude`.

### 1. `spec-solves-issue` — judges iff issue-linked, else self-attests N/A
Prefetch: linked issue title+body + spec text. Evidence = a coverage matrix over the
issue's stated problems: each problem → `addressed_by_spec` (verbatim spec quote +
location) | `not_addressed`. Verdict `solves` / `does-not-solve` / `n/a`. Form-check
`spec-solves-issue-coverage`: every problem has a cell; every quote exists verbatim
in the issue/spec text (the check **self-fetches** issue + spec text — see Artifact
resolution); the leg's `issue_linked`/`spec_present` scope matches the deterministic
recomputation.

### 2. `plan-implements-spec` — N/A if no code
Prefetch: spec + plan text. **Bidirectional matrix** with pinned field names:
```jsonc
{
  "scope": { "code_changed": true, "spec_present": true, "plan_present": true },
  "spec_to_plan": [ { "requirement": "<verbatim spec quote>",
                      "status": "covered" | "missing",      // missing => UNDERSPEC
                      "plan_quote": "<verbatim plan quote|null>" } ],
  "plan_to_spec": [ { "plan_item": "<verbatim plan quote>",
                      "status": "traces" | "extra",          // extra => OVERSPEC
                      "spec_quote": "<verbatim spec quote|null>" } ],
  "verdict": "adheres" | "underspec" | "overspec" | "n/a",   // underspec wins over overspec
  "examined": [ "<artifact ids read>" ]
}
```
Form-check `plan-spec-coverage`: both arrays present and non-trivial (or empty under
a verified N/A scope); every `requirement`/`plan_item`/`*_quote` exists verbatim in
the fetched spec/plan text; `verdict` consistent with the cells; `scope` matches the
recompute.

### 3. `code-implements-plan` — N/A if no code
Prefetch: plan text + diff. **Bidirectional matrix.** The **code side reuses
`traces-exist-in-diff`**, which iterates a fixed container — so the schema MUST emit
that exact shape (an absent `files` key makes the check pass vacuously):
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
  "verdict": "adheres" | "underplan" | "overplan" | "n/a",
  "examined": [ "<artifact ids read>" ]
}
```
Form-checks: reuse `traces-exist-in-diff` (validates `files[].verdicts[].findings[]`
anchors against the independently-fetched diff) + `code-plan-coverage`
(`plan_to_code` complete; every `plan_item` quote in the plan text; `verdict`
consistent; `scope` verified).

### 4. `mm-compliance` — reused, standalone comment removed
The existing `mm-compliance-gate.md` agent + `mm-compliance.evidence.schema.json` +
`evidence-present`/`mm-questions-present` checks, repositioned from a top-level phase
to a fanout leg. **Two changes:** its standalone `add-comment` safe-output is dropped
(the gate renders mm status, avoiding a double-post — `mm-questions-present` checks
the evidence, not the comment, so it is unaffected), and its blocking logic
(`verdict: diverges`) moves out of `conclude-mm-compliance.py` (retired) into the
gate's rollup. The `_mental_model` branch checkout step is kept; if that branch is
absent the leg must still write evidence and pass (negative attestation).

### 5. `docs-updated-appropriately` — always applicable
Replaces deterministic `docs-updated-with-code.py`. The agent **self-identifies the
docs relevant to the change**, then per relevant doc → `updated_appropriately`
(diff-anchored) | `missing` | `inadequate`. Block if any relevant doc is not handled;
negative attestation (`examined` + pass) when nothing is relevant. Form-check
`docs-coverage`: `examined` trace present; each named doc is a real repo path; each
"updated" claim anchors to the diff.

### 6. `tests-updated-appropriately` — N/A if no code
Replaces `tests-updated-with-code.py`; same agentic shape as docs. Form-check
`tests-coverage` mirrors `docs-coverage`.

## N/A leg contract (no leg is ever skipped)

The engine fan-out dispatches **every** branch unconditionally (`next.py:129`); there
is no conditional/skip-leg primitive, and the join is a strict AND-barrier requiring
every leg to reach `done` (`join.py:200-205`); a node reaches `done` only with ≥1
passing iterate-severity verdict (`lib.py:727-737`). Therefore **"N/A" never means
"skipped"** — all six agents dispatch on every run and each must reach `done`:

- An out-of-scope leg writes evidence with its scope flag false (e.g.
  `"scope": {"code_changed": false}`), `"verdict": "n/a"`, and empty matrices, plus
  an `examined` attestation.
- The leg's form-check **passes** when (i) the agent's scope flag matches the
  deterministic recompute and (ii) the matrices are correspondingly empty — so the
  leg reaches `done` and the join clears.
- The gate rollup treats an `n/a` verdict as "contributes nothing."

(This is why the Risks note "six parallel codex agents" — N/A saves no dispatch.)

## Artifact resolution (deterministic, shared)

Extends `_locate.py`:
- **issue-link** — closing keywords in the PR body (`Closes|Fixes|Resolves #N`)
  and/or the GraphQL `closingIssuesReferences` connection. No link →
  `spec-solves-issue` self-attests N/A.
- **spec / plan presence** — `_paths.is_spec_path` / `is_plan_path` over changed
  files (Decision 4), no PR-body fallback.
- **code-changed** — any `_paths.is_code` file in the diff.

**Where the text comes from for the checks.** A zone-3 check self-fetches its ground
truth with the checks job's read-only token, as `local-review-evidence.py` does via
`_review_fetch` (`checks/_review_fetch.py:5-10`). So `spec-solves-issue-coverage` /
`plan-spec-coverage` fetch the spec/plan file text at the PR head (`gh api
repos/{repo}/contents/{path}?ref={head}`, the call the agent prefetch uses) and the
linked **issue body** (`gh api repos/{repo}/issues/{N}`). **The linked-issue fetch
needs `issues: read`, which the checks job does not currently hold** (it grants only
`contents`/`actions`/`pull-requests: read` — `agentic-engine.yml:342-345`; the
`_review_fetch` precedent only reads the PR's *own* issue thread, covered by
`pull-requests: read`). So this design **adds `issues: read` to the checks job** (a
generic, protocol-agnostic grant — the one engine-workflow change noted in the
header). **Fail-closed rule:** the coverage check must distinguish "issue fetch
failed" (→ the check fails/iterates) from "issue text has no match" (→ a real
verdict); collapsing both to empty would fail-*open* a presence gate on a private
repo. The agent-leg side already has `issues: read` (`preflight-agent.md:23`), so the
new `*-agent.md` prefetch steps replicate that.

## The gate (`preflight-gate`)

A root-level `agent` node, **declared as the sibling immediately before `overview`**
in `states[]` (the cursor advances by declared sibling order, not the `next` field —
`next.py` cursor advance). Three engine facts shape it:

- **Conclude/halt runs only for a root-level agent that reaches `done`**
  (`advance.py:615`, inside the `process == "done"` branch at `advance.py:576`). A
  node reaches `done` only with ≥1 passing verdict; `lib.decide([])` →
  `iterate`/`failed` (`lib.py:727-728`).
- **A root agent's own `publish:` key is ignored** (`advance.py:284-298`), so the
  consolidated comment is posted by `conclude-preflight` (which runs unconditionally
  in the advance job, holding `PUBLISH_TOKEN` + `PR`), not an engine publish hook.
- **An agent after a join reads every leg via `inputs[]`** (`triage` precedent), both
  for the agent (materialized as `inputs/<as>.json`) and for the conclude hook
  (materialized into `CONCLUDE_INPUTS_DIR`).

**The gate agent is a synthesis step, not a noop.** It declares `inputs[]` with the
six leg aliases — `spec-solves-issue`, `plan-implements-spec`, `code-implements-plan`,
`mm-compliance`, `docs-updated-appropriately`, `tests-updated-appropriately` (alias =
leg id) — reads those six materialized evidences, and writes a **consolidated
evidence** (`preflight-gate.evidence.schema.json`): one cell per leg `{ leg, verdict,
scope, summary }`. Its form-check `preflight-gate-coverage` requires exactly one
well-formed cell per leg, so the gate reaches `done` (this is the mandatory passing
check). This consolidated evidence is what `mrp` consumes.

**`conclude-preflight` (rewritten) is authoritative for blocking.** It independently
re-reads the six leg evidences via `CONCLUDE_INPUTS_DIR` (not trusting the agent's
render), reads each leg's verdict + form-verified scope flags, and applies:

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
N/A (contributes nothing):  any leg whose verdict is n/a
```

`blocked = (any block reason)`; returns `{conclusion, summary, blocked, reasons[],
warnings[]}`. It also **posts the one consolidated preflight comment** (per-leg
status; rendered via `gh api -f body=@file` / argument vector — never shell-string
interpolation of agent text) and writes `verdict.json`. `on_blocked: halt` then marks
the run `failed`, writes the `halted` marker, and posts the `/override` notice
(`advance.py:615-648`). The gate agent's render is for `mrp`/human consumption; the
authoritative block decision is `conclude`'s independent read of the legs.

**Override:** `/override` reads the `halted` marker and advances the root cursor to
`overview` (`next.py:do_override`). One `/override` clears the whole gate at once.

## Engine grounding (why ~no engine change)

Verified against the code (all cites confirmed accurate by both reviews):
- Root-level `agent` runs `conclude` + honors `on_blocked: halt`, but only in the
  `done` branch — `advance.py:576`, `advance.py:615`, halt `advance.py:615-648`.
- A node with no checks yields zero verdicts and can never be `done` —
  `lib.py:727-728`; hence the gate's `preflight-gate-coverage` check.
- A root agent's own `publish:` key is **not** honored — `advance.py:284-298`; hence
  the comment moves to `conclude-preflight`.
- Fanout legs run **publish only, never conclude** — `advance.py:684-694`; every
  branch is dispatched unconditionally (`next.py:129`); the join is a strict
  AND-barrier (`join.py:200-205`) — hence the N/A leg contract.
- An agent after a join reads every leg via `inputs[]` — `lib.py:172-214`,
  `advance.py:357-374` (the `triage` precedent).
- **Resolver caveat:** the resolver matches a node's own id *before* scanning fanout
  legs (`lib.py:191-199`); since the new fanout's id is `preflight`, `mrp`'s
  `{from: preflight}` resolved to an unwritten path — hence the mrp repoint to
  `preflight-gate` (which writes the consolidated evidence). `validate_protocol`
  does **not** validate `inputs[].from`, so the gate's input wiring is covered by a
  dedicated pytest, not lint.
- **The one engine-workflow change:** `issues: read` added to the checks job
  permissions (generic; see Artifact resolution).

## File map

```
.github/agent-factory/protocols/code-review/
  protocol.json                          # REWRITE: preflight -> fanout(6) + join-preflight + preflight-gate
                                         #   (gate declared immediately before overview); delete mm-compliance
                                         #   phase; REPOINT mrp input {from: preflight} -> {from: preflight-gate}
  spec-solves-issue.evidence.schema.json     # NEW
  plan-implements-spec.evidence.schema.json  # NEW (bidirectional matrix; field names pinned above)
  code-implements-plan.evidence.schema.json  # NEW (code side uses the traces-exist-in-diff container)
  docs-coherence.evidence.schema.json        # NEW
  tests-coherence.evidence.schema.json       # NEW
  preflight-gate.evidence.schema.json        # NEW (consolidated rollup: one cell per leg — what mrp reads)
  mm-compliance.evidence.schema.json         # REUSE (unchanged)
  checks/
    _locate.py                           # EXTEND: issue-link detect/fetch; drop description fallback
    spec-solves-issue-coverage.py        # NEW (self-fetches issue body [needs issues:read] + spec text)
    plan-spec-coverage.py                # NEW (self-fetches spec + plan text)
    code-plan-coverage.py                # NEW
    docs-coverage.py                     # NEW
    tests-coverage.py                    # NEW
    preflight-gate-coverage.py           # NEW (one cell per leg -> gate reaches `done`)
    traces-exist-in-diff.py              # REUSE (code-implements-plan code side)
    evidence-present.py, mm-questions-present.py  # REUSE (mm-compliance leg)
    docs-updated-with-code.py, tests-updated-with-code.py  # RETIRE
    spec-present.py, plan-present.py, adherence-coverage.py  # RETIRE (superseded by the per-leg coverage checks)
  publish/
    conclude-preflight.py                # REWRITE: 6-leg rollup; posts the consolidated comment + verdict.json
    publish-verdict.py                   # RETIRE (folded into conclude-preflight)
    conclude-mm-compliance.py            # RETIRE (absorbed into the gate rollup)

.github/workflows/
  spec-solves-issue-agent.md (+ .lock.yml)      # NEW
  plan-implements-spec-agent.md (+ .lock.yml)   # NEW
  code-implements-plan-agent.md (+ .lock.yml)   # NEW
  docs-coherence-agent.md (+ .lock.yml)         # NEW
  tests-coherence-agent.md (+ .lock.yml)        # NEW
  preflight-gate-agent.md (+ .lock.yml)         # NEW (reads 6 leg inputs -> consolidated evidence)
  mm-compliance-gate.md (+ .lock.yml)           # REUSE, minus its add-comment safe-output
  agentic-engine.yml                            # +issues: read on the checks job (the one generic engine grant)

.github/agent-factory/engine/                    # otherwise UNTOUCHED (no logic change)
```

## Phasing (3 independently-shippable phases)

Each phase ends in pytest-green + one live `/review`.

- **Phase A — chain + synthesis gate.** Build `fanout → join-preflight →
  preflight-gate` (gate declared immediately before `overview`; reads 6 legs →
  consolidated evidence; `preflight-gate-coverage`; `conclude-preflight` rollup +
  comment); split today's two verdicts into the three chain legs; add issue-link +
  `issues: read` on the checks job + drop the description fallback; write the N/A leg
  contract; **repoint `mrp`'s `preflight` input to `preflight-gate`**. Temporarily
  keep `mm-compliance` as its current phase and docs/tests as the current advisory
  checks *on the gate* so nothing regresses.
- **Phase B — fold in mm-compliance.** Move it to a fanout leg (dropping its
  `add-comment`); move its block into the rollup; delete `conclude-mm-compliance.py`
  + the old phase.
- **Phase C — agentic docs/tests.** Replace the two deterministic checks with the
  `docs-coherence`/`tests-coherence` legs (now blocking).

## Testing & verification

- **pytest — `conclude-preflight` rollup (table-driven).** The harness must
  **materialize a `CONCLUDE_INPUTS_DIR` with six leg-evidence files** (+ `BLOCKING`
  env) — the rewritten hook reads the inputs dir, not a single evidence arg. Cover
  every branch: no-issue / issue+no-spec / solves / doesn't-solve / no-code N/A /
  underspec / overspec / underplan / overplan / mm compliant+diverges / docs+tests
  adequate+inadequate; assert `blocked` + `reasons`/`warnings`.
- **pytest — each new check:** completeness, anchoring (incl. a `traces-exist-in-diff`
  case proving leg-3's `files[].verdicts[].findings[]` shape is rejected on a bad
  anchor — not vacuously passed), N/A-pass (verified-N/A scope + empty matrices →
  pass), scope-disagreement (agent vs. recompute → fail), and the issue-fetch
  fail-closed rule.
- **pytest — gate input wiring (replaces a false lint claim).** Assert directly (the
  `test_mm_pipeline_wiring.py:33-54` pattern) that each of the six gate inputs
  resolves to the leg's evidence path, that `mrp`'s `preflight` input now resolves to
  `preflight-gate`, and that the gate reads a non-empty consolidated evidence (not
  `{}`).
- **Test migration (must land green per phase):**
  - `test_resolve_agent_unit.py:38-41` — loads the real protocol and asserts
    `["preflight"]` is an agent with `max_iterations: 2`; once `preflight` is a
    fanout this breaks. Repoint the assertion to `["preflight-gate"]` (or a leg path)
    and assert `node_kind(["preflight"]) == "fanout"`. **Audit every test that
    `json.load`s the real `code-review/protocol.json`** — this is the one the v1 list
    missed (the rest load `code-review-v1`). Phase A.
  - `test_preflight_checks.py` — tests four RETIRED checks; delete the retired-check
    cases (spec/plan in Phase A, docs/tests in Phase C).
  - `test_conclude_preflight.py` — rewrite the harness from single-arg to the
    `CONCLUDE_INPUTS_DIR` shape (Phase A).
  - `test_preflight_coverage.py` — `adherence-coverage` is RETIRED (superseded by the
    per-leg coverage checks); delete/migrate its cases (Phase A).
  - `test_mm_pipeline_wiring.py:54` — update the pinned `preflight` input assertion to
    `preflight-gate` (Phase A).
- **`protocol-lint.py`** structural pass (join names the fanout, agents declare a
  `workflow`, depth within `max_depth`). *It does NOT check `inputs[].from` — that is
  the dedicated pytest above.*
- **`gh aw compile`** for each new agent `.md`; commit the `.lock.yml`.
- **Live:** `/review` on a throwaway PR — clear path + each block path + `/override`.

## Risks

- **Cost.** Six parallel codex agents per preflight (N/A saves no dispatch — see the
  N/A contract). The gate still halts before `overview`.
- **Accepted cost — mandatory spec+plan on every code PR.** With the description
  fallback dropped, any code-touching PR without a committed `docs/superpowers/specs|plans`
  artifact halts and needs `/override`. This repo's own routine commits (CI fixes,
  lock regens, refactors) often lack such artifacts, so they will trip the gate. This
  is **intended**; a future per-repo opt-out could relax it if the override rate
  proves noisy.
- **Accepted cost — blocking docs/tests on a subjective agent verdict.** "Inadequate"
  is a substance judgment the form-check cannot validate (the porch ceiling), yet it
  halts (escape: `/override`). **Intended**; the per-leg `on_fail` can later default
  these two back to advisory without a structural change.
- **Persistent leg failure is fail-closed but un-overridable.** If a leg exhausts its
  iterations to `failed`, the join finalizes `failure` *without* reaching the gate, so
  no `halted` marker is written and `/override` refuses. The pipeline is safely
  stopped, but recovery is a re-run, not `/override`.
- **One `/override` clears all reasons** — cannot override mm-divergence while staying
  blocked on adherence. Accepted as the cost of one gate.
- **Gate render vs. authoritative decision.** `mrp` reads the gate agent's
  consolidated render (validated only for completeness), while the block decision is
  `conclude`'s independent read of the legs. A divergent render would mislead the
  merge-readiness pack but cannot mis-gate the pipeline; acceptable given the per-leg
  evidences remain available.
- **`issues: read` on the checks job** slightly widens the zone-3 token; it remains
  read-only and protocol-agnostic. The fail-closed rule prevents a private-repo
  permission gap from fail-opening a presence gate.

## Non-goals

- Judging *correctness* of any verdict (the engine checks form, not substance).
- Posting per-finding inline PR review comments for preflight.
- Any behavioral change to `overview` and downstream phases — **except** repointing
  `mrp`'s `preflight` input alias to the gate (required).
- Any engine *logic* change (the only engine-workflow edit is the generic
  `issues: read` permission grant).
