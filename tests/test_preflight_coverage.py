import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / ".github/agent-factory/protocols/code-review/checks"


def _run(check_name, evidence_obj, changed_files, tmp_path, params=None):
    from conftest import run_check
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(evidence_obj))
    diff = tmp_path / "diff.txt"; diff.write_text("")
    files = tmp_path / "files.txt"; files.write_text("\n".join(changed_files) + "\n")
    return run_check(CHECKS / check_name, ev, diff, files, check_params=params)


AI_PARAMS = {"ai_checks": ["spec-adherence", "plan-adherence"]}


def _evidence(ids):
    return {"checks": [{"id": i, "status": "pass", "summary": "ok", "evidence": []} for i in ids],
            "examined": ["src/app.py"]}


# adherence-coverage: expected set derived from changed-files -------------------

def test_coverage_ok_both_artifacts_present(tmp_path):
    v = _run("adherence-coverage.py", _evidence(["spec-adherence", "plan-adherence"]),
             ["docs/specs/s.md", "docs/superpowers/plans/p.md", "src/app.py"], tmp_path, AI_PARAMS)
    assert v["check"] == "adherence-coverage" and v["pass"] is True


def test_coverage_spec_only_expects_only_spec_adherence(tmp_path):
    # plan file absent → plan-adherence must NOT appear; spec-adherence must.
    v = _run("adherence-coverage.py", _evidence(["spec-adherence"]),
             ["docs/specs/s.md", "src/app.py"], tmp_path, AI_PARAMS)
    assert v["pass"] is True


def test_coverage_missing_expected_verdict_fails(tmp_path):
    # spec file present but spec-adherence not judged → fail.
    v = _run("adherence-coverage.py", _evidence([]),
             ["docs/specs/s.md", "src/app.py"], tmp_path, AI_PARAMS)
    assert v["pass"] is False and "spec-adherence" in v["feedback"]


def test_coverage_unexpected_verdict_fails(tmp_path):
    # no spec/plan file → no adherence expected, but agent judged spec-adherence → fail.
    v = _run("adherence-coverage.py", _evidence(["spec-adherence"]),
             ["src/app.py"], tmp_path, AI_PARAMS)
    assert v["pass"] is False


def test_coverage_neither_artifact_empty_evidence_passes(tmp_path):
    v = _run("adherence-coverage.py", _evidence([]), ["src/app.py"], tmp_path, AI_PARAMS)
    assert v["pass"] is True


# schema-valid: preflight evidence shape ---------------------------------------

def test_schema_valid_ok(tmp_path):
    v = _run("preflight-schema-valid.py", _evidence(["spec-adherence"]), ["docs/specs/s.md"], tmp_path, AI_PARAMS)
    assert v["check"] == "schema-valid" and v["pass"] is True


def test_schema_valid_rejects_bad_status(tmp_path):
    bad = {"checks": [{"id": "spec-adherence", "status": "MAYBE"}], "examined": ["x"]}
    v = _run("preflight-schema-valid.py", bad, ["docs/specs/s.md"], tmp_path, AI_PARAMS)
    assert v["pass"] is False


def test_schema_valid_rejects_missing_checks_key(tmp_path):
    v = _run("preflight-schema-valid.py", {"examined": ["x"]}, ["docs/specs/s.md"], tmp_path, AI_PARAMS)
    assert v["pass"] is False
