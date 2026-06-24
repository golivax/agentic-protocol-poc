# tests/test_paths.py
import json, pathlib
import sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import paths

FIX = ROOT / "tests/fixtures"
PROTOCOLS = ROOT / ".github/agent-factory/protocols"


def _proto(name):
    return json.load(open(FIX / name / "protocol.json"))


def _cr():
    return json.load(open(PROTOCOLS / "code-review/protocol.json"))


# gate-deep (single-phase) is the kept depth-5 sub-pipeline fixture:
#   outer(fanout) → A flat / B(sequence: inner fanout → report) →
#   inner(fanout) → D flat / C(probe agent, clarify gate, wrap agent) / E(probe_e, ask gate)
def test_node_at_path_top_fanout():
    p = _proto("gate-deep")
    assert paths.node_at_path(p, ["outer"])["kind"] == "fanout"

def test_node_kind_branch_subpipeline_is_sequence():
    p = _proto("gate-deep")
    assert paths.node_kind(p, ["outer", "B"]) == "sequence"

def test_node_kind_flat_branch_is_agent():
    p = _proto("gate-deep")
    assert paths.node_kind(p, ["outer", "A"]) == "agent"

def test_node_at_path_substate_leaf():
    p = _proto("gate-deep")
    assert paths.node_at_path(p, ["outer", "B", "inner", "C", "clarify"])["kind"] == "gate"

def test_next_sibling_within_subpipeline():
    p = _proto("gate-deep")
    assert paths.next_sibling(p, ["outer", "B", "inner", "C", "probe"]) == "clarify"
    assert paths.next_sibling(p, ["outer", "B", "inner", "C", "wrap"]) is None

def test_next_sibling_top_sequence_multiphase():
    p = _cr()
    assert paths.next_sibling(p, ["preflight"]) == "review"

def test_enclosing_fanout_id():
    p = _proto("gate-deep")
    assert paths.enclosing_fanout_id(p, ["outer", "B", "inner", "C", "wrap"]) == "inner"
    assert paths.enclosing_fanout_id(p, ["outer", "A"]) == "outer"

def test_max_static_depth_depths():
    assert paths.max_static_depth(_proto("gate-deep")) == 5
    assert paths.max_static_depth(_proto("cap-single-agent")) == 1


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
    # gate-deep is single-phase → the leading top fanout id is dropped from filenames.
    p = _proto("gate-deep")
    assert lib.state_path(p, ["outer", "B", "inner", "D"]) == ["B", "inner", "D"]


def test_state_path_multiphase_keeps_full():
    # code-review is multi-phase → the full tree path is kept.
    p = _cr()
    assert lib.state_path(p, ["review", "grumpy"]) == ["review", "grumpy"]


def test_state_path_empty():
    p = _proto("gate-deep")
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


def test_root_ids_lists_top_level_phases():
    p = _cr()
    assert paths.root_ids(p) == [s["id"] for s in p["states"]]
    assert paths.is_root_child(p, ["preflight"]) is True
    assert paths.is_root_child(p, ["review", "grumpy"]) is False
    assert paths.is_root_child(p, ["nonesuch"]) is False
