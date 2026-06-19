"""v4 pause-and-require approval gate — engine-side behavior. All GitHub I/O is
ENGINE_LOCAL stderr no-ops we assert on. Mirrors tests/test_override.py style."""
import itertools
import json
import os
import subprocess
import sys

import pytest
import yaml

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
NEXT_PY = ENGINE / "next.py"
JOIN_PY = ENGINE / "join.py"
LIB_PY = ENGINE / "lib.py"
PIPELINE_PROTO = ROOT / ".github/agent-factory/protocols/code-review-pipeline/protocol.json"
PID = json.load(open(PIPELINE_PROTO))["name"]

sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def test_next_phase_id_returns_gate_kind():
    proto = {"states": [
        {"id": "a", "kind": "agent", "next": "g"},
        {"id": "g", "kind": "gate", "next": "done"},
    ]}
    assert lib.next_phase_id(proto, "a") == "g"
    # a gate whose next is a terminal → None (finalize)
    assert lib.next_phase_id(proto, "g") is None


def test_pipeline_states_includes_gate_in_order():
    proto = {"states": [
        {"id": "a", "kind": "agent"},
        {"id": "f", "kind": "fanout"},
        {"id": "j", "kind": "join"},
        {"id": "g", "kind": "gate"},
    ]}
    assert [s["id"] for s in lib.pipeline_states(proto)] == ["a", "f", "g"]


def test_open_gate_seeds_file_and_check_run(tmp_path, capfd, monkeypatch):
    monkeypatch.setenv("ENGINE_LOCAL", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "golivax/agentic-protocol-poc")
    d = tmp_path / "state"
    base = d / PID / "pr-1"
    base.mkdir(parents=True)
    # an instance cursor file must exist for the status-comment refresh path
    (base / "_instance.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": "pr-1", "phase": "approval",
         "head_sha": "sha9", "joined": True, "status_comment_id": 5}))
    proto_path = tmp_path / "p.json"
    proto_path.write_text(json.dumps({"name": PID, "states": [
        {"id": "approval", "kind": "gate", "next": "done"}]}))
    lib.open_gate(str(d), PID, "pr-1", str(proto_path), "approval", "sha9", "1")

    gate = yaml.safe_load((base / "approval.yaml").read_text())
    assert gate["gates"] == {"state": "open", "history": []}
    assert gate["state"] == "approval"
    assert gate["head_sha"] == "sha9"
    err = capfd.readouterr().err
    assert "check-run code-review-pipeline/approval" in err
    assert "status=in_progress" in err


def _match(body):
    return subprocess.run(
        ["python3", str(LIB_PY), "match-trigger", str(PIPELINE_PROTO),
         "issue_comment", "", body],
        text=True, capture_output=True).stdout.strip()


@pytest.mark.parametrize("body", ["/approve", "/approve ship it",
                                  "/request-changes do X", "/reject nope"])
def test_resolve_triggers_map_to_command(body):
    assert _match(body) == "resolve-gate"


def test_review_still_routes_with_gate_triggers():
    assert _match("/review") == "start"


def test_protocol_has_gate_state():
    proto = json.load(open(PIPELINE_PROTO))
    g = next(s for s in proto["states"] if s["id"] == "approval")
    assert g["kind"] == "gate" and g["next"] == "done"
    assert g["approve_excludes_author"] is True
    j = next(s for s in proto["states"] if s["kind"] == "join")
    assert j["next"] == "approval"


def test_route_unambiguous_for_approve():
    pdir = str(ROOT / ".github/agent-factory/protocols")
    r = subprocess.run(["python3", str(LIB_PY), "route", pdir,
                        "issue_comment", "", "/approve", "", "true"],
                       text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    assert f"protocols/{PID}/protocol.json" in r.stdout and "skip=false" in r.stdout


def _seed_instance_with_gate(tmp_path, gate_state, actor="bob"):
    d = tmp_path / "state"
    base = d / PID / "pr-7"
    base.mkdir(parents=True)
    (base / "_instance.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": "pr-7", "phase": "approval",
         "head_sha": "s", "joined": True}))
    hist = [] if gate_state == "open" else [{"decision": gate_state, "actor": actor, "reason": ""}]
    (base / "approval.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": "pr-7", "state": "approval", "head_sha": "s",
         "gates": {"state": gate_state, "history": hist}}))
    return d


def test_render_gate_open_row_and_headline(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "golivax/agentic-protocol-poc")
    d = _seed_instance_with_gate(tmp_path, "open")
    body = lib.render_pipeline_status_body(str(d), PID, "pr-7", str(PIPELINE_PROTO))
    assert "**approval**" in body
    assert "awaiting human sign-off" in body
    assert "Awaiting human approval" in body  # headline


