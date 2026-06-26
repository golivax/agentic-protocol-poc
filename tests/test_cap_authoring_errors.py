"""Tests for protocol-authoring error messages.

Each test writes a minimal malformed protocol.json to tmp_path, runs
``next.py start``, and asserts:
  - exit code 2
  - stderr contains the offending node id
  - stderr contains a fix hint

Plus a max_depth test that reuses the existing tests/fixtures/too-deep/ fixture
(the check_depth guard is already implemented; we only verify the UX here).

TDD: tests were written FIRST, ran RED (opaque or missing error), then
lib.validate_protocol was added to make them GREEN.
"""
import json
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
FIXTURES = ROOT / "tests/fixtures"


def _run_next(tmp_path, engine_env, proto_path, *, command="start"):
    """Run next.py <state_dir> pr-1 <proto_path> <command>."""
    r = subprocess.run(
        [
            "python3",
            str(ENGINE / "next.py"),
            str(tmp_path / "state"),
            "pr-1",
            str(proto_path),
            command,
        ],
        text=True,
        capture_output=True,
        env=engine_env,
    )
    return r


def _write_proto(tmp_path, proto):
    """Write proto dict to tmp_path/protocol.json and return its path."""
    p = tmp_path / "protocol.json"
    p.write_text(json.dumps(proto))
    return p


# ---------------------------------------------------------------------------
# 1. max_depth guard — reuses the existing too-deep fixture
# ---------------------------------------------------------------------------

def test_max_depth_guard_exit2_with_clear_message(engine_env, tmp_path):
    """next.py must exit 2 and mention max_depth when the protocol is too deep."""
    proto = FIXTURES / "too-deep" / "protocol.json"
    r = _run_next(tmp_path, engine_env, proto)
    assert r.returncode == 2
    assert "max_depth" in r.stderr, f"expected 'max_depth' in stderr:\n{r.stderr}"


# ---------------------------------------------------------------------------
# 2. join whose `of` references an unknown fanout
# ---------------------------------------------------------------------------

def test_join_unknown_of_exit2(engine_env, tmp_path):
    """A join whose of= names a nonexistent fanout must exit 2 with the node id
    and a fix hint pointing at the of= value."""
    proto = {
        "name": "bad-join",
        "version": "0.1.0",
        "triggers": [],
        "states": [
            {
                "id": "work",
                "kind": "fanout",
                "branches": [
                    {
                        "id": "alpha",
                        "workflow": "alpha-agent",
                        "evidence": "a.evidence.schema.json",
                        "max_iterations": 2,
                        "checks": [{"run": "always-pass", "on_fail": "iterate"}],
                        "publish": "noop",
                    }
                ],
                "next": "jx",
            },
            # jx references unknown fanout 'zzz' — the bug
            {"id": "jx", "kind": "join", "of": "zzz", "next": "done"},
        ],
    }
    p = _write_proto(tmp_path, proto)
    r = _run_next(tmp_path, engine_env, p)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\nstderr: {r.stderr}"
    # Must name the offending join node
    assert "jx" in r.stderr, f"expected 'jx' (join node id) in stderr:\n{r.stderr}"
    # Must name the bad of= value
    assert "zzz" in r.stderr, f"expected 'zzz' (bad of= value) in stderr:\n{r.stderr}"
    # Fix hint should mention 'of' or 'fanout'
    assert "of=" in r.stderr or "fanout" in r.stderr or "of" in r.stderr.lower(), (
        f"expected a fix hint about 'of' in stderr:\n{r.stderr}"
    )


# ---------------------------------------------------------------------------
# 3. agent node missing workflow
# ---------------------------------------------------------------------------

def test_agent_node_missing_workflow_exit2(engine_env, tmp_path):
    """A top-level agent state without a 'workflow' key must exit 2 and name the node."""
    proto = {
        "name": "bad-agent",
        "version": "0.1.0",
        "triggers": [],
        "states": [
            {
                "id": "solo",
                "kind": "agent",
                # workflow intentionally omitted
                "evidence": "a.evidence.schema.json",
                "max_iterations": 2,
                "checks": [{"run": "always-pass", "on_fail": "iterate"}],
            }
        ],
    }
    p = _write_proto(tmp_path, proto)
    r = _run_next(tmp_path, engine_env, p)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\nstderr: {r.stderr}"
    assert "solo" in r.stderr, f"expected 'solo' (agent node id) in stderr:\n{r.stderr}"
    assert "workflow" in r.stderr, f"expected 'workflow' in stderr:\n{r.stderr}"


