# `lib.is_terminal_state` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `is_terminal_state(state) -> bool` to `lib.py` and a focused pytest test file for it.

**Architecture:** Pure function that checks membership in a frozenset of three terminal state strings. Placed immediately after `PHASE_LABEL_DEFAULTS` in `lib.py` (line 809) where the terminal label keys already live. A single new test file covers every acceptance criterion. No existing code is modified.

**Tech Stack:** Python 3 stdlib only (no new dependencies). pytest + uv for running tests.

## Global Constraints

- stdlib only — no third-party imports in the function.
- Pure function — no I/O, no side effects.
- Non-string / `None` inputs must return `False` without raising.
- `uv run pytest tests/ -q` must stay green after this change.

---

## File Structure

**Modified (engine — the vendored unit):**
- `.github/agent-factory/engine/lib.py` — add `_TERMINAL_STATES` frozenset + `is_terminal_state()` after line 809.

**Created (tests):**
- `tests/test_is_terminal_state.py` — unit tests for `lib.is_terminal_state()`. Pure; no git fixture needed.

**Regression anchors (run unchanged, must stay green):**
- `tests/test_engine.py`, `tests/test_decide.py`, `tests/test_fanout_e2e.py` — zero changes to these; confirmed green before and after.

---

## Task 1: `is_terminal_state()` in `lib.py` + tests

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (insert after line 809)
- Create: `tests/test_is_terminal_state.py`

**Interfaces:**
- Produces: `lib.is_terminal_state(state: object) -> bool`
  - Returns `True` iff `state in {"done", "failed", "blocked"}`, else `False`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_is_terminal_state.py`:

```python
"""Unit tests for lib.is_terminal_state() — the terminal-state predicate."""
import pathlib
import sys

ENGINE = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def test_done_is_terminal():
    assert lib.is_terminal_state("done") is True


def test_failed_is_terminal():
    assert lib.is_terminal_state("failed") is True


def test_blocked_is_terminal():
    assert lib.is_terminal_state("blocked") is True


def test_design_is_not_terminal():
    assert lib.is_terminal_state("design") is False


def test_preflight_is_not_terminal():
    assert lib.is_terminal_state("preflight") is False


def test_iterate_is_not_terminal():
    assert lib.is_terminal_state("iterate") is False


def test_empty_string_is_not_terminal():
    assert lib.is_terminal_state("") is False


def test_setup_is_not_terminal():
    # "setup" is a lifecycle label, not a stopping state.
    assert lib.is_terminal_state("setup") is False


def test_none_returns_false_without_raising():
    assert lib.is_terminal_state(None) is False


def test_integer_returns_false_without_raising():
    assert lib.is_terminal_state(0) is False


def test_list_returns_false_without_raising():
    assert lib.is_terminal_state([]) is False
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_is_terminal_state.py -q`
Expected: `AttributeError: module 'lib' has no attribute 'is_terminal_state'` (11 errors).

- [x] **Step 3: Implement `is_terminal_state()` in `lib.py`**

In `.github/agent-factory/engine/lib.py`, after line 809 (`PHASE_LABEL_COLOR = ...`), insert:

```python
_TERMINAL_STATES = frozenset({"done", "failed", "blocked"})


def is_terminal_state(state):
    """Return True iff state is a terminal (non-resumable) engine state.

    Terminal states: "done" (success), "failed" (exhausted/gate-blocked),
    "blocked" (pipeline halted by on_blocked:halt gate). Non-string or None
    arguments return False without raising.
    """
    return isinstance(state, str) and state in _TERMINAL_STATES
```

The insertion point is immediately after:
```
PHASE_LABEL_COLOR = "5319e7"  # one color for every engine-managed phase label
```
and before:
```
def _humanize_state_id(state_id):
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_is_terminal_state.py -q`
Expected: `11 passed`.

- [x] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest tests/ -q`
Expected: all existing tests pass plus the 11 new ones.

- [x] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_is_terminal_state.py
git commit -m "feat(engine): add lib.is_terminal_state() predicate + unit tests"
```

---

## Self-Review

**1. Spec coverage:**

| Acceptance criterion | Task/Step |
|---|---|
| `is_terminal_state("done")` → `True` | Task 1, `test_done_is_terminal` |
| `is_terminal_state("failed")` → `True` | Task 1, `test_failed_is_terminal` |
| `is_terminal_state("blocked")` → `True` | Task 1, `test_blocked_is_terminal` |
| `is_terminal_state("design")` → `False` | Task 1, `test_design_is_not_terminal` |
| `is_terminal_state("preflight")` → `False` | Task 1, `test_preflight_is_not_terminal` |
| `is_terminal_state("iterate")` → `False` | Task 1, `test_iterate_is_not_terminal` |
| `is_terminal_state("")` → `False` | Task 1, `test_empty_string_is_not_terminal` |
| `is_terminal_state("setup")` → `False` | Task 1, `test_setup_is_not_terminal` |
| `is_terminal_state(None)` → `False` no raise | Task 1, `test_none_returns_false_without_raising` |
| `is_terminal_state(0)` → `False` no raise | Task 1, `test_integer_returns_false_without_raising` |
| `is_terminal_state([])` → `False` no raise | Task 1, `test_list_returns_false_without_raising` |
| stdlib only, no I/O | Task 1, Step 3 (no imports added) |
| Full suite stays green | Task 1, Step 5 |

All 13 acceptance criteria covered. No gaps.

**2. Placeholder scan:** No TBD/TODO/similar-to-task-N patterns. All code blocks are complete.

**3. Type consistency:** `is_terminal_state(state)` is defined once in Step 3 and tested under that exact name in Step 1. `_TERMINAL_STATES` is a module-level name; no task references it by any other name.
