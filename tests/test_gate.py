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


def test_pipeline_states_includes_gate_in_order():
    proto = {"states": [
        {"id": "a", "kind": "agent"},
        {"id": "f", "kind": "fanout"},
        {"id": "j", "kind": "join"},
        {"id": "g", "kind": "gate"},
    ]}
    assert [s["id"] for s in lib.pipeline_states(proto)] == ["a", "f", "g"]


def test_open_gate_seeds_file_and_check_run(tmp_path, capfd, monkeypatch):
    monkeypatch.setenv("ENGINE_LOCAL", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "golivax/agentic-protocol-poc")
    d = tmp_path / "state"
    base = d / PID / "pr-1"
    base.mkdir(parents=True)
    # an instance cursor file must exist for the status-comment refresh path
    (base / "_instance.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": "pr-1", "phase": "approval",
         "head_sha": "sha9", "joined": True, "status_comment_id": 5}))
    proto_path = tmp_path / "p.json"
    proto_path.write_text(json.dumps({"name": PID, "states": [
        {"id": "approval", "kind": "gate", "next": "done"}]}))
    lib.open_gate(str(d), PID, "pr-1", str(proto_path), "approval", "sha9", "1")

    gate = yaml.safe_load((base / "approval.yaml").read_text())
    assert gate["gates"] == {"state": "open", "history": []}
    assert gate["state"] == "approval"
    assert gate["head_sha"] == "sha9"
    err = capfd.readouterr().err
    assert "check-run code-review-pipeline/approval" in err
    assert "status=in_progress" in err
