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
is_multiphase, phase_states, state_by_id) exercised over inline protocol dicts,
plus the NODE_PATH-required contract guard (T3).
"""
import importlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
FIXTURES = ROOT / "tests/fixtures"
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


# --- T3: bare `continue` without NODE_PATH fails loudly ---

def test_continue_without_node_path_exits_2(engine_env, tmp_path):
    """next.py `continue` with NO NODE_PATH must exit 2 with a clear stderr message.
    This pins the 'NODE_PATH required' contract: the unified engine has a single
    coordinate (NODE_PATH); a bare `continue` is a programmer error and must fail
    loudly rather than silently no-op or advance the wrong state. Uses the
    cap-mp-fanout-gate fixture (a multi-phase protocol) as the representative case
    (single-phase behaves identically — the guard is unconditional)."""
    proto = FIXTURES / "cap-mp-fanout-gate/protocol.json"
    r = subprocess.run(
        ["python3", str(ENGINE / "next.py"), str(tmp_path / "dir"), "pr-1", str(proto), "continue"],
        text=True, capture_output=True, env=engine_env,
    )
    assert r.returncode == 2, (
        f"Expected exit code 2 for bare `continue` without NODE_PATH, got {r.returncode}.\n"
        f"stderr: {r.stderr}"
    )
    assert "node_path" in r.stderr.lower(), (
        f"Expected a clear 'NODE_PATH required' message in stderr, got:\n{r.stderr}"
    )
