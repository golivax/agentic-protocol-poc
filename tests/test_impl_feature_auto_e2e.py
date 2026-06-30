"""Offline NODE_PATH walk of the real impl-feature-auto protocol (crafted verdicts).

Drives next.py + advance.py directly with crafted verdict files — no agents and
no real checks execute. Proves the production protocol's process axis:

  Walk 1 (happy path): design (all checks pass) → engine dispatches into
          implement → implement (check passes) → done.
  Walk 2 (terminal failure): design exhausts max_iterations (3) on an
          iterate-severity check → design state=failed → implement is NEVER
          dispatched/seeded.
  Walk 3 (block semantics, OBSERVED): a lone `block`-severity failure on design
          (spec-present) does NOT halt the pipeline in this engine — see the
          docstring on test_design_block_does_not_halt for the why + the gap it
          documents.

State-file layout OBSERVED from the engine (a multi-node root sequence):
  impl-feature-auto/issue-5/_instance.yaml   — root cursor (phase key)
  impl-feature-auto/issue-5/design.yaml      — design agent node
  impl-feature-auto/issue-5/implement.yaml   — implement agent node
  impl-feature-auto/issue-5/design.evidence.json — design evidence (inputs carrier)

This matches the multi-phase layout the brief assumed; mirrors how
test_unified_codereview_e2e.py locates per-phase state files under <pid>/<instance>/.
Instance key is issue-keyed (issue-5); pr_from_instance (Task 2) resolves it offline.
"""
import json
import pathlib
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / ".github/agent-factory/protocols/impl-feature-auto/protocol.json"
NEXT = ENG / "next.py"
ADVANCE = ENG / "advance.py"

PID = "impl-feature-auto"
INST = "issue-5"


def _yaml(p):
    return yaml.safe_load(open(p))


def _reclone(engine_env, tmp_path, tag):
    """Re-clone the state branch from the bare origin (fresh view of persisted state)."""
    d = tmp_path / f"rc-{tag}"
    subprocess.run(
        ["git", "clone", "-q", "-b", "agentic-state",
         engine_env["STATE_REMOTE"], str(d)],
        check=True,
    )
    return d / PID / INST


def _verdicts(tmp_path, tag, results):
    v = tmp_path / f"v-{tag}.json"
    v.write_text(json.dumps({"results": results}))
    ev = tmp_path / f"ev-{tag}.json"
    ev.write_text("{}")
    return v, ev


def _run(script, *args, env, **env_extra):
    e = dict(env)
    e.update(env_extra)
    return subprocess.run(
        ["python3", str(script), *map(str, args)],
        text=True, capture_output=True, env=e,
    )


# Design's five checks: three iterate-severity + two block-severity (spec/plan).
_DESIGN_PASS = [
    {"check": "ledger-wellformed", "pass": True, "feedback": "", "on_fail": "iterate"},
    {"check": "ledger-consistent", "pass": True, "feedback": "", "on_fail": "iterate"},
    {"check": "read-these-first-consistent", "pass": True, "feedback": "", "on_fail": "iterate"},
    {"check": "spec-present", "pass": True, "feedback": "", "on_fail": "block"},
    {"check": "plan-present", "pass": True, "feedback": "", "on_fail": "block"},
]


# ---------------------------------------------------------------------------
# Walk 1: design pass → implement → done
# ---------------------------------------------------------------------------

