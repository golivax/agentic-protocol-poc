import os
import sys
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def test_label_text_explicit_state_label():
    proto = {"states": [{"id": "preflight", "kind": "agent", "label": "pre-flight gate"}]}
    assert lib.phase_label_text(proto, "preflight") == "pre-flight gate"


def test_label_text_humanizes_id_when_no_label():
    proto = {"states": [{"id": "code-review", "kind": "agent"}]}
    assert lib.phase_label_text(proto, "code-review") == "Code review"


def test_label_text_terminal_default():
    proto = {"states": []}
    assert lib.phase_label_text(proto, "done") == "✅ done"
    assert lib.phase_label_text(proto, "failed") == "❌ failed"
    assert lib.phase_label_text(proto, "blocked") == "⛔ blocked"
    assert lib.phase_label_text(proto, "setup") == "⚙ setup"


def test_label_text_terminal_override():
    proto = {"states": [], "phase_labels": {"done": "shipped 🚀"}}
    assert lib.phase_label_text(proto, "done") == "shipped 🚀"
    # unknown override key still falls back to default
    assert lib.phase_label_text(proto, "failed") == "❌ failed"


def _engine_local_env(monkeypatch):
    monkeypatch.setenv("ENGINE_LOCAL", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")


def _write_instance(tmp_path, pid, instance, data):
    inf = tmp_path / pid / instance / "_instance.yaml"
    inf.parent.mkdir(parents=True, exist_ok=True)
    with open(inf, "w") as fh:
        yaml.safe_dump(data, fh)
    return inf


def test_ensure_phase_label_records_applied(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "preflight", "kind": "agent", "label": "pre-flight gate"}]}
    inf = _write_instance(tmp_path, "p", "pr-1", {"protocol": "p", "instance": "pr-1"})
    lib.ensure_phase_label(str(tmp_path), "p", "pr-1", proto, "1", "preflight")
    assert yaml.safe_load(inf.read_text())["phase_label"] == "pre-flight gate"


def test_ensure_phase_label_idempotent_noop(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "review", "kind": "fanout"}]}
    inf = _write_instance(tmp_path, "p", "pr-2",
                          {"protocol": "p", "instance": "pr-2", "phase_label": "Review"})
    lib.ensure_phase_label(str(tmp_path), "p", "pr-2", proto, "1", "review")
    # unchanged; still "Review"
    assert yaml.safe_load(inf.read_text())["phase_label"] == "Review"


def test_ensure_phase_label_noop_without_instance_file(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "review", "kind": "agent"}]}
    # no _instance.yaml written → must not raise, must not create one
    lib.ensure_phase_label(str(tmp_path), "p", "pr-3", proto, "1", "review")
    assert not (tmp_path / "p" / "pr-3" / "_instance.yaml").exists()


def test_ensure_phase_label_terminal_key(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "approval", "kind": "gate"}]}
    inf = _write_instance(tmp_path, "p", "pr-4",
                          {"protocol": "p", "instance": "pr-4", "phase_label": "Approval"})
    lib.ensure_phase_label(str(tmp_path), "p", "pr-4", proto, "1", "done")
    assert yaml.safe_load(inf.read_text())["phase_label"] == "✅ done"


from conftest import run_engine, read_state_yaml  # noqa: E402

CRP_PROTO = ROOT / ".github/agent-factory/protocols/code-review-pipeline/protocol.json"


def test_start_seeds_first_phase_label(engine_env, tmp_path):
    """A fresh `start` on code-review-pipeline records the first phase's label."""
    state_dir = tmp_path / "state"
    out, err, rc = run_engine(
        "next.py", state_dir, "pr-700", CRP_PROTO, "start", "deadbeef",
        env=engine_env,
    )
    assert rc == 0, err
    inf = state_dir / "code-review-pipeline" / "pr-700" / "_instance.yaml"
    data = read_state_yaml(inf)
    # preflight gets the explicit label added in Task 6; until then it is the
    # humanized id. Assert against the resolved value to stay decoupled:
    assert data["phase"] == "preflight"
    assert data["phase_label"]  # non-empty: a label was recorded
