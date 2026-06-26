# tests/test_emit_legs.py
import json, subprocess, pathlib, os
ROOT = pathlib.Path(__file__).resolve().parent.parent
NEXT = ROOT / ".github/agent-factory/engine/next.py"

_counter = [0]

def _emit(engine_env, tmp_path, proto_rel, command, *args, node_path=None):
    proto = ROOT / proto_rel
    e = dict(engine_env)
    if node_path is not None:
        e["NODE_PATH"] = node_path
    _counter[0] += 1
    sdir = tmp_path / f"s{_counter[0]}"
    r = subprocess.run(["python3", str(NEXT), str(sdir), "pr-1", str(proto),
                        command, *args], text=True, capture_output=True, env=e)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)

def test_codereview_review_legs_carry_leaf_path_and_workflow(engine_env, tmp_path):
    # code-review-v1 start lands at preflight (agent). Continue at the review fanout.
    _emit(engine_env, tmp_path, ".github/agent-factory/protocols/code-review-v1/protocol.json",
          "start", "sha1")
    act = _emit(engine_env, tmp_path,
                ".github/agent-factory/protocols/code-review-v1/protocol.json",
                "continue", node_path="review")
    assert act["action"] == "run-fanout"
    legs = {l["path"]: l["workflow"] for l in act["legs"]}
    assert legs == {"review.grumpy": "grumpy-agent", "review.security": "security-agent"}

def test_recover_legs_subpipeline_branch_points_at_first_substate(engine_env, tmp_path):
    act = _emit(engine_env, tmp_path,
                ".github/agent-factory/protocols/recover-mental-model-stub/protocol.json",
                "start", "sha1")
    assert act["action"] == "run-fanout"
    legs = {l["path"]: l["workflow"] for l in act["legs"]}
    # flat branch → branch path; sub-pipeline branch → first sub-state (draft).
    assert legs == {"recover.summary": "rmm-summary-agent",
                    "recover.rationale.draft": "rmm-draft-agent"}

def test_codereview_preflight_run_agent_carries_path_and_workflow(engine_env, tmp_path):
    act = _emit(engine_env, tmp_path,
                ".github/agent-factory/protocols/code-review-v1/protocol.json",
                "start", "sha1")
    assert act["action"] == "run-agent"
    assert act["path"] == "preflight"
    assert act["workflow"] == "preflight-agent-v1"
