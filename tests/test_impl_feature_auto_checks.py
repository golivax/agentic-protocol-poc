import json, os, pathlib, subprocess
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHK = ROOT / ".github/agent-factory/protocols/impl-feature-auto/checks"

def run_check(name, evidence_obj, tmp_path, extra_files=None):
    """Write evidence (+ optional sibling files like spec.md) and run the check."""
    ev = tmp_path / "evidence.json"
    ev.write_text(json.dumps(evidence_obj))
    for fn, content in (extra_files or {}).items():
        (tmp_path / fn).write_text(content)
    empty = tmp_path / "empty.txt"; empty.write_text("")
    r = subprocess.run(
        ["python3", str(CHK / f"{name}.py"), str(ev), str(empty), str(empty)],
        text=True, capture_output=True)
    assert r.returncode == 0, f"check must exit 0; stderr={r.stderr}"
    return json.loads(r.stdout)

def good_item(**over):
    item = {
        "id": "L1", "category": "DECISION",
        "what": "Use issue-<N> instance keys",
        "why": "mirrors recover ref-keying",
        "what_i_did": "added target field",
        "confidence": "high",
        "blast_radius": {"level": "low", "why": "internal routing only"},
        "reversibility": {"level": "reversible", "why": "field is additive"},
        "revisit_if": "a third keying scheme appears",
    }
    item.update(over)
    return item

def good_evidence(**over):
    ev = {"spec_path": "docs/superpowers/specs/x-design.md",
          "plan_path": "docs/superpowers/plans/x.md",
          "ledger": [good_item()], "read_these_first": []}
    ev.update(over)
    return ev

# ---- ledger-wellformed ----
def test_wellformed_passes_on_good_ledger(tmp_path):
    out = run_check("ledger-wellformed", good_evidence(), tmp_path)
    assert out["pass"] is True, out

def test_wellformed_fails_missing_field(tmp_path):
    item = good_item(); del item["why"]
    out = run_check("ledger-wellformed", good_evidence(ledger=[item]), tmp_path)
    assert out["pass"] is False and "why" in out["feedback"]

def test_wellformed_fails_trivial_field(tmp_path):
    out = run_check("ledger-wellformed", good_evidence(ledger=[good_item(what_i_did="TODO")]), tmp_path)
    assert out["pass"] is False

def test_wellformed_fails_bad_enum(tmp_path):
    out = run_check("ledger-wellformed", good_evidence(ledger=[good_item(confidence="maybe")]), tmp_path)
    assert out["pass"] is False

def test_wellformed_fails_bad_blast_level(tmp_path):
    it = good_item(); it["blast_radius"] = {"level": "huge", "why": "x"}
    out = run_check("ledger-wellformed", good_evidence(ledger=[it]), tmp_path)
    assert out["pass"] is False

def test_wellformed_fails_blast_why_trivial(tmp_path):
    it = good_item(); it["blast_radius"] = {"level": "high", "why": "N/A"}
    out = run_check("ledger-wellformed", good_evidence(ledger=[it]), tmp_path)
    assert out["pass"] is False and "blast_radius" in out["feedback"]

def test_wellformed_assumption_requires_verified_true(tmp_path):
    it = good_item(category="ASSUMPTION")  # no `verified`
    out = run_check("ledger-wellformed", good_evidence(ledger=[it]), tmp_path)
    assert out["pass"] is False and "verified" in out["feedback"]
    it2 = good_item(category="ASSUMPTION", verified=True)
    assert run_check("ledger-wellformed", good_evidence(ledger=[it2]), tmp_path)["pass"] is True

def test_wellformed_fails_empty_ledger(tmp_path):
    out = run_check("ledger-wellformed", good_evidence(ledger=[]), tmp_path)
    assert out["pass"] is False
