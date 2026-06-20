"""Port of tests/test-status-comment.sh — unit tests for render_fanout_status_body.

Pure renderer: reads branch state files from a directory, returns the combined
PR-comment body string.  We import lib directly and call
render_fanout_status_body(dir_, pid, instance, proto) — the same function the
CLI `python3 lib.py render-fanout-status-body ...` calls — for fast, clean tests
with no subprocess overhead.

The bash suite uses `seed_branch` (writes a minimal JSON state file) and then
calls the CLI via subprocess.  We replicate the same state files via a helper
that writes identical JSON, then call the function directly.

Bash assertion → pytest mapping
--------------------------------
Scenario A: both branches present, grumpy passed iter 1, security failed iter 1 → in-progress:
  1.  check "render: grumpy section present"
      grep -q "**grumpy**"
      → test_render_grumpy_section_present
  2.  check "render: security section present"
      grep -q "**security**"
      → test_render_security_section_present
  3.  check "render: passed checklist line"
      grep -q "iteration 1/3 — all checks passed"
      → test_render_passed_checklist_line
  4.  check "render: failed checklist line w/ fb"
      grep -q "iteration 1/3 — sec: bad anchor"
      → test_render_failed_checklist_line_with_feedback
  5.  check "render: tree/ link, not blob"
      grep -q "tree/agentic-state/fanout-mini/pr-80" and not grep "blob"
      → test_render_tree_link_not_blob
  6.  check "render: link has no .yaml suffix"
      not grep -q "pr-80.yaml"
      → test_render_link_has_no_yaml_suffix
  7.  check "render: in-progress headline"
      grep -q "Review in progress"
      → test_render_in_progress_headline

Scenario B: both done → complete headline:
  8.  check "render: complete headline"
      grep -q "Review complete"
      → test_render_complete_headline

Scenario C: done + failed → incomplete headline:
  9.  check "render: incomplete headline"
      grep -q "Review incomplete"
      → test_render_incomplete_headline

Scenario D: only grumpy seeded (partial), empty history:
  10. check "render: missing branch file → _pending_"
      grep -q "_pending_"
      → test_render_missing_branch_pending
  11. check "render: empty history → _no iterations yet_"
      grep -q "_no iterations yet_"
      → test_render_empty_history_no_iterations_yet
"""

import json
import os
import pathlib
import sys

import pytest

# Direct import of lib — same pattern as test_correlation.py.
ENGINE = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTO = ROOT / "tests/fixtures/fanout-mini/protocol.json"

# The renderer reads GITHUB_REPOSITORY from the environment to build state links.
os.environ.setdefault("GITHUB_REPOSITORY", "golivax/agentic-protocol-poc")


# ---------------------------------------------------------------------------
# Helper: mirror the bash seed_branch function
# ---------------------------------------------------------------------------

def seed_branch(base_dir, instance, branch, state, history):
    """Write a JSON state file that render_fanout_status_body can read.

    Mirrors the bash seed_branch():
        jq -n --arg inst $inst --arg st $st --argjson h $hist \
          '{protocol:"fanout-mini", instance:$inst, state:$st, iteration:1, gates:{}, history:$h}' \
          > "$d/fanout-mini/$inst/$b.yaml"

    JSON is valid YAML, so we write JSON directly (identical to what the bash does).
    """
    d = pathlib.Path(base_dir) / "fanout-mini" / instance
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "protocol": "fanout-mini",
        "instance": instance,
        "state": state,
        "iteration": 1,
        "gates": {},
        "history": history,
    }
    (d / f"{branch}.yaml").write_text(json.dumps(data))


def render(base_dir, instance):
    """Call render_fanout_status_body and return the body string."""
    return lib.render_fanout_status_body(
        str(base_dir), "fanout-mini", instance, str(PROTO)
    )


# ---------------------------------------------------------------------------
# Scenario A: both branches present; grumpy passed, security failed (in-progress)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def scenario_a(tmp_path_factory):
    d = tmp_path_factory.mktemp("sc_a")
    seed_branch(d, "pr-80", "grumpy",   "review", [{"iteration": 1, "feedback": ""}])
    seed_branch(d, "pr-80", "security", "review", [{"iteration": 1, "feedback": "sec: bad anchor"}])
    return render(d, "pr-80")


def test_render_grumpy_section_present(scenario_a):
    """Bash assertion 1: grumpy section present — grep "**grumpy**"."""
    assert "**grumpy**" in scenario_a


def test_render_security_section_present(scenario_a):
    """Bash assertion 2: security section present — grep "**security**"."""
    assert "**security**" in scenario_a


def test_render_passed_checklist_line(scenario_a):
    """Bash assertion 3: passed checklist line — "iteration 1/3 — all checks passed"."""
    assert "iteration 1/3 — all checks passed" in scenario_a


def test_render_failed_checklist_line_with_feedback(scenario_a):
    """Bash assertion 4: failed checklist line with feedback — "iteration 1/3 — sec: bad anchor"."""
    assert "iteration 1/3 — sec: bad anchor" in scenario_a


def test_render_tree_link_not_blob(scenario_a):
    """Bash assertion 5: tree/ link present and no blob link."""
    assert "tree/agentic-state/fanout-mini/pr-80" in scenario_a
    assert "blob" not in scenario_a


def test_render_link_has_no_yaml_suffix(scenario_a):
    """Bash assertion 6: link has no .yaml suffix."""
    assert "pr-80.yaml" not in scenario_a


def test_render_in_progress_headline(scenario_a):
    """Bash assertion 7: in-progress headline — "Review in progress"."""
    assert "Review in progress" in scenario_a


# ---------------------------------------------------------------------------
# Scenario B: both done → complete headline
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def scenario_b(tmp_path_factory):
    d = tmp_path_factory.mktemp("sc_b")
    seed_branch(d, "pr-81", "grumpy",   "done", [{"iteration": 1, "feedback": ""}])
    seed_branch(d, "pr-81", "security", "done", [{"iteration": 1, "feedback": ""}])
    return render(d, "pr-81")


def test_render_complete_headline(scenario_b):
    """Bash assertion 8: complete headline — "Review complete"."""
    assert "Review complete" in scenario_b


# ---------------------------------------------------------------------------
# Scenario C: done + failed → incomplete headline
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def scenario_c(tmp_path_factory):
    d = tmp_path_factory.mktemp("sc_c")
    seed_branch(d, "pr-82", "grumpy",   "done",   [{"iteration": 1, "feedback": ""}])
    seed_branch(d, "pr-82", "security", "failed", [{"iteration": 3, "feedback": "exhausted"}])
    return render(d, "pr-82")


def test_render_incomplete_headline(scenario_c):
    """Bash assertion 9: incomplete headline — "Review incomplete"."""
    assert "Review incomplete" in scenario_c


# ---------------------------------------------------------------------------
# Scenario D: only grumpy seeded (partial/early); security file absent, empty history
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def scenario_d(tmp_path_factory):
    d = tmp_path_factory.mktemp("sc_d")
    seed_branch(d, "pr-83", "grumpy", "review", [])  # empty history
    # security branch file is intentionally NOT written
    return render(d, "pr-83")


def test_render_missing_branch_pending(scenario_d):
    """Bash assertion 10: missing branch file → _pending_."""
    assert "_pending_" in scenario_d


def test_render_empty_history_no_iterations_yet(scenario_d):
    """Bash assertion 11: empty history → _no iterations yet_."""
    assert "_no iterations yet_" in scenario_d
