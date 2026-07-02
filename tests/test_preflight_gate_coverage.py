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


def test_no_legs_key_in_evidence_fails(tmp_path):
    # Evidence with NO `legs` key at all — check must reject it with a feedback
    # mentioning legs/array (the key is absent, so ev.get("legs") returns None).
    ev = {"examined": []}
    r = _run(ev, tmp_path)
    assert r["pass"] is False
    assert "legs" in r["feedback"].lower() or "array" in r["feedback"].lower()


def test_four_legs_with_scopeless_mm_cell_passes(tmp_path):
    # Phase B: the gate declares 4 legs (params.legs); the mm-compliance cell carries
    # scope:{} (mm evidence has no scope object). The check must accept the empty dict.
    four = {"legs": ["spec-solves-issue", "plan-implements-spec", "code-implements-plan", "mm-compliance"]}
    ev = {"legs": [_cell("spec-solves-issue"), _cell("plan-implements-spec", "adheres"),
                   _cell("code-implements-plan", "adheres"),
                   {"leg": "mm-compliance", "verdict": "compliant", "scope": {}, "summary": "ok"}],
          "examined": ["x"]}
    assert _run(ev, tmp_path, params=four)["pass"] is True


# R2-9: gate with 7 leaf legs (4-cluster-branch inputs flattened)
SEVEN_LEGS = {"legs": [
    "spec-solves-issue",
    "plan-implements-spec",
    "code-implements-plan",
    "mm-compliance",
    "docs-updated-appropriately",
    "tests-updated-appropriately",
    "security",
]}


def _cell7(leg, verdict="adheres", scope=None):
    if scope is None:
        scope = {"spec_present": True}
    return {"leg": leg, "verdict": verdict, "scope": scope, "summary": "ok"}


def test_seven_cell_gate_passes(tmp_path):
    """A 7-cell gate evidence (one per leaf leg, each with leg+verdict+scope) passes."""
    ev = {
        "legs": [
            _cell7("spec-solves-issue",           "solves"),
            _cell7("plan-implements-spec",         "adheres"),
            _cell7("code-implements-plan",         "adheres"),
            _cell7("mm-compliance",                "compliant", scope={}),
            _cell7("docs-updated-appropriately",   "adheres"),
            _cell7("tests-updated-appropriately",  "adheres"),
            _cell7("security",                     "clear",    scope={}),
        ],
        "examined": [],
    }
    r = _run(ev, tmp_path, params=SEVEN_LEGS)
    assert r["pass"] is True


def test_seven_cell_security_scope_empty_dict_passes(tmp_path):
    """security scope:{} (no scope from gather) is accepted by the check."""
    ev = {
        "legs": [
            _cell7("spec-solves-issue",           "solves"),
            _cell7("plan-implements-spec",         "adheres"),
            _cell7("code-implements-plan",         "adheres"),
            _cell7("mm-compliance",                "compliant", scope={}),
            _cell7("docs-updated-appropriately",   "adheres"),
            _cell7("tests-updated-appropriately",  "adheres"),
            # security scope is explicitly {}
            {"leg": "security", "verdict": "clear", "scope": {}, "summary": "no violations"},
        ],
        "examined": [],
    }
    r = _run(ev, tmp_path, params=SEVEN_LEGS)
    assert r["pass"] is True


def test_seven_cell_missing_security_leg_fails(tmp_path):
    """A 7-declared-leg gate evidence missing the security cell must fail."""
    ev = {
        "legs": [
            _cell7("spec-solves-issue",           "solves"),
            _cell7("plan-implements-spec",         "adheres"),
            _cell7("code-implements-plan",         "adheres"),
            _cell7("mm-compliance",                "compliant", scope={}),
            _cell7("docs-updated-appropriately",   "adheres"),
            _cell7("tests-updated-appropriately",  "adheres"),
            # security leg intentionally omitted
        ],
        "examined": [],
    }
    r = _run(ev, tmp_path, params=SEVEN_LEGS)
    assert r["pass"] is False
    assert "security" in r["feedback"]


def test_seven_cell_missing_one_cluster_leaf_fails(tmp_path):
    """Missing any cluster-derived leaf cell (e.g. plan-implements-spec) must fail."""
    ev = {
        "legs": [
            _cell7("spec-solves-issue",           "solves"),
            # plan-implements-spec intentionally omitted
            _cell7("code-implements-plan",         "adheres"),
            _cell7("mm-compliance",                "compliant", scope={}),
            _cell7("docs-updated-appropriately",   "adheres"),
            _cell7("tests-updated-appropriately",  "adheres"),
            _cell7("security",                     "clear",    scope={}),
        ],
        "examined": [],
    }
    r = _run(ev, tmp_path, params=SEVEN_LEGS)
    assert r["pass"] is False
    assert "plan-implements-spec" in r["feedback"]
