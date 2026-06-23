import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import paths
def test_deep_fixture_depth_is_4():
    p = json.load(open(ROOT / "tests/fixtures/deep-fanout/protocol.json"))
    assert paths.max_static_depth(p) == 4
    assert paths.node_kind(p, ["preflight", "deep", "analyze"]) == "fanout"
    assert paths.next_sibling(p, ["preflight", "deep", "analyze"]) == "join-analyze"


def test_continue_at_nested_fanout_emits_matrix(engine_env, tmp_path):
    import subprocess, json
    sd = tmp_path / "state"; sd.mkdir()
    proto = ROOT / "tests/fixtures/deep-fanout/protocol.json"
    # continue with NODE_PATH pointing at the nested fanout → emit its children matrix.
    e = dict(engine_env); e["NODE_PATH"] = "preflight.deep.analyze"
    r = subprocess.run(["python3", str(ROOT / ".github/agent-factory/engine/next.py"),
                        str(sd), "pr-1", str(proto), "continue"],
                       text=True, capture_output=True, env=e)
    assert r.returncode == 0, r.stderr
    act = json.loads(r.stdout)
    assert act["action"] == "run-fanout"
    assert {l["path"] for l in act["legs"]} == {
        "preflight.deep.analyze.sec", "preflight.deep.analyze.perf"}
    # leg files + nested join marker seeded locally (single-phase drops leading id).
    marker = sd / "deep-fanout" / "pr-1" / "deep.analyze.__join.yaml"
    assert marker.is_file()
    assert (sd / "deep-fanout" / "pr-1" / "deep.analyze.sec.yaml").is_file()
    assert (sd / "deep-fanout" / "pr-1" / "deep.analyze.perf.yaml").is_file()

    # PERSISTENCE: re-clone the state branch from the bare origin (the same way the
    # real matrix legs re-checkout state) and assert the seeded files were pushed —
    # not merely written to the local DIR. cas_push must have run.
    fresh = tmp_path / "reclone"
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state",
                    e["STATE_REMOTE"], str(fresh)], check=True)
    fdir = fresh / "deep-fanout" / "pr-1"
    assert (fdir / "deep.analyze.__join.yaml").is_file()
    assert (fdir / "deep.analyze.sec.yaml").is_file()
    assert (fdir / "deep.analyze.perf.yaml").is_file()


# ---------------------------------------------------------------------------
# Task 12a: advance.py NODE_PATH path-awareness + nested-fanout re-dispatch.
# ---------------------------------------------------------------------------
import subprocess

PROTO = ROOT / "tests/fixtures/deep-fanout/protocol.json"
NEXT = ROOT / ".github/agent-factory/engine/next.py"
ADVANCE = ROOT / ".github/agent-factory/engine/advance.py"


def _pass_verdicts(tmp_path, tag):
    v = tmp_path / f"verdicts-{tag}.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / f"evidence-{tag}.json"
    ev.write_text("{}")
    return v, ev


def _reclone(tmp_path, engine_env, suffix):
    fresh = tmp_path / f"reclone-{suffix}"
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state",
                    engine_env["STATE_REMOTE"], str(fresh)], check=True)
    return fresh / "deep-fanout" / "pr-1"


def _read_yaml(p):
    import yaml
    with open(p) as fh:
        return yaml.safe_load(fh)


def test_advance_triage_redispatches_nested_fanout(engine_env, tmp_path):
    """Drive `deep`'s triage sub-state to done via advance.py NODE_PATH. The next
    sibling (`analyze`) is a FANOUT → advance must move the deep cursor's sub_state
    to `analyze`, re-dispatch protocol-continue with path=preflight.deep.analyze,
    and NOT seed the analyze fanout's child legs (the continue does that later)."""
    # 1. Seed the top preflight fanout (next.py start).
    r = subprocess.run(["python3", str(NEXT), str(tmp_path / "start"), "pr-1",
                        str(PROTO), "start", "abc123"],
                       text=True, capture_output=True, env=engine_env)
    assert r.returncode == 0, r.stderr

    # 2. Drive deep/triage to done.
    v, ev = _pass_verdicts(tmp_path, "triage")
    e = dict(engine_env)
    e["NODE_PATH"] = "preflight.deep.triage"
    e["PR_HEAD_SHA"] = "abc123"
    e["AGENT_RUN_ID"] = "run-1"
    r = subprocess.run(["python3", str(ADVANCE), str(tmp_path / "adv-triage"), "pr-1",
                        str(PROTO), str(v), str(ev)],
                       text=True, capture_output=True, env=e)
    assert r.returncode == 0, r.stderr

    fdir = _reclone(tmp_path, engine_env, "triage")

    # (a) deep cursor sub_state advanced to "analyze".
    cursor = _read_yaml(fdir / "deep.yaml")
    assert cursor["sub_state"] == "analyze", f"cursor: {cursor}"
    assert cursor.get("state") == "preflight"  # leg stays in flight (life-state)

    # (b) stderr shows protocol-continue with path=preflight.deep.analyze.
    assert "event_type=protocol-continue" in r.stderr, r.stderr
    assert "client_payload[path]=preflight.deep.analyze" in r.stderr, r.stderr

    # (c) NO analyze child leg files seeded by THIS step.
    assert not (fdir / "deep.analyze.sec.yaml").is_file()
    assert not (fdir / "deep.analyze.perf.yaml").is_file()


