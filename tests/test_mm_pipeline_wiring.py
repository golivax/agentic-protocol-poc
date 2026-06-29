"""Offline coverage for the two structural paths the mm-compliance / mm-updater
integration introduced into the code-review protocol (PR #108):

  1. `mrp.inputs: [{from: context}]` now resolves from the **context fan-out leg**
     (post-fix.context) rather than a top-level `context` phase — the resolved path
     must equal where the leg actually writes its evidence, or mrp silently gets no
     context input.
  2. The mm-updater leg ends in a **terminal** data gate (`mm-updater -> mm-gate`):
     when it resolves to ZERO questions the auto-skip must complete the LEG and fire
     the fan-out join (not advance to a non-existent next). The recover walk only
     covers the gate-HAS-next case; this covers gate-IS-terminal via a minimal fixture.
"""
import importlib
import json
import os
import subprocess
import sys

from conftest import ENGINE, FIXTURES, PROTOCOLS, read_state_yaml, run_engine

sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")

CODE_REVIEW = PROTOCOLS / "code-review/protocol.json"


def test_mrp_input_resolves_from_context_leg():
    """mrp's `from: context` resolves to the context fan-out LEG's evidence path —
    byte-identical to where the leg writes it (post-fix.context.evidence.json)."""
    proto = json.load(open(CODE_REVIEW))
    d, pid, inst = "/s", "code-review", "pr-1"

    leg_ev = lib.output_artifact_path(
        d, pid, inst, path=lib.state_path(proto, ["post-fix", "context"]), kind="evidence")

    resolved = lib.resolve_inputs(
        proto, d, pid, inst, consuming_branch=None, consuming_phase=None,
        inputs=lib.state_inputs(proto, "mrp"), consuming_path=["mrp"])
    by_as = {r["as"]: r for r in resolved}

    assert by_as["context"]["path"] == leg_ev, (
        f"mrp 'context' input must resolve to the context leg evidence ({leg_ev}); "
        f"got {by_as['context']['path']}")
    # pin the literal leg-evidence path (guards both sides drifting together)
    assert by_as["context"]["path"].endswith("/post-fix.context.evidence.json"), \
        f"context input should resolve to the post-fix.context leg, got {by_as['context']['path']}"
    # and explicitly NOT the legacy path-unaware top-level-phase resolution — that is the
    # exact silent-loss regression (mrp would read a context.evidence.json that never gets written)
    assert not by_as["context"]["path"].endswith("/context.evidence.json"), \
        "mrp 'context' regressed to legacy top-level-phase resolution; the leg evidence would be missed"
    assert by_as["context"]["kind"] == "evidence"  # a leg output, not a gate's answers
    # the other (top-level phase) inputs still resolve as phase evidence
    assert by_as["triage"]["path"].endswith("/triage.evidence.json")
    assert by_as["preflight"]["path"].endswith("/preflight-gate.evidence.json")


def test_empty_terminal_gate_completes_leg_and_fires_join(tmp_path, engine_env):
    """A data gate that is the LAST sub-state of a sub-pipeline leg, resolving to ZERO
    questions, auto-completes -> the leg reaches `done` -> the fan-out join fires
    (the custody `mm-updater -> mm-gate` shape). Complements the gate-has-next case."""
    PROTO = FIXTURES / "gate-terminal/protocol.json"
    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))

    def adv(node_path, evidence_dict):
        ev = tmp_path / (node_path.replace(".", "-") + ".json")
        ev.write_text(json.dumps(evidence_dict))
        e = dict(engine_env)
        e["PR_HEAD_SHA"] = "abc123"
        e["AGENT_RUN_ID"] = "r"
        e["NODE_PATH"] = node_path
        out, err, rc = run_engine(
            "advance.py", tmp_path / ("dir-" + node_path.replace(".", "-")),
            "pr-1", PROTO, passv, ev, env=e)
        assert rc == 0, f"advance {node_path} failed:\n{err}"
        return err

    out, err, rc = run_engine("next.py", tmp_path / "dir-next", "pr-1", PROTO,
                              "start", "abc123", env=engine_env)
    assert rc == 0, f"next start failed:\n{err}"
    adv("f.a", {"ok": True})                          # flat leg a -> done
    err_b = adv("f.b.draft", {"questions": []})        # draft done, empty questions -> gate auto-skip

    work = tmp_path / "verify"
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)

    legb = read_state_yaml(work / "gate-terminal/pr-1/b.yaml")
    assert legb["state"] == "done", (
        f"terminal-gate auto-skip must complete leg b, got state={legb.get('state')}")
    # the auto-skip always writes the gate file as auto-resolved (unconditional — a missing
    # gate file would itself be a regression, so don't guard the assertion behind exists())
    gate = work / "gate-terminal/pr-1/b.gate.yaml"
    assert gate.exists(), "terminal gate file must be written by the auto-skip"
    assert read_state_yaml(gate)["gates"]["state"] == "auto-resolved", \
        "terminal gate must be auto-resolved (not opened/held)"
    assert "event_type=protocol-join" in err_b, (
        f"completing leg b (via terminal-gate auto-skip) must fire the fan-out join:\n{err_b}")


