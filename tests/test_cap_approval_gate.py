"""test_cap_approval_gate.py — Task 11: Approval-gate decisions capability.

Drives the code-review pipeline to the open approval gate (via the unified
NODE_PATH walk) and then exercises each of the four decision paths in
ISOLATION (fresh instance per test case):

  approve          → gates.state=="approved", pipeline done, phase_label done.
  request-changes  → gates.state=="changes_requested", pipeline halts but
                     re-runnable (NOT failed-terminal).
  reject           → gates.state=="rejected", gate.state=="failed", phase_label failed.
  self-approve     → when GATE_ACTOR == GATE_PR_AUTHOR and
                     approve_excludes_author==true, do_resolve_gate refuses
                     with a halt action + PR comment; gate stays open.

The _drive_to_gate helper mirrors the shape from test_unified_gate_resolve.py
(Task 7) and drives the full preflight→review(grumpy+security)→join→approval
pipeline via NODE_PATH calls.
"""
import json
import pathlib
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG  = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / ".github/agent-factory/protocols/code-review/protocol.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(engine_env, **extra):
    e = dict(engine_env)
    e.update(extra)
    return e


def _pass_verdicts(tmp_path, tag="v"):
    v = tmp_path / f"v-{tag}.json"
    v.write_text(json.dumps({"results": [
        {"check": "x", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))
    ev = tmp_path / f"ev-{tag}.json"
    ev.write_text("{}")
    return v, ev


def _reclone(engine_env, tmp_path, tag):
    d = tmp_path / f"rc-{tag}"
    subprocess.run(
        ["git", "clone", "-q", "-b", "agentic-state",
         engine_env["STATE_REMOTE"], str(d)],
        check=True,
    )
    return d


def _run(engine_env, *args, **extra_env):
    e = dict(engine_env)
    e["PR_HEAD_SHA"] = e.get("PR_HEAD_SHA", "sha1")
    e["AGENT_RUN_ID"] = e.get("AGENT_RUN_ID", "r1")
    e.update(extra_env)
    r = subprocess.run(
        ["python3", *map(str, args)],
        text=True, capture_output=True, env=e,
    )
    assert r.returncode == 0, f"{args[0]} failed:\n{r.stderr}"
    return r


def _drive_to_gate(engine_env, tmp_path):
    """Drive the code-review pipeline to the open approval gate.

    Mirrors the helper in test_unified_gate_resolve.py so every test starts
    from a fresh, gate-open instance.  Returns (base_env, run_fn).
    """
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "sha1"
    base["AGENT_RUN_ID"] = "r1"

    v, ev = _pass_verdicts(tmp_path)

    def run(s, *a, **env):
        e = dict(base)
        e.update(env)
        r = subprocess.run(
            ["python3", str(ENG / s), *map(str, a)],
            text=True, capture_output=True, env=e,
        )
        assert r.returncode == 0, f"{s} failed:\n{r.stderr}"
        return r

    # start → seeds preflight
    run("next.py", tmp_path / "s", "pr-1", PROTO, "start", "sha1")
    # advance preflight (pass)
    run("advance.py", tmp_path / "a0", "pr-1", PROTO, v, ev, NODE_PATH="preflight")
    # continue → review fanout
    run("next.py", tmp_path / "c", "pr-1", PROTO, "continue", NODE_PATH="review")
    # advance both review branches
    for leg in ("grumpy", "security"):
        run("advance.py", tmp_path / f"a-{leg}", "pr-1", PROTO, v, ev,
            NODE_PATH=f"review.{leg}")
    # join
    run("join.py", tmp_path / "j", "pr-1", PROTO)
    # continue → opens the approval gate
    run("next.py", tmp_path / "cg", "pr-1", PROTO, "continue", NODE_PATH="approval")

    return base, run


def _read_approval(engine_env, tmp_path, tag):
    d = _reclone(engine_env, tmp_path, tag)
    return yaml.safe_load(open(d / "code-review" / "pr-1" / "approval.yaml"))


def _read_instance(engine_env, tmp_path, tag):
    d = _reclone(engine_env, tmp_path, tag)
    return yaml.safe_load(open(d / "code-review" / "pr-1" / "_instance.yaml"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_approve_sets_gate_approved_and_done_label(engine_env, tmp_path):
    """approve → gates.state==approved, aggregate done, phase_label==done."""
    base, run = _drive_to_gate(engine_env, tmp_path)

    r = run("next.py", tmp_path / "ap", "pr-1", PROTO, "resolve-gate",
            GATE_DECISION="approve", GATE_ACTOR="alice", GATE_REASON="lgtm",
            GATE_PR_AUTHOR="bob")

    out = json.loads(r.stdout)
    assert out["action"] == "noop"
    assert "gate:approved" in out["reason"]
    # no protocol-continue (last gate in the pipeline)
    assert "event_type=protocol-continue" not in r.stderr

    gate = _read_approval(engine_env, tmp_path, "approve")
    assert gate["gates"]["state"] == "approved"

    # aggregate check-run marked done
    assert "check-run code-review" in r.stderr
    assert "status=completed" in r.stderr

    # phase_label stored on _instance.yaml
    inst = _read_instance(engine_env, tmp_path, "approve-inst")
    assert inst.get("phase_label") == "✅ done"


def test_request_changes_halts_but_reruns(engine_env, tmp_path):
    """request-changes → gates.state==changes_requested, NOT a terminal failure.

    The pipeline must be halt-and-re-runnable: gstate changes_requested, no
    failed marker on the gate file, no failed phase_label.
    """
    base, run = _drive_to_gate(engine_env, tmp_path)

    r = run("next.py", tmp_path / "rc", "pr-1", PROTO, "resolve-gate",
            GATE_DECISION="request-changes", GATE_ACTOR="carol", GATE_REASON="nit",
            GATE_PR_AUTHOR="bob")

    out = json.loads(r.stdout)
    assert out["action"] == "noop"
    assert "gate:changes" in out["reason"]

    gate = _read_approval(engine_env, tmp_path, "rc")
    assert gate["gates"]["state"] == "changes_requested"

    # Gate must NOT be terminated (state is not "failed"; it is "open" or absent
    # at the file level — changes_requested keeps it alive)
    gate_file_state = gate.get("state", "")
    assert gate_file_state != "failed", (
        "request-changes must not mark the gate as failed (it must remain re-runnable)"
    )

    # Phase label must NOT be "failed" — it stays on "approval gate"
    inst = _read_instance(engine_env, tmp_path, "rc-inst")
    phase_label = inst.get("phase_label", "")
    assert phase_label != "❌ failed", (
        "request-changes must not set the failed label (pipeline is re-runnable)"
    )

    # Pipeline can be re-resolved: approve afterwards should succeed (gate state
    # 'changes_requested' is still "live" per do_resolve_gate)
    r2 = run("next.py", tmp_path / "re-ap", "pr-1", PROTO, "resolve-gate",
             GATE_DECISION="approve", GATE_ACTOR="carol", GATE_REASON="addressed",
             GATE_PR_AUTHOR="bob")
    out2 = json.loads(r2.stdout)
    assert out2["action"] == "noop"
    assert "gate:approved" in out2["reason"]


def test_reject_terminates_pipeline(engine_env, tmp_path):
    """reject → gates.state==rejected, gate file state==failed, phase_label failed."""
    base, run = _drive_to_gate(engine_env, tmp_path)

    r = run("next.py", tmp_path / "rj", "pr-1", PROTO, "resolve-gate",
            GATE_DECISION="reject", GATE_ACTOR="dave", GATE_REASON="out-of-scope",
            GATE_PR_AUTHOR="bob")

    out = json.loads(r.stdout)
    assert out["action"] == "noop"
    assert "gate:rejected" in out["reason"]

    gate = _read_approval(engine_env, tmp_path, "reject")
    assert gate["gates"]["state"] == "rejected"
    # The gate file itself is marked failed (do_resolve_gate sets gdata["state"]="failed")
    assert gate.get("state") == "failed"

    # Aggregate pipeline check-run marked failure
    assert "check-run code-review" in r.stderr
    assert "status=completed" in r.stderr

    # Phase label must be "failed"
    inst = _read_instance(engine_env, tmp_path, "reject-inst")
    assert inst.get("phase_label") == "❌ failed"

    # A subsequent resolve attempt must be refused (gate rejected = terminal)
    r2 = run("next.py", tmp_path / "rj2", "pr-1", PROTO, "resolve-gate",
             GATE_DECISION="approve", GATE_ACTOR="dave", GATE_REASON="",
             GATE_PR_AUTHOR="bob")
    out2 = json.loads(r2.stdout)
    assert out2["action"] == "halt"
    assert "rejected" in out2["reason"]


def test_self_approve_refused(engine_env, tmp_path):
    """Self-approve is refused when approve_excludes_author==true in the gate.

    code-review/approval has approve_excludes_author=true. When GATE_ACTOR ==
    GATE_PR_AUTHOR the engine must refuse with a halt action; the gate must stay
    open (gates.state still "open").
    """
    base, run = _drive_to_gate(engine_env, tmp_path)

    # alice is both the actor and the PR author
    r = run("next.py", tmp_path / "sa", "pr-1", PROTO, "resolve-gate",
            GATE_DECISION="approve", GATE_ACTOR="alice", GATE_REASON="self-approve",
            GATE_PR_AUTHOR="alice")

    out = json.loads(r.stdout)
    # Refused → halt
    assert out["action"] == "halt"
    assert "self-approve" in out["reason"]

    # Gate must remain open
    gate = _read_approval(engine_env, tmp_path, "self-approve")
    assert gate["gates"]["state"] == "open"

    # Phase label must NOT be "failed" (gate is still live)
    inst = _read_instance(engine_env, tmp_path, "self-approve-inst")
    phase_label = inst.get("phase_label", "")
    assert phase_label != "❌ failed"

    # A different reviewer can still approve
    r2 = run("next.py", tmp_path / "sa-fix", "pr-1", PROTO, "resolve-gate",
             GATE_DECISION="approve", GATE_ACTOR="bob", GATE_REASON="lgtm",
             GATE_PR_AUTHOR="alice")
    out2 = json.loads(r2.stdout)
    assert out2["action"] == "noop"
    assert "gate:approved" in out2["reason"]
