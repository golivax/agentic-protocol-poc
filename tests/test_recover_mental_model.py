"""pytest e2e for the recover-mental-model-stub protocol.

Drives the engine through the full pipeline under ENGINE_LOCAL:
  start → advance summary(flat) ∥ rationale(draft→gate→finalize) → join → combine → done.

Also tests all 4 checks for pass/fail behaviour.
"""
import importlib, json, os, subprocess, sys
from pathlib import Path
from conftest import ENGINE, PROTOCOLS, run_engine, run_check, read_state_yaml

sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")

PROTO_DIR = PROTOCOLS / "recover-mental-model-stub"
PROTO = PROTO_DIR / "protocol.json"
CHECKS = PROTO_DIR / "checks"


# ─── check unit tests ────────────────────────────────────────────────────────

def _run(check_name, evidence_dict, tmp_path):
    ev = tmp_path / f"{check_name}.json"
    ev.write_text(json.dumps(evidence_dict))
    empty = tmp_path / "empty.txt"; empty.write_text("")
    return run_check(CHECKS / f"{check_name}.py", ev, empty, empty)


def test_summary_present_pass(tmp_path):
    r = _run("summary-present", {"summary": "This PR changes X"}, tmp_path)
    assert r["pass"] is True


def test_summary_present_fail_empty(tmp_path):
    r = _run("summary-present", {"summary": "  "}, tmp_path)
    assert r["pass"] is False
    assert "summary" in r["feedback"]


def test_summary_present_fail_missing(tmp_path):
    r = _run("summary-present", {}, tmp_path)
    assert r["pass"] is False


def test_questions_present_pass(tmp_path):
    r = _run("questions-present",
             {"questions": [{"id": "q1", "text": "Why?"}]}, tmp_path)
    assert r["pass"] is True


def test_questions_present_fail_empty_list(tmp_path):
    r = _run("questions-present", {"questions": []}, tmp_path)
    assert r["pass"] is False


def test_questions_present_fail_missing_text(tmp_path):
    r = _run("questions-present",
             {"questions": [{"id": "q1", "text": ""}]}, tmp_path)
    assert r["pass"] is False
    assert "text" in r["feedback"]


def test_questions_present_fail_missing_id(tmp_path):
    r = _run("questions-present",
             {"questions": [{"id": "", "text": "Why?"}]}, tmp_path)
    assert r["pass"] is False
    assert "id" in r["feedback"]


def test_rationale_present_pass(tmp_path):
    r = _run("rationale-present",
             {"rationale": "Because reasons, the change is safe."}, tmp_path)
    assert r["pass"] is True


def test_rationale_present_fail_empty(tmp_path):
    r = _run("rationale-present", {"rationale": ""}, tmp_path)
    assert r["pass"] is False


def test_rationale_present_fail_missing(tmp_path):
    r = _run("rationale-present", {}, tmp_path)
    assert r["pass"] is False


def test_answers_coverage_pass(tmp_path):
    r = _run("answers-coverage",
             {"questions": [{"id": "q1"}], "answers": {"q1": "because reasons"}}, tmp_path)
    assert r["pass"] is True


def test_answers_coverage_fail_missing_answer(tmp_path):
    r = _run("answers-coverage",
             {"questions": [{"id": "q1"}, {"id": "q2"}], "answers": {"q1": "yes"}}, tmp_path)
    assert r["pass"] is False
    assert "q2" in r["feedback"]


# ─── full e2e pipeline ───────────────────────────────────────────────────────

