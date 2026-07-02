# `lib.is_terminal_state(state)` helper — design

**Date:** 2026-07-02
**Status:** design
**Issue:** #217 — Add lib.is_terminal_state(state) helper

## Summary

Add a small, pure helper `is_terminal_state(state: str) -> bool` to
`.github/agent-factory/engine/lib.py` that returns whether a state-machine state
string is a terminal (non-resumable) engine state. Terminal states are the three
states from which the engine will never re-dispatch: `"done"` (clean success),
`"failed"` (max iterations exhausted or gate blocked), and `"blocked"` (pipeline
halted by a gate with `on_blocked: halt`). A non-string or `None` argument returns
`False` without raising.

The function is a single-file, stdlib-only addition to the engine's shared library.
Focused pytest unit tests live in `tests/test_is_terminal_state.py`.

## Scope

**In scope:**
- New function `is_terminal_state(state)` in
  `.github/agent-factory/engine/lib.py`, placed immediately after the
  `PHASE_LABEL_DEFAULTS` block (line 809 in the current file) where the terminal
  label keys are already defined.
- New test file `tests/test_is_terminal_state.py` covering:
  - Each of the three terminal states returns `True`
  - Representative non-terminal strings (e.g. `"design"`, `"preflight"`, `""`,
    `"iterate"`) return `False`
  - Non-string inputs (`None`, `0`, `[]`) return `False` without raising
- No refactoring: no call-sites in existing engine code are changed to use this
  helper. It is added as a utility; callers may adopt it independently.

**Out of scope:**
- Changing any logic in `advance.py`, `next.py`, `join.py`, or any protocol file.
- Adding `"setup"` to the terminal set (it is an engine lifecycle label, not a
  stopping state — the engine always proceeds past it).
- CLI exposure (`python3 lib.py is-terminal-state <state>`).

## Behavior / acceptance criteria

1. `lib.is_terminal_state("done")` → `True`
2. `lib.is_terminal_state("failed")` → `True`
3. `lib.is_terminal_state("blocked")` → `True`
4. `lib.is_terminal_state("design")` → `False`
5. `lib.is_terminal_state("preflight")` → `False`
6. `lib.is_terminal_state("iterate")` → `False`
7. `lib.is_terminal_state("")` → `False`
8. `lib.is_terminal_state("setup")` → `False` (it is a lifecycle label, not terminal)
9. `lib.is_terminal_state(None)` → `False` (no exception)
10. `lib.is_terminal_state(0)` → `False` (no exception)
11. `lib.is_terminal_state([])` → `False` (no exception)
12. Implementation: stdlib only, no I/O, no side effects.
13. Test file passes `uv run pytest tests/test_is_terminal_state.py -q`.
14. Full suite (`uv run pytest tests/ -q`) stays green.

## Accountability Ledger

| ID | Category | What | Why | What I Did | Confidence | Blast Radius | Reversibility | Revisit-if |
|----|----------|------|-----|------------|------------|--------------|---------------|------------|
| L1 | DECISION | Placement in lib.py: immediately after `PHASE_LABEL_DEFAULTS` (line 809) | The issue does not specify placement; it must sit somewhere coherent | The three terminal states are exactly the non-`"setup"` keys of `PHASE_LABEL_DEFAULTS` — placing the helper there makes the coupling to label definitions explicit and readable | high | low — pure new function, zero existing lines modified | reversible — moving a function is a trivial edit | Different placement is preferred by reviewer |
| L2 | DECISION | Hardcode `{"done", "failed", "blocked"}` as a module-level `frozenset` rather than deriving it dynamically from `PHASE_LABEL_DEFAULTS` | Issue says "stdlib only, pure function"; deriving from the label dict would couple the terminal-state predicate to the label rendering concern | Hardcoded frozenset: `_TERMINAL_STATES = frozenset({"done", "failed", "blocked"})`. Both the function and the frozenset live in the Phase labels section of lib.py for co-location | high | low — no behavioral change to any existing code | reversible — could switch to deriving if desired | Reviewer prefers dynamic derivation |
| L3 | ASSUMPTION (verified) | `"blocked"` is a genuine terminal state, not just a label | The issue explicitly lists it; needed to verify it corresponds to a real engine stopping condition | Read `lib.py:803-808` (`PHASE_LABEL_DEFAULTS`) and `advance.py:578-600`: `"blocked"` is the `halted.reason` written when a gate with `on_blocked: halt` fires — the pipeline will not resume. Confirmed terminal. | high | n/a | n/a | n/a |
| L4 | ASSUMPTION (verified) | `"setup"` must NOT be in the terminal set | The issue does not mention `"setup"`; including it would be incorrect | Read `PHASE_LABEL_DEFAULTS:803-808`: `"setup"` is the lifecycle label applied before any agent runs — it transitions forward, it is never a stopping state. `is_terminal_state("setup")` must return `False`. | high | n/a | n/a | n/a |
| L5 | DECISION | Test file: `tests/test_is_terminal_state.py` (new, dedicated) | Existing pattern is `test_<concept>.py` (e.g. `test_decide.py` for `lib.decide()`) | Follow the established pattern; a dedicated file makes the tests easy to locate and keeps `test_engine.py` uncluttered | high | low | reversible — move tests if project standard changes | Project switches to consolidated test file style |

## READ THESE FIRST

Risk-sorted (low-confidence × high/irreversible first — none here qualify; all items are high-confidence and fully reversible):

1. **L1** — placement decision: if the reviewer wants the function elsewhere (e.g. near `decide()`) this is a one-line move before implementation starts.
2. **L2** — hardcoded frozenset vs. dynamic derivation: negligible risk but the choice is visible in the public API.
3. **L3, L4** — both verified against source; no residual risk.
4. **L5** — test-file naming: cosmetic.
