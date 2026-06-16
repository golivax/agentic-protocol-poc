# Milestone 1 — Engine Foundations: DECIDE + `on_fail` Severities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the engine's process decision explicit and severity-aware — a pure `lib.decide()` fold over check verdicts, fed by a new per-check `on_fail` severity (`iterate`/`advisory`/`block`) stamped by the check runner — without changing any observable behavior of the existing `grumpy-review`/`multi-grumpy` protocols.

**Architecture:** Today `advance.py` decides "iterate vs done vs failed" with an inline `all_pass = all(r.pass)` ladder, and `on_fail` in `protocol.json` is decorative. This milestone (1) stamps each verdict with its protocol-declared `on_fail` in `run-checks.py`, (2) adds a pure `lib.decide(results, iterations_remaining) → (process, blocking)`, and (3) swaps the `advance.py` ladder for `decide()`. Every existing check defaults to `on_fail: "iterate"`, so `decide()` reproduces today's outcomes exactly. The `blocking` output has no consumer yet (it lands in the M2 phase-gate); it is computed and returned but intentionally unused here.

**Tech Stack:** Python 3 + PyYAML (runtime); pytest (dev-only). No new dependencies.

**Scope note:** This is the first of three milestone plans for the spec at `docs/superpowers/specs/2026-06-16-code-review-pipeline-design.md`. M1 is self-contained and shippable. The **conclude/publish seam** the spec lists under M1 is deferred to the M2 plan — it has no consumer until the phase-gate exists, and building it now would be untested scaffolding. M2 (multi-phase state machine + generic orchestrator) and M3 (preflight port + live test) each get their own plan after the prior milestone lands.

---

## File Structure

**Modified (engine — the vendored unit):**
- `.github/agent-factory/engine/lib.py` — add the pure `decide()` function (no I/O; sits with the other pure helpers like `match_run_by_cid`).
- `.github/agent-factory/engine/run-checks.py` — read each check entry's `on_fail` (default `"iterate"`) and stamp it onto every emitted verdict, including failure verdicts.
- `.github/agent-factory/engine/advance.py` — replace the inline `all_pass`/`iter_<max` ladder with `decide()`; filter retry feedback to `iterate`-severity failures.

**Modified (docs):**
- `CLAUDE.md` — one sentence in the Check ABI bullet documenting `on_fail`.

**Created (tests):**
- `tests/test_decide.py` — unit tests for `lib.decide()` (pure; no git fixture needed).

**Modified (tests):**
- `tests/test_runchecks.py` — assert verdicts carry the stamped `on_fail`.

**Regression anchors (run unchanged, must stay green):**
- `tests/test_engine.py` (advance.py single-agent + branch behavior), `tests/test_publish.py`, `tests/test_fanout_e2e.py`.

---

## Task 1: Pure `decide()` fold in `lib.py`

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add function after `match_run_by_cid`, ~line 177)
- Test: `tests/test_decide.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_decide.py`:

```python
"""Unit tests for lib.decide() — the pure process/conclusion fold.

decide(results, iterations_remaining) -> (process, blocking)
  process  ∈ {"done","iterate","failed"} : the process axis (drives the loop)
  blocking : bool                          : did a `block`-severity check fail
Severity comes from each result's "on_fail" (default "iterate" when absent).
"""
import pathlib
import sys

ENGINE = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def r(check, passed, on_fail=None):
    """Build a verdict dict; omit on_fail entirely when None (tests the default)."""
    v = {"check": check, "pass": passed, "feedback": "" if passed else f"{check} failed"}
    if on_fail is not None:
        v["on_fail"] = on_fail
    return v


def test_empty_results_with_room_iterates():
    assert lib.decide([], iterations_remaining=True) == ("iterate", False)


def test_empty_results_without_room_fails():
    assert lib.decide([], iterations_remaining=False) == ("failed", False)


def test_all_pass_is_done():
    results = [r("schema-valid", True), r("rubric-coverage", True)]
    assert lib.decide(results, iterations_remaining=True) == ("done", False)


def test_iterate_fail_with_room_iterates():
    results = [r("schema-valid", True), r("rubric-coverage", False, "iterate")]
    assert lib.decide(results, iterations_remaining=True) == ("iterate", False)


def test_iterate_fail_without_room_fails():
    results = [r("rubric-coverage", False, "iterate")]
    assert lib.decide(results, iterations_remaining=False) == ("failed", False)


def test_block_fail_does_not_iterate_but_blocks():
    results = [r("schema-valid", True), r("spec-present", False, "block")]
    assert lib.decide(results, iterations_remaining=True) == ("done", True)


def test_advisory_fail_is_ignored():
    results = [r("schema-valid", True), r("docs-updated", False, "advisory")]
    assert lib.decide(results, iterations_remaining=True) == ("done", False)


def test_iterate_and_block_with_room():
    results = [r("schema-valid", False, "iterate"), r("spec-present", False, "block")]
    assert lib.decide(results, iterations_remaining=True) == ("iterate", True)


def test_iterate_and_block_without_room():
    results = [r("schema-valid", False, "iterate"), r("spec-present", False, "block")]
    assert lib.decide(results, iterations_remaining=False) == ("failed", True)


def test_missing_on_fail_defaults_to_iterate():
    # No on_fail key at all → treated as iterate (v1 back-compat).
    results = [r("legacy-check", False)]
    assert lib.decide(results, iterations_remaining=True) == ("iterate", False)
    assert lib.decide(results, iterations_remaining=False) == ("failed", False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_decide.py -q`
