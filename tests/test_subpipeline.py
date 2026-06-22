from conftest import ENGINE  # noqa: F401  (ensures sys.path includes tests/)
import sys, importlib
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")


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
