"""Merge-state (`kind:merge`) join-mode tests.

The append-hook + run_merge_hook unit tests and the full subpipeline-mini
walk-with-merge that once lived here moved to test_recover_mental_model.py (which
drives the real recover-mental-model protocol: flat `summary` ∥ sub-pipeline
`rationale` → join → combine merge → done). What remains are the join MODE
selectors (next is an agent vs. next is `done`), driven over inline flat
protocols via the unified NODE_PATH coordinate.
"""
import json, subprocess, sys
from pathlib import Path
from conftest import ENGINE, run_engine, read_state_yaml  # noqa: F401
sys.path.insert(0, str(ENGINE))


def _make_flat_protocol(tmp_path: Path, join_next: str, extra_states=None) -> Path:
    """Build a minimal flat two-branch fanout protocol with no checks.

    Both branches (A, B) are flat (no sub-pipeline, no gates), so driving them
    to done requires only a single advance.py call each.  The join state's
    `next` is set to ``join_next``.  Any additional states (e.g. an agent
    combine) are appended via ``extra_states``.
    """
    proto = {
        "name": "flat-mini",
        "version": "0.1.0",
        "triggers": [],
        "states": [
            {
                "id": "review",
                "kind": "fanout",
                "branches": [
                    {
                        "id": "A",
                        "workflow": "a-agent",
                        "evidence": "e.json",
                        "max_iterations": 2,
                        "checks": [],
                        "publish": "noop",
                    },
                    {
                        "id": "B",
                        "workflow": "b-agent",
                        "evidence": "e.json",
                        "max_iterations": 2,
                        "checks": [],
                        "publish": "noop",
                    },
                ],
                "next": "join",
            },
            {"id": "join", "kind": "join", "of": "review", "next": join_next},
        ],
    }
    if extra_states:
        proto["states"].extend(extra_states)
    pf = tmp_path / "proto.json"
    pf.write_text(json.dumps(proto))
    return pf


