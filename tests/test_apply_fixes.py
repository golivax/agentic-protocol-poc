import importlib.util
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MOD = ROOT / ".github/agent-factory/protocols/code-review/publish/_apply_fixes.py"
spec = importlib.util.spec_from_file_location("_apply_fixes", MOD)
af = importlib.util.module_from_spec(spec)
spec.loader.exec_module(af)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_apply_replaces_verified_line(tmp_path):
    _write(tmp_path, "a.py", "x = 0\ny = 1\nz = 2\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 1,
           "suggested_patch": "x = 99", "original_line": "x = 0"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "applied" and res["applied_line"] == 1
    assert (tmp_path / "a.py").read_text() == "x = 99\ny = 1\nz = 2\n"


def test_reanchor_when_agent_line_is_wrong(tmp_path):
    # The unique target content lives at line 3, but the agent claims line 1.
    _write(tmp_path, "a.py", "aaa\nbbb\nTARGET\nccc\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 1,
           "suggested_patch": "FIXED", "original_line": "TARGET"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "applied" and res["detail"] == "reanchored"
    assert res["applied_line"] == 3
    assert (tmp_path / "a.py").read_text() == "aaa\nbbb\nFIXED\nccc\n"


def test_skip_not_found_when_original_absent_from_file(tmp_path):
    _write(tmp_path, "a.py", "x = 0\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 1,
           "suggested_patch": "x = 99", "original_line": "NOT IN FILE"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "skipped" and res["detail"] == "not-found"
    assert (tmp_path / "a.py").read_text() == "x = 0\n"


def test_skip_ambiguous_when_reanchor_has_multiple_matches(tmp_path):
    # agent's line 2 ("mid") doesn't match -> re-anchor "dup" -> 2 matches -> ambiguous
    _write(tmp_path, "a.py", "dup\nmid\ndup\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 2,
           "suggested_patch": "changed", "original_line": "dup"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "skipped" and res["detail"] == "ambiguous"
    assert (tmp_path / "a.py").read_text() == "dup\nmid\ndup\n"


def test_skip_no_original_line(tmp_path):
    _write(tmp_path, "a.py", "x = 0\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 1, "suggested_patch": "x = 9"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "skipped" and res["detail"] == "no-original"
    assert (tmp_path / "a.py").read_text() == "x = 0\n"


def test_skip_missing_file(tmp_path):
    res = af.apply_fix(str(tmp_path), {"cluster_id": "c1", "path": "nope.py",
                                       "line": 1, "suggested_patch": "x",
                                       "original_line": "y"})
    assert res["status"] == "skipped" and res["detail"] == "missing-file"


def test_apply_multiline_patch(tmp_path):
    _write(tmp_path, "a.py", "a\nb\nc\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 2,
           "suggested_patch": "b1\nb2", "original_line": "b"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "applied"
    assert (tmp_path / "a.py").read_text() == "a\nb1\nb2\nc\n"


def test_apply_all_returns_one_result_per_fix(tmp_path):
    _write(tmp_path, "a.py", "x = 0\n")
    fixes = [
        {"cluster_id": "c1", "path": "a.py", "line": 1, "suggested_patch": "x = 1", "original_line": "x = 0"},
        {"cluster_id": "c2", "path": "missing.py", "line": 1, "suggested_patch": "y", "original_line": "z"},
    ]
    out = af.apply_all(str(tmp_path), fixes)
    assert [r["status"] for r in out] == ["applied", "skipped"]


def test_malformed_fix_missing_path(tmp_path):
    """Fix 3a: a fix missing `path` (path is None/empty) -> status skipped, detail malformed-fix."""
    fix = {"cluster_id": "c1", "path": None, "line": 1,
           "suggested_patch": "x = 1", "original_line": "x = 0"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "skipped" and res["detail"] == "malformed-fix"


def test_malformed_fix_non_string_patch(tmp_path):
    """Fix 3a: a fix with a non-string suggested_patch -> status skipped, detail malformed-fix."""
    _write(tmp_path, "a.py", "x = 0\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 1,
           "suggested_patch": 42, "original_line": "x = 0"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "skipped" and res["detail"] == "malformed-fix"
    assert (tmp_path / "a.py").read_text() == "x = 0\n"


def test_apply_skips_on_write_error(tmp_path):
    target = _write(tmp_path, "a.py", "x = 0\n")
    os.chmod(target, 0o444)  # read-only: read succeeds, write fails
    if os.access(target, os.W_OK):
        os.chmod(target, 0o644)
        pytest.skip("filesystem/user (likely root) ignores read-only bit")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 1,
           "suggested_patch": "x = 1", "original_line": "x = 0"}
    res = af.apply_fix(str(tmp_path), fix)
    os.chmod(target, 0o644)  # restore so tmp cleanup works
    assert res["status"] == "skipped" and res["detail"] == "write-error"
    assert target.read_text() == "x = 0\n"
