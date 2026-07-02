import base64, json, os, stat, sys, subprocess
from pathlib import Path
from conftest import PROTOCOLS, run_check, FIXTURES

CHECK = PROTOCOLS / "code-review/checks/code-plan-coverage.py"
TRACES = PROTOCOLS / "code-review/checks/traces-exist-in-diff.py"
PLAN_TEXT = "Add validate_token() to auth.py.\nReturn 401 on failure."


def _gh(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True)
    plan_b64 = base64.b64encode(PLAN_TEXT.encode()).decode()
    script = f"""#!/usr/bin/env python3
import sys
if "contents/" in " ".join(sys.argv[1:]): sys.stdout.write({plan_b64!r}); sys.exit(0)
sys.exit(1)
"""
    gh = bindir / "gh"; gh.write_text(script)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def _run(ev_obj, changed, tmp_path):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(ev_obj))
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("\n".join(changed) + "\n")
    env = dict(os.environ)
    env["PATH"] = f"{_gh(tmp_path)}{os.pathsep}" + env["PATH"]
    env["PR_BODY"] = ""; env["GITHUB_REPOSITORY"] = "o/r"; env.setdefault("PR", "1")
    r = subprocess.run([sys.executable, str(CHECK), str(ev), str(diff), str(files)],
                       text=True, capture_output=True, env=env)
    return json.loads(r.stdout)


CHANGED = ["docs/superpowers/plans/p.md", "src/auth.py"]


def _ev(verdict="adheres"):
    return {"scope": {"code_changed": True, "plan_present": True},
            "plan_to_code": [{"plan_item": "Add validate_token() to auth.py.", "status": "implemented"}],
            "files": [{"path": "src/auth.py", "verdicts": [
                {"category": "code-implements-plan", "examined": ["validate_token"],
                 "findings": []}]}],
            "verdict": verdict, "examined": ["docs/superpowers/plans/p.md"]}


def test_adheres_passes(tmp_path):
    assert _run(_ev(), CHANGED, tmp_path)["pass"] is True


def test_fabricated_plan_item_quote_fails(tmp_path):
    ev = _ev(); ev["plan_to_code"][0]["plan_item"] = "Delete the database."  # not in plan text
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and "verbatim" in r["feedback"].lower()


def test_underplan_consistency(tmp_path):
    ev = _ev(verdict="adheres"); ev["plan_to_code"][0]["status"] = "missing"
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and "underplan" in r["feedback"].lower()


def test_scope_disagreement_no_plan_file(tmp_path):
    r = _run(_ev(), ["src/auth.py"], tmp_path)  # no plan file => plan_present recompute False
    assert r["pass"] is False and "scope" in r["feedback"].lower()


def test_na_no_code_passes(tmp_path):
    ev = {"scope": {"code_changed": False, "plan_present": False},
          "plan_to_code": [], "files": [], "verdict": "n/a", "examined": ["(no code)"]}
    assert _run(ev, ["README.md"], tmp_path)["pass"] is True


def test_empty_plan_to_code_in_scope_fails(tmp_path):
    ev = _ev(); ev["plan_to_code"] = []
    assert _run(ev, CHANGED, tmp_path)["pass"] is False


def test_code_changed_no_plan_passes(tmp_path):
    # Regression (live-found): code changed but NO committed plan. The agent
    # correctly recomputes plan_present=False with an empty plan_to_code; the
    # form-check MUST accept it (conclude blocks on code & !plan). Before the fix
    # this emitted "in-scope leg must have a non-empty plan_to_code array", making
    # the leg un-passable on any PR lacking a committed plan.
    ev = {"scope": {"code_changed": True, "plan_present": False},
          "plan_to_code": [], "files": [], "verdict": "overplan", "examined": ["src/auth.py"]}
    assert _run(ev, ["src/auth.py"], tmp_path)["pass"] is True


def test_plan_absent_with_nonempty_p2c_fails(tmp_path):
    # plan absent but the agent fabricated a non-empty plan_to_code → rejected.
    ev = {"scope": {"code_changed": True, "plan_present": False},
          "plan_to_code": [{"plan_item": "x", "status": "implemented"}],
          "files": [], "verdict": "overplan", "examined": ["src/auth.py"]}
    r = _run(ev, ["src/auth.py"], tmp_path)
    assert r["pass"] is False and "empty" in r["feedback"].lower()


# --- the mandated traces-exist-in-diff reuse proof: a BAD anchor in the leg-3
#     files[] shape must be REJECTED (not vacuously passed) ---
def test_traces_rejects_bad_anchor_on_leg3_shape(tmp_path):
    ev_obj = {"files": [{"path": "src/auth.js", "verdicts": [
        {"category": "code-implements-plan", "examined": [],
         "findings": [{"plan_item": "x", "status": "traces", "side": "RIGHT",
                       "line": 99, "start_line": 0, "existing_code": "nope"}]}]}]}
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(ev_obj))
    r = run_check(TRACES, ev, FIXTURES / "diff-pr1.txt", FIXTURES / "changed-files-pr1.txt")
    assert r["pass"] is False  # bad anchor caught by the reused check
