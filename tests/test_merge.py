import importlib, json, os, subprocess, sys
from conftest import ENGINE, FIXTURES, run_engine, read_state_yaml
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")


def test_append_hook_concatenates(tmp_path):
    work = tmp_path / "w"
    (work / "inputs").mkdir(parents=True)
    (work / "inputs/a.json").write_text(json.dumps({"summary": "AOUT"}))
    (work / "inputs/b.json").write_text(json.dumps({"summary": "BOUT"}))
    hook = FIXTURES / "subpipeline-mini/publish/append-outputs.py"
    r = subprocess.run([str(hook), str(work), "pr-1"], text=True, capture_output=True)
    out = json.loads(r.stdout)
    assert out["conclusion"] == "success"
    assert "AOUT" in out["summary"] and "BOUT" in out["summary"]


def test_run_merge_hook(tmp_path, engine_env):
    # Lay down a state dir with both branch outputs persisted.
    dir_ = tmp_path / "dir"
    for k, v in engine_env.items():
        os.environ[k] = v
    lib.STATE_REMOTE = engine_env["STATE_REMOTE"]
    lib.state_checkout(str(dir_))
    base = f"{dir_}/subpipeline-mini/pr-1"
    os.makedirs(base, exist_ok=True)
    # A flat leg output + B sub-pipeline leg output (finalize).
    open(f"{base}/A.evidence.json", "w").write(json.dumps({"summary": "FROM-A"}))
    open(f"{base}/B.finalize.evidence.json", "w").write(json.dumps({"summary": "FROM-B"}))

    proto_path = str(FIXTURES / "subpipeline-mini/protocol.json")
    proto = json.load(open(proto_path))
    merge_state = lib.state_by_id(proto, "combine")
    res = lib.run_merge_hook(str(dir_), "subpipeline-mini", "pr-1", proto_path, merge_state)
    assert res["conclusion"] == "success"
    assert "FROM-A" in res["summary"] and "FROM-B" in res["summary"]
