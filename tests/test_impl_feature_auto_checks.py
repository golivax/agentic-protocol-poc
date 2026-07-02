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

# ---- ledger-consistent ----
def test_consistent_passes_clean(tmp_path):
    out = run_check("ledger-consistent", good_evidence(), tmp_path)
    assert out["pass"] is True, out

def test_consistent_fails_unknown_high_confidence(tmp_path):
    it = good_item(id="L1", category="UNKNOWN", confidence="high")
    out = run_check("ledger-consistent", good_evidence(ledger=[it]), tmp_path)
    assert out["pass"] is False and "UNKNOWN" in out["feedback"]

def test_consistent_passes_unknown_low_confidence(tmp_path):
    it = good_item(id="L1", category="UNKNOWN", confidence="low")
    out = run_check("ledger-consistent", good_evidence(ledger=[it]), tmp_path)
    assert out["pass"] is True

def test_consistent_fails_duplicate_ids(tmp_path):
    led = [good_item(id="L1"), good_item(id="L1")]
    out = run_check("ledger-consistent", good_evidence(ledger=led), tmp_path)
    assert out["pass"] is False and "L1" in out["feedback"]

# ---- read-these-first-consistent ----
def _spec_for(items):
    # A minimal spec that mentions every id + what (cross-ref must pass).
    lines = ["# Spec\n", "## Accountability Ledger\n"]
    for it in items:
        lines.append(f"- {it['id']}: {it['what']}\n")
    return "".join(lines)

def test_rtf_passes_when_highrisk_listed_and_ordered(tmp_path):
    hi = good_item(id="L1", confidence="low",
                   blast_radius={"level": "high", "why": "broad"},
                   reversibility={"level": "irreversible", "why": "published"})   # risk 6
    lo = good_item(id="L2")  # risk 0
    ev = good_evidence(ledger=[hi, lo], read_these_first=["L1"])
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": _spec_for([hi, lo])})
    assert out["pass"] is True, out

def test_rtf_fails_buried_highrisk(tmp_path):
    hi = good_item(id="L1", confidence="low",
                   blast_radius={"level": "high", "why": "broad"},
                   reversibility={"level": "irreversible", "why": "x"})
    ev = good_evidence(ledger=[hi], read_these_first=[])   # high-risk omitted
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": _spec_for([hi])})
    assert out["pass"] is False and "L1" in out["feedback"]

def test_rtf_fails_unknown_id(tmp_path):
    it = good_item(id="L1")
    ev = good_evidence(ledger=[it], read_these_first=["L9"])
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": _spec_for([it])})
    assert out["pass"] is False and "L9" in out["feedback"]

def test_rtf_fails_misordered(tmp_path):
    a = good_item(id="L1")  # risk 0
    b = good_item(id="L2", confidence="low",
                  blast_radius={"level": "high", "why": "x"},
                  reversibility={"level": "irreversible", "why": "x"})  # risk 6
    ev = good_evidence(ledger=[a, b], read_these_first=["L1", "L2"])  # ascending = wrong
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": _spec_for([a, b])})
    assert out["pass"] is False and "order" in out["feedback"].lower()

def test_rtf_fails_spec_divergence(tmp_path):
    it = good_item(id="L1", what="A decision the spec forgot")
    ev = good_evidence(ledger=[it], read_these_first=[])
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": "# Spec\n## Accountability Ledger\n(empty)\n"})
    assert out["pass"] is False and "spec" in out["feedback"].lower()

def test_rtf_fails_when_spec_missing(tmp_path):
    it = good_item(id="L1")
    ev = good_evidence(ledger=[it], read_these_first=[])
    out = run_check("read-these-first-consistent", ev, tmp_path)  # no spec.md bundled
    assert out["pass"] is False and "spec.md" in out["feedback"]

def test_rtf_allows_paraphrased_what(tmp_path):
    # The cross-ref anchors on the ledger ID, not verbatim `what` text: as long as
    # the spec's Ledger section enumerates the id, the prose may paraphrase the
    # concise JSON `what` (demanding an exact substring is brittle and fights the
    # model's natural prose — semantic divergence is the substance boundary, §8.3).
    it = good_item(id="L1", what="A decision phrased one way in JSON")
    ev = good_evidence(ledger=[it], read_these_first=[])
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": "# Spec\n## Accountability Ledger\n- L1: a totally different phrasing\n"})
    assert out["pass"] is True, out

def test_rtf_exits_zero_on_nondict_ledger_item(tmp_path):
    ev = {"ledger": [123], "read_these_first": []}
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": "# Spec\n"})
    # run_check asserts exit 0 internally; just confirm we got a verdict dict
    assert out["pass"] in (True, False)

# ---- spec-present ----
FULL_SPEC = """# Feature X — design
## Summary
...
## Scope
...
## Behavior / acceptance criteria
...
## Accountability Ledger
- L1: ...
## READ THESE FIRST
- L1
"""

def test_spec_present_passes_with_all_sections(tmp_path):
    out = run_check("spec-present", good_evidence(), tmp_path,
                    extra_files={"spec.md": FULL_SPEC})
    assert out["pass"] is True, out

def test_spec_present_fails_missing_section(tmp_path):
    spec = FULL_SPEC.replace("## READ THESE FIRST", "## Other")
    out = run_check("spec-present", good_evidence(), tmp_path, extra_files={"spec.md": spec})
    assert out["pass"] is False and "read these first" in out["feedback"].lower()

def test_spec_present_fails_no_spec_file(tmp_path):
    out = run_check("spec-present", good_evidence(), tmp_path)
    assert out["pass"] is False and "spec.md" in out["feedback"]

# ---- plan-present ----
def test_plan_present_passes(tmp_path):
    out = run_check("plan-present", good_evidence(), tmp_path,
                    extra_files={"plan.md": "# Plan\n## Task 1\n..."})
    assert out["pass"] is True, out

def test_plan_present_fails_no_plan_file(tmp_path):
    out = run_check("plan-present", good_evidence(), tmp_path)
    assert out["pass"] is False

def test_plan_present_fails_empty_plan_path(tmp_path):
    out = run_check("plan-present", good_evidence(plan_path=""), tmp_path,
                    extra_files={"plan.md": "# Plan\n"})
    assert out["pass"] is False and "plan_path" in out["feedback"]

# ---- implement-schema-valid ----
def test_impl_valid_passes(tmp_path):
    ev = {"summary": "Implemented feature X", "pr_branch": "impl-feature-auto/issue-42"}
    out = run_check("implement-schema-valid", ev, tmp_path)
    assert out["pass"] is True, out

def test_impl_valid_fails_bad_branch(tmp_path):
    ev = {"summary": "x", "pr_branch": "feature/whatever"}
    out = run_check("implement-schema-valid", ev, tmp_path)
    assert out["pass"] is False and "pr_branch" in out["feedback"]

def test_impl_valid_fails_missing_summary(tmp_path):
    ev = {"pr_branch": "impl-feature-auto/issue-1"}
    out = run_check("implement-schema-valid", ev, tmp_path)
    assert out["pass"] is False and "summary" in out["feedback"]
