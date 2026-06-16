import sys
from pathlib import Path
import pytest

ENGINE = Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

from test_triggers import SINGLE, MULTI, PIPELINE  # reuse the protocol fixtures


# Happy-path resolution modes -------------------------------------------------

def test_single_agent_unit():
    u = lib.resolve_agent_unit(SINGLE)
    assert u["agent_state"] == "review" and u["life_state"] == "review"


def test_single_agent_unit_max_iterations_none():
    # SINGLE fixture has no max_iterations on the agent state
    u = lib.resolve_agent_unit(SINGLE)
    assert u["max_iterations"] is None


def test_fanout_branch_unit():
    u = lib.resolve_agent_unit(MULTI, branch="security")
    # life_state is the owning fan-out state's id, not the branch id.
    assert u["agent_state"] == "security" and u["life_state"] == "review"


def test_fanout_branch_unit_grumpy():
    u = lib.resolve_agent_unit(MULTI, branch="grumpy")
    assert u["agent_state"] == "grumpy" and u["life_state"] == "review"


def test_agent_phase_unit():
    u = lib.resolve_agent_unit(PIPELINE, phase="gate")
    assert u["agent_state"] == "gate" and u["life_state"] == "gate"


def test_fanout_phase_unit():
    u = lib.resolve_agent_unit(PIPELINE, phase="review", branch="grumpy")
    assert u["agent_state"] == "grumpy" and u["life_state"] == "review"


def test_returns_dict_keys():
    u = lib.resolve_agent_unit(SINGLE)
    assert set(u.keys()) == {"agent_state", "max_iterations", "life_state"}


# Error cases -----------------------------------------------------------------

def test_no_agent_state_raises():
    proto = {"name": "empty", "states": []}
    with pytest.raises(ValueError, match="protocol has no agent state"):
        lib.resolve_agent_unit(proto)


def test_unknown_phase_raises():
    with pytest.raises(ValueError, match="no phase 'bogus' in protocol"):
        lib.resolve_agent_unit(PIPELINE, phase="bogus")


def test_fanout_phase_without_branch_raises():
    with pytest.raises(ValueError, match="PHASE='review' is a fanout phase but BRANCH is empty"):
        lib.resolve_agent_unit(PIPELINE, phase="review", branch="")


def test_unknown_branch_in_fanout_phase_raises():
    with pytest.raises(ValueError, match="no branch 'nope' in phase 'review'"):
        lib.resolve_agent_unit(PIPELINE, phase="review", branch="nope")


def test_unknown_branch_raises():
    with pytest.raises(ValueError, match="no branch 'nope' in protocol"):
        lib.resolve_agent_unit(MULTI, branch="nope")
