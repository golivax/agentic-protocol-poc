import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
FIXTURE = ROOT / "tests/fixtures/pipeline-mini/protocol.json"


def test_continue_redispatch_carries_phase(tmp_path, state_origin):
    # Arrange: a CAS origin + a checked-out work dir seeded with an active gate phase.
    # advance.py re-clones from STATE_REMOTE, so seed via a push to the bare origin.
    work = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(state_origin), str(work)], check=True)
    inst = "pr-1"
    d = work / "pipeline-mini" / inst
    d.mkdir(parents=True, exist_ok=True)
    (d / "gate.yaml").write_text(
        "protocol: pipeline-mini\ninstance: pr-1\nstate: gate\niteration: 1\ngates: {}\nhistory: []\n"
    )
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "seed"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "agentic-state"], check=True)

    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": False, "feedback": "forced fail", "on_fail": "iterate"}
    ]}))
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"type": "object"}))

    env = dict(os.environ)
    env["ENGINE_LOCAL"] = "1"
    env["STATE_REMOTE"] = str(state_origin)
    env["PHASE"] = "gate"
    env["GITHUB_REPOSITORY"] = "owner/repo"

    r = subprocess.run(
        ["python3", str(ENGINE / "advance.py"), str(tmp_path / "adv"), inst,
         str(FIXTURE), str(verdicts), str(evidence)],
        text=True, capture_output=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    # ENGINE_LOCAL echoes `gh api ...` to stderr. The continue dispatch must carry phase.
    assert "event_type=protocol-continue" in r.stderr
    assert "client_payload[phase]=gate" in r.stderr


def test_continue_redispatch_single_phase_no_phase_key(tmp_path, state_origin):
    # Regression: a single-phase protocol's continue re-dispatch must NOT append phase key.
    # This guards the `if phase:` conditional in advance.py from silent removal.
    # Use grumpy-review (single-agent, max_iterations=3) protocol.
    work = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(state_origin), str(work)], check=True)
    inst = "pr-1"
    grumpy_proto = ROOT / "tests/fixtures/single-agent/protocol.json"
    d = work / "single-agent" / inst
    d.mkdir(parents=True, exist_ok=True)
    # Seed a single-agent state file: review state at iteration 1
    (d / "review.yaml").write_text(
        "protocol: single-agent\ninstance: pr-1\nstate: review\niteration: 1\ngates: {}\nhistory: []\n"
    )
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "seed"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "agentic-state"], check=True)

    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({"results": [
        {"check": "schema-valid", "pass": False, "feedback": "forced fail", "on_fail": "iterate"}
    ]}))
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"type": "object"}))

    env = dict(os.environ)
    env["ENGINE_LOCAL"] = "1"
    env["STATE_REMOTE"] = str(state_origin)
    env["GITHUB_REPOSITORY"] = "owner/repo"
    # NO PHASE env: single-phase path

    r = subprocess.run(
        ["python3", str(ENGINE / "advance.py"), str(tmp_path / "adv"), inst,
         str(grumpy_proto), str(verdicts), str(evidence)],
        text=True, capture_output=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    # ENGINE_LOCAL echoes `gh api ...` to stderr. The continue dispatch must NOT carry phase.
    assert "event_type=protocol-continue" in r.stderr
    assert "client_payload[phase]" not in r.stderr
