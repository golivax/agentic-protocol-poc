# `lib.is_terminal_state()` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `is_terminal_state(state) -> bool` to `.github/agent-factory/engine/lib.py` and focused unit tests in `tests/test_engine.py`.

**Architecture:** One pure function inserted after `decide()` in `lib.py` (which already defines the `"done"`/`"failed"` names in its docstring); unit tests added as a new section at the end of `tests/test_engine.py` using `pytest.mark.parametrize` with direct module import. No refactoring; no CLI subcommand.

**Tech Stack:** Python 3 (stdlib only), pytest via `uv run pytest`.

## Global Constraints

- Pure function, stdlib only — **no new imports** added to `lib.py`.
- Scope is exactly two files: `lib.py` and `tests/test_engine.py`. No other file changes.
- All existing tests must stay green: `uv run pytest tests/ -q` (baseline 673+ passing).
- TDD: write the failing test before the implementation.

---

## Task 1: Add `is_terminal_state` to `lib.py` + focused unit tests

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` — insert two lines after `decide()` ends (line ~991, immediately before `def upsert_status_comment`)
- Modify: `tests/test_engine.py` — append a new section at the end of the file (after line 176)

**Interfaces:**
- Produces: `is_terminal_state(state: Any) -> bool` in the `lib` module.
  - `True` for exactly `"done"`, `"failed"`, `"blocked"`.
  - `False` for all other values, including `None` and non-string types (no exception raised).

---

- [x] **Step 1: Write the failing tests**

Open `tests/test_engine.py`. The file already defines `ENGINE` at the top (line 31-32):
```python
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
```

Append the following block at the very end of the file (after line 176):

```python


# ===========================================================================
# Section: is_terminal_state
# ===========================================================================

import sys as _sys
_sys.path.insert(0, str(ENGINE))
from lib import is_terminal_state  # noqa: E402


@pytest.mark.parametrize("state,expected", [
    ("done",    True),
    ("failed",  True),
    ("blocked", True),
    ("design",  False),
    ("",        False),
    ("iterate", False),
    (None,      False),
    (42,        False),
])
def test_is_terminal_state(state, expected):
    assert is_terminal_state(state) == expected
```

Note: The `sys.path.insert` + `from lib import` runs at module load time. `ENGINE` is already defined at the file's top level (line 31-32), so this reference is valid at the bottom of the same module.

- [x] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_engine.py::test_is_terminal_state -v
```

Expected failure: `ImportError: cannot import name 'is_terminal_state' from 'lib'`
(or `ImportError: cannot import name 'is_terminal_state'` — confirms the function does not exist yet).

- [x] **Step 3: Implement `is_terminal_state` in `lib.py`**

Open `.github/agent-factory/engine/lib.py`. Locate `decide()`, which ends at ~line 991:

```python
    return process, block_fail


def upsert_status_comment(sf, pr, body):
```

Insert the new function in the blank gap between `decide()` and `upsert_status_comment` (i.e., replace the two blank lines with the function + two blank lines):

```python
    return process, block_fail


def is_terminal_state(state):
    """Return True iff *state* is a terminal engine state (done/failed/blocked)."""
    return state in {"done", "failed", "blocked"}


def upsert_status_comment(sf, pr, body):
```

The full insertion is three lines (the `def`, the docstring, and the `return`), indented at the module level. No new imports needed — `in` on a set handles any type including `None` without raising.

- [x] **Step 4: Run the parametrized test to verify it passes**

```bash
uv run pytest tests/test_engine.py::test_is_terminal_state -v
```

Expected output (8 PASSed):
```
PASSED tests/test_engine.py::test_is_terminal_state[done-True]
PASSED tests/test_engine.py::test_is_terminal_state[failed-True]
PASSED tests/test_engine.py::test_is_terminal_state[blocked-True]
PASSED tests/test_engine.py::test_is_terminal_state[design-False]
PASSED tests/test_engine.py::test_is_terminal_state[-False]
PASSED tests/test_engine.py::test_is_terminal_state[iterate-False]
PASSED tests/test_engine.py::test_is_terminal_state[state7-False]
PASSED tests/test_engine.py::test_is_terminal_state[state8-False]
8 passed
```

- [x] **Step 5: Run the full test suite to confirm no regressions**

```bash
uv run pytest tests/ -q
```

Expected: all existing tests still pass (673+ passing, 0 failed). Any pre-existing skip/xfail is unchanged.

- [x] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_engine.py
git commit -m "feat(lib): add is_terminal_state() helper + unit tests"
```

---

## Self-Review

**Spec coverage:**
| Spec requirement | Task covering it |
|---|---|
| `is_terminal_state(state: str) -> bool` in `lib.py` | Task 1, Step 3 |
| Returns `True` for `"done"`, `"failed"`, `"blocked"` | Task 1, Steps 1 + 3 |
| Returns `False` for non-terminal strings (e.g. `"design"`, `""`) | Task 1, Step 1 params |
| Returns `False` for `None` and non-string without raising | Task 1, Step 1 params |
| Focused pytest unit tests covering each case | Task 1, Step 1 |
| No refactoring | One task, two files |
| Pure function, stdlib only | No imports added to `lib.py` |
| All existing tests stay green | Task 1, Step 5 |

**Placeholder scan:** No TBDs, no "similar to above", no missing code blocks. ✓

**Type consistency:** `is_terminal_state` produced in Step 3 matches the name used in the `from lib import` in Step 1. ✓
