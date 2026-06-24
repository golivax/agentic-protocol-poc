import json, pathlib, subprocess, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import paths
PROTO = ROOT / ".github/agent-factory/protocols/deep-review-stub/protocol.json"
ENG = ROOT / ".github/agent-factory/engine"
NEXT = ENG / "next.py"
ADVANCE = ENG / "advance.py"
JOIN = ENG / "join.py"

def test_deep_review_stub_topology():
    p = json.load(open(PROTO))
    assert p["name"] == "deep-review-stub"
    assert paths.max_static_depth(p) == 4
    assert paths.node_kind(p, ["preflight"]) == "fanout"
    assert paths.node_kind(p, ["preflight", "deep"]) == "sequence"
    assert paths.node_kind(p, ["preflight", "deep", "analyze"]) == "fanout"
    assert paths.next_sibling(p, ["preflight", "deep", "analyze"]) == "join-analyze"
    assert paths.next_sibling(p, ["preflight", "deep", "triage"]) == "analyze"

def test_deep_review_stub_validates():
    import lib
    lib.validate_protocol(json.load(open(PROTO)))  # must not raise


# ---------------------------------------------------------------------------
# Task 2: offline NODE_PATH e2e walk — deep-review-stub start→done
# ---------------------------------------------------------------------------

def _pass_finding(tmp_path, tag):
    v = tmp_path / f"v-{tag}.json"
    v.write_text(json.dumps({"results": [
        {"check": "finding-present", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / f"e-{tag}.json"
    ev.write_text(json.dumps({"finding": "x"}))
    return v, ev


def _read_yaml(p):
    import yaml
    with open(p) as fh:
        return yaml.safe_load(fh)


def test_deep_review_stub_walks_to_done(engine_env, tmp_path):
    """Keystone: drive the depth-4 deep-review-stub protocol end-to-end through the
    shared git origin, invoking next.py/advance.py/join.py as subprocesses with
    NODE_PATH per leg + finding-present verdicts. Mirrors test_deep_fanout_walks_to_done
    exactly, substituting protocol dir and check name."""
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
        fresh = tmp_path / f"rc-{tag}"
        subprocess.run(["git", "clone", "-q", "-b", "agentic-state",
                        engine_env["STATE_REMOTE"], str(fresh)], check=True)
        return fresh / "deep-review-stub" / "pr-1"

    v, ev = _pass_finding(tmp_path, "pass")

    # --- 1. start → preflight fanout seeds quick + deep(cursor sub_state=triage) ---
    r1 = run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "abc123")
    act = json.loads(r1.stdout)
    assert act["action"] == "run-fanout"
    fdir = reclone("1")
    assert (fdir / "quick.yaml").is_file()
    deep_cur = _read_yaml(fdir / "deep.yaml")
    assert deep_cur["sub_state"] == "triage", deep_cur

    # --- 2. advance quick (pass) → quick leg done, fire_join (top, no path) ---
    r2 = run(ADVANCE, tmp_path / "s2", "pr-1", PROTO, v, ev,
             NODE_PATH="preflight.quick")
    assert "event_type=protocol-join" in r2.stderr
    assert "client_payload[path]=" not in r2.stderr  # TOP fanout → path-less
    fdir = reclone("2")
    assert _read_yaml(fdir / "quick.yaml")["state"] == "done"
    # join.py (top, NODE_PATH unset) → deep still in flight → waits.
    rj = run(JOIN, tmp_path / "s2j", "pr-1", PROTO)
    assert "not all terminal" in rj.stderr
    assert not _read_yaml(reclone("2j") / "_instance.yaml").get("joined")

    # --- 3. advance deep/triage (pass) → cursor→analyze, continue path dispatched ---
    r3 = run(ADVANCE, tmp_path / "s3", "pr-1", PROTO, v, ev,
             NODE_PATH="preflight.deep.triage")
    assert "event_type=protocol-continue" in r3.stderr
    assert "client_payload[path]=preflight.deep.analyze" in r3.stderr
    fdir = reclone("3")
    assert _read_yaml(fdir / "deep.yaml")["sub_state"] == "analyze"
    assert not (fdir / "deep.analyze.sec.yaml").is_file()  # NOT seeded yet

    # --- 4. continue NODE_PATH=preflight.deep.analyze → seeds sec/perf + marker ---
    r4 = run(NEXT, tmp_path / "s4", "pr-1", PROTO, "continue",
             NODE_PATH="preflight.deep.analyze")
    assert json.loads(r4.stdout)["action"] == "run-fanout"
    fdir = reclone("4")
    assert (fdir / "deep.analyze.sec.yaml").is_file()
    assert (fdir / "deep.analyze.perf.yaml").is_file()
    assert (fdir / "deep.analyze.__join.yaml").is_file()
    assert _read_yaml(fdir / "deep.analyze.__join.yaml")["joined"] is False

    # --- 5. advance sec (pass) → sec done, fire_join path=...analyze; join waits ---
    r5 = run(ADVANCE, tmp_path / "s5", "pr-1", PROTO, v, ev,
             NODE_PATH="preflight.deep.analyze.sec")
    assert "client_payload[path]=preflight.deep.analyze" in r5.stderr
    assert _read_yaml(reclone("5") / "deep.analyze.sec.yaml")["state"] == "done"
    rj5 = run(JOIN, tmp_path / "s5j", "pr-1", PROTO, NODE_PATH="preflight.deep.analyze")
    assert "not all terminal" in rj5.stderr
    assert _read_yaml(reclone("5j") / "deep.analyze.__join.yaml")["joined"] is False

    # --- 6. advance perf (pass) → perf done, fire_join path=...analyze ---
    r6 = run(ADVANCE, tmp_path / "s6", "pr-1", PROTO, v, ev,
             NODE_PATH="preflight.deep.analyze.perf")
    assert "client_payload[path]=preflight.deep.analyze" in r6.stderr
    assert _read_yaml(reclone("6") / "deep.analyze.perf.yaml")["state"] == "done"

    # --- 7. join NODE_PATH=...analyze → both done → marker joined, cursor→report ---
    rj7 = run(JOIN, tmp_path / "s7", "pr-1", PROTO, NODE_PATH="preflight.deep.analyze")
    assert "event_type=protocol-continue" in rj7.stderr
    assert "client_payload[path]=preflight.deep.report" in rj7.stderr
    fdir = reclone("7")
    assert _read_yaml(fdir / "deep.analyze.__join.yaml")["joined"] is True
    assert _read_yaml(fdir / "deep.yaml")["sub_state"] == "report"

    # --- 8. continue NODE_PATH=preflight.deep.report → seeds report, run-agent ---
    r8 = run(NEXT, tmp_path / "s8", "pr-1", PROTO, "continue", "abc123",
             NODE_PATH="preflight.deep.report")
    a8 = json.loads(r8.stdout)
    assert a8["action"] == "run-agent" and a8["path"] == "preflight.deep.report"
    assert (reclone("8") / "deep.report.yaml").is_file()

    # --- 9. advance report (pass) → deep sub-pipeline ends → deep cursor done, fire_join ---
    r9 = run(ADVANCE, tmp_path / "s9", "pr-1", PROTO, v, ev,
             NODE_PATH="preflight.deep.report")
    assert "event_type=protocol-join" in r9.stderr
    assert "client_payload[path]=" not in r9.stderr  # deep is a TOP-fanout leg → path-less
    assert _read_yaml(reclone("9") / "deep.yaml")["state"] == "done"

    # --- 10. join (top, NODE_PATH unset) → quick+deep both done → finalize, done ---
    rj10 = run(JOIN, tmp_path / "s10", "pr-1", PROTO)
    fdir = reclone("10")
    inst = _read_yaml(fdir / "_instance.yaml")
    assert inst["joined"] is True, inst

    # --- FINAL ASSERTS (fresh re-clone) ---
    final = reclone("final")
    assert _read_yaml(final / "_instance.yaml")["joined"] is True
    assert _read_yaml(final / "deep.analyze.__join.yaml")["joined"] is True
    assert _read_yaml(final / "deep.yaml")["state"] == "done"
    # No failure recorded anywhere (the aggregate is a success).
    assert not _read_yaml(final / "deep.analyze.__join.yaml").get("failed")
