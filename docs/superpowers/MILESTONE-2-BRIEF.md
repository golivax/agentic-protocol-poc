# Milestone 2 — live OCR-mimic protocol on dynamic fan-out (kickoff brief)

**Date:** 2026-07-01
**Status:** not started — prep notes for a fresh session
**Predecessor:** Milestone 1 (the dynamic-fanout *engine construct*) is DONE on branch
`design/dynamic-fanout` (20 commits, 651 tests green, PR opened to `main`). Read
`docs/superpowers/specs/2026-06-30-dynamic-fanout-design.md` (design) and
`docs/superpowers/plans/2026-06-30-dynamic-fanout.md` (plan), and the
"Dynamic (data-driven) fan-out" section of `docs/STATUS.md` (what shipped vs deferred).

## The goal of milestone 2

Milestone 1 proved the **engine can represent** dynamic fan-out, tested OFFLINE
(`ENGINE_LOCAL` pytest, stub expanders). Milestone 2 makes it **run live on GitHub
Actions** and builds the **actual OCR-mimic protocol** (`code-review-ocr` or similar):
fan out over the changed files of a PR → per-file review sub-pipeline → (optionally)
nested fan out over findings → reduce/dedup → post the review. This is the end-to-end
proof that the engine can replicate Alibaba's `open-code-review`
(`/home/goliva/sandbox/open-code-review`, a Go CLI). The conceptual mapping is in the
milestone-1 design spec §1 (Motivation) and the original analysis: OCR = *map over N
files → (nested) map over K comments → reduce*, where N/K are runtime-discovered.

**Recommendation: run the superpowers flow again for this** (it is substantial and
outward-facing). At least a brainstorm → spec before coding; likely a plan too. The
milestone splits cleanly into (A) engine/infra hardening for live use, then (B) the
protocol itself. Consider two specs.

## What milestone 1 delivered (so you don't re-derive it)

Engine seams (all under `.github/agent-factory/engine/`, dynamic path gated on the new
keys so static protocols are byte-identical):
- `next.py` `enter_node` fanout arm: dynamic branch runs `lib.run_expander` →
  `build_manifest` → `write_manifest` → per-leg `_seed_child` + `lib.stage_item` → emit
  run-fanout. Sub-pipeline `each` works (each-aware paths).
- `join.py` `main()` + `_nested_join`: `lib.resolve_leg_ids` (manifest for dynamic,
  branches for static) + `lib.join_policy_satisfied(policy, done, total)`
  (`all`/`any`/`quorum:N|P%`). Wait-gate unchanged; policy only decides the verdict.
- `lib.py`: `manifest_file`/`read_manifest`/`write_manifest` (keyed by FULL tree path,
  `<tree-path>.__manifest.yaml`), `leg_id` (sha1[:8] of the `id_from` value),
  `extract_key` (simple `$.a.b` JSONPath), `build_manifest` ({count, legs:[{id,key,item}]},
  fail-loud on over-cap>max_legs and duplicate leg id), `run_expander` (trusted hook,
  fail-loud), `join_policy_satisfied`, `resolve_leg_ids`, `collect_fanout_evidence`
  (rows `{leg_id,key,state,evidence}`), `run_merge_hook` from_fanout wiring, `stage_item`.
  Validation Rules 4/5/6 in `_validate_sequence` (expand/each/policy/from_fanout).
- `paths.py`: `node_at_path`/`_leg_paths` each-awareness — a dynamic fanout's runtime
  leg id resolves to the `each` template; only fires when `node.get("expand")` truthy.
- `protocol.schema.json`: `expand`/`each`/`policy`/`from_fanout` keys.

DSL: a `fanout` has static `branches[]` XOR dynamic `expand`+`each`. See
`docs/PROTOCOL-DSL.md` "Dynamic fan-out (data-driven)" section + the worked example
(`tests/fixtures/dyn-fanout-flat/protocol.json`). Fixtures: `dyn-fanout-flat`,
`dyn-fanout-subpipeline`, `dyn-nested`, `dyn-fanout-badcap`. Tests:
`tests/test_dynamic_fanout.py` (44 tests). Stub expanders read a fixture `items.json`.

