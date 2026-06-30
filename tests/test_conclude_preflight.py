import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/code-review/publish/conclude-preflight.py"
LIVE_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "preflight-live-pr7"

PID, INSTANCE = "code-review", "pr-7"

# Import the hook as a module to reuse its single source of truth for the gather
# evidence path — tests place fixtures at exactly the path the hook reads.
_spec = importlib.util.spec_from_file_location("conclude_preflight", HOOK)
conclude_preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conclude_preflight)


# ── Gather-evidence builders — exact field names the leg gather schemas/checks produce.
# Under Option 2 conclude reads scope+verdict from these GATHER files (not any echo).
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
    # mm-compliance gather has NO scope object (verdict compliant|diverges + divergences[] + examined).
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

def _security_leg(verdict):
    return {"verdict": verdict,
            "engine_report": {"violations": (
                [{"id": "V1", "locked": verdict == "LOCKED_VIOLATION", "description": "test"}]
                if verdict not in ("PASS", "n/a") else []
            )},
            "examined": ["diff"]}


ADHERENCE_LEGS = ("spec-solves-issue", "plan-implements-spec", "code-implements-plan")
CONSISTENCY_LEGS = ("docs-updated-appropriately", "tests-updated-appropriately")


def _write_gathers(state_dir, per_leg, omit=()):
    """Write each leg's gather evidence at the deterministic path conclude reads.
    A leg in `omit` is NOT written (=> conclude finds no gather => fail-safe block)."""
    for leg, obj in per_leg.items():
        if leg in omit:
            continue
        p = conclude_preflight.gather_evidence_path(str(state_dir), PID, INSTANCE, leg)
        assert p, f"no gather path for {leg}"
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_text(json.dumps(obj))


def _cluster_inputs(inputs_dir, per_leg, grades):
    """Write the 4 cluster branch outputs — read ONLY for per-leg graded_findings."""
    def _cluster(name, leg_ids):
        return {"cluster": name,
                "legs": [{"leg": l, "graded_findings": grades.get(l, [])}
                         for l in leg_ids if l in per_leg]}
    branch = {
        "adherence": _cluster("adherence", ADHERENCE_LEGS),
        "mm-compliance": {"leg": "mm-compliance", "graded_findings": grades.get("mm-compliance", [])},
        "consistency": _cluster("consistency", CONSISTENCY_LEGS),
        "security": {"leg": "security", "graded_findings": grades.get("security", [])},
    }
    for name, obj in branch.items():
        (inputs_dir / f"{name}.json").write_text(json.dumps(obj))


def _conclude(per_leg, blocking, tmp_path, grades=None, omit_gather=(), garble_gather=()):
    """Write gather evidence (scope+verdict) + cluster outputs (grades), then run the hook.

    per_leg     : {leg_id: gather_obj}
    grades      : {leg_id: [graded_finding, ...]}  (escalation grades, optional)
    omit_gather : leg ids whose gather file to skip (fail-safe: missing evidence)
    garble_gather: leg ids whose gather file to write as verdict-less junk (fail-safe)
    """
    grades = grades or {}
    state_dir = tmp_path / "state"
    _write_gathers(state_dir, per_leg, omit=omit_gather)
    for leg in garble_gather:
        p = conclude_preflight.gather_evidence_path(str(state_dir), PID, INSTANCE, leg)
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_text(json.dumps({"no_verdict": True}))

    inputs = tmp_path / "inputs"
    inputs.mkdir(exist_ok=True)
    _cluster_inputs(inputs, per_leg, grades)

    gate_ev = tmp_path / "gate.json"
    gate_ev.write_text(json.dumps({"legs": [], "examined": []}))
    env = dict(os.environ)
    env["BLOCKING"] = "1" if blocking else "0"
    env["CONCLUDE_STATE_DIR"] = str(state_dir)
    env["CONCLUDE_INPUTS_DIR"] = str(inputs)
    env["VERDICT_OUT"] = str(tmp_path / "verdict.json")
    env["ENGINE_LOCAL"] = "1"   # short-circuit the PR comment to stderr
    env["PR"] = "7"
    env["GITHUB_REPOSITORY"] = "o/r"
    r = subprocess.run(["python3", str(HOOK), str(gate_ev), "pr-7"],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout), (tmp_path / "verdict.json"), r.stderr


