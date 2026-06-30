# Preflight LLM-Judge Subworkflows — Design

**Status:** approved design, pre-implementation
**Date:** 2026-06-30
**Protocol affected:** `code-review` (the production pipeline). No engine changes.

## Summary

Restructure the `code-review` **preflight** phase so each of its six adherence
legs becomes a **two-step subworkflow** — a deterministic-form-checked
*gather* step followed by a new LLM *judge* step that grades the substance
(seriousness) of what the gather found. The authoritative block/pass decision
stays **deterministic** (`conclude-preflight`, zone 4): it reads the judges'
grades plus the form-verified scope flags and applies a fixed policy. This
introduces a real "LLM judge" (the deliberately-deferred future extension named
in `docs/HOW-IT-WORKS.md`) **without** giving any model decision authority and
**without** touching `.github/agent-factory/engine/`.

## Motivation

Live testing (SiRumCz PR #7) confirmed the current preflight works, but it also
showed the `preflight-gate` agent is a *renderer*: it copies each leg's verdict
verbatim and never judges anything; the only substance signal is the coarse
per-leg verdict the gather agent emits inline (`adheres`/`underspec`/
`inadequate`/…). We want richer, independently-produced substance grading —
"is this gap actually serious enough to block, or is it noise?" — done by a
dedicated LLM step whose output is itself form-checked, with deterministic code
still owning the decision.

This is exactly the split the engine's design anticipates:

> "Checks verify form; verification (a judge/human) verifies substance … Whether
> the agent's opinion is correct is a separate concern (a second LLM judge, or a
> human gate) — not a check." — `docs/HOW-IT-WORKS.md`

## Global constraints (non-negotiable; copied from the engine's design)

These bind every task in the implementation plan:

- **No engine edits.** All work is protocol-local under
  `.github/agent-factory/protocols/code-review/` plus new agent workflows under
  `.github/workflows/`. Do **not** modify `.github/agent-factory/engine/`.
- **Decisions stay deterministic.** The block/pass decision is computed only by
  `conclude-preflight` (zone-4 Python) from check verdicts + form-verified
  evidence fields. A model never decides whether the process advances.
- **LLM only in zone 2.** Every LLM (gather, judge, gate renderer) is a
  dispatched agent that produces `evidence.json`. Checks (zone 3) and the
  conclude hook (zone 4) are deterministic, LLM-free code.
- **Checks verify form, never substance.** A check may assert schema shape,
  coverage, traceability (every anchor appears in an independently re-fetched
  diff), and that every finding carries a grade. It must never assert that a
  grade is *correct*.
- **Ground truth re-derived independently.** Form checks re-fetch the diff
  themselves; they never trust agent-produced diffs.
- **Agent strings via `env:` only**, never interpolated into a `run:` block —
  the zone-4 job holds the state PAT.
- **A node with no passing iterate-verdict can never reach `done`.** Each judge's
  form check is `on_fail: iterate`.
- **gh-aw version pinned to v0.77.5**; codex/`gpt-5.5` engine via the OpenAI
  gateway under `engine.env` (`OPENAI_BASE_URL`); `.md` is source, `.lock.yml`
  is the committed compiled output (`gh aw compile`).

## Current state (what we're changing)

`preflight` is a `fanout` with six **flat** agent legs, each `branch.workflow`
pointing at one agent, followed by `join-preflight` → `preflight-gate`
(root `agent` + `conclude: conclude-preflight` + `on_blocked: halt`) → `overview`.

| leg id | gather agent (existing) | existing form check(s) | evidence shape (today) |
|---|---|---|---|
| `spec-solves-issue` | `spec-solves-issue-agent` | `evidence-present`, `spec-solves-issue-coverage` | `{matrix[], scope{issue_linked,spec_present}, verdict, examined}` |
| `plan-implements-spec` | `plan-implements-spec-agent` | `evidence-present`, `plan-spec-coverage` | `{spec_to_plan[], plan_to_spec[], scope{…}, verdict, examined}` |
| `code-implements-plan` | `code-implements-plan-agent` | `evidence-present`, `code-plan-coverage`, `traces-exist-in-diff` | `{plan_to_code[], files[], scope{…}, verdict, examined}` |
| `mm-compliance` | `mm-compliance-gate` | `evidence-present` | `{verdict: compliant\|diverges, divergences[], examined}` (no `scope`) |
| `docs-updated-appropriately` | `docs-coherence-agent` | `evidence-present`, `docs-coverage` | `{scope{code_changed}, items[], verdict: adequate\|inadequate\|n/a, examined}` |
| `tests-updated-appropriately` | `tests-coherence-agent` | `evidence-present`, `tests-coverage` | `{scope{code_changed}, items[], verdict, examined}` |

