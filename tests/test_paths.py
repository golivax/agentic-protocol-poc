# tests/test_paths.py
import json, pathlib
import sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import paths

FIX = ROOT / "tests/fixtures"

def _proto(name):
    return json.load(open(FIX / name / "protocol.json"))

def test_node_at_path_top_fanout():
    p = _proto("subpipeline-mini")
    assert paths.node_at_path(p, ["review"])["kind"] == "fanout"

def test_node_kind_branch_subpipeline_is_sequence():
    p = _proto("subpipeline-mini")
    assert paths.node_kind(p, ["review", "B"]) == "sequence"

def test_node_kind_flat_branch_is_agent():
    p = _proto("subpipeline-mini")
    assert paths.node_kind(p, ["review", "A"]) == "agent"

def test_node_at_path_substate_leaf():
    p = _proto("subpipeline-mini")
    assert paths.node_at_path(p, ["review", "B", "clarify"])["kind"] == "gate"

def test_next_sibling_within_subpipeline():
    p = _proto("subpipeline-mini")
    assert paths.next_sibling(p, ["review", "B", "draft"]) == "clarify"
    assert paths.next_sibling(p, ["review", "B", "finalize"]) is None

def test_next_sibling_top_sequence_multiphase():
    p = _proto("multiphase-subpipeline")
    assert paths.next_sibling(p, ["setup"]) == "review"

def test_enclosing_fanout_id():
    p = _proto("subpipeline-mini")
    assert paths.enclosing_fanout_id(p, ["review", "B", "finalize"]) == "review"
    assert paths.enclosing_fanout_id(p, ["review", "A"]) == "review"

def test_max_static_depth_depth3():
    assert paths.max_static_depth(_proto("subpipeline-mini")) == 3
    assert paths.max_static_depth(_proto("single-agent")) == 1


import importlib
lib = importlib.import_module("lib")  # same engine sys.path as paths

def test_state_file_path_matches_kwargs():
    a = lib.state_file("/s", "p", "pr-1", phase="review", branch="B", substate="draft")
    b = lib.state_file("/s", "p", "pr-1", path=["review", "B", "draft"])
    assert a == b == "/s/p/pr-1/review.B.draft.yaml"

def test_state_file_path_deep():
    got = lib.state_file("/s", "p", "pr-1", path=["pre", "deep", "analyze", "sec"])
    assert got == "/s/p/pr-1/pre.deep.analyze.sec.yaml"

def test_output_artifact_path_deep():
    got = lib.output_artifact_path("/s", "p", "pr-1",
                                   path=["pre", "deep", "analyze", "sec"], kind="evidence")
    assert got == "/s/p/pr-1/pre.deep.analyze.sec.evidence.json"


def test_state_path_single_phase_drops_top():
    p = _proto("subpipeline-mini")
    assert lib.state_path(p, ["review", "B", "draft"]) == ["B", "draft"]


def test_state_path_multiphase_keeps_full():
    p = _proto("multiphase-subpipeline")
    assert lib.state_path(p, ["review", "B", "draft"]) == ["review", "B", "draft"]


def test_state_path_empty():
    p = _proto("subpipeline-mini")
    assert lib.state_path(p, []) == []


def test_gate_deep_fixture_shapes():
    p = _proto("gate-deep")
    # Deepest leaf path is length 5 (single-phase fanout → ... → gate/agent).
    assert paths.max_static_depth(p) == 5
    # The two gates sit where the plan says.
    assert paths.node_kind(p, ["outer", "B", "inner", "C", "clarify"]) == "gate"
    assert paths.node_kind(p, ["outer", "B", "inner", "E", "ask"]) == "gate"
    # clarify has a following sibling; ask is last.
    assert paths.next_sibling(p, ["outer", "B", "inner", "C", "clarify"]) == "wrap"
    assert paths.next_sibling(p, ["outer", "B", "inner", "E", "ask"]) is None
    # The enclosing fanout of both gates is the NESTED inner fanout (length 3).
    assert paths.enclosing_fanout_path(p, ["outer", "B", "inner", "C", "clarify"]) \
        == ["outer", "B", "inner"]
