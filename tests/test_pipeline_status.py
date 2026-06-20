"""Unit tests for the UNIFIED protocol-level status comment (multi-phase).

render_pipeline_status_body(dir_, pid, instance, proto) renders EVERY phase of a
multi-phase protocol (code-review: preflight agent → review fan-out) into
ONE PR-comment body. This is the regression guard for the three PR #65 bugs:

  (i)   fan-out legs in a multi-phase protocol live at <instance>/<phase>.<branch>.yaml,
        NOT <instance>/<branch>.yaml — the renderer must find them (no false _pending_).
  (ii)  the comment is protocol-level: it shows the preflight phase too, and the
        audit-trail link points at the instance dir.
  (iii) a blocked preflight is rendered as blocked with an /override hint.

Single-phase rendering (render_fanout_status_body) is covered by test_status_comment.py
and must stay byte-identical; this module only exercises the new multi-phase path.
"""
import json
import os
import pathlib
import sys

import pytest
import yaml

ENGINE = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PIPELINE = ROOT / ".github/agent-factory/protocols/code-review/protocol.json"
MULTIGRUMPY = ROOT / ".github/agent-factory/protocols/multi-grumpy/protocol.json"
PID = "code-review"

os.environ.setdefault("GITHUB_REPOSITORY", "golivax/agentic-protocol-poc")


# ---------------------------------------------------------------------------
# Seeders: write the per-phase state files the renderer projects.
# ---------------------------------------------------------------------------

def seed_instance(base, instance, **fields):
    d = pathlib.Path(base) / PID / instance
    d.mkdir(parents=True, exist_ok=True)
    data = {"protocol": PID, "instance": instance, "joined": False}
    data.update(fields)
    (d / "_instance.yaml").write_text(yaml.safe_dump(data))


def seed_phase(base, instance, phase, state, history, branch=None):
    """Write <instance>/<phase>.yaml or <instance>/<phase>.<branch>.yaml."""
    d = pathlib.Path(base) / PID / instance
    d.mkdir(parents=True, exist_ok=True)
    name = f"{phase}.{branch}.yaml" if branch else f"{phase}.yaml"
    (d / name).write_text(yaml.safe_dump({
        "protocol": PID, "instance": instance, "state": state,
        "iteration": 1, "gates": {}, "history": history,
    }))


def render(base, instance):
    return lib.render_pipeline_status_body(str(base), PID, instance, str(PIPELINE))


# ---------------------------------------------------------------------------
# Bug (i): fan-out legs at <phase>.<branch>.yaml are found → no false _pending_
# ---------------------------------------------------------------------------

@pytest.fixture
def pr65_done(tmp_path):
    """The PR #65 shape: preflight blocked→overridden, both review legs done."""
    seed_instance(tmp_path, "pr-65", phase="review",
                  overrides=[{"phase": "preflight", "actor": "golivax", "reason": "hotfix"}])
    # Real PR #65 shape: block-severity spec/plan checks failed (empty feedback).
    seed_phase(tmp_path, "pr-65", "preflight", "failed",
               [{"iteration": 1, "feedback": "",
                 "checks": {"schema-valid": "pass", "spec-present": "fail", "plan-present": "fail"}}])
    seed_phase(tmp_path, "pr-65", "review", "done", [{"iteration": 1, "feedback": ""}], branch="grumpy")
    seed_phase(tmp_path, "pr-65", "review", "done", [{"iteration": 1, "feedback": ""}], branch="security")
    return render(tmp_path, "pr-65")


def test_grumpy_leg_not_pending(pr65_done):
    assert "**review · grumpy**" in pr65_done
    # The grumpy section must show its passed iteration, not _pending_.
    assert "_pending_" not in pr65_done


def test_security_leg_present(pr65_done):
    assert "**review · security**" in pr65_done


def test_done_legs_show_checklist(pr65_done):
    assert pr65_done.count("all checks passed") >= 2  # both legs done


# ---------------------------------------------------------------------------
# Bug (ii): protocol-level — preflight section + instance-dir audit link
# ---------------------------------------------------------------------------

def test_preflight_section_present(pr65_done):
    assert "**preflight**" in pr65_done


def test_audit_link_points_at_instance_dir(pr65_done):
    assert "tree/agentic-state/code-review/pr-65" in pr65_done
    assert "pr-65.yaml" not in pr65_done  # tree/ link, no .yaml suffix


def test_protocol_header(pr65_done):
    assert "**code-review · pr-65**" in pr65_done


def test_overridden_preflight_not_failed_headline(pr65_done):
    # preflight failed but was overridden, both legs done → pipeline reads complete.
    assert "Pipeline complete" in pr65_done
    assert "overridden" in pr65_done


def test_preflight_blocked_checks_named_not_all_passed(pr65_done):
    # The gate's block-severity failures must surface, not a false "all checks passed".
    assert "checks failed: plan-present, spec-present" in pr65_done


# ---------------------------------------------------------------------------
# Bug (iii): a still-blocked preflight renders blocked + /override hint
# ---------------------------------------------------------------------------

@pytest.fixture
def pr_blocked(tmp_path):
    seed_instance(tmp_path, "pr-70", phase="preflight",
                  halted={"phase": "preflight", "reason": "blocked", "sha": "s"})
    seed_phase(tmp_path, "pr-70", "preflight", "failed", [{"iteration": 1, "feedback": ""}])
    # review not seeded yet → legs pending
    return render(tmp_path, "pr-70")


def test_blocked_headline_mentions_override(pr_blocked):
    assert "/override" in pr_blocked
    assert "lock" in pr_blocked.lower() or "block" in pr_blocked.lower()


def test_blocked_legs_pending(pr_blocked):
    # review phase not started → its legs render pending (correct here).
    assert "_pending_" in pr_blocked


# ---------------------------------------------------------------------------
# Initial comment (pipeline just started, only preflight seeded, empty history)
# ---------------------------------------------------------------------------

def test_initial_render_has_audit_link(tmp_path):
    seed_instance(tmp_path, "pr-80", phase="preflight")
    seed_phase(tmp_path, "pr-80", "preflight", "preflight", [])  # no iterations yet
    body = render(tmp_path, "pr-80")
    assert "tree/agentic-state/code-review/pr-80" in body
    assert "no iterations yet" in body
    assert "**review · grumpy**" in body and "_pending_" in body


# ---------------------------------------------------------------------------
# Dispatcher: multiphase → pipeline renderer; single-phase fanout → fanout renderer
# ---------------------------------------------------------------------------

def test_dispatcher_multiphase_uses_pipeline(tmp_path):
    seed_instance(tmp_path, "pr-90", phase="preflight")
    seed_phase(tmp_path, "pr-90", "preflight", "preflight", [])
    body = lib.render_instance_status_body(str(tmp_path), PID, "pr-90", str(PIPELINE))
    assert "**preflight**" in body  # only the pipeline renderer emits a preflight section


def test_ensure_status_comment_noop_for_single_agent(tmp_path, monkeypatch):
    """grumpy (single-agent) has no shared comment → ensure must be a no-op."""
    grumpy = ROOT / "tests/fixtures/single-agent/protocol.json"
    monkeypatch.setenv("ENGINE_LOCAL", "1")
    # Should not raise and should not create an _instance.yaml.
    lib.ensure_status_comment(str(tmp_path), "single-agent", "pr-1", str(grumpy), "1")
    assert not (tmp_path / "single-agent" / "pr-1" / "_instance.yaml").exists()
