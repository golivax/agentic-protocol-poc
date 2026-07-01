"""test_two_fanout_publish.py — Regression test for the non-first-fanout publish bug.

Bug (confirmed live): advance.py's "flat nested-fanout child leg" block (guarded by
_paths.is_fanout(proto, parent_path(tree_path))) does an early return BEFORE calling
run_publish_hook, so no leg in ANY fanout ever publishes via that code path.

Additionally, run_publish_hook resolves the branch entry using branch=tree_path[-2]
(the fanout's STATE id), which never matches b["id"] (the leg id), so even the
legacy "remaining done" block at the bottom of advance.py would resolve to action=None.

This test uses the tests/fixtures/two-fanout-publish/ fixture which has:
  - Two sequential fanouts (f1 → j1 → f2 → j2)
  - f2's legs declare "publish": "mark-published"
  - publish/mark-published.py writes a marker file to $MARKER_DIR/<instance>-<branch>.published

The test drives f2.a2 (the first leg of the SECOND fanout) to done through advance.py
and asserts the marker file was written — proving the publish hook ran.

Before fix: marker file absent → test FAILS.
After fix:  marker file present → test PASSES.
"""
import json
import os
import subprocess
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / "tests/fixtures/two-fanout-publish/protocol.json"
NEXT = ENG / "next.py"
ADVANCE = ENG / "advance.py"

PID = "two-fanout-publish"


def _yaml(p):
    return yaml.safe_load(open(p))


def _run(script, *args, env, **env_extra):
    e = dict(env)
    e.update(env_extra)
    r = subprocess.run(
        ["python3", str(script), *map(str, args)],
        text=True,
        capture_output=True,
        env=e,
    )
    return r


def _pass_verdicts(tmp_path, tag="pass"):
    v = tmp_path / f"v-{tag}.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))
    ev = tmp_path / f"ev-{tag}.json"
    ev.write_text("{}")
    return v, ev


def test_second_fanout_leg_runs_publish_hook(engine_env, tmp_path):
    """Advancing a leg of the SECOND fanout (f2.a2) with 'publish' declared must
    invoke the publish hook and write the marker file.

    This test FAILS before the fix (marker absent) and PASSES after the fix."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "abc123"
    base["AGENT_RUN_ID"] = "r1"
    base["PR"] = "pr-1"

    marker_dir = tmp_path / "markers"
    marker_dir.mkdir()
    # Injected into advance.py's subprocess environment so the publish hook can write the marker.
    base["MARKER_DIR"] = str(marker_dir)

    v, ev = _pass_verdicts(tmp_path)

    # Step 1: seed the protocol (start → run-fanout for f1)
    r1 = _run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "abc123", env=base)
    assert r1.returncode == 0, f"next.py start failed:\n{r1.stderr}"
    act = json.loads(r1.stdout)
    assert act["action"] == "run-fanout", f"expected run-fanout, got: {act}"
    # f1's legs are f1.a1 and f1.b1
    leg_paths = {lg["path"] for lg in act.get("legs", [])}
    assert leg_paths == {"f1.a1", "f1.b1"}, f"unexpected f1 leg paths: {leg_paths}"

    # Step 2: advance both f1 legs to done (no publish on f1 — just need the join to fire)
    r2a = _run(ADVANCE, tmp_path / "s2a", "pr-1", PROTO, v, ev, env=base,
               NODE_PATH="f1.a1")
    assert r2a.returncode == 0, f"advance f1.a1 failed:\n{r2a.stderr}"

    r2b = _run(ADVANCE, tmp_path / "s2b", "pr-1", PROTO, v, ev, env=base,
               NODE_PATH="f1.b1")
    assert r2b.returncode == 0, f"advance f1.b1 failed:\n{r2b.stderr}"

    # Step 3: join f1 — both legs done → finalize and advance to f2
    r3 = _run(ENG / "join.py", tmp_path / "s3", "pr-1", PROTO, env=base)
    assert r3.returncode == 0, f"join f1 failed:\n{r3.stderr}"

    # After the join, next.py continue should seed f2's legs.
    # We drive the continue manually (simulating the protocol-continue dispatch).
    r3c = _run(NEXT, tmp_path / "s3c", "pr-1", PROTO, "continue", "abc123", env=base,
               NODE_PATH="f2")
    assert r3c.returncode == 0, f"next.py continue f2 failed:\n{r3c.stderr}"
    act3c = json.loads(r3c.stdout)
    assert act3c["action"] == "run-fanout", f"expected run-fanout for f2, got: {act3c}"
    leg_paths_f2 = {lg["path"] for lg in act3c.get("legs", [])}
    assert leg_paths_f2 == {"f2.a2", "f2.b2"}, f"unexpected f2 leg paths: {leg_paths_f2}"

    # Step 4: advance f2.a2 — this is the leg that should trigger the publish hook.
    # Before the fix: marker file is NOT written (early return skips run_publish_hook).
    # After the fix:  marker file IS written (publish hook called before early return).
    r4 = _run(ADVANCE, tmp_path / "s4", "pr-1", PROTO, v, ev, env=base,
              NODE_PATH="f2.a2", BRANCH="a2")
    assert r4.returncode == 0, f"advance f2.a2 failed:\n{r4.stderr}"

    # The marker file is the observable proof that the publish hook ran.
    marker = marker_dir / "pr-1-a2.published"
    assert marker.is_file(), (
        f"Publish hook did NOT run for f2.a2 — marker file not found at {marker}.\n"
        f"advance stderr:\n{r4.stderr}\n"
        f"This is the regression: the flat-nested-fanout-child block early-returns "
        f"before calling run_publish_hook."
    )
    content = marker.read_text()
    assert "branch=a2" in content, f"Unexpected marker content: {content!r}"


def test_first_fanout_leg_without_publish_is_unchanged(engine_env, tmp_path):
    """Advancing a leg of the FIRST fanout (f1.a1) which has NO 'publish' declared
    must NOT write any marker file — behavior is unchanged (no regression)."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "abc123"
    base["AGENT_RUN_ID"] = "r1"
    base["PR"] = "pr-1"

    marker_dir = tmp_path / "markers"
    marker_dir.mkdir()
    base["MARKER_DIR"] = str(marker_dir)

    v, ev = _pass_verdicts(tmp_path)

    # Seed the protocol
    r1 = _run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "abc123", env=base)
    assert r1.returncode == 0, r1.stderr

    # Advance f1.a1 — no publish declared on f1 legs
    r2 = _run(ADVANCE, tmp_path / "s2", "pr-1", PROTO, v, ev, env=base,
              NODE_PATH="f1.a1", BRANCH="a1")
    assert r2.returncode == 0, f"advance f1.a1 failed:\n{r2.stderr}"

    # No marker should be written for f1.a1 (no "publish" on f1 legs)
    markers = list(marker_dir.iterdir())
    assert not markers, (
        f"No marker should be written for f1.a1 (no publish declared), "
        f"but found: {markers}"
    )
