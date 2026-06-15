"""Port of tests/test-engine.sh — the v1+v2 engine regression guard.

This is the complete faithful port of all 53 bash assertions. Every bash
``check`` maps to one or more pytest assertions in the test functions below.

Bash assertion → pytest mapping (full list)
-------------------------------------------
Section: lib CAS
  1.  check "state branch created on origin"          → test_lib_cas_branch_created
  2.  check "cas push lands"                           → test_lib_cas_push_lands
  3.  check "cas push survives concurrent writer"      → test_lib_cas_concurrent_push

Section: next.py single-agent planning
  4.  check "start/absent: run-agent"                 → test_next_start_absent_action
  5.  check "start/absent: iteration 1"               → test_next_start_absent_iter
  6.  check "start/absent: state pushed"              → test_next_start_absent_state_pushed
  7.  check "continue/absent: fresh iter 1"           → test_next_continue_absent
  8.  check "continue/active: resumes iter 2"         → test_next_continue_active_iter
  9.  check "continue/active: carries feedback"       → test_next_continue_active_feedback
  10. check "continue/terminal: halts"                → test_next_continue_terminal
  11. check "start/terminal: re-reviews fresh"        → test_next_start_terminal
  12. check "start/terminal: state reset to review"   → test_next_start_terminal_state_reset
  13. check "start/active: halts"                     → test_next_start_active
  14. check "reset: run-agent iter 1"                 → test_next_reset_run_agent
  15. check "reset: new head recorded + state review" → test_next_reset_head_and_state
  16. check "reset/active: run-agent iter 1"          → test_next_reset_active

Section: advance.py single-agent
  17. check "advance: iteration bumped"               → test_advance_fail_iter_bumped
  18. check "advance: feedback in history"             → test_advance_fail_feedback
  19. check "advance: re-dispatch intended"            → test_advance_fail_redispatch
  20. check "single-agent comment keeps per-file blob link" → test_advance_pass_blob_link
  21. check "advance: state done"                      → test_advance_pass_state_done
  22. check "advance: publish intended"                → test_advance_pass_publish
  23. check "advance: verdict REQUEST_CHANGES"         → test_advance_pass_request_changes
  24. check "advance: exhausted → failed"              → test_advance_exhaust
  25. check "advance: empty verdicts → not done"       → test_advance_empty_not_done
  26. check "advance: empty verdicts → no publish"     → test_advance_empty_no_publish

Section: advance.py check-run emission
  27. check "check-run: iterate → in_progress"        → test_checkrun_iterate_in_progress
  28. check "check-run: changes requested → failure"  → test_checkrun_pass_failure
  29. check "check-run: exhausted → failure"          → test_checkrun_exhaust_failure

Section: advance.py stub/relay
  30. check "advance: relays hook conclusion"         → test_advance_relay (conclusion assert)
  31. check "advance: relays hook summary"             → test_advance_relay (summary assert)

Section: lib branch-aware paths
  32. check "state_file single-agent form unchanged"  → test_lib_state_file_single_agent
  33. check "state_file branch form nests under instance dir" → test_lib_state_file_branch
  34. check "instance_file points at _instance.yaml"  → test_lib_instance_file

Section: next.py branch-scoped continue
  35. check "branch continue: resumes iter 2"         → test_next_branch_continue_iter
  36. check "branch continue: carries branch feedback" → test_next_branch_continue_feedback

Section: next.py fanout planning
  37. check "fanout start: action run-fanout"          → test_fanout_start_action
  38. check "fanout start: two branches listed"        → test_fanout_start_two_branches
  39. check "fanout start: lists grumpy workflow"      → test_fanout_start_grumpy_workflow
  40. check "fanout start: grumpy file seeded active"  → test_fanout_start_grumpy_seeded
  41. check "fanout start: security file seeded active" → test_fanout_start_security_seeded
  42. check "fanout start: instance file w/ head"      → test_fanout_start_instance_file

Section: next.py branch lifecycle
  43. check "branch continue/terminal: halts"          → test_branch_lifecycle_terminal
  44. check "branch continue/absent: fresh run-agent iter 1" → test_branch_lifecycle_absent

Section: advance.py branch-scoped (multi-grumpy grumpy branch)
  45. check "advance branch: grumpy.yaml state done"            → test_advance_branch_state_done
  46. check "advance branch: published via hook"                → test_advance_branch_published
  47. check "advance branch: per-branch check-run name"         → test_advance_branch_checkrun_name
  48. check "advance branch: shared comment has grumpy section" → test_advance_branch_comment_section
  49. check "advance branch: shared comment tree/ link"         → test_advance_branch_comment_tree_link
  50. check "advance branch: shared comment not blob link"      → test_advance_branch_comment_no_blob

Section: advance.py fan-out signalling
  51. check "fanout iterate: protocol-continue fired"           → test_fanout_signal_continue
  52. check "fanout iterate: payload carries branch"            → test_fanout_signal_branch_payload
  53. check "fanout done: protocol-join fired"                  → test_fanout_signal_join
"""

import json
import os
import pathlib
import subprocess
import stat

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
GRUMPY_PROTO = ROOT / ".github/agent-factory/protocols/grumpy/protocol.json"
MULTI_PROTO = ROOT / ".github/agent-factory/protocols/multi-grumpy/protocol.json"
FIXTURES = ROOT / "tests/fixtures"

EVIDENCE_LAZY = FIXTURES / "evidence-lazy.json"
EVIDENCE_COMPLETE = FIXTURES / "evidence-complete.json"

# ---------------------------------------------------------------------------
# Verdicts constants (mirrors the bash FAILV / PASSV / etc.)
# ---------------------------------------------------------------------------

