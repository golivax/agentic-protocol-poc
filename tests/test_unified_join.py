"""test_unified_join.py — RED/GREEN for Task 5: top join advances to .next via path-continue.

Tests:
  1. code-review: join(next=approval gate) → dispatches protocol-continue path=approval
     + _instance.yaml.phase == "approval"
  2. subpipeline-mini: join(next=combine merge) → dispatches protocol-continue path=combine
     + _instance.yaml.phase == "combine"
  3. deep-fanout sentinel: join(next=done, done not a real state) → plain finalize
     (joined=True, no protocol-continue path dispatch) — guard for the .next-sentinel case.
"""

import json
import subprocess
import pathlib
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO_CR = ROOT / ".github/agent-factory/protocols/code-review/protocol.json"
PROTO_SP = ROOT / "tests/fixtures/subpipeline-mini/protocol.json"


def _yaml(p):
    return yaml.safe_load(open(p))


def _rc(engine_env, tmp_path, tag):
    """Clone state branch and return the protocol instance directory."""
    d = tmp_path / f"rc-{tag}"
    subprocess.run(
        ["git", "clone", "-q", "-b", "agentic-state",
         engine_env["STATE_REMOTE"], str(d)],
        check=True,
    )
    return d


def test_top_join_advances_to_approval_via_continue(engine_env, tmp_path):
    """code-review: join(next=approval) → protocol-continue path=approval + phase set."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "s1"
    base["AGENT_RUN_ID"] = "r"

    def run(s, *a, **env):
        e = dict(base)
        e.update(env)
        r = subprocess.run(
            ["python3", str(ENG / s), *map(str, a)],
            text=True, capture_output=True, env=e,
        )
        assert r.returncode == 0, f"{s} failed:\n{r.stderr}"
        return r

    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "x", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))
    ev = tmp_path / "e.json"
    ev.write_text("{}")

    # Start → advance preflight → enter review fanout → advance both legs → join
    run("next.py", tmp_path / "s", "pr-1", PROTO_CR, "start", "s1")
    run("advance.py", tmp_path / "a0", "pr-1", PROTO_CR, passv, ev, NODE_PATH="preflight")
    run("next.py", tmp_path / "c", "pr-1", PROTO_CR, "continue", NODE_PATH="review")
    for leg in ("grumpy", "security"):
        run("advance.py", tmp_path / f"a-{leg}", "pr-1", PROTO_CR, passv, ev,
            NODE_PATH=f"review.{leg}")

    rj = run("join.py", tmp_path / "j", "pr-1", PROTO_CR)
    combined = rj.stdout + rj.stderr
    assert "event_type=protocol-continue" in combined, (
        f"Expected protocol-continue dispatch, got:\n{combined}"
    )
    assert "client_payload[path]=approval" in combined, (
        f"Expected path=approval in dispatch, got:\n{combined}"
    )
    inst_dir = _rc(engine_env, tmp_path, "j") / "code-review" / "pr-1"
    inst = _yaml(inst_dir / "_instance.yaml")
    assert inst["phase"] == "approval", f"Expected phase=approval, got {inst.get('phase')!r}"


def test_top_join_advances_to_combine_via_continue(engine_env, tmp_path):
    """subpipeline-mini: join(next=combine merge) → protocol-continue path=combine + phase set."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "s2"
    base["AGENT_RUN_ID"] = "r"

    def run(s, *a, **env):
        e = dict(base)
        e.update(env)
        r = subprocess.run(
            ["python3", str(ENG / s), *map(str, a)],
            text=True, capture_output=True, env=e,
        )
        assert r.returncode == 0, f"{s} failed:\n{r.stderr}"
        return r

    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "x", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))

    # Start the subpipeline-mini fanout
    run("next.py", tmp_path / "n", "pr-1", PROTO_SP, "start", "s2")

    # Advance flat branch A to done
    ev_a = tmp_path / "ev_a.json"
    ev_a.write_text(json.dumps({"summary": "FROM-A"}))
    run("advance.py", tmp_path / "adv-A", "pr-1", PROTO_SP, passv, ev_a, BRANCH="A")

    # Advance B/draft → opens clarify gate
    ev_draft = tmp_path / "ev_draft.json"
    ev_draft.write_text(json.dumps({"questions": [{"id": "q1", "text": "Q?"}]}))
    run("advance.py", tmp_path / "adv-B-draft", "pr-1", PROTO_SP, passv, ev_draft,
        BRANCH="B", SUBSTATE="draft")

    # Answer the gate
    ea = dict(base)
    ea["ANSWER_BODY"] = "/answer q1: yes"
    ea["ANSWER_ACTOR"] = "al"
    run("next.py", tmp_path / "ans", "pr-1", PROTO_SP, "answer", **{k: v for k, v in ea.items() if k not in base or ea[k] != base[k]})

    # Advance B/finalize to done
    ev_fin = tmp_path / "ev_fin.json"
    ev_fin.write_text(json.dumps({"summary": "FROM-B"}))
    run("advance.py", tmp_path / "adv-B-fin", "pr-1", PROTO_SP, passv, ev_fin,
        BRANCH="B", SUBSTATE="finalize")

    # Join: should dispatch protocol-continue with path=combine
    rj = run("join.py", tmp_path / "j", "pr-1", PROTO_SP)
    combined = rj.stdout + rj.stderr
    assert "event_type=protocol-continue" in combined, (
        f"Expected protocol-continue dispatch, got:\n{combined}"
    )
    assert "client_payload[path]=combine" in combined, (
        f"Expected path=combine in dispatch, got:\n{combined}"
    )
    inst_dir = _rc(engine_env, tmp_path, "j") / "subpipeline-mini" / "pr-1"
    inst = _yaml(inst_dir / "_instance.yaml")
    assert inst["phase"] == "combine", f"Expected phase=combine, got {inst.get('phase')!r}"

    # Step 2: next.py continue NODE_PATH=combine runs the merge reduce hook + finalizes.
    rm = run("next.py", tmp_path / "m", "pr-1", PROTO_SP, "continue", NODE_PATH="combine")
    mcombined = rm.stdout + rm.stderr
    # append-outputs concatenated both leg outputs → merge actually executed.
    assert "FROM-A" in mcombined and "FROM-B" in mcombined, (
        f"Expected merge hook to combine both leg outputs, got:\n{mcombined}"
    )
    assert json.loads(rm.stdout).get("reason") == "merge:combine"
    inst2 = _yaml((_rc(engine_env, tmp_path, "m") / "subpipeline-mini" / "pr-1") / "_instance.yaml")
    assert inst2.get("joined") is True and inst2.get("phase") == "combine"


