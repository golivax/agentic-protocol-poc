import json
from pathlib import Path
from conftest import PROTOCOLS, run_check

CHECK = PROTOCOLS / "code-review/checks/preflight-gate-coverage.py"
LEGS = {"legs": ["spec-solves-issue", "plan-implements-spec", "code-implements-plan"]}


def _run(ev_obj, tmp_path, params=LEGS):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(ev_obj))
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("")
    return run_check(CHECK, ev, diff, files, check_params=params)


def _cell(leg, verdict="solves"):
    return {"leg": leg, "verdict": verdict, "scope": {"spec_present": True}, "summary": "ok"}


def test_one_cell_per_leg_passes(tmp_path):
    ev = {"legs": [_cell("spec-solves-issue"), _cell("plan-implements-spec", "adheres"),
                   _cell("code-implements-plan", "adheres")], "examined": ["x"]}
    assert _run(ev, tmp_path)["pass"] is True


def test_missing_leg_fails(tmp_path):
    ev = {"legs": [_cell("spec-solves-issue"), _cell("plan-implements-spec", "adheres")], "examined": ["x"]}
    r = _run(ev, tmp_path)
    assert r["pass"] is False and "code-implements-plan" in r["feedback"]


def test_duplicate_leg_fails(tmp_path):
    ev = {"legs": [_cell("spec-solves-issue"), _cell("spec-solves-issue"),
                   _cell("plan-implements-spec", "adheres"), _cell("code-implements-plan", "adheres")],
          "examined": ["x"]}
    r = _run(ev, tmp_path)
    assert r["pass"] is False and "spec-solves-issue" in r["feedback"]


def test_unexpected_leg_fails(tmp_path):
    ev = {"legs": [_cell("spec-solves-issue"), _cell("plan-implements-spec", "adheres"),
                   _cell("code-implements-plan", "adheres"), _cell("bogus-leg")], "examined": ["x"]}
    r = _run(ev, tmp_path)
    assert r["pass"] is False and "bogus-leg" in r["feedback"]


def test_malformed_cell_missing_verdict_fails(tmp_path):
    bad = {"leg": "code-implements-plan", "scope": {}, "summary": "x"}  # no verdict
    ev = {"legs": [_cell("spec-solves-issue"), _cell("plan-implements-spec", "adheres"), bad], "examined": ["x"]}
    r = _run(ev, tmp_path)
    assert r["pass"] is False and "code-implements-plan" in r["feedback"]


def test_no_params_fails(tmp_path):
    ev = {"legs": [], "examined": []}
    r = _run(ev, tmp_path, params="")
    assert r["pass"] is False and "legs" in r["feedback"]
