"""test_cap_restart.py — Task 13: Restart/reset capability via the unified engine.

Proves that a second `start` mid-pipeline (the `/review` re-trigger pattern) driven
via the unified NODE_PATH path (enter_root) correctly:

  1. Wipes all stale prior-run leg/state files from the instance directory.
  2. Writes a fresh _instance.yaml: phase=<first>, new head_sha, no joined/overrides/halted.
  3. Drops `status_comment_id` (so a new comment is created) and calls
     lib.finalize_superseded_comment (ENGINE_LOCAL → logs supersede).
  4. Calls lib.remove_pr_label for the prior run's phase_label (ENGINE_LOCAL → logs remove-label).

The test uses the `simple-fanout` fixture (enter_root path, fanout with legs f.a
and f.b) because it exercises the unified engine and produces leg files that the
second start must wipe.
"""
import json
import subprocess
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / "tests/fixtures/simple-fanout/protocol.json"
NEXT = ENG / "next.py"

PID = "simple-fanout"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _yaml(p):
    return yaml.safe_load(open(p))


def _dump_yaml(p, d):
    with open(p, "w") as fh:
        yaml.safe_dump(d, fh)


def _reclone(engine_env, tmp_path, tag):
    """Re-clone the state branch from the bare origin."""
    d = tmp_path / f"rc-{tag}"
    subprocess.run(
        ["git", "clone", "-q", "-b", "agentic-state",
         engine_env["STATE_REMOTE"], str(d)],
        check=True,
    )
    return d / PID / "pr-1"


def _git_push_all(work_dir, msg):
    subprocess.run(["git", "-C", str(work_dir), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work_dir), "-c", "user.name=t",
                    "-c", "user.email=t@t", "commit", "-qm", msg], check=True)
    subprocess.run(["git", "-C", str(work_dir), "push", "-q", "origin",
                    "agentic-state"], check=True)


def _run(script, *args, env, **env_extra):
    e = dict(env); e.update(env_extra)
    r = subprocess.run(["python3", str(script), *map(str, args)],
                       text=True, capture_output=True, env=e)
    return r


# ---------------------------------------------------------------------------
# Test: restart wipes stale state, abandons old comment, removes old label
# ---------------------------------------------------------------------------

def test_restart_wipes_prior_run_via_enter_root(engine_env, tmp_path):
    """A second `start` on the unified (enter_root) path must wipe stale leg files,
    abandon the prior run's status comment (superseded banner via
    finalize_superseded_comment), remove the prior phase label, and re-seed a fresh
    _instance.yaml with phase=f, no joined/overrides/halted, and the new head_sha."""

    # ----- Step 1: first run — start with old sha -----
    work1 = tmp_path / "state1"
    r1 = _run(NEXT, work1, "pr-1", PROTO, "start", "oldsha", env=engine_env)
    assert r1.returncode == 0, f"first start failed:\n{r1.stderr}"

    # simple-fanout seeds a.yaml, b.yaml, _instance.yaml
    inst_dir = str(work1) + f"/{PID}/pr-1"
    assert pathlib.Path(inst_dir + "/a.yaml").is_file(), "a.yaml must exist after first start"
    assert pathlib.Path(inst_dir + "/b.yaml").is_file(), "b.yaml must exist after first start"
    assert pathlib.Path(inst_dir + "/_instance.yaml").is_file()

    # ----- Step 2: simulate mid-pipeline state — inject status_comment_id, -----
    #   joined=True, a stale extra leg file, and a phase_label.
    inst = _yaml(inst_dir + "/_instance.yaml")
    # Inject markers that must NOT survive the restart.
    inst["joined"] = True
    inst["overrides"] = [{"actor": "x", "reason": "override"}]
    inst["halted"] = {"reason": "blocked"}
    inst["status_comment_id"] = 77777
    # phase_label is set by ensure_phase_label → its value is what remove_pr_label receives.
    # After the first start, _instance.yaml already has phase_label set (from ensure_phase_label
    # in enter_root). We want to assert remove_pr_label is invoked with SOMETHING non-empty,
    # so we set an explicit value here to make the assertion deterministic.
    inst["phase_label"] = "old-phase-label"
    _dump_yaml(inst_dir + "/_instance.yaml", inst)

    # Add a stale extra leg file that wasn't in the original start (simulating a
    # leg that was seeded in a later continuation step, e.g. a nested substate).
    _dump_yaml(inst_dir + "/stale-extra.yaml", {
        "protocol": PID, "instance": "pr-1", "state": "stale", "iteration": 3
    })

    # Push the injected state so the second start reads it on clone.
    _git_push_all(work1, "simulate prior-run stale state")

    # ----- Step 3: second start with a NEW head sha — fresh clone -----
    work2 = tmp_path / "state2"
    r2 = _run(NEXT, work2, "pr-1", PROTO, "start", "newsha", env=engine_env)
    assert r2.returncode == 0, f"second start failed:\n{r2.stderr}"

    inst_dir2 = str(work2) + f"/{PID}/pr-1"

    # ----- Assert 1: stale leg files are gone -----
    # The original leg files (a.yaml, b.yaml) must be wiped and re-seeded fresh
    # by enter_node; the extra stale file must be gone entirely.
    assert not pathlib.Path(inst_dir2 + "/stale-extra.yaml").is_file(), (
        "stale-extra.yaml must be wiped by _reset_wipe")

    # Re-clone from origin for the authoritative view of the pushed state.
    fdir = _reclone(engine_env, tmp_path, "after-restart")

    # Stale-extra must not be in the origin either.
    assert not (fdir / "stale-extra.yaml").is_file(), (
        "stale-extra.yaml must not be in origin after restart")

    # ----- Assert 2: _instance.yaml is fresh -----
    inst2 = _yaml(str(fdir / "_instance.yaml"))
    assert inst2.get("phase") == "f", (
        f"phase must be reset to 'f' (first fanout), got: {inst2}")
    assert inst2.get("head_sha") == "newsha", (
        f"head_sha must be the new sha, got: {inst2}")
    assert inst2.get("joined") is False or not inst2.get("joined"), (
        f"joined must be cleared, got: {inst2}")
    assert not inst2.get("overrides"), (
        f"overrides must be cleared, got: {inst2}")
    assert not inst2.get("halted"), (
        f"halted must be cleared, got: {inst2}")

    # ----- Assert 3: status_comment_id was dropped -----
    assert "status_comment_id" not in inst2, (
        f"status_comment_id must be dropped on restart, got: {inst2}")

    # ----- Assert 4: finalize_superseded_comment was invoked (ENGINE_LOCAL log) -----
    assert "[ENGINE_LOCAL] supersede comment 77777" in r2.stderr, (
        f"expected supersede log for cid=77777 in stderr:\n{r2.stderr}")
    assert "Superseded" in r2.stderr, (
        f"expected 'Superseded' banner in supersede log:\n{r2.stderr}")

    # ----- Assert 5: remove_pr_label was invoked for the prior phase label -----
    assert "[ENGINE_LOCAL] remove-label pr=1: old-phase-label" in r2.stderr, (
        f"expected remove-label log for 'old-phase-label' in stderr:\n{r2.stderr}")

    # ----- Assert 6: the action emitted is run-fanout (fresh first phase) -----
    action = json.loads(r2.stdout)
    assert action["action"] == "run-fanout", (
        f"second start must emit run-fanout, got: {action}")
