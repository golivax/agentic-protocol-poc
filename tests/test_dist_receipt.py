import hashlib, json, sys
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"
sys.path.insert(0, str(DIST))
import receipt  # noqa: E402


def test_file_hash_matches_hashlib(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello")
    assert receipt.file_hash(str(f)) == hashlib.sha256(b"hello").hexdigest()


def test_build_receipt_shape(tmp_path):
    (tmp_path / "x.py").write_bytes(b"print(1)\n")
    r = receipt.build_receipt(
        source="o/r", ref="main", engine_version="1.0.0",
        protocols={"code-review": "0.1.0"}, files=["x.py"], root=str(tmp_path),
    )
    assert r["source"] == "o/r"
    assert r["protocols"] == {"code-review": "0.1.0"}
    assert r["files"]["x.py"] == hashlib.sha256(b"print(1)\n").hexdigest()


def test_write_receipt_roundtrip(tmp_path):
    r = {"source": "o/r", "files": {"a": "b"}}
    out = tmp_path / ".install.json"
    receipt.write_receipt(str(out), r)
    assert json.loads(out.read_text()) == r
    assert out.read_text().endswith("\n")


def test_orphans_finds_removed_files():
    old = {"files": {"a": "h1", "b": "h2", "c": "h3"}}
    assert receipt.orphans(old, ["a", "c"]) == ["b"]


def test_drift_detects_local_edits(tmp_path):
    (tmp_path / "a").write_bytes(b"orig")
    rec = {"files": {"a": receipt.file_hash(str(tmp_path / "a")), "gone": "x"}}
    # unchanged → no drift; missing file is not drift
    assert receipt.drifted(rec, str(tmp_path)) == []
    (tmp_path / "a").write_bytes(b"edited")
    assert receipt.drifted(rec, str(tmp_path)) == ["a"]


import subprocess  # noqa: E402


def test_version_compat():
    assert receipt.is_compatible("1.0.0", None) is True
    assert receipt.is_compatible("1.0.0", "1.0.0") is True
    assert receipt.is_compatible("1.2.0", "1.0.0") is True
    assert receipt.is_compatible("1.0.0", "2.0.0") is False


def test_breaking_bump():
    assert receipt.is_breaking_bump("1.4.0", "2.0.0") is True
    assert receipt.is_breaking_bump("1.0.0", "1.9.0") is False


def test_compat_cli_exit_codes():
    ok = subprocess.run([sys.executable, str(DIST / "receipt.py"), "compat", "1.0.0", "1.0.0"])
    bad = subprocess.run([sys.executable, str(DIST / "receipt.py"), "compat", "1.0.0", "2.0.0"])
    assert ok.returncode == 0 and bad.returncode == 1
