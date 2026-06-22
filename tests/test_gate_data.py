import importlib, json, os, subprocess, sys
from conftest import ENGINE, FIXTURES, run_engine, run_check, read_state_yaml
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")
import pathlib


def _clone(tmp_path, engine_env):
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    return work


def test_open_gate_branch_scoped_with_questions(tmp_path, engine_env):
    # Arrange a checked-out state dir with an instance file present.
    dir_ = tmp_path / "dir"
    e = dict(engine_env)
    for k, v in e.items():
        os.environ[k] = v  # open_gate uses module-level git env via lib
    lib.STATE_REMOTE = e["STATE_REMOTE"]  # module-level constant captured at import
    lib.state_checkout(str(dir_))
    inst = lib.instance_file(str(dir_), "rev", "pr-1")
    os.makedirs(os.path.dirname(inst), exist_ok=True)
    lib.dump_yaml(inst, {"protocol": "rev", "instance": "pr-1", "joined": False})

    qs = [{"id": "q1", "text": "Which DB?"}, {"id": "q2", "text": "Sync or async?"}]
    lib.open_gate(str(dir_), "rev", "pr-1", str(FIXTURES / "subpipeline-mini/protocol.json"),
                  "clarify", "abc123", "1", branch="B", questions=qs)

    gf = read_state_yaml(lib.state_file(str(dir_), "rev", "pr-1", branch="B", substate="clarify"))
    assert gf["gates"]["state"] == "open"
    assert gf["gates"]["questions"] == qs


def test_advance_into_gate_opens_it(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    # draft → done, emitting questions in evidence.
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "draft.json"
    ev.write_text(json.dumps({"questions": [{"id": "q1", "text": "Which DB?"}]}))
    e = dict(engine_env); e.update(BRANCH="B", SUBSTATE="draft",
                                   PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir-adv", "pr-1", proto, v, ev, env=e)

    work = _clone(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "clarify"
    assert cursor["state"] == "review"      # leg NOT terminal; not joined
    gate = read_state_yaml(work / "subpipeline-mini/pr-1/B.clarify.yaml")
    assert gate["gates"]["state"] == "open"
    assert gate["gates"]["questions"][0]["id"] == "q1"


def _cov(tmp_path, questions, answers):
    doc = tmp_path / "doc.json"
    doc.write_text(json.dumps({"questions": questions, "answers": answers}))
    empty = tmp_path / "e.txt"; empty.write_text("")
    return run_check(FIXTURES / "subpipeline-mini/checks/answers-coverage.py", doc, empty, empty)


def test_answers_coverage_pass(tmp_path):
    r = _cov(tmp_path, [{"id": "q1"}, {"id": "q2"}], {"q1": "pg", "q2": "async"})
    assert r["pass"] is True


def test_answers_coverage_missing(tmp_path):
    r = _cov(tmp_path, [{"id": "q1"}, {"id": "q2"}], {"q1": "pg"})
    assert r["pass"] is False
    assert "q2" in r["feedback"]


def test_answers_coverage_empty_value(tmp_path):
    r = _cov(tmp_path, [{"id": "q1"}], {"q1": "   "})
    assert r["pass"] is False


# ── /answer command tests ────────────────────────────────────────────────────

def _seed_open_gate(tmp_path, engine_env, proto):
    """Drive start → draft done so the clarify gate is open with one question."""
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "draft.json"
    ev.write_text(json.dumps({"questions": [{"id": "q1", "text": "Which DB?"}]}))
    e = dict(engine_env); e.update(BRANCH="B", SUBSTATE="draft",
                                   PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir-adv", "pr-1", proto, v, ev, env=e)


def test_answer_completes_gate_and_advances(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    _seed_open_gate(tmp_path, engine_env, proto)
    e = dict(engine_env)
    e["ANSWER_BODY"] = "/answer q1: postgres"
    e["ANSWER_ACTOR"] = "alice"
    e["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("next.py", tmp_path / "dir2", "pr-1", proto, "answer", env=e)
    assert rc == 0, err

    work = _clone(tmp_path, engine_env)
    gate = read_state_yaml(work / "subpipeline-mini/pr-1/B.clarify.yaml")
    assert gate["gates"]["state"] == "answered"
    answers = json.loads((work / "subpipeline-mini/pr-1/B.clarify.answers.json").read_text())
    assert answers["answers"]["q1"] == "postgres"
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "finalize"


def test_answer_partial_keeps_gate_open(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    # Two questions; answer only one.
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "draft.json"
    ev.write_text(json.dumps({"questions": [{"id": "q1", "text": "A?"}, {"id": "q2", "text": "B?"}]}))
    e = dict(engine_env); e.update(BRANCH="B", SUBSTATE="draft",
                                   PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir-adv", "pr-1", proto, v, ev, env=e)

    e2 = dict(engine_env); e2["ANSWER_BODY"] = "/answer q1: x"; e2["ANSWER_ACTOR"] = "al"; e2["PR_HEAD_SHA"] = "abc123"
    run_engine("next.py", tmp_path / "dir2", "pr-1", proto, "answer", env=e2)
    work = _clone(tmp_path, engine_env)
    gate = read_state_yaml(work / "subpipeline-mini/pr-1/B.clarify.yaml")
    assert gate["gates"]["state"] == "open"   # still waiting on q2


def test_gated_leg_full_walk(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    _seed_open_gate(tmp_path, engine_env, proto)   # start + draft done + clarify gate open

    # Answer the gate.
    e = dict(engine_env)
    e["ANSWER_BODY"] = "/answer q1: postgres"
    e["ANSWER_ACTOR"] = "al"
    e["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("next.py", tmp_path / "dir2", "pr-1", proto, "answer", env=e)
    assert rc == 0, err

    # finalize resume → action must carry both inputs: answers and draft.
    out, err, rc = run_engine("next.py", tmp_path / "dir3", "pr-1", proto, "continue",
                              env=engine_env, branch="B", substate="finalize")
    assert rc == 0, err
    action = json.loads(out)
    names = {i["as"] for i in action.get("inputs", [])}
    assert "answers" in names and "draft" in names, f"missing inputs in action: {action}"

    # Advance finalize to done.
    v = tmp_path / "v.json"; v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "fin.json"; ev.write_text("{}")
    ef = dict(engine_env)
    ef.update(BRANCH="B", SUBSTATE="finalize", PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    out2, err2, rc2 = run_engine("advance.py", tmp_path / "dir-fin", "pr-1", proto, v, ev, env=ef)
    assert rc2 == 0, err2

    # Assert the leg cursor reached "done".
    work = _clone(tmp_path, engine_env)
    assert read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")["state"] == "done"