## Milestone-2 backlog (ordered, with the gotchas found in M1)

### A. Engine / infra hardening (needed before or alongside a live protocol)

1. **Live GHA runtime-matrix wiring** (the big one). Today the dynamic legs are
   materialized in `next.py` (the `plan` job) into a manifest, but the offline tests
   drive `advance.py`/`join.py` per leg by hand. The live matrix in
   `.github/workflows/agentic-engine.yml` currently gets its `strategy.matrix` legs from
   the planner's `action.legs` (Stage 4b: axis `leg:{path,workflow}`). For dynamic
   fan-out, `next.py`'s `_fanout_action`/`legs` must emit ONE leg per MANIFEST entry
   (path = `<fanout-path>.<legid>` [+ first substate for a sub-pipeline each], workflow =
   the `each` template's workflow), and the matrix must consume that runtime-sized list.
   GHA matrix hard cap is 256 (why `max_legs` ≤ 256). Also thread `NODE_PATH` per leg as
   Stage 4b already does. Check `agentic-orchestrator.yml` (router) and
   `protocol-join.yml` (path-aware concurrency) too. Verify `_fanout_action` already
   builds `legs` for dynamic (it builds from the seeded `branches` list which for dynamic
   IS the manifest legs — confirm the `path`/`workflow`/`substate` fields are right for a
   runtime leg, esp. a sub-pipeline each's first substate).

2. **Stage `inputs/<as>.json` at dispatch.** `stage_item` (M1) persists each leg's item
   beside its state file as `<...>.<as>.item.json` (offline). Live, the dispatch/checks
   job must surface that item as `inputs/<as>.json` for the leg's agent (mirror how
   `materialize_inputs` stages declared inputs). Wire it into the agent job so the
   per-file/per-comment agent actually receives its item (the file path + diff, etc.).

3. **Expander credential-scoping (SECURITY — do before any real token is live).**
   `lib.run_expander` does `env = dict(os.environ)` and forwards the FULL plan-job env to
   the expander subprocess. In `agentic-engine.yml` the `plan` step's env carries
   `GH_TOKEN`/`PUBLISH_TOKEN` (both `POC_DISPATCH_TOKEN`) + authenticated `STATE_REMOTE`.
   A trusted-but-scoped expander should get only a read token (`contents`/`pull-requests`
   read) to fetch the diff. Scrub the env passed to the subprocess (allowlist), or run the
   expander in a separate least-privilege step/job whose output (the manifest/items) feeds
   the plan. This restores the design spec §8 trust-zone claim (currently NOT enforced —
   recorded honestly in `docs/STATUS.md`).

4. **Nested `from_fanout`.** `run_merge_hook` hardcodes `fo_tree_path = [inp["from_fanout"]]`
   (top fanout only). A `merge` inside an `each` sub-pipeline reducing its sibling NESTED
   fanout needs the full tree path. M1 added a FAIL-LOUD guard (missing-manifest → error)
   so this can't silently mis-reduce; milestone 2 implements the real nested resolution.

5. **Dynamic-leg-aware rendering.** `lib.render_fanout_status_body` iterates static
   `state.get("branches", [])` → a dynamic fanout's PR status comment renders ZERO leg
   sections (check-run gating is still correct; only the human comment degrades). Same
   blind spot in `protocol-lint.py`'s tree renderer (`review [fanout]` shows no `each`
   children; `inputs: legs←?`). Make both read the manifest / know about `each`.

### B. The OCR-mimic protocol (`.github/agent-factory/protocols/code-review-ocr/`)

6. **Real diff-parsing expander(s).** `expand/expand-files` = parse `gh pr diff` → one item
   per changed file (`{path, diff, ...}`), applying OCR-style pre-filters (skip binary /
   vendored / oversized-diff files — see OCR `internal/agent/preview.go` + `filterLargeDiffs`).
   For the nested shape, `expand/expand-comments` = per-file findings → items.

