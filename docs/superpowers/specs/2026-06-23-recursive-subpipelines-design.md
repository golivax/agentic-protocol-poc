# Recursive sub-pipelines: arbitrary-depth protocol nesting (Problem #2)

**Date:** 2026-06-23
**Status:** Approved design, pre-implementation
**Scope:** Problem #2 of two. Problem #1 (multi-phase + sub-pipeline fanout at the
fixed 3-rung depth) shipped at `3d55278`. This spec lifts the depth ceiling
entirely: a sub-state may itself be a fanout or a nested sequence, to a
configurable bounded depth.

## Problem

The engine drives a protocol that is an **alternation of two node kinds**:

- a **sequence** — an ordered `states[]` list with a *cursor* (the top-level
  protocol `states[]`, and any branch carrying its own `states[]`);
- a **fanout** — parallel `branches[]` joined by an AND-barrier.

Today the coordinate of a running leg is a **fixed 3-tuple** `(PHASE, BRANCH,
SUBSTATE)`, hardcoded into `lib.state_file`, `lib.resolve_agent_unit`, the
`PHASE`/`BRANCH`/`SUBSTATE` env vars, and the seed/advance/join code in
`next.py` / `advance.py` / `join.py`. This caps depth at 3: a top-level
sequence → one fanout → branch sub-pipelines whose sub-states are **leaves only**
(`agent` / `gate`). A sub-state cannot itself be a fanout or a nested sequence.

We want the protocol language to be as flexible as the real pipelines demand
(e.g. a preflight stage that fans out to single agents *and* sub-pipelines, where
a sub-pipeline may itself fan out), without the ceiling — and without the
duplication that "bump the fixed rungs to 5/6" would re-introduce (Problem #1
spent effort *consolidating* exactly that drift).

### Why "bump the rungs" was rejected

The rungs alternate sequence/fanout, so adding fixed rungs means **hand-writing
the sequence-logic and fanout-logic two more times** — re-expanding the
two-emit-site drift Problem #1 consolidated — while still leaving a (higher)
fixed ceiling and a deeper GHA matrix. It pays most of recursion's cost in
fixed-rung form. Decided: go genuinely recursive, with a configurable depth
guard for "reasonable boundaries."

## The key realization (why this is tractable and low-risk)

**The engine's file-naming scheme is already a serialized node-path.** Today:

| Coordinate | State file |
|---|---|
| `phase` | `<phase>.yaml` |
| `phase, branch` | `<phase>.<branch>.yaml` |
| `phase, branch, substate` | `<phase>.<branch>.<substate>.yaml` |

That is exactly `".".join(path) + ".yaml"`. So the **durable state format already
supports arbitrary depth** — only the *code* hardcodes the 3-tuple. For any path
of length ≤3 the generalized engine produces byte-identical files and behavior,
which makes the existing fixtures a precise regression net.

**The GHA constraint and why recursion still works.** GitHub Actions matrices are
flat and cannot nest dynamically. But the engine is event-driven: each engine
invocation is *one transition* that emits a flat matrix of **only the immediate
children of the fanout it just entered**. A nested fanout is simply a *later*
engine invocation emitting *its own* flat matrix. The git state tree holds the
hierarchy; the matrix only ever expresses one level. Arbitrary internal depth
maps onto the existing flat-matrix + re-dispatch model with **no GHA nesting** —
the planner only needs to be able to emit `run-fanout` for a non-top node.

## Goal

A protocol whose tree alternates sequence/fanout to depth >3 — concretely, a
fanout phase with a sub-pipeline branch that *itself contains a fanout* — works
**end to end**: enter → dispatch each level's children → bubble joins up the tree
→ resolve nested gates via `/answer` → resolve cross-scope `inputs` → finalize.
Proven by pytest regression + a new deep fixture, then wired into the GHA engine
and **live-verified on a throwaway PR** via a new minimal real protocol. A
configurable `max_depth` guard enforces "reasonable boundaries."

## Non-goals

- Removing the depth bound entirely. Depth is **configurable but bounded**
  (default cap), by deliberate anti-pattern protection.
