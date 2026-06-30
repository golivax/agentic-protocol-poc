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

def _security_leg(verdict):
    """Build a security gather evidence dict (flat schema at root, no scope nesting)."""
    return {"verdict": verdict,
            "engine_report": {"violations": (
                [{"id": "V1", "locked": verdict == "LOCKED_VIOLATION", "description": "test"}]
                if verdict != "PASS" and verdict != "n/a" else []
            )},
            "examined": ["diff"]}


def _j(obj, grades=None):
    """Wrap a raw leg object in the lightened judge evidence shape: {scope, gather_verdict, graded_findings}.

    Extracts 'scope' (default {}) and 'verdict' (becomes gather_verdict) from obj.
    """
    return {"scope": obj.get("scope") if isinstance(obj.get("scope"), dict) else {},
            "gather_verdict": obj.get("verdict", "n/a") if isinstance(obj.get("verdict"), str) else "n/a",
            "graded_findings": grades or []}


def _cluster(cluster_name, legs_dict):
    """Build a cluster rollup evidence: {cluster, legs:[{leg, scope, gather_verdict, graded_findings}]}.

    legs_dict: {leg_id: judge_leg_obj}  where judge_leg_obj is already in _j() shape
               (i.e. has 'scope', 'gather_verdict', + 'graded_findings' keys).
    """
    legs = []
    for leg_id, leg_obj in legs_dict.items():
        entry = {"leg": leg_id,
                 "scope": leg_obj.get("scope", {}),
                 "gather_verdict": leg_obj.get("gather_verdict", "n/a"),
                 "graded_findings": leg_obj.get("graded_findings", [])}
        legs.append(entry)
    return {"cluster": cluster_name, "legs": legs}


def _conclude(legs_or_clusters, blocking, tmp_path):
    """Write 4 cluster branch output files and invoke conclude-preflight.

    Accepts either:
      - A dict of the 4 cluster branch files keyed by file name
        ('adherence', 'mm-compliance', 'consistency', 'security'), OR
      - A dict of per-leg judge evidences (the old flat format, for backward
        compat with the existing parametrized CASES tests) — in which case the
        helper automatically groups them into the 4 cluster files.

    The 4 branch files written:
      adherence.json    - cluster rollup for spec/plan/code legs
      mm-compliance.json - single mm judge evidence (in _j() shape)
      consistency.json  - cluster rollup for docs/tests legs
      security.json     - single security evidence (flat or _j() shape)
    """
    inputs = tmp_path / "inputs"
    if not inputs.exists():
        inputs.mkdir()

    ADHERENCE_LEGS = ("spec-solves-issue", "plan-implements-spec", "code-implements-plan")
    CONSISTENCY_LEGS = ("docs-updated-appropriately", "tests-updated-appropriately")
    # A cluster-keyed dict has exactly these top-level keys (and only these).
    CLUSTER_FILE_KEYS = {"adherence", "mm-compliance", "consistency", "security"}
    # Per-leg dicts always include at least one of these adherence or consistency leg ids.
    PER_LEG_MARKER_KEYS = set(ADHERENCE_LEGS) | set(CONSISTENCY_LEGS)

    # Detect if caller passed cluster-keyed dict or per-leg dict.
    # If any per-leg marker key is present, treat as per-leg flat format.
    if legs_or_clusters.keys().isdisjoint(PER_LEG_MARKER_KEYS):
        # Already cluster-keyed — write directly.
        branch_files = legs_or_clusters
    else:
        # Per-leg flat dict — group into 4 cluster files automatically.
        per_leg = legs_or_clusters
        adherence_legs_dict = {k: per_leg[k] for k in ADHERENCE_LEGS if k in per_leg}
        consistency_legs_dict = {k: per_leg[k] for k in CONSISTENCY_LEGS if k in per_leg}
        branch_files = {
            "adherence": _cluster("adherence", adherence_legs_dict),
            "mm-compliance": per_leg.get("mm-compliance", _j(_mm_leg("compliant"))),
            "consistency": _cluster("consistency", consistency_legs_dict),
            "security": per_leg.get("security", _j(_security_leg("PASS"))),
        }

    for name, obj in branch_files.items():
        (inputs / f"{name}.json").write_text(json.dumps(obj))

    # argv[1] evidence = the gate's consolidated render (display only); a minimal stub is fine.
    gate_ev = tmp_path / "gate.json"
    gate_ev.write_text(json.dumps({"legs": [], "examined": []}))
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


