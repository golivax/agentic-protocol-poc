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


# --- conclude-design hook (Task 14: enforce no-spec/plan => no-PR) ---------

CONCLUDE = ROOT / ".github/agent-factory/protocols/impl-feature-auto/publish/conclude-design.py"


def run_conclude(blocking, tmp_path):
    """Invoke conclude-design.py directly (it is executable, per the engine ABI),
    with BLOCKING set. Returns the parsed {conclusion,summary,blocked} dict."""
    ev = tmp_path / "evidence.json"
    ev.write_text("{}")
    env = dict(os.environ)
    env["BLOCKING"] = blocking
    r = subprocess.run([str(CONCLUDE), str(ev), "issue-5"],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_conclude_design_is_executable():
    # The engine (advance.run_conclude_hook) requires os.access(path, X_OK).
    assert os.access(CONCLUDE, os.X_OK), "conclude-design.py must be executable"


def test_conclude_design_blocking(tmp_path):
    out = run_conclude("1", tmp_path)
    assert out["blocked"] is True
    assert out["conclusion"] == "blocked"
    assert "blocked" in out  # valid JSON carrying the blocked key


def test_conclude_design_clear(tmp_path):
    out = run_conclude("0", tmp_path)
    assert out["blocked"] is False
    assert out["conclusion"] == "clear"
    assert "blocked" in out
