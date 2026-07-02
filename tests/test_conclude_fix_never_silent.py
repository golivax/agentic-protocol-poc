"""FIX2 — conclude-fix.py must never fail/return silently.

Two silent windows closed:
  (1) the non-local apply body (head, clone, apply_all, diag build) now runs
      inside try/except — an uncaught exception no longer crashes the hook;
      report["error"] is set and a scrubbed exception lands in the diag.
  (2) EVERY non-local return path — including the "no fixes" branch, which used
      to post nothing — now calls _write_diag + _post_apply_comment exactly once.

Hermetic: subprocess.run (gh/git remote) is stubbed to a fake-success; no real
network/remote calls happen. ENGINE_LOCAL is deliberately UNSET so the non-local
path is exercised.

Applied to every conclude-fix.py copy so they stay behaviourally identical.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COPIES = [
    ROOT / ".github/agent-factory/protocols/code-review/publish/conclude-fix.py",
    ROOT / ".github/agent-factory/protocols/code-review-reviewonly/publish/conclude-fix.py",
]


def _load(path):
    """Load a conclude-fix.py copy as a uniquely-named module (its publish dir
    on sys.path so it can import _apply_fixes)."""
    publish_dir = str(path.parent)
    if publish_dir not in sys.path:
        sys.path.insert(0, publish_dir)
    modname = "conclude_fix_" + path.parent.parent.name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _nonlocal_env(monkeypatch, tmp_path):
    """Wire a NON-local environment: repo/pr/token present, ENGINE_LOCAL unset,
    CONCLUDE_STATE_DIR pointing at tmp_path so _write_diag actually writes."""
    monkeypatch.delenv("ENGINE_LOCAL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/repo")
    monkeypatch.setenv("PR", "8")
    monkeypatch.setenv("GH_TOKEN", "fake-token-value")
    monkeypatch.setenv("CONCLUDE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("APPLY_OUT", raising=False)


def _fake_run_factory():
    """A subprocess.run stub that fake-succeeds every gh/git call (clone,
    pr view, commit, push, comment). Records comment invocations."""
    calls = {"comments": 0}

    def fake_run(cmd, *a, **k):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        r = R()
        if cmd and cmd[0] == "gh" and "pr" in cmd and "view" in cmd:
            r.stdout = "feature-branch\n"
        if cmd and cmd[0] == "gh" and "api" in cmd and any("comments" in str(x) for x in cmd):
            calls["comments"] += 1
        return r

    return fake_run, calls


@pytest.mark.parametrize("copy", COPIES, ids=lambda p: p.parent.parent.name)
def test_fix2_apply_exception_is_not_silent(copy, monkeypatch, tmp_path):
    """WITH fixes, force _apply_fixes.apply_all to raise in a non-local run.
    The hook must NOT raise; a diag file is written; report['error'] is set;
    _post_apply_comment is invoked."""
    mod = _load(copy)
    _nonlocal_env(monkeypatch, tmp_path)

    fake_run, calls = _fake_run_factory()
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    def boom(*a, **k):
        raise RuntimeError("apply blew up")
    monkeypatch.setattr(mod._apply_fixes, "apply_all", boom)

    posts = []
    orig_post = mod._post_apply_comment
    def spy_post(repo, pr, token, report):
        posts.append(dict(report))
        return orig_post(repo, pr, token, report)
    monkeypatch.setattr(mod, "_post_apply_comment", spy_post)

    evidence = {"mode": "suggest", "skipped": [],
                "fixes": [{"cluster_id": "c1", "path": "a.py", "line": 1,
                           "rationale": "r", "suggested_patch": "x = 1",
                           "original_line": "x = 0"}]}

    # Must not raise.
    report = mod._apply_commit_close(evidence)

    assert report.get("error"), "report['error'] must be set on apply exception"
    assert "apply blew up" in report["error"]
    assert len(posts) == 1, "expected exactly one _post_apply_comment call"
    diag_file = tmp_path / "_fix_diag.json"
    assert diag_file.exists(), "diag must be written even on exception"
    diag = json.loads(diag_file.read_text())
    assert "exception" in diag and "apply blew up" in diag["exception"]


@pytest.mark.parametrize("copy", COPIES, ids=lambda p: p.parent.parent.name)
def test_fix2_no_fixes_is_not_silent(copy, monkeypatch, tmp_path):
    """The 'no fixes' branch must post a PR comment (previously silent)."""
    mod = _load(copy)
    _nonlocal_env(monkeypatch, tmp_path)

    fake_run, _calls = _fake_run_factory()
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    posts = []
    orig_post = mod._post_apply_comment
    def spy_post(repo, pr, token, report):
        posts.append(dict(report))
        return orig_post(repo, pr, token, report)
    monkeypatch.setattr(mod, "_post_apply_comment", spy_post)

    report = mod._apply_commit_close({"mode": "suggest", "fixes": [], "skipped": []})

    assert len(posts) == 1, "the 'no fixes' branch must call _post_apply_comment (not silent)"
    assert report["applied"] == 0
    diag_file = tmp_path / "_fix_diag.json"
    assert diag_file.exists()


@pytest.mark.parametrize("copy", COPIES, ids=lambda p: p.parent.parent.name)
def test_fix2_engine_local_makes_no_network_calls(copy, monkeypatch, tmp_path):
    """ENGINE_LOCAL short-circuit preserved: no subprocess (gh/git-remote) call
    and NO PR comment. Uses a subprocess.run stub that fails the test if hit."""
    mod = _load(copy)
    monkeypatch.setenv("ENGINE_LOCAL", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/repo")
    monkeypatch.setenv("PR", "8")
    monkeypatch.setenv("CONCLUDE_STATE_DIR", str(tmp_path))
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "a.py").write_text("x = 0\n")
    monkeypatch.setenv("APPLY_WORKDIR", str(workdir))

    def no_network(cmd, *a, **k):
        raise AssertionError(f"ENGINE_LOCAL must not shell out: {cmd}")
    monkeypatch.setattr(mod.subprocess, "run", no_network)

    posts = []
    monkeypatch.setattr(mod, "_post_apply_comment",
                        lambda *a, **k: posts.append(1))

    evidence = {"mode": "suggest", "skipped": [],
                "fixes": [{"cluster_id": "c1", "path": "a.py", "line": 1,
                           "rationale": "r", "suggested_patch": "x = 1",
                           "original_line": "x = 0"}]}
    report = mod._apply_commit_close(evidence)

    assert posts == [], "local path must NOT post a PR comment"
    assert report["applied"] == 1  # applied locally in-place
    assert (workdir / "a.py").read_text() == "x = 1\n"


@pytest.mark.parametrize("copy", COPIES, ids=lambda p: p.parent.parent.name)
def test_fix2_push_failure_does_not_leak_token(copy, monkeypatch, tmp_path):
    """CRITICAL leak regression: on a push failure git echoes the tokened clone
    URL (https://x-access-token:<PAT>@github.com/...) into stderr. That must
    NEVER reach report['push_error'] (rendered into the exit-0 verdict summary →
    public check-run) nor the PUBLIC PR-comment body — both must be scrubbed.
    Mirrors the already-scrubbed diag['push_detail']. Fails against the pre-fix
    code where report['push_error'] = push['detail'] verbatim."""
    mod = _load(copy)
    _nonlocal_env(monkeypatch, tmp_path)
    SECRET = "fake-token-value"  # == GH_TOKEN set by _nonlocal_env

    # Capture the PR-comment body that _post_apply_comment posts via `gh api`.
    bodies = []

    def fake_run(cmd, *a, **k):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        r = R()
        if cmd and cmd[0] == "gh" and "pr" in cmd and "view" in cmd:
            r.stdout = "feature-branch\n"
        if cmd and cmd[0] == "gh" and "api" in cmd:
            for x in cmd:
                if isinstance(x, str) and x.startswith("body="):
                    bodies.append(x)
        return r
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    # One applied fix so the code reaches the push.
    monkeypatch.setattr(mod._apply_fixes, "apply_all",
                        lambda wd, fx: [{"status": "applied", "cluster_id": "c1",
                                         "path": "a.py", "detail": ""}])
    # Push fails with git's tokened-URL error verbatim in the detail.
    leak = ("push failed: fatal: unable to access "
            f"'https://x-access-token:{SECRET}@github.com/acme/repo.git/'")
    monkeypatch.setattr(mod, "_commit_push",
                        lambda *a, **k: {"ok": False, "detail": leak})

    evidence = {"mode": "suggest", "skipped": [],
                "fixes": [{"cluster_id": "c1", "path": "a.py", "line": 1,
                           "rationale": "r", "suggested_patch": "x = 1",
                           "original_line": "x = 0"}]}
    report = mod._apply_commit_close(evidence)

    assert report.get("push_error"), "push_error must be recorded"
    assert SECRET not in report["push_error"], "token leaked into report['push_error']"
    assert "<TOK>" in report["push_error"], "redaction marker absent — scrub did not run"
    assert bodies, "expected a PR comment to be posted"
    assert all(SECRET not in b for b in bodies), "token leaked into the PUBLIC PR comment body"
    # The git-side diag (cas_push'd to agentic-state) must not leak either.
    diag = json.loads((tmp_path / "_fix_diag.json").read_text())
    assert SECRET not in json.dumps(diag), "token leaked into the diag"
