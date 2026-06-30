from api import state_reader
from tests.api.fixtures_helper import load_instance_files, load_instance_dir

def test_status_projection_code_review_pr62():
    out = state_reader.status_projection(load_instance_files("code-review", 62))
    assert out["protocol"] == "code-review"
    assert out["pr"] == 62
    assert out["head"]["phase"] == "approval"
    phases = {p["id"]: p for p in out["phases"]}
    assert phases["preflight"]["kind"] == "agent"
    assert phases["preflight"]["status"] == "done"
    assert phases["preflight"]["checks"]["spec-present"] == "pass"
    assert phases["review"]["kind"] == "fanout"
    legs = {b["id"]: b for b in phases["review"]["branches"]}
    assert legs["grumpy"]["status"] == "done"
    assert legs["security"]["status"] == "done"
    assert phases["approval"]["kind"] == "gate"
    assert phases["approval"]["gate"]["open"] is True

def test_status_projection_head_carries_run_identity():
    # The head must carry run identity so a client can distinguish "the previous
    # run's terminal done" from a fresh done. head_sha comes from _instance.yaml;
    # run_id/attempt come from the head phase node when it is a single agent.
    out = state_reader.status_projection(load_instance_files("code-review", 62))
    head = out["head"]
    assert head["head_sha"] == "657e290beb6266ccd55b8bd95e247491e3468392"
    # pr-62's head is the `approval` gate (no agent run) — run_id is absent/None,
    # but head_sha still distinguishes the run.
    assert head.get("run_id") is None


def test_status_projection_agent_leaf_carries_run_id():
    # Every agent leaf (single agent phase or fanout branch) surfaces the run_id
    # that produced its latest attempt, sourced from history[-1].agent_run_id.
    out = state_reader.status_projection(load_instance_files("code-review", 62))
    phases = {p["id"]: p for p in out["phases"]}
    assert phases["preflight"]["run_id"] == "28110616119"
    legs = {b["id"]: b for b in phases["review"]["branches"]}
    assert all("run_id" in leg for leg in legs.values())


def test_status_projection_terminal_merge_head_reports_done():
    # A completed merge node (recover-mental-model's `combine`) writes no own
    # state file, so the head phase has no matching `phases` entry. The instance
    # is genuinely done — `_instance.yaml` carries `phase_label: "✅ done"`. The
    # projection must surface that as a top-level `status` and on the head, not
    # leave a statusless head that looks stuck.
    out = state_reader.status_projection(
        load_instance_dir("recover-mental-model", "pr-82"))
    assert out["status"] == "completed"
    assert out["head"]["phase"] == "combine"
    assert out["head"]["status"] == "done"


def test_status_projection_mid_combine_stays_running():
    # joined but no terminal phase_label yet (merge hook not finished): the
    # instance is still running, and the head must NOT be reported as done.
    out = state_reader.status_projection(
        load_instance_dir("recover-mental-model", "ref-main"))
    assert out["status"] == "running"
    assert "status" not in out["head"]


def test_status_projection_ignores_sidecars_and_join_markers():
    out = state_reader.status_projection(load_instance_files("deep-review-stub", 88))
    ids = {p["id"] for p in out["phases"]}
    assert "deep.analyze.__join" not in ids
    assert all(not i.endswith(".json") for i in ids)

def test_status_projection_excludes_injected_sidecar_filenames():
    # Inject the sidecar filename categories directly to prove _is_node_file
    # excludes them (not merely that no .yaml node happens to be named .json).
    files = load_instance_files("code-review", 62)
    files["preflight.evidence.json"] = '{"x": 1}'
    files["something.answers.json"] = '{"y": 2}'
    files["review.__join.yaml"] = "joined: true\n"
    out = state_reader.status_projection(files)
    ids = {p["id"] for p in out["phases"]}
    # the real nodes still project; none of the injected sidecars become a phase
    assert "preflight" in ids and "review" in ids and "approval" in ids
    assert not any("evidence" in i or "answers" in i or "__join" in i for i in ids)

def test_evidence_projection_splits_evidence_and_answers():
    files = {
        "_instance.yaml": "protocol: code-review\ninstance: pr-62\n",
        "preflight.yaml": "state: done\n",
        "preflight.evidence.json": '{"checks":[{"id":"spec-present","status":"pass"}]}',
        "review.security.evidence.json": '{"dimension":"security","verdict":"APPROVE","findings":[]}',
        "approval.answers.json": '{"decision":"approve"}',
    }
    out = state_reader.evidence_projection(files)
    assert out["evidence"]["preflight"] == {"checks": [{"id": "spec-present", "status": "pass"}]}
    assert out["evidence"]["review.security"]["dimension"] == "security"
    assert out["answers"]["approval"] == {"decision": "approve"}
    assert "preflight" not in out["answers"]
    assert set(out["evidence"]) == {"preflight", "review.security"}

def test_evidence_projection_skips_malformed_json():
    files = {"bad.evidence.json": "{not json", "ok.evidence.json": '{"a":1}'}
    out = state_reader.evidence_projection(files)
    assert out["evidence"] == {"ok": {"a": 1}}
