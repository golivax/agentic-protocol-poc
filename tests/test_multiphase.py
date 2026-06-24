"""Pure lib-helper unit tests for multi-phase protocol introspection.

The multi-phase engine-walk regression that drove next/advance/join over the
legacy `pipeline-mini` fixture via PHASE coords + `advance-phase` +
`protocol-advance` was removed when the engine unified onto the single NODE_PATH
path (Stage 4a, Task 16). That behaviour is now covered by the NODE_PATH suite:
  - multi-phase start → agent phase → advance via path-continue
        → test_unified_codereview_e2e.py, test_unified_advance.py
  - blocked gate halts the pipeline
        → test_override.py
  - restart resets the whole instance
        → test_cap_restart.py
  - phase labels across the walk
        → test_phase_labels.py

What remains is the GENERIC, still-live lib surface (state_file phase forms,
is_multiphase, phase_states, state_by_id) exercised over inline protocol dicts.
"""
import importlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


# Inline multi-phase protocol: agent phase `gate` → single-branch fanout `work` → join.
MULTIPHASE = {
    "name": "p",
    "states": [
        {"id": "gate", "kind": "agent", "workflow": "g-agent", "max_iterations": 2, "next": "work"},
        {"id": "work", "kind": "fanout", "next": "join", "branches": [
            {"id": "alpha", "workflow": "a-agent", "max_iterations": 2},
        ]},
        {"id": "join", "kind": "join", "of": "work", "next": None},
    ],
}

# Inline single-phase protocols (NOT multi-phase).
SINGLE_AGENT = {"name": "s", "states": [
    {"id": "review", "kind": "agent", "workflow": "r-agent"},
]}
SINGLE_FANOUT = {"name": "f", "states": [
    {"id": "review", "kind": "fanout", "branches": [
        {"id": "grumpy", "workflow": "g-agent", "max_iterations": 2},
    ]},
    {"id": "join", "kind": "join", "of": "review"},
]}


# --- lib.state_file phase arg (pure) ---

def test_state_file_legacy_single_agent():
    assert lib.state_file("/d", "p", "pr-1") == "/d/p/pr-1.yaml"


def test_state_file_legacy_fanout_branch():
    assert lib.state_file("/d", "p", "pr-1", branch="g") == "/d/p/pr-1/g.yaml"


def test_state_file_multiphase_agent():
    assert lib.state_file("/d", "p", "pr-1", phase="gate") == "/d/p/pr-1/gate.yaml"


def test_state_file_multiphase_fanout_branch():
    assert lib.state_file("/d", "p", "pr-1", branch="g", phase="work") == "/d/p/pr-1/work.g.yaml"


# --- protocol introspection (pure) ---

def test_is_multiphase_single_agent_false():
    assert lib.is_multiphase(SINGLE_AGENT) is False


def test_is_multiphase_single_fanout_false():
    assert lib.is_multiphase(SINGLE_FANOUT) is False


def test_is_multiphase_pipeline_true():
    assert lib.is_multiphase(MULTIPHASE) is True


def test_phase_states_are_agent_and_fanout_in_order():
    ids = [s["id"] for s in lib.phase_states(MULTIPHASE)]
    assert ids == ["gate", "work"]


def test_phase_states_excludes_join():
    ids = [s["id"] for s in lib.phase_states(MULTIPHASE)]
    assert "join" not in ids


def test_state_by_id():
    assert lib.state_by_id(MULTIPHASE, "join")["kind"] == "join"
    assert lib.state_by_id(MULTIPHASE, "missing") is None
