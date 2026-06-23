# Design: nested-gate `/answer` + default `max_depth` = 5

Date: 2026-06-23
Status: approved
Follows: `2026-06-23-recursive-subpipelines-design.md` (Problem #2 engine)

## Motivation

Problem #2 made every engine operation recursive across arbitrary (bounded)
depth — opening gates, advancing sub-pipeline cursors, and bubbling joins all
work at any path depth. **One operation was deliberately left at depth-3: the
`/answer` comment handler** (`next.py` `do_answer` + `_find_open_gate`). It was
deferred as a known limitation because gates sit at odd path-depths (3, 5, 7…)
and the next nested gate is depth 5, which the then-default `max_depth = 4`
structurally forbade — so no protocol could reach it.

This change (a) raises the default cap to 5 so a depth-5 nested gate is allowed
out of the box, and (b) makes `/answer` recursive like the rest of the engine,
so Stage 4's real protocol can use a deeply-nested question gate.

Scope: **engine + pytest only.** No live gh-aw/PR wiring (that is Stage 4).

## Part A — default `max_depth` 4 → 5

- `engine/lib.py`: `DEFAULT_MAX_DEPTH = 5`. `effective_max_depth` /
  `check_depth` are otherwise unchanged.
- **Ripple — the guard fixture.** `tests/fixtures/too-deep/` is a depth-5
  protocol with no `max_depth` field; `test_max_depth.py::test_default_cap_rejects_depth5`
  asserts the *default* cap rejects it. With the default now 5, depth-5 passes,
  so the guard fixture must move to **depth-6**:
  - Deepen `too-deep` to depth 6.
  - Retarget `test_max_depth.py`: the default cap now rejects **depth-6**; an
    explicit `max_depth: 6` allows the depth-6 fixture; the CLI exits 2 on the
    depth-6 fixture.
- The new depth-5 gate fixture (Part D) doubles as proof that the default cap (5)
  *allows* depth-5.

## Part B — recursive `_find_open_gate` (`next.py`)

`/answer` first has to identify which open gate the comment refers to. Today
`_find_open_gate` walks only the top fanout's branches: for each sub-pipeline
branch whose live cursor `sub_state` is a `gate` in state `open`, it returns the
depth-3 path `[fanout_id, branch_id, gate_id]`. A deeper gate is invisible.

**Change:** follow live cursors recursively. At each fanout branch, read its
cursor `sub_state`:
- `sub_state` is a `gate` in state `open` → return its full tree path.
- `sub_state` is a nested `fanout` → recurse into that fanout's child-branch
  cursors (same rule, one level deeper).
- otherwise (agent in flight, no cursor, etc.) → skip.

First open gate found wins — at most one gate per branch lineage is open at a
time (a sequence pauses at its gate). `want` keeps its current meaning:
restrict the search to a named **top-level** branch only (no nested
disambiguation; documented).

Returns the gate's full **tree** path (rooted at the top fanout id). For a
depth-3 gate this is byte-identical to today's result, so depth-3 `/answer`
behavior is unchanged.

## Part C — path-aware `do_answer` completion (`next.py`)

After the answers-coverage check passes, the pipeline must step forward. Today
`do_answer` steps the *top* branch's cursor and, on leg completion, fires the
*top* join (`fire_join_dispatch`). For a nested gate it must step the *enclosing
sub-pipeline* cursor and fire the *enclosing* (nested) join.

`do_answer` already computes the gate's tree path (from `_find_open_gate`). The
recursive "step the sequence forward / open next gate / bubble the join"
machinery **already exists** — it is what `advance.py` `advance_node` does on
`process="done"`, and the `continue`-at-`NODE_PATH` guard in `next.py`
(lines ~672–713) already seeds + dispatches the next sibling by kind
(agent / fanout / gate) path-aware. `do_answer` reuses that rather than
reimplementing per-kind logic.

**Discriminator** (identical rule to `advance.py` `_join_path`):
`fanout_path = paths.enclosing_fanout_path(proto, gate_path)`.

- **Top gate** (`len(fanout_path) <= 1`, the depth-3 case) → **existing inline
  logic, byte-identical.** Seed the next sub-state file, `dispatch_continue`
  (branch/substate, legacy resolution); on leg-done `fire_join_dispatch`. The
  existing `test_gate_data.py` `/answer` tests stay untouched and green.
- **Nested gate** (`len(fanout_path) > 1`):
  1. Close the gate (mark `answered`) — already path-aware via `lib.state_path`.
  2. `nxt = paths.next_sibling(proto, gate_path)`; `parent = parent_path(gate_path)`
     (the enclosing sequence's cursor file path).
  3. If `nxt` exists → write the parent cursor (`sub_state = nxt`,
     `state = life`), `cas_push`, and **re-dispatch `protocol-continue` carrying
     `client_payload[path] = parent + [nxt]`**. The `continue`-at-`NODE_PATH`
     guard then seeds/opens/dispatches `nxt` by kind. `do_answer` does **not**
     reimplement per-kind seeding for the nested case.
  4. If no `nxt` (gate is the last sub-state) → mark the parent cursor `done`,
     `cas_push`, and fire a **path-carrying join** so `join.py` bubbles up the
     enclosing fanout barrier.

**Two small `lib` helpers** (additive, back-compatible):
- a path-carrying `protocol-continue` dispatch (e.g. `dispatch_continue` gains an
  optional `path=` that, when set, sends `client_payload[path]` instead of
  branch/substate); and
- `fire_join_dispatch(pid, instance, fanout_path="")` gains the optional
  `fanout_path` arg (sent as `client_payload[path]` only when non-empty — top
  join stays path-less, byte-identical).

Gates followed by a fanout or another gate are handled for free by the `continue`
guard delegation — no extra code in `do_answer`.

## Part D — depth-5 gate fixture + tests

New `tests/fixtures/gate-deep/` — single-phase, **no `max_depth` field** (so it
exercises the new default 5):

```
outer (fanout, depth 1)
  branches:
    - A  (flat agent leg, depth 2)            # makes outer a real fanout
    - B  (sub-pipeline, depth 2)
        states:
          - inner (fanout, depth 3)
              branches:
                - D (flat agent leg, depth 4) # makes inner a real fanout
                - C (sub-pipeline, depth 4)
                    states:
                      - probe   (agent, depth 5, produces questions)
                      - clarify (gate,  depth 5, questions_from: probe)
                      - wrap    (agent, depth 5)
          - join-inner (join, of: inner, next: report)
          - report (agent, depth 3)
  next: join-outer
join-outer (join, of: outer)
```

Gate `clarify` sits at `[outer, B, inner, C, clarify]` — depth 5.

Fixture also needs: `checks/answers-coverage.py` + `checks/always-pass.py`
(copy from `subpipeline-mini`), evidence schemas for `probe`/`wrap`/`A`/`D`/`report`,
and `questions_from: probe` wiring.

**Tests** (pytest, `ENGINE_LOCAL=1`; assert state writes + dispatch args via the
`ENGINE_LOCAL` stderr log):
- `_find_open_gate` returns `[outer, B, inner, C, clarify]` when that gate is
  seeded open (and `None`/skip when not).
- `do_answer` partial coverage → gate stays open, partial comment recorded.
- `do_answer` full coverage → enclosing-sequence cursor advances to `wrap`, a
  `protocol-continue` is dispatched with `client_payload[path] = outer.B.inner.C.wrap`.
- `do_answer` on a gate-as-last-substate variant → fires the nested join with the
  correct `client_payload[path]` (the enclosing fanout path).
- **Regression:** the full existing suite (445 tests) stays green; depth-3
  `/answer` (`test_gate_data.py`) byte-identical.

## Non-goals (YAGNI)

- No live gh-aw agents, no live PR `/answer` verification (Stage 4).
- No nested `want` disambiguation (top-level branch selector only).
- No new subsystem — both parts reuse existing recursive machinery.
