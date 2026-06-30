"""Tests for security-gather-coverage.py form-check.

The check re-derives the verdict from engine_report.violations and asserts
it matches evidence.verdict. It does NOT run the real Cedar/Guardians engines —
all tests supply synthetic engine_reports.

Verdict rule:
  LOCKED_VIOLATION  iff engine_report.violations has any entry with locked:true
  n/a               if neither engine could run (no violations field at all, or
                    engines-absent signal)
  PASS              otherwise (violations is present but none have locked:true)
"""
import json
from pathlib import Path
from conftest import PROTOCOLS, run_check

CHECK = PROTOCOLS / "code-review/checks/security-gather-coverage.py"


def _run(ev_obj, tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps(ev_obj))
    diff = tmp_path / "d.txt"
    diff.write_text("")
    files = tmp_path / "f.txt"
    files.write_text("")
    return run_check(CHECK, ev, diff, files)


def _base_evidence(verdict, violations=None, *, engines_absent=False):
    """Build a minimal valid security-gather evidence object."""
    if engines_absent:
        engine_report = {"_engines_absent": True}
    elif violations is None:
        engine_report = {"violations": [], "summary": {}}
    else:
        engine_report = {"violations": violations, "summary": {}}

    return {
        "scope": {},
        "cedar": {"status": "ok", "flags": []},
        "guardians": {"ok": True, "violations": [], "warnings": []},
        "engine_report": engine_report,
        "verdict": verdict,
        "examined": ["policy/cedar/default"],
    }


# ---------------------------------------------------------------------------
# Happy-path: verdict matches the recomputed value
# ---------------------------------------------------------------------------


def test_locked_violation_detected(tmp_path):
    """A locked:true violation => verdict must be LOCKED_VIOLATION."""
    violations = [{"id": "cedar-1", "locked": True, "rule": "no-exfil", "detail": "data leaked"}]
    ev = _base_evidence("LOCKED_VIOLATION", violations=violations)
    r = _run(ev, tmp_path)
    assert r["pass"] is True, r["feedback"]


def test_pass_clean_report(tmp_path):
    """No locked violations => verdict PASS."""
    violations = [{"id": "cedar-2", "locked": False, "rule": "advisory-only"}]
    ev = _base_evidence("PASS", violations=violations)
    r = _run(ev, tmp_path)
    assert r["pass"] is True, r["feedback"]


def test_pass_empty_violations(tmp_path):
    """Empty violations list => verdict PASS."""
    ev = _base_evidence("PASS", violations=[])
    r = _run(ev, tmp_path)
    assert r["pass"] is True, r["feedback"]


def test_na_engines_absent(tmp_path):
    """Engines-absent sentinel => verdict n/a (fail-open, never silent PASS)."""
    ev = _base_evidence("n/a", engines_absent=True)
    r = _run(ev, tmp_path)
    assert r["pass"] is True, r["feedback"]


# ---------------------------------------------------------------------------
# Mismatch: evidence.verdict disagrees with recomputed verdict => fail
# ---------------------------------------------------------------------------


def test_verdict_mismatch_locked_but_says_pass(tmp_path):
    """locked:true violation but evidence.verdict = PASS => check must fail."""
    violations = [{"id": "cedar-1", "locked": True, "rule": "no-exfil"}]
    ev = _base_evidence("PASS", violations=violations)
    r = _run(ev, tmp_path)
    assert r["pass"] is False
    assert "LOCKED_VIOLATION" in r["feedback"] or "mismatch" in r["feedback"].lower()


def test_verdict_mismatch_clean_but_says_locked(tmp_path):
    """Clean report but evidence.verdict = LOCKED_VIOLATION => check must fail."""
    ev = _base_evidence("LOCKED_VIOLATION", violations=[])
    r = _run(ev, tmp_path)
    assert r["pass"] is False
    assert "PASS" in r["feedback"] or "mismatch" in r["feedback"].lower()


def test_verdict_mismatch_absent_but_says_pass(tmp_path):
    """Engines-absent report but evidence.verdict = PASS => must fail (not n/a)."""
    ev = _base_evidence("PASS", engines_absent=True)
    r = _run(ev, tmp_path)
    assert r["pass"] is False


# ---------------------------------------------------------------------------
# Required sub-object presence
# ---------------------------------------------------------------------------


def test_missing_cedar_fails(tmp_path):
    """cedar object must be present."""
    ev = _base_evidence("PASS", violations=[])
    del ev["cedar"]
    r = _run(ev, tmp_path)
    assert r["pass"] is False
    assert "cedar" in r["feedback"].lower()


def test_missing_guardians_fails(tmp_path):
    """guardians object must be present."""
    ev = _base_evidence("PASS", violations=[])
    del ev["guardians"]
    r = _run(ev, tmp_path)
    assert r["pass"] is False
    assert "guardians" in r["feedback"].lower()


def test_missing_engine_report_fails(tmp_path):
    """engine_report object must be present."""
    ev = _base_evidence("PASS", violations=[])
    del ev["engine_report"]
    r = _run(ev, tmp_path)
    assert r["pass"] is False
    assert "engine_report" in r["feedback"].lower()


def test_engine_report_not_object_fails(tmp_path):
    """engine_report must be an object, not a list or scalar."""
    ev = _base_evidence("PASS", violations=[])
    ev["engine_report"] = "oops"
    r = _run(ev, tmp_path)
    assert r["pass"] is False


# ---------------------------------------------------------------------------
# Verdict enum validation
# ---------------------------------------------------------------------------


def test_invalid_verdict_enum_fails(tmp_path):
    """verdict must be one of PASS|LOCKED_VIOLATION|n/a."""
    ev = _base_evidence("PASS", violations=[])
    ev["verdict"] = "REQUEST_CHANGES"  # wrong enum
    r = _run(ev, tmp_path)
    assert r["pass"] is False


# ---------------------------------------------------------------------------
# Malformed evidence
# ---------------------------------------------------------------------------


def test_evidence_not_object_fails(tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text("[]")
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("")
    r = run_check(CHECK, ev, diff, files)
    assert r["pass"] is False


def test_evidence_unreadable_fails(tmp_path):
    ev = tmp_path / "no-such-file.json"
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("")
    r = run_check(CHECK, ev, diff, files)
    assert r["pass"] is False
