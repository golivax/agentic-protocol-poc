# Stage 4 — Recursive Engine Unification + GHA Wiring (First Release)

- **Date:** 2026-06-23
- **Status:** Design approved; ready for implementation plan
- **Supersedes scope note:** the "Stage 4 = GHA-only" framing in
  `stage4-recursive-gha-wiring` memory and
  `2026-06-23-recursive-subpipelines-design.md`. Stage 4 is now an **engine
  unification** (engine work + GHA wiring + live), not GHA-only.
- **Quality bar:** this is the **first pre-beta release** of a generic protocol
  engine. Customers will extend it by authoring their own protocols. "PoC" is no
  longer the standard — production robustness, clear authoring-error messages,
  and a thorough capability test-suite are first-class requirements.

## 1. Motivation

The recursive sub-pipeline engine (Problem #2, pushed @ `beb0dee`/`5f890b4`)
gave us arbitrary-depth fanouts driven by a node-path coordinate (`NODE_PATH`),
proven by the `deep-fanout` (depth-4) and `gate-deep` (depth-5) fixtures. But it
landed as a **second code path alongside** the original engine: the top-level
phase sequence is still driven by three bespoke mechanisms
(`start_fanout` / `seed_and_dispatch_phase`, the `protocol-advance`
repository_dispatch type, and the top-level join's phase-advancement), and the
GitHub Actions layer still threads the fixed `(BRANCH, PHASE, SUBSTATE)` tuple —
which **cannot address a nested leg** (a nested fanout's `sec`/`perf` branch ids
lose their path prefix). So arbitrary depth works in pytest but has **no path to
production**.

Stage 4 closes that: collapse the two engine code paths into **one recursive
sequencer** and wire GitHub Actions to the single `NODE_PATH` coordinate.

## 2. The core architectural idea — root-as-sequence

**Every protocol's top-level `states` list is already a linear sequence of
phases:**

- `code-review`: `preflight`(agent) → `review`(fanout) → `join` → `approval`(gate)
- `recover-mental-model-stub`: `recover`(fanout) → `join` → `combine`(merge/agent)
- `deep-fanout`: `preflight`(fanout) → `join-preflight`
- single-agent: one `agent` state

The recursive sequencer (`enter_node` / `advance_node` / `complete_sequence` /
`_nested_join`, driven by `NODE_PATH`) already performs exactly this seed →
advance → next-sibling → join walk — but one scope deeper, stopping at the top
fanout. **`paths.py` already models the protocol root as a sequence**
(`node_at_path` level-0 reads `proto["states"]`; `next_sibling` with
`parent is None` falls back to `_root_children`).

**Full migration = treat the protocol root as a `sequence` node and drive phase
transitions, the top-level join, and the approval gate through the recursive
sequencer on `NODE_PATH`.** A top-level phase becomes a depth-1 path
(`["review"]`); a phase transition becomes "continue at the next sibling path".
This **retires the `protocol-advance` dispatch type entirely** and deletes the
bespoke `start_fanout` / `seed_and_dispatch_phase` / single-agent code paths.

There is exactly **one** viable architecture here. The alternative considered —
a translation shim that keeps both internal code paths and derives
`(phase, branch, substate)` from `NODE_PATH` — was explicitly rejected: it
perpetuates the per-level drift the recursion was meant to end.

## 3. Goals / non-goals

**Goals**
1. One recursive code path in the engine; no `(phase, branch, substate)` legacy
   path; no `protocol-advance` dispatch type.
2. GitHub Actions threads a single `NODE_PATH` coordinate end-to-end (plan →
   dispatch → checks → advance → join), supporting arbitrary depth.
3. `code-review` and `recover-mental-model-stub` work end-to-end on the new path
   (live-verified). A new live `deep-review-stub` proves arbitrary depth live.
4. A **capability test-suite** covering each generic engine feature a customer
   could use, plus authoring-error UX and security regressions.
5. Production-quality robustness and error messages; `actionlint` in CI.

**Non-goals**
- Byte-identical compatibility with the pre-Stage-4 engine. It is **intentionally
  broken**; legacy byte-identity fixtures/tests are removed.
- In-flight state migration across the deploy. A fresh `/review` / `/recover` /
  trigger is required after deploy (documented, not coded).
- Preserving every legacy fixture/protocol. Only `code-review`, `recover`, and
  `deep-review-stub` must work; other shapes are re-added as capability fixtures,
  not byte-identity oracles.

## 4. Engine design (Stage 4a)

The unit of change is `.github/agent-factory/engine/`. The recursive primitives
already exist; 4a **promotes the root sequence into the same walk** and deletes
the bespoke top-level machinery.

### 4.1 Coordinate model (`paths.py`, `lib`)
- The tree path is rooted at the **first top-level node id** (a phase), e.g.
  `["preflight"]`, `["review"]`, `["review","grumpy"]`, `["approval"]`. The root
  sequence is implicit (the `proto["states"]` list); it is never named by a path
  segment.
- `lib.state_path(proto, tree_path)` keeps its existing single-phase-vs-multi-phase
  behavior (single-phase drops the leading top id; see
  `.superpowers/sdd/PATH-CONVENTIONS.md`). The walker carries TREE paths and
  converts at every file call — unchanged from Problem #2.
- Add/confirm `paths` helpers needed for root-level navigation: `next_sibling`
  at the root (exists), and a predicate for "this sequence is the protocol root"
  (path length 1 about to advance) so the walker writes the root cursor to
  `_instance.yaml.phase` rather than a `<id>.yaml` cursor file.

### 4.2 The root cursor stays in `_instance.yaml.phase`
The recursive sequencer writes a `<seq>.yaml` cursor (`sub_state`) for every
sequence node **except the root**: the root sequence's cursor is the existing
`_instance.yaml` `phase` field. This keeps `render_pipeline_status_body`, phase
labels, and `do_resolve_gate`/`do_override` (which read `inst["phase"]`) working
unchanged. The walker gets one special-case: when advancing a child of the root
sequence, set `_instance.yaml.phase` instead of writing a root cursor file.

### 4.3 `next.py`
- **Entry (`start` / `reset`)** routes through `enter_node` at the root: enter the
  root sequence → seed + dispatch the first phase. The **restart/reset wipe
  logic** (`reset_instance`: abandon old status comment with the "superseded"
  banner, remove the phase label, wipe the instance dir, refresh `head_sha`) is
  **preserved** — it folds into the unified entry, alongside instance-file
  creation, the setup/phase labels, and `cas_push`. `start_fanout` and
  `seed_and_dispatch_phase` are deleted.
- **`continue` at a depth-1 phase** is handled by the existing
  `continue`-at-`NODE_PATH` guard: phase = a top-level node, dispatched by kind
  (fanout → seed children matrix; agent → seed + run-agent; gate → open).
- **Phase transition** is "continue at next sibling path" — no `advance-phase`
  command, no `protocol-advance`. The `advance-phase` branch in `next.py` is
  deleted.
- **Gates** unify: a depth-1 approval gate is opened by `enter_node`'s gate arm;
  `do_resolve_gate` / `do_override` keep their auth + refusal semantics but their
  "advance to next phase" tail changes from `seed_and_dispatch_phase(nxt)` to
  "set root cursor → `dispatch_continue(path=<next sibling>)`". The nested-gate
  `/answer` recursion (`_find_open_gate`, `do_answer`) already follows live
  cursors recursively and is reused unchanged.
- **`workflow` on each leg:** `_fanout_action` / `_seed_child` include the
  resolved `workflow` on each `legs[]` entry (the node is already in hand), so the
  GHA dispatch job reads `matrix.leg.workflow` instead of calling
  `lib.agent-workflow`. The run-agent action already carries `workflow` for the
  recursive path; extend it to all run-agent emissions.

### 4.4 `advance.py`
- Delete the legacy `(branch, phase, substate)` coordinate-derivation block; the
  invocation is always `NODE_PATH`-driven. `resolve_agent_unit_path` is the sole
  unit resolver.
- The **agent-phase-clear** block (currently fires `protocol-advance` to the next
  phase) becomes: set `_instance.yaml.phase` to the next root sibling →
  `dispatch_continue(path=<next sibling>)`. The **no-next-phase** finalize
  (aggregate check-run success/done) is preserved.
- The **pre-flight gate** semantics (`is_agent_phase` + `conclude` /
  `on_blocked:halt` → `halted:{reason:blocked}` marker that `/override` reads) are
  retained verbatim; only the clear-path dispatch changes to a path-continue.
- Fanout-leg done/failed → `fire_join` with the enclosing fanout path (existing
  `_join_path` logic), now including the **top** fanout (a depth-1 fanout phase
  fires a path-carrying join whose path is the phase id; join.py routes it — see
  4.5). Sub-pipeline-leg and flat-nested-child handling is unchanged.

### 4.5 `join.py`
- A join is "the barrier for the fanout named by its `of`, sitting in some
  enclosing sequence." The **top-level join** (currently the `_instance.yaml`
  path with bespoke phase-advance + mode-2/3 agent-combine/merge) collapses into
  the recursive sequence-advance: on all-done, advance the **enclosing sequence
  cursor** (the root → `_instance.yaml.phase`, or a sub-pipeline cursor) to the
  join's `.next` and `dispatch_continue(path=<next>)`; the merge/agent/gate that
  follows is then entered by the recursive `continue` (its kind arm). `join.py`'s
  bespoke mode-2 (agent combine) / mode-3 (merge) / gate-open tails are replaced
  by "continue at `.next`", with the merge reduce-hook run by the `merge`-kind arm
  of the recursive `continue` (a small addition: the recursive `continue` learns a
  `merge` kind, running `run_merge_hook` then finalizing).
- The barrier marker: the **top fanout** keeps the `_instance.yaml` `joined`
  field (no new file); **nested fanouts** keep the path-keyed `__join.yaml`
  marker. This is unchanged from Problem #2; only the post-join action unifies.
- Failure bubbling (all-terminal-not-all-done → mark cursor failed → fire
  enclosing join) is unchanged.

> **Design note (to resolve in the plan):** the top fanout's join currently runs
> with `NODE_PATH` unset (legacy `_instance.yaml` evaluation). Under unification
> the top join still uses the `_instance.yaml` marker but must perform the
> *recursive sequence-advance* on `.next`. The plan's first tasks establish
> whether the top join is reached with `NODE_PATH=<fanout-phase-id>` (depth-1) or
> unset, and makes the post-join "continue at `.next`" identical in both the
> root-sequence and sub-pipeline cases. The `deep-fanout` keystone (single-phase,
> top fanout → join → `done`) and `code-review` (multi-phase, review fanout → join
> → approval gate) are the two oracles that pin this down.

### 4.6 Removals (4a)
Delete: `protocol-advance` production/consumption; `start_fanout`;
`seed_and_dispatch_phase`; the bespoke single-agent path in `next.py`/`advance.py`;
`next.py`'s `advance-phase` command branch; `join.py`'s bespoke mode-2/3/gate
tails (folded into recursive `continue`). Remove legacy byte-identity fixtures
(`single-agent`, `fanout-mini`, `pipeline-mini`, `multiphase-subpipeline`,
`subpipeline-mini`) and the tests that only assert byte-identity. **Re-add**
single-agent and simple-fanout as *capability* fixtures under the new engine.

## 5. GHA wiring (Stage 4b)

### 5.1 `agentic-engine.yml`
- Matrix axis `leg: {branch, substate}` → **`leg: {path, workflow}`**, fed from
  `action.legs[]`. No empty-string sentinel leg: a single-agent / agent-phase leg
  carries its own depth-1 path (e.g. `preflight`).
- Thread **`NODE_PATH=${{ matrix.leg.path }}`** into dispatch/checks/advance;
  **delete** `BRANCH`/`PHASE`/`SUBSTATE` env wiring.
- **ctx step:** on `protocol-continue`, `NODE_PATH=client_payload.path`. Drop the
  `branch`/`phase`/`substate` payload parsing and the `advance-phase` case.
- **dispatch step:** resolve the agent workflow from `matrix.leg.workflow`
  (remove the `lib.agent-workflow` call). CID leg token derived from the
  sanitized path.
- **checks / advance steps:** `NODE` for `run-checks.py` derived from
  `NODE_PATH`; `run-checks.py` already accepts the path coordinate (confirm /
  adjust). Advance invoked with `NODE_PATH` only.
- **Artifact names** `runmeta-…` / `verdicts-…` → **path-keyed**. The dot-joined
  path is the natural key. Node ids cannot contain `.` (it is the `NODE_PATH`
  separator), so the dot-path is already unambiguous; **dots are legal in GitHub
  artifact names**, so the path can be used as the artifact key directly
  (`runmeta-preflight.deep.analyze.sec`) with **no sanitization** — the simplest
  injective choice. (A naive `.`→`-` substitution is *not* injective once ids
  contain `-`, e.g. `join-analyze` — `a.b-c` and `a-b.c` would collide; avoid it.)
  If a future constraint forbids `.`, switch to an encoding using a separator that
  is illegal in node ids. A unit test asserts distinctness over representative
  deep paths.

### 5.2 `protocol-join.yml`
- Add `NODE_PATH: ${{ github.event.client_payload.path }}` to the `join.py` step
  env (empty → top join, unchanged path).
- Concurrency group **path-aware**: `join-<instance>-<client_payload.path>` so
  nested joins at different fanout paths don't serialize against each other or the
  top join.

### 5.3 `agentic-orchestrator.yml`
- Concurrency `group: agentic-<instance>-<client_payload.path>` (replacing
  `…-<branch>`).
- Drop `protocol-advance` from `on: repository_dispatch types`.
- Optionally include the path in `run-name` for debuggability.

### 5.4 Security (CLAUDE.md invariant)
`client_payload.path` / `NODE_PATH` and all agent-derived strings stay strictly
`env:`-passed, never interpolated into a `run:` block. Path segments are validated
against protocol nodes by the engine (`node_at_path` returns None for an unknown
segment); the GHA layer never `eval`s the path.

## 6. Live protocol + agents (Stage 4c)

- **`deep-review-stub`** mirrors the proven `deep-fanout` depth-4 topology:
  `preflight` fanout(`quick` ∥ `deep`[`triage` → `analyze`(`sec` ∥ `perf`) →
  `join-analyze` → `report`]), review-flavored agent names. The keystone
  (`test_deep_fanout_walks_to_done`) already walks this exact engine shape, so the
  live run validates GHA wiring against a known-good engine walk.
- gh-aw agents (`*.md`) per leaf; `gh aw compile` → commit `.lock.yml`s; kept on
  `main` (workflow-on-default-branch rule). Frontmatter per the existing agents
  (`strict:false` + `sandbox.agent:false`; LLM endpoint under `engine.env`;
  `run-name` embeds `cid:[<cid>]`).
- **Live verification:** walk `deep-review-stub` on a live PR through the nested
  levels + a nested gate `/answer`, confirm join bubbling + final state; **and**
  re-verify `code-review` + `recover-mental-model-stub` end-to-end on the new
  path. Budget a live-debug pass for 1-3 live-only bugs (missing dispatch token,
  coordinate mismatch under a real protocol name/depth, artifact-name collision).

## 7. Testing strategy (release bar)

The suite is a **capability matrix** against the single unified engine, not a
byte-identity oracle.

- **Pure unit** — `paths.py` (root-as-sequence nav, `next_sibling` at root,
  `enclosing_fanout_*`, `max_static_depth`, malformed/empty paths); `lib`
  coordinate/`state_path` helpers.
- **Shape e2e walks** (through the shared git origin, like the keystone) — one per
  shape: single-agent; single fanout; multi-phase (agent→fanout→join→gate);
  sub-pipeline branch; depth-4 (`deep-fanout`); depth-5 with nested gates
  (`gate-deep`); nested fanout-in-sub-pipeline.
- **Control-flow** — iterate→exhaust→failed; failure bubbling through nested
  joins; AND-barrier success/failure; data-gate `/answer` (partial + complete,
  top + nested); approval gate `/approve` / `/request-changes` / `/reject`;
  `/override` of a blocked gate; restart/reset (superseded-comment + wipe);
  `inputs` resolution across nested scopes; merge/combine.
- **Guardrails & authoring-error UX** — `max_depth` exceeded; malformed
  protocol.json (missing `next`, unknown join `of`, bad `workflow` ref, gate with
  no source) → **clear, actionable error messages** with the offending node path.
- **ABIs** — check / publish / conclude / merge contracts.
- **Security regression** — agent-derived strings never reach a `run:`/argv
  injection point; path segments validated.
- **Workflow lint** — `actionlint` on the three workflows in CI.

All tests stay pytest-only; no new runtime dependency beyond Python 3 + PyYAML.

## 8. Staging

- **4a — Engine unification** (root-as-sequence; retire `protocol-advance`;
  recursive phase transitions/gates/join; removals) + full capability test-suite.
  Provable offline; `code-review`, `recover`, `deep-fanout`, `gate-deep` as
  oracles. Subagent-driven TDD.
- **4b — GHA wiring** to the unified `NODE_PATH` axis (engine.yml, join.yml,
  orchestrator.yml) + `actionlint` in CI.
- **4c — Live**: `deep-review-stub` protocol + agents + live PR verify of deep,
  code-review, and recover.

Each stage is mergeable; 4a is the bulk of the risk and the bulk of the testing.

## 9. Risks

- **Rewriting the most battle-tested live flow (code-review).** Mitigated by the
  capability suite + the `code-review`/`recover` oracles + live re-verify in 4c.
- **The top-join unification subtlety (§4.5 design note).** The first 4a tasks
  must pin down the top-join's post-join continue against both oracles before the
  broader rewrite.
- **Artifact-name sanitization collisions** across nested legs. Mitigated by an
  injective scheme + a unit test over representative deep paths.
- **Live-only bugs** (token/coordinate/artifact) — budgeted 4c debug pass.

## 10. Out of scope / future

- Check-authoring meta-protocol; native-PR-review gate trigger (existing backlog).
- Cross-file join-dispatch de-dup (`advance.py` `_join_path` vs `join.py`) — minor
  cleanup, fold in opportunistically.
- Protocol-authoring documentation for customers — valuable for the release but
  tracked separately from this engine/wiring spec.
