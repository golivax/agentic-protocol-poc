"""Unit tests for lib.is_terminal_state() — the terminal-state predicate."""
import pathlib
import sys

ENGINE = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def test_done_is_terminal():
    assert lib.is_terminal_state("done") is True


def test_failed_is_terminal():
    assert lib.is_terminal_state("failed") is True


def test_blocked_is_terminal():
    assert lib.is_terminal_state("blocked") is True


def test_design_is_not_terminal():
    assert lib.is_terminal_state("design") is False


def test_preflight_is_not_terminal():
    assert lib.is_terminal_state("preflight") is False


def test_iterate_is_not_terminal():
    assert lib.is_terminal_state("iterate") is False


def test_empty_string_is_not_terminal():
    assert lib.is_terminal_state("") is False


def test_setup_is_not_terminal():
    # "setup" is a lifecycle label, not a stopping state.
    assert lib.is_terminal_state("setup") is False


def test_none_returns_false_without_raising():
    assert lib.is_terminal_state(None) is False


def test_integer_returns_false_without_raising():
    assert lib.is_terminal_state(0) is False


def test_list_returns_false_without_raising():
    assert lib.is_terminal_state([]) is False
