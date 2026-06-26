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
    assert by_as["preflight"]["path"].endswith("/preflight.evidence.json")


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
