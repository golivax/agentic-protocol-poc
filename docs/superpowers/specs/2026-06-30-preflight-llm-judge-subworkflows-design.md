# Preflight LLM-Judge Subworkflows — Design

**Status:** approved design, pre-implementation (revised after adversarial review)
**Date:** 2026-06-30
**Protocol affected:** `code-review` (the production pipeline). No engine changes.

## Summary

Restructure the `code-review` **preflight** phase so each of its six adherence
legs becomes a **two-step subworkflow** — the existing deterministic-form-checked
*gather* agent followed by a new LLM *judge* agent that grades the substance
(seriousness) of what the gather found. The authoritative block/pass decision
stays **deterministic** (`conclude-preflight`, zone 4): today's deterministic
block conditions remain a **floor the judge cannot remove**, and the judge's
grades can only **escalate** (add blocks) or annotate seriousness. This adds a
real "LLM judge" (the deliberately-deferred future extension named in
`docs/HOW-IT-WORKS.md`) **without** giving any model authority to *remove* a
deterministic block, and **without** touching `.github/agent-factory/engine/`.

> **Interpretation of the ask.** The user asked for "the preflight phase to be a
> workflow whose gates are subworkflows, using an LLM in a subworkflow." We read
> **"gate" as each adherence leg** — the per-leg pass/fail evaluation points — so
> each leg becomes a `gather → judge` subworkflow. The literal `preflight-gate`
> node stays a single **root** node on purpose: `on_blocked: halt` only fires for
> a root-level agent phase (engine constraint), so the halting decider cannot be
> nested.

## Motivation