- Nested fanouts whose *branches* are themselves fanouts with no intervening
  sequence are allowed structurally (the recursion does not forbid it) but are
  not a target shape; the live protocol exercises sequence→fanout→sequence→fanout.
- Changing the trust-zone model, the evidence/check ABI, or the publish/merge
  hook ABI. Recursion only deepens the *path*; no credential crosses a new
  boundary.

## Approach (chosen: recursive walk over a node-path coordinate)

Mirror the engine's existing "one seam, not a parallel code path" philosophy.
Three structural changes plus a guard.

### 1. Coordinate = node-path (list of ids)

Replace the fixed `(PHASE, BRANCH, SUBSTATE)` 3-tuple with **one variable-length
node-path**: the ordered list of node ids from the root sequence to the current
node.

```
["preflight"]                                         # a top-level agent phase
["preflight", "deep"]                                 # leg "deep" of preflight fanout
["preflight", "deep", "triage"]                       # substate of sub-pipeline "deep"
["preflight", "deep", "analyze", "sec"]               # NEW: leg of a nested fanout (depth 4)
["preflight", "deep", "analyze", "sec", "step2"]      # NEW: depth 5
```

- The **state-file path is unchanged** (`".".join(path) + ".yaml"`); depth-≤3
  files are byte-identical.
- `lib.state_file(...)` keeps its `branch=/phase=/substate=` signature as a
  back-compat shim that builds a 3-element path; the canonical internal form is a
  path list.
- The wire format (GHA matrix leg + dispatch payload) carries one dot-joined
  `path` string.

### 2. Tree-navigation helpers (replace the fixed-rung resolvers) — `lib.py`

- `node_at_path(proto, path)` → the protocol node dict at that path, walking
  `fanout → branch → substate` to any depth. Generalizes `resolve_agent_unit`,
  `branch_config`, `branch_substates`.
- `parent_path(path)` and `next_sibling(proto, path)` → next child within the
  enclosing sequence. Generalizes both `next_phase_id` **and** `next_substate_id`
  into one function.
- `node_kind(proto, path)`, `is_fanout` / `is_sequence` / `is_leaf`.
- `cursor_file(path)` vs `leaf_file(path)` — same path; the cursor is the
  sequence-node file (holds `sub_state`), the leaf is the current child file.
