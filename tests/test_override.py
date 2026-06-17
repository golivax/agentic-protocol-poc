"""HITL override gate — engine-side behavior (the `halted` marker, the override
command, and its guards). All GitHub I/O is ENGINE_LOCAL stderr no-ops."""
import json
import os
import subprocess

import pytest
import yaml

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
NEXT_PY = ENGINE / "next.py"
ADVANCE_PY = ENGINE / "advance.py"
LIB_PY = ENGINE / "lib.py"
PIPELINE_PROTO = ROOT / ".github/agent-factory/protocols/code-review-pipeline/protocol.json"
PID = json.load(open(PIPELINE_PROTO))["name"]
REVIEW_BRANCHES = [
    b["id"]
    for s in json.load(open(PIPELINE_PROTO))["states"]
    if s["id"] == "review"
    for b in s["branches"]
]


def _env(state_origin, **extra):
    e = dict(os.environ)
    e["ENGINE_LOCAL"] = "1"
    e["STATE_REMOTE"] = str(state_origin)
    e["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    e.update(extra)
    return e


def _run(script, args, env):
    r = subprocess.run(
        ["python3", str(script), *map(str, args)],
        text=True, capture_output=True, env=env,
    )
    return r.stdout, r.stderr, r.returncode


def _clone(state_origin, target):
    subprocess.run(
        ["git", "clone", "-q", "--branch", "agentic-state", str(state_origin), str(target)],
        check=True,
    )


def seed_preflight(state_origin, work, instance, *, state, iteration, head_sha):
    """Seed a preflight phase state + _instance cursor (no halted marker) and push."""
    _run(LIB_PY, ["state-checkout", str(work)], _env(state_origin))
    base = work / PID / instance
    base.mkdir(parents=True, exist_ok=True)
    (base / "preflight.yaml").write_text(yaml.safe_dump({
        "protocol": PID, "instance": instance, "state": state,
        "iteration": iteration, "gates": {}, "head_sha": head_sha, "history": [],
    }))
    (base / "_instance.yaml").write_text(yaml.safe_dump({
        "protocol": PID, "instance": instance, "phase": "preflight",
        "head_sha": head_sha, "joined": False,
    }))
    _run(LIB_PY, ["cas-push", str(work), f"seed {instance}"], _env(state_origin))


def write_json(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return p


# Verdicts that drive advance.py to each terminal:
#   block: a block-severity check fails, no iterate fail → process=done, blocking=True
VERDICTS_BLOCK = {"results": [
    {"check": "spec-present", "pass": False, "on_fail": "block", "feedback": "missing spec"},
    {"check": "preflight-schema-valid", "pass": True, "on_fail": "iterate", "feedback": ""},
]}
#   exhaust: an iterate-severity check fails with no iterations remaining → process=failed
VERDICTS_ITER_FAIL = {"results": [
    {"check": "preflight-schema-valid", "pass": False, "on_fail": "iterate", "feedback": "bad evidence"},
]}
EVIDENCE_MIN = {"checks": [], "examined": []}


def test_block_halt_stamps_halted_marker(state_origin, tmp_path):
    inst = "pr-1"
    seed_preflight(state_origin, tmp_path / "seed", inst, state="preflight", iteration=1, head_sha="sha-block")
    v = write_json(tmp_path, "verdicts.json", VERDICTS_BLOCK)
    ev = write_json(tmp_path, "evidence.json", EVIDENCE_MIN)
    env = _env(state_origin, PHASE="preflight", PR="1", PR_HEAD_SHA="sha-block")
    _run(ADVANCE_PY, [tmp_path / "adv", inst, PIPELINE_PROTO, v, ev], env)

    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load((tmp_path / "verify" / PID / inst / "_instance.yaml").read_text())
    assert inf["halted"] == {"phase": "preflight", "reason": "blocked", "sha": "sha-block"}
    pf = yaml.safe_load((tmp_path / "verify" / PID / inst / "preflight.yaml").read_text())
    assert pf["state"] == "failed"


def test_exhaustion_writes_no_halted_marker(state_origin, tmp_path):
    inst = "pr-2"
    # iteration == max_iterations (2) → no iterations remaining → process=failed
    seed_preflight(state_origin, tmp_path / "seed", inst, state="preflight", iteration=2, head_sha="sha-exh")
    v = write_json(tmp_path, "verdicts.json", VERDICTS_ITER_FAIL)
    ev = write_json(tmp_path, "evidence.json", EVIDENCE_MIN)
    env = _env(state_origin, PHASE="preflight", PR="2", PR_HEAD_SHA="sha-exh")
    _run(ADVANCE_PY, [tmp_path / "adv", inst, PIPELINE_PROTO, v, ev], env)

    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load((tmp_path / "verify" / PID / inst / "_instance.yaml").read_text())
    assert "halted" not in inf
    pf = yaml.safe_load((tmp_path / "verify" / PID / inst / "preflight.yaml").read_text())
    assert pf["state"] == "failed"


def test_post_pr_comment_local_noop(state_origin, capfd):
    """In ENGINE_LOCAL the helper must not call gh; it logs to stderr and returns None."""
    out, err, rc = _run(
        LIB_PY, ["post-pr-comment", "7", "hello world"],
        _env(state_origin),
    )
    assert rc == 0
    assert "pr#7" in err and "hello world" in err


def _seed_blocked(state_origin, tmp_path, inst, head_sha="sha-blk"):
    """Produce a real blocked-halt state (preflight failed + halted marker)."""
    seed_preflight(state_origin, tmp_path / "seed", inst, state="preflight", iteration=1, head_sha=head_sha)
    v = write_json(tmp_path, "v.json", VERDICTS_BLOCK)
    ev = write_json(tmp_path, "ev.json", EVIDENCE_MIN)
    env = _env(state_origin, PHASE="preflight", PR=inst[3:], PR_HEAD_SHA=head_sha)
    _run(ADVANCE_PY, [tmp_path / "adv", inst, PIPELINE_PROTO, v, ev], env)


def test_override_advances_one_phase(state_origin, tmp_path):
    inst = "pr-10"
    _seed_blocked(state_origin, tmp_path, inst)
    env = _env(state_origin, OVERRIDE_ACTOR="alice", OVERRIDE_REASON="ship it")
    out, err, rc = _run(NEXT_PY, [tmp_path / "ovr", inst, PIPELINE_PROTO, "override", "sha-blk"], env)
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-fanout"
    assert action["phase"] == "review"

    _clone(state_origin, tmp_path / "verify")
    base = tmp_path / "verify" / PID / inst
    inf = yaml.safe_load((base / "_instance.yaml").read_text())
    assert inf["phase"] == "review"
    assert "halted" not in inf
    assert inf["overrides"] == [{"phase": "preflight", "actor": "alice", "reason": "ship it"}]
    # The blocked gate's verdict is preserved — never rewritten.
    assert yaml.safe_load((base / "preflight.yaml").read_text())["state"] == "failed"
    # The next phase (review fan-out) was seeded.
    for b in REVIEW_BRANCHES:
        assert (base / f"review.{b}.yaml").is_file()


def test_override_announcement_names_actor(state_origin, tmp_path):
    inst = "pr-11"
    _seed_blocked(state_origin, tmp_path, inst)
    env = _env(state_origin, OVERRIDE_ACTOR="bob", OVERRIDE_REASON="")
    _, err, rc = _run(NEXT_PY, [tmp_path / "ovr", inst, PIPELINE_PROTO, "override", "sha-blk"], env)
    assert rc == 0
    assert "overridden by @bob" in err  # the ENGINE_LOCAL comment log


def test_override_on_exhausted_gate_refuses(state_origin, tmp_path):
    inst = "pr-12"
    # preflight failed, but NO halted marker (exhaustion shape).
    seed_preflight(state_origin, tmp_path / "seed", inst, state="failed", iteration=2, head_sha="s")
    out, err, rc = _run(NEXT_PY, [tmp_path / "ovr", inst, PIPELINE_PROTO, "override", "s"], _env(state_origin))
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "exhausted" in err
    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load((tmp_path / "verify" / PID / inst / "_instance.yaml").read_text())
    assert inf["phase"] == "preflight" and "overrides" not in inf


def test_override_no_instance_refuses(state_origin, tmp_path):
    out, err, rc = _run(NEXT_PY, [tmp_path / "ovr", "pr-99", PIPELINE_PROTO, "override", "s"], _env(state_origin))
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "no" in err.lower() and "run exists" in err


def test_override_not_halted_refuses(state_origin, tmp_path):
    inst = "pr-13"
    # preflight still active (state == life_state, not failed), no halted marker.
    seed_preflight(state_origin, tmp_path / "seed", inst, state="preflight", iteration=1, head_sha="s")
    out, err, rc = _run(NEXT_PY, [tmp_path / "ovr", inst, PIPELINE_PROTO, "override", "s"], _env(state_origin))
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "not currently halted" in err


def test_override_is_idempotent(state_origin, tmp_path):
    inst = "pr-14"
    _seed_blocked(state_origin, tmp_path, inst)
    env = _env(state_origin, OVERRIDE_ACTOR="alice", OVERRIDE_REASON="")
    _run(NEXT_PY, [tmp_path / "ovr1", inst, PIPELINE_PROTO, "override", "sha-blk"], env)
    # second override: marker already cleared, cursor on review → "not halted", no double-advance
    out, err, rc = _run(NEXT_PY, [tmp_path / "ovr2", inst, PIPELINE_PROTO, "override", "sha-blk"], env)
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load((tmp_path / "verify" / PID / inst / "_instance.yaml").read_text())
    assert len(inf["overrides"]) == 1  # not doubled


def test_override_reason_is_inert_data(state_origin, tmp_path):
    inst = "pr-15"
    _seed_blocked(state_origin, tmp_path, inst)
    nasty = "$(rm -rf /); `id`; <b>x</b>"
    env = _env(state_origin, OVERRIDE_ACTOR="alice", OVERRIDE_REASON=nasty)
    _run(NEXT_PY, [tmp_path / "ovr", inst, PIPELINE_PROTO, "override", "sha-blk"], env)
    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load((tmp_path / "verify" / PID / inst / "_instance.yaml").read_text())
    assert inf["overrides"][0]["reason"] == nasty  # stored verbatim, never executed


def test_override_trigger_maps_to_command():
    out = subprocess.run(
        ["python3", str(LIB_PY), "match-trigger", str(PIPELINE_PROTO),
         "issue_comment", "", "/override please"],
        text=True, capture_output=True,
    ).stdout.strip()
    assert out == "override"


def test_override_routes_unambiguously():
    protocols_dir = str(ROOT / ".github/agent-factory/protocols")
    r = subprocess.run(
        ["python3", str(LIB_PY), "route", protocols_dir, "issue_comment", "", "/override", "", "true"],
        text=True, capture_output=True,
    )
    assert r.returncode == 0, r.stderr  # no ambiguity raised
    assert f"protocols/{PID}/protocol.json" in r.stdout
    assert "skip=false" in r.stdout


def test_review_still_routes_after_adding_override():
    protocols_dir = str(ROOT / ".github/agent-factory/protocols")
    r = subprocess.run(
        ["python3", str(LIB_PY), "route", protocols_dir, "issue_comment", "", "/review", "", "true"],
        text=True, capture_output=True,
    )
    assert r.returncode == 0, r.stderr
    assert f"protocols/{PID}/protocol.json" in r.stdout
    assert "skip=false" in r.stdout
