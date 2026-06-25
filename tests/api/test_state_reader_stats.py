from api import state_reader
from tests.api.fixtures_helper import load_instance_files

def test_instance_stats_code_review_pr62():
    out = state_reader.instance_stats(load_instance_files("code-review", 62))
    assert out["protocol"] == "code-review"
    assert out["pr"] == 62
    assert out["current_phase"] == "approval"
    assert out["head_sha"] == "657e290beb6266ccd55b8bd95e247491e3468392"
    # preflight(1) + review.grumpy(1) + review.security(1) = 3 history entries
    assert out["state_transitions"] == 3
    assert out["iterations_by_phase"]["preflight"] == 1
    assert out["iterations_by_phase"]["review.grumpy"] == 1
    assert out["phases_completed"] >= 2   # preflight + review done
    assert out["phases_failed"] == 0
