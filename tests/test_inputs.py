import importlib, sys
from conftest import ENGINE
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")


def test_output_artifact_path_substate():
    p = lib.output_artifact_path("/s", "rev", "pr-1", branch="B", substate="draft")
    assert p == "/s/rev/pr-1/B.draft.evidence.json"


def test_output_artifact_path_flat_leg():
    p = lib.output_artifact_path("/s", "rev", "pr-1", branch="A")
    assert p == "/s/rev/pr-1/A.evidence.json"


def test_output_artifact_path_answers_kind():
    p = lib.output_artifact_path("/s", "rev", "pr-1", branch="B", substate="clarify", kind="answers")
    assert p == "/s/rev/pr-1/B.clarify.answers.json"
