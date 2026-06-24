"""test_cap_override.py — Task 12: /override HITL escape-hatch capability.

Proves the blocked-gate halt + /override flow through the unified NODE_PATH path:

  1. advance.py with NODE_PATH=preflight + blocking verdicts (conclude returns blocked
     AND on_blocked==halt) → stamps halted:{reason:blocked,phase:preflight} on
     _instance.yaml and marks preflight.yaml state=failed.

  2. next.py override (OVERRIDE_ACTOR+OVERRIDE_REASON set) on a halted instance →
     clears the halted marker, records an overrides[] entry, advances
     _instance.phase to the next sibling (review), and dispatches
     event_type=protocol-continue with client_payload[path]=review (visible in
     ENGINE_LOCAL stderr). Action emitted: noop, reason contains
     "override:continue:review".

  3. next.py override on a not-halted instance (active, no halted marker) →
     refuses with action=halt, "not currently halted" in stderr, no state change.

  4. next.py override on an exhausted instance (state=failed, no halted marker) →
     refuses with action=halt, "exhausted" in stderr, no state change.

These tests drive the code-review protocol exclusively via NODE_PATH, reusing the
`engine_env` fixture (ENGINE_LOCAL=1, STATE_REMOTE pointing at a bare origin) from
conftest.py. No new fixtures or protocol changes are needed.
"""

import json
import pathlib
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG  = ROOT / ".github/agent-factory/engine"
NEXT = ENG / "next.py"
ADV  = ENG / "advance.py"
PROTO = ROOT / ".github/agent-factory/protocols/code-review/protocol.json"

PID = json.loads(PROTO.read_text())["name"]   # "code-review"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _reclone(engine_env, tmp_path, tag):
    """Clone the agentic-state branch from the bare origin and return the path."""
    d = tmp_path / f"rc-{tag}"
    subprocess.run(
        ["git", "clone", "-q", "-b", "agentic-state",
         engine_env["STATE_REMOTE"], str(d)],
        check=True,
    )
    return d


def _run(script, *args, env, **env_extra):
    """Run a Python engine script and return the CompletedProcess."""
    e = dict(env)
    e.update(env_extra)
    return subprocess.run(
        ["python3", str(script), *map(str, args)],
        text=True, capture_output=True, env=e,
    )


def _block_verdicts(tmp_path, tag="bv"):
    """Verdicts that drive decide() to process=done, blocking=True.

    A block-severity check failing means process==done (not iterate/failed)
    AND blocking==True. The conclude hook sees BLOCKING=1 and returns blocked=True.
    advance.py then hits the on_blocked==halt arm and stamps the halted marker.
    """
    v = tmp_path / f"v-block-{tag}.json"
    v.write_text(json.dumps({"results": [
        {"check": "spec-present",          "pass": False, "on_fail": "block",   "feedback": "no spec"},
        {"check": "preflight-schema-valid", "pass": True,  "on_fail": "iterate", "feedback": ""},
    ]}))
    ev = tmp_path / f"ev-block-{tag}.json"
    ev.write_text(json.dumps({"checks": [], "examined": []}))
    return v, ev


def _pass_verdicts(tmp_path, tag="pv"):
    """Passing verdicts (used when seeding intermediate states quickly)."""
    v = tmp_path / f"v-pass-{tag}.json"
    v.write_text(json.dumps({"results": [
        {"check": "preflight-schema-valid", "pass": True, "on_fail": "iterate", "feedback": ""},
    ]}))
    ev = tmp_path / f"ev-pass-{tag}.json"
    ev.write_text(json.dumps({"checks": [], "examined": []}))
    return v, ev


