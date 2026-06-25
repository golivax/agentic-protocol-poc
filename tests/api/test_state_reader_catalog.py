from pathlib import Path
from api import state_reader

FX = Path(__file__).parent / "fixtures" / "protocols"

def test_list_protocols_summarizes_name_version_triggers():
    cr = (FX / "code-review.protocol.json").read_text()
    deep = (FX / "deep-review-stub.protocol.json").read_text()
    out = state_reader.list_protocols([cr, deep])
    names = {p["name"] for p in out}
    assert names == {"code-review", "deep-review-stub"}
    assert out == sorted(out, key=lambda p: p["name"])
    cr_entry = next(p for p in out if p["name"] == "code-review")
    assert cr_entry["version"] == "0.1.0"
    assert any(t["comment_prefix"] == "/review" for t in cr_entry["triggers"])

def test_protocol_detail_exposes_state_graph():
    cr = (FX / "code-review.protocol.json").read_text()
    out = state_reader.protocol_detail(cr)
    assert out["name"] == "code-review"
    preflight = next(s for s in out["states"] if s["id"] == "preflight")
    assert preflight["kind"] == "agent"
    assert preflight["max_iterations"] == 2
    assert any(c["run"] == "spec-present" for c in preflight["checks"])
    review = next(s for s in out["states"] if s["id"] == "review")
    assert {b["id"] for b in review["branches"]} == {"grumpy", "security"}
    # leaf branches retain their per-leg fields (not flattened to just id)
    grumpy = next(b for b in review["branches"] if b["id"] == "grumpy")
    assert grumpy["workflow"] == "grumpy-agent"
    assert grumpy["max_iterations"] == 3