7. **The protocol shape.** Mirror OCR (analysis in M1 design spec + the original
   open-code-review report): dynamic `review` fanout over files, `each` = a sub-pipeline
   (`plan`? → `main-review` → `filter`) per OCR's Plan→Main→ReviewFilter; optionally a
   nested dynamic fanout over findings; `join(policy: any or quorum)`; `merge(from_fanout)`
   that dedups + posts the aggregated review. Evidence schemas + deterministic checks:
   OCR's `ResolveLineNumbers`/positioning maps to the existing `traces-exist-in-diff`
   check pattern (findings carry `existing_code` + a line anchor). Reuse the code-review
   protocol's checks/publish hooks as templates.

8. **gh-aw agent(s)** for the per-file (and per-finding) review, compiled to `.lock.yml`
   (`gh aw compile`), following the existing `grumpy-agent`/`security-agent` pattern
   (custom Anthropic endpoint via `engine.env`, `sandbox.agent:false` caveat, `run-name`
   cid). Model pinned. Read-only/tokenless per the four-zone model.

9. **Live verification** on a real PR (the M1 offline walks are the template for what
   "correct" looks like end-to-end): PR opened → files fanned out → per-file reviews →
   reduce → single posted review; over-cap/zero-file edge behavior; the check-run gate.

## Key gotchas / facts to carry in

- **Manifest keying:** always the FULL tree path (`review.__manifest.yaml`,
  `review.<legid>.comments.__manifest.yaml`). Leg *state* files use `lib.state_path`,
  which DROPS the leading id for single-phase protocols (so top-level dynamic legs are
  `<legid>.yaml`), but keeps the full path when nested/multi-phase.
- **`leg_id` = sha1(id_from-value)[:8]**, scoped per fanout — two sibling review legs can
  produce identical comment-leg ids, but files are path-scoped (`<L>.comments.<cid>.yaml`)
  so no collision. Fine, but keep in mind for any manifest-wide reasoning.
- **`is_multiphase`** counts agent|fanout phase states; a single top fanout = single-phase
  (leading id dropped). The OCR protocol will likely be multi-phase (preflight? → review),
  changing on-disk names — verify state_path behavior when you author it.
- **Static path must stay byte-identical** — the whole M1 regression story rests on the
  dynamic code only firing on `expand`/`policy`/`from_fanout`. Keep that invariant when
  wiring GHA (don't change the static matrix path).
- **Workflows run from the DEFAULT branch** for `issue_comment`/`repository_dispatch` —
  the orchestrator/engine/agent-lock changes must land on `main` (see CLAUDE.md
  "Operational gotchas"). Plan the merge accordingly (M1 engine is on
  `design/dynamic-fanout` → merge to main before/with the live wiring).
- Fail-loud is the house style: over-cap, expander failure, missing from_fanout manifest,
  duplicate leg id all raise. (Cosmetic: they surface as raw tracebacks — a clean
  top-level error print in `next.py`/`join.py` `main()` is an optional polish.)

## Where things are

- Engine: `.github/agent-factory/engine/{next,join,lib,paths,advance,run-checks}.py`
- Protocols: `.github/agent-factory/protocols/<name>/` (author `code-review-ocr` here;
  copy structure from `code-review/`)
- Workflows: `.github/workflows/{agentic-orchestrator,agentic-engine,protocol-join}.yml`
  + `*-agent.md` → `*-agent.lock.yml`
- Tests: `tests/test_dynamic_fanout.py`, `tests/fixtures/dyn-*`; harness in
  `tests/conftest.py` (`run_engine`, `engine_env`, `read_state_yaml`)
- OCR reference source: `/home/goliva/sandbox/open-code-review` (Go). Key files from the
  M1 analysis: `internal/agent/agent.go` (dispatchSubtasks = per-file fan-out; Plan/Main
  phases), `internal/diff/resolver.go` + `relocation.go` (positioning ≈ traces-exist),
  `internal/scan/batch.go` (batching), `internal/tool/code_comment.go` (findings).
