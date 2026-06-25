from api import state_reader
from tests.api.fixtures_helper import load_instance_files

def test_status_projection_code_review_pr62():
    out = state_reader.status_projection(load_instance_files("code-review", 62))
    assert out["protocol"] == "code-review"
    assert out["pr"] == 62
    assert out["head"]["phase"] == "approval"
    phases = {p["id"]: p for p in out["phases"]}
    assert phases["preflight"]["kind"] == "agent"
    assert phases["preflight"]["status"] == "done"
    assert phases["preflight"]["checks"]["spec-present"] == "pass"
    assert phases["review"]["kind"] == "fanout"
    legs = {b["id"]: b for b in phases["review"]["branches"]}
    assert legs["grumpy"]["status"] == "done"
    assert legs["security"]["status"] == "done"
    assert phases["approval"]["kind"] == "gate"
    assert phases["approval"]["gate"]["open"] is True

def test_status_projection_ignores_sidecars_and_join_markers():
    out = state_reader.status_projection(load_instance_files("deep-review-stub", 88))
    ids = {p["id"] for p in out["phases"]}
    assert "deep.analyze.__join" not in ids
    assert all(not i.endswith(".json") for i in ids)
