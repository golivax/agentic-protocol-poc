import json
from pathlib import Path
import jsonschema  # dev-only dep, already used by protocol-lint
from conftest import PROTOCOLS

SCHEMA = json.loads((PROTOCOLS / "code-review/judge.evidence.schema.json").read_text())

def _valid():
    return {"leg": "plan-implements-spec",
            "gather": {"scope": {"spec_present": True, "plan_present": True, "code_changed": True},
                       "verdict": "underspec", "spec_to_plan": [], "plan_to_spec": [], "examined": ["x"]},
            "graded_findings": [{"ref": "REQ-1", "severity": "blocking", "rationale": "no plan item"}],
            "verdict": "block", "examined": ["REQ-1"]}

def test_valid_judge_evidence_passes():
    jsonschema.validate(_valid(), SCHEMA)

def test_bad_severity_rejected():
    ev = _valid(); ev["graded_findings"][0]["severity"] = "critical"
    try:
        jsonschema.validate(ev, SCHEMA); assert False, "should reject"
    except jsonschema.ValidationError:
        pass

def test_missing_gather_rejected():
    ev = _valid(); del ev["gather"]
    try:
        jsonschema.validate(ev, SCHEMA); assert False
    except jsonschema.ValidationError:
        pass
