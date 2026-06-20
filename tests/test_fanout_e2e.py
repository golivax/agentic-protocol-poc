"""Port of tests/test-fanout-e2e.sh — end-to-end fan-out lifecycle (ENGINE_LOCAL).

The bash suite:
  1. Calls next.py with 'start' on the fanout-mini fanout protocol.
  2. Calls advance.py for grumpy branch (BRANCH=grumpy) with all-pass verdicts.
  3. Calls advance.py for security branch (BRANCH=security) with all-pass verdicts.
  4. Checks out state and asserts both branches are 'done'.
  5. Calls join.py and asserts aggregate success.

Each engine call uses a SEPARATE checkout workdir but the SAME STATE_REMOTE (bare
git origin), which is exactly what the bash test does with $WORK/{p,ag,as,v,j}.

Bash assertion → pytest mapping
--------------------------------
  1.  check "e2e: run-fanout"
      [ "$(jq -r .action <<<"$A")" = run-fanout ]
      → test_e2e_run_fanout_action

  2.  check "e2e: grumpy done"
      yq -r .state $WORK/v/fanout-mini/pr-80/grumpy.yaml == done
      → test_e2e_grumpy_done

  3.  check "e2e: security done"
      yq -r .state $WORK/v/fanout-mini/pr-80/security.yaml == done
      → test_e2e_security_done

  4.  check "e2e: join → success"
      grep -q "check-run fanout-mini sha=e2esha status=completed conclusion=success"
      → test_e2e_join_success
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
NEXT_PY = ENGINE / "next.py"
ADVANCE_PY = ENGINE / "advance.py"
JOIN_PY = ENGINE / "join.py"
PROTO = ROOT / "tests/fixtures/fanout-mini/protocol.json"
FIXTURES = ROOT / "tests/fixtures"


# ---------------------------------------------------------------------------
# Module-scoped fixture: run the full fan-out lifecycle once, share results
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def e2e_results(tmp_path_factory):
    """Execute the full fan-out lifecycle and return intermediate results.

    Returns a dict with:
      - next_action: action string from next.py
      - verify_dir:  path to a fresh state checkout for branch-state assertions
      - join_out:    combined stdout+stderr from join.py
    """
    base = tmp_path_factory.mktemp("e2e")

    # Bare git origin — the single shared STATE_REMOTE for all engine calls.
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    # All-pass verdicts file (mirrors $PASSV in the bash suite).
    passv = base / "pass.json"
    passv.write_text(json.dumps({"results": [{"check": "x", "pass": True, "feedback": ""}]}))

    def env(**extra):
        e = dict(os.environ)
        e["ENGINE_LOCAL"] = "1"
        e["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
        e["STATE_REMOTE"] = str(origin)
        e["PR"] = "80"
        e["PR_HEAD_SHA"] = "e2esha"
        e.update(extra)
        return e

    # ------------------------------------------------------------------
    # Step 1: next.py start  → seeds both branch state files + _instance.yaml
    # ------------------------------------------------------------------
    r_next = subprocess.run(
        ["python3", str(NEXT_PY), str(base / "p"), "pr-80", str(PROTO), "start", "e2esha"],
        env=env(),
        text=True,
        capture_output=True,
        check=True,
    )
    next_action = json.loads(r_next.stdout)["action"]

    # ------------------------------------------------------------------
    # Step 2: advance.py for grumpy branch
    # ------------------------------------------------------------------
    subprocess.run(
        [
            "python3", str(ADVANCE_PY),
            str(base / "ag"), "pr-80", str(PROTO),
            str(passv), str(FIXTURES / "evidence-complete.json"),
        ],
        env=env(BRANCH="grumpy"),
        text=True,
        capture_output=True,
        check=True,
    )

    # ------------------------------------------------------------------
    # Step 3: advance.py for security branch
    # ------------------------------------------------------------------
    subprocess.run(
        [
            "python3", str(ADVANCE_PY),
            str(base / "as"), "pr-80", str(PROTO),
            str(passv), str(FIXTURES / "evidence-security.json"),
        ],
        env=env(BRANCH="security"),
        text=True,
        capture_output=True,
        check=True,
    )

    # ------------------------------------------------------------------
    # Step 4: checkout for branch-state verification
    # ------------------------------------------------------------------
    verify_dir = base / "v"
    subprocess.run(
        ["python3", str(LIB_PY), "state-checkout", str(verify_dir)],
        env=env(),
        check=True,
    )

    # ------------------------------------------------------------------
    # Step 5: join.py  → aggregate barrier
    # ------------------------------------------------------------------
    r_join = subprocess.run(
        ["python3", str(JOIN_PY), str(base / "j"), "pr-80", str(PROTO)],
        env=env(),
        text=True,
        capture_output=True,
    )
    join_out = r_join.stdout + r_join.stderr

    return {
        "next_action": next_action,
        "verify_dir": verify_dir,
        "join_out": join_out,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_e2e_run_fanout_action(e2e_results):
    """Bash assertion 1: next.py start on fanout protocol → action=run-fanout."""
    assert e2e_results["next_action"] == "run-fanout"


def test_e2e_grumpy_done(e2e_results):
    """Bash assertion 2: grumpy branch state == done after advance."""
    sf = e2e_results["verify_dir"] / "fanout-mini" / "pr-80" / "grumpy.yaml"
    with open(sf) as f:
        state_data = yaml.safe_load(f)
    assert state_data["state"] == "done"


def test_e2e_security_done(e2e_results):
    """Bash assertion 3: security branch state == done after advance."""
    sf = e2e_results["verify_dir"] / "fanout-mini" / "pr-80" / "security.yaml"
    with open(sf) as f:
        state_data = yaml.safe_load(f)
    assert state_data["state"] == "done"


def test_e2e_join_success(e2e_results):
    """Bash assertion 4: join.py emits aggregate success check-run."""
    assert (
        "check-run fanout-mini sha=e2esha status=completed conclusion=success"
        in e2e_results["join_out"]
    )
