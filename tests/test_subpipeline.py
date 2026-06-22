import json, shutil, subprocess
from conftest import run_engine, read_state_yaml, FIXTURES, ENGINE  # noqa: F401  (ensures sys.path includes tests/)
import sys, importlib, json, pathlib
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


SUBPIPE_PROTO = {
    "name": "rev",
    "states": [
        {"id": "review", "kind": "fanout", "branches": [
            {"id": "A", "workflow": "a-agent", "max_iterations": 2},
            {"id": "B", "states": [
                {"id": "draft", "kind": "agent", "workflow": "draft-agent", "max_iterations": 2},
                {"id": "finalize", "kind": "agent", "workflow": "final-agent", "max_iterations": 2},
            ]},
        ]},
        {"id": "join", "kind": "join", "of": "review", "next": "done"},
    ],
}


def test_branch_config():
    assert lib.branch_config(SUBPIPE_PROTO, "A")["workflow"] == "a-agent"
    assert lib.branch_config(SUBPIPE_PROTO, "B")["id"] == "B"
    assert lib.branch_config(SUBPIPE_PROTO, "missing") is None


def test_is_subpipeline_branch():
    assert lib.is_subpipeline_branch(lib.branch_config(SUBPIPE_PROTO, "B")) is True
    assert lib.is_subpipeline_branch(lib.branch_config(SUBPIPE_PROTO, "A")) is False


def test_branch_substates():
    ids = [s["id"] for s in lib.branch_substates(SUBPIPE_PROTO, "B")]
    assert ids == ["draft", "finalize"]
    assert lib.branch_substates(SUBPIPE_PROTO, "A") == []


def test_next_substate_id():
    assert lib.next_substate_id(SUBPIPE_PROTO, "B", "draft") == "finalize"
    assert lib.next_substate_id(SUBPIPE_PROTO, "B", "finalize") is None


def test_resolve_agent_unit_substate():
    u = lib.resolve_agent_unit(SUBPIPE_PROTO, phase="review", branch="B", substate="finalize")
    assert u["agent_state"] == "finalize"
    assert u["max_iterations"] == 2
    assert u["life_state"] == "review"


def test_resolve_agent_unit_flat_branch_unchanged():
    u = lib.resolve_agent_unit(SUBPIPE_PROTO, phase="review", branch="A")
    assert u["agent_state"] == "A"
    assert u["life_state"] == "review"


def test_subpipeline_mini_loads():
    proto = json.loads((FIXTURES / "subpipeline-mini/protocol.json").read_text())
    assert proto["name"] == "subpipeline-mini"
    b = next(x for x in proto["states"][0]["branches"] if x["id"] == "B")
    assert [s["id"] for s in b["states"]] == ["draft", "finalize"]
    chk = FIXTURES / "subpipeline-mini/checks/always-pass.py"
    assert chk.stat().st_mode & 0o111  # executable


def _state_dir(tmp_path, engine_env, suffix=""):
    """Clone the fake origin so we can read pushed state files back."""
    work = tmp_path / f"work{suffix}"
    if work.exists():
        shutil.rmtree(work)
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    return work


_advance_call_count = {}


def _advance(tmp_path, engine_env, instance, branch, substate, proto, sha="abc123"):
    """Run advance.py for a leg with an all-pass verdict + empty evidence.

    Uses a unique workdir per (branch, substate) call to avoid git-clone collisions
    with the next.py call that already populated tmp_path / "dir".
    """
    key = f"{branch}-{substate}"
    _advance_call_count[key] = _advance_call_count.get(key, 0) + 1
    verdicts = tmp_path / f"verdicts-{branch}-{substate}.json"
    verdicts.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    evid = tmp_path / "evidence.json"
    evid.write_text("{}")
    e = dict(engine_env)
    e["BRANCH"] = branch
    e["SUBSTATE"] = substate
    e["PR_HEAD_SHA"] = sha
    e["AGENT_RUN_ID"] = "run-1"
    workdir = tmp_path / f"adv-{branch}-{substate}-{_advance_call_count[key]}"
    out, err, rc = run_engine("advance.py", workdir, instance, proto,
                              verdicts, evid, env=e)
    return out, err, rc


