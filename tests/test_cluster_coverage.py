import json
from pathlib import Path
from conftest import PROTOCOLS, run_check

CHECK = PROTOCOLS / "code-review/checks/cluster-coverage.py"
LEGS = {"legs": ["adherence", "consistency"]}


def _run(ev_obj, tmp_path, params=LEGS):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(ev_obj))
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("")
    return run_check(CHECK, ev, diff, files, check_params=params)


def _cell(leg, scope=None, gather_verdict=None, graded_findings=None):
    return {
        "leg": leg,
        "scope": scope if scope is not None else {"k": "v"},
        "gather_verdict": gather_verdict if gather_verdict is not None else "n/a",
        "graded_findings": graded_findings if graded_findings is not None else [],
    }


def test_one_cell_per_leg_passes(tmp_path):
    ev = {"cluster": "adherence", "legs": [_cell("adherence"), _cell("consistency")]}
    assert _run(ev, tmp_path)["pass"] is True


def test_missing_leg_fails(tmp_path):
    ev = {"cluster": "adherence", "legs": [_cell("adherence")]}
    r = _run(ev, tmp_path)
    assert r["pass"] is False and "consistency" in r["feedback"]


def test_duplicate_leg_fails(tmp_path):
    ev = {"cluster": "adherence", "legs": [_cell("adherence"), _cell("adherence"), _cell("consistency")]}
    r = _run(ev, tmp_path)
    assert r["pass"] is False and "adherence" in r["feedback"]


def test_unexpected_leg_fails(tmp_path):
    ev = {"cluster": "adherence", "legs": [_cell("adherence"), _cell("consistency"), _cell("bogus-leg")]}
    r = _run(ev, tmp_path)
    assert r["pass"] is False and "bogus-leg" in r["feedback"]


# Option 2: the rollup only forwards grades; a cell needs just a non-empty `leg`.
# scope / gather_verdict are no longer required (conclude reads them from gathers).

def test_cell_without_scope_passes(tmp_path):
    bare = {"leg": "consistency"}  # grades-only cell, no scope/gather_verdict/grades
    ev = {"cluster": "adherence", "legs": [_cell("adherence"), bare]}
    assert _run(ev, tmp_path)["pass"] is True


def test_cell_without_gather_verdict_passes(tmp_path):
    bare = {"leg": "consistency", "graded_findings": []}
    ev = {"cluster": "adherence", "legs": [_cell("adherence"), bare]}
    assert _run(ev, tmp_path)["pass"] is True


def test_cell_graded_findings_not_a_list_fails(tmp_path):
    bad = {"leg": "consistency", "graded_findings": {"ref": "x"}}  # not a list
    ev = {"cluster": "adherence", "legs": [_cell("adherence"), bad]}
    r = _run(ev, tmp_path)
    assert r["pass"] is False and "consistency" in r["feedback"]


def test_no_params_fails(tmp_path):
    ev = {"cluster": "adherence", "legs": []}
    r = _run(ev, tmp_path, params="")
    assert r["pass"] is False and "legs" in r["feedback"]


def test_no_legs_key_in_evidence_fails(tmp_path):
    # Evidence with NO `legs` key — check must reject it
    ev = {"cluster": "adherence"}
    r = _run(ev, tmp_path)
    assert r["pass"] is False
    assert "legs" in r["feedback"].lower() or "array" in r["feedback"].lower()


def test_empty_legs_in_evidence_fails(tmp_path):
    # Evidence.legs is present but empty — missing both declared legs
    ev = {"cluster": "adherence", "legs": []}
    r = _run(ev, tmp_path)
    assert r["pass"] is False