Expected: FAIL — `AttributeError: module 'lib' has no attribute 'decide'`.

- [ ] **Step 3: Implement `decide()` in `lib.py`**

Insert this function immediately after `match_run_by_cid` (after line 176, before `upsert_status_comment`):

```python
def decide(results, iterations_remaining):
    """Pure fold: (check verdicts + severities) → (process, blocking).

    process  ∈ {"done","iterate","failed"} — the process axis that drives the
             iterate loop and terminal state.
    blocking : bool — did a `block`-severity check fail (the conclusion-axis
             input; no consumer yet — the M2 phase-gate reads it).

    Severity is each verdict's "on_fail" (default "iterate" when absent, so
    pre-severity verdicts and the single-agent regression path are unchanged).
    `iterate`-severity failures drive the loop; `block` failures never iterate
    but set blocking; `advisory` failures are recorded only. Zero verdicts is a
    checks-job failure → treated as a failed attempt.
    """
    if not results:
        return ("iterate" if iterations_remaining else "failed"), False
    sev = lambda v: v.get("on_fail", "iterate")
    iterate_fail = any(not v.get("pass") and sev(v) == "iterate" for v in results)
    block_fail = any(not v.get("pass") and sev(v) == "block" for v in results)
    if iterate_fail:
        process = "iterate" if iterations_remaining else "failed"
    else:
        process = "done"
    return process, block_fail
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_decide.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_decide.py
git commit -m "feat(engine): add pure decide() fold over check verdicts + severities

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Stamp `on_fail` onto verdicts in `run-checks.py`

**Files:**
- Modify: `.github/agent-factory/engine/run-checks.py:29-31` (the `fail_verdict` helper) and `:74-142` (the check loop)
- Modify: `CLAUDE.md` (Check ABI bullet)
- Test: `tests/test_runchecks.py` (add two tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runchecks.py` (after the existing tests). The module already
defines everything these use: the runner helper `run_checks(proto, state_id,
evidence, diff, files, branch=None, env=None)`, the constants `GRUMPY_PROTO`,
`EV_COMPLETE`, `DIFF_PR1`, `FILES_PR1`, and the `temp_proto_in_grumpy` fixture
(writes a temp `protocol.json` inside the grumpy dir and cleans it up — used by
`test_runner_unknown_check_fail_verdict`). The grumpy `review` checks declare no
`on_fail`, so they must come back stamped `"iterate"`:

```python
def test_runner_stamps_default_on_fail_iterate():
    """Every verdict carries on_fail; absent in protocol.json ⇒ 'iterate'."""
    out = run_checks(GRUMPY_PROTO, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1)
    results = out["results"]
    assert results, "expected verdicts"
    assert all(v.get("on_fail") == "iterate" for v in results), results


def test_runner_stamps_declared_on_fail_on_failure_verdict(temp_proto_in_grumpy):
    """A failure verdict is stamped with the entry's DECLARED on_fail (not the default)."""
    proto_content = json.loads(GRUMPY_PROTO.read_text())
    proto_content["states"][0]["checks"] = [{"run": "does-not-exist", "on_fail": "block"}]
    temp_proto_in_grumpy.write_text(json.dumps(proto_content))
    out = run_checks(temp_proto_in_grumpy, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1)
    assert out["results"][0]["pass"] is False
    assert out["results"][0]["on_fail"] == "block"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_runchecks.py -k "on_fail" -q`
Expected: FAIL — `KeyError`/`assert None == 'iterate'` (verdicts have no `on_fail` key yet).

- [ ] **Step 3: Update `fail_verdict` to carry `on_fail`**

