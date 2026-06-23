import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import lib


def test_default_cap_allows_depth5():
    # gate-deep is depth-5 with NO max_depth field → allowed by the new default (5).
    p = json.load(open(ROOT / "tests/fixtures/gate-deep/protocol.json"))
    lib.check_depth(p)  # no raise


def test_default_cap_rejects_depth6():
    p = json.load(open(ROOT / "tests/fixtures/too-deep/protocol.json"))
    with pytest.raises(ValueError, match="max_depth"):
        lib.check_depth(p)


def test_explicit_max_depth_allows_depth6():
    p = json.load(open(ROOT / "tests/fixtures/too-deep/protocol.json"))
    p["max_depth"] = 6
    lib.check_depth(p)  # no raise


def test_next_refuses_too_deep(engine_env, tmp_path):
    proto = ROOT / "tests/fixtures/too-deep/protocol.json"
    r = subprocess.run(
        ["python3", str(ROOT / ".github/agent-factory/engine/next.py"),
         str(tmp_path), "pr-1", str(proto), "start"],
        text=True, capture_output=True, env=engine_env,
    )
    assert r.returncode == 2
    assert "max_depth" in r.stderr or "too deep" in r.stderr.lower()
