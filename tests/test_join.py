"""Port of tests/test-join.sh — tests for join.py (fan-out AND-barrier).

The bash suite seeds per-branch state files and _instance.yaml into a bare
git origin via lib.py state-checkout + cas-push, then calls join.py with a
fresh checkout dir and inspects stdout/stderr + the post-join state on origin.

Seeding mirrors the bash seed() function:
  python3 lib.py state-checkout <dir>
  # write grumpy.yaml, security.yaml, _instance.yaml as JSON (valid YAML)
  python3 lib.py cas-push <dir> "seed ..."

join.py is then called with a SEPARATE checkout dir so it clones fresh.
After join.py runs, a third checkout dir is used to verify the final state.

Bash assertion → pytest mapping
--------------------------------
Case 1 — both done → aggregate success, joined=true:
  1.  check "all done → check-run success"
      grep -q "check-run fanout-mini sha=joinsha status=completed conclusion=success"
      → test_all_done_check_run_success
  2.  check "all done → comment shows complete headline"
      grep -q "Review complete — published"
      → test_all_done_comment_headline
  3.  check "all done → comment shows both sections"
      grep -q "**grumpy**" && grep -q "**security**"
      → test_all_done_comment_both_sections
  4.  check "all done → joined=true"
      yq -r .joined ... == true
      → test_all_done_joined_true

Case 2 — one failed → aggregate failure:
  5.  check "one failed → check-run failure"
      grep -q "check-run fanout-mini sha=joinsha status=completed conclusion=failure"
      → test_one_failed_check_run_failure
  6.  check "one failed → comment shows incomplete headline"
      grep -q "Review incomplete — a branch could not complete"
      → test_one_failed_comment_headline
  7.  check "one failed → comment shows both sections"
      grep -q "**grumpy**" && grep -q "**security**"
      → test_one_failed_comment_both_sections

Case 3 — not all terminal → no aggregate yet, joined stays false:
  8.  check "partial → no completed aggregate"
      ! grep -q "status=completed"
      → test_partial_no_completed_aggregate
  9.  check "partial → joined stays false"
      yq -r .joined ... == false
      → test_partial_joined_stays_false

Case 4 — idempotent: second join after joined=true is a no-op:
  10. check "idempotent: second join is a no-op"
      grep -qi "already joined"
      → test_idempotent_second_join_is_noop
"""

