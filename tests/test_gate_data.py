import importlib, json, os, subprocess, sys
from conftest import ENGINE, FIXTURES, run_engine, read_state_yaml
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")


def _clone(tmp_path, engine_env):
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    return work


def test_open_gate_branch_scoped_with_questions(tmp_path, engine_env):
    # Arrange a checked-out state dir with an instance file present.
    dir_ = tmp_path / "dir"
    e = dict(engine_env)
    for k, v in e.items():
        os.environ[k] = v  # open_gate uses module-level git env via lib
    lib.STATE_REMOTE = e["STATE_REMOTE"]  # module-level constant captured at import
    lib.state_checkout(str(dir_))
    inst = lib.instance_file(str(dir_), "rev", "pr-1")
    os.makedirs(os.path.dirname(inst), exist_ok=True)
    lib.dump_yaml(inst, {"protocol": "rev", "instance": "pr-1", "joined": False})

    qs = [{"id": "q1", "text": "Which DB?"}, {"id": "q2", "text": "Sync or async?"}]
    lib.open_gate(str(dir_), "rev", "pr-1", str(FIXTURES / "subpipeline-mini/protocol.json"),
                  "clarify", "abc123", "1", branch="B", questions=qs)

    gf = read_state_yaml(lib.state_file(str(dir_), "rev", "pr-1", branch="B", substate="clarify"))
    assert gf["gates"]["state"] == "open"
    assert gf["gates"]["questions"] == qs
