"""M2a — multi-phase state machine tests.

lib helpers are pure; the next/advance/join tests drive the engine scripts in
ENGINE_LOCAL mode against a self-contained fixture protocol (tests/fixtures/
pipeline-mini): a `gate` agent phase → a single-branch `work` fan-out → `join`.
"""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
PROTOCOLS = ROOT / ".github/agent-factory/protocols"
FIXTURES = ROOT / "tests/fixtures"
MINI = FIXTURES / "pipeline-mini/protocol.json"
GRUMPY = PROTOCOLS / "grumpy/protocol.json"
MULTI = PROTOCOLS / "multi-grumpy/protocol.json"

sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def load(path):
    with open(path) as f:
        return json.load(f)


# --- lib.state_file phase arg ---

def test_state_file_legacy_single_agent():
    assert lib.state_file("/d", "p", "pr-1") == "/d/p/pr-1.yaml"


def test_state_file_legacy_fanout_branch():
    assert lib.state_file("/d", "p", "pr-1", branch="g") == "/d/p/pr-1/g.yaml"


def test_state_file_multiphase_agent():
    assert lib.state_file("/d", "p", "pr-1", phase="gate") == "/d/p/pr-1/gate.yaml"


def test_state_file_multiphase_fanout_branch():
    assert lib.state_file("/d", "p", "pr-1", branch="g", phase="work") == "/d/p/pr-1/work.g.yaml"


# --- protocol introspection ---

def test_is_multiphase_grumpy_false():
    assert lib.is_multiphase(load(GRUMPY)) is False


def test_is_multiphase_multigrumpy_false():
    assert lib.is_multiphase(load(MULTI)) is False


def test_is_multiphase_pipeline_true():
    assert lib.is_multiphase(load(MINI)) is True


def test_phase_states_are_agent_and_fanout_in_order():
    ids = [s["id"] for s in lib.phase_states(load(MINI))]
    assert ids == ["gate", "work"]


def test_phase_states_excludes_join():
    ids = [s["id"] for s in lib.phase_states(load(MINI))]
    assert "join" not in ids


def test_next_phase_id_follows_next():
    assert lib.next_phase_id(load(MINI), "gate") == "work"


def test_next_phase_id_terminal_is_none():
    # `work`.next is "join", a join state — not another phase → None
    assert lib.next_phase_id(load(MINI), "work") is None


def test_next_phase_id_unknown_is_none():
    assert lib.next_phase_id(load(MINI), "does-not-exist") is None


def test_state_by_id():
    assert lib.state_by_id(load(MINI), "join")["kind"] == "join"
    assert lib.state_by_id(load(MINI), "missing") is None


# --- fixture stub sanity (the engine resolves+runs these) ---

MINI_DIR = FIXTURES / "pipeline-mini"


def _run(path, *args, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([str(path), *args], text=True, capture_output=True, env=env)


def test_always_pass_check_abi():
    r = _run(MINI_DIR / "checks/always-pass.py", "ev", "diff", "files")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out == {"check": "always-pass", "pass": True, "feedback": ""}


def test_conclude_gate_clear_by_default(tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"gate": "clear"}))
    r = _run(MINI_DIR / "publish/conclude-gate.py", str(ev), "pr-1",
             env_extra={"BLOCKING": "0", "ENGINE_LOCAL": "1"})
    out = json.loads(r.stdout)
    assert out["blocked"] is False and out["conclusion"] and out["summary"]


def test_conclude_gate_blocked_by_evidence(tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"gate": "blocked"}))
    r = _run(MINI_DIR / "publish/conclude-gate.py", str(ev), "pr-1",
             env_extra={"BLOCKING": "0", "ENGINE_LOCAL": "1"})
    assert json.loads(r.stdout)["blocked"] is True


def test_conclude_gate_blocked_by_env(tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"gate": "clear"}))
    r = _run(MINI_DIR / "publish/conclude-gate.py", str(ev), "pr-1",
             env_extra={"BLOCKING": "1", "ENGINE_LOCAL": "1"})
    assert json.loads(r.stdout)["blocked"] is True
