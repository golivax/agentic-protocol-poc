import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/code-review/publish/conclude-preflight.py"

# Leg-evidence builders — exact field names the leg schemas/checks produce.
def _spec_leg(verdict, *, issue_linked, spec_present):
    return {"verdict": verdict,
            "scope": {"issue_linked": issue_linked, "spec_present": spec_present},
            "matrix": [], "examined": ["issue#1", "spec.md"]}

def _plan_leg(verdict, *, code_changed, spec_present, plan_present):
    return {"verdict": verdict,
            "scope": {"code_changed": code_changed, "spec_present": spec_present,
                      "plan_present": plan_present},
            "spec_to_plan": [], "plan_to_spec": [], "examined": ["spec.md", "plan.md"]}

def _code_leg(verdict, *, code_changed, plan_present):
    return {"verdict": verdict,
            "scope": {"code_changed": code_changed, "plan_present": plan_present},
            "plan_to_code": [], "files": [], "examined": ["plan.md", "diff"]}

def _mm_leg(verdict):
    # mm-compliance evidence has NO scope object (verdict compliant|diverges + divergences[] + examined).
    return {"verdict": verdict,
            "divergences": ([] if verdict == "compliant"
                            else [{"decision": "ADR-1", "detail": "contradicts X", "evidence": "f.py:1"}]),
            "examined": ["_mm/socratic/x.adoc", "f.py"]}

def _docs_leg(verdict, *, code_changed=True):
    return {"verdict": verdict, "scope": {"code_changed": code_changed},
            "items": ([] if verdict == "adequate"
                      else [{"path": "docs/guide.md", "status": "missing", "reason": "x"}]),
            "examined": ["docs/guide.md"]}

def _tests_leg(verdict, *, code_changed=True):
    return {"verdict": verdict, "scope": {"code_changed": code_changed},
            "items": ([] if verdict in ("adequate", "n/a")
                      else [{"path": "tests/test_app.py", "status": "missing", "reason": "x"}]),
            "examined": ["tests/test_app.py"]}


def _j(obj, grades=None):
    """Wrap a raw leg object in the judge evidence shape: {gather: obj, graded_findings: grades}."""
    return {"gather": obj, "graded_findings": grades or []}


def _conclude(legs, blocking, tmp_path):
    """legs = {'spec-solves-issue': obj, 'plan-implements-spec': obj, 'code-implements-plan': obj, 'mm-compliance': obj, 'docs-updated-appropriately': obj, 'tests-updated-appropriately': obj}."""
    inputs = tmp_path / "inputs"; inputs.mkdir()
    for name, obj in legs.items():
        (inputs / f"{name}.json").write_text(json.dumps(obj))
    # argv[1] evidence = the gate's consolidated render (display only); a minimal stub is fine.
    gate_ev = tmp_path / "gate.json"
    gate_ev.write_text(json.dumps({"legs": [{"leg": k} for k in legs],
                                   "examined": []}))
    env = dict(os.environ)
    env["BLOCKING"] = "1" if blocking else "0"
    env["CONCLUDE_INPUTS_DIR"] = str(inputs)
    env["VERDICT_OUT"] = str(tmp_path / "verdict.json")
    env["ENGINE_LOCAL"] = "1"   # short-circuit the PR comment to stderr
    env["PR"] = "7"
    env["GITHUB_REPOSITORY"] = "o/r"
    r = subprocess.run(["python3", str(HOOK), str(gate_ev), "pr-7"],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout), (tmp_path / "verdict.json"), r.stderr


# Baseline: the 3 chain legs N/A + mm-compliance compliant + docs adequate + tests n/a => clear.
def _all_na():
    return {"spec-solves-issue": _j(_spec_leg("n/a", issue_linked=False, spec_present=False)),
            "plan-implements-spec": _j(_plan_leg("n/a", code_changed=False, spec_present=False, plan_present=False)),
            "code-implements-plan": _j(_code_leg("n/a", code_changed=False, plan_present=False)),
            "mm-compliance": _j(_mm_leg("compliant")),
            "docs-updated-appropriately": _j(_docs_leg("adequate", code_changed=False)),
            "tests-updated-appropriately": _j(_tests_leg("n/a", code_changed=False))}


