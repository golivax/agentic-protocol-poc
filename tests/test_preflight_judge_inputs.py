import importlib, sys
from conftest import ENGINE
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")

# Minimal mirror of the proposed preflight shape: a fanout whose branches are
# gather->judge sub-pipelines, with a root gate that reads each branch.
PROTO = {
    "name": "code-review",
    "states": [
        {"id": "preflight", "kind": "fanout", "next": "join-preflight", "branches": [
            {"id": "plan-implements-spec", "states": [
                {"id": "plan-implements-spec-gather", "kind": "agent", "workflow": "plan-implements-spec-agent"},
                {"id": "plan-implements-spec-judge", "kind": "agent", "workflow": "plan-implements-spec-judge-agent",
                 "inputs": [{"from": "plan-implements-spec-gather", "as": "gather"}]},
            ]},
            {"id": "mm-compliance", "states": [
                {"id": "mm-compliance-gather", "kind": "agent", "workflow": "mm-compliance-gate"},
                {"id": "mm-compliance-judge", "kind": "agent", "workflow": "mm-compliance-judge-agent",
                 "inputs": [{"from": "mm-compliance-gather", "as": "gather"}]},
            ]},
        ]},
        {"id": "join-preflight", "kind": "join", "of": "preflight", "next": "preflight-gate"},
        {"id": "preflight-gate", "kind": "agent", "workflow": "preflight-gate-agent",
         "inputs": [{"from": "plan-implements-spec", "as": "plan-implements-spec"},
                    {"from": "mm-compliance", "as": "mm-compliance"}]},
    ],
}


def test_branch_output_is_the_judge_substate():
    assert lib.branch_output_substate(PROTO, "plan-implements-spec") == "plan-implements-spec-judge"
    assert lib.branch_output_substate(PROTO, "mm-compliance") == "mm-compliance-judge"


def test_gate_reads_terminal_judge_per_leg():
    res = lib.resolve_inputs(PROTO, "/s", "code-review", "pr-1",
                             consuming_branch=None, consuming_phase=None,
                             inputs=[{"from": "plan-implements-spec", "as": "plan-implements-spec"},
                                     {"from": "mm-compliance", "as": "mm-compliance"}])
    paths = {r["as"]: r["path"] for r in res}
    assert paths["plan-implements-spec"] == "/s/code-review/pr-1/plan-implements-spec.plan-implements-spec-judge.evidence.json"
    assert paths["mm-compliance"] == "/s/code-review/pr-1/mm-compliance.mm-compliance-judge.evidence.json"


def test_judge_reads_its_gather_sibling():
    res = lib.resolve_inputs(PROTO, "/s", "code-review", "pr-1",
                             consuming_branch="plan-implements-spec", consuming_phase=None,
                             inputs=[{"from": "plan-implements-spec-gather", "as": "gather"}])
    assert res == [{"as": "gather",
                    "path": "/s/code-review/pr-1/plan-implements-spec.plan-implements-spec-gather.evidence.json",
                    "kind": "evidence"}]


import json
from conftest import PROTOCOLS
REAL = json.loads((PROTOCOLS / "code-review/protocol.json").read_text())

def test_real_protocol_gate_resolves_terminal_judges():
    legs = ["spec-solves-issue", "plan-implements-spec", "code-implements-plan",
            "mm-compliance", "docs-updated-appropriately", "tests-updated-appropriately"]
    res = lib.resolve_inputs(REAL, "/s", "code-review", "pr-1",
                             consuming_branch=None, consuming_phase=None,
                             inputs=[{"from": l, "as": l} for l in legs])
    paths = {r["as"]: r["path"] for r in res}
    for l in legs:
        assert paths[l] == f"/s/code-review/pr-1/{l}.{l}-judge.evidence.json"
