import json
from pathlib import Path
import jsonschema  # dev-only dep, already used by protocol-lint
from conftest import PROTOCOLS

SCHEMA = json.loads((PROTOCOLS / "code-review/judge.evidence.schema.json").read_text())

def _valid():
    # Revision 3 lightened shape: scope + gather_verdict instead of gather object
    return {"leg": "plan-implements-spec",
            "scope": {"spec_present": True, "plan_present": True, "code_changed": True},
            "gather_verdict": "underspec",
            "graded_findings": [{"ref": "REQ-1", "severity": "blocking", "rationale": "no plan item"}],
            "examined": ["REQ-1"]}

def test_valid_judge_evidence_passes():
    jsonschema.validate(_valid(), SCHEMA)

def test_bad_severity_rejected():
    ev = _valid(); ev["graded_findings"][0]["severity"] = "critical"
    try:
        jsonschema.validate(ev, SCHEMA); assert False, "should reject"
    except jsonschema.ValidationError:
        pass

def test_missing_scope_rejected():
    ev = _valid(); del ev["scope"]
    try:
        jsonschema.validate(ev, SCHEMA); assert False
    except jsonschema.ValidationError:
        pass

def test_missing_gather_verdict_rejected():
    ev = _valid(); del ev["gather_verdict"]
    try:
        jsonschema.validate(ev, SCHEMA); assert False
    except jsonschema.ValidationError:
        pass

def test_old_gather_field_is_not_required():
    """The old 'gather' object is no longer required (R3 lightening)."""
    ev = _valid()
    # Should pass without a 'gather' key
    jsonschema.validate(ev, SCHEMA)