VERDICTS_FAIL = json.dumps({
    "results": [
        {"check": "rubric-coverage", "pass": False, "feedback": "Missing: duplication × src/report.js"},
        {"check": "schema-valid", "pass": True, "feedback": ""},
    ]
})

VERDICTS_PASS = json.dumps({
    "results": [
        {"check": "schema-valid", "pass": True, "feedback": ""},
        {"check": "rubric-coverage", "pass": True, "feedback": ""},
        {"check": "traces-exist-in-diff", "pass": True, "feedback": ""},
    ]
})

VERDICTS_EMPTY = json.dumps({"results": []})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_env(state_origin, **extra):
    """Return an env dict with ENGINE_LOCAL=1 and STATE_REMOTE set."""
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


def run_next(state_origin, work_dir, instance, proto, command, head_sha=None, branch=None):
    """Run next.py and return parsed action JSON dict."""
    e = make_env(state_origin)
    if branch:
        e["BRANCH"] = branch
    args = [str(NEXT_PY), str(work_dir), instance, str(proto), command]
    if head_sha:
        args.append(head_sha)
    r = subprocess.run(args, text=True, capture_output=True, env=e)
    assert r.returncode == 0, f"next.py failed: {r.stderr}"
    return json.loads(r.stdout)


def run_advance(state_origin, work_dir, instance, proto, verdicts_path, evidence_path,
                branch=None, pr=None, agent_run_id=None, pr_head_sha=None):
    """Run advance.py and return (combined_output, returncode).

    combined_output = stdout + stderr (mirrors the bash 2>&1).
    """
    e = make_env(state_origin)
    if branch:
        e["BRANCH"] = branch
    if pr is not None:
        e["PR"] = str(pr)
    if agent_run_id is not None:
        e["AGENT_RUN_ID"] = str(agent_run_id)
    if pr_head_sha is not None:
        e["PR_HEAD_SHA"] = pr_head_sha

    r = subprocess.run(
        [
            "python3", str(ADVANCE_PY),
            str(work_dir), instance, str(proto),
            str(verdicts_path), str(evidence_path),
        ],
        text=True, capture_output=True, env=e,
    )
    combined = r.stdout + r.stderr
    return combined, r.returncode


def write_verdicts(tmp_path, name, content):
    """Write a verdicts JSON file and return its path."""
    p = tmp_path / name
    p.write_text(content)
    return p


def yq_set(path, expr):
    """Apply a yq expression to a YAML file in-place."""
    r = subprocess.run(["yq", "-i", expr, str(path)], check=True, text=True)


def yq_read(path, expr):
    """Read a yq expression from a YAML file; return stripped string."""
    r = subprocess.run(["yq", "-r", expr, str(path)], check=True, text=True, capture_output=True)
    return r.stdout.strip()


def read_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


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
    """Bash: check "state branch created on origin"
    state_checkout creates the agentic-state branch on a bare origin."""
    s1 = tmp_path_factory.mktemp("s1")
    state_checkout(cas_origin, s1)
    r = subprocess.run(
        ["git", "ls-remote", "--heads", str(cas_origin), "agentic-state"],
        capture_output=True, text=True
    )
    assert "agentic-state" in r.stdout


def test_lib_cas_push_lands(cas_origin, tmp_path_factory):
    """Bash: check "cas push lands"
    A new state file pushed via cas_push is visible in a fresh clone."""
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
    """Bash: check "cas push survives concurrent writer"
    A stale clone rebases and pushes successfully when origin has moved."""
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
# Section: next.py single-agent planning
# ===========================================================================


@pytest.fixture(scope="module")
def next_origin(tmp_path_factory):
    """Module-scoped bare git origin shared across the next.py single-agent tests."""
    base = tmp_path_factory.mktemp("next_base")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


@pytest.fixture(scope="module")
def next_pr7_state(next_origin, tmp_path_factory):
    """Run next.py start for pr-7 and return (action_json, verify_dir).
    This state is shared across start/absent, continue/active, continue/terminal,
    and start/terminal tests."""
    work = tmp_path_factory.mktemp("n1")
    action = run_next(next_origin, work, "pr-7", GRUMPY_PROTO, "start")
    verify = tmp_path_factory.mktemp("vn1")
    clone_state(next_origin, verify)
    return action, verify


def test_next_start_absent_action(next_pr7_state):
    """Bash: check "start/absent: run-agent" """
    action, _ = next_pr7_state
    assert action["action"] == "run-agent"


def test_next_start_absent_iter(next_pr7_state):
    """Bash: check "start/absent: iteration 1" """
    action, _ = next_pr7_state
    assert action["iteration"] == 1


def test_next_start_absent_state_pushed(next_pr7_state):
    """Bash: check "start/absent: state pushed"
    The state file grumpy-review/pr-7.yaml should exist with state=review."""
    _, verify = next_pr7_state
    state_path = verify / "grumpy-review" / "pr-7.yaml"
    assert state_path.exists()
    data = read_yaml(state_path)
    assert data["state"] == "review"


def test_next_continue_absent(next_origin, tmp_path_factory):
    """Bash: check "continue/absent: fresh iter 1"
    continue on ABSENT state → defensive fresh iter 1 (engine loop before any start)."""
    work = tmp_path_factory.mktemp("nc0")
    action = run_next(next_origin, work, "pr-700", GRUMPY_PROTO, "continue")
    assert action["action"] == "run-agent"
    assert action["iteration"] == 1


