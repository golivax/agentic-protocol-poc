import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import paths
PROTO = ROOT / ".github/agent-factory/protocols/deep-review-stub/protocol.json"

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
