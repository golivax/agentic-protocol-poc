import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / ".github/agent-factory/protocols/code-review/checks"
sys.path.insert(0, str(CHECKS))

import _locate  # noqa: E402


# --- parse_closing_issue_refs: closing-keyword detection --------------------

def test_closes_single_issue():
    assert _locate.parse_closing_issue_refs("Closes #42") == [42]

def test_fixes_keyword():
    assert _locate.parse_closing_issue_refs("This Fixes #7 in the parser") == [7]

def test_resolves_keyword():
    assert _locate.parse_closing_issue_refs("Resolves #123") == [123]

def test_keyword_case_insensitive():
    assert _locate.parse_closing_issue_refs("CLOSES #5 and fixes #6") == [5, 6]

def test_optional_colon_after_keyword():
    assert _locate.parse_closing_issue_refs("Closes: #9") == [9]

def test_multiple_issues_order_preserved_and_deduped():
    body = "Fixes #3\nAlso closes #10\nand again Resolves #3"
    assert _locate.parse_closing_issue_refs(body) == [3, 10]

def test_no_closing_keyword_returns_empty():
    # a bare "#12" mention is NOT a closing reference
    assert _locate.parse_closing_issue_refs("see #12 for context") == []

def test_none_and_empty_body():
    assert _locate.parse_closing_issue_refs(None) == []
    assert _locate.parse_closing_issue_refs("") == []

def test_keyword_must_be_whole_word():
    # "Forecloses" must not match "closes"
    assert _locate.parse_closing_issue_refs("Forecloses #8") == []

def test_detect_issue_link_first_ref():
    assert _locate.detect_issue_link("Fixes #3 and closes #10") == 3

def test_detect_issue_link_none_when_no_keyword():
    assert _locate.detect_issue_link("see #12 for context") is None


# --- locate: chain drops the PR-description spec fallback --------------------

def test_locate_spec_default_keeps_description_fallback():
    # existing callers (default allow_body_fallback=True) are unaffected:
    # a body-only PR still resolves spec via the description.
    r = _locate.locate("spec", "Some prose description, no spec file.", ["src/app.py"])
    assert r["found"] is True and r["source"] == "pr-description"

def test_locate_spec_chain_drops_description_fallback():
    # chain mode: only a PR description, no committed spec file -> NOT found.
    r = _locate.locate("spec", "Some prose description, no spec file.",
                       ["src/app.py"], allow_body_fallback=False)
    assert r["found"] is False and r["source"] is None

def test_locate_spec_chain_still_finds_committed_file():
    # a real committed spec file is found regardless of the fallback flag.
    r = _locate.locate("spec", "", ["docs/superpowers/specs/x.md", "src/app.py"],
                       allow_body_fallback=False)
    assert r["found"] is True and r["source"] == "file"

def test_locate_spec_chain_still_finds_body_section():
    # a structured requirements section is association, not the fallback claim.
    body = "## Requirements\n- must parse links\n"
    r = _locate.locate("spec", body, ["src/app.py"], allow_body_fallback=False)
    assert r["found"] is True and r["source"] == "body-section"

def test_locate_plan_unaffected_by_flag():
    # plan never had a fallback; the flag is a no-op for plan.
    r = _locate.locate("plan", "prose only", ["src/app.py"], allow_body_fallback=False)
    assert r["found"] is False
