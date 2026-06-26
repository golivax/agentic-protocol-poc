"""Branch-scoped data-gate: lib.open_gate unit coverage.

The full branch-scoped data-gate WALK (advance into gate → /answer → partial →
complete → advance leg cursor) and the answers-coverage check unit tests that
once drove the legacy `subpipeline-mini` fixture via BRANCH/SUBSTATE coords moved
to the NODE_PATH suite:
  - draft → gate open → /answer → finalize → leg done
        → test_recover_mental_model.py (test_full_pipeline)
  - nested gate /answer (complete / partial / last-gate-fires-join)
        → test_nested_gate_answer.py (gate-deep)
  - answers-coverage check pass / missing / empty
        → test_recover_mental_model.py (test_answers_coverage_*)

What remains is the lib.open_gate branch-scoped + questions unit (its only home).
"""
import importlib
import os
import pathlib
import sys

from conftest import FIXTURES, read_state_yaml

ENGINE = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")

# A fixture protocol with a branch-scoped gate (rationale.clarify) — the shape the
# real recover-mental-model protocol no longer has (it uses socratic phase1→
# answering→phase2). Preserved here so this engine regression keeps its coverage.
RECOVER_PROTO = FIXTURES / "subpipeline-gate/protocol.json"


def test_open_gate_branch_scoped_with_questions(tmp_path, engine_env):
    """open_gate(branch=..., questions=[...]) seeds a branch-scoped gate file with
    state=open and the questions verbatim."""
    dir_ = tmp_path / "dir"
    for k, v in engine_env.items():
        os.environ[k] = v  # open_gate uses module-level git env via lib
    lib.STATE_REMOTE = engine_env["STATE_REMOTE"]
    lib.state_checkout(str(dir_))
    inst = lib.instance_file(str(dir_), "rev", "pr-1")
    os.makedirs(os.path.dirname(inst), exist_ok=True)
    lib.dump_yaml(inst, {"protocol": "rev", "instance": "pr-1", "joined": False})

    qs = [{"id": "q1", "text": "Which DB?"}, {"id": "q2", "text": "Sync or async?"}]
    lib.open_gate(str(dir_), "rev", "pr-1", str(RECOVER_PROTO),
                  "clarify", "abc123", "1", branch="rationale", questions=qs)

    gf = read_state_yaml(lib.state_file(str(dir_), "rev", "pr-1",
                                        branch="rationale", substate="clarify"))
    assert gf["gates"]["state"] == "open"
    assert gf["gates"]["questions"] == qs
