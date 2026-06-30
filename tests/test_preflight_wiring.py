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
    legs = [b["id"] for b in paths.node_at_path(proto, ["preflight"])["branches"]]
    assert legs == ["spec-solves-issue", "plan-implements-spec", "code-implements-plan", "mm-compliance", "docs-updated-appropriately", "tests-updated-appropriately"]


def test_preflight_gate_inputs_resolve_to_each_leg_evidence():
    proto = json.load(open(CODE_REVIEW))
    d, pid, inst = "/s", "code-review", "pr-1"
    resolved = lib.resolve_inputs(
        proto, d, pid, inst, consuming_branch=None, consuming_phase=None,
        inputs=lib.state_inputs(proto, "preflight-gate"), consuming_path=["preflight-gate"])
    by_as = {r["as"]: r for r in resolved}
    for leg in ("spec-solves-issue", "plan-implements-spec", "code-implements-plan", "mm-compliance", "docs-updated-appropriately", "tests-updated-appropriately"):
        # Each branch is now a gather->judge sub-pipeline; the resolver returns the terminal judge.
        judge_ev = lib.output_artifact_path(
            d, pid, inst, path=lib.state_path(proto, ["preflight", leg, f"{leg}-judge"]), kind="evidence")
        assert by_as[leg]["path"] == judge_ev
        assert by_as[leg]["path"].endswith(f"/preflight.{leg}.{leg}-judge.evidence.json")
        assert by_as[leg]["kind"] == "evidence"


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
