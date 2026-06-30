import base64, json, os, stat, sys
from pathlib import Path
from conftest import PROTOCOLS, run_check

CHECK = PROTOCOLS / "code-review/checks/plan-spec-coverage.py"
SPEC_TEXT = "The system MUST validate the token.\nIt MUST log every denial."
PLAN_TEXT = "Add validate_token() to auth.py.\nAdd a denial logger."


def _gh(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True)
    spec_b64 = base64.b64encode(SPEC_TEXT.encode()).decode()
    plan_b64 = base64.b64encode(PLAN_TEXT.encode()).decode()
    script = f"""#!/usr/bin/env python3
import sys
j = " ".join(sys.argv[1:])
if "contents/" in j and "spec" in j: sys.stdout.write({spec_b64!r}); sys.exit(0)
if "contents/" in j and "plan" in j: sys.stdout.write({plan_b64!r}); sys.exit(0)
sys.exit(1)
"""
    gh = bindir / "gh"; gh.write_text(script)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def _run(ev_obj, changed, tmp_path, pr_body=""):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(ev_obj))
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("\n".join(changed) + "\n")
    env = dict(os.environ)
    env["PATH"] = f"{_gh(tmp_path)}{os.pathsep}" + env["PATH"]
    env["PR_BODY"] = pr_body
    env["GITHUB_REPOSITORY"] = "o/r"
    env.setdefault("PR", "1")
    # run_check forwards CHECK_PARAMS + inherits env; replicate its call with our env:
    import subprocess
    r = subprocess.run([sys.executable, str(CHECK), str(ev), str(diff), str(files)],
                       text=True, capture_output=True, env=env)
    return json.loads(r.stdout)


CHANGED = ["docs/superpowers/specs/s.md", "docs/superpowers/plans/p.md", "src/auth.py"]


def _adheres_ev():
    return {"scope": {"code_changed": True, "spec_present": True, "plan_present": True},
            "spec_to_plan": [{"requirement": "The system MUST validate the token.",
                              "status": "covered", "plan_quote": "Add validate_token() to auth.py."}],
            "plan_to_spec": [{"plan_item": "Add a denial logger.",
                              "status": "traces", "spec_quote": "It MUST log every denial."}],
            "verdict": "adheres", "examined": ["docs/superpowers/specs/s.md"]}


def test_adheres_passes(tmp_path):
    assert _run(_adheres_ev(), CHANGED, tmp_path)["pass"] is True


def test_fabricated_requirement_quote_fails(tmp_path):
    ev = _adheres_ev()
    ev["spec_to_plan"][0]["requirement"] = "The system MUST delete all data."  # not in spec text
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and "verbatim" in r["feedback"].lower()


def test_underspec_verdict_consistency(tmp_path):
    ev = _adheres_ev()
    ev["spec_to_plan"][0]["status"] = "missing"; ev["spec_to_plan"][0]["plan_quote"] = None
    ev["verdict"] = "adheres"  # WRONG: a missing requirement => verdict must be underspec
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and "underspec" in r["feedback"].lower()


def test_underspec_correct_passes(tmp_path):
    ev = _adheres_ev()
    ev["spec_to_plan"][0]["status"] = "missing"; ev["spec_to_plan"][0]["plan_quote"] = None
    ev["verdict"] = "underspec"
    assert _run(ev, CHANGED, tmp_path)["pass"] is True


def test_scope_disagreement_fails(tmp_path):
    # agent claims plan_present True, but no plan file in changed list => recompute disagrees
    ev = _adheres_ev()
    r = _run(ev, ["docs/superpowers/specs/s.md", "src/auth.py"], tmp_path)
    assert r["pass"] is False and "scope" in r["feedback"].lower()


def test_na_no_code_passes(tmp_path):
    ev = {"scope": {"code_changed": False, "spec_present": False, "plan_present": False},
          "spec_to_plan": [], "plan_to_spec": [], "verdict": "n/a", "examined": ["(no code)"]}
    assert _run(ev, ["README.md"], tmp_path)["pass"] is True


def test_na_but_code_changed_fails(tmp_path):
    # verdict n/a + empty matrices but code DID change => scope disagreement
    ev = {"scope": {"code_changed": False}, "spec_to_plan": [], "plan_to_spec": [],
          "verdict": "n/a", "examined": ["x"]}
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False


def test_fabricated_plan_item_in_plan_to_spec_fails(tmp_path):
    # Item 5: plan_to_spec cell's plan_item is NOT verbatim in the plan text.
    # Everything else is valid (spec_to_plan ok, verdict matches cells, scope ok).
    ev = _adheres_ev()
    ev["plan_to_spec"][0]["plan_item"] = "Completely fabricated plan step not in plan."  # not in PLAN_TEXT
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and "verbatim" in r["feedback"].lower()


def test_na_no_code_spec_present_passes(tmp_path):
    # Item 6: code_changed=False + spec_present=True (spec file IS in changed list)
    # + verdict n/a + empty matrices → verified N/A.
    # Pins: the check's "no code" early-exit treats out-of-scope as N/A regardless
    # of whether a spec file was committed in the same PR.
    ev = {"scope": {"code_changed": False, "spec_present": True, "plan_present": False},
          "spec_to_plan": [], "plan_to_spec": [], "verdict": "n/a",
          "examined": ["docs/superpowers/specs/s.md"]}
    # changed-files: spec file + doc-only; no code file => code_changed recomputes False
    r = _run(ev, ["README.md", "docs/superpowers/specs/s.md"], tmp_path)
    # The check passes as verified N/A (no code change; empty matrices).
    assert r["pass"] is True


def test_code_changed_no_spec_no_plan_passes(tmp_path):
    # Regression (live-found): a PR with code changes but NO committed spec/plan —
    # the common case for a normal repo. The agent correctly recomputes
    # spec_present/plan_present=False with empty matrices; the form-check MUST accept
    # it. Blocking on the missing spec/plan is conclude-preflight's job (it fires on
    # the code & !spec / code & !plan scope flags), NOT the form-check's. Before the
    # fix this emitted "in-scope leg must have non-empty …", making the leg
    # un-passable on any PR lacking docs/superpowers/specs|plans.
    ev = {"scope": {"code_changed": True, "spec_present": False, "plan_present": False},
          "spec_to_plan": [], "plan_to_spec": [], "verdict": "underspec",
          "examined": ["src/auth.py"]}
    assert _run(ev, ["src/auth.py"], tmp_path)["pass"] is True


def test_spec_present_plan_absent_passes(tmp_path):
    # spec committed but no plan: spec_to_plan has no (missing) plan to map to, so
    # empty matrices are correct. conclude blocks on (code & !plan).
    ev = {"scope": {"code_changed": True, "spec_present": True, "plan_present": False},
          "spec_to_plan": [], "plan_to_spec": [], "verdict": "underspec",
          "examined": ["docs/superpowers/specs/s.md"]}
    assert _run(ev, ["docs/superpowers/specs/s.md", "src/auth.py"], tmp_path)["pass"] is True


def test_absent_artifact_with_nonempty_matrix_fails(tmp_path):
    # spec/plan absent but the agent fabricated a non-empty matrix (nothing to map
    # against) → rejected.
    ev = {"scope": {"code_changed": True, "spec_present": False, "plan_present": False},
          "spec_to_plan": [{"requirement": "x", "status": "covered", "plan_quote": "y"}],
          "plan_to_spec": [], "verdict": "underspec", "examined": ["src/auth.py"]}
    r = _run(ev, ["src/auth.py"], tmp_path)
    assert r["pass"] is False and "empty" in r["feedback"].lower()
