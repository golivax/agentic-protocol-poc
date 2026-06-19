"""v4 pause-and-require approval gate — engine-side behavior. All GitHub I/O is
ENGINE_LOCAL stderr no-ops we assert on. Mirrors tests/test_override.py style."""
import json
import os
import subprocess
import sys

import pytest
import yaml

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
NEXT_PY = ENGINE / "next.py"
JOIN_PY = ENGINE / "join.py"
LIB_PY = ENGINE / "lib.py"
PIPELINE_PROTO = ROOT / ".github/agent-factory/protocols/code-review-pipeline/protocol.json"
PID = json.load(open(PIPELINE_PROTO))["name"]

sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def test_next_phase_id_returns_gate_kind():
    proto = {"states": [
        {"id": "a", "kind": "agent", "next": "g"},
        {"id": "g", "kind": "gate", "next": "done"},
    ]}
    assert lib.next_phase_id(proto, "a") == "g"
    # a gate whose next is a terminal → None (finalize)
    assert lib.next_phase_id(proto, "g") is None
