"""Port of tests/test-runchecks.sh — tests for the data-driven check runner (run-checks.py).

Bash assertion → pytest mapping
--------------------------------
Happy path / aggregation (grumpy protocol.json, review state, evidence-complete)
  1.  "runner: 3 results"
      → test_runner_three_results
  2.  "runner: all pass on complete"
      → test_runner_all_pass_on_complete
  3.  "runner: includes python rubric-coverage"
      → test_runner_includes_rubric_coverage

Lazy evidence
  4.  "runner: lazy → rubric-coverage fails"
      → test_runner_lazy_rubric_coverage_fails
  5.  "runner: lazy → schema-valid passes"
      → test_runner_lazy_schema_valid_passes

Unknown check name
  6.  "runner: unknown check → fail verdict"
      → test_runner_unknown_check_fail_verdict
  7.  "runner: unknown check → useful feedback"
      → test_runner_unknown_check_useful_feedback

Explicit exec override
  8.  "runner: exec override runs the file"
      → test_runner_exec_override

Non-executable check file
  9.  "runner: non-executable check → fail verdict"
      → test_runner_non_executable_fail_verdict
  10. "runner: non-executable → useful feedback"
      → test_runner_non_executable_useful_feedback

Crashing check
  11. "runner: crashing check → fail verdict"
      → test_runner_crashing_check_fail_verdict

resolve_executable unit tests (via lib.py direct import)
  12. "resolve: finds checks/schema-valid.py"
      → test_resolve_finds_schema_valid
  13. "resolve: missing → ERR"
      → test_resolve_missing_returns_err
  14. "resolve: explicit exec resolves"
      → test_resolve_explicit_exec

Branch-aware check list (fanout-mini protocol)
  15. "branch grumpy → 3 checks run"
      → test_branch_grumpy_three_checks
  16. "branch security → 2 checks run (no rubric-coverage)"
      → test_branch_security_two_checks_no_rubric_coverage
  17. "branch security → schema-valid rejects non-security category"
      → test_branch_security_schema_valid_rejects_non_security

Params forwarding
  18. "params: state-scoped forwarded"
      → test_params_state_scoped_forwarded
  19. "params: branch-scoped overrides state"
      → test_params_branch_scoped_overrides_state
"""

import json
import os
import stat
import sys
import pathlib

import pytest

# Direct import of lib — matches the pattern from test_correlation.py.
ENGINE = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

from conftest import FIXTURES, PROTOCOLS, ENGINE as ENGINE_CONST, run_engine

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# The single-agent / fanout-mini fixtures were deleted with the legacy engine; the
# code-review protocol carries the identical schema-valid / rubric-coverage /
# traces-exist-in-diff checks under its `review` fanout (grumpy: all three;
# security: schema-valid + traces). run-checks.py resolves the `review` fanout's
# grumpy branch checks, so the resolution tests pass branch="grumpy".
GRUMPY_PROTO = PROTOCOLS / "code-review/protocol.json"
MULTI_GRUMPY_PROTO = PROTOCOLS / "code-review/protocol.json"
GRUMPY_CHECKS_DIR = PROTOCOLS / "code-review/checks"
GRUMPY_PDIR = PROTOCOLS / "code-review"

EV_COMPLETE = FIXTURES / "evidence-complete.json"
EV_LAZY = FIXTURES / "evidence-lazy.json"
DIFF_PR1 = FIXTURES / "diff-pr1.txt"
FILES_PR1 = FIXTURES / "changed-files-pr1.txt"


def run_checks(proto, state_id, evidence, diff, files, branch=None, substate=None, env=None):
    """Invoke run-checks.py and return the parsed dict with 'results' list."""
    stdout, stderr, rc = run_engine(
        "run-checks.py",
        proto, state_id, evidence, diff, files,
        env=env,
        branch=branch,
        substate=substate,
    )
    return json.loads(stdout)


# ===========================================================================
# Happy path — grumpy protocol, review state, evidence-complete
# ===========================================================================

# 1
def test_runner_three_results():
    """Bash assertion 1: 3 checks configured → 3 results."""
    out = run_checks(GRUMPY_PROTO, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1, branch="grumpy")
    assert len(out["results"]) == 3

# 2
def test_runner_all_pass_on_complete():
    """Bash assertion 2: all checks pass with evidence-complete."""
    out = run_checks(GRUMPY_PROTO, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1, branch="grumpy")
    assert all(r["pass"] for r in out["results"])

