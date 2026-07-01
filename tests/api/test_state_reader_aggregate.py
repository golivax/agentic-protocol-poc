from api import state_reader
from tests.api.fixtures_helper import load_instance_files, load_instance_dir


def test_status_projection_ref_instance_has_null_pr():
    """A ref-targeted (non-PR) instance: projections must not crash on int(pr-…)
    and must report pr=null + the instance id (UI polls by instance)."""
    files = load_instance_dir("recover-mental-model", "ref-main")
    st = state_reader.status_projection(files)
    assert st["pr"] is None and st["instance"] == "ref-main"
    stats = state_reader.instance_stats(files)
    assert stats["pr"] is None and stats["instance"] == "ref-main"


def test_pr_of_helper():
    assert state_reader._pr_of("pr-62") == 62
    assert state_reader._pr_of("ref-main") is None
    assert state_reader._pr_of("ui-abc") is None


def test_node_status_gate_aware():
    # A gate's top-level `state` stays the gate id; its progress is in gates.state.
    ns = state_reader._node_status
    assert ns({"state": "answering", "gates": {"state": "open"}}) == "running"
    assert ns({"state": "answering", "gates": {"state": "answered"}}) == "done"
    assert ns({"state": "approval", "gates": {"state": "approved"}}) == "done"
    assert ns({"state": "approval", "gates": {"state": "rejected"}}) == "failed"
    # agent nodes (no gates.state) unchanged
    assert ns({"state": "done"}) == "done"
    assert ns({"state": "phase2"}) == "running"


def test_status_projection_answered_gate_is_done():
    """Regression: an answered issue-gate (channel:issue) in a sub-pipeline must
    read done, not forever-running (visibility API showed socratic running)."""
    files = {
        "_instance.yaml": "protocol: p\ninstance: ref-x\nphase: combine\n",
        "socratic.phase1.yaml": "state: done\n",
        "socratic.answering.yaml": "state: answering\ngates:\n  state: answered\n  channel: issue\n",
        "socratic.phase2.yaml": "state: done\n",
    }
    st = state_reader.status_projection(files)
    soc = next(p for p in st["phases"] if p["id"] == "socratic")
    assert soc["status"] == "done"
    assert {b["id"]: b["status"] for b in soc["branches"]}["answering"] == "done"


def test_classify_label_variants():
    assert state_reader.classify_label("✅ done") == "completed"
    assert state_reader.classify_label("❌ failed") == "failed"
    assert state_reader.classify_label("⛔ blocked") == "blocked"
    assert state_reader.classify_label("approval gate") == "running"


def test_classify_label_none_is_running():
    # phase_label may be missing/None; must not raise.
    assert state_reader.classify_label(None) == "running"


def test_classify_instance_reads_phase_label_from_instance_yaml():
    # deep-review-stub pr-88 _instance.yaml carries phase_label "✅ done"
    files = load_instance_files("deep-review-stub", 88)
    assert state_reader.classify_instance(files["_instance.yaml"]) == "completed"
    # code-review pr-62 is mid-flight at the approval gate -> running
    cr = load_instance_files("code-review", 62)
    assert state_reader.classify_instance(cr["_instance.yaml"]) == "running"


def test_gate_view_open_answer_gate():
    gv = state_reader.gate_view(load_instance_files("recover-mental-model", 82))
    # pr-82's clarify gate is answered (closed) -> no OPEN gate
    assert gv is None


def test_gate_view_open_approval_gate():
    gv = state_reader.gate_view(load_instance_files("code-review", 62))
    assert gv is not None
    assert gv["phase"] == "approval"
    assert gv["open"] is True
    assert gv["awaiting"] == "approval"


def test_sum_run_minutes_wallclock():
    runs = [
        {"run_started_at": "2026-06-24T10:00:00Z", "updated_at": "2026-06-24T10:03:00Z"},
        {"run_started_at": "2026-06-24T11:00:00Z", "updated_at": "2026-06-24T11:01:30Z"},
        {"run_started_at": None, "updated_at": "2026-06-24T11:01:30Z"},  # contributes 0
    ]
    assert state_reader.sum_run_minutes(runs) == 4.5