def test_render_gate_approved_row(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "golivax/agentic-protocol-poc")
    d = _seed_instance_with_gate(tmp_path, "approved", actor="carol")
    body = lib.render_pipeline_status_body(str(d), PID, "pr-7", str(PIPELINE_PROTO))
    assert "approved by @carol" in body


def _env(state_origin, **extra):
    e = dict(os.environ)
    e["ENGINE_LOCAL"] = "1"
    e["STATE_REMOTE"] = str(state_origin)
    e["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    e.update(extra)
    return e


def _run(script, args, env):
    r = subprocess.run(["python3", str(script), *map(str, args)],
                       text=True, capture_output=True, env=env)
    return r.stdout, r.stderr, r.returncode


def _clone(state_origin, target):
    subprocess.run(["git", "clone", "-q", "--branch", "agentic-state",
                    str(state_origin), str(target)], check=True)


def _seed_cursor(state_origin, work, instance, phase, *, head_sha="s"):
    """Seed an _instance.yaml cursor on `phase` and push (no per-phase files)."""
    _run(LIB_PY, ["state-checkout", str(work)], _env(state_origin))
    base = work / PID / instance
    base.mkdir(parents=True, exist_ok=True)
    (base / "_instance.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": instance, "phase": phase,
         "head_sha": head_sha, "joined": True}))
    _run(LIB_PY, ["cas-push", str(work), f"seed {instance}"], _env(state_origin))


REVIEW_BRANCHES = [b["id"] for s in json.load(open(PIPELINE_PROTO))["states"]
                   if s["id"] == "review" for b in s["branches"]]


def _seed_review_all_done(state_origin, work, instance, *, head_sha="js"):
    _run(LIB_PY, ["state-checkout", str(work)], _env(state_origin))
    base = work / PID / instance
    base.mkdir(parents=True, exist_ok=True)
    (base / "_instance.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": instance, "phase": "review",
         "head_sha": head_sha, "joined": False, "status_comment_id": 9}))
    for b in REVIEW_BRANCHES:
        (base / f"review.{b}.yaml").write_text(yaml.safe_dump(
            {"protocol": PID, "instance": instance, "state": "done",
             "iteration": 1, "gates": {}, "head_sha": head_sha, "history": []}))
    _run(LIB_PY, ["cas-push", str(work), f"seed {instance}"], _env(state_origin))


def test_join_opens_following_gate(state_origin, tmp_path):
    inst = "pr-21"
    _seed_review_all_done(state_origin, tmp_path / "seed", inst)
    env = _env(state_origin, PR="21", PR_HEAD_SHA="js")
    out, err, rc = _run(JOIN_PY, [tmp_path / "w", inst, PIPELINE_PROTO], env)
    assert rc == 0, err
    _clone(state_origin, tmp_path / "verify")
    base = tmp_path / "verify" / PID / inst
    inf = yaml.safe_load((base / "_instance.yaml").read_text())
    assert inf["phase"] == "approval" and inf["joined"] is True
    gate = yaml.safe_load((base / "approval.yaml").read_text())
    assert gate["gates"]["state"] == "open"
    # the aggregate pid check-run is NOT completed at the gate (still in_progress)
    assert "check-run code-review-pipeline sha=js status=completed" not in err


def test_advance_phase_into_gate_opens_it(state_origin, tmp_path):
    inst = "pr-20"
    _seed_cursor(state_origin, tmp_path / "seed", inst, "review")
    env = _env(state_origin, PHASE="approval")
    out, err, rc = _run(NEXT_PY, [tmp_path / "w", inst, PIPELINE_PROTO,
                                  "advance-phase", "s"], env)
    assert rc == 0, err
    assert json.loads(out)["action"] == "noop"
    assert json.loads(out)["reason"] == "gate-open:approval"
    _clone(state_origin, tmp_path / "verify")
    base = tmp_path / "verify" / PID / inst
    gate = yaml.safe_load((base / "approval.yaml").read_text())
    assert gate["gates"]["state"] == "open"
    assert yaml.safe_load((base / "_instance.yaml").read_text())["phase"] == "approval"


def _seed_open_gate(state_origin, work, instance, *, gstate="open", head_sha="gs"):
    _run(LIB_PY, ["state-checkout", str(work)], _env(state_origin))
    base = work / PID / instance
    base.mkdir(parents=True, exist_ok=True)
    (base / "_instance.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": instance, "phase": "approval",
         "head_sha": head_sha, "joined": True, "status_comment_id": 9}))
    hist = [] if gstate == "open" else [{"decision": "request-changes", "actor": "x", "reason": ""}]
    (base / "approval.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": instance, "state": "approval",
         "head_sha": head_sha, "gates": {"state": gstate, "history": hist}}))
    _run(LIB_PY, ["cas-push", str(work), f"seed {instance}"], _env(state_origin))


