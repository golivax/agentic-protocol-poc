"""Port of tests/test-correlation.sh — unit tests for match_run_by_cid in lib.py.

Pure: no git, no subprocess, no fixtures needed. We import lib directly and call
match_run_by_cid(runs_json, cid) for each case.

Bash assertion → pytest mapping
--------------------------------
1. "collision: picks the run carrying our cid (not the newest)"
       RUNS, cid="42-1-grumpy" → "111"
2. "collision: picks the other cid correctly"
       RUNS, cid="99-1-grumpy" → "222"
3. "no match → empty"
       RUNS, cid="7-1-grumpy"  → ""
4. "empty list → empty"
       "[]", cid="42-1-grumpy" → ""
5. "delimiter: prefix cid does not false-match"
       PFX,  cid="42-1-grumpy" → "2"
6. "null displayTitle does not abort the match"
       NULLT, cid="42-1-grumpy" → "8"
"""

import json
import sys
import pathlib

import pytest

# Direct import — cleaner and faster than a subprocess call.
ENGINE = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402  (import after sys.path manipulation)

# ---------------------------------------------------------------------------
# Shared test data (mirrors the bash variables exactly)
# ---------------------------------------------------------------------------

# Two concurrent runs of the SAME workflow, different cids.
# databaseId 222 is the newest (listed first) — matcher must pick by cid, not recency.
RUNS = json.dumps([
    {"databaseId": 222, "displayTitle": "Grumpy Agent · cid:[99-1-grumpy]"},
    {"databaseId": 111, "displayTitle": "Grumpy Agent · cid:[42-1-grumpy]"},
])

# Delimiter-safety fixture: databaseId 1's cid is a PREFIX of the target cid.
PFX = json.dumps([
    {"databaseId": 1, "displayTitle": "x cid:[42-1-grumpy2]"},
    {"databaseId": 2, "displayTitle": "x cid:[42-1-grumpy]"},
])

# Null displayTitle fixture.
NULLT = json.dumps([
    {"databaseId": 7, "displayTitle": None},
    {"databaseId": 8, "displayTitle": "Grumpy Agent · cid:[42-1-grumpy]"},
])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_collision_picks_by_cid_not_recency():
    """Bash assertion 1: collision: picks the run carrying our cid (not the newest)."""
    assert lib.match_run_by_cid(RUNS, "42-1-grumpy") == "111"


def test_collision_picks_other_cid():
    """Bash assertion 2: collision: picks the other cid correctly."""
    assert lib.match_run_by_cid(RUNS, "99-1-grumpy") == "222"


def test_no_match_returns_empty():
    """Bash assertion 3: no match → empty."""
    assert lib.match_run_by_cid(RUNS, "7-1-grumpy") == ""


def test_empty_run_list_returns_empty():
    """Bash assertion 4: empty list → empty."""
    assert lib.match_run_by_cid("[]", "42-1-grumpy") == ""


def test_delimiter_prefix_does_not_false_match():
    """Bash assertion 5: delimiter: prefix cid does not false-match."""
    assert lib.match_run_by_cid(PFX, "42-1-grumpy") == "2"


def test_null_display_title_does_not_abort_match():
    """Bash assertion 6: null displayTitle does not abort the match."""
    assert lib.match_run_by_cid(NULLT, "42-1-grumpy") == "8"


def test_pr_from_instance_handles_pr_issue_and_passthrough():
    import sys, pathlib
    eng = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
    sys.path.insert(0, str(eng))
    import lib
    assert lib.pr_from_instance("pr-5") == "5"
    assert lib.pr_from_instance("issue-42") == "42"
    assert lib.pr_from_instance("ref-feat-x") == "ref-feat-x"
    assert lib.pr_from_instance("ui-abc") == "ui-abc"
