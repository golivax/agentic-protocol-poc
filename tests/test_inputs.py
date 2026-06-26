import importlib, sys
from conftest import ENGINE
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")


SUBPIPE = {
    "name": "rev",
    "states": [
        {"id": "review", "kind": "fanout", "branches": [
            {"id": "A", "workflow": "a"},
            {"id": "B", "states": [
                {"id": "draft", "kind": "agent", "workflow": "d"},
                {"id": "finalize", "kind": "agent", "workflow": "f",
                 "inputs": [{"from": "draft", "as": "draft"}]},
            ]},
        ]},
        {"id": "join", "kind": "join", "of": "review", "next": "combine"},
        {"id": "combine", "kind": "merge", "inputs": [
            {"from": "A", "as": "a"}, {"from": "B", "as": "b"}]},
    ],
}


def test_output_artifact_path_substate():
    p = lib.output_artifact_path("/s", "rev", "pr-1", branch="B", substate="draft")
    assert p == "/s/rev/pr-1/B.draft.evidence.json"


def test_output_artifact_path_flat_leg():
    p = lib.output_artifact_path("/s", "rev", "pr-1", branch="A")
    assert p == "/s/rev/pr-1/A.evidence.json"


def test_output_artifact_path_answers_kind():
    p = lib.output_artifact_path("/s", "rev", "pr-1", branch="B", substate="clarify", kind="answers")
    assert p == "/s/rev/pr-1/B.clarify.answers.json"


# The engine-walk inputs tests (evidence persisted on done; draft output flows to
# finalize's resolved inputs; run-agent action carries resolved inputs) drove the
# legacy subpipeline-mini fixture via BRANCH/SUBSTATE coords. They are covered by
# the NODE_PATH suite over recover-mental-model:
#   - test_recover_mental_model.test_full_pipeline persists each leg's evidence and
#     walks draft → gate → finalize;
#   - test_recover_mental_model.test_answer_then_continue_dispatches_finalize asserts
#     the resolved inputs ({answers, draft}) ride the finalize run-agent action.
# The pure lib unit tests below (over the inline SUBPIPE dict) stay.


def test_branch_output_substate():
    assert lib.branch_output_substate(SUBPIPE, "B") == "finalize"
    assert lib.branch_output_substate(SUBPIPE, "A") is None


def test_state_inputs():
    assert lib.state_inputs(SUBPIPE, "finalize") == [{"from": "draft", "as": "draft"}]
    assert lib.state_inputs(SUBPIPE, "combine")[0]["from"] == "A"
    assert lib.state_inputs(SUBPIPE, "draft") == []


def test_resolve_inputs_sibling_substate():
    res = lib.resolve_inputs(SUBPIPE, "/s", "rev", "pr-1",
                             consuming_branch="B", consuming_phase=None,
                             inputs=[{"from": "draft", "as": "draft"}])
    assert res == [{"as": "draft",
                    "path": "/s/rev/pr-1/B.draft.evidence.json",
                    "kind": "evidence"}]


def test_resolve_inputs_branch_leg_outputs():
    res = lib.resolve_inputs(SUBPIPE, "/s", "rev", "pr-1",
                             consuming_branch=None, consuming_phase=None,
                             inputs=[{"from": "A", "as": "a"}, {"from": "B", "as": "b"}])
    paths = {r["as"]: r["path"] for r in res}
    assert paths["a"] == "/s/rev/pr-1/A.evidence.json"
    assert paths["b"] == "/s/rev/pr-1/B.finalize.evidence.json"


def test_materialize_inputs(tmp_path):
    src = tmp_path / "src.json"; src.write_text('{"k": 1}')
    resolved = [{"as": "draft", "path": str(src), "kind": "evidence"},
                {"as": "missing", "path": str(tmp_path / "nope.json"), "kind": "evidence"}]
    manifest = lib.materialize_inputs(resolved, tmp_path / "agentwork")
    staged = {m["as"]: m["staged_path"] for m in manifest}
    assert set(staged) == {"draft"}   # missing source skipped
    assert (tmp_path / "agentwork/inputs/draft.json").read_text() == '{"k": 1}'