_resolve_counter = itertools.count()


def _resolve(state_origin, tmp_path, inst, decision, actor, reason="", pr_author="someone"):
    env = _env(state_origin, GATE_DECISION=decision, GATE_ACTOR=actor,
               GATE_REASON=reason, GATE_PR_AUTHOR=pr_author)
    return _run(NEXT_PY, [tmp_path / f"w-{decision}-{next(_resolve_counter)}", inst, PIPELINE_PROTO,
                          "resolve-gate", "gs"], env)


def test_resolve_approve_finalizes_last_gate(state_origin, tmp_path):
    inst = "pr-30"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "bob", "lgtm")
    assert rc == 0, err
    assert json.loads(out)["action"] == "noop"
    _clone(state_origin, tmp_path / "verify")
    g = yaml.safe_load((tmp_path / "verify" / PID / inst / "approval.yaml").read_text())
    assert g["gates"]["state"] == "approved"
    assert g["gates"]["history"][-1] == {"decision": "approve", "actor": "bob", "reason": "lgtm"}
    # final gate → aggregate pid check-run completed success
    assert "check-run code-review-pipeline sha=gs status=completed conclusion=success" in err


def test_resolve_request_changes_halts_no_cursor_move(state_origin, tmp_path):
    inst = "pr-31"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    out, err, rc = _resolve(state_origin, tmp_path, inst, "request-changes", "alice", "fix it")
    assert rc == 0, err
    assert json.loads(out)["action"] == "noop"
    _clone(state_origin, tmp_path / "verify")
    base = tmp_path / "verify" / PID / inst
    assert yaml.safe_load((base / "_instance.yaml").read_text())["phase"] == "approval"
    g = yaml.safe_load((base / "approval.yaml").read_text())
    assert g["gates"]["state"] == "changes_requested"
    assert g["state"] == "approval"  # NOT failed (non-terminal)


def test_resolve_changes_then_approve(state_origin, tmp_path):
    inst = "pr-32"
    _seed_open_gate(state_origin, tmp_path / "seed", inst, gstate="changes_requested")
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "bob")
    assert rc == 0, err
    assert json.loads(out)["action"] == "noop"
    _clone(state_origin, tmp_path / "verify")
    g = yaml.safe_load((tmp_path / "verify" / PID / inst / "approval.yaml").read_text())
    assert g["gates"]["state"] == "approved"


def test_resolve_reject_is_terminal(state_origin, tmp_path):
    inst = "pr-33"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    out, err, rc = _resolve(state_origin, tmp_path, inst, "reject", "carol", "no")
    assert rc == 0, err
    _clone(state_origin, tmp_path / "verify")
    g = yaml.safe_load((tmp_path / "verify" / PID / inst / "approval.yaml").read_text())
    assert g["gates"]["state"] == "rejected" and g["state"] == "failed"
    assert "check-run code-review-pipeline sha=gs status=completed conclusion=failure" in err


def test_resolve_reject_then_approve_refused(state_origin, tmp_path):
    inst = "pr-34"
    _seed_open_gate(state_origin, tmp_path / "seed", inst, gstate="rejected")
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "bob")
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "rejected" in err.lower()


def test_resolve_self_approval_refused(state_origin, tmp_path):
    inst = "pr-35"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "dave", pr_author="dave")
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "cannot approve their own" in err.lower()
    _clone(state_origin, tmp_path / "verify")
    g = yaml.safe_load((tmp_path / "verify" / PID / inst / "approval.yaml").read_text())
    assert g["gates"]["state"] == "open"  # unchanged


def test_resolve_not_a_gate_refused(state_origin, tmp_path):
    inst = "pr-36"
    _seed_cursor(state_origin, tmp_path / "seed", inst, "preflight")
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "bob")
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "no approval gate" in err.lower()


def test_resolve_no_instance_refused(state_origin, tmp_path):
    out, err, rc = _resolve(state_origin, tmp_path, "pr-99", "approve", "bob")
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "no" in err.lower() and "run exists" in err


def test_resolve_idempotent_double_approve(state_origin, tmp_path):
    inst = "pr-37"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    _resolve(state_origin, tmp_path, inst, "approve", "bob")
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "bob")
    # second approve: gate already approved (state != open/changes) → refused
    assert json.loads(out)["action"] == "halt"


def test_resolve_reason_is_inert(state_origin, tmp_path):
    inst = "pr-38"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    nasty = "$(rm -rf /); `id`; <b>x</b>"
    _resolve(state_origin, tmp_path, inst, "reject", "carol", reason=nasty)
    _clone(state_origin, tmp_path / "verify")
    g = yaml.safe_load((tmp_path / "verify" / PID / inst / "approval.yaml").read_text())
    assert g["gates"]["history"][-1]["reason"] == nasty