def test_join_sentinel_next_does_not_dispatch_continue(engine_env, tmp_path):
    """join.next=done (sentinel not a real state) → plain finalize, no protocol-continue."""
    # Build a minimal flat fanout protocol where join.next="done" (sentinel, not a state)
    proto = {
        "name": "sentinel-test",
        "version": "0.1.0",
        "triggers": [],
        "states": [
            {
                "id": "review",
                "kind": "fanout",
                "branches": [
                    {"id": "A", "workflow": "a-agent", "evidence": "e.json",
                     "max_iterations": 1, "checks": [], "publish": "noop"},
                    {"id": "B", "workflow": "b-agent", "evidence": "e.json",
                     "max_iterations": 1, "checks": [], "publish": "noop"},
                ],
                "next": "join",
            },
            {"id": "join", "kind": "join", "of": "review", "next": "done"},
        ],
    }
    pf = tmp_path / "proto.json"
    pf.write_text(json.dumps(proto))

    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "s3"
    base["AGENT_RUN_ID"] = "r"

    def run(s, *a, **env):
        e = dict(base)
        e.update(env)
        r = subprocess.run(
            ["python3", str(ENG / s), *map(str, a)],
            text=True, capture_output=True, env=e,
        )
        assert r.returncode == 0, f"{s} failed:\n{r.stderr}"
        return r

    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "x", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))
    ev = tmp_path / "e.json"
    ev.write_text(json.dumps({"summary": "ok"}))

    run("next.py", tmp_path / "n", "pr-1", pf, "start", "s3")

    # Advance both branches to done (flat, no PHASE needed for single-phase)
    run("advance.py", tmp_path / "adv-A", "pr-1", pf, passv, ev, BRANCH="A")
    run("advance.py", tmp_path / "adv-B", "pr-1", pf, passv, ev, BRANCH="B")

    rj = run("join.py", tmp_path / "j", "pr-1", pf)
    combined = rj.stdout + rj.stderr
    # Must NOT dispatch a protocol-continue (sentinel "done" is not a real state)
    assert "event_type=protocol-continue" not in combined, (
        f"Did not expect protocol-continue for sentinel next=done, got:\n{combined}"
    )
    # Should plain-finalize (joined=True)
    inst_dir = _rc(engine_env, tmp_path, "j") / "sentinel-test" / "pr-1"
    inst = _yaml(inst_dir / "_instance.yaml")
    assert inst.get("joined") is True, f"Expected joined=True, got {inst}"
