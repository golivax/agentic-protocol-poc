import importlib.util
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MOD = ROOT / ".github/agent-factory/protocols/code-review-demo/publish/_apply_fixes.py"
spec = importlib.util.spec_from_file_location("_apply_fixes", MOD)
af = importlib.util.module_from_spec(spec)
spec.loader.exec_module(af)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_apply_replaces_target_line(tmp_path):
    _write(tmp_path, "a.py", "x = 0\ny = 1\nz = 2\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 1,
           "suggested_patch": "x = 99", "original_line": "x = 0"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "applied"
    assert (tmp_path / "a.py").read_text() == "x = 99\ny = 1\nz = 2\n"


def test_apply_skips_on_drift(tmp_path):
    _write(tmp_path, "a.py", "x = 0\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 1,
           "suggested_patch": "x = 99", "original_line": "DOES NOT MATCH"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "skipped" and res["detail"] == "drift"
    assert (tmp_path / "a.py").read_text() == "x = 0\n"


def test_apply_skips_missing_file(tmp_path):
    res = af.apply_fix(str(tmp_path), {"cluster_id": "c1", "path": "nope.py",
                                       "line": 1, "suggested_patch": "x"})
    assert res["status"] == "skipped" and res["detail"] == "missing-file"


def test_apply_skips_out_of_range(tmp_path):
    _write(tmp_path, "a.py", "x = 0\n")
    res = af.apply_fix(str(tmp_path), {"cluster_id": "c1", "path": "a.py",
                                       "line": 99, "suggested_patch": "x"})
    assert res["status"] == "skipped" and res["detail"] == "line-out-of-range"


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
        {"cluster_id": "c2", "path": "missing.py", "line": 1, "suggested_patch": "y"},
    ]
    out = af.apply_all(str(tmp_path), fixes)
    assert [r["status"] for r in out] == ["applied", "skipped"]


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