Live testing (SiRumCz PR #7) confirmed preflight works, but showed the
`preflight-gate` agent is a *renderer*: it copies each leg's verdict verbatim and
never judges anything; the only substance signal is the coarse per-leg verdict
the gather emits inline. We want richer, independently-produced substance grading
— "is this gap serious enough to block, or noise?" — done by a dedicated LLM step
whose output is itself form-checked, with deterministic code still owning the
decision (and never *weakening* today's guarantees). This is the split the
engine's design anticipates: *"Checks verify form; verification (a judge/human)
verifies substance."* (`docs/HOW-IT-WORKS.md`)

## Global constraints (non-negotiable; bind every plan task)

- **No engine edits.** All work is protocol-local under
  `.github/agent-factory/protocols/code-review/` plus new agents under
  `.github/workflows/`. Do **not** modify `.github/agent-factory/engine/`.
- **Decisions stay deterministic.** Only `conclude-preflight` (zone-4 Python)
  decides block/pass, from check verdicts + form-verified evidence fields. A
  model never decides whether the process advances, and **never removes** a
  deterministic block.
- **LLM only in zone 2.** Every LLM (gather, judge, the thin gate halt-bearer) is
  a dispatched agent that produces `evidence.json`. Checks (zone 3) and the
  conclude hook (zone 4) are deterministic, LLM-free code.
- **Checks verify form, never substance.** A check may assert schema shape,
  coverage, traceability against independently re-derived ground truth, and that
  every finding carries a grade. It must never assert that a grade is *correct*.
- **Ground truth re-derived independently.** A check re-derives the same ground
  truth the gather's own check uses (the diff *and/or* the self-fetched issue /
  spec / plan text, per leg); it never trusts agent-produced data.
- **Agent strings via `env:`/argv only**, never interpolated into a `run:` block
  — the zone-4 job holds the state PAT.
- **A node with no passing iterate-verdict can never reach `done`.** Each judge's
  form check is `on_fail: iterate`.
- **gh-aw pinned to v0.77.5**; codex/`gpt-5.5` engine via the OpenAI gateway under
  `engine.env`; `.md` is source, `.lock.yml` is the committed compiled output.

## Current state

`preflight` is a `fanout` of six **flat** agent legs → `join-preflight` →
`preflight-gate` (root `agent` + `conclude: conclude-preflight` +
`on_blocked: halt`) → `overview`. Each leg's gather verdict is **deterministically
computed from form-verified cells** by its coverage check (e.g.
`plan-spec-coverage` forces `verdict == "underspec"` iff any `spec_to_plan` cell
is `missing`) — so the verdicts are *form signals, not LLM prose*.

| leg id | gather agent (existing) | gather checks (existing) | gather evidence + how its check derives ground truth |
|---|---|---|---|
| `spec-solves-issue` | `spec-solves-issue-agent` | `evidence-present`, `spec-solves-issue-coverage` | `{matrix[] (cells keyed by `problem`), scope{issue_linked,spec_present}, verdict, examined}`; check **self-fetches the issue body + spec text** (PR_BODY + `_artifact_fetch`), not the diff |
| `plan-implements-spec` | `plan-implements-spec-agent` | `evidence-present`, `plan-spec-coverage` | `{spec_to_plan[] (keyed `requirement`), plan_to_spec[] (keyed `plan_item`), scope, verdict, examined}`; check **self-fetches spec + plan text** |
| `code-implements-plan` | `code-implements-plan-agent` | `evidence-present`, `code-plan-coverage`, `traces-exist-in-diff` | `{plan_to_code[], files[] (diff-anchored findings: side/line/existing_code), scope, verdict, examined}`; check uses the **diff** + self-fetched plan text |
| `mm-compliance` | `mm-compliance-gate` | `evidence-present` | `{verdict: compliant\|diverges, divergences[] (free-text), examined}` — **no `scope`, no diff anchors** |
| `docs-updated-appropriately` | `docs-coherence-agent` | `evidence-present`, `docs-coverage` | `{scope{code_changed}, items[] (keyed `path`), verdict: adequate\|inadequate, examined}`; **always applicable** (no `n/a`) |
| `tests-updated-appropriately` | `tests-coherence-agent` | `evidence-present`, `tests-coverage` | `{scope{code_changed}, items[], verdict: adequate\|inadequate\|n/a, examined}` |

`conclude-preflight.rollup(...)` blocks on **nine** conditions:
`issue_linked & !spec_present` · `spec & does-not-solve` · `code & !spec_present` ·
`code & !plan_present` · `plan==underspec` · `code==underplan` · `mm==diverges` ·
`docs==inadequate` · `code & tests==inadequate`; warns on `overspec`/`overplan`.
**All nine are preserved** (see Decision, below).

## Proposed architecture

```
preflight   kind: fanout
  ├─ spec-solves-issue           states: [ spec-solves-issue (gather) → spec-solves-issue-judge ]
  ├─ plan-implements-spec        states: [ plan-implements-spec (gather) → plan-implements-spec-judge ]
  ├─ code-implements-plan        states: [ code-implements-plan (gather) → code-implements-plan-judge ]
  ├─ mm-compliance               states: [ mm-compliance (gather) → mm-compliance-judge ]
  ├─ docs-updated-appropriately  states: [ docs-updated-appropriately (gather) → docs-updated-appropriately-judge ]
  └─ tests-updated-appropriately states: [ tests-updated-appropriately (gather) → tests-updated-appropriately-judge ]
  next → join-preflight
join-preflight   kind: join, of: preflight
preflight-gate   kind: agent          (thin root HALT-BEARER; see below)
     inputs:   one per leg, written as { from: <leg/branch id>, as: <leg> }   ← resolver returns the branch's TERMINAL judge evidence
     params:   { legs: [ the 6 leg ids ] }
     checks:   preflight-gate-coverage (on_fail: iterate) + local-review-evidence (on_fail: advisory)
     conclude: conclude-preflight       (DETERMINISTIC decide; reads the 6 judges)
     on_blocked: halt
     next → overview
```

- Each branch drops its `workflow` key and gains `states: [gather → judge]` (the
  branch is XOR flat-vs-sub-pipeline). The **gather** sub-state keeps the existing
  agent id and its existing checks unchanged; the **judge** is a new sibling
  `id: <leg-id>-judge`, `workflow: <leg-id>-judge-agent`.
- **Same nesting primitive `deep-review-stub` uses** (a `fanout` branch carrying
  `states[]`, a root-level `join` whose `of` names the sibling fanout, `inputs[]`
  for data flow); ours is shallower (linear `gather → judge`, no fanout nested
  inside a leg).
- **Depth:** deepest leaf `[preflight, <leg>, <leg>-judge]` = depth **3**, within
  the engine default `max_depth` 5 (`deep-review-stub` runs at depth 4). **No
  `max_depth` change.** The plan confirms with `protocol-lint.py`.
- **Inputs addressing (verified against `lib.resolve_inputs`):** writing
  `{from: <branch-id>}` from the root gate resolves to the branch's **terminal
  sub-state** (the judge), because the path-aware resolver appends
  `branch.states[-1].id`. **Address the branch id, never the judge sub-state id**
  (the latter is neither a root sibling nor a fanout-branch id, so it would fail
  to resolve). This requires the judge to be the **terminal** step of its leg.
  Likewise the judge reads its gather via `{from: <gather/leg id>, as: gather}`
  (direct sibling within the leg's sub-pipeline). The plan's **first task**
  validates both resolutions for all six legs (path-aware path, `consuming_path`
  set) before building the rest — the existing unit coverage only exercises the
  legacy non-path-aware path, so this assertion is mandatory, not assumed.

## The gather → judge contract (per leg)

| step | LLM does | deterministic check verifies (FORM only) |
|---|---|---|
| **gather** (reused agent, unchanged) | extract matrix/items/divergences + scope + verbatim anchors; emit the deterministically-computed `verdict` | existing per-leg checks unchanged (scope-agreement, coverage, verdict-consistency, traceability where applicable) |
| **judge** (new, light) | read the gather's **form-verified** evidence via `inputs[]`; re-state each gather finding with a `severity` grade + rationale; **echo the gather's `scope` and `verdict` verbatim** | new **per-leg-parametrized** `judge-coverage`: re-derives the leg's ground truth, verifies scope-echo, verdict-echo, coverage, per-leg traceability, and a valid grade on every finding |

The judge re-states the gather's **full** finding set, each annotated with a
`severity`, and copies the gather's `scope` and `verdict` forward verbatim so the
gate/conclude needs only the terminal judge evidence per leg (one input per
branch). The judge never re-derives scope/verdict; `judge-coverage`
independently re-checks that the echo is faithful (so the floor stays
form-verified, not LLM-asserted).

### Judge evidence schema (one schema, **per-leg parametrized**)

```json
{
  "leg": "<leg-id>",
  "scope": { "...echoed verbatim from the gather (object); OMITTED for mm-compliance..." },
  "gather_verdict": "<echoed verbatim from the gather's verdict — the deterministic floor signal>",
  "graded_findings": [
    {
      "ref": "<per-leg key identifying the gather finding (see table); REQUIRED>",
      "severity": "blocking | advisory | noise",
      "rationale": "<1–2 sentences>",
      "anchor": { "...per-leg shape; see table; OPTIONAL where the leg has no anchor..." }
    }
  ],
  "verdict": "block | warn | clear | n/a",
  "examined": [ "<gather refs / artifacts the judge actually read>" ]
}
```

Per-leg `ref` + anchor mode (drives `judge-coverage`):

| leg | a "finding" is | `ref` | anchor mode (traceability) | scope mode |
|---|---|---|---|---|
| `spec-solves-issue` | a `matrix` cell | `problem` | verbatim in **fetched issue/spec text** | recompute `issue_linked`,`spec_present` |
| `plan-implements-spec` | a `spec_to_plan` / `plan_to_spec` cell | `requirement` \| `plan_item` | verbatim in **fetched spec/plan text** | recompute `spec_present`,`plan_present`,`code_changed` |
| `code-implements-plan` | a `plan_to_code` cell / `files[].findings` entry | `plan_item` / file+anchor | **diff** anchor (`side`/`line`/`existing_code`) | recompute `plan_present`,`code_changed` |
| `mm-compliance` | a `divergences[]` entry | divergence index/text | **citation only** (no diff/scope) | **none** (mm has no scope; always in-scope) |
| `docs-updated-appropriately` | an `items[]` entry | `path` | path ∈ **changed-files**, **except `status=="missing"`** (a missing doc is not in the diff → graded by `ref` only, no anchor) | `code_changed` (always applicable) |
| `tests-updated-appropriately` | an `items[]` entry | `path` | path ∈ **changed-files**, **except `status=="missing"`** (same exemption) | `code_changed` (n/a allowed) |

- **Anchorless cells (no traceability anchor, graded by `ref` + severity only):**
  `spec-solves-issue` `not_addressed` cells (null `spec_quote`), and docs/tests
  `items[].status=="missing"` entries (a missing doc/test path is by definition
  not in the changed-files). For `plan`/`code`, a `missing`/`not_addressed` cell's
  `ref` (`requirement`/`plan_item`) **is** still anchorable to the fetched
  spec/plan text — only the cross-side quote is absent — so those are NOT exempt
  on `ref`. An anchorless cell's severity is advisory-to-the-comment, **never
  load-bearing for the floor** (the floor reads the form-verified `gather_verdict`,
  not individual grades).
- Out-of-scope / `n/a` (per the leg's applicability — e.g. `spec-solves-issue`
  with no linked issue; any code-gated leg on a no-code PR): `graded_findings`
  is `[]`, `verdict` is `n/a`, `scope` echoes the out-of-scope flags. **`docs` is
  never `n/a`** (always applicable); **`mm` is never `n/a`** (always in-scope).
- `verdict` is the judge's advisory roll-up; `conclude` recomputes and never
  treats it as authoritative.

### `judge-coverage` check (one file, **per-leg dispatched** via `CHECK_PARAMS`; zone 3)

ABI: `judge-coverage.py <evidence.json> <diff.txt> <changed-files.txt>`,
prints `{"check","pass","feedback"}`, exit 0. Reads `CHECK_PARAMS` for the leg's
mode (which scope flags to recompute incl. `"none"` for mm; the anchor mode; the
`ref` key) and **self-fetches the same ground truth the leg's gather check uses**
(PR_BODY + issue/spec/plan text via `_artifact_fetch` for spec/plan/code legs;
the diff for code; changed-files for docs/tests; nothing for mm). It reuses the
gather checks' helpers (`_locate`, `_paths`, `_artifact_fetch`, `_diff`,
`traces-exist-in-diff` logic). Verifies, FORM only:

1. **scope-echo** == independent recompute (mode `"none"` ⇒ accept absent/empty).
2. **verdict-echo** == the leg's independently-recomputed verdict (so the floor
   `conclude` reads is form-verified, not LLM-asserted). Each gather check's
   verdict fold is currently **inline in its `main()`** (e.g. `plan-spec-coverage`'s
   `underspec`/`overspec` derivation; `_coherence`'s `inadequate` fold) — so a
   prerequisite plan task **extracts each into a pure importable helper**
   (`plan_spec_verdict(...)`, `code_plan_verdict(...)`, `spec_solves_verdict(...)`,
   `coherence_verdict(items)`) that BOTH the gather check and `judge-coverage`
   import, giving one source of truth (no duplicated/divergent verdict logic).
   **Exception — `mm-compliance`:** mm's verdict is an LLM compliance judgment with
   **no deterministic recompute**, so zone 3 cannot verify the echo. For mm,
   `judge-coverage` verifies only `gather_verdict ∈ {compliant, diverges}` (enum)
   and that each graded `divergences` entry cites a gather divergence. The mm floor
   (`mm==diverges`) therefore rests on the LLM verdict at the **same trust level as
   today** (today `conclude` reads it straight from the mm gather, also only
   schema-checked) — the only new surface is the judge's copy hop, mitigated by a
   "copy verbatim" instruction + the enum check. See Open Questions for the
   `mm`-gather-only alternative that removes even that hop.
3. out-of-scope ⇒ `graded_findings == []` and `verdict == "n/a"` (only for legs
   whose mode permits `n/a`).
4. in-scope ⇒ **coverage** (every finding the leg's gather coverage logic
   requires is present and graded), **traceability** per the leg's anchor mode
   (diff line / fetched-artifact quote / changed-file path / citation), every
   `severity ∈ {blocking, advisory, noise}`, and `examined` non-empty.

`on_fail: iterate`. The legit `n/a`/empty-findings case **is** reachable-to-`done`
(empty `graded_findings` is valid form), so a judge only `failed`s on genuinely
malformed output after `max_iterations` (2, matching the gathers).

It is **not diff-only** and it does **not** need the gather file: it
independently re-derives the leg's ground truth (the same way the gather check
does) and verifies the judge's self-contained superset against it.