In `.github/agent-factory/engine/run-checks.py`, replace the helper at lines 29-31:

```python
def fail_verdict(name, feedback):
    return {"check": name, "pass": False, "feedback": feedback}
```

with:

```python
def fail_verdict(name, feedback, on_fail="iterate"):
    return {"check": name, "pass": False, "feedback": feedback, "on_fail": on_fail}
```

- [ ] **Step 4: Read each entry's `on_fail` and stamp every verdict**

In the same file, inside the `for entry in checks_list:` loop (starts line 74), do four edits:

(a) Right after `ex = entry.get("exec", "") or ""` (line 76), add:

```python
        on_fail = entry.get("on_fail", "iterate")
```

(b) The resolution-error branch (lines 82-84) becomes:

```python
        if kind == "ERR":
            results.append(fail_verdict(name, rest, on_fail))
            continue
```

(c) The non-executable branch (lines 88-93) becomes:

```python
        if not os.access(path, os.X_OK):
            results.append(fail_verdict(
                name,
                f"check is not executable: {path} (chmod +x and add a shebang)",
                on_fail,
            ))
            continue
```

(d) The runner-error branch (lines 106-108):

```python
        except OSError as exc:
            results.append(fail_verdict(name, f"check runner error: {exc}", on_fail))
            continue
```

(e) The nonzero-exit branch (lines 110-115):

```python
        if result.returncode != 0:
            results.append(fail_verdict(
                name,
                f"check exited {result.returncode} (a check must exit 0 and print a JSON verdict)",
                on_fail,
            ))
            continue
```

(f) The two malformed-verdict branches (lines 122-138) — both `results.append(fail_verdict(name, "...did not print a valid {check,pass,feedback} JSON verdict"))` calls — add `, on_fail` as the third argument to each.

(g) The success path: replace the final `results.append(verdict)` (line 140) with:

```python
        verdict["on_fail"] = on_fail
        results.append(verdict)
```

- [ ] **Step 5: Document `on_fail` in `CLAUDE.md`**

In `CLAUDE.md`, find the **Check** bullet under "## Contracts (ABIs)" (it begins "**Check:** an executable invoked as ..."). Append this sentence to the end of that bullet:

```markdown
  Each check entry may declare `on_fail` (`"iterate"` default | `"advisory"` |
  `"block"`); the runner stamps it onto the verdict, and the engine's `decide()`
  fold uses it — `iterate` drives the retry loop, `block` blocks the conclusion
  without iterating, `advisory` is recorded only.
```

- [ ] **Step 6: Run the new tests + the full runner suite**

Run: `pytest tests/test_runchecks.py -q`
Expected: PASS (all existing runner tests + the 2 new ones).

- [ ] **Step 7: Commit**

```bash
git add .github/agent-factory/engine/run-checks.py tests/test_runchecks.py CLAUDE.md
git commit -m "feat(engine): stamp protocol on_fail severity onto every verdict

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Swap `advance.py`'s decision ladder for `decide()`

**Files:**
- Modify: `.github/agent-factory/engine/advance.py:243-251` (decision + feedback) and the docstring
- Regression: `tests/test_engine.py`, `tests/test_fanout_e2e.py`, `tests/test_publish.py` (unchanged, must stay green)

- [ ] **Step 1: Run the regression anchors first to confirm a green baseline**

Run: `pytest tests/test_engine.py tests/test_fanout_e2e.py tests/test_publish.py -q`
Expected: PASS (these are the byte-identical guard; they must already pass before the edit).

- [ ] **Step 2: Replace the decision block in `advance.py`**

In `.github/agent-factory/engine/advance.py`, replace lines 243-251:

```python
    results = verdicts.get("results", [])
    all_pass = len(results) > 0 and all(r.get("pass", False) for r in results)

    # Compute feedback string
    fb_parts = [r.get("feedback", "") for r in results if not r.get("pass", False)]
    fb = "; ".join(p for p in fb_parts if p)
    if not fb and len(results) == 0:
        fb = "no check verdicts produced (checks job failure?)"
```

with:

```python
    results = verdicts.get("results", [])
    # DECIDE: the process axis (iterate/done/failed) is a pure fold over the
    # verdicts + their on_fail severities. `blocking` (a block-severity fail)
    # has no consumer in M1 — the M2 phase-gate will read it.
    process, _blocking = lib.decide(results, iterations_remaining=(iter_ < max_iter))

    # Feedback fed back to the agent: only iterate-severity failures, since the
    # agent cannot fix advisory/block facts by re-running. Defaulting on_fail to
    # "iterate" keeps the single-agent regression path byte-identical (all v1
    # checks are iterate-severity, so this is every non-pass verdict).
    fb_parts = [r.get("feedback", "") for r in results
                if not r.get("pass", False) and r.get("on_fail", "iterate") == "iterate"]
    fb = "; ".join(p for p in fb_parts if p)
    if not fb and len(results) == 0:
        fb = "no check verdicts produced (checks job failure?)"