# 3
def test_runner_includes_rubric_coverage():
    """Bash assertion 3: rubric-coverage check is present in results."""
    out = run_checks(GRUMPY_PROTO, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1, branch="grumpy")
    names = {r["check"] for r in out["results"]}
    assert "rubric-coverage" in names


# ===========================================================================
# Lazy evidence — rubric-coverage fails, schema-valid passes
# ===========================================================================

# 4
def test_runner_lazy_rubric_coverage_fails():
    """Bash assertion 4: lazy evidence → rubric-coverage check fails."""
    out = run_checks(GRUMPY_PROTO, "review", EV_LAZY, DIFF_PR1, FILES_PR1, branch="grumpy")
    rc_result = next(r for r in out["results"] if r["check"] == "rubric-coverage")
    assert rc_result["pass"] is False

# 5
def test_runner_lazy_schema_valid_passes():
    """Bash assertion 5: lazy evidence → schema-valid check passes."""
    out = run_checks(GRUMPY_PROTO, "review", EV_LAZY, DIFF_PR1, FILES_PR1, branch="grumpy")
    sv_result = next(r for r in out["results"] if r["check"] == "schema-valid")
    assert sv_result["pass"] is True


# ===========================================================================
# Unknown check name → synthesised not-found failure, run still completes
# Bash uses a temp protocol file placed inside the grumpy dir so the resolver
# can find checks/ relative to the protocol directory.
# ===========================================================================

@pytest.fixture
def temp_proto_in_grumpy(tmp_path):
    """Write a temp protocol.json inside the code-review protocol dir (so the
    resolver finds checks/ relative to it); clean up after. The returned object
    is a (path, write) pair — `write(checks)` writes a minimal single-`review`-
    agent protocol carrying the given check entries."""
    tp = GRUMPY_PDIR / ".test-proto.json"

    def write(checks):
        tp.write_text(json.dumps({
            "name": "rc-test",
            "states": [{"id": "review", "kind": "agent", "workflow": "x",
                        "params": {"categories": ["naming", "error-handling",
                                                  "performance", "duplication", "security"]},
                        "checks": checks}],
        }))
    try:
        yield tp, write
    finally:
        if tp.exists():
            tp.unlink()


# 6
def test_runner_unknown_check_fail_verdict(temp_proto_in_grumpy):
    """Bash assertion 6: unknown check name → pass=false."""
    tp, write = temp_proto_in_grumpy
    write([{"run": "does-not-exist", "on_fail": "iterate"}])
    out = run_checks(tp, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1)
    assert out["results"][0]["pass"] is False

# 7
def test_runner_unknown_check_useful_feedback(temp_proto_in_grumpy):
    """Bash assertion 7: unknown check → feedback contains 'no executable found'."""
    tp, write = temp_proto_in_grumpy
    write([{"run": "does-not-exist", "on_fail": "iterate"}])
    out = run_checks(tp, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1)
    assert "no executable found" in out["results"][0]["feedback"]


# ===========================================================================
# Explicit exec override
# ===========================================================================

# 8
def test_runner_exec_override(temp_proto_in_grumpy):
    """Bash assertion 8: exec override runs the named file → pass=true."""
    tp, write = temp_proto_in_grumpy
    write([{"run": "sv", "exec": "checks/schema-valid.py", "on_fail": "iterate"}])

    out = run_checks(tp, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1)
    assert out["results"][0]["pass"] is True


# ===========================================================================
# Non-executable check file
# ===========================================================================

def _make_sandbox_proto(tmp_path, check_script_content, make_executable=False):
    """Build a minimal temp protocol + checks dir in tmp_path. Returns protocol.json path."""
    checks_dir = tmp_path / "checks"
    checks_dir.mkdir(parents=True)
    script = checks_dir / "noexec.sh"
    script.write_text(check_script_content)
    if make_executable:
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    proto = tmp_path / "protocol.json"
    proto.write_text(json.dumps({
        "name": "x",
        "states": [{"id": "review", "checks": [{"run": "noexec"}]}]
    }))
    return proto


# 9
def test_runner_non_executable_fail_verdict(tmp_path):
    """Bash assertion 9: check file exists but not +x → pass=false."""
    proto = _make_sandbox_proto(tmp_path, '#!/usr/bin/env bash\necho "{}"\n', make_executable=False)
    out = run_checks(proto, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1)
    assert out["results"][0]["pass"] is False

# 10
def test_runner_non_executable_useful_feedback(tmp_path):
    """Bash assertion 10: non-executable check → feedback contains 'not executable'."""
    proto = _make_sandbox_proto(tmp_path, '#!/usr/bin/env bash\necho "{}"\n', make_executable=False)
    out = run_checks(proto, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1)
    assert "not executable" in out["results"][0]["feedback"]