def test_empty_data_gate_with_next_advances_to_next_substate(tmp_path, engine_env):
    """A data gate that is NOT the last sub-state (`draft -> clarify(gate) -> finalize`),
    resolving to ZERO questions, auto-resolves and advances to the NEXT sub-state
    (finalize) instead of opening for a human or completing the leg. This is the
    gate-HAS-next twin of the terminal-gate case, and exercises the auto-skip's
    recursion through the post-d1df1f7 no-pre-seed agent dispatch arm."""
    PROTO = FIXTURES / "subpipeline-gate/protocol.json"
    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))

    def adv(node_path, evidence_dict):
        ev = tmp_path / (node_path.replace(".", "-") + ".json")
        ev.write_text(json.dumps(evidence_dict))
        e = dict(engine_env)
        e.update(PR_HEAD_SHA="abc123", AGENT_RUN_ID="r", PR="1", NODE_PATH=node_path)
        out, err, rc = run_engine(
            "advance.py", tmp_path / ("dir-" + node_path.replace(".", "-")),
            "pr-1", PROTO, passv, ev, env=e)
        assert rc == 0, f"advance {node_path} failed:\n{err}"
        return err

    out, err, rc = run_engine("next.py", tmp_path / "dir-next", "pr-1", PROTO,
                              "start", "abc123", env=engine_env)
    assert rc == 0, f"next start failed:\n{err}"
    err_d = adv("recover.rationale.draft", {"questions": []})  # empty questions -> gate auto-skip

    work = tmp_path / "verify"
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)

    # the clarify gate is auto-resolved (not opened for a human)...
    gate = work / "subpipeline-gate/pr-1/rationale.clarify.yaml"
    assert gate.exists(), "the auto-skipped gate file must be written"
    gy = read_state_yaml(gate)
    assert gy["gates"]["state"] == "auto-resolved", \
        f"empty data gate must be auto-resolved, got {gy['gates'].get('state')!r}"
    assert gy["state"] == "done", f"auto-skipped gate state should be done, got {gy.get('state')!r}"

    # ...and the leg ADVANCES to finalize (still in flight) rather than completing/firing join.
    leg = read_state_yaml(work / "subpipeline-gate/pr-1/rationale.yaml")
    assert leg.get("sub_state") == "finalize", (
        f"gate-with-next auto-skip must advance the leg cursor to finalize, "
        f"got sub_state={leg.get('sub_state')!r}")
    assert leg.get("state") != "done", \
        "leg must stay in flight after auto-skipping to a non-terminal next (finalize), not complete"
    assert "client_payload[substate]=finalize" in err_d, (
        f"auto-skip must dispatch a protocol-continue for the finalize agent:\n{err_d}")
    assert "event_type=protocol-join" not in err_d, \
        "a gate-with-next auto-skip must NOT fire the join (the leg is not terminal yet)"


