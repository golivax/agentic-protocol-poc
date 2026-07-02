import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/code-review/publish/conclude-fix.py"


def _run(env, evidence_path, instance="pr-8"):
    r = subprocess.run(["python3", str(HOOK), str(evidence_path), instance],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip())


def test_apply_writes_files_and_records_closes(tmp_path):
    # workdir = a fake PR head checkout
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "a.py").write_text("x = 0\n")

    # triage input (cluster c1 -> a correctness finding titled "Bad default")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "triage.json").write_text(json.dumps({
        "clusters": [{
            "cluster_id": "c1", "title": "Bad default", "dimension": ["correctness"],
            "severity": "high", "paths": ["a.py"], "rank": 1,
            "member_findings": [{"dimension": "correctness", "path": "a.py",
                                 "line": 1, "severity": "high", "title": "Bad default"}]
        }]
    }))

    evidence = tmp_path / "fix.json"
    evidence.write_text(json.dumps({
        "mode": "suggest", "skipped": [],
        "fixes": [{"cluster_id": "c1", "path": "a.py", "line": 1,
                   "rationale": "default should be 1", "suggested_patch": "x = 1",
                   "original_line": "x = 0"}]
    }))

    apply_out = tmp_path / "apply.json"
    env = dict(os.environ)
    env.update({
        "ENGINE_LOCAL": "1",
        "CONCLUDE_INPUTS_DIR": str(inputs),
        "APPLY_WORKDIR": str(workdir),
        "APPLY_OUT": str(apply_out),
        "GITHUB_REPOSITORY": "acme/repo", "PR": "8",
        "FIX_REVIEW_OUT": str(tmp_path / "review.json"),
        "FIX_OUT": str(tmp_path / "report.json"),
    })
    out = _run(env, evidence)

    # file actually edited
    assert (workdir / "a.py").read_text() == "x = 1\n"
    # report recorded the applied fix + the issue it would close
    rep = json.loads(apply_out.read_text())
    assert rep["applied"] == 1
    assert any(c["label"] == "review:correctness" and c["title"] == "Bad default"
               for c in rep["close"])
    assert out["conclusion"] == "neutral"


def test_close_issues_no_suffix_collision(tmp_path):
    """Fix 1 TDD: a target title that is a suffix of another issue's title must NOT
    over-close the longer issue.  Two open issues under review:correctness:
      - "[ai-review][correctness] guard null deref"  (should NOT be closed)
      - "[ai-review][correctness] null deref"         (the exact target -> should close)
    Target title = "null deref" -> only issue number 2 must be closed.
    """
    import importlib.util, pathlib, json as _json, subprocess as _sp

    ROOT = pathlib.Path(__file__).resolve().parent.parent
    MOD = ROOT / ".github/agent-factory/protocols/code-review/publish/conclude-fix.py"

    # We import _close_issues by loading conclude-fix as a module.
    spec = importlib.util.spec_from_file_location("conclude_fix", MOD)
    cf = importlib.util.module_from_spec(spec)
    # conclude-fix imports _apply_fixes on load; ensure its directory is on sys.path
    import sys
    publish_dir = str(MOD.parent)
    if publish_dir not in sys.path:
        sys.path.insert(0, publish_dir)
    spec.loader.exec_module(cf)

    closed = []

    # Stub gh issue list: return two issues with different titles
    def fake_run(cmd, **kwargs):
        class FakeResult:
            returncode = 0
            stdout = ""
        if "issue" in cmd and "list" in cmd:
            items = [
                {"number": 1, "title": "[ai-review][correctness] guard null deref"},
                {"number": 2, "title": "[ai-review][correctness] null deref"},
            ]
            r = FakeResult()
            r.stdout = _json.dumps(items)
            return r
        if "issue" in cmd and "close" in cmd:
            # Record which issue number was closed
            # cmd is like ["gh","issue","close","<N>","--repo",...]
            for tok in cmd:
                try:
                    closed.append(int(tok))
                    break
                except ValueError:
                    pass
            return FakeResult()
        return FakeResult()

    import unittest.mock as mock
    with mock.patch.object(_sp, "run", side_effect=fake_run):
        targets = [{"label": "review:correctness", "title": "null deref"}]
        cf._close_issues("acme/repo", targets, "fake-token")

    assert closed == [2], (
        f"Expected only issue #2 (exact match) to be closed, got: {closed}. "
        "Suffix match must not close issue #1 ('guard null deref')."
    )


def test_engine_local_no_apply_workdir_with_fixes(tmp_path):
    """Fix 3b: ENGINE_LOCAL with APPLY_WORKDIR unset but non-empty fixes ->
    _apply_commit_close records applied==0, close==[], conclusion neutral, no crash.
    Guards the `apply_all(workdir,...) if workdir else []` path."""
    evidence = tmp_path / "fix.json"
    evidence.write_text(json.dumps({
        "mode": "suggest", "skipped": [],
        "fixes": [{"cluster_id": "c1", "path": "a.py", "line": 1,
                   "rationale": "r", "suggested_patch": "x = 1",
                   "original_line": "x = 0"}]
    }))
    apply_out = tmp_path / "apply.json"
    env = dict(os.environ)
    # ENGINE_LOCAL=1 but APPLY_WORKDIR deliberately absent
    env.update({
        "ENGINE_LOCAL": "1",
        "APPLY_OUT": str(apply_out),
        "GITHUB_REPOSITORY": "acme/repo", "PR": "8",
        "FIX_REVIEW_OUT": str(tmp_path / "r.json"),
        "FIX_OUT": str(tmp_path / "rep.json"),
    })
    env.pop("APPLY_WORKDIR", None)
    env.pop("CONCLUDE_INPUTS_DIR", None)

    out = _run(env, evidence)
    assert out["conclusion"] == "neutral"
    rep = json.loads(apply_out.read_text())
    assert rep["applied"] == 0
    assert rep["close"] == []


def test_no_fixes_is_noop(tmp_path):
    evidence = tmp_path / "fix.json"
    evidence.write_text(json.dumps({"mode": "suggest", "fixes": [], "skipped": []}))
    apply_out = tmp_path / "apply.json"
    env = dict(os.environ)
    env.update({"ENGINE_LOCAL": "1", "APPLY_OUT": str(apply_out),
                "GITHUB_REPOSITORY": "acme/repo", "PR": "8",
                "FIX_REVIEW_OUT": str(tmp_path / "r.json"), "FIX_OUT": str(tmp_path / "rep.json")})
    out = _run(env, evidence)
    assert out["conclusion"] == "neutral"
    rep = json.loads(apply_out.read_text())
    assert rep["applied"] == 0 and rep["close"] == []
