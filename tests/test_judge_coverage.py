# tests/test_judge_coverage.py
import base64, json, os, stat, sys, subprocess
from conftest import PROTOCOLS
CHECK = PROTOCOLS / "code-review/checks/judge-coverage.py"

def _gh(tmp_path, spec="S MUST x.", plan="do x."):
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True)
    sb, pb = base64.b64encode(spec.encode()).decode(), base64.b64encode(plan.encode()).decode()
    (bindir / "gh").write_text(f"""#!/usr/bin/env python3
import sys
j = " ".join(sys.argv[1:])
if "contents/" in j and "spec" in j: sys.stdout.write({sb!r}); sys.exit(0)
if "contents/" in j and "plan" in j: sys.stdout.write({pb!r}); sys.exit(0)
sys.exit(1)
""")
    (bindir / "gh").chmod(0o755)
    return bindir

def _run(ev_obj, changed, tmp_path, params):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(ev_obj))
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("\n".join(changed) + "\n")
    env = dict(os.environ)
    env["PATH"] = f"{_gh(tmp_path)}{os.pathsep}" + env["PATH"]
    env["PR_BODY"] = ""; env["GITHUB_REPOSITORY"] = "o/r"; env["PR"] = "1"
    env["CHECK_PARAMS"] = json.dumps(params)
    r = subprocess.run([sys.executable, str(CHECK), str(ev), str(diff), str(files)],
                       text=True, capture_output=True, env=env)
    return json.loads(r.stdout)

# coherence leg (docs): gather copy must pass _coherence.evaluate AND every item graded
def _docs_judge(graded):
    return {"leg": "docs-updated-appropriately",
            "gather": {"scope": {"code_changed": True},
                       "items": [{"path": "docs/a.md", "status": "missing"}],
                       "verdict": "inadequate", "examined": ["docs/a.md"]},
            "graded_findings": graded, "verdict": "block", "examined": ["docs/a.md"]}

def test_docs_judge_all_items_graded_passes(tmp_path):
    ev = _docs_judge([{"ref": "docs/a.md", "severity": "blocking", "rationale": "missing"}])
    assert _run(ev, ["src/x.py", "docs/a.md"], tmp_path, {"leg": "docs-updated-appropriately", "mode": "coherence"})["pass"] is True

def test_docs_judge_ungraded_finding_fails(tmp_path):
    ev = _docs_judge([])  # item not graded
    r = _run(ev, ["src/x.py", "docs/a.md"], tmp_path, {"leg": "docs-updated-appropriately", "mode": "coherence"})
    assert r["pass"] is False and "grade" in r["feedback"].lower()

def test_bad_severity_fails(tmp_path):
    ev = _docs_judge([{"ref": "docs/a.md", "severity": "critical", "rationale": "x"}])
    r = _run(ev, ["src/x.py", "docs/a.md"], tmp_path, {"leg": "docs-updated-appropriately", "mode": "coherence"})
    assert r["pass"] is False

def test_gather_copy_that_fails_its_own_check_fails(tmp_path):
    # gather verdict inconsistent with items => the re-run gather check fails => judge fails
    ev = _docs_judge([{"ref": "docs/a.md", "severity": "noise", "rationale": "x"}])
    ev["gather"]["verdict"] = "adequate"   # but a 'missing' item => recompute 'inadequate'
    r = _run(ev, ["src/x.py", "docs/a.md"], tmp_path, {"leg": "docs-updated-appropriately", "mode": "coherence"})
    assert r["pass"] is False and "inadequate" in r["feedback"].lower()

def test_mm_no_scope_enum_verdict(tmp_path):
    ev = {"leg": "mm-compliance",
          "gather": {"verdict": "diverges", "divergences": ["d0"], "examined": ["mm"]},
          "graded_findings": [{"ref": "0", "severity": "blocking", "rationale": "real"}],
          "verdict": "block", "examined": ["0"]}
    assert _run(ev, ["src/x.py"], tmp_path, {"leg": "mm-compliance", "mode": "mm"})["pass"] is True


def test_code_plan_anchor_error_fails(tmp_path):
    # gather.code-plan-coverage.evaluate PASSES (scope/verdict/plan_item all OK),
    # but the copied findings[] entry has a line anchor that does NOT exist in the
    # empty diff → _trace.findings_anchor_errors fires → judge returns pass:False.
    plan_item = "do x."           # verbatim in the plan text returned by _gh stub
    ev = {
        "leg": "code-implements-plan",
        "gather": {
            "scope": {"code_changed": True, "plan_present": True},
            "plan_to_code": [{"plan_item": plan_item, "status": "implemented"}],
            "verdict": "adheres",
            "files": [
                {
                    "path": "src/x.py",
                    "verdicts": [
                        {
                            "category": "implementation",
                            "verdict": "present",
                            "findings": [
                                {
                                    "plan_item": plan_item,
                                    "side": "RIGHT",
                                    "line": 42,       # line 42 not in empty diff
                                    "existing_code": "some code",
                                }
                            ],
                            "examined": [],
                        }
                    ],
                }
            ],
            "examined": ["src/x.py"],
        },
        "graded_findings": [
            {"ref": plan_item, "severity": "advisory", "rationale": "implemented"}
        ],
        "verdict": "pass",
        "examined": ["src/x.py"],
    }
    # changed files: one plan file (triggers plan_present=True recompute) + one code file
    changed = ["docs/superpowers/plans/p.md", "src/x.py"]
    r = _run(ev, changed, tmp_path, {"leg": "code-implements-plan", "mode": "code-plan"})
    assert r["pass"] is False and "anchor" in r["feedback"].lower()