- `life_state(proto, path)` → the **enclosing fanout's id** at any depth (the
  leg's in-flight marker). Subsumes the `_fanout_state(proto)["id"]` hardcoding
  that `do_answer` / `advance.py` currently carry (the source of one of Problem
  #1's live-only bugs).

`resolve_agent_unit`, `_fanout_state`, `next_phase_id`, `next_substate_id`, and
`branch_substates` become thin wrappers over these (or are deleted once callers
migrate). For any path of length ≤3 they return exactly today's values.

### 3. One recursive sequencer: `enter_node` / `advance_node`

Today four pieces of code each know how to "start the next thing":
`start_fanout`, `seed_and_dispatch_phase`, `seed_branch` (`next.py`), and the
sub-pipeline advance block (`advance.py`). They have drifted once already.
Collapse them into one recursive pair, dispatching purely on node-kind:

```
enter_node(proto, path):                       # seed state + emit/dispatch
    node = node_at_path(proto, path); kind = node_kind(node)
    if kind == "sequence":                     # a branch that is a sub-pipeline
        write cursor file (sub_state = first child id)
        enter_node(proto, path + [first_child_id])
    elif kind == "fanout":
        write the fanout's path-keyed join marker (joined=False)
        for b in branches: seed_branch(path + [b.id])   # leaf→state file, seq→recurse
        emit run-fanout(legs=[full path per branch])     # flat matrix, ONE level
    elif kind == "agent":
        write fresh state file; emit run-agent(path)
    elif kind == "gate":
        open_gate(path)                          # no dispatch; wait for human
    elif kind == "merge":
        run_merge_hook(path); complete_sequence(proto, parent_path(path))
```

```
advance_node(proto, path, process):            # advance.py on a leaf's done/failed
    if process == "done":
        nxt = next_sibling(proto, path)
        if nxt: enter_node(proto, parent_path(path) + [nxt])
        else:   complete_sequence(proto, parent_path(path))   # sequence finished → bubble
    elif process == "iterate": (unchanged iterate loop)
    else: # failed
        mark leg failed; bubble failure up the same path
```

`enter_node` on a top-level fanout reproduces today's
`start_fanout`/`seed_and_dispatch_phase`; on a branch sub-pipeline it reproduces
`seed_branch`. Recursion only *engages* when a sub-state is itself a
fanout/sequence; depth ≤3 is byte-identical.

**The planner emits `run-fanout` for a non-top node.** Today only
`start`/`reset`/`advance-phase` emit `run-fanout` (top-level), and a `continue`
always resolves a single agent leg. We add: *if the node at the dispatched path
is a fanout, `enter_node` emits its matrix.* A nested fanout discovered mid-leg
is a `protocol-continue` re-dispatch carrying that fanout's path; the next
invocation's plan job emits the sub-matrix. **Decision:** reuse
`protocol-continue` rather than add a `protocol-fanout` event (fewer moving
parts; the planner branches on node-kind at the path).

### 4. Recursive join (bubbling) — the heart of depth >3

Today there is one `joined` bool on `_instance.yaml`, and `join.py` evaluates the
single top fanout. With nested fanouts, **each fanout instance needs its own
barrier**, and completing one must bubble up:

1. **Path-keyed join markers.** Replace the single instance-level `joined` bool
   with a per-fanout marker file `<fanout-path>.__join.yaml`. (Decision: a *file*,
   not a field on the shared `_instance.yaml` — see Concurrency & isolation; the
   top-level fanout still also writes `_instance.yaml.joined: true` for the
   status-renderer's back-compat.)
2. **`join.py` evaluates the fanout at a given path.** It reads each branch's
   terminal state at `path + [branch]` (the branch *cursor* file, exactly as
   today). The current `phase_for_path` special-case dissolves into "the fanout's
   path."
3. **`complete_sequence` — the single bubbling primitive.** Shared by
   `advance_node` (a sequence's last leaf done) and `join.py` (a fanout's last
   branch done); both mean "this sub-tree finished; advance the thing that
   contains it." When all branches of fanout `F` at path `P` are terminal:
   - **all done** → mark `F` joined; `complete_sequence(parent_path(P))` —
     advance to `F`'s next sibling (a `merge` / `gate` / `agent`, or — if `F` was
     the last child — complete *that* sequence, which if it is a branch of a
     higher fanout fires *that* fanout's join). The bubble continues until it
     reaches the root or a not-yet-terminal node.
   - **any failed** → mark `F` joined-with-failure; bubble a failed leg to the
     parent (AND-barrier semantics propagate; a failed nested fanout fails its
     enclosing branch).

The existing post-join behaviors (gate-after-join → open; agent/merge-after-join
→ dispatch/run; else finalize) become the depth-agnostic `complete_sequence` of
the root. For a single top-level fanout the bubble terminates immediately at the
root — identical to today's `join.py`, guarded by `fanout-e2e`, `join`, and the
recover fixtures.

### 5. Gates, inputs & `/answer` under the path model

Already correct at depth 3; they take a **path** instead of `(branch, substate,
phase)`:

- **`open_gate` / data-carrying gates** — keyed by the gate's full path;
  `questions_from` resolves the source artifact at `parent_path(gate) + [qfrom]`.
- **`/answer`** — `_find_open_gate` walks the tree for any `gate` leaf in state
  `open` and returns its path; `life_state(proto, path)` supplies the enclosing
  fanout id at any depth (subsuming the hardcoded-`"review"` bug). Disambiguation
  for >1 open gate stays `/answer <id> …`, matched against the leaf id anywhere
  in the tree.
- **`inputs: [{from, as}]`** — `resolve_inputs` resolution becomes path-relative:
  (1) an earlier sibling in the same sequence, (2) a sibling branch's leg-output,
  (3) walk up the path to outer scopes. Persisted-output path is
  `output_artifact_path(path)`. Depth ≤3 resolves identically.

### 6. `max_depth` guard (configurable, default cap)

**Depth is defined as node-path length.** Today's deepest leg
`[phase, branch, substate]` is depth 3; `deep-review-stub`'s deepest leg
`[preflight, deep, analyze, sec]` is depth 4; the `too-deep` fixture is depth 5.
A nested fanout adds *two* segments (the fanout sub-state + its chosen branch),
so a cap of 4 permits exactly one nested fanout whose branches are flat agents
(e.g. `sec`/`perf`); making a nested branch a sub-pipeline (depth 5) requires an
explicit `max_depth: 5`.

- New optional top-level `protocol.json` field `"max_depth": <int>`.
- Engine constant `DEFAULT_MAX_DEPTH = 4` (one level past today's ceiling — enough
  for "a sub-pipeline branch that contains a fanout"; a protocol opts higher
  explicitly).
- **Enforced at seed time** (in `enter_node`) **and** by a static protocol-load
  check: if the *static* tree depth exceeds the effective cap, the engine refuses
  to seed and posts a clear error — it never half-runs a too-deep protocol.

## Concurrency & isolation

Cross-protocol and cross-instance isolation is already sound and is preserved;
the work is keeping *intra-instance* deep concurrency from regressing it.

- **State partition** stays `<protocol>/<instance>/<dot-path>.yaml` → disjoint per
  protocol, instance, and leg. Two different protocols (or PRs) touch disjoint
  file trees and run fully in parallel today.
- **Path-keyed join markers** (`<fanout-path>.__join.yaml`) → every nested leg
  completion writes a *disjoint* file. A field on the shared `_instance.yaml`
  would create real **semantic** write-contention within an instance under
  nesting; path-keyed files keep CAS conflict-free.
- **Concurrency groups keyed by full path.** The orchestrator group becomes
  `agentic-<instance>-<dot-path>` (per-leg) and the join group becomes
  `join-<instance>-<fanout-path>` (per-fanout). Distinct deep legs stay parallel;
  the *same* fanout's join stays serialized (which protects its AND-barrier from
  double-firing). Today's single-`branch` key no longer uniquely identifies a leg
  once paths nest.
- **`cas_push` bounded retry loop** (3–5 attempts, small backoff) replacing the
  single rebase-retry. The single retry handles 2-way ref contention; deep nested
  fanouts can land many simultaneous pushers on the shared `agentic-state` ref,
  where a second collision inside the retry window would exhaust one retry. This
  is a pre-existing limit that nesting makes materially more likely to bite.
- **Security rule preserved.** A leg's `path` is composed of `protocol.json` node
  ids, passed to shell via `env:`, never interpolated into `run:` — same
  discipline as today's `branch`/`feedback`. No credential crosses a new zone.

## GHA wiring (`agentic-engine.yml`, `agentic-orchestrator.yml`)

- **Leg carries `path`** (dot-joined) instead of `{branch, substate}` + separate
  `phase`. The matrix axis `leg: {path}` stays a *flat* list (immediate children
  of the just-entered fanout). `lib.py agent-workflow` resolves the agent unit
  from `path`.
- **`ctx` step** parses one `DISPATCH_PATH` from the payload. `branch`/`phase`/
  `substate` survive as derived shims during the migration stage so a
  half-migrated dispatch still resolves.
- **Re-dispatch payloads** (`protocol-continue`, the nested fanout entry) carry
  `client_payload[path]`. `protocol-advance` (top-phase) and `protocol-join`
  continue to work; the join payload gains the fanout `path`.
- **Artifact names** (`runmeta-…`, `verdicts-…`) key on a path-slug instead of
  `branch||agent`-`substate||none`.

## protocol.json schema changes (additive, backward-compatible)

- A sequence child (a branch `states[]` entry) **may now be `kind:"fanout"`**
  (with its own `branches[]` + a `join`) or a nested sequence — the single new
  structural allowance.
- Optional top-level `max_depth`.
- A nested fanout references its join the same way the top one does
  (`join.of == fanout.id`); a load-time validator checks id uniqueness within
  each scope.
- `inputs`, `questions_from`, `merge`, `gate` are unchanged. Every existing
  protocol validates as-is.

## The live deep protocol (proves depth 4 end-to-end)

A new minimal real protocol — working name **`deep-review-stub`** — shaped to
exercise exactly one nested level, mirroring the `recover-mental-model-stub`
rhythm:

```
preflight (fanout)
  ├─ quick     (flat agent)
  └─ deep      (sub-pipeline)
        triage   (agent)
        analyze  (fanout)                     ← depth-4 nested fanout
           ├─ sec   (agent)
           └─ perf  (agent)
           → join-analyze
        report   (agent)  inputs:[analyze legs]
  → join-preflight
done
```

Plus the gh-aw agents it needs (reusing existing agent scaffolding where
possible). **Live verification on a throwaway PR** walks: enter preflight fanout
→ `deep` leg runs `triage` → `triage` done re-dispatches the nested `analyze`
fanout → `sec ∥ perf` run → `join-analyze` bubbles → `report` runs → `deep` leg
done → `join-preflight` → done.

## Staging (migrate-first, then lift the ceiling)

1. **Stage 1 — internal path representation.** Refactor `lib.py` coordinate/nav
   helpers to operate on a path list; the 3 env vars map to a 3-element path.
   **Zero behavior change**; all existing tests + both live protocols
   byte-identical.
2. **Stage 2 — unified `enter_node`/`advance_node`/recursive `join`** collapsing
   the 4 drifted code paths, still depth ≤3. Tests byte-identical;
   **re-verify `code-review` and `recover-mental-model-stub`** before going
   further.
3. **Stage 3 — lift the ceiling:** allow fanout-in-substate, path-keyed join
   markers, bubbling, the `max_depth` guard, new deep pytest fixture(s).
4. **Stage 4 — GHA wiring** + concurrency-key / `cas_push` changes, the
   `deep-review-stub` protocol + agents, and live PR verification.

## Verification — tests

- **Regression net (byte-identical through Stages 1–2):** `single-agent`,
  `fanout-mini`, `pipeline-mini`, `subpipeline-mini`, `multiphase-subpipeline`
  fixtures + the two live protocols.
- **New pytest fixtures:** `deep-fanout` (depth-4: a nested fanout inside a
  sub-pipeline, with a flat sibling branch) and `too-deep` (depth cap+1, asserts
  refusal).
- **New tests:**
  1. recursive `enter_node` walk seeds each level correctly (cursor + first child
     at the path-qualified file; nested fanout emits a flat matrix of its
     immediate children with full paths);
  2. `advance_node` advances within a sequence and bubbles when a sequence ends;
  3. recursive join bubbling across 2 levels (`join-analyze` completing advances
     the `deep` sequence to `report`; the last `deep` step completing fires
     `join-preflight`);
  4. path-keyed join markers are disjoint per fanout;
  5. `max_depth` rejection (static + seed-time) with the precise error;
  6. path-based `inputs` resolution across a scope boundary;
  7. `/answer` to a nested gate (open-gate discovery by tree walk;
     `life_state` correct at depth);
  8. `cas_push` bounded-retry under a simulated multi-writer collision;
  9. cross-protocol path non-overlap (two protocols' seeds into one state dir).
- **Live:** the `deep-review-stub` end-to-end PR walk (Stage 4), in the spirit of
  the PR #82 verification that caught two live-only bugs pytest could not.

## Risks / mitigations

- **The recursive refactor regresses the live depth-≤3 engine.** Mitigated by the
  staged order: Stages 1–2 are behavior-preserving and gated by the full existing
  fixture suite + a re-verification of both live protocols *before* any
  ceiling-lifting code lands.
- **Bubbling join double-fires or deadlocks.** Mitigated by per-fanout
  path-keyed markers (idempotent, like today's `joined` check) + per-fanout join
  concurrency group; a 2-level bubbling test asserts single-fire.
- **Deep concurrency overwhelms the shared ref.** Mitigated by the `cas_push`
  bounded-retry loop and disjoint path-keyed writes (no semantic conflicts to
  rebase).
- **An author writes a pathological deep tree.** Mitigated by the `max_depth`
  guard, on by default.

## Forward-compat

This generalization is the natural endpoint of the consolidation Problem #1
began (one `seed_branch`, one phase qualifier). Once the coordinate is a path and
the sequencer/join are recursive, no future depth change touches the engine —
only `protocol.json` and the `max_depth` knob.