import json
import os
import pathlib
import subprocess

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
LIB_PY = ENGINE / "lib.py"
JOIN_PY = ENGINE / "join.py"
PROTO = ROOT / "tests/fixtures/fanout-mini/protocol.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_env(state_remote):
    """Return an env dict with ENGINE_LOCAL=1 and STATE_REMOTE set."""
    env = dict(os.environ)
    env["ENGINE_LOCAL"] = "1"
    env["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    env["STATE_REMOTE"] = str(state_remote)
    env["PR_HEAD_SHA"] = "joinsha"
    return env


def _run(cmd, env, check=True):
    return subprocess.run(cmd, env=env, text=True, capture_output=True, check=check)


def seed(state_remote, workdir, pr, grumpy_state, security_state):
    """Mirror the bash seed() function.

    1. state-checkout into workdir
    2. Write grumpy.yaml, security.yaml, _instance.yaml as JSON (valid YAML)
    3. cas-push to commit into the bare origin

    Args:
        state_remote: path to the bare git origin
        workdir: path for the seed checkout (must NOT exist yet)
        pr: instance key, e.g. "pr-1"
        grumpy_state: state string for the grumpy branch
        security_state: state string for the security branch
    """
    env = make_env(state_remote)
    _run(["python3", str(LIB_PY), "state-checkout", str(workdir)], env)

    d = pathlib.Path(workdir) / "fanout-mini" / pr
    d.mkdir(parents=True, exist_ok=True)

    grumpy_data = {
        "protocol": "fanout-mini",
        "instance": pr,
        "state": grumpy_state,
        "iteration": 1,
        "gates": {},
        "history": [],
    }
    security_data = {
        "protocol": "fanout-mini",
        "instance": pr,
        "state": security_state,
        "iteration": 1,
        "gates": {},
        "history": [],
    }
    instance_data = {
        "protocol": "fanout-mini",
        "instance": pr,
        "head_sha": "joinsha",
        "joined": False,
    }

    (d / "grumpy.yaml").write_text(json.dumps(grumpy_data))
    (d / "security.yaml").write_text(json.dumps(security_data))
    (d / "_instance.yaml").write_text(json.dumps(instance_data))

    _run(
        ["python3", str(LIB_PY), "cas-push", str(workdir), f"seed {pr} g={grumpy_state} s={security_state}"],
        env,
    )


def run_join(state_remote, join_workdir, pr, pr_num):
    """Run join.py with a fresh checkout dir. Returns (stdout, stderr)."""
    env = make_env(state_remote)
    env["PR"] = str(pr_num)
    result = subprocess.run(
        ["python3", str(JOIN_PY), str(join_workdir), pr, str(PROTO)],
        env=env,
        text=True,
        capture_output=True,
    )
    # Combine stdout+stderr to mirror the bash `2>&1` redirect
    combined = result.stdout + result.stderr
    return combined, result.returncode


def checkout_verify(state_remote, verify_dir):
    """Check out the state branch for post-join verification."""
    env = make_env(state_remote)
    _run(["python3", str(LIB_PY), "state-checkout", str(verify_dir)], env)


def load_instance_yaml(verify_dir, pr):
    p = pathlib.Path(verify_dir) / "fanout-mini" / pr / "_instance.yaml"
    with open(p) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Case 1: both done → aggregate success, joined=true
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def case_all_done(tmp_path_factory):
    """Seed pr-1 with grumpy=done, security=done; run join.py; verify."""
    base = tmp_path_factory.mktemp("j1")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    seed(origin, base / "seed", "pr-1", "done", "done")
    out, rc = run_join(origin, base / "join", "pr-1", pr_num=1)
    checkout_verify(origin, base / "verify")
    return out, rc, base / "verify"


def test_all_done_check_run_success(case_all_done):
    """Bash assertion 1: all done → check-run success."""
    out, _, _ = case_all_done
    assert "check-run fanout-mini sha=joinsha status=completed conclusion=success" in out


def test_all_done_comment_headline(case_all_done):
    """Bash assertion 2: all done → comment shows complete headline."""
    out, _, _ = case_all_done
    assert "Review complete — published" in out


def test_all_done_comment_both_sections(case_all_done):
    """Bash assertion 3: all done → comment shows both **grumpy** and **security** sections."""
    out, _, _ = case_all_done
    assert "**grumpy**" in out
    assert "**security**" in out


def test_all_done_joined_true(case_all_done):
    """Bash assertion 4: all done → joined=true in the instance file."""
    _, _, verify_dir = case_all_done
    inst = load_instance_yaml(verify_dir, "pr-1")
    assert inst["joined"] is True


# ---------------------------------------------------------------------------
# Case 2: one failed → aggregate failure
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def case_one_failed(tmp_path_factory):
    """Seed pr-2 with grumpy=done, security=failed; run join.py."""
    base = tmp_path_factory.mktemp("j2")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    seed(origin, base / "seed", "pr-2", "done", "failed")
    out, rc = run_join(origin, base / "join", "pr-2", pr_num=2)
    return out, rc


def test_one_failed_check_run_failure(case_one_failed):
    """Bash assertion 5: one failed → check-run failure."""
    out, _ = case_one_failed
    assert "check-run fanout-mini sha=joinsha status=completed conclusion=failure" in out


def test_one_failed_comment_headline(case_one_failed):
    """Bash assertion 6: one failed → comment shows incomplete headline."""
    out, _ = case_one_failed
    assert "Review incomplete — a branch could not complete" in out


def test_one_failed_comment_both_sections(case_one_failed):
    """Bash assertion 7: one failed → comment shows both sections."""
    out, _ = case_one_failed
    assert "**grumpy**" in out
    assert "**security**" in out


# ---------------------------------------------------------------------------
# Case 3: partial (not all terminal) → no aggregate yet, joined stays false
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def case_partial(tmp_path_factory):
    """Seed pr-3 with grumpy=done, security=review (not terminal); run join.py; verify."""
    base = tmp_path_factory.mktemp("j3")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    seed(origin, base / "seed", "pr-3", "done", "review")
    out, rc = run_join(origin, base / "join", "pr-3", pr_num=3)
    checkout_verify(origin, base / "verify")
    return out, rc, base / "verify"


def test_partial_no_completed_aggregate(case_partial):
    """Bash assertion 8: partial → no 'status=completed' in output."""
    out, _, _ = case_partial
    assert "status=completed" not in out


def test_partial_joined_stays_false(case_partial):
    """Bash assertion 9: partial → joined stays false."""
    _, _, verify_dir = case_partial
    inst = load_instance_yaml(verify_dir, "pr-3")
    assert inst["joined"] is False


# ---------------------------------------------------------------------------
# Case 4: idempotent — second join after joined=true is a no-op
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def case_idempotent(tmp_path_factory):
    """Seed pr-4 with both done; run join.py twice; assert second is no-op."""
    base = tmp_path_factory.mktemp("j4")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    seed(origin, base / "seed", "pr-4", "done", "done")
    # First join: sets joined=true
    run_join(origin, base / "first", "pr-4", pr_num=4)
    # Second join: should detect joined=true and say "already joined"
    out2, rc2 = run_join(origin, base / "second", "pr-4", pr_num=4)
    return out2, rc2


def test_idempotent_second_join_is_noop(case_idempotent):
    """Bash assertion 10: idempotent — second join is a no-op (output contains "already joined")."""
    out, _ = case_idempotent
    assert "already joined" in out.lower()
