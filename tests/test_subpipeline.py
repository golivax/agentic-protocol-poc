"""Pure lib-helper unit tests for sub-pipeline branch resolution.

The engine-walk coverage that once drove advance.py/next.py/join.py over the
legacy `subpipeline-mini` fixture via BRANCH/SUBSTATE coords was removed when the
engine unified onto the single NODE_PATH path (Stage 4a, Task 16). Those walks
are now covered by the NODE_PATH suite:
  - sub-pipeline leg seeding + cursor advance + agent→agent + leg-done + join
        → test_deep_fanout_e2e.py (depth-4 sub-pipeline legs)
  - branch-scoped data gate (open / answer / partial / advance)
        → test_gate_data.py, test_nested_gate_answer.py

What remains here is the GENERIC lib surface that advance.py/next.py still call
(branch_config, is_subpipeline_branch, branch_substates, next_substate_id,
agent_workflow, state_file) — exercised over an inline protocol dict so it needs
no fixture on disk.
"""
import importlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")


# Inline sub-pipeline protocol: flat branch A + sub-pipeline branch B
# (draft agent → clarify gate → finalize agent). Mirrors the shape the live
# engine resolves; no fixture file required.
SUBPIPE_PROTO = {
    "name": "rev",
    "states": [
        {"id": "review", "kind": "fanout", "branches": [
            {"id": "A", "workflow": "a-agent", "max_iterations": 2},
            {"id": "B", "states": [
                {"id": "draft", "kind": "agent", "workflow": "draft-agent", "max_iterations": 2},
                {"id": "clarify", "kind": "gate", "questions_from": "draft"},
                {"id": "finalize", "kind": "agent", "workflow": "finalize-agent", "max_iterations": 2},
            ]},
        ]},
        {"id": "join", "kind": "join", "of": "review", "next": "done"},
    ],
}


# ---------------------------------------------------------------------------
# state_file path shapes (pure)
# ---------------------------------------------------------------------------

def test_state_file_substate_branch_only():
    p = lib.state_file("/s", "rev", "pr-1", branch="B", substate="draft")
    assert p == "/s/rev/pr-1/B.draft.yaml"


def test_state_file_substate_with_phase():
    p = lib.state_file("/s", "rev", "pr-1", branch="B", phase="review", substate="draft")
    assert p == "/s/rev/pr-1/review.B.draft.yaml"


def test_state_file_existing_shapes_unchanged():
    assert lib.state_file("/s", "rev", "pr-1") == "/s/rev/pr-1.yaml"
    assert lib.state_file("/s", "rev", "pr-1", branch="B") == "/s/rev/pr-1/B.yaml"
    assert lib.state_file("/s", "rev", "pr-1", phase="review") == "/s/rev/pr-1/review.yaml"
    assert lib.state_file("/s", "rev", "pr-1", branch="B", phase="review") == "/s/rev/pr-1/review.B.yaml"


# ---------------------------------------------------------------------------
# branch / sub-state resolution helpers (live; called by advance.py + next.py)
# ---------------------------------------------------------------------------

def test_branch_config():
    assert lib.branch_config(SUBPIPE_PROTO, "A")["workflow"] == "a-agent"
    assert lib.branch_config(SUBPIPE_PROTO, "B")["id"] == "B"
    assert lib.branch_config(SUBPIPE_PROTO, "missing") is None


def test_is_subpipeline_branch():
    assert lib.is_subpipeline_branch(lib.branch_config(SUBPIPE_PROTO, "B")) is True
    assert lib.is_subpipeline_branch(lib.branch_config(SUBPIPE_PROTO, "A")) is False


def test_branch_substates():
    ids = [s["id"] for s in lib.branch_substates(SUBPIPE_PROTO, "B")]
    assert ids == ["draft", "clarify", "finalize"]
    assert lib.branch_substates(SUBPIPE_PROTO, "A") == []


def test_next_substate_id():
    assert lib.next_substate_id(SUBPIPE_PROTO, "B", "draft") == "clarify"
    assert lib.next_substate_id(SUBPIPE_PROTO, "B", "clarify") == "finalize"
    assert lib.next_substate_id(SUBPIPE_PROTO, "B", "finalize") is None


# ---------------------------------------------------------------------------
# agent_workflow substate awareness (live; lib.py CLI forwards substate)
# ---------------------------------------------------------------------------

def test_agent_workflow_substate_draft():
    assert lib.agent_workflow(SUBPIPE_PROTO, phase="review", branch="B", substate="draft") == "draft-agent"


def test_agent_workflow_substate_finalize():
    assert lib.agent_workflow(SUBPIPE_PROTO, phase="review", branch="B", substate="finalize") == "finalize-agent"


def test_agent_workflow_flat_branch_unchanged():
    assert lib.agent_workflow(SUBPIPE_PROTO, phase="review", branch="A") == "a-agent"


def test_agent_workflow_substate_unknown_returns_empty():
    assert lib.agent_workflow(SUBPIPE_PROTO, phase="review", branch="B", substate="nonexistent") == ""


def test_agent_workflow_substate_via_branch_only_arm():
    assert lib.agent_workflow(SUBPIPE_PROTO, branch="B", substate="draft") == "draft-agent"


def test_agent_workflow_gate_substate_has_no_workflow():
    """A gate sub-state resolves to no workflow."""
    assert lib.agent_workflow(SUBPIPE_PROTO, phase="review", branch="B", substate="clarify") == ""
