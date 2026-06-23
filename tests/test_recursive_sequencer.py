# tests/test_recursive_sequencer.py
import json, os, pathlib, subprocess, sys, shutil
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"


def _run_next(state_dir, proto, instance, cmd, env, **coords):
    e = dict(env)
    for k in ("PHASE", "BRANCH", "SUBSTATE"):
        e.pop(k, None)
    for k, v in coords.items():
        e[k.upper()] = v
    return subprocess.run(["python3", str(ENGINE / "next.py"), str(state_dir), instance,
                           str(proto), cmd], text=True, capture_output=True, env=e)


def test_enter_top_fanout_seeds_branches(engine_env, tmp_path):
    sd = tmp_path / "state"; sd.mkdir()
    proto = ROOT / "tests/fixtures/subpipeline-mini/protocol.json"
    r = _run_next(sd, proto, "pr-1", "start", engine_env)
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-fanout"
    # flat branch A: no substate; sub-pipeline branch B: substate=draft
    by = {b["id"]: b for b in action["branches"]}
    assert "substate" not in by["A"]
    assert by["B"]["substate"] == "draft"
    # cursor + first sub-state files written under the instance dir
    base = sd / "subpipeline-mini" / "pr-1"
    assert (base / "B.yaml").exists() and (base / "B.draft.yaml").exists()