# Baseline: 3 chain legs N/A + mm compliant + docs adequate + tests n/a + security PASS => clear.
def _all_na():
    return {"spec-solves-issue": _spec_leg("n/a", issue_linked=False, spec_present=False),
            "plan-implements-spec": _plan_leg("n/a", code_changed=False, spec_present=False, plan_present=False),
            "code-implements-plan": _code_leg("n/a", code_changed=False, plan_present=False),
            "mm-compliance": _mm_leg("compliant"),
            "docs-updated-appropriately": _docs_leg("adequate", code_changed=False),
            "tests-updated-appropriately": _tests_leg("n/a", code_changed=False),
            "security": _security_leg("PASS")}


def _all_clear():
    return {"spec-solves-issue": _spec_leg("n/a", issue_linked=False, spec_present=False),
            "plan-implements-spec": _plan_leg("adheres", code_changed=True, spec_present=True, plan_present=True),
            "code-implements-plan": _code_leg("adheres", code_changed=True, plan_present=True),
            "mm-compliance": _mm_leg("compliant"),
            "docs-updated-appropriately": _docs_leg("adequate"),
            "tests-updated-appropriately": _tests_leg("adequate"),
            "security": _security_leg("PASS")}


CASES = [
    # (name, mutate, expect_blocked, reason_substr, warning_substr)
    ("no-issue-no-code-clear", lambda L: L, False, None, None),
    ("issue-no-spec-block",
     lambda L: L | {"spec-solves-issue": _spec_leg("n/a", issue_linked=True, spec_present=False)},
     True, "spec", None),
    ("solves-clear",
     lambda L: L | {"spec-solves-issue": _spec_leg("solves", issue_linked=True, spec_present=True)},
     False, None, None),
    ("does-not-solve-block",
     lambda L: L | {"spec-solves-issue": _spec_leg("does-not-solve", issue_linked=True, spec_present=True)},
     True, "solve", None),
    ("code-no-spec-block",
     lambda L: L | {"plan-implements-spec": _plan_leg("n/a", code_changed=True, spec_present=False, plan_present=True)},
     True, "spec", None),
    ("code-no-plan-block",
     lambda L: L | {"plan-implements-spec": _plan_leg("n/a", code_changed=True, spec_present=True, plan_present=False)},
     True, "plan", None),
    ("underspec-block",
     lambda L: L | {"plan-implements-spec": _plan_leg("underspec", code_changed=True, spec_present=True, plan_present=True)},
     True, "underspec", None),
    ("overspec-warn",
     lambda L: L | {"plan-implements-spec": _plan_leg("overspec", code_changed=True, spec_present=True, plan_present=True),
                    "code-implements-plan": _code_leg("adheres", code_changed=True, plan_present=True)},
     False, None, "overspec"),
    ("underplan-block",
     lambda L: L | {"code-implements-plan": _code_leg("underplan", code_changed=True, plan_present=True),
                    "plan-implements-spec": _plan_leg("adheres", code_changed=True, spec_present=True, plan_present=True)},
     True, "underplan", None),
    ("overplan-warn",
     lambda L: L | {"code-implements-plan": _code_leg("overplan", code_changed=True, plan_present=True),
                    "plan-implements-spec": _plan_leg("adheres", code_changed=True, spec_present=True, plan_present=True)},
     False, None, "overplan"),
    ("mm-compliant-clear",
     lambda L: L | {"mm-compliance": _mm_leg("compliant")},
     False, None, None),
    ("mm-diverges-block",
     lambda L: L | {"mm-compliance": _mm_leg("diverges")},
     True, "mental model", None),
    ("docs-inadequate-block",
     lambda L: L | {"docs-updated-appropriately": _docs_leg("inadequate")},
     True, "docs", None),
    ("docs-adequate-clear",
     lambda L: L | {"docs-updated-appropriately": _docs_leg("adequate")},
     False, None, None),
    ("tests-inadequate-with-code-block",
     lambda L: L | {"tests-updated-appropriately": _tests_leg("inadequate"),
                    "plan-implements-spec": _plan_leg("adheres", code_changed=True, spec_present=True, plan_present=True),
                    "code-implements-plan": _code_leg("adheres", code_changed=True, plan_present=True),
                    "spec-solves-issue": _spec_leg("n/a", issue_linked=False, spec_present=True)},
     True, "tests", None),
    ("tests-inadequate-no-code-clear",
     lambda L: L | {"tests-updated-appropriately": _tests_leg("inadequate", code_changed=False)},
     False, None, None),
]