# Baseline: the 3 chain legs N/A + mm-compliance compliant + docs adequate + tests n/a
# + security PASS => clear.
def _all_na():
    return {"spec-solves-issue": _j(_spec_leg("n/a", issue_linked=False, spec_present=False)),
            "plan-implements-spec": _j(_plan_leg("n/a", code_changed=False, spec_present=False, plan_present=False)),
            "code-implements-plan": _j(_code_leg("n/a", code_changed=False, plan_present=False)),
            "mm-compliance": _j(_mm_leg("compliant")),
            "docs-updated-appropriately": _j(_docs_leg("adequate", code_changed=False)),
            "tests-updated-appropriately": _j(_tests_leg("n/a", code_changed=False)),
            "security": _j(_security_leg("PASS"))}


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
        "security": _j(_security_leg("PASS")),
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


# --- Cluster-architecture tests (Revision 2) ---

def _make_cluster_inputs(legs_per_leg_dict):
    """Convert a per-leg dict into 4 branch cluster files for direct cluster-path testing."""
    ADHERENCE_LEGS = ("spec-solves-issue", "plan-implements-spec", "code-implements-plan")
    CONSISTENCY_LEGS = ("docs-updated-appropriately", "tests-updated-appropriately")
    adherence_dict = {k: legs_per_leg_dict[k] for k in ADHERENCE_LEGS if k in legs_per_leg_dict}
    consistency_dict = {k: legs_per_leg_dict[k] for k in CONSISTENCY_LEGS if k in legs_per_leg_dict}
    return {
        "adherence": _cluster("adherence", adherence_dict),
        "mm-compliance": legs_per_leg_dict.get("mm-compliance", _j(_mm_leg("compliant"))),
        "consistency": _cluster("consistency", consistency_dict),
        "security": legs_per_leg_dict.get("security", _j(_security_leg("PASS"))),
    }


def test_cluster_all_clear_no_block(tmp_path):
    """Via direct cluster-file path: all-clear input => not blocked."""
    cluster_files = _make_cluster_inputs(_all_clear())
    out, _v, _s = _conclude(cluster_files, False, tmp_path)
    assert out["blocked"] is False


def test_cluster_nine_floors_via_cluster_path(tmp_path):
    """All nine original floors still block when sourced through cluster rollup files."""
    floor_cases = [
        # 1. issue_linked & !spec_present
        ("issue-no-spec",
         lambda L: L | {"spec-solves-issue": _j(_spec_leg("n/a", issue_linked=True, spec_present=False))},
         "spec"),
        # 2. spec.verdict == does-not-solve
        ("does-not-solve",
         lambda L: L | {"spec-solves-issue": _j(_spec_leg("does-not-solve", issue_linked=True, spec_present=True))},
         "solve"),
        # 3. code_changed & !spec_present
        ("code-no-spec",
         lambda L: L | {"plan-implements-spec": _j(_plan_leg("n/a", code_changed=True, spec_present=False, plan_present=True))},
         "spec"),
        # 4. code_changed & !plan_present
        ("code-no-plan",
         lambda L: L | {"plan-implements-spec": _j(_plan_leg("n/a", code_changed=True, spec_present=True, plan_present=False))},
         "plan"),
        # 5. plan.verdict == underspec
        ("underspec",
         lambda L: L | {"plan-implements-spec": _j(_plan_leg("underspec", code_changed=True, spec_present=True, plan_present=True))},
         "underspec"),
        # 6. code.verdict == underplan
        ("underplan",
         lambda L: L | {"code-implements-plan": _j(_code_leg("underplan", code_changed=True, plan_present=True)),
                        "plan-implements-spec": _j(_plan_leg("adheres", code_changed=True, spec_present=True, plan_present=True))},
         "underplan"),
        # 7. mm.verdict == diverges
        ("mm-diverges",
         lambda L: L | {"mm-compliance": _j(_mm_leg("diverges"))},
         "mental model"),
        # 8. docs.verdict == inadequate
        ("docs-inadequate",
         lambda L: L | {"docs-updated-appropriately": _j(_docs_leg("inadequate"))},
         "docs"),
        # 9. code_changed & tests.verdict == inadequate
        ("tests-inadequate",
         lambda L: L | {"tests-updated-appropriately": _j(_tests_leg("inadequate")),
                        "plan-implements-spec": _j(_plan_leg("adheres", code_changed=True, spec_present=True, plan_present=True)),
                        "code-implements-plan": _j(_code_leg("adheres", code_changed=True, plan_present=True)),
                        "spec-solves-issue": _j(_spec_leg("n/a", issue_linked=False, spec_present=True))},
         "tests"),
    ]
    for case_name, mutate, reason_substr in floor_cases:
        per_leg = mutate(_all_na())
        cluster_files = _make_cluster_inputs(per_leg)
        case_dir = tmp_path / case_name
        case_dir.mkdir(exist_ok=True)
        out, _v, _s = _conclude(cluster_files, False, case_dir)
        assert out["blocked"] is True, f"{case_name}: expected block"
        assert any(reason_substr in r for r in out["reasons"]), \
            f"{case_name}: expected '{reason_substr}' in reasons: {out['reasons']}"


