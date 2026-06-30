"""Engine: run_conclude_hook exposes the state checkout to the conclude hook as
CONCLUDE_STATE_DIR (Option 2 — generic capability, no protocol-specific logic).

A conclude hook needs to read deeply-nested persisted evidence (e.g. a leg's
gather evidence) whose path the input-resolver cannot reach from a sibling root
child; CONCLUDE_STATE_DIR gives it the checkout root to resolve those paths."""
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))
import advance  # noqa: E402


def _make_proto_with_conclude(tmp_path):
    """A minimal protocol dir whose single agent state declares a conclude hook
    that records the CONCLUDE_STATE_DIR it was given."""
    pdir = tmp_path / "proto"
    (pdir / "publish").mkdir(parents=True)
    proto = {"name": "t", "states": [
        {"id": "s", "kind": "agent", "workflow": "w", "evidence": "e", "conclude": "echo-statedir"}]}
    proto_path = pdir / "protocol.json"
    proto_path.write_text(json.dumps(proto))
    hook = pdir / "publish" / "echo-statedir.py"
    hook.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "open(os.environ['SENTINEL'], 'w').write(os.environ.get('CONCLUDE_STATE_DIR', '<unset>'))\n"
        "print(json.dumps({'conclusion': 'clear', 'summary': '', 'blocked': False}))\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return proto, str(proto_path)


def test_run_conclude_hook_sets_state_dir(tmp_path):
    proto, proto_path = _make_proto_with_conclude(tmp_path)
    sentinel = tmp_path / "seen.txt"
    state_dir = tmp_path / "state-checkout"
    evid = tmp_path / "ev.json"; evid.write_text("{}")

    os.environ["SENTINEL"] = str(sentinel)
    try:
        res = advance.run_conclude_hook(proto_path, proto, "s", str(evid), "pr-1",
                                        blocking=False, dir_=str(state_dir))
    finally:
        os.environ.pop("SENTINEL", None)

    assert res is not None and res.get("blocked") is False
    assert sentinel.read_text() == str(state_dir)


def test_run_conclude_hook_state_dir_empty_when_no_checkout(tmp_path):
    proto, proto_path = _make_proto_with_conclude(tmp_path)
    sentinel = tmp_path / "seen.txt"
    evid = tmp_path / "ev.json"; evid.write_text("{}")

    os.environ["SENTINEL"] = str(sentinel)
    try:
        advance.run_conclude_hook(proto_path, proto, "s", str(evid), "pr-1",
                                  blocking=False, dir_=None)
    finally:
        os.environ.pop("SENTINEL", None)

    assert sentinel.read_text() == ""  # dir_ None => CONCLUDE_STATE_DIR=""