def test_data_gate_comment_uses_protocol_answer_prefix(tmp_path, engine_env, capfd):
    """open_gate must instruct the protocol's CONFIGURED answer prefix (/mm-answer for
    code-review), not a hardcoded /answer — which is not a code-review trigger and would
    route to nothing, leaving the gate unresolvable."""
    for k, v in engine_env.items():
        os.environ[k] = v
    lib.STATE_REMOTE = engine_env["STATE_REMOTE"]
    d = tmp_path / "dir"
    lib.state_checkout(str(d))
    inf = lib.instance_file(str(d), "code-review", "pr-1")
    os.makedirs(os.path.dirname(inf), exist_ok=True)
    lib.dump_yaml(inf, {"protocol": "code-review", "instance": "pr-1", "joined": False})
    capfd.readouterr()
    lib.open_gate(str(d), "code-review", "pr-1", str(CODE_REVIEW), "mm-gate", "sha", "1",
                  questions=[{"id": "mm-pr", "text": "decide on the MM PR"}],
                  path=["post-fix", "mm", "mm-gate"])
    cap = capfd.readouterr()
    out = cap.out + cap.err
    assert "/mm-answer" in out, f"gate comment should instruct /mm-answer, got:\n{out}"
    assert "`/answer " not in out, f"gate comment must not instruct the unregistered /answer:\n{out}"


def test_two_top_level_fanouts_second_join_advances(tmp_path, engine_env):
    """Two top-level fanouts in sequence (f1 -> j1 -> mid -> f2 -> j2 -> done): the
    SECOND fanout's join must advance to `done`. The instance-wide `joined` latch set
    by f1's join is reset when f2 is ENTERED, else join.py no-ops f2's barrier and the
    pipeline stalls — exactly the code-review review->...->post-fix->mrp stall."""
    PROTO = FIXTURES / "two-fanout/protocol.json"
    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))

    def adv(node_path, ev_dict=None):
        ev = tmp_path / (node_path.replace(".", "-") + ".json")
        ev.write_text(json.dumps(ev_dict or {"ok": True}))
        e = dict(engine_env)
        e.update(PR_HEAD_SHA="abc123", AGENT_RUN_ID="r", PR="1", NODE_PATH=node_path)
        out, err, rc = run_engine("advance.py", tmp_path / ("adv-" + node_path.replace(".", "-")),
                                  "pr-1", PROTO, passv, ev, env=e)
        assert rc == 0, f"advance {node_path}:\n{err}"

    def join():
        e = dict(engine_env)
        e.update(PR_HEAD_SHA="abc123", PR="1")
        out, err, rc = run_engine("join.py", tmp_path / f"join-{join.n}", "pr-1", PROTO, env=e)
        join.n += 1
        assert rc == 0, f"join:\n{err}"
        return out + err
    join.n = 0

    def cont(node_path):
        e = dict(engine_env)
        e.update(PR_HEAD_SHA="abc123", PR="1", NODE_PATH=node_path)
        out, err, rc = run_engine("next.py", tmp_path / f"cont-{node_path}", "pr-1", PROTO,
                                  "continue", env=e)
        assert rc == 0, f"continue {node_path}:\n{err}"
        return out + err

    def phase():
        w = tmp_path / f"verify-{phase.n}"
        phase.n += 1
        subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(w)], check=True)
        return read_state_yaml(w / "two-fanout/pr-1/_instance.yaml").get("phase")
    phase.n = 0

    run_engine("next.py", tmp_path / "dir-next", "pr-1", PROTO, "start", "abc123", env=engine_env)
    adv("f1.a1"); adv("f1.b1")
    assert "client_payload[path]=mid" in join(), "f1 join should advance to mid"
    cont("mid"); adv("mid")        # mid agent -> advance root cursor to f2 + dispatch continue f2
    cont("f2")                     # ENTER f2 (must reset the joined latch)
    adv("f2.a2"); adv("f2.b2")
    j2 = join()
    assert "already joined; no-op" not in j2, \
        f"second fanout join stalled on the stale instance-wide joined latch:\n{j2}"
    assert "client_payload[path]=final" in j2, \
        f"second top-level fanout join must advance to its next phase (final):\n{j2}"
    assert phase() == "final", f"instance should advance past the second fanout, got phase={phase()!r}"
