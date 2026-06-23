# Multi-phase + sub-pipeline fanout: closing the latent `next.py` gap

**Date:** 2026-06-23
**Status:** Approved design, pre-implementation
**Scope:** Problem #1 of two. Problem #2 (arbitrary-depth recursive sub-pipelines)
is a separate, larger spec to be written after this ships.

## Problem

The engine fans out a run into parallel agent *legs* ("branches"). A branch is
either **flat** (one agent unit) or a **sub-pipeline** (`is_subpipeline_branch` —
it has a `states[]` array of linear sub-states, each an agent or gate step).

Two independent properties of a protocol decide which code path seeds and
dispatches a fanout:

- **Has a sub-pipeline branch?** — a branch with `states[]`.
- **Is multi-phase?** — the fanout is *one phase among several* top-level states,
  rather than the whole protocol.

These are orthogonal, and exactly one of the four combinations is broken:

| Protocol | Multi-phase? | Sub-pipeline branch? | Entry path | Works? |
|---|---|---|---|---|
| `recover-mental-model-stub` | no (fanout is top-level) | **yes** | `start_fanout` | ✅ |
| `code-review` | **yes** | no (flat branches) | `seed_and_dispatch_phase` | ✅ |
| *(no protocol yet)* | **yes** | **yes** | `seed_and_dispatch_phase` | 💥 |

No protocol exercises the broken cell today, so the gap is **latent**:
`recover-mental-model-stub` dodges it by being single-phase (entered via
`start_fanout`, which handles sub-pipelines); `code-review` dodges it by having
only flat branches.

The bug fires only when **both** are true: a multi-phase pipeline (≥2 top-level
phases) where one phase is a fanout containing a sub-pipeline branch. Such a
protocol is entered through `seed_and_dispatch_phase`, which is phase-blind to
sub-pipelines.

### The two concrete defects (both in `next.py`)

1. **Emit omits the sub-pipeline.** `seed_and_dispatch_phase` with `kind=="fanout"`
   (`next.py:171-186`) loops over branches and *unconditionally* writes one flat
   per-branch phase file. It never calls `is_subpipeline_branch(b)`, so it does not
   seed the branch **cursor** (`sub_state`) or the **first sub-state file**, and the
   emitted branch dicts omit `"substate"`. Result: a sub-pipeline branch nested in a
   multi-phase pipeline never dispatches its first sub-state.

2. **`/answer` gate path is phase-blind.** `_find_open_gate_branch`, `do_answer`,
   and the cursor/advance writes inside it (`next.py:390-520`) build
   `state_file(..., branch=, substate=)` / `output_artifact_path(...)` **without**
   `phase=`. A multi-phase run writes `<phase>.<branch>.<sub>.yaml`, but these
   functions look in `<branch>.<sub>.yaml` — so even if the first sub-state *were*
   dispatched, a downstream data-gate could never be located, and `/answer` could
   not advance the leg.

### What is already correct (so the fix stays small)

The 3-rung representation (`phase + branch + substate`) already exists end to end:

- `lib.state_file` supports `<phase>.<branch>.<substate>.yaml` (lib.py:49,55-56).
- `lib.resolve_agent_unit` walks the full `phase → branch → substate` ladder
  (lib.py:182-204).
- `advance.py` threads `phase + branch + substate` on the durable-write side
  (advance.py:30,259,351,370,383,537).
- `join.py` threads `phase` (join.py:69).
- `lib.dispatch_continue(pid, instance, branch, substate, phase="")` already
  accepts and forwards `phase` (lib.py:1009-1014).

So the planner/emit side (`next.py`) is the only place that doesn't yet *drive*
the 3-rung combination. The gap is concentrated there.

## Goal

Make a multi-phase pipeline whose one fanout phase contains a sub-pipeline branch
work **end to end**: enter the fanout phase → dispatch the first sub-state →
`/answer` a nested data-gate → advance the leg → fire the join. Engine-only, proven
by a pytest regression fixture. No live gh-aw wiring (that would be a separate
follow-up in the spirit of the `recover-mental-model-stub` effort).

## Non-goals

- **Problem #2 — arbitrary-depth recursion** (a sub-state that is itself a
  fanout/sub-pipeline). Decided: pursue it next, as its own spec. This fix must not
  add throwaway fixed-rung scaffolding that #2 will rip out.
- Any change to `advance.py`, `join.py`, `run-checks.py`, or `lib.state_file` /
  `lib.resolve_agent_unit` — they already support the 3-rung shape.
- Live gh-aw agent wiring, `agentic-engine.yml` matrix changes, or a live PR
  verification.

## Approach (chosen: A — thread `PHASE`, share one seeding helper)

Do **not** fork a second code path. Mirror the engine's existing "one env-var seam,
not a parallel code path" philosophy (the `BRANCH` seam). Two consolidations:

1. **Extract a shared per-branch seeding helper.** Factor the per-branch seeding
   currently inlined in `start_fanout` (`next.py:60-88`) into one helper, e.g.
   `seed_branch(b, fanout_id, phase=None) -> branch_dict`:
   - if `is_subpipeline_branch(b)`: write the branch **cursor** file (carrying
     `sub_state = first["id"]`) and the **first sub-state file**, both at the
     `phase=`-qualified path when `phase` is set; return
     `{"id", "workflow": first["workflow"], "substate": first["id"], "iteration": 1, "feedback": ""}`.
   - else (flat): write the flat per-branch file and return
     `{"id", "workflow": b["workflow"], "iteration": 1, "feedback": ""}`.

   Both `start_fanout` (calls with `phase=None`) and `seed_and_dispatch_phase`
   (calls with `phase=phase_id`) use it. This removes the two-emit-site drift the
   project memory already flagged once.

2. **Thread the `phase` qualifier through the `/answer` gate path.** In
   `_find_open_gate_branch`, `do_answer`, and the cursor/advance writes inside it,
   add `phase=` to every `state_file` / `output_artifact_path` call, and pass
   `phase=` to `dispatch_continue`. Derive the qualifier from the protocol: there is
   exactly one fanout state, so when `is_multiphase(proto)` the phase qualifier is
   `_fanout_state(proto)["id"]` (the fanout *is* a phase); when single-phase it is
   `None` (unchanged behavior). Centralize this derivation in one tiny helper, e.g.
   `_gate_phase(proto)`, so all three call sites agree.

### Rejected alternatives

- **B. Separate multi-phase variants** of the sub-pipeline functions. Duplicates
  logic that has already drifted once; doubles the surface #2 must later make
  recursive.
- **C. Generalize toward recursion now** as part of #1. Violates the agreed
  "ship #1 first" sequencing and YAGNI; #2 gets its own spec.

## Affected code (all in `.github/agent-factory/engine/next.py`)

- `start_fanout` (`next.py:51-99`) — refactor its per-branch loop to call
  `seed_branch`. Behavior must be byte-for-byte unchanged (regression-guarded by the
  existing single-phase fixtures).
- `seed_and_dispatch_phase`, `kind=="fanout"` arm (`next.py:171-186`) — replace the
  flat-only loop with a `seed_branch(b, phase_id)` loop; emit the returned dicts
  (now carrying `substate` for sub-pipeline branches).
- `_find_open_gate_branch` (`next.py:390-408`) — phase-qualify the cursor and gate
  `state_file` lookups.
- `do_answer` (`next.py:~411-520`) — phase-qualify `gsf`, `apath`, `cf`, `nsf`; pass
  `phase=` to `dispatch_continue`. (Life-state derivation via `_fanout_state` already
  yields the fanout/phase id, so it is unchanged.)
- New private helpers `seed_branch(...)` and `_gate_phase(proto)` in the same file.

No new env vars. No changes outside `next.py`.

## Verification — new pytest fixture + regression tests

Add `tests/fixtures/multiphase-subpipeline/` (working name): a protocol that is
`pipeline-mini` × `subpipeline-mini` — ≥2 top-level phases, where one phase is a
fanout containing at least one sub-pipeline branch (with an agent sub-state and a
gate sub-state, to exercise `/answer`) **and** one flat branch alongside it (so the
mixed case — `substate` emitted for one branch, omitted for the other — is proven in
one fixture). Reuse existing check executables/evidence shapes where possible.

New tests (in `tests/`, following `conftest.py` helpers `run_engine` /
`read_state_yaml`), asserting:

1. **Emit (sub-pipeline seeded).** Entering the fanout phase seeds the branch cursor
   with `sub_state == <first sub-state>` and writes the first sub-state file, both at
   the **phase-qualified** path `<phase>.<branch>.<first>.yaml`; the emitted
   `run-fanout` action's branch dict for the sub-pipeline branch carries
   `"substate"`. The flat branch in the same fanout still emits without `substate`.
2. **`/answer` locates the nested gate.** With the leg advanced to its gate
   sub-state, `/answer` finds the open gate at the phase-qualified path, records
   answers, and on full coverage advances the cursor to the next sub-state (writing
   `<phase>.<branch>.<next>.yaml`).
3. **Leg completion → join.** The last sub-state completing sets the leg cursor
   `state: done` and fires the join dispatch.

Existing fixtures (`single-agent`, `fanout-mini`, `pipeline-mini`,
`subpipeline-mini`) must still pass unchanged — they guard that the `start_fanout`
refactor and the `phase=None` default preserve the single-phase behavior.

## Risks / mitigations

- **Refactoring `start_fanout` regresses the working single-phase path.** Mitigated
  by the existing single-phase fixtures, which must pass byte-for-byte; the
  `phase=None` default routes the helper to today's exact behavior.
- **`/answer` phase derivation wrong for single-phase.** `_gate_phase` returns
  `None` when not multi-phase, preserving the current `<branch>.<sub>.yaml` paths;
  covered by `subpipeline-mini`'s existing `/answer` tests.

## Forward-compat with #2

Extracting `seed_branch` and threading the `PHASE` rung (rather than duplicating a
multi-phase variant) is the same consolidation #2's arbitrary-depth recursion will
build on: one seeding helper and one phase/qualifier derivation point are far easier
to generalize into an N-rung recursive walk than two drifting copies.
