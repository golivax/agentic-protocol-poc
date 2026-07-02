# tests/test_judge_coverage.py
# Option 2 — grades-only judge contract. judge-coverage validates ONLY the grade
# form; it no longer rejects on a missing/empty/mismatched scope, gather_verdict,
# or examined echo. conclude-preflight reads scope+verdict from the gather
# evidence, so the judge's job is purely to grade findings.
import json
import os
import subprocess
import sys

from conftest import PROTOCOLS

CHECK = PROTOCOLS / "code-review/checks/judge-coverage.py"


def _run(ev_obj, tmp_path, params):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(ev_obj))
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("")
    env = dict(os.environ)
    env["CHECK_PARAMS"] = json.dumps(params) if params != "" else ""
    r = subprocess.run([sys.executable, str(CHECK), str(ev), str(diff), str(files)],
                       text=True, capture_output=True, env=env)
    return json.loads(r.stdout)


# ── well-formed grades pass ──────────────────────────────────────────────────

def test_well_formed_grades_pass(tmp_path):
    ev = {"graded_findings": [{"ref": "docs/a.md", "severity": "blocking", "rationale": "missing"}]}
    assert _run(ev, tmp_path, {"leg": "docs-updated-appropriately", "mode": "coherence"})["pass"] is True


def test_empty_grades_pass(tmp_path):
    assert _run({"graded_findings": []}, tmp_path, {"leg": "security", "mode": "security"})["pass"] is True


def test_absent_grades_pass(tmp_path):
    """A judge that grades nothing (no graded_findings key) is treated as empty → pass."""
    assert _run({}, tmp_path, {"leg": "mm-compliance", "mode": "mm"})["pass"] is True


# ── grade FORM is still enforced ─────────────────────────────────────────────

def test_bad_severity_fails(tmp_path):
    ev = {"graded_findings": [{"ref": "x", "severity": "critical", "rationale": "y"}]}
    assert _run(ev, tmp_path, {"leg": "security", "mode": "security"})["pass"] is False


def test_missing_ref_fails(tmp_path):
    ev = {"graded_findings": [{"severity": "blocking", "rationale": "y"}]}
    assert _run(ev, tmp_path, {"leg": "security", "mode": "security"})["pass"] is False


def test_graded_findings_not_a_list_fails(tmp_path):
    ev = {"graded_findings": {"ref": "x", "severity": "noise"}}
    assert _run(ev, tmp_path, {"leg": "mm-compliance", "mode": "mm"})["pass"] is False


def test_missing_check_params_fails(tmp_path):
    assert _run({"graded_findings": []}, tmp_path, "")["pass"] is False


# ── the EXACT live exhaustion shapes now PASS (regression) ───────────────────
# These are the evidences that exhausted the chain/coherence judges at iteration
# 2 under the old R3 contract — null gather_verdict, empty examined, mismatched
# scope. With Option 2 they are no longer the judge's concern.

def test_live_null_gather_verdict_now_passes(tmp_path):
    """iter1 live failure: judge emitted gather_verdict=null. No longer rejected."""
    ev = {"leg": "spec-solves-issue", "gather_verdict": None,
          "scope": {}, "examined": [], "graded_findings": []}
    assert _run(ev, tmp_path, {"leg": "spec-solves-issue", "mode": "spec-solves"})["pass"] is True


def test_live_empty_examined_now_passes(tmp_path):
    """iter2 live failure (spec-solves): examined was empty. No longer rejected."""
    ev = {"leg": "spec-solves-issue", "gather_verdict": "n/a",
          "scope": {"issue_linked": False, "spec_present": False},
          "examined": [], "graded_findings": []}
    assert _run(ev, tmp_path, {"leg": "spec-solves-issue", "mode": "spec-solves"})["pass"] is True


def test_live_scope_mismatch_now_passes(tmp_path):
    """iter2 live failure (docs): scope.code_changed echoed False vs recompute True.
    No longer rejected — scope echo is not load-bearing."""
    ev = {"leg": "docs-updated-appropriately", "gather_verdict": "adequate",
          "scope": {"code_changed": False}, "examined": [],
          "graded_findings": [{"ref": "docs/x.md", "severity": "advisory", "rationale": "minor"}]}
    assert _run(ev, tmp_path, {"leg": "docs-updated-appropriately", "mode": "coherence"})["pass"] is True


def test_missing_scope_object_now_passes(tmp_path):
    """A judge that omits scope entirely passes (scope no longer required)."""
    ev = {"leg": "mm-compliance", "graded_findings": [{"ref": "d0", "severity": "blocking", "rationale": "div"}]}
    assert _run(ev, tmp_path, {"leg": "mm-compliance", "mode": "mm"})["pass"] is True
