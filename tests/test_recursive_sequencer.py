# tests/test_recursive_sequencer.py
"""Recursive sequencer (enter_root / enter_node) seeding behaviour.

The /answer-advances-cursor and draft→gate advance walks that once drove the
legacy `subpipeline-mini` fixture via BRANCH/SUBSTATE coords are covered by the
NODE_PATH suite:
  - /answer advances the leg cursor to the next sub-state
        → test_recover_mental_model.py (test_full_pipeline, test_answer_then_continue_dispatches_finalize)
  - draft (agent) done → clarify (gate) opens, cursor advances
        → test_recover_mental_model.py (test_full_pipeline)

What remains is the top-fanout SEEDING shape (flat leg vs. sub-pipeline leg with
its first sub-state), driven over a kept fixture via `start` (enter_root).
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
sys.path.insert(0, str(ROOT / "tests"))
from conftest import FIXTURES  # noqa: E402


def _run_next(state_dir, proto, instance, cmd, env, **coords):
    e = dict(env)
    for k, v in coords.items():
        e[k.upper()] = v
    return subprocess.run(["python3", str(ENGINE / "next.py"), str(state_dir), instance,
                           str(proto), cmd], text=True, capture_output=True, env=e)


def test_enter_top_fanout_seeds_branches(engine_env, tmp_path):
    """start on a single-phase fanout protocol with a flat leg + a sub-pipeline
    leg seeds: run-fanout action (flat leg has no substate, sub-pipeline leg
    carries its first sub-state) + the cursor + first sub-state files on disk.
    Driven over the subpipeline-gate fixture (summary flat, rationale sub-pipeline
    whose first sub-state is draft)."""
    sd = tmp_path / "state"; sd.mkdir()
    proto = FIXTURES / "subpipeline-gate" / "protocol.json"
    r = _run_next(sd, proto, "pr-1", "start", engine_env)
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-fanout"
    by = {b["id"]: b for b in action["branches"]}
    assert "substate" not in by["summary"]          # flat leg
    assert by["rationale"]["substate"] == "draft"   # sub-pipeline leg first sub-state
    # cursor + first sub-state files written under the instance dir
    base = sd / "subpipeline-gate" / "pr-1"
    assert (base / "rationale.yaml").exists() and (base / "rationale.draft.yaml").exists()
