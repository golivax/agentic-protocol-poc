"""test_workflow_contract.py — structural contract tests for the GHA workflow files.
These tests run offline (no GitHub Actions environment needed) and verify that
the workflow YAML files contain/exclude specific strings that encode the
NODE_PATH wiring contract established in Stage 4b.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
WF = ROOT / ".github/workflows"


def _load(name):
    return WF.joinpath(name).read_text()


def test_engine_yml_threads_node_path_not_legacy():
    t = _load("agentic-engine.yml")
    # NODE_PATH is threaded from the matrix leg.
    assert "NODE_PATH: ${{ matrix.leg.path }}" in t
    assert "matrix.leg.workflow" in t
    assert "github.event.client_payload.path" in t
    # legacy coordinate wiring is gone from the engine jobs.
    assert "client_payload.branch" not in t
    assert "client_payload.substate" not in t
    assert "client_payload.phase" not in t
    assert "advance-phase" not in t
    assert "agent-workflow" not in t   # dispatch reads matrix.leg.workflow now
    # matrix is fed from the action's legs.
    assert "fromJSON(needs.plan.outputs.legs)" in t


def test_engine_yml_matrix_leg_has_path_and_workflow():
    t = _load("agentic-engine.yml")
    assert "matrix.leg.path" in t


def test_join_yml_threads_node_path_and_path_concurrency():
    t = _load("protocol-join.yml")
    assert "NODE_PATH: ${{ github.event.client_payload.path }}" in t
    # concurrency group is path-aware so nested joins don't serialize against the top join
    assert "join-${{ github.event.client_payload.instance }}-${{ github.event.client_payload.path }}" in t


def test_orchestrator_yml_path_concurrency_and_no_protocol_advance():
    t = _load("agentic-orchestrator.yml")
    assert "agentic-${{ github.event.client_payload.instance" in t
    assert "github.event.client_payload.path }}" in t   # concurrency keyed on path
    assert "protocol-advance" not in t                  # dropped from on: types
    # protocol-continue is still accepted; protocol-join still owned by protocol-join.yml
    assert "protocol-continue" in t


def test_no_workflow_references_retired_mechanisms():
    for name in ("agentic-engine.yml", "protocol-join.yml", "agentic-orchestrator.yml"):
        t = _load(name)
        assert "protocol-advance" not in t, name
        assert "client_payload.branch" not in t, name
        assert "client_payload.substate" not in t, name


def test_lint_workflow_runs_actionlint():
    t = _load("lint.yml")
    assert "actionlint" in t
