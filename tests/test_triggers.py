import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

MULTI = {
    "name": "fanout-demo",
    "triggers": [
        {"on": "issue_comment", "comment_prefix": "/grumpy", "command": "start"},
        {"on": "pull_request", "actions": ["opened", "reopened"], "command": "start"},
        {"on": "pull_request", "actions": ["synchronize"], "command": "reset"},
    ],
    "states": [
        {"id": "review", "kind": "fanout", "next": "join", "branches": [
            {"id": "grumpy", "workflow": "grumpy-agent"},
            {"id": "security", "workflow": "security-agent"},
        ]},
        {"id": "join", "kind": "join", "of": "review", "next": "done"},
    ],
}

SINGLE = {
    "name": "single-demo",
    "triggers": [{"on": "pull_request", "actions": ["opened"], "command": "start"}],
    "states": [
        {"id": "review", "kind": "agent", "workflow": "grumpy-agent", "next": "publish"},
        {"id": "publish", "kind": "deterministic", "next": None},
    ],
}

PIPELINE = {
    "name": "pipe",
    "states": [
        {"id": "gate", "kind": "agent", "workflow": "preflight-agent", "next": "review"},
        {"id": "review", "kind": "fanout", "next": "join", "branches": [
            {"id": "grumpy", "workflow": "grumpy-agent"},
        ]},
        {"id": "join", "kind": "join", "of": "review", "next": "done"},
    ],
}


# match_trigger ----------------------------------------------------------------

def test_issue_comment_prefix_match():
    assert lib.match_trigger(MULTI, "issue_comment", "", "/grumpy please") == "start"


def test_issue_comment_prefix_no_match():
    assert lib.match_trigger(MULTI, "issue_comment", "", "lgtm") == ""


def test_pull_request_opened_starts():
    assert lib.match_trigger(MULTI, "pull_request", "opened", "") == "start"


def test_pull_request_synchronize_resets():
    assert lib.match_trigger(MULTI, "pull_request", "synchronize", "") == "reset"


def test_pull_request_unlisted_action_no_match():
    assert lib.match_trigger(MULTI, "pull_request", "labeled", "") == ""


def test_no_triggers_block_returns_empty():
    assert lib.match_trigger({"name": "x"}, "pull_request", "opened", "") == ""


# agent_workflow ---------------------------------------------------------------

def test_workflow_single_agent_first_state():
    assert lib.agent_workflow(SINGLE) == "grumpy-agent"


def test_workflow_fanout_branch():
    assert lib.agent_workflow(MULTI, branch="security") == "security-agent"


def test_workflow_fanout_unknown_branch_empty():
    assert lib.agent_workflow(MULTI, branch="nope") == ""


def test_workflow_agent_phase():
    assert lib.agent_workflow(PIPELINE, phase="gate") == "preflight-agent"


def test_workflow_fanout_phase_branch():
    assert lib.agent_workflow(PIPELINE, phase="review", branch="grumpy") == "grumpy-agent"


# CLI --------------------------------------------------------------------------

def _write(tmp_path, proto):
    p = tmp_path / "protocol.json"
    p.write_text(json.dumps(proto))
    return p


def _cli(*args):
    r = subprocess.run(["python3", str(ENGINE / "lib.py"), *map(str, args)],
                       text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_cli_match_trigger(tmp_path):
    p = _write(tmp_path, MULTI)
    assert _cli("match-trigger", p, "pull_request", "synchronize", "") == "reset"


def test_cli_agent_workflow(tmp_path):
    p = _write(tmp_path, PIPELINE)
    assert _cli("agent-workflow", p, "review", "grumpy") == "grumpy-agent"


# ── command_prefix: per-protocol answer-prefix lookup ────────────────────────

def test_command_prefix_returns_declared_prefix():
    proto = {"triggers": [
        {"on": "issue_comment", "comment_prefix": "/recover", "command": "start"},
        {"on": "issue_comment", "comment_prefix": "/clarify", "command": "answer"},
    ]}
    assert lib.command_prefix(proto, "answer", "/answer") == "/clarify"


def test_command_prefix_default_when_no_trigger():
    # subpipeline-mini declares no answer trigger → the default is used.
    proto = {"triggers": [
        {"on": "pull_request", "actions": ["opened"], "command": "start"},
    ]}
    assert lib.command_prefix(proto, "answer", "/answer") == "/answer"


def test_command_prefix_default_when_trigger_has_no_prefix():
    proto = {"triggers": [{"on": "issue_comment", "command": "answer"}]}
    assert lib.command_prefix(proto, "answer", "/answer") == "/answer"


def test_command_prefix_first_match_wins():
    proto = {"triggers": [
        {"on": "issue_comment", "comment_prefix": "/a", "command": "answer"},
        {"on": "issue_comment", "comment_prefix": "/b", "command": "answer"},
    ]}
    assert lib.command_prefix(proto, "answer", "/answer") == "/a"
