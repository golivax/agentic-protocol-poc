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