def test_advance_nested_leg_done_fires_join_with_path(engine_env, tmp_path):
    """With the analyze fanout entered (via next.py continue path=...analyze),
    drive sec to done via advance.py NODE_PATH. The leg is the last sub-state of a
    flat agent child → leg done → fire_join carrying path=preflight.deep.analyze."""
    # Seed top fanout.
    subprocess.run(["python3", str(NEXT), str(tmp_path / "start"), "pr-1",
                    str(PROTO), "start", "abc123"],
                   text=True, capture_output=True, env=engine_env, check=True)
    # Advance triage so the deep cursor is at analyze (mirrors the real walk).
    v, ev = _pass_verdicts(tmp_path, "triage")
    et = dict(engine_env); et["NODE_PATH"] = "preflight.deep.triage"
    et["PR_HEAD_SHA"] = "abc123"; et["AGENT_RUN_ID"] = "r"
    subprocess.run(["python3", str(ADVANCE), str(tmp_path / "adv-triage"), "pr-1",
                    str(PROTO), str(v), str(ev)],
                   text=True, capture_output=True, env=et, check=True)
    # Enter the analyze fanout (seeds sec/perf legs + nested __join.yaml).
    ec = dict(engine_env); ec["NODE_PATH"] = "preflight.deep.analyze"
    subprocess.run(["python3", str(NEXT), str(tmp_path / "enter-analyze"), "pr-1",
                    str(PROTO), "continue"],
                   text=True, capture_output=True, env=ec, check=True)

    # Drive sec to done.
    v2, ev2 = _pass_verdicts(tmp_path, "sec")
    es = dict(engine_env); es["NODE_PATH"] = "preflight.deep.analyze.sec"
    es["PR_HEAD_SHA"] = "abc123"; es["AGENT_RUN_ID"] = "r"
    r = subprocess.run(["python3", str(ADVANCE), str(tmp_path / "adv-sec"), "pr-1",
                        str(PROTO), str(v2), str(ev2)],
                       text=True, capture_output=True, env=es)
    assert r.returncode == 0, r.stderr

    fdir = _reclone(tmp_path, engine_env, "sec")
    # (a) sec leg file is done.
    sec = _read_yaml(fdir / "deep.analyze.sec.yaml")
    assert sec["state"] == "done", f"sec: {sec}"
    # (b) stderr shows protocol-join carrying path=preflight.deep.analyze.
    assert "event_type=protocol-join" in r.stderr, r.stderr
    assert "client_payload[path]=preflight.deep.analyze" in r.stderr, r.stderr
    # (c) NO spurious cursor write at the analyze FANOUT: deep.analyze.yaml must
    # NOT exist — sec is a flat fanout child (its own terminal), not a
    # sub-pipeline leg with a cursor. Writing deep.analyze.yaml {state: done}
    # would mark the whole analyze fanout done while perf is still in flight.
    assert not (fdir / "deep.analyze.yaml").is_file(), \
        "sec completing must NOT write the analyze fanout cursor file"
    # perf is still in flight (its leg file stays non-terminal).
    perf = _read_yaml(fdir / "deep.analyze.perf.yaml")
    assert perf.get("state") != "done", f"perf should not be done yet: {perf}"


def test_continue_at_nested_agent_seeds_and_emits(engine_env, tmp_path):
    """Step A: a `continue` with NODE_PATH at a non-fanout AGENT sub-state (the
    `report` sub-state of the deep leg) must seed that sub-state's file and emit a
    path-qualified run-agent action — mirroring the fanout-continue's
    seed→cas_push→emit order, but for an agent leaf."""
    # Seed the top fanout so the instance dir exists.
    subprocess.run(["python3", str(NEXT), str(tmp_path / "start"), "pr-1",
                    str(PROTO), "start", "abc123"],
                   text=True, capture_output=True, env=engine_env, check=True)
    e = dict(engine_env); e["NODE_PATH"] = "preflight.deep.report"
    e["HEAD_SHA"] = "abc123"
    r = subprocess.run(["python3", str(NEXT), str(tmp_path / "cont-report"), "pr-1",
                        str(PROTO), "continue", "abc123"],
                       text=True, capture_output=True, env=e)
    assert r.returncode == 0, r.stderr
    act = json.loads(r.stdout)
    assert act["action"] == "run-agent", act
    assert act["path"] == "preflight.deep.report", act
    assert act["workflow"] == "report-agent", act
    # Seeded file persisted to origin (single-phase drops leading "preflight").
    fdir = _reclone(tmp_path, engine_env, "report")
    assert (fdir / "deep.report.yaml").is_file()
    seeded = _read_yaml(fdir / "deep.report.yaml")
    assert seeded["state"] == "preflight"  # leg life-state
    assert seeded["iteration"] == 1