# ===========================================================================
# Crashing check (exit non-zero)
# ===========================================================================

# 11
def test_runner_crashing_check_fail_verdict(tmp_path):
    """Bash assertion 11: check exits non-zero → pass=false, run survives."""
    proto = _make_sandbox_proto(tmp_path, '#!/usr/bin/env bash\nexit 3\n', make_executable=True)
    out = run_checks(proto, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1)
    assert out["results"][0]["pass"] is False


# ===========================================================================
# resolve_executable unit tests — direct import of lib
# ===========================================================================

PDIR_STR = str(GRUMPY_PDIR)
CHECKS_STR = str(GRUMPY_CHECKS_DIR)

# 12
def test_resolve_finds_schema_valid():
    """Bash assertion 12: resolve 'schema-valid' → OK + path contains checks/schema-valid.py."""
    result = lib.resolve_executable(CHECKS_STR, "schema-valid", PDIR_STR, "")
    kind, rest = result.split("\t", 1)
    assert kind == "OK"
    assert "checks/schema-valid.py" in rest

# 13
def test_resolve_missing_returns_err():
    """Bash assertion 13: resolve 'does-not-exist' → ERR."""
    result = lib.resolve_executable(CHECKS_STR, "does-not-exist", PDIR_STR, "")
    kind, _ = result.split("\t", 1)
    assert kind == "ERR"

# 14
def test_resolve_explicit_exec():
    """Bash assertion 14: explicit exec 'checks/rubric-coverage.py' → OK + path contains rubric-coverage.py."""
    result = lib.resolve_executable(CHECKS_STR, "ignored", PDIR_STR, "checks/rubric-coverage.py")
    kind, rest = result.split("\t", 1)
    assert kind == "OK"
    assert "rubric-coverage.py" in rest


# ===========================================================================
# Branch-aware check list (fanout-mini protocol)
# ===========================================================================

# 15
def test_branch_grumpy_three_checks():
    """Bash assertion 15: BRANCH=grumpy → 3 checks run."""
    out = run_checks(MULTI_GRUMPY_PROTO, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1, branch="grumpy")
    assert len(out["results"]) == 3

# 16
def test_branch_security_two_checks_no_rubric_coverage():
    """Bash assertion 16: BRANCH=security → 2 checks run, no rubric-coverage."""
    out = run_checks(MULTI_GRUMPY_PROTO, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1, branch="security")
    assert len(out["results"]) == 2
    names = {r["check"] for r in out["results"]}
    assert "rubric-coverage" not in names

# 17
def test_branch_security_schema_valid_rejects_non_security():
    """Bash assertion 17: BRANCH=security → schema-valid rejects non-security categories in evidence-complete."""
    out = run_checks(MULTI_GRUMPY_PROTO, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1, branch="security")
    sv = next(r for r in out["results"] if r["check"] == "schema-valid")
    assert sv["pass"] is False
    assert "illegal category" in sv["feedback"]


# ===========================================================================
# Params forwarding via a stub protocol + echo-params.sh check
# ===========================================================================