```

- [ ] **Step 3: Rewrite the three decision branches to switch on `process`**

Still in `advance.py`, the transition block currently reads `if all_pass:` (line 274), `elif iter_ < max_iter:` (line 293), and `else:` (line 320). Change only those three branch conditions (leave every line inside each branch exactly as-is):

- Line 274: `if all_pass:` → `if process == "done":`
- Line 293: `elif iter_ < max_iter:` → `elif process == "iterate":`
- Line 320: `else:` → `else:  # process == "failed"`

- [ ] **Step 4: Update the module docstring**

In `advance.py`, the docstring's first sentence (lines 2-3) reads:

```python
"""advance.py <state_workdir> <instance-key> <protocol.json> <verdicts.json> <evidence.json>
The ONLY writer of non-initial state. Reads check verdicts (never agent files,
```

Append one sentence to the end of that line 3 clause, so it reads:

```python
"""advance.py <state_workdir> <instance-key> <protocol.json> <verdicts.json> <evidence.json>
The ONLY writer of non-initial state. The iterate/done/failed decision is the
pure lib.decide() fold over verdict severities. Reads check verdicts (never agent
files,
```

(Keep the rest of the docstring unchanged.)

- [ ] **Step 5: Run the regression anchors to confirm byte-identical behavior**

Run: `pytest tests/test_engine.py tests/test_fanout_e2e.py tests/test_publish.py -q`
Expected: PASS — identical results to Step 1. If any advance test fails, the mapping is not byte-identical; STOP and reconcile `decide()`/feedback against the failing assertion before proceeding.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/advance.py
git commit -m "refactor(engine): advance decides via lib.decide() (severity-aware)

Behavior byte-identical for grumpy/multi-grumpy (all checks default to
on_fail=iterate). blocking output is computed but unused until the M2 gate.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Full-suite verification + milestone close-out

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `pytest tests/ -q`
Expected: PASS — the prior count (154) plus the 10 new `test_decide.py` tests plus the 2 new `test_runchecks.py` tests = **166 passed**.

- [ ] **Step 2: Confirm no stray debug/diff**

Run: `git status` and `git diff --stat origin/main`
Expected: only `lib.py`, `run-checks.py`, `advance.py`, `CLAUDE.md`, `tests/test_decide.py`, `tests/test_runchecks.py` changed; three commits ahead of `origin/main`.

- [ ] **Step 3: (Optional) push for review**

Only if the user asks. Otherwise leave the three commits local on `main`.

---

## Self-Review (completed by plan author)

**1. Spec coverage (M1 portion):**
- `on_fail` severities (`iterate`/`advisory`/`block`) → Task 2. ✓
- `run-checks.py` stamps `on_fail` onto verdicts → Task 2. ✓
- `lib.decide(results, iterations_remaining) → (process, blocking)`, pure → Task 1. ✓
- `advance.py` swaps `all_pass` ladder for `decide()`; feedback filtered to `iterate`-severity → Task 3. ✓
- Regression byte-identical (`test_engine.py` et al. unchanged) → Task 3 Steps 1/5, Task 4. ✓
- **Conclude/publish seam:** spec lists under M1; **deferred to the M2 plan** (no consumer yet) — called out in the header scope note. Not a dropped requirement; it moves to where it is consumed.

**2. Placeholder scan:** No "TBD"/"add error handling"/"write tests for the above". Task 2's tests were verified against the real `test_runchecks.py` symbols — `run_checks(...)`, `GRUMPY_PROTO`, `EV_COMPLETE`, `DIFF_PR1`, `FILES_PR1`, and the `temp_proto_in_grumpy` fixture all exist in that module (confirmed by reading lines 86-186).

**3. Type/signature consistency:** `decide(results, iterations_remaining) → (process, blocking)` is defined identically in Task 1 (impl + tests) and called identically in Task 3 (`lib.decide(results, iterations_remaining=(iter_ < max_iter))`). `fail_verdict(name, feedback, on_fail="iterate")` is defined once (Task 2 Step 3) and every call site updated in the same task. Verdict key `on_fail` is the same string in run-checks (stamp), decide (read), and advance (feedback filter).
