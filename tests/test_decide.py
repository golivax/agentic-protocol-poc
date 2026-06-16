"""Unit tests for lib.decide() — the pure process/conclusion fold.

decide(results, iterations_remaining) -> (process, blocking)
  process  ∈ {"done","iterate","failed"} : the process axis (drives the loop)
  blocking : bool                          : did a `block`-severity check fail
Severity comes from each result's "on_fail" (default "iterate" when absent).
"""
import pathlib
import sys

ENGINE = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def r(check, passed, on_fail=None):
    """Build a verdict dict; omit on_fail entirely when None (tests the default)."""
    v = {"check": check, "pass": passed, "feedback": "" if passed else f"{check} failed"}
    if on_fail is not None:
        v["on_fail"] = on_fail
    return v


def test_empty_results_with_room_iterates():
    assert lib.decide([], iterations_remaining=True) == ("iterate", False)


def test_empty_results_without_room_fails():
    assert lib.decide([], iterations_remaining=False) == ("failed", False)


def test_all_pass_is_done():
    results = [r("schema-valid", True), r("rubric-coverage", True)]
    assert lib.decide(results, iterations_remaining=True) == ("done", False)


def test_iterate_fail_with_room_iterates():
    results = [r("schema-valid", True), r("rubric-coverage", False, "iterate")]
    assert lib.decide(results, iterations_remaining=True) == ("iterate", False)


def test_iterate_fail_without_room_fails():
    results = [r("rubric-coverage", False, "iterate")]
    assert lib.decide(results, iterations_remaining=False) == ("failed", False)


def test_block_fail_does_not_iterate_but_blocks():
    results = [r("schema-valid", True), r("spec-present", False, "block")]
    assert lib.decide(results, iterations_remaining=True) == ("done", True)


def test_advisory_fail_is_ignored():
    results = [r("schema-valid", True), r("docs-updated", False, "advisory")]
    assert lib.decide(results, iterations_remaining=True) == ("done", False)


def test_block_and_advisory_without_iterate_blocks_but_is_done():
    # No iterate-severity failure → process is done regardless of room; the
    # block fail sets blocking, the advisory fail is ignored.
    results = [r("schema-valid", True),
               r("spec-present", False, "block"),
               r("docs-updated", False, "advisory")]
    assert lib.decide(results, iterations_remaining=True) == ("done", True)
    assert lib.decide(results, iterations_remaining=False) == ("done", True)


def test_iterate_and_block_with_room():
    results = [r("schema-valid", False, "iterate"), r("spec-present", False, "block")]
    assert lib.decide(results, iterations_remaining=True) == ("iterate", True)


def test_iterate_and_block_without_room():
    results = [r("schema-valid", False, "iterate"), r("spec-present", False, "block")]
    assert lib.decide(results, iterations_remaining=False) == ("failed", True)


def test_missing_on_fail_defaults_to_iterate():
    # No on_fail key at all → treated as iterate (v1 back-compat).
    results = [r("legacy-check", False)]
    assert lib.decide(results, iterations_remaining=True) == ("iterate", False)
    assert lib.decide(results, iterations_remaining=False) == ("failed", False)
