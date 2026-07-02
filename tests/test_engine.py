"""Generic engine library (lib.py) unit tests.

This file once held the v1/v2 legacy single-agent + fanout-mini byte-identity
regression. With the engine unified onto the single NODE_PATH code path (Stage 4a,
Task 16) the legacy planner/advance paths were deleted, so those walk tests went
with them — their behaviour is now covered by the capability suite:

  - single-agent lifecycle (start / continue / reset / iterate / exhaust / done)
        → test_cap_single_agent.py
  - fan-out branch advance + leg seeding + join signalling
        → test_cap_simple_fanout.py, test_deep_fanout_e2e.py
  - multi-phase / sub-pipeline advance
        → test_unified_codereview_e2e.py, test_unified_recover_e2e.py

What remains here is the GENERIC, protocol-agnostic lib surface that has no other
home: the CAS push/checkout behaviour and the lib.py path-CLI (state-file /
instance-file). The protocol-id arguments to the path-CLI are pure strings (no
fixture file is read), so they stay as-is.
"""

import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
LIB_PY = ENGINE / "lib.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_env(state_origin, **extra):
    """Return an env dict with ENGINE_LOCAL=1 and STATE_REMOTE set."""
    import os
    e = dict(os.environ)
    e["ENGINE_LOCAL"] = "1"
    e["STATE_REMOTE"] = str(state_origin)
    e["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    e.update(extra)
    return e


def lib_cmd(state_origin, *args, extra_env=None):
    """Invoke lib.py CLI and return (stdout, stderr, rc)."""
    e = make_env(state_origin, **(extra_env or {}))
    r = subprocess.run(
        ["python3", str(LIB_PY), *map(str, args)],
        text=True, capture_output=True, env=e,
    )
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def state_checkout(state_origin, work_dir):
    """python3 lib.py state-checkout <work_dir>"""
    lib_cmd(state_origin, "state-checkout", work_dir)


def cas_push(state_origin, work_dir, msg):
    """python3 lib.py cas-push <work_dir> <msg>"""
    lib_cmd(state_origin, "cas-push", work_dir, msg)


def clone_state(state_origin, target):
    """Clone the agentic-state branch from the bare origin into target."""
    subprocess.run(
        ["git", "clone", "-q", "--branch", "agentic-state", str(state_origin), str(target)],
        check=True,
    )


# ---------------------------------------------------------------------------
# Shared fixture: single bare origin for lib CAS tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cas_origin(tmp_path_factory):
    """Module-scoped bare git origin for the CAS test group."""
    base = tmp_path_factory.mktemp("cas")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


# ===========================================================================
# Section: lib CAS behavior
# ===========================================================================


def test_lib_cas_branch_created(cas_origin, tmp_path_factory):
    """state_checkout creates the agentic-state branch on a bare origin."""
    s1 = tmp_path_factory.mktemp("s1")
    state_checkout(cas_origin, s1)
    r = subprocess.run(
        ["git", "ls-remote", "--heads", str(cas_origin), "agentic-state"],
        capture_output=True, text=True
    )
    assert "agentic-state" in r.stdout


def test_lib_cas_push_lands(cas_origin, tmp_path_factory):
    """A new state file pushed via cas_push is visible in a fresh clone."""
    s1 = tmp_path_factory.mktemp("s1_push")
    state_checkout(cas_origin, s1)
    (s1 / "grumpy").mkdir(exist_ok=True)
    (s1 / "grumpy" / "pr-1.yaml").write_text("state: review\n")
    cas_push(cas_origin, s1, "init pr-1")

    verify = tmp_path_factory.mktemp("verify1")
    clone_state(cas_origin, verify)
    content = (verify / "grumpy" / "pr-1.yaml").read_text()
    assert "state: review" in content


def test_lib_cas_concurrent_push(cas_origin, tmp_path_factory):
    """A stale clone rebases and pushes successfully when origin has moved."""
    # Get s1 (fresh checkout of what was already pushed in test_lib_cas_push_lands)
    s1 = tmp_path_factory.mktemp("s1_conc")
    state_checkout(cas_origin, s1)

    # s2: stale clone (taken at same time as s1)
    s2 = tmp_path_factory.mktemp("s2_conc")
    state_checkout(cas_origin, s2)

    # s2 prepares a disjoint file
    (s2 / "grumpy").mkdir(exist_ok=True)
    (s2 / "grumpy" / "pr-2.yaml").write_text("state: review\n")

    # s1 moves origin forward first
    (s1 / "grumpy").mkdir(exist_ok=True)
    (s1 / "grumpy" / "pr-1.yaml").write_text("state: publish\n")
    cas_push(cas_origin, s1, "advance pr-1")

    # s2 push with stale clone must succeed via rebase
    cas_push(cas_origin, s2, "init pr-2")

    verify = tmp_path_factory.mktemp("verify2")
    clone_state(cas_origin, verify)
    assert "state: publish" in (verify / "grumpy" / "pr-1.yaml").read_text()
    assert "state: review" in (verify / "grumpy" / "pr-2.yaml").read_text()


# ===========================================================================
# Section: lib path CLI (pure string formatting — no fixture file is read)
# ===========================================================================


def test_lib_state_file_single_agent(tmp_path):
    """state_file /s <pid> pr-5 → /s/<pid>/pr-5.yaml (flat single-agent form)."""
    out, _, rc = lib_cmd(None, "state-file", "/s", "single-agent", "pr-5",
                         extra_env={"STATE_REMOTE": str(tmp_path)})
    assert rc == 0
    assert out == "/s/single-agent/pr-5.yaml"


def test_lib_state_file_branch(tmp_path):
    """state_file /s <pid> pr-5 grumpy → /s/<pid>/pr-5/grumpy.yaml (branch form)."""
    out, _, rc = lib_cmd(None, "state-file", "/s", "fanout-mini", "pr-5", "grumpy",
                         extra_env={"STATE_REMOTE": str(tmp_path)})
    assert rc == 0
    assert out == "/s/fanout-mini/pr-5/grumpy.yaml"


def test_lib_instance_file(tmp_path):
    """instance_file /s <pid> pr-5 → /s/<pid>/pr-5/_instance.yaml."""
    out, _, rc = lib_cmd(None, "instance-file", "/s", "fanout-mini", "pr-5",
                         extra_env={"STATE_REMOTE": str(tmp_path)})
    assert rc == 0
    assert out == "/s/fanout-mini/pr-5/_instance.yaml"


# ===========================================================================
# Section: is_terminal_state
# ===========================================================================

import sys as _sys
_sys.path.insert(0, str(ENGINE))
from lib import is_terminal_state  # noqa: E402


@pytest.mark.parametrize("state,expected", [
    ("done",    True),
    ("failed",  True),
    ("blocked", True),
    ("design",  False),
    ("",        False),
    ("iterate", False),
    (None,      False),
    (42,        False),
])
def test_is_terminal_state(state, expected):
    assert is_terminal_state(state) == expected
