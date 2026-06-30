# tests/test_judge_coverage.py
# Revision 3 — lightened judge evidence shape (scope + gather_verdict, no gather copy).
# TDD: tests written BEFORE the implementation rewrite.
import base64, json, os, stat, sys, subprocess
from conftest import PROTOCOLS
CHECK = PROTOCOLS / "code-review/checks/judge-coverage.py"


# ─── gh PATH stub helpers ──────────────────────────────────────────────────────

def _gh(tmp_path, spec="S MUST x.", plan="do x."):
    """Minimal gh stub that returns base64-encoded spec/plan content."""
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True)
    sb = base64.b64encode(spec.encode()).decode()
    pb = base64.b64encode(plan.encode()).decode()
    (bindir / "gh").write_text(f"""#!/usr/bin/env python3
import sys
j = " ".join(sys.argv[1:])
if "contents/" in j and "spec" in j: sys.stdout.write({sb!r}); sys.exit(0)
if "contents/" in j and "plan" in j: sys.stdout.write({pb!r}); sys.exit(0)
sys.exit(1)
""")
    (bindir / "gh").chmod(0o755)
    return bindir


def _run(ev_obj, changed, tmp_path, params, pr_body=""):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(ev_obj))
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("\n".join(changed) + "\n")
    env = dict(os.environ)
    env["PATH"] = f"{_gh(tmp_path)}{os.pathsep}" + env["PATH"]
    env["PR_BODY"] = pr_body
    env["GITHUB_REPOSITORY"] = "o/r"
    env["PR"] = "1"
    env["CHECK_PARAMS"] = json.dumps(params)
    r = subprocess.run([sys.executable, str(CHECK), str(ev), str(diff), str(files)],
                       text=True, capture_output=True, env=env)
    return json.loads(r.stdout)


# ─── coherence mode (docs / tests) ────────────────────────────────────────────

def _docs_judge(graded, scope=None, gather_verdict="inadequate"):
    """Minimal well-formed docs judge evidence (lightened shape)."""
    return {
        "leg": "docs-updated-appropriately",
        "scope": scope if scope is not None else {"code_changed": True},
        "gather_verdict": gather_verdict,
        "graded_findings": graded,
        "examined": ["docs/a.md"],
    }


def test_docs_judge_correct_passes(tmp_path):
    """Correct lightened docs judge evidence passes."""
    ev = _docs_judge([{"ref": "docs/a.md", "severity": "blocking", "rationale": "missing"}])
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "docs-updated-appropriately", "mode": "coherence"})
    assert r["pass"] is True


def test_docs_judge_bad_severity_fails(tmp_path):
    """Bad severity value fails."""
    ev = _docs_judge([{"ref": "docs/a.md", "severity": "critical", "rationale": "x"}])
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "docs-updated-appropriately", "mode": "coherence"})
    assert r["pass"] is False


def test_docs_judge_scope_mismatch_fails(tmp_path):
    """Scope echo mismatch fails (agent claims code_changed:False but file is code)."""
    ev = _docs_judge([], scope={"code_changed": False})  # wrong: src/x.py is code
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "docs-updated-appropriately", "mode": "coherence"})
    assert r["pass"] is False and "scope" in r["feedback"].lower()


def test_docs_judge_bad_verdict_enum_fails(tmp_path):
    """gather_verdict not in the coherence enum fails."""
    ev = _docs_judge([], gather_verdict="solves")  # not in {adequate,inadequate,n/a}
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "docs-updated-appropriately", "mode": "coherence"})
    assert r["pass"] is False


def test_docs_judge_verdict_inconsistency_fails(tmp_path):
    """docs has no n/a; if code_changed scope says False (impossible for docs) is irrelevant —
    but gather_verdict 'n/a' is not allowed for docs (always applicable). Fails."""
    ev = _docs_judge([], gather_verdict="n/a")  # docs is never n/a
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "docs-updated-appropriately", "mode": "coherence"})
    assert r["pass"] is False


def test_tests_judge_no_code_gives_na_passes(tmp_path):
    """Tests leg with no code_changed → gather_verdict must be n/a."""
    ev = {
        "leg": "tests-updated-appropriately",
        "scope": {"code_changed": False},
        "gather_verdict": "n/a",
        "graded_findings": [],
        "examined": ["tests/"],
    }
    # changed files: only a doc (not code)
    r = _run(ev, ["README.md"], tmp_path, {"leg": "tests-updated-appropriately", "mode": "coherence"})
    assert r["pass"] is True


