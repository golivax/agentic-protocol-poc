import importlib, json, os, subprocess, sys
from pathlib import Path
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


def test_full_pipeline_with_merge(tmp_path, engine_env):
    """End-to-end: start → A(flat) ∥ B(draft→gate→finalize) → join → combine → done."""
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir-next", "pr-1", proto, "start", "abc123", env=engine_env)
    passv = tmp_path / "v.json"; passv.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))

    def adv(branch, substate, summary, questions=None):
        ev = tmp_path / f"{branch}-{substate or 'flat'}.json"
        ev.write_text(json.dumps({"summary": summary, "questions": questions or []}))
        e = dict(engine_env); e.update(BRANCH=branch, PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
        if substate:
            e["SUBSTATE"] = substate
        run_engine("advance.py", tmp_path / f"dir-adv-{branch}-{substate or 'flat'}", "pr-1", proto, passv, ev, env=e)

    adv("A", None, "ALPHA")
    adv("B", "draft", "DRAFTOUT", questions=[{"id": "q1", "text": "Q?"}])
    ea = dict(engine_env); ea["ANSWER_BODY"] = "/answer q1: yes"; ea["ANSWER_ACTOR"] = "al"; ea["PR_HEAD_SHA"] = "abc123"
    run_engine("next.py", tmp_path / "dir-answer", "pr-1", proto, "answer", env=ea)
    adv("B", "finalize", "BETA")

    ej = dict(engine_env); ej["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("join.py", tmp_path / "dir-join", "pr-1", proto, env=ej)
    assert rc == 0, err

    work = tmp_path / "work"; subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    inst = read_state_yaml(work / "subpipeline-mini/pr-1/_instance.yaml")
    assert inst.get("joined") is True
    assert inst.get("phase") == "combine"


def _make_flat_protocol(tmp_path: Path, join_next: str, extra_states=None) -> Path:
    """Build a minimal flat two-branch fanout protocol with no checks.

    Both branches (A, B) are flat (no sub-pipeline, no gates), so driving them
    to done requires only a single advance.py call each.  The join state's
    `next` is set to ``join_next``.  Any additional states (e.g. an agent
    combine) are appended via ``extra_states``.
    """
    proto = {
        "name": "flat-mini",
        "version": "0.1.0",
        "triggers": [],
        "states": [
            {
                "id": "review",
                "kind": "fanout",
                "branches": [
                    {
                        "id": "A",
                        "workflow": "a-agent",
                        "evidence": "e.json",
                        "max_iterations": 2,
                        "checks": [],
                        "publish": "noop",
                    },
                    {
                        "id": "B",
                        "workflow": "b-agent",
                        "evidence": "e.json",
                        "max_iterations": 2,
                        "checks": [],
                        "publish": "noop",
                    },
                ],
                "next": "join",
            },
            {"id": "join", "kind": "join", "of": "review", "next": join_next},
        ],
    }
    if extra_states:
        proto["states"].extend(extra_states)
    pf = tmp_path / "proto.json"
    pf.write_text(json.dumps(proto))
    return pf


def test_join_dispatches_agent_combine(tmp_path, engine_env):
    """Mode 2: join.next is a kind:'agent' state → join advances cursor to it + dispatches.

    Drive sequence (flat legs — no sub-pipeline, no gate, no checks):
      1. next.py start          → seeds _instance.yaml + branch state files
      2. advance.py BRANCH=A    → drives A to done (flat, no checks)
      3. advance.py BRANCH=B    → drives B to done (flat, no checks)
      4. join.py                → all done → should advance phase to combine2
    Assert: _instance.yaml.phase == "combine2" (agent-combine cursor advanced)
    """
    # Protocol: join.next → combine2 (kind:agent)
    pf = _make_flat_protocol(
        tmp_path,
        join_next="combine2",
        extra_states=[
            {
                "id": "combine2",
                "kind": "agent",
                "workflow": "c-agent",
                "evidence": "e.json",
                "max_iterations": 1,
                "inputs": [{"from": "A", "as": "a"}, {"from": "B", "as": "b"}],
                "checks": [],
                "next": "done",
            }
        ],
    )

    # All-pass verdicts: one synthetic passing result drives decide() to "done".
    # (Empty results → decide() returns "iterate"; a single pass → "done".)
    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))

    # Minimal evidence file (advance reads it for persist_output; content irrelevant).
    ev = tmp_path / "e.json"
    ev.write_text(json.dumps({"summary": "ok"}))

    # Step 1: seed the instance + branch state files.
    run_engine("next.py", tmp_path / "dir-next", "pr-1", pf, "start", "abc123", env=engine_env)

    # Step 2: drive A to done.
    # PHASE=review is required because the protocol is multiphase (review+combine2),
    # so advance.py writes review.A.yaml which join.py will find via phase_for_path.
    e_a = dict(engine_env)
    e_a["BRANCH"] = "A"
    e_a["PHASE"] = "review"
    e_a["PR_HEAD_SHA"] = "abc123"
    e_a["AGENT_RUN_ID"] = "r1"
    out, err, rc = run_engine(
        "advance.py", tmp_path / "dir-adv-a", "pr-1", pf, passv, ev, env=e_a
    )
    assert rc == 0, f"advance A failed:\n{err}"

    # Step 3: drive B to done.
    e_b = dict(engine_env)
    e_b["BRANCH"] = "B"
    e_b["PHASE"] = "review"
    e_b["PR_HEAD_SHA"] = "abc123"
    e_b["AGENT_RUN_ID"] = "r2"
    out, err, rc = run_engine(
        "advance.py", tmp_path / "dir-adv-b", "pr-1", pf, passv, ev, env=e_b
    )
    assert rc == 0, f"advance B failed:\n{err}"

    # Step 4: run join — all branches done, join.next is kind:agent → mode 2.
    ej = dict(engine_env)
    ej["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("join.py", tmp_path / "dir-join", "pr-1", pf, env=ej)
    assert rc == 0, f"join failed:\n{err}"

    # Assert: instance cursor advanced to the agent-combine state.
    work = tmp_path / "work-m2"
    subprocess.run(
        ["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True
    )
    inst = read_state_yaml(work / "flat-mini/pr-1/_instance.yaml")
    assert inst.get("phase") == "combine2", (
        f"Expected phase='combine2', got phase={inst.get('phase')!r}; "
        f"joined={inst.get('joined')!r}"
    )


def test_join_mode3_publish_only_finalizes(tmp_path, engine_env):
    """Mode 3 regression: join.next == done → plain finalize (joined=True, no phase advance).

    Drive sequence (flat legs — no sub-pipeline, no gate, no checks):
      1. next.py start          → seeds _instance.yaml + branch state files
      2. advance.py BRANCH=A    → drives A to done (one synthetic pass verdict)
      3. advance.py BRANCH=B    → drives B to done (one synthetic pass verdict)
      4. join.py                → all done → should plain-finalize
    Assert: joined == True AND phase is None or absent (no post-join cursor advance).
    """
    # Protocol: join.next → done (plain finalize, mode 3)
    pf = _make_flat_protocol(tmp_path, join_next="done")

    # One passing verdict so decide() yields "done" (empty → "iterate").
    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))

    ev = tmp_path / "e.json"
    ev.write_text(json.dumps({"summary": "ok"}))

    # Step 1: seed the instance + branch state files.
    run_engine("next.py", tmp_path / "dir-next", "pr-1", pf, "start", "abc123", env=engine_env)

    # Step 2: drive A to done.
    e_a = dict(engine_env)
    e_a["BRANCH"] = "A"
    e_a["PR_HEAD_SHA"] = "abc123"
    e_a["AGENT_RUN_ID"] = "r1"
    out, err, rc = run_engine(
        "advance.py", tmp_path / "dir-adv-a", "pr-1", pf, passv, ev, env=e_a
    )
    assert rc == 0, f"advance A failed:\n{err}"

    # Step 3: drive B to done.
    e_b = dict(engine_env)
    e_b["BRANCH"] = "B"
    e_b["PR_HEAD_SHA"] = "abc123"
    e_b["AGENT_RUN_ID"] = "r2"
    out, err, rc = run_engine(
        "advance.py", tmp_path / "dir-adv-b", "pr-1", pf, passv, ev, env=e_b
    )
    assert rc == 0, f"advance B failed:\n{err}"

    # Step 4: run join — all branches done, join.next == "done" → mode 3 plain finalize.
    ej = dict(engine_env)
    ej["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("join.py", tmp_path / "dir-join", "pr-1", pf, env=ej)
    assert rc == 0, f"join failed:\n{err}"

    # Assert: plain finalize — joined but no post-join phase advance.
    work = tmp_path / "work-m3"
    subprocess.run(
        ["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True
    )
    inst = read_state_yaml(work / "flat-mini/pr-1/_instance.yaml")
    assert inst.get("joined") is True, f"Expected joined=True, got {inst.get('joined')!r}"
    # Phase must not have been advanced to a post-join state.
    assert inst.get("phase") in (None, "review"), (
        f"Expected phase to be None/review (no advance), got {inst.get('phase')!r}"
    )
