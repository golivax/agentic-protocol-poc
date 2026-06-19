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
