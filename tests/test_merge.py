import importlib, json, os, subprocess, sys
from conftest import ENGINE, FIXTURES, run_engine, read_state_yaml
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")


def test_append_hook_concatenates(tmp_path):
    work = tmp_path / "w"
    (work / "inputs").mkdir(parents=True)
    (work / "inputs/a.json").write_text(json.dumps({"summary": "AOUT"}))
    (work / "inputs/b.json").write_text(json.dumps({"summary": "BOUT"}))
    hook = FIXTURES / "subpipeline-mini/publish/append-outputs.py"
    r = subprocess.run([str(hook), str(work), "pr-1"], text=True, capture_output=True)
    out = json.loads(r.stdout)
    assert out["conclusion"] == "success"
    assert "AOUT" in out["summary"] and "BOUT" in out["summary"]


def test_run_merge_hook(tmp_path, engine_env):
    # Lay down a state dir with both branch outputs persisted.
    dir_ = tmp_path / "dir"
    for k, v in engine_env.items():
        os.environ[k] = v
    lib.STATE_REMOTE = engine_env["STATE_REMOTE"]
    lib.state_checkout(str(dir_))
    base = f"{dir_}/subpipeline-mini/pr-1"
    os.makedirs(base, exist_ok=True)
    # A flat leg output + B sub-pipeline leg output (finalize).
    open(f"{base}/A.evidence.json", "w").write(json.dumps({"summary": "FROM-A"}))
    open(f"{base}/B.finalize.evidence.json", "w").write(json.dumps({"summary": "FROM-B"}))

    proto_path = str(FIXTURES / "subpipeline-mini/protocol.json")
    proto = json.load(open(proto_path))
    merge_state = lib.state_by_id(proto, "combine")
    res = lib.run_merge_hook(str(dir_), "subpipeline-mini", "pr-1", proto_path, merge_state)
    assert res["conclusion"] == "success"
    assert "FROM-A" in res["summary"] and "FROM-B" in res["summary"]


def test_join_runs_merge_then_finalizes(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    # Minimal path: make B a 1-step leg for THIS test by finishing draft as the
    # leg output is not needed; we just need both cursors `done`. Drive directly:
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    passv = tmp_path / "v.json"; passv.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))

    # Force both branch cursors done + persist leg outputs by writing state directly
    # through the engine: finish A, then walk B draft->(gate auto-answered)->finalize.
    def adv(branch, substate, summary, questions=None):
        ev = tmp_path / f"{branch}-{substate or 'flat'}.json"
        ev.write_text(json.dumps({"summary": summary, "questions": questions or []}))
        e = dict(engine_env); e.update(BRANCH=branch, PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
        if substate:
            e["SUBSTATE"] = substate
        run_engine("advance.py", tmp_path / f"dir-adv-{branch}-{substate or 'flat'}", "pr-1", proto, passv, ev, env=e)

    adv("A", None, "FROM-A")
    adv("B", "draft", "DRAFTOUT", questions=[{"id": "q1", "text": "Q?"}])
    ea = dict(engine_env); ea["ANSWER_BODY"] = "/answer q1: yes"; ea["ANSWER_ACTOR"] = "al"; ea["PR_HEAD_SHA"] = "abc123"
    run_engine("next.py", tmp_path / "dir-answer", "pr-1", proto, "answer", env=ea)
    adv("B", "finalize", "FROM-B")

    # Now both legs done → run join.
    ej = dict(engine_env); ej["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("join.py", tmp_path / "dir-join", "pr-1", proto, env=ej)
    assert rc == 0, err
    work = tmp_path / "work"; subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    inst = read_state_yaml(work / "subpipeline-mini/pr-1/_instance.yaml")
    assert inst.get("joined") is True
    # The merge ran: instance cursor parked at the merge state.
    assert inst.get("phase") == "combine"