def test_advance_draft_moves_cursor_to_finalize(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    out, err, rc = _advance(tmp_path, engine_env, "pr-1", "B", "draft", proto)
    assert rc == 0, err

    work = _state_dir(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "finalize"
    assert cursor.get("state") == "review"   # leg still in flight
    fin = read_state_yaml(work / "subpipeline-mini/pr-1/B.finalize.yaml")
    assert fin["state"] == "review" and fin["iteration"] == 1


def test_advance_finalize_marks_leg_done(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    _advance(tmp_path, engine_env, "pr-1", "B", "draft", proto)
    out, err, rc = _advance(tmp_path, engine_env, "pr-1", "B", "finalize", proto)
    assert rc == 0, err
    work = _state_dir(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["state"] == "done"


def test_start_seeds_subpipeline_first_substate(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    workdir = tmp_path / "dir"
    out, err, rc = run_engine("next.py", workdir, "pr-1", proto, "start", "abc123", env=engine_env)
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-fanout"
    b = next(x for x in action["branches"] if x["id"] == "B")
    assert b["substate"] == "draft"
    assert b["workflow"] == "draft-agent"
    a = next(x for x in action["branches"] if x["id"] == "A")
    assert "substate" not in a  # flat branch unchanged

    # State files: branch cursor carries sub_state; sub-state file seeded.
    work = _state_dir(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "draft"
    assert cursor["state"] == "review"
    sub = read_state_yaml(work / "subpipeline-mini/pr-1/B.draft.yaml")
    assert sub["state"] == "review" and sub["iteration"] == 1


def test_advance_substate_in_check_run_name(tmp_path, engine_env):
    """Verify that sub-pipeline advance includes sub-state in check-run name."""
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    out, err, rc = _advance(tmp_path, engine_env, "pr-1", "B", "draft", proto)
    assert rc == 0, err
    # set_check_run emits name to stderr under ENGINE_LOCAL=1
    assert "check-run subpipeline-mini/B/draft" in err, f"Expected sub-state in check-run name. stderr: {err}"


def test_continue_resumes_substate(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    # Resume the draft sub-state explicitly (use a unique workdir to avoid git-clone collision).
    out, err, rc = run_engine("next.py", tmp_path / "dir2", "pr-1", proto, "continue",
                              env=engine_env, branch="B", substate="draft")
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-agent"
    assert "phase" not in action
    assert action.get("substate") == "draft"


_run_join_call_count = {}


def _run_join(tmp_path, engine_env, instance, proto, sha="abc123"):
    key = instance
    _run_join_call_count[key] = _run_join_call_count.get(key, 0) + 1
    e = dict(engine_env)
    e["PR_HEAD_SHA"] = sha
    workdir = tmp_path / f"join-{instance}-{_run_join_call_count[key]}"
    return run_engine("join.py", workdir, instance, proto, env=e)


def test_join_waits_for_subpipeline_then_joins(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir-start", "pr-1", proto, "start", "abc123", env=engine_env)
    # Finish flat leg A.
    va = tmp_path / "va.json"; va.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "ev.json"; ev.write_text("{}")
    ea = dict(engine_env); ea.update(BRANCH="A", PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir-adv-a", "pr-1", proto, va, ev, env=ea)

    # B: draft done, finalize NOT yet → join must wait.
    _advance(tmp_path, engine_env, "pr-1", "B", "draft", proto)
    _run_join(tmp_path, engine_env, "pr-1", proto)
    work = _state_dir(tmp_path, engine_env, suffix="-1")
    inst = read_state_yaml(work / "subpipeline-mini/pr-1/_instance.yaml")
    assert not inst.get("joined")   # still waiting on B.finalize

    # Finish B.
    _advance(tmp_path, engine_env, "pr-1", "B", "finalize", proto)
    _run_join(tmp_path, engine_env, "pr-1", proto)
    work = _state_dir(tmp_path, engine_env, suffix="-2")
    inst = read_state_yaml(work / "subpipeline-mini/pr-1/_instance.yaml")
    assert inst.get("joined") is True
