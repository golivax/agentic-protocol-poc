import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import lib


def _run_capturing(snippet, env):
    """Run a one-liner against lib in a subprocess so we can read the
    ENGINE_LOCAL stderr dispatch log."""
    code = ("import sys; sys.path.insert(0, %r); import lib; %s"
            % (str(ROOT / ".github/agent-factory/engine"), snippet))
    return subprocess.run([sys.executable, "-c", code], text=True,
                          capture_output=True, env=env)


def _env():
    e = dict(os.environ)
    e["ENGINE_LOCAL"] = "1"
    e["GITHUB_REPOSITORY"] = "o/r"
    return e


def test_dispatch_continue_path_only():
    r = _run_capturing(
        "lib.dispatch_continue('p', 'pr-1', path='outer.B.inner.C.wrap')", _env())
    assert "event_type=protocol-continue" in r.stderr
    assert "client_payload[path]=outer.B.inner.C.wrap" in r.stderr
    assert "client_payload[branch]" not in r.stderr
    assert "client_payload[substate]" not in r.stderr


def test_dispatch_continue_legacy_unchanged():
    r = _run_capturing(
        "lib.dispatch_continue('p', 'pr-1', 'B', 'finalize')", _env())
    assert "client_payload[branch]=B" in r.stderr
    assert "client_payload[substate]=finalize" in r.stderr
    assert "client_payload[path]" not in r.stderr


def test_fire_join_dispatch_with_path():
    r = _run_capturing(
        "lib.fire_join_dispatch('p', 'pr-1', fanout_path='outer.B.inner')", _env())
    assert "event_type=protocol-join" in r.stderr
    assert "client_payload[path]=outer.B.inner" in r.stderr


def test_fire_join_dispatch_legacy_pathless():
    r = _run_capturing("lib.fire_join_dispatch('p', 'pr-1')", _env())
    assert "event_type=protocol-join" in r.stderr
    assert "client_payload[path]" not in r.stderr