All six legs also run `evidence-present` first. `mm-compliance` has **no `scope`
object** (its "findings" are `divergences[]`); its judge echoes `scope: {}` and
grades each divergence's severity.

`conclude-preflight.rollup(...)` blocks on: `issue_linked & !spec_present` ·
`spec_present & does-not-solve` · `code_changed & !spec_present` ·
`code_changed & !plan_present` · `plan.verdict==underspec` ·
`code.verdict==underplan` · `mm.verdict==diverges` · `docs.verdict==inadequate` ·
`code_changed & tests.verdict==inadequate`; warns on `overspec`/`overplan`.

## Proposed architecture

Each preflight branch becomes a **sub-pipeline** (`states: [gather → judge]`).
The root gate stays the deterministic decider (root level so `on_blocked: halt`
fires).

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
preflight-gate   kind: agent          (thin renderer; root-level for halt)
     inputs:   one per leg, from each branch's terminal judge sub-state
     params:   { legs: [ the 6 leg ids ] }
     checks:   preflight-gate-coverage (on_fail: iterate) + local-review-evidence (on_fail: advisory)
     conclude: conclude-preflight       (DETERMINISTIC decide; reads judge grades + scope)
     on_blocked: halt
     next → overview
```

- Within a branch, the **gather** sub-state keeps the existing agent id (so its
  state path and existing checks are unchanged); the **judge** is a new sibling
  sub-state, `id: <leg-id>-judge`, `workflow: <leg-id>-judge-agent`.
- The fanout branch is XOR flat-vs-sub-pipeline, so each branch drops its
  `workflow` key and gains `states: [...]` (per the DSL).
- `join-preflight.of` still names `preflight` (a sibling fanout — satisfies
  `validate_protocol`).
- Deepest leaf is `[preflight, <leg>, <leg>-judge]` — static depth **3**, within
  the engine default `max_depth` 5, so **no `max_depth` change is needed** (for
  reference `deep-review-stub` runs at depth 4). The plan confirms with
  `protocol-lint.py`.

**This is the same nesting primitive `deep-review-stub` uses** — a `fanout`
branch carrying `states[]` (a sub-pipeline of full nodes), joined by a
root-level `join` whose `of` names the sibling fanout, with `inputs[]` for
cross-node data. Our sub-pipelines are a shallower case (linear `gather → judge`,
no fanout nested inside a leg), so they are a strict subset of the patterns
`deep-review-stub` exercises.

## The gather → judge contract (per leg)

| step | LLM does | deterministic check verifies (FORM only) |
|---|---|---|
| **gather** (reused agent) | extract matrix/findings/mappings + **scope flags** + verbatim diff anchors; coarse mechanical verdict | existing checks unchanged: scope-agreement vs independent recompute, coverage, every anchor in the re-fetched diff |
| **judge** (new, light) | read the gather's **form-verified** evidence via `inputs[]`; re-state each finding with a `severity` grade + 1-line rationale; **echo the gather's `scope` verbatim**; emit a rolled `verdict` | new `judge-coverage` (one shared parametrized check): scope-echo == independent recompute, every graded finding's anchor ∈ re-fetched diff, every finding carries a valid `severity`, `examined` present (negative attestation) |

The judge reads the gather output via `inputs: [{from: <gather sub-state>, as: gather}]`
(materialized to `inputs/gather.json`). It never re-fetches or re-derives scope;
it grades substance over already-verified evidence.

### Judge evidence schema (`<leg-id>-judge.evidence.schema.json`)

One schema, reused across legs (leg-specific `ref` semantics described in each
agent's prompt). Shape:

```json
{
  "leg": "<leg-id>",
  "scope": { "...": "echoed verbatim from the gather evidence (same keys the leg's gather emits)" },
  "graded_findings": [
    {
      "ref": "<stable identifier of the gather finding being graded: the plan_item / requirement / item.path / divergence>",
      "severity": "blocking | advisory | noise",
      "rationale": "<1–2 sentences>",
      "side": "LEFT | RIGHT",
      "line": 0,
      "existing_code": "<verbatim anchor copied from the gather finding, for traceability>"
    }
  ],
  "verdict": "block | warn | clear | n/a",
  "examined": [ "<gather finding refs / files the judge actually read>" ]
}
```

- `graded_findings` re-states the gather's **full** finding set (one entry per
  gather finding/cell), each annotated with a `severity` and carrying the
  finding's verbatim `existing_code` + `side`/`line` anchor. Because the judge
  evidence is a self-contained superset, `judge-coverage` can verify it **against
  the re-fetched diff alone** (coverage + traceability + a grade on every finding)
  without reading the gather file. For an out-of-scope / `n/a` gather (e.g. `spec-solves-issue`
  with no linked issue, or any leg on a no-code PR), `graded_findings` is `[]`,
  `verdict` is `n/a`, and `scope` echoes the gather's out-of-scope flags.
- `verdict` is the judge's own roll-up of its grades; it is advisory —
  `conclude-preflight` recomputes the decision itself and never trusts this field
  as authoritative.

### `judge-coverage` check (one shared, parametrized; zone 3)

ABI: `judge-coverage.py <evidence.json> <diff.txt> <changed-files.txt>`,
prints `{"check","pass","feedback"}`, exit 0. Reads `CHECK_PARAMS` for the leg's
scope-recompute mode. Verifies, FORM only:

1. `scope` present and equals an **independent recompute** (reusing the existing
   `_locate`/`_paths` helpers the gather checks already use) — the judge cannot
   fabricate scope.
2. out-of-scope ⇒ `graded_findings == []` and `verdict == "n/a"`.
3. in-scope ⇒ **coverage** (every finding the leg's gather coverage check would
   require is re-stated — reuse the leg's existing coverage logic on the
   re-fetched diff), every `existing_code` anchor appears verbatim in the diff at
   the claimed `side`/`line` (reuse `traces-exist-in-diff` logic), every
   `graded_findings[i].severity ∈ {blocking, advisory, noise}`, and `examined`
   is non-empty (negative attestation).

`on_fail: iterate` — a malformed/absent judge verdict re-dispatches the judge with
feedback until `max_iterations`, then the leg fails (and the gate blocks fail-safe).

The check re-derives ground truth from the re-fetched diff and verifies the
judge's **self-contained superset** evidence; it needs neither the gather file
nor `inputs[]` (zone 3 gets only this node's evidence + the diff).

## The deterministic decision (`conclude-preflight`, enriched)

`conclude-preflight` stays the sole authoritative decider. It independently
re-reads each leg's terminal **judge** evidence from `CONCLUDE_INPUTS_DIR`
(never trusting the gate renderer's `argv[1]`), and computes `blocked` from two
classes of reason:

- **Hard scope blocks — code-only, a judge can never remove them:**
  `issue_linked & !spec_present` · `code_changed & !spec_present` ·
  `code_changed & !plan_present`. Read from the judges' echoed `scope` flags.
- **Substance blocks — driven by the judges' grades:** for each *in-scope* leg,
  block iff that leg's judge graded ≥ 1 finding `blocking`. `advisory` grades
  become warnings; `noise` is dropped. (This is the recommended weighting the
  user approved: the judge owns *seriousness within in-scope legs*, while the
  structural scope blocks remain immovable.)

Decision truth table (per leg, after scope is fixed):

| leg scope | judge grades present | outcome |
|---|---|---|
| out-of-scope (`n/a`) | `[]` | no block, no warn |
| hard-scope-missing (e.g. code & !plan) | any | **block** (scope reason; judge cannot override) |
| in-scope | ≥1 `blocking` | **block** (substance) |
| in-scope | only `advisory` | warn |
| in-scope | only `noise` / none | clear |

Final `blocked = any(hard-scope reason) or any(in-scope leg with a blocking grade)`.
`on_blocked: halt` halts the run; a maintainer `/override <reason>` advances one
phase, exactly as today.

`conclude-preflight` emits the same single consolidated PR comment as today
(verdict table + blocking reasons + `/override` notice), with the per-leg row
now reflecting the judge's grade (e.g. `plan-implements-spec — blocking (2
serious gaps)` / `docs — advisory` / `tests — clear`). Agent-supplied rationale
strings are passed to `gh` as argument-vector elements, never interpolated.

## Trust zones (unchanged invariant, restated for the new nodes)

| zone | runs | for the new design |
|---|---|---|
| 2 — agent (sandboxed, read-only repo + LLM creds) | gather, judge, gate renderer | the only place a model runs; output is `evidence.json` |
| 3 — checks (no credentials) | gather checks, `judge-coverage` | form only; re-fetch the diff |
| 4 — conclude (state PAT + publish token) | `conclude-preflight` | deterministic decide + halt + comment |

The judge's grades are *inputs* to the zone-4 decision, never authoritative; no
LLM runs in zone 3 or 4; no engine code changes.

## Files

**Reused as-is (become the gather steps):** `spec-solves-issue-agent`,
`plan-implements-spec-agent`, `code-implements-plan-agent`, `mm-compliance-gate`,
`docs-coherence-agent`, `tests-coherence-agent` (`.md` + `.lock.yml`) and their
existing checks (`spec-solves-issue-coverage`, `plan-spec-coverage`,
`code-plan-coverage`, `traces-exist-in-diff`, `docs-coverage`, `tests-coverage`,
plus the mm form check). Minor prompt tweak only if needed to stabilize the
finding `ref`/anchor fields the judge consumes.

**New:**
- 6 judge agents: `.github/workflows/<leg-id>-judge-agent.md` (+ committed
  `.lock.yml`), codex/`gpt-5.5`, gateway under `engine.env`, `noop` safe-output,
  evidence-artifact post-step — same frontmatter pattern as the existing gather
  agents.
- 1 judge evidence schema, reused across legs:
  `.github/agent-factory/protocols/code-review/<judge>.evidence.schema.json`
  (final name chosen in the plan; one schema, leg-parameterized).
- 1 shared check:
  `.github/agent-factory/protocols/code-review/checks/judge-coverage.py`
  (executable, `100755`, shebang; reuses `_locate`/`_paths`/`_diff` and the
  `traces-exist-in-diff` anchor logic).

**Modified:**
- `protocol.json`: 6 branches flat → sub-pipeline (`states: [gather → judge]`);
  `preflight-gate.inputs` repointed to the judge sub-states;
  each judge sub-state declares `inputs: [{from: <gather>, as: gather}]`, its
  evidence schema, `checks: [{run: judge-coverage, on_fail: iterate}]`,
  `max_iterations` (2, matching the legs).
- `publish/conclude-preflight.py`: consume the judges' `graded_findings` +
  echoed `scope`; replace the per-leg substance verdicts in `rollup()` with the
  grade-based policy above; keep the hard scope blocks; fail-safe — a leg whose
  judge sub-state is missing or `failed` ⇒ block.
- `checks/preflight-gate-coverage.py` + the `preflight-gate-agent.md` renderer:
  read the judge cell shape (one graded cell per declared leg) instead of the
  old leg-verdict cell.

## Testing

- **Unit (pytest, `tests/`):**
  - `test_judge_coverage.py`: scope-echo agreement, fabricated-anchor rejection,
    missing-severity rejection, out-of-scope `n/a` acceptance, `on_fail: iterate`
    stamping.
  - Extend `test_conclude_preflight.py`: a grades × scope truth table — hard
    scope block immovable under any grades; in-scope `blocking` ⇒ block;
    `advisory` ⇒ warn; `noise`/none ⇒ clear; missing/failed judge ⇒ fail-safe
    block; consolidated-comment text reflects grades.
  - Update `test_preflight_gate_coverage.py` for the judge cell shape.
  - `protocol-lint.py` clean on the new tree; `validate_protocol` passes
    (every agent/sub-state has a `workflow`; `join.of` in scope; depth ≤ 6).
- **Live:** re-run `/review` on SiRumCz PR #7. Expected: each leg runs
  gather → judge; the gate blocks with a *judged* verdict table (PR #7 has no
  spec/plan ⇒ hard scope blocks fire regardless of grades), `on_blocked: halt`.

## Rollout / migration

Build on a branch off `feat/backport-protocol-from-yuanrong`; subagent-driven
implementation with task + final reviews; merge locally; re-run unit suite; then
the live re-test. State-branch instances mid-flight are unaffected (a fresh
`/review` re-enters the new tree). `dist/install.sh` already handles codex
gateway restore + exec bits, so installs pick the new agents up unchanged.

## Open questions deferred to the implementation plan

- **`inputs[]` addressing of a sub-state inside a sibling sub-pipeline.** The gate
  reads each branch's **terminal judge**; confirm the path-aware resolver returns
  the terminal sub-state's evidence for `{from: <branch-id>}`, or address the
  judge sub-state explicitly. (Chosen contract: judge echoes scope + grades so the
  gate/conclude needs only the terminal judge evidence per leg.) The plan's first
  task validates this against `lib.resolve_inputs` with a fixture before building
  all six.
- **Single shared judge agent vs six.** Six per-leg judge agents (one per branch)
  keep prompts leg-specific and match the gather agents; a single parametrized
  judge is possible but couples the legs. Default: six, mirroring the gathers.
- **Exact `ref` vocabulary per leg** (plan_item vs requirement vs item.path vs
  divergence) — specified per judge prompt in the plan.

## Risks

- **Cost/latency:** ~13 LLM dispatches per preflight (6 gather + 6 judge + 1
  renderer) vs 7 today. Accepted (the richer judging is the goal).
- **Depth creep:** the tree is depth 3 today (default cap 5); if a later change
  nests a fanout *inside* a leg (as `deep-review-stub` does), revisit `max_depth`.
- **Judge over-filtering:** a judge could grade a real gap as `noise`. Mitigation:
  hard scope blocks are immovable; the judge's grades are form-checked for
  traceability (it must point at real diff lines); a future second-opinion or
  human gate remains available on the existing graduated-failure ladder.
