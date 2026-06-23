# tests/test_recursive_sequencer.py
import json, os, pathlib, subprocess, sys, shutil
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
sys.path.insert(0, str(ROOT / "tests"))
from conftest import run_engine, read_state_yaml, FIXTURES  # noqa: E402


def _run_next(state_dir, proto, instance, cmd, env, **coords):
    e = dict(env)
    for k in ("PHASE", "BRANCH", "SUBSTATE"):
        e.pop(k, None)
    for k, v in coords.items():
        e[k.upper()] = v
    return subprocess.run(["python3", str(ENGINE / "next.py"), str(state_dir), instance,
                           str(proto), cmd], text=True, capture_output=True, env=e)


def _state_dir(tmp_path, engine_env, suffix=""):
    """Clone the fake origin so we can read pushed state files back."""
    work = tmp_path / f"work{suffix}"
    if work.exists():
        shutil.rmtree(work)
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    return work


def test_enter_top_fanout_seeds_branches(engine_env, tmp_path):
    sd = tmp_path / "state"; sd.mkdir()
    proto = ROOT / "tests/fixtures/subpipeline-mini/protocol.json"
    r = _run_next(sd, proto, "pr-1", "start", engine_env)
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-fanout"
    # flat branch A: no substate; sub-pipeline branch B: substate=draft
    by = {b["id"]: b for b in action["branches"]}
    assert "substate" not in by["A"]
    assert by["B"]["substate"] == "draft"
    # cursor + first sub-state files written under the instance dir
    base = sd / "subpipeline-mini" / "pr-1"
    assert (base / "B.yaml").exists() and (base / "B.draft.yaml").exists()


def _seed_open_gate(tmp_path, engine_env, proto):
    """Drive start → draft done so clarify gate is open with one question.
    Lifted from test_gate_data.py._seed_open_gate for /answer regression test."""
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "draft.json"
    ev.write_text(json.dumps({"questions": [{"id": "q1", "text": "Which DB?"}]}))
    e = dict(engine_env); e.update(BRANCH="B", SUBSTATE="draft",
                                   PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir-adv", "pr-1", proto, v, ev, env=e)


def test_answer_via_path_advances_cursor_to_finalize(engine_env, tmp_path):
    """Regression test for Task 8: _find_open_gate returns a node-path and
    do_answer uses it. After a full /answer the branch B cursor sub_state
    must advance to 'finalize' — byte-identical to the depth-<=3 behavior."""
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    _seed_open_gate(tmp_path, engine_env, proto)

    e = dict(engine_env)
    e["ANSWER_BODY"] = "/answer q1: postgres"
    e["ANSWER_ACTOR"] = "alice"
    e["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("next.py", tmp_path / "dir2", "pr-1", proto, "answer", env=e)
    assert rc == 0, err

    work = _state_dir(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "finalize", f"cursor should advance to finalize: {cursor}"


def test_advance_subpipeline_draft_to_gate(engine_env, tmp_path):
    """Advancing branch B / draft done → cursor sub_state==clarify + gate open.

    This is a behavior-identical guard for the advance_node/complete_sequence
    refactor (Task 6). Reproduces test_advance_draft_moves_cursor_to_clarify
    from test_subpipeline.py, driven from test_recursive_sequencer.py to pin
    the advance path under the new shared function pair.
    """
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    # Seed initial state via next.py start (uses a separate workdir to avoid collision).
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)

    # Build passing verdicts + evidence (with questions for the gate to consume).
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))
    evid = tmp_path / "evidence.json"
    evid.write_text(json.dumps({"questions": [{"id": "q1", "text": "Which DB?"}]}))

    e = dict(engine_env)
    e.update(BRANCH="B", SUBSTATE="draft", PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    out, err, rc = run_engine("advance.py", tmp_path / "dir-adv", "pr-1", proto, verdicts, evid, env=e)
    assert rc == 0, err

    # Clone origin and read back the pushed state files.
    work = _state_dir(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "clarify", f"cursor: {cursor}"
    assert cursor.get("state") == "review", f"leg should still be in flight: {cursor}"
    gate = read_state_yaml(work / "subpipeline-mini/pr-1/B.clarify.yaml")
    assert gate["gates"]["state"] == "open", f"gate: {gate}"
    assert gate["gates"]["questions"][0]["id"] == "q1", f"gate questions: {gate}"