@pytest.fixture
def params_sandbox(tmp_path):
    """Build a protocol with an echo-params.sh check that echoes CHECK_PARAMS as feedback."""
    checks_dir = tmp_path / "checks"
    checks_dir.mkdir()
    echo_script = checks_dir / "echo-params.sh"
    echo_script.write_text(
        '#!/usr/bin/env bash\n'
        'jq -nc --arg f "${CHECK_PARAMS:-MISSING}" \'{check:"echo-params",pass:true,feedback:$f}\'\n'
    )
    echo_script.chmod(echo_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    proto = tmp_path / "protocol.json"
    proto.write_text(json.dumps({
        "name": "p",
        "states": [{
            "id": "s",
            "params": {"categories": ["a", "b"]},
            "checks": [{"run": "echo-params"}],
            "branches": [{
                "id": "bx",
                "params": {"categories": ["only-b"]},
                "checks": [{"run": "echo-params"}]
            }]
        }]
    }))
    return proto

# 18
def test_params_state_scoped_forwarded(params_sandbox):
    """Bash assertion 18: state-scoped params forwarded as CHECK_PARAMS (no BRANCH)."""
    out = run_checks(params_sandbox, "s", EV_COMPLETE, DIFF_PR1, FILES_PR1)
    feedback_parsed = json.loads(out["results"][0]["feedback"])
    assert feedback_parsed["categories"] == ["a", "b"]

# 19
def test_params_branch_scoped_overrides_state(params_sandbox):
    """Bash assertion 19: BRANCH=bx → branch-scoped params override state params."""
    out = run_checks(params_sandbox, "s", EV_COMPLETE, DIFF_PR1, FILES_PR1, branch="bx")
    feedback_parsed = json.loads(out["results"][0]["feedback"])
    assert feedback_parsed["categories"] == ["only-b"]


def test_runner_stamps_default_on_fail_iterate():
    """Every verdict carries on_fail; absent in protocol.json ⇒ 'iterate'."""
    out = run_checks(GRUMPY_PROTO, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1, branch="grumpy")
    results = out["results"]
    assert results, "expected verdicts"
    assert all(v.get("on_fail") == "iterate" for v in results), results


def test_runner_stamps_declared_on_fail_on_failure_verdict(temp_proto_in_grumpy):
    """A failure verdict is stamped with the entry's DECLARED on_fail (not the default)."""
    tp, write = temp_proto_in_grumpy
    write([{"run": "does-not-exist", "on_fail": "block"}])
    out = run_checks(tp, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1)
    assert out["results"][0]["pass"] is False
    assert out["results"][0]["on_fail"] == "block"


def test_runner_stamps_declared_on_fail_on_passing_verdict(temp_proto_in_grumpy):
    """A declared non-default on_fail is stamped onto a PASSING verdict too."""
    tp, write = temp_proto_in_grumpy
    write([{"run": "schema-valid", "on_fail": "advisory"}])
    out = run_checks(tp, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1)
    assert out["results"][0]["pass"] is True
    assert out["results"][0]["on_fail"] == "advisory"


# ===========================================================================
# Gap B — SUBSTATE support in run-checks.py
# Tests written BEFORE implementation (TDD RED).
# ===========================================================================

SUBPIPE_PROTO_PATH = FIXTURES / "subpipeline-gate/protocol.json"
EV_EMPTY = FIXTURES / "diff-pr1.txt"   # reuse as a stand-in; we just need a valid file path
# Use /dev/null as a minimal empty evidence file
import tempfile as _tempfile


def _empty_evidence(tmp_path_local=None):
    """Return a path to a minimal empty JSON evidence file."""
    if tmp_path_local:
        p = tmp_path_local / "empty-ev.json"
        p.write_text("{}")
        return p
    # Fallback: create in system temp
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".json")
    os.write(fd, b"{}")
    os.close(fd)
    return pathlib.Path(path)


def _empty_diff(tmp_path_local=None):
    """Return a path to an empty diff file."""
    if tmp_path_local:
        p = tmp_path_local / "empty-diff.txt"
        p.write_text("")
        return p
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    return pathlib.Path(path)


def _empty_files(tmp_path_local=None):
    """Return a path to an empty changed-files file."""
    if tmp_path_local:
        p = tmp_path_local / "empty-files.txt"
        p.write_text("")
        return p
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    return pathlib.Path(path)


# 20
def test_substate_draft_runs_its_check(tmp_path):
    """BRANCH=rationale SUBSTATE=draft → sub-state 'draft' has the questions-present
    check → 1 result (subpipeline-gate fixture sub-pipeline leg)."""
    ev = _empty_evidence(tmp_path)
    diff = _empty_diff(tmp_path)
    files = _empty_files(tmp_path)
    out = run_checks(SUBPIPE_PROTO_PATH, "recover", ev, diff, files,
                     branch="rationale", substate="draft")
    assert len(out["results"]) == 1
    assert out["results"][0]["check"] == "questions-present"


# 21
def test_substate_branch_only_no_checks(tmp_path):
    """BRANCH=rationale with NO SUBSTATE → the branch node itself has no 'checks'
    → empty results."""
    ev = _empty_evidence(tmp_path)
    diff = _empty_diff(tmp_path)
    files = _empty_files(tmp_path)
    out = run_checks(SUBPIPE_PROTO_PATH, "recover", ev, diff, files, branch="rationale")
    assert out["results"] == []


# ===========================================================================
# NODE_PATH mode (Stage 4b) — the unified single-coordinate path.
# run-checks.py navigates the protocol tree via paths.node_at_path when NODE_PATH
# is set (no BRANCH/SUBSTATE). These lock the NODE_PATH branch added in Task 2:
#   (a) it resolves the SAME check list + node-scoped CHECK_PARAMS the legacy
#       BRANCH path would for the same node, and
#   (b) an UNRESOLVABLE NODE_PATH errors loudly (non-zero exit) instead of
#       silently emitting {"results":[]} — which would let advance.py see zero
#       failing verdicts and proceed as a false success.
# ===========================================================================