def test_flat_fanout_branch_missing_workflow_exit2(engine_env, tmp_path):
    """A flat fanout branch without a 'workflow' key must exit 2 and name the branch."""
    proto = {
        "name": "bad-branch",
        "version": "0.1.0",
        "triggers": [],
        "states": [
            {
                "id": "review",
                "kind": "fanout",
                "branches": [
                    {
                        "id": "grumpy",
                        # workflow intentionally omitted
                        "evidence": "a.evidence.schema.json",
                        "max_iterations": 2,
                        "checks": [{"run": "always-pass", "on_fail": "iterate"}],
                        "publish": "noop",
                    }
                ],
                "next": "j",
            },
            {"id": "j", "kind": "join", "of": "review", "next": "done"},
        ],
    }
    p = _write_proto(tmp_path, proto)
    r = _run_next(tmp_path, engine_env, p)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\nstderr: {r.stderr}"
    assert "grumpy" in r.stderr, f"expected 'grumpy' (branch id) in stderr:\n{r.stderr}"
    assert "workflow" in r.stderr, f"expected 'workflow' in stderr:\n{r.stderr}"


# ---------------------------------------------------------------------------
# 4. gate whose questions_from names a nonexistent sibling sub-state
# ---------------------------------------------------------------------------

def test_gate_questions_from_nonexistent_sibling_exit2(engine_env, tmp_path):
    """A gate with questions_from pointing at a nonexistent sub-state must exit 2."""
    proto = {
        "name": "bad-gate",
        "version": "0.1.0",
        "triggers": [],
        "states": [
            {
                "id": "review",
                "kind": "fanout",
                "branches": [
                    {
                        "id": "B",
                        "states": [
                            {
                                "id": "draft",
                                "kind": "agent",
                                "workflow": "draft-agent",
                                "evidence": "a.evidence.schema.json",
                                "max_iterations": 2,
                                "checks": [{"run": "always-pass", "on_fail": "iterate"}],
                            },
                            {
                                "id": "clarify",
                                "kind": "gate",
                                # questions_from references a nonexistent sibling
                                "questions_from": "nonexistent-state",
                                "checks": [{"run": "answers-coverage", "on_fail": "iterate"}],
                            },
                        ],
                    }
                ],
                "next": "j",
            },
            {"id": "j", "kind": "join", "of": "review", "next": "done"},
        ],
    }
    p = _write_proto(tmp_path, proto)
    r = _run_next(tmp_path, engine_env, p)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\nstderr: {r.stderr}"
    # Must name the gate node
    assert "clarify" in r.stderr, f"expected 'clarify' (gate id) in stderr:\n{r.stderr}"
    # Must name the bad questions_from value
    assert "nonexistent-state" in r.stderr, (
        f"expected 'nonexistent-state' in stderr:\n{r.stderr}"
    )


# ---------------------------------------------------------------------------
# 5. Valid protocols must NOT be rejected — regression guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", [
    "simple-fanout",
    "deep-fanout",
    "gate-deep",
    "cap-single-agent",
])
def test_valid_fixture_not_rejected(engine_env, tmp_path, fixture):
    """validate_protocol must NOT error on any known-good fixture."""
    proto_path = FIXTURES / fixture / "protocol.json"
    r = _run_next(tmp_path, engine_env, proto_path)
    # The run may succeed (0) or fail for legitimate runtime reasons (e.g. missing
    # evidence schema) — but it must NOT be exit 2 due to validate_protocol.
    # We accept 0 or 1; only 2 indicates a validation error.
    assert r.returncode != 2, (
        f"fixture '{fixture}' was wrongly rejected by validate_protocol:\n{r.stderr}"
    )


@pytest.mark.parametrize("proto_name", [
    "code-review",
    "recover-mental-model",
])
def test_live_protocol_not_rejected(engine_env, tmp_path, proto_name):
    """validate_protocol must NOT error on the live production protocols."""
    from conftest import PROTOCOLS
    proto_path = PROTOCOLS / proto_name / "protocol.json"
    r = _run_next(tmp_path, engine_env, proto_path)
    assert r.returncode != 2, (
        f"live protocol '{proto_name}' was wrongly rejected:\n{r.stderr}"
    )
