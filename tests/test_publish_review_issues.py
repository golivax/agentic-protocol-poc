import json, os, subprocess, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/code-review/publish/publish-review.py"
DIMS = ["correctness", "test", "performance", "security", "maintainability"]


def _finding(title="Null deref in handler", **kw):
    f = {"path": "src/a.py", "line": 12, "severity": "high", "category": "correctness",
         "title": title, "impact": "crash on empty input", "fix": "guard None"}
    f.update(kw); return f


def _run(evidence, tmp_path, extra_env=None):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(evidence))
    out = tmp_path / "issues.json"
    env = dict(os.environ)
    env["ENGINE_LOCAL"] = "1"; env["PR"] = "7"; env["GITHUB_REPOSITORY"] = "o/r"
    env["REVIEW_ISSUES_OUT"] = str(out)
    if extra_env: env.update(extra_env)
    r = subprocess.run([sys.executable, str(HOOK), str(ev), "pr-7"], text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    plan = json.loads(out.read_text()) if out.exists() else []
    return json.loads(r.stdout), plan


def test_opens_one_issue_per_finding(tmp_path):
    ev = {"dimension": "correctness", "verdict": "REQUEST_CHANGES",
          "findings": [_finding(title="A"), _finding(title="B")]}
    _out, plan = _run(ev, tmp_path)
    assert len(plan) == 2


@pytest.mark.parametrize("dim", DIMS)
def test_issue_title_prefix_and_label(dim, tmp_path):
    ev = {"dimension": dim, "verdict": "COMMENT", "findings": [_finding(title="Bad default")]}
    _out, plan = _run(ev, tmp_path)
    assert plan[0]["title"] == f"[ai-review][{dim}] Bad default"
    assert set(plan[0]["labels"]) == {"ai-review", f"review:{dim}"}


@pytest.mark.parametrize("dim", DIMS)
def test_title_endswith_finding_title(dim, tmp_path):
    ev = {"dimension": dim, "verdict": "COMMENT", "findings": [_finding(title="X Y Z")]}
    _out, plan = _run(ev, tmp_path)
    assert plan[0]["title"].lower().endswith("x y z")


def test_issue_body_has_path_line_severity_fix(tmp_path):
    ev = {"dimension": "correctness", "verdict": "COMMENT", "findings": [_finding()]}
    _out, plan = _run(ev, tmp_path)
    b = plan[0]["body"]
    assert "src/a.py:12" in b and "high" in b and "guard None" in b
    assert "Found by the correctness reviewer on PR #7" in b


def test_approve_empty_findings_opens_no_issue(tmp_path):
    out, plan = _run({"dimension": "test", "verdict": "APPROVE", "findings": []}, tmp_path)
    assert plan == [] and "0" in out["summary"]


def test_cap_five_findings(tmp_path):
    ev = {"dimension": "security", "verdict": "REQUEST_CHANGES",
          "findings": [_finding(title=f"F{i}") for i in range(7)]}
    _out, plan = _run(ev, tmp_path)
    assert len(plan) == 5


def test_conclusion_maps_verdict(tmp_path):
    for verdict, concl in [("REQUEST_CHANGES", "failure"), ("APPROVE", "success"), ("COMMENT", "neutral")]:
        out, _plan = _run({"dimension": "correctness", "verdict": verdict, "findings": []}, tmp_path)
        assert out["conclusion"] == concl


def test_idempotent_skips_existing_open_titles(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("pr_hook", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    class R:  # fake gh_api result
        def __init__(self, rc=0, out="", err=""): self.returncode, self.stdout, self.stderr = rc, out, err
    calls = {"posts": 0}
    def fake_gh_api(path, method="GET", input_json=None, token="", jq=None):
        if method == "GET":
            return R(0, "[ai-review][correctness] A\n")   # A already open
        calls["posts"] += 1
        return R(0, "")
    monkeypatch.setattr(mod, "gh_api", fake_gh_api)
    monkeypatch.setenv("ENGINE_LOCAL", "0")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r"); monkeypatch.setenv("PR", "7"); monkeypatch.setenv("PUBLISH_TOKEN", "t")
    ev = {"dimension": "correctness", "verdict": "REQUEST_CHANGES",
          "findings": [_finding(title="A"), _finding(title="B")]}
    evp = tmp_path / "ev.json"; evp.write_text(json.dumps(ev))
    monkeypatch.setattr(sys, "argv", ["publish-review.py", str(evp), "pr-7"])
    mod.main()
    assert calls["posts"] == 1   # only B opened; A skipped
