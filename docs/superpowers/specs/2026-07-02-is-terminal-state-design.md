# `lib.is_terminal_state()` helper — design

**Date:** 2026-07-02
**Issue:** #215
**Status:** design, pending planning

## Summary

Add a pure-function helper `is_terminal_state(state: str) -> bool` to
`.github/agent-factory/engine/lib.py`. The function returns `True` for exactly
the three terminal states of the engine's state machine — `"done"`, `"failed"`,
`"blocked"` — and `False` for any other value, including `None` and non-string
types (no exception raised). Accompanied by focused unit tests in
`tests/test_engine.py`.

## Scope

**In scope:**
- One function `is_terminal_state(state)` in `lib.py`, placed after the existing
  `decide()` function (line ~991) where the terminal-state names are already
  canonically defined in the docstring.
- A new test section in `tests/test_engine.py` with parametrized cases covering:
  each of the three terminal states, at least two non-terminal strings (e.g.
  `"design"`, `""`), and at least two non-string/None inputs.

**Out of scope:**
- Any refactoring of existing callers (no existing code references
  `is_terminal_state`; the function is purely additive).
- A CLI subcommand for `lib.py` (the function is for programmatic use only).
- Changes to the protocol DSL, evidence schemas, or workflow YAML.

## Behavior / acceptance criteria

1. `is_terminal_state("done")` → `True`
2. `is_terminal_state("failed")` → `True`
3. `is_terminal_state("blocked")` → `True`
4. `is_terminal_state("design")` → `False`
5. `is_terminal_state("")` → `False`
6. `is_terminal_state(None)` → `False` (no `AttributeError` or `TypeError`)
7. `is_terminal_state(42)` → `False` (no `TypeError`)
8. `is_terminal_state("iterate")` → `False` (in-flight state, not terminal)
9. The function uses only Python stdlib; no new imports are needed.
10. All existing tests remain green (`uv run pytest tests/ -q` passes unmodified).

## Accountability Ledger

### L1 — ASSUMPTION: Terminal set is exactly `{"done", "failed", "blocked"}`

**What:** The three terminal states are `"done"`, `"failed"`, and `"blocked"`.
**Why:** The issue mandates this set. The codebase confirms it: `decide()` (lib.py
~line 966) names `"done"`, `"iterate"`, `"failed"` as the process axis values;
`PHASE_LABEL_DEFAULTS` (lib.py ~line 805-808) lists `"done"`, `"failed"`,
`"blocked"` as the three stable end-labels; `"blocked"` is the state written for
a gate node that cannot proceed. `"iterate"` is an *in-flight* state, not a
terminal one.
**What I did:** Verified in `lib.py` (lines 805-808, 966-991) and confirmed
against the issue specification. `"done"` and `"failed"` come from `decide()`;
`"blocked"` comes from gate-advance logic. Together they are the full set.
**Confidence:** high
**Blast radius:** low — a pure reader function; it touches no state, no files, no
external calls.
**Reversibility:** reversible — a one-function addition is trivially deleted.
**Revisit if:** a new terminal state is introduced (e.g. `"abandoned"`); the set
would need expanding.
**Verified:** true

### L2 — DECISION: Tests go in `tests/test_engine.py`

**What:** The new unit tests for `is_terminal_state` are added to the existing
`tests/test_engine.py`, in a new dedicated section.
**Why:** `test_engine.py` is the established home for generic, protocol-agnostic
`lib.py` surface tests (its own docstring says so). The feature is a trivial
addition to that surface; a new file would add navigation overhead with no benefit.
**What I did:** Chose `test_engine.py` and a new `# Section: is_terminal_state`
block, matching the file's existing section-comment convention.
**Confidence:** high
**Blast radius:** low — adding tests to an existing file cannot break existing
tests.
**Reversibility:** reversible — tests can be moved to a new file at any time.
**Revisit if:** the team adopts a convention of one test file per function for
`lib.py` helpers.

### L3 — DECISION: Placement in `lib.py` — after `decide()` (line ~993)

**What:** `is_terminal_state` is inserted immediately after the `decide()` function
(which ends at ~line 991), before `upsert_status_comment`.
**Why:** `decide()` is where `"done"`, `"iterate"`, and `"failed"` are canonically
named for the process axis. Proximity makes the relationship obvious and keeps the
reading order logical: decide produces a state, `is_terminal_state` classifies it.
**What I did:** Identified `decide()` at line ~963-991 of `lib.py`; the insert
point is immediately after its closing return statement.
**Confidence:** high
**Blast radius:** low — a two-line function in the middle of a 1803-line module
does not structurally change the file or any caller.
**Reversibility:** reversible — the function can be moved or removed without
consequence.
**Revisit if:** the codebase reorganises `lib.py` into sub-modules.

### L4 — ASSUMPTION: No CLI subcommand needed

**What:** The helper will not be exposed as a `python3 lib.py <subcommand>` CLI
entry point.
**Why:** The issue explicitly says "pure function, stdlib only." CLI exposure is
only needed when the orchestrator YAML must call into `lib.py` from a shell step.
No such caller is identified.
**What I did:** Confirmed the issue says "pure function" and verified no existing
shell step calls anything resembling `is_terminal_state`.
**Confidence:** high
**Blast radius:** low — omitting a CLI entry is additive-neutral.
**Reversibility:** reversible — adding a CLI entry later is trivial.
**Revisit if:** a GHA step needs to branch on whether a state is terminal.
**Verified:** true

## READ THESE FIRST

Risk-sorted (low-confidence × high blast-radius / irreversibility first):

1. L1 — Assumption about the terminal set (verified against code, but the set
   could grow if new terminal states are introduced).
2. L2 — Test placement decision (conventional but a minor style call).
3. L3 — Placement within lib.py (no risk, included for completeness).
4. L4 — No CLI exposure (confirmed by issue text).
