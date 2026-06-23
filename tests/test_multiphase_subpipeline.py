import json
import shutil
import subprocess
from pathlib import Path

from conftest import run_engine, read_state_yaml, FIXTURES, ENGINE  # noqa: F401

import sys
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

PROTO = FIXTURES / "multiphase-subpipeline/protocol.json"


def _load():
    return json.loads(PROTO.read_text())


def _state_dir(tmp_path, engine_env, suffix=""):
    """Clone the fake origin so we can read pushed state files back."""
    work = tmp_path / f"work{suffix}"
    if work.exists():
        shutil.rmtree(work)
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    return work


def test_fixture_is_multiphase_with_subpipeline_branch():
    proto = _load()
    # Two phases (setup agent + review fanout) → multi-phase.
    assert lib.is_multiphase(proto) is True
    assert [s["id"] for s in lib.phase_states(proto)] == ["setup", "review"]
    # review fanout: A flat, B sub-pipeline (draft -> clarify -> finalize).
    assert lib.is_subpipeline_branch(lib.branch_config(proto, "A")) is False
    assert lib.is_subpipeline_branch(lib.branch_config(proto, "B")) is True
    assert [s["id"] for s in lib.branch_substates(proto, "B")] == ["draft", "clarify", "finalize"]
    # The fanout phase id is what _gate_phase will derive.
    assert lib._fanout_state(proto)["id"] == "review"


def test_start_fanout_single_phase_unchanged(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    out, err, rc = run_engine("next.py", tmp_path / "d", "pr-1", proto, "start", "abc123",
                              env=engine_env)
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-fanout"
    b = next(x for x in action["branches"] if x["id"] == "B")
    assert b["substate"] == "draft" and b["workflow"] == "draft-agent"
    a = next(x for x in action["branches"] if x["id"] == "A")
    assert "substate" not in a
    work = _state_dir(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "draft" and cursor["state"] == "review"
    assert "head_sha" not in cursor                      # single-phase cursor omits head_sha
    sub = read_state_yaml(work / "subpipeline-mini/pr-1/B.draft.yaml")
    assert sub["state"] == "review" and sub["iteration"] == 1
    flat = read_state_yaml(work / "subpipeline-mini/pr-1/A.yaml")
    assert "head_sha" not in flat                        # single-phase flat omits head_sha
