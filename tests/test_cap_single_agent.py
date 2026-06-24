"""test_cap_single_agent.py — Task 9: Single-agent capability fixture + walk.

Proves the unified engine handles a one-`agent`-state protocol (root sequence,
single child) entirely via NODE_PATH:

  start  → enter_root → run-agent at depth-1; _instance.yaml phase=solo seeded.
  advance NODE_PATH=solo, pass verdicts → no-next finalize → pipeline complete.
  advance NODE_PATH=solo, fail verdicts (×2) → exhaust max_iterations → failed.
"""
import json
import subprocess
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / "tests/fixtures/cap-single-agent/protocol.json"
NEXT = ENG / "next.py"
ADVANCE = ENG / "advance.py"

PID = "single-agent"   # name field in the fixture protocol


def _yaml(p):
    return yaml.safe_load(open(p))


def _reclone(engine_env, tmp_path, tag):
    """Re-clone the state branch from the bare origin (simulates matrix leg re-checkout)."""
    d = tmp_path / f"rc-{tag}"
    subprocess.run(
        ["git", "clone", "-q", "-b", "agentic-state",
         engine_env["STATE_REMOTE"], str(d)],
        check=True,
    )
    return d / PID / "pr-1"


def _pass_verdicts(tmp_path, tag):
    v = tmp_path / f"v-pass-{tag}.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))
    ev = tmp_path / f"ev-{tag}.json"
    ev.write_text("{}")
    return v, ev


def _fail_verdicts(tmp_path, tag):
    v = tmp_path / f"v-fail-{tag}.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": False, "feedback": "forced-fail", "on_fail": "iterate"}
    ]}))
    ev = tmp_path / f"ev-f-{tag}.json"
    ev.write_text("{}")
    return v, ev


def _run(script, *args, env, **env_extra):
    e = dict(env); e.update(env_extra)
    r = subprocess.run(["python3", str(script), *map(str, args)],
                       text=True, capture_output=True, env=e)
    return r


# ---------------------------------------------------------------------------
# Test 1: start → enter_root → run-agent + _instance.yaml seeded
# ---------------------------------------------------------------------------

def test_start_single_agent_emits_run_agent(engine_env, tmp_path):
    """start on a single-agent protocol must emit run-agent (not halt/noop) and
    seed _instance.yaml with phase=solo via enter_root."""
    r = _run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "sha1", env=engine_env)
    assert r.returncode == 0, r.stderr
    act = json.loads(r.stdout)
    assert act["action"] == "run-agent", f"expected run-agent, got: {act}"

    # _instance.yaml must exist with phase=solo
    fdir = _reclone(engine_env, tmp_path, "start")
    inst_yaml = fdir / "_instance.yaml"
    assert inst_yaml.is_file(), "_instance.yaml must be seeded by enter_root on start"
    inst = _yaml(inst_yaml)
    assert inst.get("phase") == "solo", f"expected phase=solo, got: {inst}"

    # Agent state file must be seeded (flat path: <pid>/<instance>.yaml)
    # For a single-phase single-agent protocol, state_path(proto, ["solo"]) = []
    # → state_file = <pid>/<instance>.yaml
    state_file = fdir.parent / "pr-1.yaml"
    assert state_file.is_file(), "agent state file (pr-1.yaml) must be seeded"
    sf = _yaml(state_file)
    assert sf.get("state") == "solo", f"agent state must be 'solo', got: {sf}"
    assert sf.get("iteration") == 1, f"iteration must be 1, got: {sf}"


# ---------------------------------------------------------------------------
# Test 2: advance pass → no-next finalize (pipeline complete)
# ---------------------------------------------------------------------------

def test_advance_pass_finalizes_pipeline(engine_env, tmp_path):
    """start then advance NODE_PATH=solo with passing verdicts → pipeline complete
    (no-next finalize branch: aggregate check-run success + done label)."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "sha1"
    base["AGENT_RUN_ID"] = "r1"

    # 1. start
    r = _run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "sha1", env=base)
    assert r.returncode == 0, r.stderr

    # 2. advance with passing verdicts
    v, ev = _pass_verdicts(tmp_path, "pass")
    r2 = _run(ADVANCE, tmp_path / "a1", "pr-1", PROTO, v, ev,
              env=base, NODE_PATH="solo")
    assert r2.returncode == 0, r2.stderr

    # must NOT dispatch protocol-continue or protocol-advance
    assert "event_type=protocol-continue" not in r2.stderr, r2.stderr
    assert "event_type=protocol-advance" not in r2.stderr, r2.stderr

    # state file must be marked done
    fdir = _reclone(engine_env, tmp_path, "done")
    state_file = fdir.parent / "pr-1.yaml"
    assert state_file.is_file(), "state file must persist"
    sf = _yaml(state_file)
    assert sf.get("state") == "done", f"state must be done, got: {sf}"


# ---------------------------------------------------------------------------
# Test 3: advance fail ×max_iterations → exhaust → failed
# ---------------------------------------------------------------------------

def test_advance_fail_exhausts_to_failed(engine_env, tmp_path):
    """start then advance NODE_PATH=solo with failing verdicts twice (max_iterations=2)
    → first advance iterates (re-dispatch), second exhausts → state=failed."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "sha1"
    base["AGENT_RUN_ID"] = "r1"

    # 1. start
    r = _run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "sha1", env=base)
    assert r.returncode == 0, r.stderr

    # 2. first advance: fail → iterate (re-dispatch protocol-continue)
    fv, fev = _fail_verdicts(tmp_path, "f1")
    r2 = _run(ADVANCE, tmp_path / "a1", "pr-1", PROTO, fv, fev,
              env=base, NODE_PATH="solo")
    assert r2.returncode == 0, r2.stderr
    assert "event_type=protocol-continue" in r2.stderr, (
        f"expected iterate re-dispatch on first fail, got: {r2.stderr}")

    # iteration counter must have advanced to 2
    fdir2 = _reclone(engine_env, tmp_path, "iter2")
    sf2 = _yaml(fdir2.parent / "pr-1.yaml")
    assert sf2.get("iteration") == 2, f"iteration must be 2 after first fail, got: {sf2}"
    # still in flight
    assert sf2.get("state") == "solo", f"state must still be solo (in flight), got: {sf2}"

    # 3. second advance: fail → exhausted → failed
    fv2, fev2 = _fail_verdicts(tmp_path, "f2")
    base2 = dict(base); base2["AGENT_RUN_ID"] = "r2"
    r3 = _run(ADVANCE, tmp_path / "a2", "pr-1", PROTO, fv2, fev2,
              env=base2, NODE_PATH="solo")
    assert r3.returncode == 0, r3.stderr
    # must NOT re-dispatch (exhausted)
    assert "event_type=protocol-continue" not in r3.stderr, (
        f"must not re-dispatch after exhaustion, got: {r3.stderr}")

    fdir3 = _reclone(engine_env, tmp_path, "failed")
    sf3 = _yaml(fdir3.parent / "pr-1.yaml")
    assert sf3.get("state") == "failed", f"state must be failed after exhaustion, got: {sf3}"
