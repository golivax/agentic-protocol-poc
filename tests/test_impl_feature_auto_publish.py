import json, os, pathlib, subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/impl-feature-auto/publish/post-summary.py"

def run_hook(evidence_obj, instance, tmp_path, env_extra=None):
    ev = tmp_path / "evidence.json"
    ev.write_text(json.dumps(evidence_obj))
    env = dict(os.environ); env["ENGINE_LOCAL"] = "1"
    env.update(env_extra or {})
    r = subprocess.run(["python3", str(HOOK), str(ev), instance],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)

def test_post_summary_local_success(tmp_path):
    out = run_hook({"summary": "did X", "pr_branch": "impl-feature-auto/issue-7"},
                   "issue-7", tmp_path)
    assert out["conclusion"] == "success"
    assert "issue-7" in out["summary"] or "7" in out["summary"]

def test_post_summary_defensive_no_branch(tmp_path):
    out = run_hook({"summary": "did X"}, "issue-7", tmp_path)
    assert out["conclusion"] in ("neutral", "failure")
    assert "pr" in out["summary"].lower()