def _seed_blocked(engine_env, tmp_path, inst="pr-42"):
    """Drive a full start → advance-blocked cycle, leaving the instance in the
    halted:{reason:blocked} state (preflight failed, _instance.halted stamped).

    Returns the instance key so callers can use it in assertions.
    """
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "sha-blocked"
    base["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    base["PR"] = inst[len("pr-"):]

    # 1. start → enter_root → seeds preflight
    r = _run(NEXT, tmp_path / f"seed-s-{inst}", inst, PROTO, "start", "sha-blocked",
             env=base)
    assert r.returncode == 0, f"start failed:\n{r.stderr}"

    # 2. advance NODE_PATH=preflight with blocking verdicts → halted marker stamped
    bv, bev = _block_verdicts(tmp_path, tag=inst)
    r = _run(ADV, tmp_path / f"seed-a-{inst}", inst, PROTO, bv, bev,
             env=base, NODE_PATH="preflight")
    assert r.returncode == 0, f"advance failed:\n{r.stderr}"
    return inst


# ---------------------------------------------------------------------------
# Test 1: blocked gate stamps halted marker on _instance.yaml
# ---------------------------------------------------------------------------

def test_blocked_gate_stamps_halted_marker(engine_env, tmp_path):
    """advance.py NODE_PATH=preflight + blocking verdict → _instance.yaml carries
    halted:{reason:blocked,phase:preflight}; preflight.yaml state=failed."""
    inst = _seed_blocked(engine_env, tmp_path, "pr-t1")

    rc = _reclone(engine_env, tmp_path, "t1")
    inf = yaml.safe_load((rc / PID / inst / "_instance.yaml").read_text())
    assert inf.get("halted", {}).get("reason") == "blocked"
    assert inf["halted"].get("phase") == "preflight"

    pf = yaml.safe_load((rc / PID / inst / "preflight.yaml").read_text())
    assert pf.get("state") == "failed"


# ---------------------------------------------------------------------------
# Test 2: /override (authorized) clears halted, advances one phase
# ---------------------------------------------------------------------------

def test_override_clears_halted_and_dispatches_continue(engine_env, tmp_path):
    """/override (OVERRIDE_ACTOR set, instance halted:blocked at preflight) must:
      - clear the halted marker from _instance.yaml
      - record overrides[{phase:preflight,actor:alice,reason:ship it}]
      - advance _instance.phase to "review" (next sibling)
      - emit action=noop with reason containing "override:continue:review"
      - dispatch event_type=protocol-continue with client_payload[path]=review (stderr)
    """
    inst = _seed_blocked(engine_env, tmp_path, "pr-t2")

    env = dict(engine_env)
    env["OVERRIDE_ACTOR"] = "alice"
    env["OVERRIDE_REASON"] = "ship it"
    env["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    env["PR"] = inst[len("pr-"):]
    env["PR_HEAD_SHA"] = "sha-blocked"

    r = _run(NEXT, tmp_path / "ov-t2", inst, PROTO, "override", "sha-blocked", env=env)
    assert r.returncode == 0, f"override failed:\n{r.stderr}"

    act = json.loads(r.stdout)
    assert act["action"] == "noop", f"expected noop, got: {act}"
    assert "override:continue:review" in act["reason"], f"reason: {act['reason']}"

    # dispatch is in ENGINE_LOCAL stderr
    assert "event_type=protocol-continue" in r.stderr, r.stderr
    assert "client_payload[path]=review" in r.stderr, r.stderr

    # verify state
    rc = _reclone(engine_env, tmp_path, "t2-verify")
    inf = yaml.safe_load((rc / PID / inst / "_instance.yaml").read_text())
    assert "halted" not in inf, "halted marker must be cleared after override"
    assert inf.get("phase") == "review", f"phase must advance to review, got: {inf.get('phase')}"
    assert inf.get("overrides") == [
        {"phase": "preflight", "actor": "alice", "reason": "ship it"}
    ], f"overrides mismatch: {inf.get('overrides')}"

    # blocked preflight verdict preserved — not rewritten
    pf = yaml.safe_load((rc / PID / inst / "preflight.yaml").read_text())
    assert pf.get("state") == "failed", "preflight.yaml state must stay failed after override"


# ---------------------------------------------------------------------------
# Test 3: /override on not-halted instance is refused
# ---------------------------------------------------------------------------

def test_override_refuses_when_not_halted(engine_env, tmp_path):
    """/override on a live (non-halted) instance must refuse: action=halt,
    "not currently halted" message in stderr, no state change.

    We drive start (preflight seeded, state active) and immediately call override
    without any advance — so there is no halted marker."""
    inst = "pr-t3"
    base = dict(engine_env)
    base["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    base["PR"] = inst[len("pr-"):]
    base["PR_HEAD_SHA"] = "sha-active"

    # start → preflight seeded and active
    r = _run(NEXT, tmp_path / "s-t3", inst, PROTO, "start", "sha-active", env=base)
    assert r.returncode == 0, r.stderr

    # override on active (not halted)
    r = _run(NEXT, tmp_path / "ov-t3", inst, PROTO, "override", "sha-active",
             env=base, OVERRIDE_ACTOR="alice", OVERRIDE_REASON="")
    assert r.returncode == 0, r.stderr

    act = json.loads(r.stdout)
    assert act["action"] == "halt", f"expected halt, got: {act}"
    assert "not currently halted" in r.stderr, f"expected refusal message, got: {r.stderr}"

    # no state change: _instance.phase still preflight, no overrides key
    rc = _reclone(engine_env, tmp_path, "t3-verify")
    inf = yaml.safe_load((rc / PID / inst / "_instance.yaml").read_text())
    assert inf.get("phase") == "preflight", f"phase must stay preflight, got: {inf.get('phase')}"
    assert "overrides" not in inf, "overrides must not be added on a refusal"


# ---------------------------------------------------------------------------
# Test 4: /override on exhausted instance is refused
# ---------------------------------------------------------------------------

def test_override_refuses_when_exhausted(engine_env, tmp_path):
    """/override on an exhausted instance (state=failed, NO halted marker) must
    refuse with action=halt, "exhausted" in stderr, no state change.

    To induce exhaustion without a conclude hook we need iterate-only verdicts to
    exhaust max_iterations. We drive start → advance (fail, iter 1) →
    advance (fail, iter 2 = max) → exhausted, then call override."""
    inst = "pr-t4"
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "sha-exh"
    base["AGENT_RUN_ID"] = "r1"
    base["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    base["PR"] = inst[len("pr-"):]

    # start
    r = _run(NEXT, tmp_path / "s-t4", inst, PROTO, "start", "sha-exh", env=base)
    assert r.returncode == 0, r.stderr

    # iterate-only failing verdicts (no block-severity)
    iter_fail_v = tmp_path / "v-iter-fail-t4.json"
    iter_fail_v.write_text(json.dumps({"results": [
        {"check": "preflight-schema-valid", "pass": False, "on_fail": "iterate",
         "feedback": "bad schema"},
    ]}))
    iter_fail_ev = tmp_path / "ev-iter-fail-t4.json"
    iter_fail_ev.write_text(json.dumps({"checks": [], "examined": []}))

    # preflight max_iterations from protocol
    import json as _json
    proto = _json.loads(PROTO.read_text())
    for s in proto["states"]:
        if s["id"] == "preflight":
            max_iter = s.get("max_iterations", 3)
            break

    # exhaust all iterations
    for i in range(1, max_iter + 1):
        env_i = dict(base); env_i["AGENT_RUN_ID"] = f"r{i}"
        r = _run(ADV, tmp_path / f"a-exh-{i}", inst, PROTO, iter_fail_v, iter_fail_ev,
                 env=env_i, NODE_PATH="preflight")
        assert r.returncode == 0, r.stderr

    # verify exhausted (no halted marker)
    rc0 = _reclone(engine_env, tmp_path, "t4-exh")
    inf0 = yaml.safe_load((rc0 / PID / inst / "_instance.yaml").read_text())
    pf0 = yaml.safe_load((rc0 / PID / inst / "preflight.yaml").read_text())
    assert pf0.get("state") == "failed", "preflight must be failed after exhaustion"
    assert "halted" not in inf0, "halted marker must NOT be set on exhaustion"

    # now call override → must refuse with "exhausted"
    r = _run(NEXT, tmp_path / "ov-t4", inst, PROTO, "override", "sha-exh",
             env=base, OVERRIDE_ACTOR="bob", OVERRIDE_REASON="")
    assert r.returncode == 0, r.stderr

    act = json.loads(r.stdout)
    assert act["action"] == "halt", f"expected halt, got: {act}"
    assert "exhausted" in r.stderr, f"expected exhausted message, got: {r.stderr}"

    # no state change
    rc = _reclone(engine_env, tmp_path, "t4-verify")
    inf = yaml.safe_load((rc / PID / inst / "_instance.yaml").read_text())
    assert "overrides" not in inf, "overrides must not be added on exhaustion refusal"
