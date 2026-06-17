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