@pytest.mark.parametrize("name,mutate,blocked,reason,warning", CASES, ids=[c[0] for c in CASES])
def test_preflight_floors_from_gather(name, mutate, blocked, reason, warning, tmp_path):
    """The 9 floors + 2 warnings, sourced from each leg's GATHER evidence."""
    legs = mutate(_all_na())
    out, vpath, _ = _conclude(legs, blocking=False, tmp_path=tmp_path)
    assert out["blocked"] is blocked, out
    assert out["conclusion"] == ("blocked" if blocked else "clear")
    if reason:
        assert any(reason in r for r in out["reasons"]), out["reasons"]
    if warning:
        assert any(warning in w for w in out["warnings"]), out["warnings"]
    assert vpath.exists()


def test_all_clear_no_block(tmp_path):
    out, _v, _s = _conclude(_all_clear(), False, tmp_path)
    assert out["blocked"] is False


def test_engine_blocking_forces_block(tmp_path):
    out, _v, stderr = _conclude(_all_na(), blocking=True, tmp_path=tmp_path)
    assert out["blocked"] is True
    assert any("engine blocking signal" in r for r in out["reasons"])
    assert stderr.count("[ENGINE_LOCAL] pr comment") == 1, stderr


def test_verdict_json_shape(tmp_path):
    _out, vpath, _s = _conclude(_all_na(), blocking=False, tmp_path=tmp_path)
    v = json.loads(vpath.read_text())
    assert "records" in v and isinstance(v["records"], list)
    assert any(r.get("type") == "verdict" for r in v["records"])
    assert any(r.get("type") == "leg" and r.get("leg") == "mm-compliance" for r in v["records"])


def test_verdict_json_contains_security_leg(tmp_path):
    _out, vpath, _s = _conclude(_all_clear(), False, tmp_path)
    v = json.loads(vpath.read_text())
    assert any(r.get("type") == "leg" and r.get("leg") == "security" for r in v["records"]), v["records"]


def test_posts_one_comment_engine_local(tmp_path):
    _out, _v, stderr = _conclude(_all_na(), blocking=False, tmp_path=tmp_path)
    assert stderr.count("[ENGINE_LOCAL] pr comment") == 1, stderr


def test_grouped_comment_contains_headings(tmp_path):
    _out, _v, stderr = _conclude(_all_clear(), False, tmp_path)
    assert "Adherence" in stderr or "adherence" in stderr.lower(), stderr
    assert "Mental-model" in stderr or "mental" in stderr.lower() or "mm-compliance" in stderr.lower(), stderr
    assert "Consistency" in stderr or "consistency" in stderr.lower(), stderr
    assert "Security" in stderr or "security" in stderr.lower(), stderr


# ── Floor vs escalation (grades come from the cluster outputs; floor from gather) ──

def test_floor_underspec_blocks_even_if_judge_all_noise(tmp_path):
    legs = _all_clear()
    legs["plan-implements-spec"] = _plan_leg("underspec", code_changed=True, spec_present=True, plan_present=True)
    out, _v, _s = _conclude(legs, False, tmp_path,
                            grades={"plan-implements-spec": [{"ref": "R1", "severity": "noise", "rationale": "x"}]})
    assert out["blocked"] is True and any("underspec" in r for r in out["reasons"])  # floor held


def test_judge_escalates_clean_leg(tmp_path):
    legs = _all_clear()  # code leg verdict 'adheres' (no floor)
    out, _v, _s = _conclude(legs, False, tmp_path,
                            grades={"code-implements-plan": [{"ref": "F1", "severity": "blocking", "rationale": "real bug"}]})
    assert out["blocked"] is True and any("code-implements-plan" in r for r in out["reasons"])