def test_tests_judge_no_code_non_na_verdict_fails(tmp_path):
    """Tests leg: !code_changed ⇒ gather_verdict must be 'n/a'. scope-inconsistent verdict fails."""
    ev = {
        "leg": "tests-updated-appropriately",
        "scope": {"code_changed": False},
        "gather_verdict": "adequate",  # wrong: should be n/a when no code
        "graded_findings": [],
        "examined": ["tests/"],
    }
    r = _run(ev, ["README.md"], tmp_path, {"leg": "tests-updated-appropriately", "mode": "coherence"})
    assert r["pass"] is False


# ─── mm mode ──────────────────────────────────────────────────────────────────

def test_mm_compliant_no_scope_passes(tmp_path):
    """mm mode: scope={} (no scope), gather_verdict in enum, no graded_findings OK."""
    ev = {
        "leg": "mm-compliance",
        "scope": {},
        "gather_verdict": "compliant",
        "graded_findings": [],
        "examined": ["mm"],
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "mm-compliance", "mode": "mm"})
    assert r["pass"] is True


def test_mm_diverges_with_findings_passes(tmp_path):
    """mm mode: diverges verdict with graded findings passes."""
    ev = {
        "leg": "mm-compliance",
        "scope": {},
        "gather_verdict": "diverges",
        "graded_findings": [{"ref": "divergence-0", "severity": "blocking", "rationale": "real divergence"}],
        "examined": ["mm"],
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "mm-compliance", "mode": "mm"})
    assert r["pass"] is True


def test_mm_bad_verdict_enum_fails(tmp_path):
    """mm gather_verdict not in {compliant, diverges} fails."""
    ev = {
        "leg": "mm-compliance",
        "scope": {},
        "gather_verdict": "n/a",  # not in mm enum
        "graded_findings": [],
        "examined": ["mm"],
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "mm-compliance", "mode": "mm"})
    assert r["pass"] is False


def test_mm_bad_severity_fails(tmp_path):
    """mm mode: bad severity in graded_findings fails."""
    ev = {
        "leg": "mm-compliance",
        "scope": {},
        "gather_verdict": "diverges",
        "graded_findings": [{"ref": "d0", "severity": "urgent", "rationale": "x"}],
        "examined": ["mm"],
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "mm-compliance", "mode": "mm"})
    assert r["pass"] is False


# ─── spec-solves mode ─────────────────────────────────────────────────────────

def test_spec_solves_no_issue_na_passes(tmp_path):
    """spec-solves: no linked issue → gather_verdict must be n/a."""
    ev = {
        "leg": "spec-solves-issue",
        "scope": {"issue_linked": False, "spec_present": False},
        "gather_verdict": "n/a",
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    # PR_BODY has no 'Closes #N' → issue_linked=False
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "spec-solves-issue", "mode": "spec-solves"})
    assert r["pass"] is True


def test_spec_solves_no_issue_but_verdict_not_na_fails(tmp_path):
    """spec-solves: !issue_linked but gather_verdict != n/a — the exact live failure."""
    ev = {
        "leg": "spec-solves-issue",
        "scope": {"issue_linked": False, "spec_present": False},
        "gather_verdict": "solves",  # wrong: must be n/a when no issue
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "spec-solves-issue", "mode": "spec-solves"})
    assert r["pass"] is False


def test_spec_solves_scope_mismatch_fails(tmp_path):
    """spec-solves: scope says issue_linked=True but PR body has no closing keyword → fail."""
    ev = {
        "leg": "spec-solves-issue",
        "scope": {"issue_linked": True, "spec_present": False},  # mismatch
        "gather_verdict": "solves",
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    # PR_BODY has no Closes keyword → recompute issue_linked=False
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "spec-solves-issue", "mode": "spec-solves"})
    assert r["pass"] is False and "scope" in r["feedback"].lower()


