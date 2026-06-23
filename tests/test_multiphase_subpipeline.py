import json
import shutil
import subprocess
from pathlib import Path

from conftest import run_engine, read_state_yaml, FIXTURES, ENGINE  # noqa: F401

import sys
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

PROTO = FIXTURES / "multiphase-subpipeline/protocol.json"


def _load():
    return json.loads(PROTO.read_text())


def _state_dir(tmp_path, engine_env, suffix=""):
    """Clone the fake origin so we can read pushed state files back."""
    work = tmp_path / f"work{suffix}"
    if work.exists():
        shutil.rmtree(work)
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    return work


def test_fixture_is_multiphase_with_subpipeline_branch():
    proto = _load()
    # Two phases (setup agent + review fanout) → multi-phase.
    assert lib.is_multiphase(proto) is True
    assert [s["id"] for s in lib.phase_states(proto)] == ["setup", "review"]
    # review fanout: A flat, B sub-pipeline (draft -> clarify -> finalize).
    assert lib.is_subpipeline_branch(lib.branch_config(proto, "A")) is False
    assert lib.is_subpipeline_branch(lib.branch_config(proto, "B")) is True
    assert [s["id"] for s in lib.branch_substates(proto, "B")] == ["draft", "clarify", "finalize"]
    # The fanout phase id is what _gate_phase will derive.
    assert lib._fanout_state(proto)["id"] == "review"


def test_advance_phase_into_fanout_seeds_subpipeline(tmp_path, engine_env):
    # Drive the multi-phase advance-phase entry directly into the fanout phase.
    out, err, rc = run_engine("next.py", tmp_path / "d", "pr-1", str(PROTO),
                              "advance-phase", "abc123", env=engine_env, phase="review")
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-fanout"
    assert action.get("phase") == "review"

    b = next(x for x in action["branches"] if x["id"] == "B")
    assert b["substate"] == "draft"          # sub-pipeline branch now dispatches its first sub-state
    assert b["workflow"] == "draft-agent"
    a = next(x for x in action["branches"] if x["id"] == "A")
    assert "substate" not in a               # flat branch still flat

    work = _state_dir(tmp_path, engine_env)
    # Phase-qualified paths: <phase>.<branch>[.<substate>].yaml
    cursor = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.yaml")
    assert cursor["sub_state"] == "draft" and cursor["state"] == "review"
    sub = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.draft.yaml")
    assert sub["state"] == "review" and sub["iteration"] == 1
    flat = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.A.yaml")
    assert flat["head_sha"] == "abc123"      # multi-phase flat carries head_sha


