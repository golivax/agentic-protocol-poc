"""Unit tests for the unified path-based agent-unit resolver (resolve_agent_unit_path).

The legacy coord resolver lib.resolve_agent_unit (PHASE/BRANCH/SUBSTATE) was
deleted when the engine unified onto the single NODE_PATH path (Stage 4a, Task
16); its behaviour is now subsumed by resolve_agent_unit_path, which the live
engine (next.py / advance.py) calls. These tests pin that resolver over kept
fixtures.
"""
import json
import pathlib
import sys

ENGINE = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_resolve_unit_by_path_subpipeline_leaf():
    """A sub-pipeline agent leaf resolves to its own id + max_iterations; the
    life_state is the enclosing fanout id (deep-fanout: preflight.deep.triage)."""
    p = json.load(open(ROOT / "tests/fixtures/deep-fanout/protocol.json"))
    u = lib.resolve_agent_unit_path(p, ["preflight", "deep", "triage"])
    assert u == {"agent_state": "triage", "max_iterations": 2, "life_state": "preflight"}


def test_resolve_unit_by_path_flat_fanout_leg():
    """A flat fan-out child leg resolves to the branch id; life_state is the fanout id."""
    p = json.load(open(ROOT / "tests/fixtures/deep-fanout/protocol.json"))
    u = lib.resolve_agent_unit_path(p, ["preflight", "quick"])
    assert u["agent_state"] == "quick"
    assert u["life_state"] == "preflight"


def test_resolve_unit_by_path_top_level_agent():
    """A top-level agent phase resolves to itself with its own life_state
    (code-review: preflight is a root agent phase, max_iterations=2)."""
    p = json.load(open(ROOT / ".github/agent-factory/protocols/code-review/protocol.json"))
    u = lib.resolve_agent_unit_path(p, ["preflight"])
    assert u == {"agent_state": "preflight", "max_iterations": 2, "life_state": "preflight"}
