import importlib, json, subprocess, sys
from conftest import ENGINE, FIXTURES, run_engine
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