def test_join_dispatches_agent_combine(tmp_path, engine_env):
    """Mode 2: join.next is a kind:'agent' state → join advances cursor to it + dispatches.

    Drive sequence (flat legs — no sub-pipeline, no gate, no checks):
      1. next.py start          → seeds _instance.yaml + branch state files
      2. advance.py BRANCH=A    → drives A to done (flat, no checks)
      3. advance.py BRANCH=B    → drives B to done (flat, no checks)
      4. join.py                → all done → should advance phase to combine2
    Assert: _instance.yaml.phase == "combine2" (agent-combine cursor advanced)
    """
    # Protocol: join.next → combine2 (kind:agent)
    pf = _make_flat_protocol(
        tmp_path,
        join_next="combine2",
        extra_states=[
            {
                "id": "combine2",
                "kind": "agent",
                "workflow": "c-agent",
                "evidence": "e.json",
                "max_iterations": 1,
                "inputs": [{"from": "A", "as": "a"}, {"from": "B", "as": "b"}],
                "checks": [],
                "next": "done",
            }
        ],
    )

    # All-pass verdicts: one synthetic passing result drives decide() to "done".
    # (Empty results → decide() returns "iterate"; a single pass → "done".)
    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))

    # Minimal evidence file (advance reads it for persist_output; content irrelevant).
    ev = tmp_path / "e.json"
    ev.write_text(json.dumps({"summary": "ok"}))

    # Step 1: seed the instance + branch state files.
    run_engine("next.py", tmp_path / "dir-next", "pr-1", pf, "start", "abc123", env=engine_env)

    # Step 2: drive A to done.
    # PHASE=review is required because the protocol is multiphase (review+combine2),
    # so advance.py writes review.A.yaml which join.py will find via phase_for_path.
    e_a = dict(engine_env)
    e_a["NODE_PATH"] = "review.A"
    e_a["PR_HEAD_SHA"] = "abc123"
    e_a["AGENT_RUN_ID"] = "r1"
    out, err, rc = run_engine(
        "advance.py", tmp_path / "dir-adv-a", "pr-1", pf, passv, ev, env=e_a
    )
    assert rc == 0, f"advance A failed:\n{err}"

    # Step 3: drive B to done.
    e_b = dict(engine_env)
    e_b["NODE_PATH"] = "review.B"
    e_b["PR_HEAD_SHA"] = "abc123"
    e_b["AGENT_RUN_ID"] = "r2"
    out, err, rc = run_engine(
        "advance.py", tmp_path / "dir-adv-b", "pr-1", pf, passv, ev, env=e_b
    )
    assert rc == 0, f"advance B failed:\n{err}"

    # Step 4: run join — all branches done, join.next is kind:agent → mode 2.
    ej = dict(engine_env)
    ej["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("join.py", tmp_path / "dir-join", "pr-1", pf, env=ej)
    assert rc == 0, f"join failed:\n{err}"

    # Assert the DISPATCH contract: join advances the cursor to the agent-combine
    # state AND fires a protocol-continue carrying its path (the unified .next
    # dispatch). next.py's continue agent arm picks that up to run the combine agent.
    combined = out + err
    assert "event_type=protocol-continue" in combined, (
        f"Expected join to dispatch protocol-continue, got:\n{combined}"
    )
    assert "client_payload[path]=combine2" in combined, (
        f"Expected dispatch path=combine2, got:\n{combined}"
    )

    # Assert: instance cursor advanced to the agent-combine state.
    work = tmp_path / "work-m2"
    subprocess.run(
        ["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True
    )
    inst = read_state_yaml(work / "flat-mini/pr-1/_instance.yaml")
    assert inst.get("phase") == "combine2", (
        f"Expected phase='combine2', got phase={inst.get('phase')!r}; "
        f"joined={inst.get('joined')!r}"
    )


def test_join_mode3_publish_only_finalizes(tmp_path, engine_env):
    """Mode 3 regression: join.next == done → plain finalize (joined=True, no phase advance).

    Drive sequence (flat legs — no sub-pipeline, no gate, no checks):
      1. next.py start          → seeds _instance.yaml + branch state files
      2. advance.py BRANCH=A    → drives A to done (one synthetic pass verdict)
      3. advance.py BRANCH=B    → drives B to done (one synthetic pass verdict)
      4. join.py                → all done → should plain-finalize
    Assert: joined == True AND phase is None or absent (no post-join cursor advance).
    """
    # Protocol: join.next → done (plain finalize, mode 3)
    pf = _make_flat_protocol(tmp_path, join_next="done")

    # One passing verdict so decide() yields "done" (empty → "iterate").
    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))

    ev = tmp_path / "e.json"
    ev.write_text(json.dumps({"summary": "ok"}))

    # Step 1: seed the instance + branch state files.
    run_engine("next.py", tmp_path / "dir-next", "pr-1", pf, "start", "abc123", env=engine_env)

    # Step 2: drive A to done.
    e_a = dict(engine_env)
    e_a["NODE_PATH"] = "review.A"
    e_a["PR_HEAD_SHA"] = "abc123"
    e_a["AGENT_RUN_ID"] = "r1"
    out, err, rc = run_engine(
        "advance.py", tmp_path / "dir-adv-a", "pr-1", pf, passv, ev, env=e_a
    )
    assert rc == 0, f"advance A failed:\n{err}"

    # Step 3: drive B to done.
    e_b = dict(engine_env)
    e_b["NODE_PATH"] = "review.B"
    e_b["PR_HEAD_SHA"] = "abc123"
    e_b["AGENT_RUN_ID"] = "r2"
    out, err, rc = run_engine(
        "advance.py", tmp_path / "dir-adv-b", "pr-1", pf, passv, ev, env=e_b
    )
    assert rc == 0, f"advance B failed:\n{err}"

    # Step 4: run join — all branches done, join.next == "done" → mode 3 plain finalize.
    ej = dict(engine_env)
    ej["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("join.py", tmp_path / "dir-join", "pr-1", pf, env=ej)
    assert rc == 0, f"join failed:\n{err}"

    # Assert: plain finalize — joined but no post-join phase advance.
    work = tmp_path / "work-m3"
    subprocess.run(
        ["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True
    )
    inst = read_state_yaml(work / "flat-mini/pr-1/_instance.yaml")
    assert inst.get("joined") is True, f"Expected joined=True, got {inst.get('joined')!r}"
    # Phase must not have been advanced to a post-join state.
    assert inst.get("phase") in (None, "review"), (
        f"Expected phase to be None/review (no advance), got {inst.get('phase')!r}"
    )


def test_run_merge_hook_provides_PR_env_from_instance(tmp_path, monkeypatch):
    """Live-found regression: the merge hook posts its combined PR comment via
    lib.post_pr_comment, which reads PR from the env. In the unified engine the
    merge runs from next.py in the PLAN job, which does not set PR — so the hook's
    comment silently no-op'd. run_merge_hook must derive PR from the instance for
    the hook subprocess. With PR unset in the parent env, the hook must still see it."""
    import json as _json
    import pathlib as _pl
    import sys as _sys
    _root = _pl.Path(__file__).resolve().parent.parent
    _sys.path.insert(0, str(_root / ".github/agent-factory/engine"))
    import lib as _lib

    pdir = tmp_path / "proto"
    (pdir / "publish").mkdir(parents=True)
    proto = {
        "name": "merge-pr-probe",
        "states": [
            {"id": "f", "kind": "fanout", "branches": [
                {"id": "a", "workflow": "a-agent"}]},
            {"id": "join-f", "kind": "join", "of": "f", "next": "c"},
            {"id": "c", "kind": "merge", "hook": "echo-pr", "inputs": []},
        ],
    }
    (pdir / "protocol.json").write_text(_json.dumps(proto))
    hook = pdir / "publish" / "echo-pr"
    hook.write_text('#!/usr/bin/env python3\n'
                    'import os, json\n'
                    'print(json.dumps({"conclusion": "success", '
                    '"summary": os.environ.get("PR", "MISSING")}))\n')
    hook.chmod(0o755)

    monkeypatch.delenv("PR", raising=False)
    res = _lib.run_merge_hook(str(tmp_path / "state"), "merge-pr-probe", "pr-77",
                              str(pdir / "protocol.json"), proto["states"][2])
    assert res["summary"] == "77", (
        f"merge hook must receive PR derived from the instance (pr-77 -> 77): {res}"
    )
