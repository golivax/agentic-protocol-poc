"""Unified gate-resolve: approve finalizes via path-continue (not seed_and_dispatch_phase).
After Task 7, do_resolve_gate's approve arm sets _instance.yaml.phase=nxt and dispatches
protocol-continue(path=nxt) instead of calling seed_and_dispatch_phase. For code-review-v1's
approval gate (the LAST phase), nxt is None so it finalizes directly (no continue)."""
import json
import pathlib
import subprocess
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / ".github/agent-factory/protocols/code-review-v1/protocol.json"
LIB_PY = ENG / "lib.py"
import os, sys
sys.path.insert(0, str(ENG))


def _env(state_origin, **extra):
    e = dict(os.environ)
    e["ENGINE_LOCAL"] = "1"
    e["STATE_REMOTE"] = str(state_origin)
    e["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    e.update(extra)
    return e


def _drive_to_gate(engine_env, tmp_path):
    """Drive the code-review-v1 pipeline to the open approval gate."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "s1"
    base["AGENT_RUN_ID"] = "r"

    def run(s, *a, **env):
        e = dict(base)
        e.update(env)
        r = subprocess.run(["python3", str(ENG / s), *map(str, a)],
                           text=True, capture_output=True, env=e)
        assert r.returncode == 0, f"{s} failed:\n{r.stderr}"
        return r

    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "x", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "e.json"
    ev.write_text("{}")

    # start: seeds preflight
    run("next.py", tmp_path / "s", "pr-1", PROTO, "start", "s1")
    # advance preflight (pass)
    run("advance.py", tmp_path / "a0", "pr-1", PROTO, v, ev, NODE_PATH="preflight")
    # continue: next.py picks up review fanout
    run("next.py", tmp_path / "c", "pr-1", PROTO, "continue", NODE_PATH="review")
    # advance both review branches
    for leg in ("grumpy", "security"):
        run("advance.py", tmp_path / f"a-{leg}", "pr-1", PROTO, v, ev,
            NODE_PATH=f"review.{leg}")
    # join
    run("join.py", tmp_path / "j", "pr-1", PROTO)
    # continue: next.py opens the approval gate
    run("next.py", tmp_path / "cg", "pr-1", PROTO, "continue", NODE_PATH="approval")
    return base, run


def test_approve_finalizes_pipeline(engine_env, tmp_path):
    """Approve the last gate → pipeline done (finalize path, NOT a protocol-continue)."""
    base, run = _drive_to_gate(engine_env, tmp_path)

    # resolve-gate approve → should finalize (nxt is None for the last phase)
    r = run("next.py", tmp_path / "ap", "pr-1", PROTO, "resolve-gate",
            GATE_DECISION="approve", GATE_ACTOR="alice", GATE_REASON="",
            GATE_PR_AUTHOR="bob")

    # Finalize path: action noop + gate:approved reason
    out = json.loads(r.stdout)
    assert out["action"] == "noop"
    assert "gate:approved" in out["reason"]

    # Verify the gate state in the persisted branch
    d = tmp_path / "rcz"
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state",
                    engine_env["STATE_REMOTE"], str(d)], check=True)
    gate = yaml.safe_load(open(d / "code-review-v1" / "pr-1" / "approval.yaml"))
    assert gate["gates"]["state"] == "approved"
    # aggregate check-run completed → done
    assert "check-run code-review-v1" in r.stderr and "status=completed" in r.stderr
    # no protocol-continue dispatched (last phase)
    assert "event_type=protocol-continue" not in r.stderr


def test_approve_non_last_gate_dispatches_continue(engine_env, tmp_path):
    """Approve a gate that is NOT the last phase → protocol-continue path=<next>.
    We synthesize a minimal multi-phase protocol with a gate followed by an agent phase."""
    import json as _json

    # Build a tiny multi-phase protocol: preflight(agent) → checkpoint(gate) → finish(agent)
    tiny_proto = {
        "name": "tiny-gated",
        "max_depth": 5,
        "comment_prefix": "/",
        "states": [
            {"id": "preflight", "kind": "agent", "next": "checkpoint",
             "workflow": "noop.yml", "max_iterations": 1},
            {"id": "checkpoint", "kind": "gate", "next": "finish",
             "approve_excludes_author": False},
            {"id": "finish", "kind": "agent", "next": "done",
             "workflow": "noop.yml", "max_iterations": 1},
        ],
    }
    proto_path = tmp_path / "tiny.json"
    proto_path.write_text(_json.dumps(tiny_proto))

    # Seed _instance cursor at the checkpoint gate + open the gate file
    e = dict(engine_env)
    e["ENGINE_LOCAL"] = "1"
    subprocess.run(["python3", str(LIB_PY), "state-checkout", str(tmp_path / "seed")],
                   env=e, check=True, capture_output=True)
    base = tmp_path / "seed" / "tiny-gated" / "pr-5"
    base.mkdir(parents=True)
    (base / "_instance.yaml").write_text(yaml.safe_dump({
        "protocol": "tiny-gated", "instance": "pr-5",
        "phase": "checkpoint", "head_sha": "sh5", "joined": False,
    }))
    (base / "checkpoint.yaml").write_text(yaml.safe_dump({
        "protocol": "tiny-gated", "instance": "pr-5",
        "state": "checkpoint", "head_sha": "sh5",
        "gates": {"state": "open", "history": []},
    }))
    subprocess.run(["python3", str(LIB_PY), "cas-push", str(tmp_path / "seed"), "seed gate"],
                   env=e, check=True, capture_output=True)

    # Now resolve-gate approve
    env2 = dict(e)
    env2.update({"GATE_DECISION": "approve", "GATE_ACTOR": "alice",
                 "GATE_REASON": "", "GATE_PR_AUTHOR": "bob", "PR_HEAD_SHA": "sh5"})
    r = subprocess.run(["python3", str(ENG / "next.py"),
                        str(tmp_path / "rg"), "pr-5", str(proto_path),
                        "resolve-gate", "sh5"],
                       text=True, capture_output=True, env=env2)
    assert r.returncode == 0, r.stderr

    # Must dispatch protocol-continue path=finish (NOT the last phase)
    assert "event_type=protocol-continue" in r.stderr, f"No protocol-continue:\n{r.stderr}"
    assert "client_payload[path]=finish" in r.stderr, f"Wrong path:\n{r.stderr}"

    # _instance.yaml must have phase=finish
    d = tmp_path / "verify2"
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state",
                    engine_env["STATE_REMOTE"], str(d)], check=True)
    inst = yaml.safe_load(open(d / "tiny-gated" / "pr-5" / "_instance.yaml"))
    assert inst["phase"] == "finish", f"phase mismatch: {inst}"


def test_override_non_last_gate_dispatches_continue(engine_env, tmp_path):
    """Override a blocked gate → protocol-continue path=<next> (not run-fanout).
    Uses the real code-review-v1 protocol: preflight(blocked) → override → review."""
    import sys as _sys
    _sys.path.insert(0, str(ENG))
    import lib as _lib

    e = dict(engine_env)
    e["ENGINE_LOCAL"] = "1"

    # Seed blocked preflight state
    subprocess.run(["python3", str(LIB_PY), "state-checkout", str(tmp_path / "seed")],
                   env=e, check=True, capture_output=True)
    base = tmp_path / "seed" / "code-review-v1" / "pr-50"
    base.mkdir(parents=True)
    (base / "_instance.yaml").write_text(yaml.safe_dump({
        "protocol": "code-review-v1", "instance": "pr-50",
        "phase": "preflight", "head_sha": "ovsh", "joined": False,
        "halted": {"phase": "preflight", "reason": "blocked", "sha": "ovsh"},
    }))
    (base / "preflight.yaml").write_text(yaml.safe_dump({
        "protocol": "code-review-v1", "instance": "pr-50",
        "state": "failed", "iteration": 1, "gates": {}, "head_sha": "ovsh", "history": [],
    }))
    subprocess.run(["python3", str(LIB_PY), "cas-push", str(tmp_path / "seed"), "seed blocked"],
                   env=e, check=True, capture_output=True)

    env2 = dict(e)
    env2.update({"OVERRIDE_ACTOR": "alice", "OVERRIDE_REASON": "ship it", "PR": "50"})
    r = subprocess.run(["python3", str(ENG / "next.py"),
                        str(tmp_path / "ovr"), "pr-50", str(PROTO),
                        "override", "ovsh"],
                       text=True, capture_output=True, env=env2)
    assert r.returncode == 0, r.stderr

    # Must dispatch protocol-continue path=review (next sibling of preflight)
    assert "event_type=protocol-continue" in r.stderr, f"No protocol-continue:\n{r.stderr}"
    assert "client_payload[path]=review" in r.stderr, f"Wrong path:\n{r.stderr}"

    # _instance.yaml must have phase=review, no halted marker
    d = tmp_path / "verify3"
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state",
                    engine_env["STATE_REMOTE"], str(d)], check=True)
    inst = yaml.safe_load(open(d / "code-review-v1" / "pr-50" / "_instance.yaml"))
    assert inst["phase"] == "review", f"phase mismatch: {inst}"
    assert "halted" not in inst
    assert inst.get("overrides") == [{"phase": "preflight", "actor": "alice", "reason": "ship it"}]
    # review fan-out legs NOT seeded yet (seeding is deferred to the continue dispatch)
    assert not (d / "code-review-v1" / "pr-50" / "review.grumpy.yaml").exists()
