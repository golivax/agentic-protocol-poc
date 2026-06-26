"""test_unified_advance.py — Task 4: NODE_PATH agent-phase clear → path-continue.

When advance.py is called with NODE_PATH=preflight (a depth-1 agent phase) and
all checks pass, it must:
  - write _instance.yaml.phase = "review"
  - dispatch protocol-continue with client_payload[path]=review
  - NOT dispatch protocol-advance
"""
import json
import subprocess
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / ".github/agent-factory/protocols/code-review-v1/protocol.json"


def _yaml(p):
    return yaml.safe_load(open(p))


def _rc(engine_env, tmp_path, tag):
    d = tmp_path / f"rc-{tag}"
    subprocess.run(
        ["git", "clone", "-q", "-b", "agentic-state",
         engine_env["STATE_REMOTE"], str(d)],
        check=True,
    )
    return d / "code-review-v1" / "pr-1"


def test_preflight_clear_advances_via_path_continue(engine_env, tmp_path):
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "s1"
    base["AGENT_RUN_ID"] = "r"

    # Seed initial state via next.py (start command)
    subprocess.run(
        ["python3", str(ENG / "next.py"), str(tmp_path / "s"), "pr-1", str(PROTO),
         "start", "s1"],
        text=True, capture_output=True, env=base, check=True,
    )

    # Verdicts: one passing check
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "x", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))

    # Evidence: empty
    ev = tmp_path / "e.json"
    ev.write_text("{}")

    # Invoke advance.py with NODE_PATH=preflight
    e = dict(base)
    e["NODE_PATH"] = "preflight"

    r = subprocess.run(
        ["python3", str(ENG / "advance.py"), str(tmp_path / "a"), "pr-1", str(PROTO),
         str(v), str(ev)],
        text=True, capture_output=True, env=e,
    )
    assert r.returncode == 0, r.stderr

    # Must dispatch protocol-continue with path=review
    assert "event_type=protocol-continue" in r.stderr, r.stderr
    assert "client_payload[path]=review" in r.stderr, r.stderr

    # Must NOT dispatch protocol-advance
    assert "protocol-advance" not in r.stderr, r.stderr

    # _instance.yaml must have phase=review
    inst_yaml = _rc(engine_env, tmp_path, "pf") / "_instance.yaml"
    assert _yaml(inst_yaml)["phase"] == "review", _yaml(inst_yaml)
