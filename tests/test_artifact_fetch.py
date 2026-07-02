import json, os, stat, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / ".github/agent-factory/protocols/code-review/checks"


def _fake_gh(tmp_path, *, issue_body=None, issue_fail=False, file_b64=None, file_fail=False):
    """Write a fake `gh` onto a temp bin dir; return that dir for PATH-prepend."""
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True)
    script = f"""#!/usr/bin/env python3
import sys, json
args = sys.argv[1:]
joined = " ".join(args)
if "issues/" in joined:
    if {issue_fail!r}: sys.exit(1)
    sys.stdout.write({json.dumps(issue_body or "")!r})
    sys.exit(0)
if "contents/" in joined:
    if {file_fail!r}: sys.exit(1)
    sys.stdout.write({json.dumps(file_b64 or "")!r})
    sys.exit(0)
sys.exit(1)
"""
    gh = bindir / "gh"; gh.write_text(script)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def _import(tmp_path, bindir):
    """Import _artifact_fetch in a subprocess with the fake gh first on PATH,
    returning a tiny driver's JSON stdout."""
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}" + env["PATH"]
    return env


def test_fetch_issue_ok(tmp_path):
    bindir = _fake_gh(tmp_path, issue_body="problem one\nproblem two")
    env = _import(tmp_path, bindir)
    driver = f"import sys; sys.path.insert(0, {str(CHECKS)!r}); import _artifact_fetch, json; print(json.dumps(_artifact_fetch.fetch_issue('o/r', 7)))"
    out = subprocess.run([sys.executable, "-c", driver], env=env, text=True, capture_output=True)
    res = json.loads(out.stdout)
    assert res["ok"] is True and "problem one" in res["body"]


def test_fetch_issue_fail_closed(tmp_path):
    bindir = _fake_gh(tmp_path, issue_fail=True)
    env = _import(tmp_path, bindir)
    driver = f"import sys; sys.path.insert(0, {str(CHECKS)!r}); import _artifact_fetch, json; print(json.dumps(_artifact_fetch.fetch_issue('o/r', 7)))"
    out = subprocess.run([sys.executable, "-c", driver], env=env, text=True, capture_output=True)
    assert json.loads(out.stdout)["ok"] is False


def test_fetch_file_text_b64(tmp_path):
    import base64
    b64 = base64.b64encode(b"spec line A\nspec line B").decode()
    bindir = _fake_gh(tmp_path, file_b64=b64)
    env = _import(tmp_path, bindir)
    driver = f"import sys; sys.path.insert(0, {str(CHECKS)!r}); import _artifact_fetch; print(_artifact_fetch.fetch_file_text('o/r','docs/s.md','HEAD') or '')"
    out = subprocess.run([sys.executable, "-c", driver], env=env, text=True, capture_output=True)
    assert "spec line A" in out.stdout


def test_fetch_file_text_fail_returns_none(tmp_path):
    bindir = _fake_gh(tmp_path, file_fail=True)
    env = _import(tmp_path, bindir)
    driver = f"import sys; sys.path.insert(0, {str(CHECKS)!r}); import _artifact_fetch; v = _artifact_fetch.fetch_file_text('o/r','docs/s.md','HEAD'); print('NONE' if v is None else v)"
    out = subprocess.run([sys.executable, "-c", driver], env=env, text=True, capture_output=True)
    assert out.stdout.strip() == "NONE"


def test_fetch_file_text_none_ref_defaults_to_HEAD(tmp_path):
    """fetch_file_text with ref=None must not interpolate '?ref=None'; the guard
    ref = ref or 'HEAD' makes it fall through to ?ref=HEAD which the fake gh serves."""
    import base64
    b64 = base64.b64encode(b"guarded content").decode()
    bindir = _fake_gh(tmp_path, file_b64=b64)
    env = _import(tmp_path, bindir)
    driver = (
        f"import sys; sys.path.insert(0, {str(CHECKS)!r}); "
        "import _artifact_fetch; "
        "v = _artifact_fetch.fetch_file_text('o/r', 'docs/s.md', None); "
        "print(v or 'NONE')"
    )
    out = subprocess.run([sys.executable, "-c", driver], env=env, text=True, capture_output=True)
    assert "guarded content" in out.stdout, f"expected content, got: {out.stdout!r}"


def test_head_sha(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True)
    (bindir / "gh").write_text("#!/usr/bin/env python3\nimport sys\n"
                               "sys.stdout.write('deadbeef') if 'pr' in sys.argv else sys.exit(1)\n")
    (bindir / "gh").chmod(0o755)
    env = dict(os.environ); env["PATH"] = f"{bindir}{os.pathsep}" + env["PATH"]
    driver = f"import sys; sys.path.insert(0, {str(CHECKS)!r}); import _artifact_fetch; print(_artifact_fetch.head_sha('7'))"
    out = subprocess.run([sys.executable, "-c", driver], env=env, text=True, capture_output=True)
    assert out.stdout.strip() == "deadbeef"