def test_security_locked_violation_blocks(tmp_path):
    """Security floor: LOCKED_VIOLATION in security gather.verdict => block."""
    legs = _all_clear()
    legs["security"] = _j(_security_leg("LOCKED_VIOLATION"))
    out, _v, _s = _conclude(legs, False, tmp_path)
    assert out["blocked"] is True
    assert any("LOCKED_VIOLATION" in r for r in out["reasons"]), out["reasons"]


def test_security_pass_does_not_block(tmp_path):
    """Security verdict PASS => no block from security."""
    out, _v, _s = _conclude(_all_clear(), False, tmp_path)
    assert out["blocked"] is False
    assert not any("LOCKED" in r for r in out["reasons"])


def test_security_judge_escalates_non_locked(tmp_path):
    """A non-LOCKED security violation with a blocking judge grade => escalation block."""
    legs = _all_clear()
    # verdict is not LOCKED_VIOLATION (so no security floor), but judge grades blocking.
    legs["security"] = _j(
        _security_leg("PASS"),  # gather verdict PASS — not a LOCKED floor
        [{"ref": "V1", "severity": "blocking", "rationale": "novel critical vuln"}])
    out, _v, _s = _conclude(legs, False, tmp_path)
    assert out["blocked"] is True
    assert any("security" in r for r in out["reasons"]), out["reasons"]


def test_missing_adherence_cluster_blocks(tmp_path):
    """Missing adherence.json => all 3 adherence legs are fail-safe blocked."""
    cluster_files = _make_cluster_inputs(_all_clear())
    del cluster_files["adherence"]
    out, _v, _s = _conclude(cluster_files, False, tmp_path)
    assert out["blocked"] is True
    # All three adherence legs should appear as fail-safe blocks.
    reasons_str = " ".join(out["reasons"])
    assert "spec-solves-issue" in reasons_str or "plan-implements-spec" in reasons_str or \
           "code-implements-plan" in reasons_str, out["reasons"]


def test_missing_consistency_cluster_blocks(tmp_path):
    """Missing consistency.json => docs + tests legs are fail-safe blocked."""
    cluster_files = _make_cluster_inputs(_all_clear())
    del cluster_files["consistency"]
    out, _v, _s = _conclude(cluster_files, False, tmp_path)
    assert out["blocked"] is True
    reasons_str = " ".join(out["reasons"])
    assert "docs-updated-appropriately" in reasons_str or \
           "tests-updated-appropriately" in reasons_str, out["reasons"]


def test_missing_inner_leg_in_cluster_blocks(tmp_path):
    """A cluster rollup that omits an inner leg => fail-safe block for that leg."""
    # Build a consistency cluster that only has tests, not docs.
    consistency_missing_docs = _cluster("consistency", {
        "tests-updated-appropriately": _j(_tests_leg("adequate"))
        # docs-updated-appropriately deliberately omitted
    })
    cluster_files = _make_cluster_inputs(_all_clear())
    cluster_files["consistency"] = consistency_missing_docs
    out, _v, _s = _conclude(cluster_files, False, tmp_path)
    assert out["blocked"] is True
    assert any("docs-updated-appropriately" in r for r in out["reasons"]), out["reasons"]


def test_garbled_cluster_file_blocks(tmp_path):
    """A cluster file that is not a valid cluster object => fail-safe block."""
    cluster_files = _make_cluster_inputs(_all_clear())
    # Overwrite adherence with a non-dict/non-cluster value (still valid JSON).
    cluster_files["adherence"] = [1, 2, 3]  # a list, not a dict
    out, _v, _s = _conclude(cluster_files, False, tmp_path)
    assert out["blocked"] is True


def test_cluster_grouped_comment_contains_headings(tmp_path):
    """The PR comment groups rows under the 4 cluster headings."""
    _out, _v, stderr = _conclude(_all_clear(), False, tmp_path)
    # ENGINE_LOCAL writes the comment body to stderr.
    assert "Adherence" in stderr or "adherence" in stderr.lower(), stderr
    assert "Mental-model" in stderr or "mm-compliance" in stderr.lower() or \
           "mental" in stderr.lower(), stderr
    assert "Consistency" in stderr or "consistency" in stderr.lower(), stderr
    assert "Security" in stderr or "security" in stderr.lower(), stderr


def test_verdict_json_contains_security_leg(tmp_path):
    """verdict.json records must include the security leg."""
    _out, vpath, _s = _conclude(_all_clear(), False, tmp_path)
    v = json.loads(vpath.read_text())
    assert any(r.get("type") == "leg" and r.get("leg") == "security" for r in v["records"]), \
        v["records"]