def test_spec_solves_bad_verdict_enum_fails(tmp_path):
    """spec-solves gather_verdict not in {solves, does-not-solve, n/a} fails."""
    ev = {
        "leg": "spec-solves-issue",
        "scope": {"issue_linked": False, "spec_present": False},
        "gather_verdict": "adheres",  # wrong enum
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "spec-solves-issue", "mode": "spec-solves"})
    assert r["pass"] is False


def test_spec_solves_with_issue_solves_passes(tmp_path):
    """spec-solves: linked issue (Closes #5 in body) → scope recomputed correctly, verdict solves OK."""
    ev = {
        "leg": "spec-solves-issue",
        "scope": {"issue_linked": True, "spec_present": False},
        "gather_verdict": "solves",
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    pr_body = "Closes #5\nSome description."
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "spec-solves-issue", "mode": "spec-solves"},
             pr_body=pr_body)
    assert r["pass"] is True


# ─── plan-spec mode ───────────────────────────────────────────────────────────

def test_plan_spec_no_code_na_passes(tmp_path):
    """plan-spec: no code changed → gather_verdict must be n/a."""
    ev = {
        "leg": "plan-implements-spec",
        "scope": {"spec_present": False, "plan_present": False, "code_changed": False},
        "gather_verdict": "n/a",
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    r = _run(ev, ["README.md"], tmp_path, {"leg": "plan-implements-spec", "mode": "plan-spec"})
    assert r["pass"] is True


def test_plan_spec_no_code_non_na_fails(tmp_path):
    """plan-spec: !code_changed ⇒ gather_verdict must be n/a. scope-inconsistent verdict fails."""
    ev = {
        "leg": "plan-implements-spec",
        "scope": {"spec_present": False, "plan_present": False, "code_changed": False},
        "gather_verdict": "adheres",  # wrong: must be n/a
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    r = _run(ev, ["README.md"], tmp_path, {"leg": "plan-implements-spec", "mode": "plan-spec"})
    assert r["pass"] is False


def test_plan_spec_scope_code_mismatch_fails(tmp_path):
    """plan-spec: scope says code_changed=False but changed files include code → scope mismatch."""
    ev = {
        "leg": "plan-implements-spec",
        "scope": {"spec_present": False, "plan_present": False, "code_changed": False},  # wrong
        "gather_verdict": "n/a",
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    # src/x.py is code
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "plan-implements-spec", "mode": "plan-spec"})
    assert r["pass"] is False and "scope" in r["feedback"].lower()


def test_plan_spec_bad_verdict_enum_fails(tmp_path):
    """plan-spec gather_verdict not in enum fails."""
    ev = {
        "leg": "plan-implements-spec",
        "scope": {"spec_present": False, "plan_present": False, "code_changed": False},
        "gather_verdict": "solves",  # wrong enum for plan-spec
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    r = _run(ev, ["README.md"], tmp_path, {"leg": "plan-implements-spec", "mode": "plan-spec"})
    assert r["pass"] is False


def test_plan_spec_with_code_adheres_passes(tmp_path):
    """plan-spec: code_changed=True, adheres verdict passes."""
    ev = {
        "leg": "plan-implements-spec",
        "scope": {"spec_present": True, "plan_present": True, "code_changed": True},
        "gather_verdict": "adheres",
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    changed = ["docs/superpowers/specs/s.md", "docs/superpowers/plans/p.md", "src/x.py"]
    r = _run(ev, changed, tmp_path, {"leg": "plan-implements-spec", "mode": "plan-spec"})
    assert r["pass"] is True


# ─── code-plan mode ───────────────────────────────────────────────────────────

def test_code_plan_no_code_na_passes(tmp_path):
    """code-plan: no code changed → gather_verdict must be n/a."""
    ev = {
        "leg": "code-implements-plan",
        "scope": {"plan_present": False, "code_changed": False},
        "gather_verdict": "n/a",
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    r = _run(ev, ["README.md"], tmp_path, {"leg": "code-implements-plan", "mode": "code-plan"})
    assert r["pass"] is True


def test_code_plan_no_code_non_na_fails(tmp_path):
    """code-plan: !code_changed ⇒ gather_verdict must be n/a. Inconsistent verdict fails."""
    ev = {
        "leg": "code-implements-plan",
        "scope": {"plan_present": False, "code_changed": False},
        "gather_verdict": "adheres",  # wrong
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    r = _run(ev, ["README.md"], tmp_path, {"leg": "code-implements-plan", "mode": "code-plan"})
    assert r["pass"] is False


def test_code_plan_scope_mismatch_fails(tmp_path):
    """code-plan: scope says code_changed=False but file is code → scope mismatch."""
    ev = {
        "leg": "code-implements-plan",
        "scope": {"plan_present": False, "code_changed": False},  # wrong
        "gather_verdict": "n/a",
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "code-implements-plan", "mode": "code-plan"})
    assert r["pass"] is False and "scope" in r["feedback"].lower()


def test_code_plan_bad_verdict_enum_fails(tmp_path):
    """code-plan gather_verdict not in {adheres, underplan, overplan, n/a} fails."""
    ev = {
        "leg": "code-implements-plan",
        "scope": {"plan_present": False, "code_changed": False},
        "gather_verdict": "solves",  # wrong enum
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    r = _run(ev, ["README.md"], tmp_path, {"leg": "code-implements-plan", "mode": "code-plan"})
    assert r["pass"] is False


def test_code_plan_with_code_adheres_passes(tmp_path):
    """code-plan: code_changed=True, plan_present=True, adheres verdict passes."""
    ev = {
        "leg": "code-implements-plan",
        "scope": {"plan_present": True, "code_changed": True},
        "gather_verdict": "adheres",
        "graded_findings": [],
        "examined": ["pr-body"],
    }
    changed = ["docs/superpowers/plans/p.md", "src/x.py"]
    r = _run(ev, changed, tmp_path, {"leg": "code-implements-plan", "mode": "code-plan"})
    assert r["pass"] is True


# ─── security mode ────────────────────────────────────────────────────────────

def test_security_no_scope_pass_verdict_passes(tmp_path):
    """security: scope={}, gather_verdict=PASS, no findings → passes."""
    ev = {
        "leg": "security",
        "scope": {},
        "gather_verdict": "PASS",
        "graded_findings": [],
        "examined": ["cedar-policy"],
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "security", "mode": "security"})
    assert r["pass"] is True


def test_security_locked_violation_passes(tmp_path):
    """security: LOCKED_VIOLATION verdict with graded findings passes."""
    ev = {
        "leg": "security",
        "scope": {},
        "gather_verdict": "LOCKED_VIOLATION",
        "graded_findings": [{"ref": "v0", "severity": "blocking", "rationale": "locked rule"}],
        "examined": ["cedar-policy"],
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "security", "mode": "security"})
    assert r["pass"] is True


def test_security_bad_verdict_enum_fails(tmp_path):
    """security gather_verdict not in {PASS, LOCKED_VIOLATION, n/a} fails."""
    ev = {
        "leg": "security",
        "scope": {},
        "gather_verdict": "compliant",  # wrong enum for security
        "graded_findings": [],
        "examined": ["cedar-policy"],
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "security", "mode": "security"})
    assert r["pass"] is False


def test_security_bad_severity_fails(tmp_path):
    """security: bad severity in graded_findings fails."""
    ev = {
        "leg": "security",
        "scope": {},
        "gather_verdict": "PASS",
        "graded_findings": [{"ref": "v0", "severity": "urgent", "rationale": "x"}],
        "examined": ["cedar-policy"],
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "security", "mode": "security"})
    assert r["pass"] is False


# ─── missing required fields ──────────────────────────────────────────────────

def test_missing_check_params_fails(tmp_path):
    """No CHECK_PARAMS fails."""
    ev = tmp_path / "ev.json"; ev.write_text("{}")
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("")
    env = dict(os.environ)
    env.pop("CHECK_PARAMS", None)
    r = subprocess.run([sys.executable, str(CHECK), str(ev), str(diff), str(files)],
                       text=True, capture_output=True, env=env)
    out = json.loads(r.stdout)
    assert out["pass"] is False


def test_empty_examined_fails(tmp_path):
    """examined must be non-empty list."""
    ev = {
        "leg": "mm-compliance",
        "scope": {},
        "gather_verdict": "compliant",
        "graded_findings": [],
        "examined": [],  # empty!
    }
    r = _run(ev, ["src/x.py"], tmp_path, {"leg": "mm-compliance", "mode": "mm"})
    assert r["pass"] is False
