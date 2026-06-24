"""test_cap_simple_fanout.py — Task 10: Simple-fanout capability fixture + walk.

Proves the unified engine handles a single-phase fanout (two flat agent legs)
→ join → done entirely via NODE_PATH:

  start         → run-fanout; legs f.a and f.b seeded, _instance.yaml present.
  advance f.a   → pass verdicts → a.yaml done, fire_join top (no path).
  advance f.b   → pass verdicts → b.yaml done, fire_join top (no path).
  join (top)    → both done → _instance.yaml joined=True (finalized, aggregate
                  success; join-f.next=done is the sentinel → no further dispatch).

No engine change is expected — this is the degenerate single-phase fanout, already
exercised by deep-fanout's top level.  This fixture proves the capability explicitly
and provides a regression anchor for the flat-fanout shape.
"""
import json
import subprocess
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG  = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / "tests/fixtures/simple-fanout/protocol.json"
NEXT    = ENG / "next.py"
ADVANCE = ENG / "advance.py"
JOIN    = ENG / "join.py"

PID = "simple-fanout"  # protocol.json "name" field


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _yaml(p):
    return yaml.safe_load(open(p))


def _reclone(engine_env, tmp_path, tag):
    """Re-clone the agentic-state branch from the bare origin."""
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


def _run(script, *args, env, **env_extra):
    e = dict(env); e.update(env_extra)
    r = subprocess.run(["python3", str(script), *map(str, args)],
                       text=True, capture_output=True, env=e)
    return r


# ---------------------------------------------------------------------------
# Step 1: start → run-fanout, legs seeded, _instance.yaml present
# ---------------------------------------------------------------------------

def test_start_emits_run_fanout_and_seeds_legs(engine_env, tmp_path):
    """start on simple-fanout must emit run-fanout with legs f.a and f.b,
    seed their state files, and create _instance.yaml."""
    r = _run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "sha1", env=engine_env)
    assert r.returncode == 0, r.stderr

    act = json.loads(r.stdout)
    assert act["action"] == "run-fanout", f"expected run-fanout, got: {act}"

    # Both legs must appear in the emitted matrix with their full tree paths.
    leg_paths = {lg["path"] for lg in act.get("legs", [])}
    assert leg_paths == {"f.a", "f.b"}, f"expected legs {{f.a, f.b}}, got: {leg_paths}"

    # Re-clone and verify persistence.
    fdir = _reclone(engine_env, tmp_path, "start")

    # Leg state files seeded (single-phase drops leading "f").
    assert (fdir / "a.yaml").is_file(), "a.yaml must be seeded"
    assert (fdir / "b.yaml").is_file(), "b.yaml must be seeded"

    # _instance.yaml must exist (created by start_fanout).
    assert (fdir / "_instance.yaml").is_file(), "_instance.yaml must be created on start"
    inst = _yaml(fdir / "_instance.yaml")
    assert not inst.get("joined"), "_instance.yaml must not be joined yet"

    # Leg files are in the correct initial life-state.
    a_st = _yaml(fdir / "a.yaml")
    assert a_st.get("state") == "f", f"a.yaml state should be 'f' (top fanout id), got: {a_st}"
    assert a_st.get("iteration") == 1


# ---------------------------------------------------------------------------
# Step 2: full walk — start → advance a → advance b → join → finalized
# ---------------------------------------------------------------------------

def test_simple_fanout_walks_to_done(engine_env, tmp_path):
    """Drive the simple-fanout protocol end-to-end: both legs pass → join fires
    → _instance.yaml joined=True (aggregate success, no further dispatch)."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "abc123"
    base["AGENT_RUN_ID"] = "r"

    def run(script, *args, **env_extra):
        e = dict(base); e.update(env_extra)
        r = subprocess.run(["python3", str(script), *map(str, args)],
                           text=True, capture_output=True, env=e)
        assert r.returncode == 0, f"{script.name} {args}: {r.stderr}"
        return r

    def reclone(tag):
        return _reclone(engine_env, tmp_path, tag)

    v, ev = _pass_verdicts(tmp_path, "pass")

    # --- 1. start → run-fanout, a.yaml + b.yaml + _instance.yaml seeded ---
    r1 = run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "abc123")
    act = json.loads(r1.stdout)
    assert act["action"] == "run-fanout"
    fdir = reclone("1")
    assert (fdir / "a.yaml").is_file()
    assert (fdir / "b.yaml").is_file()
    inst = _yaml(fdir / "_instance.yaml")
    assert not inst.get("joined")

    # --- 2. advance f.a (pass) → a.yaml done, fire_join (top, no path) ---
    r2 = run(ADVANCE, tmp_path / "s2", "pr-1", PROTO, v, ev,
             NODE_PATH="f.a")
    assert "event_type=protocol-join" in r2.stderr, \
        f"expected top-level protocol-join, got stderr: {r2.stderr}"
    # Top fanout join must carry NO path qualifier.
    assert "client_payload[path]=" not in r2.stderr, \
        f"top join must not carry a path, got: {r2.stderr}"
    fdir2 = reclone("2")
    assert _yaml(fdir2 / "a.yaml")["state"] == "done", "a.yaml must be done"
    # b still in flight.
    assert _yaml(fdir2 / "b.yaml").get("state") != "done", "b.yaml must still be in flight"

    # --- 3. join (top) after only leg a done → not all terminal yet, waits ---
    rj3 = run(JOIN, tmp_path / "s3j", "pr-1", PROTO)
    assert "not all terminal" in rj3.stderr, \
        f"join must wait while b is still in flight: {rj3.stderr}"
    assert not _yaml(reclone("3j") / "_instance.yaml").get("joined"), \
        "_instance.yaml must NOT be joined yet"

    # --- 4. advance f.b (pass) → b.yaml done, fire_join (top, no path) ---
    r4 = run(ADVANCE, tmp_path / "s4", "pr-1", PROTO, v, ev,
             NODE_PATH="f.b")
    assert "event_type=protocol-join" in r4.stderr, \
        f"expected top-level protocol-join after b, got: {r4.stderr}"
    assert "client_payload[path]=" not in r4.stderr, \
        f"top join must not carry a path, got: {r4.stderr}"
    fdir4 = reclone("4")
    assert _yaml(fdir4 / "b.yaml")["state"] == "done", "b.yaml must be done"

    # --- 5. join (top) → both done → finalize, _instance.yaml joined=True ---
    rj5 = run(JOIN, tmp_path / "s5j", "pr-1", PROTO)
    # join-f.next = "done" is a sentinel (not a real state) → no further dispatch.
    assert "event_type=protocol-continue" not in rj5.stderr, \
        f"sentinel 'done' must NOT trigger a further dispatch: {rj5.stderr}"
    fdir5 = reclone("5j")
    inst5 = _yaml(fdir5 / "_instance.yaml")
    assert inst5.get("joined") is True, f"_instance.yaml must be joined=True: {inst5}"

    # Sanity: no failure recorded.
    assert not inst5.get("failed"), f"aggregate must not be marked failed: {inst5}"
