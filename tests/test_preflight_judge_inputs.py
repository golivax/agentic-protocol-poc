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


# ---------------------------------------------------------------------------
# PROTO_R2: nested-cluster shape (Revision 2 topology)
#
# preflight (fanout) with two branches:
#   adherence (sub-pipeline):
#     adherence-fanout (fanout) with legs spec-solves-issue, plan-implements-spec,
#                                         code-implements-plan (each gather->judge)
#     join-adherence (join)
#     adherence-rollup (agent) — reads the inner leg judges
#   security (sub-pipeline):
#     security-gather (agent)
#     security-judge (agent)
# preflight-gate (agent) — reads cluster terminals (adherence-rollup, security-judge)
# ---------------------------------------------------------------------------
PROTO_R2 = {
    "name": "code-review",
    "states": [
        {"id": "preflight", "kind": "fanout", "next": "preflight-gate", "branches": [
            {"id": "adherence", "states": [
                {"id": "adherence-fanout", "kind": "fanout", "branches": [
                    {"id": "spec-solves-issue", "states": [
                        {"id": "spec-solves-issue-gather", "kind": "agent", "workflow": "spec-solves-issue-gather-agent"},
                        {"id": "spec-solves-issue-judge", "kind": "agent", "workflow": "spec-solves-issue-judge-agent",
                         "inputs": [{"from": "spec-solves-issue-gather", "as": "gather"}]},
                    ]},
                    {"id": "plan-implements-spec", "states": [
                        {"id": "plan-implements-spec-gather", "kind": "agent", "workflow": "plan-implements-spec-gather-agent"},
                        {"id": "plan-implements-spec-judge", "kind": "agent", "workflow": "plan-implements-spec-judge-agent",
                         "inputs": [{"from": "plan-implements-spec-gather", "as": "gather"}]},
                    ]},
                    {"id": "code-implements-plan", "states": [
                        {"id": "code-implements-plan-gather", "kind": "agent", "workflow": "code-implements-plan-gather-agent"},
                        {"id": "code-implements-plan-judge", "kind": "agent", "workflow": "code-implements-plan-judge-agent",
                         "inputs": [{"from": "code-implements-plan-gather", "as": "gather"}]},
                    ]},
                ]},
                {"id": "join-adherence", "kind": "join", "of": "adherence-fanout"},
                {"id": "adherence-rollup", "kind": "agent", "workflow": "adherence-rollup-agent",
                 "inputs": [{"from": "spec-solves-issue", "as": "spec-solves-issue"},
                             {"from": "plan-implements-spec", "as": "plan-implements-spec"},
                             {"from": "code-implements-plan", "as": "code-implements-plan"}]},
            ]},
            {"id": "security", "states": [
                {"id": "security-gather", "kind": "agent", "workflow": "security-gather-agent"},
                {"id": "security-judge", "kind": "agent", "workflow": "security-judge-agent",
                 "inputs": [{"from": "security-gather", "as": "gather"}]},
            ]},
        ]},
        {"id": "preflight-gate", "kind": "agent", "workflow": "preflight-gate-agent",
         "inputs": [{"from": "adherence", "as": "adherence"},
                    {"from": "security", "as": "security"}]},
    ],
}


def test_r2_gate_reads_cluster_terminals():
    assert lib.branch_output_substate(PROTO_R2, "adherence") == "adherence-rollup"
    assert lib.branch_output_substate(PROTO_R2, "security") == "security-judge"
    # The root gate consumes each cluster's terminal. The engine resolves a node's
    # inputs PATH-AWARE (next.py:751-754 / advance.py:361-370 pass
    # consuming_path=tree_path), so we call resolve_inputs the way the engine does —
    # with the gate's tree path.
    res = lib.resolve_inputs(PROTO_R2, "/s", "code-review", "pr-1", consuming_branch=None, consuming_phase=None,
                             inputs=[{"from": "adherence", "as": "adherence"}, {"from": "security", "as": "security"}],
                             consuming_path=["preflight-gate"])
    p = {r["as"]: r["path"] for r in res}
    assert p["adherence"] == "/s/code-review/pr-1/preflight.adherence.adherence-rollup.evidence.json"
    assert p["security"] == "/s/code-review/pr-1/preflight.security.security-judge.evidence.json"


def test_r2_rollup_reads_inner_judge():
    # adherence-rollup is an agent sub-state INSIDE the adherence cluster; the engine
    # dispatches it with consuming_path=["preflight","adherence","adherence-rollup"]
    # (advance.py:361-370 / next.py:751-754). Path-aware resolution walks UP from the
    # rollup, finds the sibling adherence-fanout, and reaches the spec-solves-issue
    # leg's TERMINAL JUDGE evidence one level down.
    res = lib.resolve_inputs(PROTO_R2, "/s", "code-review", "pr-1", consuming_branch="adherence", consuming_phase=None,
                             inputs=[{"from": "spec-solves-issue", "as": "spec-solves-issue"}],
                             consuming_path=["preflight", "adherence", "adherence-rollup"])
    assert res[0]["path"] == "/s/code-review/pr-1/preflight.adherence.adherence-fanout.spec-solves-issue.spec-solves-issue-judge.evidence.json"
