"""Characterization tests for the enter_root unified entry path.
These tests PASS against both the old and new code — they are the safety net for the refactor."""
import json
import subprocess
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"


def _yaml(p):
    return yaml.safe_load(open(p))


def _rc(engine_env, tmp_path, pid, tag):
    d = tmp_path / f"rc-{tag}"
    subprocess.run(
        ["git", "clone", "-q", "-b", "agentic-state",
         engine_env["STATE_REMOTE"], str(d)],
        check=True,
    )
    return d / pid / "pr-1"


def _start(engine_env, tmp_path, proto, sha="s1"):
    r = subprocess.run(
        ["python3", str(ENG / "next.py"), str(tmp_path / "s"), "pr-1",
         str(proto), "start", sha],
        text=True,
        capture_output=True,
        env=engine_env,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_start_codereview_seeds_first_phase(engine_env, tmp_path):
    proto = ROOT / ".github/agent-factory/protocols/code-review/protocol.json"
    act = _start(engine_env, tmp_path, proto)
    assert act["action"] == "run-agent"           # preflight is an agent phase
    assert _yaml(_rc(engine_env, tmp_path, "code-review", "cr") / "_instance.yaml")["phase"] == "preflight"


def test_start_deepfanout_seeds_fanout(engine_env, tmp_path):
    proto = ROOT / "tests/fixtures/deep-fanout/protocol.json"
    act = _start(engine_env, tmp_path, proto)
    assert act["action"] == "run-fanout"
    assert {l["path"] for l in act["legs"]} == {"preflight.quick", "preflight.deep"}