## The deterministic decision (`conclude-preflight`, enriched)

`conclude-preflight` (zone 4) stays the sole authoritative decider. It
independently re-reads each leg's terminal **judge** evidence from
`CONCLUDE_INPUTS_DIR` (never trusting the gate renderer's `argv[1]`), and computes
`blocked` as **floor + escalation**:

- **Deterministic floor (the judge can NEVER remove) — all nine of today's
  conditions, read from the judges' form-verified echoed `scope`/`gather_verdict`:**
  `issue_linked & !spec_present` · `spec gather_verdict==does-not-solve` ·
  `code & !spec_present` · `code & !plan_present` · `plan==underspec` ·
  `code==underplan` · `mm==diverges` · `docs==inadequate` ·
  `code & tests==inadequate`.
- **Judge escalation (ADD-only):** for any *in-scope* leg whose `gather_verdict`
  is **not** already a floor-block, also block if that leg's judge graded ≥ 1
  finding `blocking`. (The judge can promote a clean leg to blocking on substance
  the form check can't see; it can never demote a floor-block.)
- **Warnings:** today's `overspec`/`overplan` (unchanged) plus any in-scope
  `advisory` grades.

`final blocked = any(floor reason) or any(in-scope, non-floor leg with a blocking grade)`.

Decision table — **per-leg view** (the three cross-leg *presence* floors —
`issue_linked & !spec_present`, `code & !spec_present`, `code & !plan_present` —
are aggregate conditions evaluated **globally** over the spec/plan/code legs'
echoed flags, per the floor list above, not attributable to a single leg row):

| gather_verdict / scope | judge grades | outcome |
|---|---|---|
| floor-block (e.g. does-not-solve, underspec, underplan, diverges, inadequate; or a global presence floor) | any | **block** (floor; judge cannot remove) |
| in-scope, non-floor | ≥1 `blocking` | **block** (judge escalation) |
| in-scope, non-floor | only `advisory` | warn |
| in-scope, non-floor | only `noise` / none | clear |
| out-of-scope (`n/a`) | `[]` | no block, no warn |

For every leg the verdict enum partitions **exhaustively** into floor-block vs
non-floor (spec: `does-not-solve`=floor, else non-floor; plan: `underspec`=floor;
code: `underplan`=floor; mm: `diverges`=floor, `compliant`=non-floor; docs/tests:
`inadequate`=floor, `adequate`/`n/a`=non-floor), so "non-floor" is total and
escalation never falls in a gap or double-counts a floor-block.

`on_blocked: halt` halts the run; a maintainer `/override <reason>` advances one
phase, exactly as today. `conclude-preflight` emits the same single consolidated
PR comment, now showing each leg's gather verdict **and** the judge's seriousness
(e.g. `plan — block (underspec; judge: 2 serious)`, `docs — block (inadequate)`,
`code — block (judge escalated: 1 blocking)`, `tests — clear`). Agent rationale
strings are passed to `gh` as argv elements, never interpolated.

## Failure modes (corrected)

Two distinct outcomes — do **not** conflate them:

- **A judge fails its form check** (malformed output through `max_iterations`):
  the judge sub-state goes `failed` ⇒ `complete_sequence` marks the leg `failed`
  ⇒ the `join-preflight` AND-barrier fails ⇒ **the run ends red at the join**;
  `preflight-gate`/`conclude-preflight` never run, and `/override` does **not**
  apply (override only advances a *blocked gate*, not a failed join). This is the
  engine's normal hard-fail (merge stays gated). It is **not** a "block verdict."
- **A judge produced valid evidence the conclude hook later finds missing/garbled**
  at zone 4 (`_load_leg` returns `{}`): `conclude` treats that leg as a block
  (fail-safe). This is reachable only because the judge passed its form check and
  the leg reached the gate. **⚠ This is a behavior change:** today
  `conclude-preflight._load_leg` returns `{}` for a missing leg and the rollup
  reads it as *no signal* (`_verdict({})→"n/a"`, `_flag({},…)→False`) — i.e. today
  a missing leg silently does **not** block. The new design flips that to
  fail-safe-block; the Modified-files list + a `test_conclude_preflight.py` case
  must cover it explicitly.

The spec does **not** claim `conclude` can rescue a form-failed judge (it can't —
see the join semantics above).

## Trust zones (unchanged invariant)

| zone | runs | new design |
|---|---|---|
| 2 — agent (read-only repo + LLM creds) | gather, judge, gate halt-bearer | only place a model runs; output is `evidence.json` |
| 3 — checks (no credentials) | gather checks, per-leg `judge-coverage` | form only; re-derive ground truth (diff + self-fetched artifacts) |
| 4 — conclude (state PAT + publish token) | `conclude-preflight` | deterministic floor+escalation decide; halt; comment |

Judge grades are *inputs* to the zone-4 decision and can only *add* blocks; no LLM
runs in zone 3 or 4; no engine changes.

## Files

**Reused as-is (become the gather steps):** the six existing leg agents
(`spec-solves-issue-agent`, `plan-implements-spec-agent`,
`code-implements-plan-agent`, `mm-compliance-gate`, `docs-coherence-agent`,
`tests-coherence-agent`) + their checks (`spec-solves-issue-coverage`,
`plan-spec-coverage`, `code-plan-coverage`, `traces-exist-in-diff`,
`docs-coverage`, `tests-coverage`, `evidence-present`). Minor prompt tweak only if
needed to stabilize the `ref`/anchor fields the judge consumes.

**New:**
- 6 judge agents: `.github/workflows/<leg-id>-judge-agent.md` (+ committed
  `.lock.yml`), codex/`gpt-5.5`, gateway under `engine.env`, `noop` safe-output,
  evidence-artifact post-step — same frontmatter pattern as the gather agents.
- 1 judge evidence schema (per-leg parametrized) under
  `.github/agent-factory/protocols/code-review/`.
- 1 check `checks/judge-coverage.py` (executable `100755` + shebang; per-leg
  dispatch on `CHECK_PARAMS`; reuses `_locate`/`_paths`/`_artifact_fetch`/`_diff`
  and the `traces-exist-in-diff` anchor logic).

**Modified:**
- **Verdict-helper refactor (prerequisite):** extract each gather check's inline
  verdict fold into a pure importable helper in the same module
  (`plan-spec-coverage.py` → `plan_spec_verdict`; `code-plan-coverage.py` →
  `code_plan_verdict`; `spec-solves-issue-coverage.py` → `spec_solves_verdict`;
  `_coherence.py` → `coherence_verdict`); the gather check AND `judge-coverage`
  both call it (single source of truth). Likewise extract `traces-exist-in-diff`'s
  anchor logic into an importable `_trace.py` both checks import (avoids importing
  a hyphenated filename). Pure refactor — gather behavior unchanged, guarded by the
  existing gather-check tests.
- `protocol.json`: 6 branches flat → sub-pipeline (`states: [gather → judge]`);
  each judge sub-state gets `inputs: [{from: <gather>, as: gather}]`, its evidence
  schema, `params` (the leg mode), `checks: [{run: judge-coverage, on_fail:
  iterate}]`, `max_iterations: 2`; `preflight-gate.inputs` repointed to
  `{from: <leg/branch id>}` × 6 (resolves to the terminal judges); `params.legs`
  unchanged.
- `publish/conclude-preflight.py`: read the judges' echoed `scope`/`gather_verdict`
  + `graded_findings`; keep all nine floor conditions; **add** the judge-escalation
  blocks; **change the missing-leg behavior** from "no signal" to **fail-safe block**
  (today `_load_leg`→`{}` reads as `n/a`/`False`; new: a missing/garbled leg blocks);
  render the enriched comment.
- `checks/preflight-gate-coverage.py` + `preflight-gate-agent.md`: read the judge
  cell shape (one graded cell per declared leg) instead of the old leg-verdict cell.

### Why keep the `preflight-gate` node at all

With six judges + `conclude` re-reading them, the gate agent no longer *decides*
anything. It is retained **only as the root-level halt-bearer**: `on_blocked:
halt` fires solely for a root-child *agent* phase carrying a `conclude` hook, and
the deciding/halting node cannot be nested. The agent is kept minimal (reads the
judges, writes a thin evidence object, `noop`); `conclude-preflight` owns the
real decision + the comment. (A no-agent gate node was considered but is not a
supported shape for `on_blocked: halt` today, and adding one would require engine
changes — out of scope.)

## Testing

- **Unit (pytest, `tests/`):**
  - `test_judge_coverage.py`: per-leg modes — scope-echo agreement (incl. mm
    `"none"`), verdict-echo agreement, coverage, **per-leg traceability** (a
    spec/plan judge finding anchored to fetched-artifact text passes where a
    diff-only rule would wrongly reject it; a code finding anchored to the diff;
    a docs/tests finding anchored to a changed-file path; an mm citation),
    fabricated-anchor rejection, missing-severity rejection, out-of-scope `n/a`
    acceptance (and rejection for docs/mm which have no `n/a`), `on_fail: iterate`.
  - Extend `test_conclude_preflight.py`: a **floor-vs-escalation truth table** —
    every one of the nine floor conditions still blocks regardless of any judge
    grades (the "deterministic floor" regression); an in-scope non-floor leg with
    a `blocking` grade blocks (escalation); `advisory`⇒warn; `noise`/none⇒clear;
    `n/a`⇒inert; enriched-comment text reflects gather verdict + judge grade; and a
    **missing/garbled leg ⇒ fail-safe block** (the behavior change — today it reads
    as no-signal). Also a `verdict-echo` unit: `judge_coverage` rejects a judge that
    echoes a `gather_verdict` not equal to the shared helper's recompute.
  - Update `test_preflight_gate_coverage.py` for the judge cell shape.
  - `protocol-lint.py` clean; `validate_protocol` passes (every gather/judge
    sub-state has a `workflow`; `join.of` in scope; depth ≤ 5).
  - **Inputs-resolution test (mandatory, the plan's first task):** assert
    `resolve_inputs(consuming_path=['preflight-gate'], inputs=[{from:<leg>}])` →
    `preflight.<leg>.<leg>-judge.evidence.json` for all six legs (path-aware
    path), and that the judge's `{from:<gather>}` resolves to the gather sub-state.
- **Live:** re-run `/review` on SiRumCz PR #7. Expected: each leg runs
  gather → judge; the gate blocks (PR #7 has no spec/plan ⇒ floor blocks fire
  regardless of grades) with a *judged* verdict table; `on_blocked: halt`.

## Rollout / migration

Branch off `feat/backport-protocol-from-yuanrong`; subagent-driven implementation
with task + final reviews; the **first task** is the inputs-resolution validation
(above) — if it fails, stop and reconsider before building six agents. Merge
locally; run the unit suite; then the live re-test. Mid-flight state-branch
instances are unaffected (a fresh `/review` re-enters the new tree).
`dist/install.sh` already restores the codex gateway + exec bits, so installs pick
up the new agents unchanged.

## Open questions deferred to the implementation plan

- **Which cells are genuinely anchorless.** Only `spec-solves-issue`
  `not_addressed` cells (null `spec_quote`) and docs/tests `items[].status=="missing"`
  entries have no traceability anchor — the judge grades them by `ref` + severity
  only. For `plan`/`code`, even a `missing`/`not_addressed` cell's `ref`
  (`requirement`/`plan_item`) **is** still anchorable to the fetched spec/plan text
  (only the cross-side quote is absent), so it is NOT exempt on `ref`. The plan
  verifies the gather schemas mark these cases and that `judge-coverage`'s
  traceability arm exempts exactly this set.
- **`mm-compliance`: keep its judge, or make it the one gather-only exception?**
  mm is the leg where the judge adds least (it is already a focused
  `compliant|diverges` verdict) *and* the one leg whose verdict-echo cannot be
  recomputed in zone 3 (no deterministic recompute exists), so adding a judge hop
  inserts an LLM copy of the floor signal that `judge-coverage` can only enum-check,
  not verify. **Option A (default, keeps uniformity):** mm keeps its judge; the
  judge is instructed to copy `gather_verdict` verbatim and grades divergence
  severity; accept the same trust level as today for the mm floor. **Option B
  (cleaner correctness):** mm stays a single gather-only flat leg (no judge), so
  `conclude` reads mm's verdict directly from the mm gather (a flat branch's
  terminal) with no copy hop — at the cost of breaking strict uniform-on-6. Decide
  in the plan; default A unless the reviewer floor-integrity concern outweighs
  uniformity.
- **Single judge agent vs six.** Six per-leg judge agents (mirroring the gathers)
  keep prompts leg-specific; a single parametrized judge couples the legs.
  Default: six.

## Risks

- **Cost/latency:** ~13 LLM dispatches per preflight (6 gather + 6 judge + 1 thin
  gate) vs 7 today. Accepted. A cheaper future variant could skip the judge on
  legs that are `n/a` or already floor-blocking (noted, not adopted — uniform
  on 6 was chosen for consistency).
- **Per-leg `judge-coverage` complexity:** the check is a dispatcher with six
  arms reusing the gather helpers; the unit matrix above is the guard.
- **`judge-coverage` re-fetches artifacts** (issue/spec/plan) like the gather
  checks — same network/`gh` dependency, same degrade-to-advisory behavior when a
  toolchain/artifact is absent applies as it does for the gather checks.
- **Inputs addressing** relies on the path-aware resolver returning a sub-pipeline
  branch's terminal sub-state — proven in code but not previously exercised live;
  the mandatory first task closes this.
