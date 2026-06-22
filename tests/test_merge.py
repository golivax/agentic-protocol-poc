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
