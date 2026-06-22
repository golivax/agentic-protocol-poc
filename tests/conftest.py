import json
import os
import pathlib
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
PROTOCOLS = ROOT / ".github/agent-factory/protocols"
FIXTURES = ROOT / "tests/fixtures"


@pytest.fixture
def state_origin(tmp_path):
    """Bare git repo used as the fake agentic-state remote (ENGINE_LOCAL mode)."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "agentic-state", str(origin)],
        check=True,
    )
    return origin


@pytest.fixture
def engine_env(state_origin):
    """os.environ copy with ENGINE_LOCAL=1 and STATE_REMOTE pointing at the bare origin."""
    env = dict(os.environ)
    env["ENGINE_LOCAL"] = "1"
    env["STATE_REMOTE"] = str(state_origin)
    return env


def run_engine(script, *args, env=None, branch=None, phase=None, substate=None):
    """Run an engine Python script and return (stdout, stderr, returncode).

    ``script`` is a filename relative to ENGINE (e.g. "next.py").
    ``env`` defaults to os.environ; pass ``engine_env`` from the fixture to get
    ENGINE_LOCAL + STATE_REMOTE wired up.
    ``branch`` sets the BRANCH env var for fan-out branch-scoped calls.
    ``phase`` sets the PHASE env var.
    ``substate`` sets the SUBSTATE env var.
    """
    e = dict(env or os.environ)
    if branch is not None:
        e["BRANCH"] = branch
    if phase is not None:
        e["PHASE"] = phase
    if substate is not None:
        e["SUBSTATE"] = substate
    r = subprocess.run(
        ["python3", str(ENGINE / script), *map(str, args)],
        text=True,
        capture_output=True,
        env=e,
    )
    return r.stdout, r.stderr, r.returncode


def read_state_yaml(path):
    """Load and return a state YAML file as a dict (yaml.safe_load)."""
    with open(path) as fh:
        return yaml.safe_load(fh)


def run_check(check_path, evidence, diff, changed_files, check_params=None):
    """Run a check executable and return the parsed JSON dict.

    ``check_path`` is an absolute path to the check script.
    ``evidence``, ``diff``, ``changed_files`` are path-like objects.
    ``check_params`` is a dict or string; if a dict it is JSON-serialised.
    If None the CHECK_PARAMS env var is not set (inherits from os.environ).
    Returns the parsed ``{check, pass, feedback}`` dict.
    """
    env = dict(os.environ)
    if check_params is not None:
        if isinstance(check_params, dict):
            env["CHECK_PARAMS"] = json.dumps(check_params)
        else:
            env["CHECK_PARAMS"] = check_params
    r = subprocess.run(
        ["python3", str(check_path), str(evidence), str(diff), str(changed_files)],
        text=True,
        capture_output=True,
        env=env,
    )
    return json.loads(r.stdout)