def test_full_pipeline(tmp_path, engine_env):
    """End-to-end: start → summary(flat) ∥ rationale(draft→gate→finalize) → join → combine → done."""
    # A single synthetic passing verdict is required: empty results → decide() returns
    # "iterate" (not "done"). One passing result drives decide() to "done".
    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))

    def adv(branch, substate, evidence_dict):
        ev = tmp_path / f"{branch}-{substate or 'flat'}.json"
        ev.write_text(json.dumps(evidence_dict))
        e = dict(engine_env)
        e["PR_HEAD_SHA"] = "abc123"
        e["AGENT_RUN_ID"] = "r"
        # Unified NODE_PATH coordinate: recover-mental-model-stub is single-phase
        # (fanout `recover`), so the tree path is recover.<branch>[.<substate>].
        node = "recover." + branch + (f".{substate}" if substate else "")
        e["NODE_PATH"] = node
        # Each advance.py call needs its own workdir (git-clone into non-empty fails).
        out, err, rc = run_engine(
            "advance.py",
            tmp_path / f"dir-adv-{branch}-{substate or 'flat'}",
            "pr-1", PROTO, passv, ev, env=e,
        )
        assert rc == 0, f"advance {branch}/{substate} failed:\n{err}"

    def clone():
        work = tmp_path / f"work-{clone.n}"
        clone.n += 1
        subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
        return work
    clone.n = 0

    # 1. Seed instance + branch state files.
    out, err, rc = run_engine("next.py", tmp_path / "dir-next", "pr-1", PROTO, "start", "abc123",
                              env=engine_env)
    assert rc == 0, f"next start failed:\n{err}"

    # 2. Advance the flat summary branch to done.
    adv("summary", None, {"summary": "This PR changes X"})

    # 3. Advance rationale/draft to done (emits questions → gate opens).
    adv("rationale", "draft", {"questions": [{"id": "q1", "text": "Why?"}]})

    # Verify the gate is now open.
    w = clone()
    cursor = read_state_yaml(w / "recover-mental-model-stub/pr-1/rationale.yaml")
    assert cursor["sub_state"] == "clarify", f"expected sub_state=clarify, got {cursor}"

    # 4. Answer the gate via /answer command.
    ea = dict(engine_env)
    ea["ANSWER_BODY"] = "/answer q1: because reasons"
    ea["ANSWER_ACTOR"] = "alice"
    ea["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("next.py", tmp_path / "dir-answer", "pr-1", PROTO, "answer",
                              env=ea)
    assert rc == 0, f"answer command failed:\n{err}"

    # Verify cursor advanced to finalize.
    w2 = clone()
    cursor2 = read_state_yaml(w2 / "recover-mental-model-stub/pr-1/rationale.yaml")
    assert cursor2["sub_state"] == "finalize", f"expected sub_state=finalize, got {cursor2}"

    # 5. Advance rationale/finalize to done.
    adv("rationale", "finalize", {"rationale": "Because reasons, the change is safe."})

    # 6. Run join.py — both legs done. Join sets the cursor to combine and
    # dispatches protocol-continue path=combine (it does NOT run the merge inline).
    ej = dict(engine_env)
    ej["PR_HEAD_SHA"] = "abc123"
    ej["PR"] = "1"
    out, err, rc = run_engine("join.py", tmp_path / "dir-join", "pr-1", PROTO, env=ej)
    assert rc == 0, f"join failed:\n{err}"
    jcombined = out + err
    assert "event_type=protocol-continue" in jcombined and \
        "client_payload[path]=combine" in jcombined, \
        f"expected join to dispatch protocol-continue path=combine, got:\n{jcombined}"

    # 7. Join side: _instance.yaml shows joined=True and the cursor parked at combine
    #    (the merge has NOT executed yet — only the dispatch has happened).
    w3 = clone()
    inst = read_state_yaml(w3 / "recover-mental-model-stub/pr-1/_instance.yaml")
    assert inst.get("joined") is True, f"expected joined=True, got {inst}"
    assert inst.get("phase") == "combine", f"expected phase=combine, got {inst.get('phase')!r}"

    # 8. next.py continue NODE_PATH=combine ACTUALLY runs the merge reduce hook
    #    (append-rationale) which posts the combined summary + rationale → done.
    ec = dict(engine_env)
    ec["PR_HEAD_SHA"] = "abc123"
    ec["PR"] = "1"
    ec["NODE_PATH"] = "combine"
    out2, err2, rc2 = run_engine("next.py", tmp_path / "dir-merge", "pr-1", PROTO, "continue", env=ec)
    assert rc2 == 0, f"merge continue failed:\n{err2}"
    mcombined = out2 + err2
    # The reduce hook actually executed: its returned summary rides the Combined
    # check-run + the 🧬 combine comment. (The hook's own leg-text comment is run in
    # a captured subprocess inside run_merge_hook, so only its verdict surfaces here.)
    assert json.loads(out2).get("reason") == "merge:combine"
    assert "title=Combined" in mcombined and \
        "Recovered mental model: summary + rationale posted." in mcombined, (
        f"expected merge reduce hook to run + finalize, got:\n{mcombined}"
    )
    # Cursor finalized to done after the merge.
    assert "Combine → ✅ done" in mcombined or "→ ✅ done" in mcombined