def test_start_fanout_single_phase_unchanged(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    out, err, rc = run_engine("next.py", tmp_path / "d", "pr-1", proto, "start", "abc123",
                              env=engine_env)
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-fanout"
    b = next(x for x in action["branches"] if x["id"] == "B")
    assert b["substate"] == "draft" and b["workflow"] == "draft-agent"
    a = next(x for x in action["branches"] if x["id"] == "A")
    assert "substate" not in a
    work = _state_dir(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "draft" and cursor["state"] == "review"
    assert "head_sha" not in cursor                      # single-phase cursor omits head_sha
    sub = read_state_yaml(work / "subpipeline-mini/pr-1/B.draft.yaml")
    assert sub["state"] == "review" and sub["iteration"] == 1
    flat = read_state_yaml(work / "subpipeline-mini/pr-1/A.yaml")
    assert "head_sha" not in flat                        # single-phase flat omits head_sha


def _advance_substate(tmp_path, engine_env, instance, branch, substate, sha="abc123", n=0,
                      evidence=None):
    v = tmp_path / f"v-{branch}-{substate}-{n}.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / f"ev-{branch}-{substate}-{n}.json"
    ev.write_text(json.dumps(evidence) if evidence is not None else "{}")
    e = dict(engine_env)
    e.update(BRANCH=branch, SUBSTATE=substate, PHASE="review", PR_HEAD_SHA=sha, AGENT_RUN_ID="r")
    return run_engine("advance.py", tmp_path / f"adv-{branch}-{substate}-{n}", instance,
                      str(PROTO), v, ev, env=e)


def test_answer_finds_nested_gate_in_multiphase(tmp_path, engine_env):
    # Enter the fanout phase (seeds B.draft).
    run_engine("next.py", tmp_path / "d0", "pr-1", str(PROTO), "advance-phase", "abc123",
               env=engine_env, phase="review")
    # Advance B.draft → opens the clarify gate at review.B.clarify.yaml.
    # Provide a question in the draft evidence so the gate has questions to answer.
    out, err, rc = _advance_substate(tmp_path, engine_env, "pr-1", "B", "draft",
                                     evidence={"questions": [{"id": "q1", "text": "Which DB?"}]})
    assert rc == 0, err
    work = _state_dir(tmp_path, engine_env, suffix="-g")
    gate = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.clarify.yaml")
    assert gate["gates"]["state"] == "open"
    qid = gate["gates"]["questions"][0]["id"]

    # /answer with NO phase env — do_answer must derive phase="review" itself.
    e = dict(engine_env)
    e["ANSWER_BODY"] = f"/answer {qid}: postgres"
    e["ANSWER_ACTOR"] = "alice"
    e["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("next.py", tmp_path / "d1", "pr-1", str(PROTO), "answer", env=e)
    assert rc == 0, err

    work = _state_dir(tmp_path, engine_env, suffix="-a")
    gate = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.clarify.yaml")
    assert gate["gates"]["state"] == "answered"
    cursor = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.yaml")
    assert cursor["sub_state"] == "finalize"          # advanced to the next sub-state
    answers = json.loads((work / "multiphase-subpipeline/pr-1/review.B.clarify.answers.json").read_text())
    assert answers["answers"][qid] == "postgres"
    # The continue re-dispatch must carry the phase so the resumed leg uses qualified paths.
    assert "client_payload[phase]=review" in err


def test_full_subpipeline_leg_walk_to_join(tmp_path, engine_env):
    # Enter fanout (seeds B.draft + flat A).
    run_engine("next.py", tmp_path / "d0", "pr-1", str(PROTO), "advance-phase", "abc123",
               env=engine_env, phase="review")
    # Finish flat leg A.
    out, err, rc = _advance_substate(tmp_path, engine_env, "pr-1", "A", "", n=1)  # flat: no substate
    # NOTE: flat branches advance with BRANCH set + SUBSTATE empty.
    assert rc == 0, err

    # B: draft → opens clarify gate (evidence must carry a question so the gate has one to answer).
    assert _advance_substate(tmp_path, engine_env, "pr-1", "B", "draft", n=2,
                             evidence={"questions": [{"id": "q1", "text": "Which DB?"}]})[2] == 0
    work = _state_dir(tmp_path, engine_env, suffix="-1")
    qid = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.clarify.yaml"
                          )["gates"]["questions"][0]["id"]

    # Answer the gate → advances cursor to finalize.
    e = dict(engine_env, ANSWER_BODY=f"/answer {qid}: pg", ANSWER_ACTOR="al", PR_HEAD_SHA="abc123")
    assert run_engine("next.py", tmp_path / "d1", "pr-1", str(PROTO), "answer", env=e)[2] == 0

    # Finish B.finalize → leg done.
    assert _advance_substate(tmp_path, engine_env, "pr-1", "B", "finalize", n=3)[2] == 0
    work = _state_dir(tmp_path, engine_env, suffix="-2")
    assert read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.yaml")["state"] == "done"

    # Join: both legs done → instance joins.
    ej = dict(engine_env, PR_HEAD_SHA="abc123")
    assert run_engine("join.py", tmp_path / "j", "pr-1", str(PROTO), env=ej)[2] == 0
    work = _state_dir(tmp_path, engine_env, suffix="-3")
    assert read_state_yaml(work / "multiphase-subpipeline/pr-1/_instance.yaml").get("joined") is True
