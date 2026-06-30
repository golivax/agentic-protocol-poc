"""Structural wiring for the Phase-A preflight decomposition. Resolve over the
REAL code-review protocol with the engine resolver and pin the literal evidence
paths so neither side can drift (mirrors test_mm_pipeline_wiring.py)."""
import importlib
import json
import sys

from conftest import ENGINE, PROTOCOLS

sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")
paths = importlib.import_module("paths")

CODE_REVIEW = PROTOCOLS / "code-review/protocol.json"


def test_preflight_is_a_fanout():
    proto = json.load(open(CODE_REVIEW))
    assert paths.node_kind(proto, ["preflight"]) == "fanout"
    # Revision 2: preflight now has 4 cluster branches.
    branches = [b["id"] for b in paths.node_at_path(proto, ["preflight"])["branches"]]
    assert branches == ["adherence", "mm-compliance", "consistency", "security"]


def test_preflight_gate_inputs_resolve_to_cluster_terminals():
    """Gate inputs resolve to each cluster's terminal agent evidence (Revision 2 topology)."""
    proto = json.load(open(CODE_REVIEW))
    d, pid, inst = "/s", "code-review", "pr-1"
    resolved = lib.resolve_inputs(
        proto, d, pid, inst, consuming_branch=None, consuming_phase=None,
        inputs=lib.state_inputs(proto, "preflight-gate"), consuming_path=["preflight-gate"])
    by_as = {r["as"]: r for r in resolved}
    # adherence → adherence-rollup (terminal of the adherence sub-pipeline)
    assert by_as["adherence"]["path"].endswith("/preflight.adherence.adherence-rollup.evidence.json")
    assert by_as["adherence"]["kind"] == "evidence"
    # mm-compliance → mm-compliance-judge (terminal of the mm-compliance sub-pipeline)
    assert by_as["mm-compliance"]["path"].endswith("/preflight.mm-compliance.mm-compliance-judge.evidence.json")
    assert by_as["mm-compliance"]["kind"] == "evidence"
    # consistency → consistency-rollup (terminal of the consistency sub-pipeline)
    assert by_as["consistency"]["path"].endswith("/preflight.consistency.consistency-rollup.evidence.json")
    assert by_as["consistency"]["kind"] == "evidence"
    # security → security-judge (terminal of the security sub-pipeline)
    assert by_as["security"]["path"].endswith("/preflight.security.security-judge.evidence.json")
    assert by_as["security"]["kind"] == "evidence"


def test_mrp_preflight_input_resolves_to_the_gate():
    proto = json.load(open(CODE_REVIEW))
    d, pid, inst = "/s", "code-review", "pr-1"
    resolved = lib.resolve_inputs(
        proto, d, pid, inst, consuming_branch=None, consuming_phase=None,
        inputs=lib.state_inputs(proto, "mrp"), consuming_path=["mrp"])
    by_as = {r["as"]: r for r in resolved}
    gate_ev = lib.output_artifact_path(
        d, pid, inst, path=lib.state_path(proto, ["preflight-gate"]), kind="evidence")
    assert by_as["preflight"]["path"] == gate_ev
    assert by_as["preflight"]["path"].endswith("/preflight-gate.evidence.json")
    assert not by_as["preflight"]["path"].endswith("/preflight.evidence.json")
