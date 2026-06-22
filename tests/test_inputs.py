import importlib, json, subprocess, sys
from conftest import ENGINE, FIXTURES, run_engine
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


def _clone(tmp_path, engine_env):
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    return work


def test_evidence_persisted_on_done(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    verdicts = tmp_path / "v.json"
    verdicts.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    evid = tmp_path / "evidence.json"
    evid.write_text(json.dumps({"summary": "draft output", "questions": []}))
    e = dict(engine_env); e.update(BRANCH="B", SUBSTATE="draft",
                                   PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    out, err, rc = run_engine("advance.py", tmp_path / "dir-adv", "pr-1", proto, verdicts, evid, env=e)
    assert rc == 0, err
    work = _clone(tmp_path, engine_env)
    persisted = work / "subpipeline-mini/pr-1/B.draft.evidence.json"
    assert persisted.exists()
    assert json.loads(persisted.read_text())["summary"] == "draft output"


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


def test_run_agent_action_carries_inputs(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    # Start a fresh review in pr-1
    dir1 = tmp_path / "dir1"
    run_engine("next.py", dir1, "pr-1", proto, "start", "abc123", env=engine_env)
    # Resume finalize → its action should carry resolved inputs.
    # Needs a fresh directory to avoid state_checkout clone conflict
    dir2 = tmp_path / "dir2"
    out, err, rc = run_engine("next.py", dir2, "pr-1", proto, "continue",
                              env=engine_env, branch="B", substate="finalize")
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-agent"
    names = {i["as"]: i for i in action.get("inputs", [])}
    assert "draft" in names
    assert names["draft"]["path"].endswith("B.draft.evidence.json")


def test_draft_output_flows_to_finalize_inputs(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)

    # draft → done, persisting evidence with a distinctive payload.
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "draft-evidence.json"
    ev.write_text(json.dumps({"summary": "DRAFT-PAYLOAD"}))
    e = dict(engine_env); e.update(BRANCH="B", SUBSTATE="draft",
                                   PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir-adv", "pr-1", proto, v, ev, env=e)

    # Resolve finalize's inputs from the freshly pushed state, then materialize.
    work = _clone(tmp_path, engine_env)
    declared = lib.state_inputs(json.loads(proto.read_text()), "finalize")
    resolved = lib.resolve_inputs(json.loads(proto.read_text()), str(work),
                                  "subpipeline-mini", "pr-1",
                                  consuming_branch="B", consuming_phase=None,
                                  inputs=declared)
    manifest = lib.materialize_inputs(resolved, tmp_path / "agentwork")
    staged = (tmp_path / "agentwork/inputs/draft.json")
    assert staged.exists()
    assert json.loads(staged.read_text())["summary"] == "DRAFT-PAYLOAD"