@pytest.fixture(scope="module")
def next_continue_active_state(next_origin, next_pr7_state, tmp_path_factory):
    """Simulate a failed iteration 1 on pr-7 and run continue.
    Returns (action, verify_dir) for continue/active tests."""
    # Reuse the pr-7 state already created by start/absent
    n2 = tmp_path_factory.mktemp("n2")
    state_checkout(next_origin, n2)
    sf = n2 / "grumpy-review" / "pr-7.yaml"
    # Bump iteration and inject feedback (mirrors the bash yq command)
    data = read_yaml(sf)
    data["iteration"] = 2
    if "history" not in data or data["history"] is None:
        data["history"] = []
    data["history"].append({"iteration": 1, "agent_run_id": "100",
                             "feedback": "Missing: security × src/auth.js"})
    with open(sf, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    cas_push(next_origin, n2, "simulate failed iteration")

    n3 = tmp_path_factory.mktemp("n3")
    action = run_next(next_origin, n3, "pr-7", GRUMPY_PROTO, "continue")
    return action


def test_next_continue_active_iter(next_continue_active_state):
    """Bash: check "continue/active: resumes iter 2" """
    action = next_continue_active_state
    assert action["iteration"] == 2


def test_next_continue_active_feedback(next_continue_active_state):
    """Bash: check "continue/active: carries feedback" """
    action = next_continue_active_state
    assert "security × src/auth.js" in action.get("feedback", "")


@pytest.fixture(scope="module")
def next_terminal_pr7(next_origin, next_pr7_state, next_continue_active_state,
                      tmp_path_factory):
    """Push pr-7 to terminal (state=done) and return an action from continue."""
    n4 = tmp_path_factory.mktemp("n4")
    state_checkout(next_origin, n4)
    sf = n4 / "grumpy-review" / "pr-7.yaml"
    data = read_yaml(sf)
    data["state"] = "done"
    with open(sf, "w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)
    cas_push(next_origin, n4, "simulate done")

    n5 = tmp_path_factory.mktemp("n5")
    action = run_next(next_origin, n5, "pr-7", GRUMPY_PROTO, "continue")
    return action


def test_next_continue_terminal(next_terminal_pr7):
    """Bash: check "continue/terminal: halts" """
    assert next_terminal_pr7["action"] == "halt"


@pytest.fixture(scope="module")
def next_start_terminal_results(next_origin, next_terminal_pr7, tmp_path_factory):
    """start on TERMINAL → should reset to fresh iter 1. Returns (action, verify_dir)."""
    n6 = tmp_path_factory.mktemp("n6")
    action = run_next(next_origin, n6, "pr-7", GRUMPY_PROTO, "start")
    n6b = tmp_path_factory.mktemp("n6b")
    state_checkout(next_origin, n6b)
    return action, n6b


def test_next_start_terminal(next_start_terminal_results):
    """Bash: check "start/terminal: re-reviews fresh" """
    action, _ = next_start_terminal_results
    assert action["action"] == "run-agent"
    assert action["iteration"] == 1


def test_next_start_terminal_state_reset(next_start_terminal_results):
    """Bash: check "start/terminal: state reset to review" """
    _, verify = next_start_terminal_results
    sf = verify / "grumpy-review" / "pr-7.yaml"
    data = read_yaml(sf)
    assert data["state"] == "review"


def test_next_start_active(next_origin, tmp_path_factory):
    """Bash: check "start/active: halts"
    start on ACTIVE → halt (do not disturb in-flight)."""
    n7 = tmp_path_factory.mktemp("n7")
    state_checkout(next_origin, n7)
    (n7 / "grumpy-review").mkdir(exist_ok=True)
    state = {
        "protocol": "grumpy-review",
        "instance": "pr-88",
        "state": "review",
        "iteration": 2,
        "gates": {},
        "head_sha": "aaa",
        "history": [],
    }
    with open(n7 / "grumpy-review" / "pr-88.yaml", "w") as fh:
        yaml.safe_dump(state, fh, sort_keys=False, default_flow_style=False)
    cas_push(next_origin, n7, "seed pr-88 active")

    n8 = tmp_path_factory.mktemp("n8")
    action = run_next(next_origin, n8, "pr-88", GRUMPY_PROTO, "start")
    assert action["action"] == "halt"


@pytest.fixture(scope="module")
def next_reset_results(next_origin, tmp_path_factory):
    """Seed pr-9 as done@old111, then reset to new222. Returns (action, verify_dir)."""
    n9 = tmp_path_factory.mktemp("n9")
    state_checkout(next_origin, n9)
    (n9 / "grumpy-review").mkdir(exist_ok=True)
    state = {
        "protocol": "grumpy-review",
        "instance": "pr-9",
        "state": "done",
        "iteration": 3,
        "gates": {},
        "head_sha": "old111",
        "history": [{"iteration": 1, "feedback": "old"}],
    }
    with open(n9 / "grumpy-review" / "pr-9.yaml", "w") as fh:
        yaml.safe_dump(state, fh, sort_keys=False, default_flow_style=False)
    cas_push(next_origin, n9, "seed pr-9 done@old111")

    n10 = tmp_path_factory.mktemp("n10")
    action = run_next(next_origin, n10, "pr-9", GRUMPY_PROTO, "reset", head_sha="new222")

    n11 = tmp_path_factory.mktemp("n11")
    state_checkout(next_origin, n11)
    return action, n11


def test_next_reset_run_agent(next_reset_results):
    """Bash: check "reset: run-agent iter 1" """
    action, _ = next_reset_results
    assert action["action"] == "run-agent"
    assert action["iteration"] == 1


def test_next_reset_head_and_state(next_reset_results):
    """Bash: check "reset: new head recorded + state review" """
    _, verify = next_reset_results
    sf = verify / "grumpy-review" / "pr-9.yaml"
    data = read_yaml(sf)
    assert data["head_sha"] == "new222"
    assert data["state"] == "review"


def test_next_reset_active(next_origin, tmp_path_factory):
    """Bash: check "reset/active: run-agent iter 1"
    reset on ACTIVE → also fresh iter 1 (unconditional)."""
    n12 = tmp_path_factory.mktemp("n12")
    state_checkout(next_origin, n12)
    (n12 / "grumpy-review").mkdir(exist_ok=True)
    state = {
        "protocol": "grumpy-review",
        "instance": "pr-99",
        "state": "review",
        "iteration": 2,
        "gates": {},
        "head_sha": "x",
        "history": [{"iteration": 1, "feedback": "prev"}],
    }
    with open(n12 / "grumpy-review" / "pr-99.yaml", "w") as fh:
        yaml.safe_dump(state, fh, sort_keys=False, default_flow_style=False)
    cas_push(next_origin, n12, "seed pr-99 active")

    n13 = tmp_path_factory.mktemp("n13")
    action = run_next(next_origin, n13, "pr-99", GRUMPY_PROTO, "reset", head_sha="z999")
    assert action["action"] == "run-agent"
    assert action["iteration"] == 1


# ===========================================================================
# Section: advance.py single-agent
# ===========================================================================


@pytest.fixture(scope="module")
def advance_origin(tmp_path_factory):
    """Module-scoped bare git origin for advance.py tests."""
    base = tmp_path_factory.mktemp("adv_base")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


@pytest.fixture(scope="module")
def advance_fail_results(advance_origin, tmp_path_factory):
    """advance(fail): failed checks → iteration bump + feedback + re-dispatch intent."""
    vf = tmp_path_factory.mktemp("adv_fail_v") / "verdicts-fail.json"
    vf.write_text(VERDICTS_FAIL)

    w7 = tmp_path_factory.mktemp("w7")
    out, rc = run_advance(
        advance_origin, w7, "pr-8", GRUMPY_PROTO, vf, EVIDENCE_LAZY,
        pr=8, agent_run_id=200,
    )
    assert rc == 0, f"advance(fail) exited nonzero: {out}"

    verify = tmp_path_factory.mktemp("verify7")
    clone_state(advance_origin, verify)
    return out, verify


def test_advance_fail_iter_bumped(advance_fail_results):
    """Bash: check "advance: iteration bumped" """
    _, verify = advance_fail_results
    data = read_yaml(verify / "grumpy-review" / "pr-8.yaml")
    assert data["iteration"] == 2


def test_advance_fail_feedback(advance_fail_results):
    """Bash: check "advance: feedback in history" """
    _, verify = advance_fail_results
    data = read_yaml(verify / "grumpy-review" / "pr-8.yaml")
    history = data.get("history", [])
    assert any("duplication × src/report.js" in (e.get("feedback", "") or "") for e in history)


def test_advance_fail_redispatch(advance_fail_results):
    """Bash: check "advance: re-dispatch intended" """
    out, _ = advance_fail_results
    assert "protocol-continue" in out


@pytest.fixture(scope="module")
def advance_pass_results(advance_origin, advance_fail_results, tmp_path_factory):
    """advance(pass): all pass → publish + state done.

    Depends on advance_fail_results to ensure ordering (pr-8 is already in state
    after the fail run; the pass run now resolves it)."""
    vp = tmp_path_factory.mktemp("adv_pass_v") / "verdicts-pass.json"
    vp.write_text(VERDICTS_PASS)

    w8 = tmp_path_factory.mktemp("w8")
    out, rc = run_advance(
        advance_origin, w8, "pr-8", GRUMPY_PROTO, vp, EVIDENCE_COMPLETE,
        pr=8, agent_run_id=201,
    )
    assert rc == 0, f"advance(pass) exited nonzero: {out}"

    verify = tmp_path_factory.mktemp("verify8")
    clone_state(advance_origin, verify)
    return out, verify


def test_advance_pass_blob_link(advance_pass_results):
    """Bash: check "single-agent comment keeps per-file blob link" """
    out, _ = advance_pass_results
    assert "blob/agentic-state/grumpy-review/pr-8.yaml" in out


def test_advance_pass_state_done(advance_pass_results):
    """Bash: check "advance: state done" """
    _, verify = advance_pass_results
    data = read_yaml(verify / "grumpy-review" / "pr-8.yaml")
    assert data["state"] == "done"


def test_advance_pass_publish(advance_pass_results):
    """Bash: check "advance: publish intended" """
    out, _ = advance_pass_results
    assert "pulls/8/reviews" in out


def test_advance_pass_request_changes(advance_pass_results):
    """Bash: check "advance: verdict REQUEST_CHANGES" """
    out, _ = advance_pass_results
    assert "REQUEST_CHANGES" in out


@pytest.fixture(scope="module")
def advance_exhaust_results(advance_origin, advance_pass_results, tmp_path_factory):
    """Simulate iteration 3 on pr-8 (exhaustion path).

    Depends on advance_pass_results (pr-8 is now done; we need to seed pr-8
    again at iteration 3 to test exhaustion). The bash test re-uses the same
    state and bumps it."""
    w9 = tmp_path_factory.mktemp("w9")
    state_checkout(advance_origin, w9)
    sf = w9 / "grumpy-review" / "pr-8.yaml"
    data = read_yaml(sf)
    data["iteration"] = 3
    data["state"] = "review"
    with open(sf, "w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)
    cas_push(advance_origin, w9, "simulate iteration 3")

    vf = tmp_path_factory.mktemp("adv_exhaust_v") / "verdicts-fail.json"
    vf.write_text(VERDICTS_FAIL)

    w10 = tmp_path_factory.mktemp("w10")
    out, rc = run_advance(
        advance_origin, w10, "pr-8", GRUMPY_PROTO, vf, EVIDENCE_LAZY,
        pr=8, agent_run_id=202,
    )
    assert rc == 0, f"advance(exhaust) exited nonzero: {out}"

    verify = tmp_path_factory.mktemp("verify9")
    clone_state(advance_origin, verify)
    return out, verify


def test_advance_exhaust(advance_exhaust_results):
    """Bash: check "advance: exhausted → failed" """
    _, verify = advance_exhaust_results
    data = read_yaml(verify / "grumpy-review" / "pr-8.yaml")
    assert data["state"] == "failed"


@pytest.fixture(scope="module")
def advance_empty_results(advance_origin, advance_exhaust_results, tmp_path_factory):
    """advance(empty): empty verdicts must NOT publish."""
    ve = tmp_path_factory.mktemp("adv_empty_v") / "verdicts-empty.json"
    ve.write_text(VERDICTS_EMPTY)

    w11 = tmp_path_factory.mktemp("w11")
    out, rc = run_advance(
        advance_origin, w11, "pr-9", GRUMPY_PROTO, ve, EVIDENCE_LAZY,
        pr=9, agent_run_id=203,
    )
    assert rc == 0, f"advance(empty) exited nonzero: {out}"

    verify = tmp_path_factory.mktemp("verify11")
    clone_state(advance_origin, verify)
    return out, verify


def test_advance_empty_not_done(advance_empty_results):
    """Bash: check "advance: empty verdicts → not done" """
    _, verify = advance_empty_results
    data = read_yaml(verify / "grumpy-review" / "pr-9.yaml")
    assert data["state"] != "done"


def test_advance_empty_no_publish(advance_empty_results):
    """Bash: check "advance: empty verdicts → no publish" """
    out, _ = advance_empty_results
    assert "pulls/9/reviews" not in out


# ===========================================================================
# Section: advance.py check-run emission
# ===========================================================================


@pytest.fixture(scope="module")
def checkrun_origin(tmp_path_factory):
    """Module-scoped bare git origin for check-run emission tests."""
    base = tmp_path_factory.mktemp("cr_base")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


def test_checkrun_iterate_in_progress(checkrun_origin, tmp_path_factory):
    """Bash: check "check-run: iterate → in_progress"
    fail at iter < max → check-run status=in_progress."""
    vf = tmp_path_factory.mktemp("cr_fail_v") / "verdicts-fail.json"
    vf.write_text(VERDICTS_FAIL)
    c1 = tmp_path_factory.mktemp("c1")
    out, _ = run_advance(
        checkrun_origin, c1, "pr-20", GRUMPY_PROTO, vf, EVIDENCE_LAZY,
        pr=20, agent_run_id=300, pr_head_sha="testsha123",
    )
    assert "check-run grumpy-review sha=testsha123 status=in_progress" in out


def test_checkrun_pass_failure(checkrun_origin, tmp_path_factory):
    """Bash: check "check-run: changes requested → failure"
    all pass with issues-found → status=completed conclusion=failure."""
    vp = tmp_path_factory.mktemp("cr_pass_v") / "verdicts-pass.json"
    vp.write_text(VERDICTS_PASS)
    c2 = tmp_path_factory.mktemp("c2")
    out, _ = run_advance(
        checkrun_origin, c2, "pr-20", GRUMPY_PROTO, vp, EVIDENCE_COMPLETE,
        pr=20, agent_run_id=301, pr_head_sha="testsha123",
    )
    assert "status=completed conclusion=failure" in out


def test_checkrun_exhaust_failure(checkrun_origin, tmp_path_factory):
    """Bash: check "check-run: exhausted → failure"
    fail at iter==max → status=completed conclusion=failure."""
    # Seed pr-21 at iteration 3
    w12 = tmp_path_factory.mktemp("c3")
    state_checkout(checkrun_origin, w12)
    (w12 / "grumpy-review").mkdir(exist_ok=True)
    state = {
        "protocol": "grumpy-review",
        "instance": "pr-21",
        "state": "review",
        "iteration": 3,
        "gates": {},
        "history": [],
    }
    with open(w12 / "grumpy-review" / "pr-21.yaml", "w") as fh:
        yaml.safe_dump(state, fh, sort_keys=False, default_flow_style=False)
    cas_push(checkrun_origin, w12, "seed pr-21 iter3")

    vf = tmp_path_factory.mktemp("cr_exhaust_v") / "verdicts-fail.json"
    vf.write_text(VERDICTS_FAIL)
    c4 = tmp_path_factory.mktemp("c4")
    out, _ = run_advance(
        checkrun_origin, c4, "pr-21", GRUMPY_PROTO, vf, EVIDENCE_LAZY,
        pr=21, agent_run_id=302, pr_head_sha="testsha123",
    )
    assert "status=completed conclusion=failure" in out


# ===========================================================================
# Section: advance.py stub/relay test
# ===========================================================================


def test_advance_relay(tmp_path):
    """Bash: check "advance: relays hook conclusion" + "advance: relays hook summary"

    The bash test creates a stub publish hook that echoes a known
    {"conclusion":"success","summary":"STUB-RELAYED-OK"} and a stub protocol.json
    pointing at it (action="stub-publish"), then verifies advance.py relays
    the hook's conclusion and summary to the check-run output.

    We create both stub files in tmp_path (not in the repo tree) and point
    advance.py at the stub protocol. Cleanup is guaranteed by tmp_path's
    tear-down even on test failure.
    """
    # Create a fresh bare origin for this test
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    # Build a stub protocol directory mirroring .github/agent-factory/protocols/grumpy/
    # but with all paths redirected into tmp_path.
    stub_proto_dir = tmp_path / "stub-proto"
    stub_proto_dir.mkdir()
    stub_publish_dir = stub_proto_dir / "publish"
    stub_publish_dir.mkdir()

    # The stub hook outputs the known conclusion + summary
    stub_hook = stub_publish_dir / "stub-publish.sh"
    stub_hook.write_text(
        '#!/usr/bin/env bash\n'
        'echo \'{"conclusion":"success","summary":"STUB-RELAYED-OK"}\'\n'
    )
    stub_hook.chmod(stub_hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # The stub protocol JSON: copy the grumpy protocol but override the publish
    # state's action to "stub-publish" (matching the stub hook filename).
    with open(GRUMPY_PROTO) as fh:
        proto_data = json.load(fh)
    for state in proto_data.get("states", []):
        if state.get("kind") == "deterministic":
            state["action"] = "stub-publish"
    stub_proto_json = stub_proto_dir / "stub-proto.json"
    with open(stub_proto_json, "w") as fh:
        json.dump(proto_data, fh)

    # Write stub verdicts (one check, pass=True) to the stub proto dir
    stub_verdicts = tmp_path / "verdicts-stub.json"
    stub_verdicts.write_text(
        json.dumps({"results": [{"check": "x", "pass": True, "feedback": ""}]})
    )

    relay_work = tmp_path / "relay"
    out, rc = run_advance(
        origin, relay_work, "pr-8", stub_proto_json, stub_verdicts, EVIDENCE_COMPLETE,
        pr=8, agent_run_id=400,
    )
    assert rc == 0, f"advance(relay) exited nonzero: {out}"

    # Bash: check "advance: relays hook conclusion"
    assert "conclusion=success" in out, f"expected conclusion=success in: {out}"
    # Bash: check "advance: relays hook summary"
    assert "STUB-RELAYED-OK" in out, f"expected STUB-RELAYED-OK in: {out}"


# ===========================================================================
# Section: lib branch-aware paths
# ===========================================================================


def test_lib_state_file_single_agent(tmp_path):
    """Bash: check "state_file single-agent form unchanged"
    state_file /s grumpy-review pr-5 → /s/grumpy-review/pr-5.yaml"""
    out, _, rc = lib_cmd(None, "state-file", "/s", "grumpy-review", "pr-5",
                         extra_env={"STATE_REMOTE": str(tmp_path)})
    # lib_cmd passes STATE_REMOTE but state_file is pure — no remote needed
    assert rc == 0
    assert out == "/s/grumpy-review/pr-5.yaml"


def test_lib_state_file_branch(tmp_path):
    """Bash: check "state_file branch form nests under instance dir"
    state_file /s multi-grumpy pr-5 grumpy → /s/multi-grumpy/pr-5/grumpy.yaml"""
    out, _, rc = lib_cmd(None, "state-file", "/s", "multi-grumpy", "pr-5", "grumpy",
                         extra_env={"STATE_REMOTE": str(tmp_path)})
    assert rc == 0
    assert out == "/s/multi-grumpy/pr-5/grumpy.yaml"


def test_lib_instance_file(tmp_path):
    """Bash: check "instance_file points at _instance.yaml"
    instance_file /s multi-grumpy pr-5 → /s/multi-grumpy/pr-5/_instance.yaml"""
    out, _, rc = lib_cmd(None, "instance-file", "/s", "multi-grumpy", "pr-5",
                         extra_env={"STATE_REMOTE": str(tmp_path)})
    assert rc == 0
    assert out == "/s/multi-grumpy/pr-5/_instance.yaml"


# ===========================================================================
# Section: next.py branch-scoped continue
# ===========================================================================


@pytest.fixture(scope="module")
def branch_continue_origin(tmp_path_factory):
    base = tmp_path_factory.mktemp("bc_base")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


@pytest.fixture(scope="module")
def branch_continue_results(branch_continue_origin, tmp_path_factory):
    """Seed pr-77 security branch at iter 2 and run continue with BRANCH=security."""
    nb1 = tmp_path_factory.mktemp("nb1")
    state_checkout(branch_continue_origin, nb1)
    (nb1 / "multi-grumpy" / "pr-77").mkdir(parents=True, exist_ok=True)
    state = {
        "protocol": "multi-grumpy",
        "instance": "pr-77",
        "state": "review",
        "iteration": 2,
        "gates": {},
        "history": [{"iteration": 1, "feedback": "sec: missing anchor"}],
    }
    with open(nb1 / "multi-grumpy" / "pr-77" / "security.yaml", "w") as fh:
        yaml.safe_dump(state, fh, sort_keys=False, default_flow_style=False)
    cas_push(branch_continue_origin, nb1, "seed pr-77 security active@iter2")

    nb2 = tmp_path_factory.mktemp("nb2")
    action = run_next(branch_continue_origin, nb2, "pr-77", MULTI_PROTO, "continue",
                      branch="security")
    return action


def test_next_branch_continue_iter(branch_continue_results):
    """Bash: check "branch continue: resumes iter 2" """
    assert branch_continue_results["iteration"] == 2


def test_next_branch_continue_feedback(branch_continue_results):
    """Bash: check "branch continue: carries branch feedback" """
    assert "missing anchor" in branch_continue_results.get("feedback", "")


# ===========================================================================
# Section: next.py fanout planning
# ===========================================================================


@pytest.fixture(scope="module")
def fanout_origin(tmp_path_factory):
    base = tmp_path_factory.mktemp("fo_base")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


@pytest.fixture(scope="module")
def fanout_start_results(fanout_origin, tmp_path_factory):
    """Run next.py start on multi-grumpy (fanout) protocol for pr-50."""
    fo1 = tmp_path_factory.mktemp("fo1")
    action = run_next(fanout_origin, fo1, "pr-50", MULTI_PROTO, "start", head_sha="head50")
    fo2 = tmp_path_factory.mktemp("fo2")
    state_checkout(fanout_origin, fo2)
    return action, fo2


def test_fanout_start_action(fanout_start_results):
    """Bash: check "fanout start: action run-fanout" """
    action, _ = fanout_start_results
    assert action["action"] == "run-fanout"


def test_fanout_start_two_branches(fanout_start_results):
    """Bash: check "fanout start: two branches listed" """
    action, _ = fanout_start_results
    assert len(action["branches"]) == 2


def test_fanout_start_grumpy_workflow(fanout_start_results):
    """Bash: check "fanout start: lists grumpy workflow" """
    action, _ = fanout_start_results
    grumpy_branches = [b for b in action["branches"] if b["id"] == "grumpy"]
    assert len(grumpy_branches) == 1
    assert grumpy_branches[0]["workflow"] == "grumpy-agent"


def test_fanout_start_grumpy_seeded(fanout_start_results):
    """Bash: check "fanout start: grumpy file seeded active" """
    _, verify = fanout_start_results
    sf = verify / "multi-grumpy" / "pr-50" / "grumpy.yaml"
    assert sf.exists()
    data = read_yaml(sf)
    assert data["state"] == "review"
    assert data["iteration"] == 1


def test_fanout_start_security_seeded(fanout_start_results):
    """Bash: check "fanout start: security file seeded active" """
    _, verify = fanout_start_results
    sf = verify / "multi-grumpy" / "pr-50" / "security.yaml"
    assert sf.exists()
    data = read_yaml(sf)
    assert data["state"] == "review"
    assert data["iteration"] == 1


def test_fanout_start_instance_file(fanout_start_results):
    """Bash: check "fanout start: instance file w/ head" """
    _, verify = fanout_start_results
    inf = verify / "multi-grumpy" / "pr-50" / "_instance.yaml"
    assert inf.exists()
    data = read_yaml(inf)
    assert data["head_sha"] == "head50"
    assert data["joined"] is False


# ===========================================================================
# Section: next.py branch lifecycle
# ===========================================================================


@pytest.fixture(scope="module")
def lifecycle_origin(tmp_path_factory):
    base = tmp_path_factory.mktemp("lc_base")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


def test_branch_lifecycle_terminal(lifecycle_origin, tmp_path_factory):
    """Bash: check "branch continue/terminal: halts"
    Terminal branch (done) → halt."""
    bl1 = tmp_path_factory.mktemp("bl1")
    state_checkout(lifecycle_origin, bl1)
    (bl1 / "multi-grumpy" / "pr-60").mkdir(parents=True, exist_ok=True)
    state = {
        "protocol": "multi-grumpy",
        "instance": "pr-60",
        "state": "done",
        "iteration": 2,
        "gates": {},
        "history": [],
    }
    with open(bl1 / "multi-grumpy" / "pr-60" / "grumpy.yaml", "w") as fh:
        yaml.safe_dump(state, fh, sort_keys=False, default_flow_style=False)
    cas_push(lifecycle_origin, bl1, "seed pr-60 grumpy done")

    bl2 = tmp_path_factory.mktemp("bl2")
    action = run_next(lifecycle_origin, bl2, "pr-60", MULTI_PROTO, "continue",
                      branch="grumpy")
    assert action["action"] == "halt"


def test_branch_lifecycle_absent(lifecycle_origin, tmp_path_factory):
    """Bash: check "branch continue/absent: fresh run-agent iter 1"
    Absent branch → fresh run-agent iter 1."""
    bl3 = tmp_path_factory.mktemp("bl3")
    action = run_next(lifecycle_origin, bl3, "pr-61", MULTI_PROTO, "continue",
                      branch="grumpy")
    assert action["action"] == "run-agent"
    assert action["iteration"] == 1


# ===========================================================================
# Section: advance.py branch-scoped (multi-grumpy grumpy branch)
# ===========================================================================


@pytest.fixture(scope="module")
def adv_branch_origin(tmp_path_factory):
    """Shared bare origin for branch-scoped advance tests.

    pr-50 fan-out state was seeded by fanout_start_results above (different
    origin). Here we create a fresh origin and pre-seed it so the advance tests
    for branches can run independently from the next.py fanout section.
    """
    base = tmp_path_factory.mktemp("adv_branch_base")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


def _seed_fanout_pr50(origin, tmp_path_factory):
    """Seed multi-grumpy/pr-50 with both branch files + _instance.yaml."""
    seed_dir = tmp_path_factory.mktemp("seed_pr50")
    state_checkout(origin, seed_dir)
    (seed_dir / "multi-grumpy" / "pr-50").mkdir(parents=True, exist_ok=True)

    for bid in ("grumpy", "security"):
        sf = seed_dir / "multi-grumpy" / "pr-50" / f"{bid}.yaml"
        yaml_content = {
            "protocol": "multi-grumpy",
            "instance": "pr-50",
            "state": "review",
            "iteration": 1,
            "gates": {},
            "history": [],
        }
        with open(sf, "w") as fh:
            yaml.safe_dump(yaml_content, fh, sort_keys=False, default_flow_style=False)

    inf = seed_dir / "multi-grumpy" / "pr-50" / "_instance.yaml"
    with open(inf, "w") as fh:
        yaml.safe_dump({
            "protocol": "multi-grumpy",
            "instance": "pr-50",
            "head_sha": "mgsha1",
            "joined": False,
        }, fh, sort_keys=False, default_flow_style=False)

    cas_push(origin, seed_dir, "seed pr-50 fanout")


@pytest.fixture(scope="module")
def adv_branch_grumpy_results(adv_branch_origin, tmp_path_factory):
    """advance(branch=grumpy, pr-50, all-pass): done + per-branch publish + check-run."""
    _seed_fanout_pr50(adv_branch_origin, tmp_path_factory)

    vp = tmp_path_factory.mktemp("adv_bp_v") / "verdicts-pass.json"
    vp.write_text(VERDICTS_PASS)

    w = tmp_path_factory.mktemp("advmg1")
    out, rc = run_advance(
        adv_branch_origin, w, "pr-50", MULTI_PROTO, vp, EVIDENCE_COMPLETE,
        branch="grumpy", pr=50, agent_run_id=900, pr_head_sha="mgsha1",
    )
    assert rc == 0, f"advance(mg grumpy) nonzero: {out}"

    verify = tmp_path_factory.mktemp("vmg1")
    clone_state(adv_branch_origin, verify)
    return out, verify


def test_advance_branch_state_done(adv_branch_grumpy_results):
    """Bash: check "advance branch: grumpy.yaml state done" """
    _, verify = adv_branch_grumpy_results
    data = read_yaml(verify / "multi-grumpy" / "pr-50" / "grumpy.yaml")
    assert data["state"] == "done"


def test_advance_branch_published(adv_branch_grumpy_results):
    """Bash: check "advance branch: published via hook" """
    out, _ = adv_branch_grumpy_results
    assert "pulls/50/reviews" in out


def test_advance_branch_checkrun_name(adv_branch_grumpy_results):
    """Bash: check "advance branch: per-branch check-run name" """
    out, _ = adv_branch_grumpy_results
    assert "check-run multi-grumpy/grumpy " in out


def test_advance_branch_comment_section(adv_branch_grumpy_results):
    """Bash: check "advance branch: shared comment has grumpy section" """
    out, _ = adv_branch_grumpy_results
    assert "**grumpy**" in out


def test_advance_branch_comment_tree_link(adv_branch_grumpy_results):
    """Bash: check "advance branch: shared comment tree/ link" """
    out, _ = adv_branch_grumpy_results
    assert "tree/agentic-state/multi-grumpy/pr-50" in out


def test_advance_branch_comment_no_blob(adv_branch_grumpy_results):
    """Bash: check "advance branch: shared comment not blob link" """
    out, _ = adv_branch_grumpy_results
    assert "blob/agentic-state" not in out


# ===========================================================================
# Section: advance.py fan-out signalling
# ===========================================================================


@pytest.fixture(scope="module")
def fanout_signal_origin(tmp_path_factory):
    """Shared bare origin for fan-out signalling tests."""
    base = tmp_path_factory.mktemp("fs_base")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


def _seed_pr(origin, tmp_path_factory, pr_num, branches=("grumpy", "security"),
             head_sha="mgsha1"):
    """Seed a fan-out PR with given branches all at iteration=1, state=review."""
    seed_dir = tmp_path_factory.mktemp(f"seed_pr{pr_num}")
    state_checkout(origin, seed_dir)
    (seed_dir / "multi-grumpy" / f"pr-{pr_num}").mkdir(parents=True, exist_ok=True)
    for bid in branches:
        sf = seed_dir / "multi-grumpy" / f"pr-{pr_num}" / f"{bid}.yaml"
        with open(sf, "w") as fh:
            yaml.safe_dump({
                "protocol": "multi-grumpy",
                "instance": f"pr-{pr_num}",
                "state": "review",
                "iteration": 1,
                "gates": {},
                "history": [],
            }, fh, sort_keys=False, default_flow_style=False)
    inf = seed_dir / "multi-grumpy" / f"pr-{pr_num}" / "_instance.yaml"
    with open(inf, "w") as fh:
        yaml.safe_dump({
            "protocol": "multi-grumpy",
            "instance": f"pr-{pr_num}",
            "head_sha": head_sha,
            "joined": False,
        }, fh, sort_keys=False, default_flow_style=False)
    cas_push(origin, seed_dir, f"seed pr-{pr_num} fanout")


@pytest.fixture(scope="module")
def fanout_iterate_results(fanout_signal_origin, tmp_path_factory):
    """iterate (fail, iter<max) on security branch of pr-50 → protocol-continue WITH branch."""
    _seed_pr(fanout_signal_origin, tmp_path_factory, 50)

    vf = tmp_path_factory.mktemp("fs_v") / "verdicts-secfail.json"
    vf.write_text(json.dumps({
        "results": [{"check": "schema-valid", "pass": False, "feedback": "sec: bad anchor"}]
    }))

    w = tmp_path_factory.mktemp("advmg2")
    out, rc = run_advance(
        fanout_signal_origin, w, "pr-50", MULTI_PROTO, vf, EVIDENCE_LAZY,
        branch="security", pr=50, agent_run_id=901,
    )
    assert rc == 0, f"advance(security iterate) nonzero: {out}"
    return out


def test_fanout_signal_continue(fanout_iterate_results):
    """Bash: check "fanout iterate: protocol-continue fired" """
    assert "protocol-continue" in fanout_iterate_results


def test_fanout_signal_branch_payload(fanout_iterate_results):
    """Bash: check "fanout iterate: payload carries branch" """
    assert "client_payload[branch]=security" in fanout_iterate_results


@pytest.fixture(scope="module")
def fanout_done_results(fanout_signal_origin, fanout_iterate_results, tmp_path_factory):
    """terminal (done) on grumpy branch of pr-51 → protocol-join fired."""
    _seed_pr(fanout_signal_origin, tmp_path_factory, 51)

    vp = tmp_path_factory.mktemp("fs_pv") / "verdicts-pass.json"
    vp.write_text(VERDICTS_PASS)

    w = tmp_path_factory.mktemp("advmg3")
    out, rc = run_advance(
        fanout_signal_origin, w, "pr-51", MULTI_PROTO, vp, EVIDENCE_COMPLETE,
        branch="grumpy", pr=51, agent_run_id=902,
    )
    assert rc == 0, f"advance(grumpy done) nonzero: {out}"
    return out


def test_fanout_signal_join(fanout_done_results):
    """Bash: check "fanout done: protocol-join fired" """
    assert "protocol-join" in fanout_done_results