CASES = [
    # (name, mutate, expect_blocked, expect_reason_substr, expect_warning_substr)
    ("no-issue-no-code-clear", lambda L: L, False, None, None),
    ("issue-no-spec-block",
     lambda L: L | {"spec-solves-issue": _j(_spec_leg("n/a", issue_linked=True, spec_present=False))},
     True, "spec", None),
    ("solves-clear",
     lambda L: L | {"spec-solves-issue": _j(_spec_leg("solves", issue_linked=True, spec_present=True))},
     False, None, None),
    ("does-not-solve-block",
     lambda L: L | {"spec-solves-issue": _j(_spec_leg("does-not-solve", issue_linked=True, spec_present=True))},
     True, "solve", None),
    ("code-no-spec-block",
     lambda L: L | {"plan-implements-spec": _j(_plan_leg("n/a", code_changed=True, spec_present=False, plan_present=True))},
     True, "spec", None),
    ("code-no-plan-block",
     lambda L: L | {"plan-implements-spec": _j(_plan_leg("n/a", code_changed=True, spec_present=True, plan_present=False))},
     True, "plan", None),
    ("underspec-block",
     lambda L: L | {"plan-implements-spec": _j(_plan_leg("underspec", code_changed=True, spec_present=True, plan_present=True))},
     True, "underspec", None),
    ("overspec-warn",
     lambda L: L | {"plan-implements-spec": _j(_plan_leg("overspec", code_changed=True, spec_present=True, plan_present=True)),
                    "code-implements-plan": _j(_code_leg("adheres", code_changed=True, plan_present=True))},
     False, None, "overspec"),
    ("underplan-block",
     lambda L: L | {"code-implements-plan": _j(_code_leg("underplan", code_changed=True, plan_present=True)),
                    "plan-implements-spec": _j(_plan_leg("adheres", code_changed=True, spec_present=True, plan_present=True))},
     True, "underplan", None),
    ("overplan-warn",
     lambda L: L | {"code-implements-plan": _j(_code_leg("overplan", code_changed=True, plan_present=True)),
                    "plan-implements-spec": _j(_plan_leg("adheres", code_changed=True, spec_present=True, plan_present=True))},
     False, None, "overplan"),
    ("mm-compliant-clear",
     lambda L: L | {"mm-compliance": _j(_mm_leg("compliant"))},
     False, None, None),
    ("mm-diverges-block",
     lambda L: L | {"mm-compliance": _j(_mm_leg("diverges"))},
     True, "mental model", None),
    ("docs-inadequate-block",
     lambda L: L | {"docs-updated-appropriately": _j(_docs_leg("inadequate"))},
     True, "docs", None),
    ("docs-adequate-clear",
     lambda L: L | {"docs-updated-appropriately": _j(_docs_leg("adequate"))},
     False, None, None),
    ("tests-inadequate-with-code-block",
     lambda L: L | {"tests-updated-appropriately": _j(_tests_leg("inadequate")),
                    "plan-implements-spec": _j(_plan_leg("adheres", code_changed=True, spec_present=True, plan_present=True)),
                    "code-implements-plan": _j(_code_leg("adheres", code_changed=True, plan_present=True)),
                    "spec-solves-issue": _j(_spec_leg("n/a", issue_linked=False, spec_present=True))},
     True, "tests", None),
    ("tests-inadequate-no-code-clear",
     lambda L: L | {"tests-updated-appropriately": _j(_tests_leg("inadequate", code_changed=False))},
     False, None, None),
]


@pytest.mark.parametrize("name,mutate,blocked,reason,warning",
                         CASES, ids=[c[0] for c in CASES])
def test_preflight_rollup(name, mutate, blocked, reason, warning, tmp_path):
    legs = mutate(_all_na())
    out, vpath, _stderr = _conclude(legs, blocking=False, tmp_path=tmp_path)
    assert out["blocked"] is blocked, out
    assert out["conclusion"] == ("blocked" if blocked else "clear")
    if reason:
        assert any(reason in r for r in out["reasons"]), out["reasons"]
    if warning:
        assert any(warning in w for w in out["warnings"]), out["warnings"]
    assert vpath.exists()


def test_engine_blocking_forces_block(tmp_path):
    out, _v, stderr = _conclude(_all_na(), blocking=True, tmp_path=tmp_path)
    assert out["blocked"] is True
    # The engine-blocking signal must appear in the reported reasons.
    assert any("engine blocking signal" in r for r in out["reasons"])
    # Exactly one consolidated PR comment must be posted on a blocking run.
    assert stderr.count("[ENGINE_LOCAL] pr comment") == 1, stderr


def test_verdict_json_shape(tmp_path):
    _out, vpath, _s = _conclude(_all_na(), blocking=False, tmp_path=tmp_path)
    v = json.loads(vpath.read_text())
    assert "records" in v and isinstance(v["records"], list)
    assert any(r.get("type") == "verdict" for r in v["records"])
    assert any(r.get("type") == "leg" and r.get("leg") == "mm-compliance" for r in v["records"])


def test_posts_one_comment_engine_local(tmp_path):
    # ENGINE_LOCAL routes the single consolidated comment to stderr.
    _out, _v, stderr = _conclude(_all_na(), blocking=False, tmp_path=tmp_path)
    assert stderr.count("[ENGINE_LOCAL] pr comment") == 1, stderr


# --- New judge-aware tests ---

def _all_clear():
    return {
        "spec-solves-issue": _j(_spec_leg("n/a", issue_linked=False, spec_present=False)),
        "plan-implements-spec": _j(_plan_leg("adheres", code_changed=True, spec_present=True, plan_present=True)),
        "code-implements-plan": _j(_code_leg("adheres", code_changed=True, plan_present=True)),
        "mm-compliance": _j(_mm_leg("compliant")),
        "docs-updated-appropriately": _j(_docs_leg("adequate")),
        "tests-updated-appropriately": _j(_tests_leg("adequate")),
    }


def test_all_clear_no_block(tmp_path):
    out, _v, _s = _conclude(_all_clear(), False, tmp_path)
    assert out["blocked"] is False


def test_floor_underspec_blocks_even_if_judge_all_noise(tmp_path):
    legs = _all_clear()
    legs["plan-implements-spec"] = _j(
        _plan_leg("underspec", code_changed=True, spec_present=True, plan_present=True),
        [{"ref": "R1", "severity": "noise", "rationale": "x"}])
    out, _v, _s = _conclude(legs, False, tmp_path)
    assert out["blocked"] is True and any("underspec" in r for r in out["reasons"])  # floor held


def test_judge_escalates_clean_leg(tmp_path):
    legs = _all_clear()
    legs["code-implements-plan"] = _j(
        _code_leg("adheres", code_changed=True, plan_present=True),
        [{"ref": "F1", "severity": "blocking", "rationale": "real bug"}])
    out, _v, _s = _conclude(legs, False, tmp_path)
    assert out["blocked"] is True and any("code-implements-plan" in r for r in out["reasons"])


def test_missing_leg_fail_safe_blocks(tmp_path):
    legs = _all_clear()
    del legs["docs-updated-appropriately"]
    out, _v, _s = _conclude(legs, False, tmp_path)
    assert out["blocked"] is True