def test_run_merge_hook(tmp_path, engine_env):
    """Directly exercise lib.run_merge_hook for the combine state.

    Resolution (is_multiphase=False, single fanout phase):
      summary  → flat branch  → <dir>/recover-mental-model-stub/pr-1/summary.evidence.json
      rationale → sub-pipeline → last substate=finalize →
                  <dir>/recover-mental-model-stub/pr-1/rationale.finalize.evidence.json
    """
    # Wire lib.STATE_REMOTE so state_checkout can clone the bare origin.
    for k, v in engine_env.items():
        os.environ[k] = v
    lib.STATE_REMOTE = engine_env["STATE_REMOTE"]

    dir_ = str(tmp_path / "dir")
    lib.state_checkout(dir_)

    base = os.path.join(dir_, "recover-mental-model-stub", "pr-1")
    os.makedirs(base, exist_ok=True)

    # Flat summary branch leg output.
    with open(os.path.join(base, "summary.evidence.json"), "w") as f:
        json.dump({"summary": "SUMMARY-TEXT"}, f)
    # Sub-pipeline rationale branch leg output (last substate = finalize).
    with open(os.path.join(base, "rationale.finalize.evidence.json"), "w") as f:
        json.dump({"rationale": "RATIONALE-TEXT"}, f)

    proto_path = str(PROTO)
    proto = json.load(open(proto_path))
    merge_state = lib.state_by_id(proto, "combine")
    assert merge_state is not None, "combine state not found in protocol"

    res = lib.run_merge_hook(dir_, "recover-mental-model-stub", "pr-1", proto_path, merge_state)
    assert res["conclusion"] == "success", (
        f"Expected conclusion='success', got {res!r}"
    )
    # The hook returns a fixed summary string on success — confirm it's non-empty.
    assert res.get("summary"), f"Expected non-empty summary, got {res!r}"

def test_answer_then_continue_dispatches_finalize(tmp_path, engine_env):
    """Regression (live-only bug): after /answer advances a gated sub-pipeline,
    `next.py continue` for the next sub-state must emit run-agent, NOT halt.
    do_answer hardcoded the leg life-state as "review" (which only matched the
    subpipeline-mini fixture); this protocol's fanout is "recover", so the seeded
    `state` mismatched the life_state and continue halted ("instance is terminal")
    — finalize never dispatched live. Also asserts the resolved inputs ride along."""
    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    run_engine("next.py", tmp_path / "dir-next", "pr-1", PROTO, "start", "abc123", env=engine_env)
    ev = tmp_path / "draft.json"
    ev.write_text(json.dumps({"questions": [{"id": "q1", "text": "Why?"}]}))
    e = dict(engine_env)
    e.update(NODE_PATH="recover.rationale.draft", PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir-adv", "pr-1", PROTO, passv, ev, env=e)
    ea = dict(engine_env)
    ea.update(ANSWER_BODY="/answer q1: yes", ANSWER_ACTOR="al", PR_HEAD_SHA="abc123")
    run_engine("next.py", tmp_path / "dir-answer", "pr-1", PROTO, "answer", env=ea)

    ec = dict(engine_env)
    ec.update(NODE_PATH="recover.rationale.finalize")
    out, err, rc = run_engine("next.py", tmp_path / "dir-cont", "pr-1", PROTO, "continue", env=ec)
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-agent", f"expected run-agent, got {action}"
    assert action.get("path") == "recover.rationale.finalize"
    names = {i["as"] for i in action.get("inputs", [])}
    assert {"answers", "draft"} <= names, f"inputs missing: {names}"