def test_design_pass_then_implement_then_done(engine_env, tmp_path):
    """Happy path: design clears → engine dispatches into implement → implement
    clears → done. Mirrors the real GHA flow (advance dispatches protocol-continue,
    which a follow-up `next.py continue` services to seed the next node)."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "sha1"
    base["AGENT_RUN_ID"] = "r1"

    # start → enter_root → run-agent at design; _instance.yaml + design.yaml seeded.
    r = _run(NEXT, tmp_path / "s", INST, PROTO, "start", "sha1", env=base)
    assert r.returncode == 0, r.stderr
    act = json.loads(r.stdout)
    assert act["action"] == "run-agent", act
    assert act["phase"] == "design", act

    fdir0 = _reclone(engine_env, tmp_path, "start")
    assert (fdir0 / "_instance.yaml").is_file()
    assert _yaml(fdir0 / "_instance.yaml")["phase"] == "design"
    assert (fdir0 / "design.yaml").is_file(), "design.yaml must be seeded by start"
    assert _yaml(fdir0 / "design.yaml")["state"] == "design"

    # advance design, all checks pass → design done + dispatch protocol-continue into implement.
    v, ev = _verdicts(tmp_path, "design", _DESIGN_PASS)
    r2 = _run(ADVANCE, tmp_path / "a1", INST, PROTO, v, ev, env=base, NODE_PATH="design")
    assert r2.returncode == 0, r2.stderr
    assert "event_type=protocol-continue" in r2.stderr, r2.stderr
    assert "client_payload[path]=implement" in r2.stderr, r2.stderr

    fdir1 = _reclone(engine_env, tmp_path, "afterdesign")
    assert _yaml(fdir1 / "design.yaml")["state"] == "done"
    assert _yaml(fdir1 / "_instance.yaml")["phase"] == "implement"
    # design evidence is persisted as the inputs carrier for implement.
    assert (fdir1 / "design.evidence.json").is_file()

    # continue NODE_PATH=implement → seed implement.yaml + emit run-agent carrying
    # design's evidence as the `design` input (Task-9 inputs wiring).
    r3 = _run(NEXT, tmp_path / "c", INST, PROTO, "continue", env=base, NODE_PATH="implement")
    assert r3.returncode == 0, r3.stderr
    act3 = json.loads(r3.stdout)
    assert act3["action"] == "run-agent", act3
    assert act3["path"] == "implement", act3
    inputs = {i["as"] for i in act3.get("inputs", [])}
    assert "design" in inputs, f"implement must receive design evidence input: {act3}"

    fdir2 = _reclone(engine_env, tmp_path, "implseed")
    assert (fdir2 / "implement.yaml").is_file(), "implement.yaml must be seeded by continue"

    # advance implement, check passes → done (implement is the last node; next=done).
    v2, ev2 = _verdicts(tmp_path, "impl",
                        [{"check": "implement-schema-valid", "pass": True,
                          "feedback": "", "on_fail": "iterate"}])
    base2 = dict(base)
    base2["AGENT_RUN_ID"] = "r2"
    r4 = _run(ADVANCE, tmp_path / "a2", INST, PROTO, v2, ev2, env=base2, NODE_PATH="implement")
    assert r4.returncode == 0, r4.stderr
    # last node: must NOT dispatch a further protocol-continue.
    assert "event_type=protocol-continue" not in r4.stderr, r4.stderr

    fdir3 = _reclone(engine_env, tmp_path, "done")
    assert _yaml(fdir3 / "implement.yaml")["state"] == "done", \
        _yaml(fdir3 / "implement.yaml")


# ---------------------------------------------------------------------------
# Walk 2: design terminal failure (iterate-exhaust) → implement never runs
# ---------------------------------------------------------------------------

def test_design_iterate_exhaust_fails_implement_never_runs(engine_env, tmp_path):
    """The genuine 'a design failure stops the pipeline before implement' proof.

    The brief framed this as a `block` failure, but in this engine a block-severity
    failure does NOT drive the process axis (see test_design_block_does_not_halt).
    The faithful terminal-failure path is an iterate-severity check exhausting
    max_iterations (design.max_iterations = 3): advance #1/#2 re-dispatch the SAME
    design node (path=design); advance #3 exhausts → design state=failed and NO
    dispatch into implement; implement.yaml is never seeded.
    """
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "sha1"

    r = _run(NEXT, tmp_path / "s", INST, PROTO, "start", "sha1", env=base)
    assert r.returncode == 0, r.stderr

    # ledger-wellformed is an iterate-severity check.
    fail = [{"check": "ledger-wellformed", "pass": False, "feedback": "bad", "on_fail": "iterate"}]

    # advance #1 and #2: iterate → re-dispatch the design node itself (path=design).
    for i in (1, 2):
        base_i = dict(base)
        base_i["AGENT_RUN_ID"] = f"r{i}"
        v, ev = _verdicts(tmp_path, f"f{i}", fail)
        ri = _run(ADVANCE, tmp_path / f"a{i}", INST, PROTO, v, ev, env=base_i, NODE_PATH="design")
        assert ri.returncode == 0, ri.stderr
        assert "client_payload[path]=design" in ri.stderr, \
            f"iterate must re-dispatch design (not implement): {ri.stderr}"
        assert "client_payload[path]=implement" not in ri.stderr, ri.stderr
        fdir = _reclone(engine_env, tmp_path, f"iter{i}")
        d = _yaml(fdir / "design.yaml")
        assert d["state"] == "design", d
        assert d["iteration"] == i + 1, d
        assert not (fdir / "implement.yaml").is_file()

    # advance #3: max_iterations exhausted → failed, no further dispatch.
    base3 = dict(base)
    base3["AGENT_RUN_ID"] = "r3"
    v3, ev3 = _verdicts(tmp_path, "f3", fail)
    r3 = _run(ADVANCE, tmp_path / "a3", INST, PROTO, v3, ev3, env=base3, NODE_PATH="design")
    assert r3.returncode == 0, r3.stderr
    assert "event_type=protocol-continue" not in r3.stderr, \
        f"exhausted design must not dispatch into implement: {r3.stderr}"

    fdir3 = _reclone(engine_env, tmp_path, "failed")
    assert _yaml(fdir3 / "design.yaml")["state"] == "failed"
    assert not (fdir3 / "implement.yaml").is_file(), \
        "implement must NOT be seeded after a terminal design failure"


# ---------------------------------------------------------------------------
# Walk 3: block semantics on a node without on_blocked — OBSERVED behavior
# ---------------------------------------------------------------------------

def test_design_block_does_not_halt(engine_env, tmp_path):
    """OBSERVED block semantics (documents a protocol-authoring gap, not a test bug).

    The design node marks spec-present / plan-present as `on_fail: block`. In this
    engine `lib.decide()` treats a block-severity failure as NON-iterating but it
    does NOT fail the process axis: with no iterate-severity failure present, the
    fold returns process='done' and a `blocking=True` flag. That flag only halts
    the pipeline when the node declares a `conclude` hook returning blocked + an
    `on_blocked: "halt"` field (see advance.py run_conclude_hook / the on_blocked
    check). The impl-feature-auto design node declares NEITHER, so a lone block
    failure leaves design state=done and the engine DISPATCHES protocol-continue
    into implement — i.e. the block is currently toothless.

    This test asserts the engine's ACTUAL behavior (engine is the source of truth;
    not modified to fit the test). The mismatch with the design intent — a missing
    spec/plan should stop the run before implement — is a protocol gap surfaced in
    the Task-13 report (consider adding a conclude hook + on_blocked:halt to the
    design node).
    """
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "sha1"
    base["AGENT_RUN_ID"] = "r1"

    r = _run(NEXT, tmp_path / "s", INST, PROTO, "start", "sha1", env=base)
    assert r.returncode == 0, r.stderr

    # Only spec-present (block) fails; the iterate checks pass → process axis = done.
    results = [
        {"check": "ledger-wellformed", "pass": True, "feedback": "", "on_fail": "iterate"},
        {"check": "ledger-consistent", "pass": True, "feedback": "", "on_fail": "iterate"},
        {"check": "read-these-first-consistent", "pass": True, "feedback": "", "on_fail": "iterate"},
        {"check": "spec-present", "pass": False, "feedback": "no spec", "on_fail": "block"},
        {"check": "plan-present", "pass": True, "feedback": "", "on_fail": "block"},
    ]
    v, ev = _verdicts(tmp_path, "block", results)
    r2 = _run(ADVANCE, tmp_path / "a", INST, PROTO, v, ev, env=base, NODE_PATH="design")
    assert r2.returncode == 0, r2.stderr

    # OBSERVED: block does not halt — design done + dispatch into implement.
    assert "client_payload[path]=implement" in r2.stderr, \
        f"block does not halt in this engine; expected dispatch into implement: {r2.stderr}"

    fdir = _reclone(engine_env, tmp_path, "block")
    design = _yaml(fdir / "design.yaml")
    assert design["state"] == "done", \
        f"block-severity failure does not fail the process axis: {design}"
    assert design["history"][-1]["checks"]["spec-present"] == "fail", design
    # The block advance itself only dispatches the continue; implement.yaml is
    # seeded by the follow-up `next.py continue` (not exercised here).
    assert not (fdir / "implement.yaml").is_file()
