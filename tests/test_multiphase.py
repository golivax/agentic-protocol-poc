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


from conftest import state_origin, engine_env  # noqa: F401  (pytest fixtures)


def run_next(work_dir, instance, proto, command, env, phase="", branch="", head=""):
    e = dict(env)
    e["PHASE"] = phase
    e["BRANCH"] = branch
    r = subprocess.run(
        ["python3", str(ENGINE / "next.py"), str(work_dir), instance, str(proto), command, head],
        text=True, capture_output=True, env=e,
    )
    return r


def test_multiphase_start_seeds_cursor_at_first_phase(tmp_path, engine_env):
    work = tmp_path / "state"
    r = run_next(work, "pr-1", MINI, "start", engine_env, head="abc")
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-agent"
    assert action["phase"] == "gate"
    inst = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/_instance.yaml")
    assert inst["phase"] == "gate"
    assert inst["head_sha"] == "abc"
    assert inst["joined"] is False
    gate = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/gate.yaml")
    assert gate["state"] == "gate" and gate["iteration"] == 1


def test_multiphase_start_does_not_seed_later_phases(tmp_path, engine_env):
    work = tmp_path / "state"
    run_next(work, "pr-1", MINI, "start", engine_env, head="abc")
    assert not os.path.exists(str(work) + "/pipeline-mini/pr-1/work.alpha.yaml")


def test_singlephase_grumpy_start_unchanged(tmp_path, engine_env):
    work = tmp_path / "state"
    r = run_next(work, "pr-1", GRUMPY, "start", engine_env, head="abc")
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-agent"
    assert "phase" not in action
    assert os.path.exists(str(work) + "/grumpy-review/pr-1.yaml")


def test_singlephase_multigrumpy_start_unchanged(tmp_path, engine_env):
    work = tmp_path / "state"
    r = run_next(work, "pr-1", MULTI, "start", engine_env, head="abc")
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-fanout"
    assert "phase" not in action
    assert os.path.exists(str(work) + "/multi-grumpy/pr-1/_instance.yaml")
    assert os.path.exists(str(work) + "/multi-grumpy/pr-1/grumpy.yaml")


def test_seed_unknown_phase_exits_nonzero(tmp_path, engine_env):
    # advance-phase with a PHASE that isn't a real state → clean non-zero exit
    work = tmp_path / "state"
    r = run_next(work, "pr-1", MINI, "advance-phase", engine_env, phase="nope")
    assert r.returncode != 0
    assert "unknown phase" in r.stderr
