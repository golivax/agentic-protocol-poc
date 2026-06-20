"""Regression guard: the code-review protocol's preflight and review-branch checks
must resolve to distinct validators.  If both phases again reference the same file
this test will catch it early.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / ".github/agent-factory/protocols/code-review/checks"


def _run(check_name, evidence_obj, tmp_path, check_params=None):
    from conftest import run_check
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(evidence_obj))
    diff = tmp_path / "diff.txt"; diff.write_text("")
    files = tmp_path / "files.txt"; files.write_text("")
    return run_check(CHECKS / check_name, ev, diff, files, check_params=check_params)


# Valid grumpy-shaped evidence: one file with a single none-found verdict.
# none-found requires a non-empty `examined` list.
_GRUMPY_CATEGORIES = ["naming", "error-handling", "performance", "duplication", "security"]

_VALID_GRUMPY_EVIDENCE = {
    "files": [
        {
            "path": "a.js",
            "verdicts": [
                {
                    "category": "naming",
                    "verdict": "none-found",
                    "examined": ["someFunction"]
                }
            ]
        }
    ]
}

_GRUMPY_PARAMS = {"categories": _GRUMPY_CATEGORIES}

# Valid preflight evidence
_VALID_PREFLIGHT_EVIDENCE = {
    "checks": [{"id": "spec-adherence", "status": "pass"}],
    "examined": ["x"]
}


def test_review_branch_schema_valid_accepts_grumpy_evidence(tmp_path):
    """The review-branch schema-valid (grumpy rubric validator) must PASS on valid
    grumpy-shaped evidence.  This confirms the correct validator is wired up."""
    v = _run("schema-valid.py", _VALID_GRUMPY_EVIDENCE, tmp_path, check_params=_GRUMPY_PARAMS)
    assert v["pass"] is True, f"expected pass, got: {v['feedback']}"


def test_preflight_schema_valid_accepts_preflight_evidence(tmp_path):
    """The preflight-schema-valid validator must PASS on valid preflight evidence."""
    v = _run("preflight-schema-valid.py", _VALID_PREFLIGHT_EVIDENCE, tmp_path)
    assert v["pass"] is True, f"expected pass, got: {v['feedback']}"


def test_review_branch_schema_valid_rejects_preflight_shaped_evidence(tmp_path):
    """The review-branch schema-valid must FAIL on preflight-shaped evidence (no
    `files` key) — proving the two checks are now distinct validators."""
    v = _run("schema-valid.py", _VALID_PREFLIGHT_EVIDENCE, tmp_path, check_params=_GRUMPY_PARAMS)
    assert v["pass"] is False, "review schema-valid should reject preflight evidence (no files key)"
