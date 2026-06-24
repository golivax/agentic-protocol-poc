"""test_unified_recover_e2e.py — Task 8: full e2e oracle walk for recover-mental-model-stub via NODE_PATH.

Walk:
  start
  → advance NODE_PATH=recover.summary (flat leaf, pass)
  → advance NODE_PATH=recover.rationale.draft (sub-pipeline first step, emits questions)
  → answer the clarify gate (/answer q1: ...) — auto-detected by _find_open_gate
  → continue NODE_PATH=recover.rationale.finalize (seeded after gate advance)
  → advance NODE_PATH=recover.rationale.finalize (pass)
  → join.py (top, no NODE_PATH)
  → continue NODE_PATH=combine (runs the merge reduce hook)
  → assert _instance joined:true, phase=combine, merge ran

Protocol: recover-mental-model-stub (single-phase fanout, comment_prefix /answer for answer).
State-path (single-phase): drops leading 'recover' id:
  recover.summary         → summary.yaml
  recover.rationale       → rationale.yaml  (cursor)
  recover.rationale.draft → rationale.draft.yaml
  recover.rationale.clarify → rationale.clarify.yaml  (gate)
  recover.rationale.finalize → rationale.finalize.yaml
"""

import json
import pathlib
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / ".github/agent-factory/protocols/recover-mental-model-stub/protocol.json"

NEXT = ENG / "next.py"
ADVANCE = ENG / "advance.py"
JOIN = ENG / "join.py"


def _yaml(p):
    return yaml.safe_load(open(p))


