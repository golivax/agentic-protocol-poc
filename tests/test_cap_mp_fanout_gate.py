"""test_cap_mp_fanout_gate.py — Capability fixture: multi-phase protocol with a
fanout data-gate in a NON-FIRST phase.

Bug I1 proof: `_find_open_gate` previously called `lib._fanout_state(proto)` which
always returns the FIRST fanout state.  In a multi-phase protocol (preflight→review→join),
the open gate is under `review` (the second phase/fanout), not the first.  The old
code would scan the first fanout, find nothing, and return "No open question gate".

Fix: read `_instance.yaml`'s `phase` cursor; if multi-phase and that phase is a
`kind:fanout`, scan THAT fanout.  The test below runs RED before the fix and GREEN
after.

Walk:
  start → advance preflight → continue review (seed B sub-pipeline leg) →
  advance draft → gate opens → /answer (must find the gate under review.B.clarify) →
  advance finalize → join → done
"""
import json
import pathlib
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / "tests/fixtures/cap-mp-fanout-gate/protocol.json"

NEXT = ENG / "next.py"
ADVANCE = ENG / "advance.py"
JOIN = ENG / "join.py"


def _yaml(p):
    return yaml.safe_load(open(p))


def test_mp_fanout_gate_answer_finds_gate_in_second_phase(engine_env, tmp_path):
    """Full cap-mp-fanout-gate pipeline walk.  Proves _find_open_gate resolves the
    fanout from the CURSOR phase (review), not the first phase (preflight), so /answer
    succeeds instead of returning 'No open question gate'."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "sha-cap"
    base["AGENT_RUN_ID"] = "r0"

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
        return d / "cap-mp-fanout-gate" / "pr-1"

    # always-pass verdicts
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))

    # blank evidence (permissive schema)
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({}))

    # --- Step 1: start → seeds preflight (first root node, kind=agent) ---
    r1 = run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "sha-cap")
    act1 = json.loads(r1.stdout)
    assert act1["action"] == "run-agent", f"Expected run-agent on start: {act1}"
    assert act1.get("phase") == "preflight", f"Expected phase=preflight: {act1}"

    fdir1 = reclone("1")
    inst1 = _yaml(fdir1 / "_instance.yaml")
    assert inst1.get("phase") == "preflight"

    # --- Step 2: advance NODE_PATH=preflight → done, advances to review ---
    r2 = run(ADVANCE, tmp_path / "s2", "pr-1", PROTO, v, ev,
             NODE_PATH="preflight")
    # Root-child agent done → dispatches protocol-continue for next sibling (review)
    assert "protocol-continue" in r2.stderr or "client_payload[path]=review" in r2.stderr, (
        f"Expected protocol-continue dispatch after preflight:\n{r2.stderr}"
    )
    fdir2 = reclone("2")
    pf_state = _yaml(fdir2 / "preflight.yaml")
    assert pf_state["state"] == "done", f"preflight should be done: {pf_state}"
    inst2 = _yaml(fdir2 / "_instance.yaml")
    assert inst2.get("phase") == "review", f"_instance phase should advance to review: {inst2}"

    # --- Step 3: continue NODE_PATH=review → seeds the review fanout (branch B) ---
    r3 = run(NEXT, tmp_path / "s3", "pr-1", PROTO, "continue",
             NODE_PATH="review")
    act3 = json.loads(r3.stdout)
    assert act3["action"] == "run-fanout", f"Expected run-fanout for review: {act3}"
    fdir3 = reclone("3")
    # B is a sub-pipeline → seeded as review.B.yaml (cursor) + review.B.draft.yaml
    assert (fdir3 / "review.B.yaml").is_file(), "review.B.yaml not seeded"
    assert (fdir3 / "review.B.draft.yaml").is_file(), "review.B.draft.yaml not seeded"

    # --- Step 4: advance NODE_PATH=review.B.draft → emits questions, gate opens ---
    ev_draft = tmp_path / "ev_draft.json"
    ev_draft.write_text(json.dumps({"questions": [{"id": "q1", "text": "What changed?"}]}))
    r4 = run(ADVANCE, tmp_path / "s4", "pr-1", PROTO, v, ev_draft,
             NODE_PATH="review.B.draft")
    fdir4 = reclone("4")
    cursor4 = _yaml(fdir4 / "review.B.yaml")
    assert cursor4["sub_state"] == "clarify", (
        f"B cursor should advance to clarify: {cursor4}"
    )
    gate4 = _yaml(fdir4 / "review.B.clarify.yaml")
    assert gate4["gates"]["state"] == "open", (
        f"clarify gate should be open: {gate4}"
    )
    assert gate4["gates"]["questions"][0]["id"] == "q1"

    # --- Step 5: /answer — THIS is the I1 regression step ---
    # The gate is under review (second phase), not preflight (first phase).
    # Before the fix: _find_open_gate scans _fanout_state (=preflight, an agent)
    # or returns None → "No open question gate".
    # After the fix: reads _instance.yaml phase=review, sees it is a fanout, scans it.
    r5 = run(NEXT, tmp_path / "s5", "pr-1", PROTO, "answer",
             ANSWER_BODY="/answer q1: the auth module",
             ANSWER_ACTOR="alice")
    combined5 = r5.stdout + r5.stderr
    assert "no open question gate" not in combined5.lower(), (
        "I1 bug: _find_open_gate failed to find the gate under the non-first phase 'review'.\n"
        f"stdout: {r5.stdout}\nstderr: {r5.stderr}"
    )
    # Gate fully covered → cursor advances to finalize and a path-continue is dispatched
    fdir5 = reclone("5")
    cursor5 = _yaml(fdir5 / "review.B.yaml")
    assert cursor5["sub_state"] == "finalize", (
        f"B cursor should advance to finalize after gate answered: {cursor5}"
    )
    gate5 = _yaml(fdir5 / "review.B.clarify.yaml")
    assert gate5["gates"]["state"] == "answered", (
        f"clarify gate should be answered: {gate5}"
    )
    # Path emitted in the path-continue dispatch
    assert "client_payload[path]=review.B.finalize" in r5.stderr, (
        f"Expected path=review.B.finalize dispatch:\n{r5.stderr}"
    )

    # --- Step 6: continue NODE_PATH=review.B.finalize → seeds + run-agent ---
    r6 = run(NEXT, tmp_path / "s6", "pr-1", PROTO, "continue",
             NODE_PATH="review.B.finalize")
    act6 = json.loads(r6.stdout)
    assert act6["action"] == "run-agent", f"Expected run-agent for finalize: {act6}"

    # --- Step 7: advance NODE_PATH=review.B.finalize → leg done, fires join ---
    r7 = run(ADVANCE, tmp_path / "s7", "pr-1", PROTO, v, ev,
             NODE_PATH="review.B.finalize")
    assert "event_type=protocol-join" in r7.stderr, (
        f"Expected protocol-join after B/finalize done:\n{r7.stderr}"
    )
    fdir7 = reclone("7")
    cursor7 = _yaml(fdir7 / "review.B.yaml")
    assert cursor7["state"] == "done", f"B cursor should be done: {cursor7}"

    # --- Step 8: join (all branches done) → pipeline complete ---
    # The fixture has join.next = "done" (a literal terminal), so join.py
    # marks the instance joined and concludes the pipeline (no further
    # protocol-continue dispatch — the suite is "Review complete").
    rj = run(JOIN, tmp_path / "s8", "pr-1", PROTO)
    assert "review complete" in rj.stderr.lower() or "pipeline complete" in rj.stderr.lower(), (
        f"Expected join to complete the pipeline:\n{rj.stderr}"
    )
    fdir8 = reclone("8")
    inst8 = _yaml(fdir8 / "_instance.yaml")
    assert inst8.get("joined") is True, f"Should be joined: {inst8}"
