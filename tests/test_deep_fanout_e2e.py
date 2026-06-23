import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import paths
def test_deep_fixture_depth_is_4():
    p = json.load(open(ROOT / "tests/fixtures/deep-fanout/protocol.json"))
    assert paths.max_static_depth(p) == 4
    assert paths.node_kind(p, ["preflight", "deep", "analyze"]) == "fanout"
    assert paths.next_sibling(p, ["preflight", "deep", "analyze"]) == "join-analyze"
