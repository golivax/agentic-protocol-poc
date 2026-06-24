"""test_unified_codereview_e2e.py — Task 8: full e2e oracle walk for code-review via NODE_PATH.

Walk:
  start
  → advance NODE_PATH=preflight (clear, multi-phase depth-1 agent)
  → continue NODE_PATH=review (seed fanout legs)
  → advance NODE_PATH=review.grumpy + review.security (pass)
  → join.py (top, no NODE_PATH)
  → continue NODE_PATH=approval (gate opens)
  → next.py resolve-gate (GATE_DECISION=approve)
  → assert approval.yaml gates.state==approved + _instance joined/done

This is an integration oracle: it drives the unified NODE_PATH engine path end-to-end
using real engine scripts as subprocesses with the shared bare git origin (conftest fixture).
All assertions check persisted state in fresh re-clones of the bare origin.
"""

import json
import pathlib
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / ".github/agent-factory/protocols/code-review/protocol.json"

NEXT = ENG / "next.py"
ADVANCE = ENG / "advance.py"
JOIN = ENG / "join.py"


def _yaml(p):
    return yaml.safe_load(open(p))


def test_codereview_unified_e2e(engine_env, tmp_path):
    """Full code-review pipeline driven entirely via NODE_PATH."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "sha1"
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
        return d / "code-review" / "pr-1"

    # Passing verdicts
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "x", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))
    ev = tmp_path / "ev.json"
    ev.write_text("{}")

    # --- Step 1: start → seeds preflight.yaml ---
    r1 = run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "sha1")
    act1 = json.loads(r1.stdout)
    # multi-phase start seeds the first phase (preflight agent) → must emit run-agent
    assert act1["action"] == "run-agent", act1
    fdir = reclone("1")
    # preflight.yaml seeded
    assert (fdir / "preflight.yaml").is_file(), "preflight.yaml not seeded after start"
    inst = _yaml(fdir / "_instance.yaml")
    assert inst["phase"] == "preflight"

    # --- Step 2: advance NODE_PATH=preflight (all checks pass) → path-continue to review ---
    r2 = run(ADVANCE, tmp_path / "s2", "pr-1", PROTO, v, ev, NODE_PATH="preflight")
    # dispatch: protocol-continue path=review
    assert "event_type=protocol-continue" in r2.stderr, (
        f"Expected protocol-continue after preflight clear:\n{r2.stderr}"
    )
    assert "client_payload[path]=review" in r2.stderr, (
        f"Expected path=review in dispatch:\n{r2.stderr}"
    )
    fdir2 = reclone("2")
    pf = _yaml(fdir2 / "preflight.yaml")
    assert pf["state"] == "done", f"preflight should be done: {pf}"
    inst2 = _yaml(fdir2 / "_instance.yaml")
    assert inst2["phase"] == "review", f"_instance phase should be review: {inst2}"

    # --- Step 3: continue NODE_PATH=review → seeds grumpy.yaml + security.yaml ---
    r3 = run(NEXT, tmp_path / "s3", "pr-1", PROTO, "continue", NODE_PATH="review")
    act3 = json.loads(r3.stdout)
    assert act3["action"] == "run-fanout", f"Expected run-fanout, got: {act3}"
    leg_paths = {l["path"] for l in act3.get("legs", [])}
    assert "review.grumpy" in leg_paths and "review.security" in leg_paths, (
        f"Expected both review legs in action: {act3}"
    )
    fdir3 = reclone("3")
    assert (fdir3 / "review.grumpy.yaml").is_file(), "review.grumpy.yaml not seeded"
    assert (fdir3 / "review.security.yaml").is_file(), "review.security.yaml not seeded"

    # --- Step 4: advance NODE_PATH=review.grumpy (pass) → leg done, fire_join ---
    r4g = run(ADVANCE, tmp_path / "s4g", "pr-1", PROTO, v, ev, NODE_PATH="review.grumpy")
    assert "event_type=protocol-join" in r4g.stderr, (
        f"Expected protocol-join after grumpy done:\n{r4g.stderr}"
    )
    fdir4g = reclone("4g")
    grumpy = _yaml(fdir4g / "review.grumpy.yaml")
    assert grumpy["state"] == "done", f"grumpy should be done: {grumpy}"

    # --- Step 5: advance NODE_PATH=review.security (pass) → leg done, fire_join ---
    r4s = run(ADVANCE, tmp_path / "s4s", "pr-1", PROTO, v, ev, NODE_PATH="review.security")
    assert "event_type=protocol-join" in r4s.stderr, (
        f"Expected protocol-join after security done:\n{r4s.stderr}"
    )
    fdir4s = reclone("4s")
    security = _yaml(fdir4s / "review.security.yaml")
    assert security["state"] == "done", f"security should be done: {security}"

    # --- Step 6: join.py (top, no NODE_PATH) → both done → advance to approval gate ---
    rj = run(JOIN, tmp_path / "s5", "pr-1", PROTO)
    assert "event_type=protocol-continue" in rj.stderr, (
        f"Expected protocol-continue path=approval from join:\n{rj.stderr}"
    )
    assert "client_payload[path]=approval" in rj.stderr, (
        f"Expected path=approval from join:\n{rj.stderr}"
    )
    fdir5 = reclone("5")
    inst5 = _yaml(fdir5 / "_instance.yaml")
    assert inst5["phase"] == "approval", f"phase should be approval: {inst5}"
    assert inst5.get("joined") is True, f"should be joined: {inst5}"

    # --- Step 7: continue NODE_PATH=approval → gate opens ---
    r6 = run(NEXT, tmp_path / "s6", "pr-1", PROTO, "continue", NODE_PATH="approval")
    act6 = json.loads(r6.stdout)
    assert act6["action"] == "noop", f"Approval gate open → noop expected: {act6}"
    assert "gate-open" in act6.get("reason", ""), f"Expected gate-open reason: {act6}"
    fdir6 = reclone("6")
    approval = _yaml(fdir6 / "approval.yaml")
    assert approval["gates"]["state"] == "open", f"approval gate should be open: {approval}"

    # --- Step 8: resolve-gate approve → pipeline done ---
    r7 = run(NEXT, tmp_path / "s7", "pr-1", PROTO, "resolve-gate",
             GATE_DECISION="approve",
             GATE_ACTOR="alice",
             GATE_REASON="",
             GATE_PR_AUTHOR="bob")
    act7 = json.loads(r7.stdout)
    assert act7["action"] == "noop", f"Final approval → noop expected: {act7}"
    assert "gate:approved" in act7.get("reason", ""), f"Expected gate:approved reason: {act7}"
    # No protocol-continue for the LAST phase
    assert "event_type=protocol-continue" not in r7.stderr, (
        f"Should NOT dispatch protocol-continue after last gate approval:\n{r7.stderr}"
    )

    # --- FINAL: assert persisted state ---
    final = reclone("final")
    approval_final = _yaml(final / "approval.yaml")
    assert approval_final["gates"]["state"] == "approved", (
        f"approval.yaml gates.state should be approved: {approval_final}"
    )
    inst_final = _yaml(final / "_instance.yaml")
    assert inst_final.get("joined") is True, f"_instance should be joined: {inst_final}"
    assert inst_final.get("phase") == "approval", (
        f"_instance phase should be approval (last phase, finalized in-place): {inst_final}"
    )