def _run_checks_node_path(proto, state_id, evidence, diff, files, node_path, env=None):
    """Invoke run-checks.py with NODE_PATH set (no BRANCH/SUBSTATE).
    Returns (parsed_dict_or_None, returncode)."""
    e = dict(env or os.environ)
    e["NODE_PATH"] = node_path
    # Ensure no legacy coords leak in from the ambient env.
    e.pop("BRANCH", None)
    e.pop("SUBSTATE", None)
    stdout, stderr, rc = run_engine(
        "run-checks.py", proto, state_id, evidence, diff, files, env=e,
    )
    parsed = None
    try:
        parsed = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    return parsed, rc, stderr


# 22
def test_node_path_review_grumpy_resolves_same_checks_as_legacy():
    """NODE_PATH=review.grumpy resolves the grumpy fanout leg's 3 checks — the
    SAME list the legacy branch='grumpy' path resolves (assertion 15)."""
    out, rc, _ = _run_checks_node_path(
        GRUMPY_PROTO, "review.grumpy", EV_COMPLETE, DIFF_PR1, FILES_PR1,
        node_path="review.grumpy",
    )
    assert rc == 0
    assert out is not None
    legacy = run_checks(GRUMPY_PROTO, "review", EV_COMPLETE, DIFF_PR1, FILES_PR1,
                        branch="grumpy")
    assert [r["check"] for r in out["results"]] == [r["check"] for r in legacy["results"]]
    assert len(out["results"]) == 3


# 23
def test_node_path_review_security_resolves_security_leg_checks():
    """NODE_PATH=review.security resolves the security leg's 2 checks (no
    rubric-coverage) — matching the legacy branch='security' path (assertion 16)."""
    out, rc, _ = _run_checks_node_path(
        GRUMPY_PROTO, "review.security", EV_COMPLETE, DIFF_PR1, FILES_PR1,
        node_path="review.security",
    )
    assert rc == 0
    assert len(out["results"]) == 2
    assert "rubric-coverage" not in {r["check"] for r in out["results"]}


# 24
def test_node_path_forwards_node_scoped_params(tmp_path):
    """NODE_PATH forwards the leaf node's node-scoped params as CHECK_PARAMS — the
    SAME params the legacy branch path resolves. Built on a real fanout protocol
    (kind:'fanout') with an echo-params.sh leg check so we can read CHECK_PARAMS
    back out of the verdict feedback."""
    checks_dir = tmp_path / "checks"
    checks_dir.mkdir()
    echo_script = checks_dir / "echo-params.sh"
    echo_script.write_text(
        '#!/usr/bin/env bash\n'
        'jq -nc --arg f "${CHECK_PARAMS:-MISSING}" \'{check:"echo-params",pass:true,feedback:$f}\'\n'
    )
    echo_script.chmod(echo_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    proto = tmp_path / "protocol.json"
    proto.write_text(json.dumps({
        "name": "p",
        "states": [{
            "id": "review",
            "kind": "fanout",
            "params": {"categories": ["state-default"]},
            "branches": [{
                "id": "bx",
                "params": {"categories": ["only-b"]},
                "checks": [{"run": "echo-params"}],
            }],
        }],
    }))
    out, rc, _ = _run_checks_node_path(
        proto, "review.bx", EV_COMPLETE, DIFF_PR1, FILES_PR1, node_path="review.bx",
    )
    assert rc == 0
    feedback_parsed = json.loads(out["results"][0]["feedback"])
    assert feedback_parsed["categories"] == ["only-b"]


# 25
def test_node_path_unresolvable_errors_not_silent_empty():
    """A NODE_PATH that does not resolve to a node MUST error (non-zero exit), NOT
    silently print {"results":[]}. Zero verdicts would make advance.py see no
    failing checks and proceed — a dangerous false success."""
    out, rc, stderr = _run_checks_node_path(
        GRUMPY_PROTO, "review.bogus", EV_COMPLETE, DIFF_PR1, FILES_PR1,
        node_path="review.bogus",
    )
    assert rc != 0, f"expected non-zero exit for unresolvable NODE_PATH, got rc={rc}, out={out}"
    # And it must NOT have printed an empty-but-valid verdict set on stdout.
    assert not (out and out.get("results") == []), \
        "unresolvable NODE_PATH silently produced empty verdicts"
    assert "does not resolve" in stderr