def test_security_locked_violation_blocks(tmp_path):
    legs = _all_clear()
    legs["security"] = _security_leg("LOCKED_VIOLATION")
    out, _v, _s = _conclude(legs, False, tmp_path)
    assert out["blocked"] is True
    assert any("LOCKED_VIOLATION" in r for r in out["reasons"]), out["reasons"]


def test_security_pass_does_not_block(tmp_path):
    out, _v, _s = _conclude(_all_clear(), False, tmp_path)
    assert out["blocked"] is False
    assert not any("LOCKED" in r for r in out["reasons"])


def test_security_judge_escalates_non_locked(tmp_path):
    legs = _all_clear()  # security verdict PASS — no LOCKED floor
    out, _v, _s = _conclude(legs, False, tmp_path,
                            grades={"security": [{"ref": "V1", "severity": "blocking", "rationale": "novel critical vuln"}]})
    assert out["blocked"] is True
    assert any("security" in r for r in out["reasons"]), out["reasons"]


# ── Fail-safe: a leg with no trustworthy GATHER evidence blocks ──

def test_missing_one_gather_fail_safe_blocks(tmp_path):
    out, _v, _s = _conclude(_all_clear(), False, tmp_path, omit_gather={"docs-updated-appropriately"})
    assert out["blocked"] is True
    assert any("docs-updated-appropriately" in r for r in out["reasons"]), out["reasons"]


def test_missing_adherence_gathers_block(tmp_path):
    out, _v, _s = _conclude(_all_clear(), False, tmp_path, omit_gather=set(ADHERENCE_LEGS))
    assert out["blocked"] is True
    reasons = " ".join(out["reasons"])
    assert all(leg in reasons for leg in ADHERENCE_LEGS), out["reasons"]


def test_garbled_gather_fail_safe_blocks(tmp_path):
    out, _v, _s = _conclude(_all_clear(), False, tmp_path, garble_gather={"security"})
    assert out["blocked"] is True
    assert any("security" in r for r in out["reasons"]), out["reasons"]


def test_missing_state_dir_blocks_all(tmp_path):
    """No CONCLUDE_STATE_DIR at all => no leg verifiable => fail-safe block."""
    inputs = tmp_path / "inputs"; inputs.mkdir()
    _cluster_inputs(inputs, _all_clear(), {})
    gate_ev = tmp_path / "gate.json"; gate_ev.write_text("{}")
    env = dict(os.environ)
    env["BLOCKING"] = "0"
    env.pop("CONCLUDE_STATE_DIR", None)
    env["CONCLUDE_INPUTS_DIR"] = str(inputs)
    env["VERDICT_OUT"] = str(tmp_path / "v.json")
    env["ENGINE_LOCAL"] = "1"; env["PR"] = "7"; env["GITHUB_REPOSITORY"] = "o/r"
    r = subprocess.run(["python3", str(HOOK), str(gate_ev), "pr-7"], text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["blocked"] is True


# ── Live replay: the exact gather facts captured from SiRumCz PR #7 ──

def test_live_pr7_replay_blocks_with_real_reasons(tmp_path):
    """Regression seeded from the real failed run: the 7 captured gather evidences
    must conclude BLOCKED with the structural reasons (code w/o spec & plan,
    underspec, underplan, docs inadequate) — proving conclude derives the correct
    decision straight from gather facts, no LLM echo involved."""
    if not LIVE_FIXTURES.is_dir():
        pytest.skip("live fixtures not captured")
    per_leg = {}
    for f in LIVE_FIXTURES.glob("*.gather.json"):
        per_leg[f.name[:-len(".gather.json")]] = json.loads(f.read_text())
    assert len(per_leg) == 7, sorted(per_leg)
    out, _v, _s = _conclude(per_leg, False, tmp_path)
    assert out["blocked"] is True
    blob = " ".join(out["reasons"])
    assert "spec" in blob and "plan" in blob          # code changed w/o committed spec/plan
    assert any("underspec" in r for r in out["reasons"]), out["reasons"]
    assert any("underplan" in r for r in out["reasons"]), out["reasons"]
    assert any("docs" in r for r in out["reasons"]), out["reasons"]
    # mm compliant + security PASS + tests adequate => those legs add no reason
    assert not any("mental model" in r for r in out["reasons"])
    assert not any("LOCKED" in r for r in out["reasons"])
