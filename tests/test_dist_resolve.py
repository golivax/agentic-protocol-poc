import json, subprocess, sys
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"
sys.path.insert(0, str(DIST))
import resolve  # noqa: E402


def test_flat_protocol_collects_top_and_branch_workflows():
    proto = {
        "states": [
            {"id": "preflight", "kind": "agent", "workflow": "preflight-agent", "next": "review"},
            {"id": "review", "kind": "fanout", "branches": [
                {"id": "grumpy", "workflow": "grumpy-agent"},
                {"id": "security", "workflow": "security-agent"},
            ]},
        ]
    }
    assert resolve.derive_agents(proto) == ["preflight-agent", "grumpy-agent", "security-agent"]


def test_recursive_nested_subpipeline_and_fanout():
    proto = {
        "states": [
            {"id": "preflight", "kind": "fanout", "branches": [
                {"id": "quick", "workflow": "quick-agent"},
                {"id": "deep", "states": [
                    {"id": "triage", "kind": "agent", "workflow": "triage-agent"},
                    {"id": "analyze", "kind": "fanout", "branches": [
                        {"id": "sec", "workflow": "sec-agent"},
                        {"id": "perf", "workflow": "perf-agent"},
                    ]},
                    {"id": "report", "kind": "agent", "workflow": "report-agent"},
                ]},
            ]},
        ]
    }
    assert resolve.derive_agents(proto) == [
        "quick-agent", "triage-agent", "sec-agent", "perf-agent", "report-agent",
    ]


def test_dedup_and_ignore_non_string():
    proto = {"states": [
        {"workflow": "a-agent"},
        {"workflow": "a-agent"},
        {"workflow": None},
        {"branches": [{"workflow": "b-agent"}]},
    ]}
    assert resolve.derive_agents(proto) == ["a-agent", "b-agent"]


def test_cli_agents(tmp_path):
    p = tmp_path / "protocol.json"
    p.write_text(json.dumps({"states": [{"workflow": "x-agent"}]}))
    out = subprocess.run(
        [sys.executable, str(DIST / "resolve.py"), "agents", str(p)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out.split() == ["x-agent"]