def test_recover_unified_e2e(engine_env, tmp_path):
    """Full recover-mental-model-stub pipeline driven via NODE_PATH."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "sha2"
    base["AGENT_RUN_ID"] = "r"

    def run(script, *args, **env_extra):
        e = dict(base)
        e.update(env_extra)
        r = subprocess.run(
            ["python3", str(script), *map(str, args)],
            text=True, capture_output=True, env=e,
        )
        assert r.returncode == 0, f"{script.name} {args} failed:\n{r.stderr}"
        return r

    def reclone(tag):
        d = tmp_path / f"rc-{tag}"
        subprocess.run(
            ["git", "clone", "-q", "-b", "agentic-state",
             engine_env["STATE_REMOTE"], str(d)],
            check=True,
        )
        return d / "recover-mental-model-stub" / "pr-1"

    # Passing verdicts (one result, so decide() returns "done")
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))

    # --- Step 1: start → seeds the recover fanout (summary + rationale legs) ---
    r1 = run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "sha2")
    act1 = json.loads(r1.stdout)
    assert act1["action"] == "run-fanout", f"Expected run-fanout on start: {act1}"
    fdir1 = reclone("1")
    assert (fdir1 / "summary.yaml").is_file(), "summary.yaml not seeded after start"
    assert (fdir1 / "rationale.yaml").is_file(), "rationale.yaml not seeded after start"
    inst1 = _yaml(fdir1 / "_instance.yaml")
    assert inst1.get("joined") is False

    # --- Step 2: advance NODE_PATH=recover.summary (flat leaf, pass) → leg done ---
    ev_summary = tmp_path / "ev_summary.json"
    ev_summary.write_text(json.dumps({"summary": "This PR changes X"}))
    r2 = run(ADVANCE, tmp_path / "s2", "pr-1", PROTO, v, ev_summary,
             NODE_PATH="recover.summary")
    # Flat fanout child done → fire_join (top, no path)
    assert "event_type=protocol-join" in r2.stderr, (
        f"Expected protocol-join after summary done:\n{r2.stderr}"
    )
    fdir2 = reclone("2")
    summary_state = _yaml(fdir2 / "summary.yaml")
    assert summary_state["state"] == "done", f"summary.yaml should be done: {summary_state}"

    # join at this point: rationale still in-flight → should wait
    rj_early = run(JOIN, tmp_path / "sj_early", "pr-1", PROTO)
    assert "not all terminal" in rj_early.stderr, (
        f"Join should wait while rationale is in-flight:\n{rj_early.stderr}"
    )
    fdir_je = reclone("je")
    inst_je = _yaml(fdir_je / "_instance.yaml")
    assert inst_je.get("joined") is not True, (
        f"Should not be joined while rationale is in-flight: {inst_je}"
    )

    # --- Step 3: advance NODE_PATH=recover.rationale.draft → emits questions, gate opens ---
    ev_draft = tmp_path / "ev_draft.json"
    ev_draft.write_text(json.dumps({"questions": [{"id": "q1", "text": "Why this change?"}]}))
    r3 = run(ADVANCE, tmp_path / "s3", "pr-1", PROTO, v, ev_draft,
             NODE_PATH="recover.rationale.draft")
    # Sub-pipeline leg: cursor advances to clarify (gate)
    fdir3 = reclone("3")
    cursor3 = _yaml(fdir3 / "rationale.yaml")
    assert cursor3["sub_state"] == "clarify", (
        f"rationale cursor should be at clarify after draft done: {cursor3}"
    )
    assert cursor3["state"] == "recover", (
        f"rationale leg state should be the fanout life-state 'recover': {cursor3}"
    )
    gate3 = _yaml(fdir3 / "rationale.clarify.yaml")
    assert gate3["gates"]["state"] == "open", (
        f"clarify gate should be open after draft emits questions: {gate3}"
    )
    assert gate3["gates"]["questions"][0]["id"] == "q1"

    # --- Step 4: /answer command to close the clarify gate ---
    # The recover protocol declares comment_prefix=/answer for the answer command.
    # do_answer's _find_open_gate auto-discovers the rationale.clarify gate.
    r4 = run(NEXT, tmp_path / "s4", "pr-1", PROTO, "answer",
             ANSWER_BODY="/answer q1: because this change is safe",
             ANSWER_ACTOR="alice")
    # Gate fully covered → cursor advances to finalize
    fdir4 = reclone("4")
    cursor4 = _yaml(fdir4 / "rationale.yaml")
    assert cursor4["sub_state"] == "finalize", (
        f"rationale cursor should be at finalize after gate answered: {cursor4}"
    )
    gate4 = _yaml(fdir4 / "rationale.clarify.yaml")
    assert gate4["gates"]["state"] == "answered", (
        f"clarify gate should be answered: {gate4}"
    )

    # --- Step 5: continue NODE_PATH=recover.rationale.finalize → seeds finalize, run-agent ---
    r5 = run(NEXT, tmp_path / "s5", "pr-1", PROTO, "continue",
             NODE_PATH="recover.rationale.finalize")
    act5 = json.loads(r5.stdout)
    assert act5["action"] == "run-agent", f"Expected run-agent for finalize: {act5}"
    assert act5.get("path") == "recover.rationale.finalize", (
        f"Expected path=recover.rationale.finalize in action: {act5}"
    )
    # Inputs should include answers and draft
    input_names = {i["as"] for i in act5.get("inputs", [])}
    assert "answers" in input_names and "draft" in input_names, (
        f"finalize should have both answers and draft inputs: {input_names}"
    )
    fdir5 = reclone("5")
    assert (fdir5 / "rationale.finalize.yaml").is_file(), (
        "rationale.finalize.yaml should be seeded after continue"
    )

    # --- Step 6: advance NODE_PATH=recover.rationale.finalize (pass) → sub-pipeline ends ---
    ev_final = tmp_path / "ev_final.json"
    ev_final.write_text(json.dumps({"rationale": "Because reasons, the change is safe."}))
    r6 = run(ADVANCE, tmp_path / "s6", "pr-1", PROTO, v, ev_final,
             NODE_PATH="recover.rationale.finalize")
    # Sub-pipeline last sub-state done → cursor becomes "done" → fire_join (top)
    assert "event_type=protocol-join" in r6.stderr, (
        f"Expected protocol-join after rationale/finalize done:\n{r6.stderr}"
    )
    fdir6 = reclone("6")
    cursor6 = _yaml(fdir6 / "rationale.yaml")
    assert cursor6["state"] == "done", (
        f"rationale cursor should be done after finalize: {cursor6}"
    )

    # --- Step 7: join.py (top, no NODE_PATH) → both legs done → advance to combine ---
    rj = run(JOIN, tmp_path / "s7", "pr-1", PROTO)
    assert "event_type=protocol-continue" in rj.stderr, (
        f"Expected protocol-continue path=combine from join:\n{rj.stderr}"
    )
    assert "client_payload[path]=combine" in rj.stderr, (
        f"Expected path=combine from join:\n{rj.stderr}"
    )
    fdir7 = reclone("7")
    inst7 = _yaml(fdir7 / "_instance.yaml")
    assert inst7.get("joined") is True, f"Should be joined after join: {inst7}"
    assert inst7.get("phase") == "combine", f"phase should be combine: {inst7}"

    # --- Step 8: continue NODE_PATH=combine → runs the merge reduce hook + done ---
    r8 = run(NEXT, tmp_path / "s8", "pr-1", PROTO, "continue", NODE_PATH="combine")
    act8 = json.loads(r8.stdout)
    # merge:combine reason confirms the reduce hook ran
    assert act8.get("reason") == "merge:combine", (
        f"Expected reason=merge:combine, got: {act8}"
    )
    combined8 = r8.stdout + r8.stderr
    # The append-rationale reduce hook actually ran: its real summary string surfaces
    # in the captured output (mirrors test_recover_mental_model.py::test_full_pipeline).
    assert "Recovered mental model: summary + rationale posted." in combined8, (
        f"Expected append-rationale hook output in:\n{combined8}"
    )

    # --- FINAL: assert persisted state ---
    final = reclone("final")
    inst_final = _yaml(final / "_instance.yaml")
    assert inst_final.get("joined") is True, (
        f"_instance should be joined: {inst_final}"
    )
    assert inst_final.get("phase") == "combine", (
        f"_instance phase should be combine (merge phase): {inst_final}"
    )
