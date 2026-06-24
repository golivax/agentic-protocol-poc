"""test_workflow_contract.py — structural contract tests for the GHA workflow files.
These tests run offline (no GitHub Actions environment needed) and verify that
the workflow YAML files contain/exclude specific strings that encode the
NODE_PATH wiring contract established in Stage 4b.
"""
import pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
WF = ROOT / ".github/workflows"


def _load(name):
    # GitHub 'on' parses to python True; that's fine, we read as text + yaml.
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
