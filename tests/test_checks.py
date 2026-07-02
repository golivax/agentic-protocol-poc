"""Port of tests/test-checks.sh — direct invocations of the three check scripts.

Bash assertion → pytest mapping
--------------------------------
schema-valid.py
  1.  assert_check schema-valid.py true  ""                      evidence-complete.json
      → test_schema_valid_complete_passes
  2.  assert_check schema-valid.py true  ""                      evidence-lazy.json
      → test_schema_valid_lazy_passes (structurally valid; coverage catches it)
  3.  assert_check schema-valid.py false "not valid JSON"        /dev/null
      → test_schema_valid_devnull_not_json
  4.  assert_check schema-valid.py false "no findings"           ev-nofindings.json
      → test_schema_valid_issues_found_no_findings
  5.  assert_check schema-valid.py false "illegal category"      ev-badcat.json
      → test_schema_valid_illegal_category
  6.  assert_check schema-valid.py false "no examined"           ev-noexam.json
      → test_schema_valid_none_found_no_examined
  7.  assert_check schema-valid.py false "not an object"         ev-strfile.json
      → test_schema_valid_file_not_object
  8.  assert_check schema-valid.py false "verdicts"              ev-badverdicts.json
      → test_schema_valid_verdicts_not_array
  9.  assert_check schema-valid.py false "anchor"                ev-noanchor.json
      → test_schema_valid_issues_found_no_anchor
  10. assert_check schema-valid.py false "anchor"                ev-strline.json
      → test_schema_valid_anchor_string_line
  11. assert_check schema-valid.py false "anchor"                ev-badside.json
      → test_schema_valid_anchor_bad_side
  12. assert_check schema-valid.py false "anchor"                ev-strstart.json
      → test_schema_valid_anchor_string_start_line
  13. assert_check schema-valid.py true  ""                      ev-okstart.json
      → test_schema_valid_anchor_int_start_line_passes
  14. assert_check schema-valid.py false "anchor"                ev-zeroline.json
      → test_schema_valid_anchor_line_zero_rejected
  15. inline: empty categories array → pass=false, feedback *no categories*
      → test_schema_valid_empty_categories_array
  16. inline: non-array categories → pass=false, feedback *no categories*
      → test_schema_valid_non_array_categories

rubric-coverage.py
  17. assert_check rubric-coverage.py true  ""                   evidence-complete.json
      → test_rubric_coverage_complete_passes
  18. assert_check rubric-coverage.py false "security × src/auth.js"   evidence-lazy.json
      → test_rubric_coverage_lazy_missing_security_auth
  19. assert_check rubric-coverage.py false "duplication × src/report.js" evidence-lazy.json
      → test_rubric_coverage_lazy_missing_dup_report
  20. assert_check rubric-coverage.py false "naming × src/auth.js"     ev-dup.json
      → test_rubric_coverage_duplicated_verdict
  21. assert_check rubric-coverage.py false "src/report.js"             evidence-lazy.json with no-newline files
      → test_rubric_coverage_files_no_trailing_newline
  22. inline: empty CHECK_PARAMS → pass=false, feedback *no categories*
      → test_rubric_coverage_missing_check_params
  23. inline: empty categories array → pass=false, feedback *no categories*
      → test_rubric_coverage_empty_categories_array
  24. inline: non-array categories → pass=false, feedback *no categories*
      → test_rubric_coverage_non_array_categories

traces-exist-in-diff.py
  25. assert_check traces-exist-in-diff.py true  ""             evidence-complete.json
      → test_traces_complete_passes
  26. assert_check traces-exist-in-diff.py false "does not match" evidence-fabricated.json
      → test_traces_fabricated_content_mismatch
  27. assert_check traces-exist-in-diff.py false "renderDashboard" evidence-fabricated.json
      → test_traces_fabricated_examined_missing
  28. correct RIGHT anchor passes
      → test_traces_anchor_right_passes
  29. correct LEFT anchor (deleted line) passes
      → test_traces_anchor_left_passes
  30. correct multi-line RIGHT range passes
      → test_traces_anchor_range_passes
  31. wrong line (content mismatch) fails
      → test_traces_anchor_wrong_line
  32. wrong side (added line on LEFT) fails
      → test_traces_anchor_wrong_side
  33. line outside any hunk fails
      → test_traces_anchor_no_such_line
  34. start_line >= line fails
      → test_traces_anchor_bad_range
  35. cross-hunk range fails
      → test_traces_anchor_cross_hunk
  36. examined identifier absent from renamed file's diff fails
      → test_traces_examined_path_not_in_diff
  37. missing args → clean JSON rejection, exit 0
      → test_traces_missing_args_clean_rejection
  38. deleted file: LEFT anchor on removed line resolves
      → test_traces_deleted_file_left_anchor
  39. deleted file: examined identifier from removed code resolves
      → test_traces_deleted_file_examined
"""

import json
import os
import subprocess

import pytest

from conftest import FIXTURES, PROTOCOLS, run_check

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

GRUMPY_CHECKS = PROTOCOLS / "code-review-v1/checks"
SCHEMA_VALID = GRUMPY_CHECKS / "schema-valid.py"
RUBRIC_COVERAGE = GRUMPY_CHECKS / "rubric-coverage.py"
TRACES = GRUMPY_CHECKS / "traces-exist-in-diff.py"

FIX_SCHEMA_VALID = PROTOCOLS / "code-review/checks/fix-schema-valid.py"

# Default grumpy rubric (matches the bash export at the top of test-checks.sh)
DEFAULT_PARAMS = {"categories": ["naming", "error-handling", "performance", "duplication", "security"]}

EV_COMPLETE = FIXTURES / "evidence-complete.json"
EV_LAZY = FIXTURES / "evidence-lazy.json"
EV_FABRICATED = FIXTURES / "evidence-fabricated.json"
DIFF_PR1 = FIXTURES / "diff-pr1.txt"
DIFF_PR2 = FIXTURES / "diff-pr2-deletions.txt"
DIFF_PR3 = FIXTURES / "diff-pr3-filedelete.txt"
FILES_PR1 = FIXTURES / "changed-files-pr1.txt"
FILES_PR2 = FIXTURES / "changed-files-pr2.txt"
FILES_PR3 = FIXTURES / "changed-files-pr3.txt"


# ---------------------------------------------------------------------------
# Convenience: run a check with DEFAULT_PARAMS unless overridden
# ---------------------------------------------------------------------------

def chk(script, evidence, diff=None, files=None, params=DEFAULT_PARAMS):
    """Wrapper: defaults diff/files to pr1 fixtures, params to DEFAULT_PARAMS."""
    return run_check(
        script,
        evidence,
        diff if diff is not None else DIFF_PR1,
        files if files is not None else FILES_PR1,
        check_params=params,
    )


# ===========================================================================
# schema-valid.py
# ===========================================================================

# 1
def test_schema_valid_complete_passes():
    """Bash assertion 1: schema-valid + evidence-complete → pass."""
    r = chk(SCHEMA_VALID, EV_COMPLETE)
    assert r["pass"] is True

# 2
def test_schema_valid_lazy_passes():
    """Bash assertion 2: schema-valid + evidence-lazy → pass (structurally valid; coverage catches it)."""
    r = chk(SCHEMA_VALID, EV_LAZY)
    assert r["pass"] is True

# 3
def test_schema_valid_devnull_not_json(tmp_path):
    """Bash assertion 3: /dev/null (empty file) → pass=false, feedback 'not valid JSON'."""
    # /dev/null produces empty content; we replicate with an empty temp file
    ev = tmp_path / "empty.json"
    ev.write_bytes(b"")
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is False
    assert "not valid JSON" in r["feedback"]

# 4
def test_schema_valid_issues_found_no_findings(tmp_path):
    """Bash assertion 4: issues-found verdict with empty findings array → pass=false, 'no findings'."""
    ev = tmp_path / "ev-nofindings.json"
    ev.write_text(json.dumps({
        "files": [{"path": "a.js", "verdicts": [
            {"category": "naming", "verdict": "issues-found", "findings": []}
        ]}]
    }))
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is False
    assert "no findings" in r["feedback"]

# 5
def test_schema_valid_illegal_category(tmp_path):
    """Bash assertion 5: unknown category 'vibes' → pass=false, 'illegal category'."""
    ev = tmp_path / "ev-badcat.json"
    ev.write_text(json.dumps({
        "files": [{"path": "a.js", "verdicts": [
            {"category": "vibes", "verdict": "none-found", "examined": ["x"]}
        ]}]
    }))
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is False
    assert "illegal category" in r["feedback"]

# 6
def test_schema_valid_none_found_no_examined(tmp_path):
    """Bash assertion 6: none-found without examined → pass=false, 'no examined'."""
    ev = tmp_path / "ev-noexam.json"
    ev.write_text(json.dumps({
        "files": [{"path": "a.js", "verdicts": [
            {"category": "naming", "verdict": "none-found"}
        ]}]
    }))
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is False
    assert "no examined" in r["feedback"]

# 7
def test_schema_valid_file_not_object(tmp_path):
    """Bash assertion 7: files array contains a string, not an object → 'not an object'."""
    ev = tmp_path / "ev-strfile.json"
    ev.write_text(json.dumps({"files": ["a.js"]}))
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is False
    assert "not an object" in r["feedback"]

# 8
def test_schema_valid_verdicts_not_array(tmp_path):
    """Bash assertion 8: verdicts is a string, not an array → pass=false, 'verdicts' in feedback."""
    ev = tmp_path / "ev-badverdicts.json"
    ev.write_text(json.dumps({
        "files": [{"path": "a.js", "verdicts": "oops"}]
    }))
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is False
    assert "verdicts" in r["feedback"]

# 9
def test_schema_valid_issues_found_no_anchor(tmp_path):
    """Bash assertion 9: issues-found finding with no anchor fields → 'anchor'."""
    ev = tmp_path / "ev-noanchor.json"
    ev.write_text(json.dumps({
        "files": [{"path": "a.js", "verdicts": [
            {"category": "naming", "verdict": "issues-found", "findings": [
                {"existing_code": "x", "comment": "y"}
            ]}
        ]}]
    }))
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is False
    assert "anchor" in r["feedback"]

# 10
def test_schema_valid_anchor_string_line(tmp_path):
    """Bash assertion 10: line is a string "3" instead of int → 'anchor'."""
    ev = tmp_path / "ev-strline.json"
    ev.write_text(json.dumps({
        "files": [{"path": "a.js", "verdicts": [
            {"category": "naming", "verdict": "issues-found", "findings": [
                {"existing_code": "x", "comment": "y", "side": "RIGHT", "line": "3"}
            ]}
        ]}]
    }))
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is False
    assert "anchor" in r["feedback"]

# 11
def test_schema_valid_anchor_bad_side(tmp_path):
    """Bash assertion 11: side is 'UP' (not LEFT/RIGHT) → 'anchor'."""
    ev = tmp_path / "ev-badside.json"
    ev.write_text(json.dumps({
        "files": [{"path": "a.js", "verdicts": [
            {"category": "naming", "verdict": "issues-found", "findings": [
                {"existing_code": "x", "comment": "y", "side": "UP", "line": 3}
            ]}
        ]}]
    }))
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is False
    assert "anchor" in r["feedback"]

# 12
def test_schema_valid_anchor_string_start_line(tmp_path):
    """Bash assertion 12: start_line is a string "two" → 'anchor'."""
    ev = tmp_path / "ev-strstart.json"
    ev.write_text(json.dumps({
        "files": [{"path": "a.js", "verdicts": [
            {"category": "naming", "verdict": "issues-found", "findings": [
                {"existing_code": "x", "comment": "y", "side": "RIGHT", "line": 5, "start_line": "two"}
            ]}
        ]}]
    }))
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is False
    assert "anchor" in r["feedback"]

# 13
def test_schema_valid_anchor_int_start_line_passes(tmp_path):
    """Bash assertion 13: integer start_line must be accepted → pass=true."""
    ev = tmp_path / "ev-okstart.json"
    ev.write_text(json.dumps({
        "files": [{"path": "a.js", "verdicts": [
            {"category": "naming", "verdict": "issues-found", "findings": [
                {"existing_code": "x", "comment": "y", "side": "RIGHT", "line": 5, "start_line": 3}
            ]}
        ]}]
    }))
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is True

# 14
def test_schema_valid_anchor_line_zero_rejected(tmp_path):
    """Bash assertion 14: line=0 must be rejected (minimum is 1) → 'anchor'."""
    ev = tmp_path / "ev-zeroline.json"
    ev.write_text(json.dumps({
        "files": [{"path": "a.js", "verdicts": [
            {"category": "naming", "verdict": "issues-found", "findings": [
                {"existing_code": "x", "comment": "y", "side": "RIGHT", "line": 0}
            ]}
        ]}]
    }))
    r = chk(SCHEMA_VALID, ev)
    assert r["pass"] is False
    assert "anchor" in r["feedback"]

# 15
def test_schema_valid_empty_categories_array():
    """Bash assertion 15: empty categories array → pass=false, 'no categories' (not 'illegal category')."""
    r = chk(SCHEMA_VALID, EV_COMPLETE, params={"categories": []})
    assert r["pass"] is False
    assert "no categories" in r["feedback"]

# 16
def test_schema_valid_non_array_categories():
    """Bash assertion 16: non-array categories (string) → pass=false, 'no categories'."""
    # Pass as raw JSON string so categories is a JSON string not an array
    r = run_check(
        SCHEMA_VALID, EV_COMPLETE, DIFF_PR1, FILES_PR1,
        check_params='{"categories":"naming"}',
    )
    assert r["pass"] is False
    assert "no categories" in r["feedback"]


# ===========================================================================
# rubric-coverage.py
# ===========================================================================

# 17
def test_rubric_coverage_complete_passes():
    """Bash assertion 17: rubric-coverage + evidence-complete → pass."""
    r = chk(RUBRIC_COVERAGE, EV_COMPLETE)
    assert r["pass"] is True

# 18
def test_rubric_coverage_lazy_missing_security_auth():
    """Bash assertion 18: evidence-lazy missing security verdict for src/auth.js → fail, 'security × src/auth.js'."""
    r = chk(RUBRIC_COVERAGE, EV_LAZY)
    assert r["pass"] is False
    assert "security × src/auth.js" in r["feedback"]

# 19
def test_rubric_coverage_lazy_missing_dup_report():
    """Bash assertion 19: evidence-lazy missing duplication verdict for src/report.js → fail, 'duplication × src/report.js'."""
    r = chk(RUBRIC_COVERAGE, EV_LAZY)
    assert r["pass"] is False
    assert "duplication × src/report.js" in r["feedback"]

# 20
def test_rubric_coverage_duplicated_verdict(tmp_path):
    """Bash assertion 20: duplicated verdict for one cell → fail, 'naming × src/auth.js'."""
    ev_complete = json.loads(EV_COMPLETE.read_text())
    # Duplicate the first verdict of the first file
    first_verdict = ev_complete["files"][0]["verdicts"][0]
    ev_complete["files"][0]["verdicts"].append(first_verdict)
    ev = tmp_path / "ev-dup.json"
    ev.write_text(json.dumps(ev_complete))
    r = chk(RUBRIC_COVERAGE, ev)
    assert r["pass"] is False
    assert "naming × src/auth.js" in r["feedback"]

# 21
def test_rubric_coverage_files_no_trailing_newline(tmp_path):
    """Bash assertion 21: changed-files without trailing newline must not exempt last file."""
    files_no_newline = tmp_path / "files-nonewline.txt"
    files_no_newline.write_text("src/auth.js\nsrc/report.js")  # no trailing newline
    r = run_check(RUBRIC_COVERAGE, EV_LAZY, DIFF_PR1, files_no_newline, check_params=DEFAULT_PARAMS)
    assert r["pass"] is False
    assert "src/report.js" in r["feedback"]

# 22
def test_rubric_coverage_missing_check_params():
    """Bash assertion 22: empty CHECK_PARAMS → pass=false, 'no categories'."""
    r = run_check(RUBRIC_COVERAGE, EV_COMPLETE, DIFF_PR1, FILES_PR1, check_params="")
    assert r["pass"] is False
    assert "no categories" in r["feedback"]

# 23
def test_rubric_coverage_empty_categories_array():
    """Bash assertion 23: empty categories array → pass=false, 'no categories'."""
    r = chk(RUBRIC_COVERAGE, EV_COMPLETE, params={"categories": []})
    assert r["pass"] is False
    assert "no categories" in r["feedback"]

# 24
def test_rubric_coverage_non_array_categories():
    """Bash assertion 24: non-array categories (string) → pass=false, 'no categories'."""
    r = run_check(
        RUBRIC_COVERAGE, EV_COMPLETE, DIFF_PR1, FILES_PR1,
        check_params='{"categories":"naming"}',
    )
    assert r["pass"] is False
    assert "no categories" in r["feedback"]


# ===========================================================================
# traces-exist-in-diff.py
# ===========================================================================

# 25
def test_traces_complete_passes():
    """Bash assertion 25: traces + evidence-complete → pass."""
    r = chk(TRACES, EV_COMPLETE)
    assert r["pass"] is True

# 26
def test_traces_fabricated_content_mismatch():
    """Bash assertion 26: evidence-fabricated has snippet mismatch → fail, 'does not match'."""
    r = chk(TRACES, EV_FABRICATED)
    assert r["pass"] is False
    assert "does not match" in r["feedback"]

# 27
def test_traces_fabricated_examined_missing():
    """Bash assertion 27: evidence-fabricated has fabricated examined id → fail, 'renderDashboard'."""
    r = chk(TRACES, EV_FABRICATED)
    assert r["pass"] is False
    assert "renderDashboard" in r["feedback"]

# 28
def test_traces_anchor_right_passes(tmp_path):
    """Bash assertion 28: correct single-line RIGHT anchor passes (pr2 diff)."""
    ev = tmp_path / "ev-anc-right.json"
    ev.write_text(json.dumps({"files": [{"path": "src/cache.js", "verdicts": [
        {"category": "naming", "verdict": "issues-found", "findings": [
            {"existing_code": "function set(key, value) {", "comment": "name it", "side": "RIGHT", "line": 6}
        ]}
    ]}]}))
    r = chk(TRACES, ev, diff=DIFF_PR2, files=FILES_PR2)
    assert r["pass"] is True

# 29
def test_traces_anchor_left_passes(tmp_path):
    """Bash assertion 29: correct LEFT anchor (deleted line) passes (pr2 diff)."""
    ev = tmp_path / "ev-anc-left.json"
    ev.write_text(json.dumps({"files": [{"path": "src/cache.js", "verdicts": [
        {"category": "naming", "verdict": "issues-found", "findings": [
            {"existing_code": "function set(key, val) {", "comment": "why", "side": "LEFT", "line": 6}
        ]}
    ]}]}))
    r = chk(TRACES, ev, diff=DIFF_PR2, files=FILES_PR2)
    assert r["pass"] is True

# 30
def test_traces_anchor_range_passes(tmp_path):
    """Bash assertion 30: correct multi-line RIGHT range passes (pr2 diff)."""
    ev = tmp_path / "ev-anc-range.json"
    ev.write_text(json.dumps({"files": [{"path": "src/cache.js", "verdicts": [
        {"category": "duplication", "verdict": "issues-found", "findings": [
            {"existing_code": "function get(key) {\n  return store[key];\n}",
             "comment": "blk", "side": "RIGHT", "start_line": 3, "line": 5}
        ]}
    ]}]}))
    r = chk(TRACES, ev, diff=DIFF_PR2, files=FILES_PR2)
    assert r["pass"] is True

# 31
def test_traces_anchor_wrong_line(tmp_path):
    """Bash assertion 31: wrong line (content mismatch) fails → 'does not match'."""
    ev = tmp_path / "ev-anc-wrongline.json"
    ev.write_text(json.dumps({"files": [{"path": "src/cache.js", "verdicts": [
        {"category": "naming", "verdict": "issues-found", "findings": [
            {"existing_code": "function set(key, value) {", "comment": "x", "side": "RIGHT", "line": 7}
        ]}
    ]}]}))
    r = chk(TRACES, ev, diff=DIFF_PR2, files=FILES_PR2)
    assert r["pass"] is False
    assert "does not match" in r["feedback"]

# 32
def test_traces_anchor_wrong_side(tmp_path):
    """Bash assertion 32: wrong side (added line claimed on LEFT) fails → 'does not match'."""
    ev = tmp_path / "ev-anc-wrongside.json"
    ev.write_text(json.dumps({"files": [{"path": "src/cache.js", "verdicts": [
        {"category": "naming", "verdict": "issues-found", "findings": [
            {"existing_code": "function set(key, value) {", "comment": "x", "side": "LEFT", "line": 6}
        ]}
    ]}]}))
    r = chk(TRACES, ev, diff=DIFF_PR2, files=FILES_PR2)
    assert r["pass"] is False
    assert "does not match" in r["feedback"]

# 33
def test_traces_anchor_no_such_line(tmp_path):
    """Bash assertion 33: line 99 outside any hunk → fail, 'not on RIGHT'."""
    ev = tmp_path / "ev-anc-noline.json"
    ev.write_text(json.dumps({"files": [{"path": "src/cache.js", "verdicts": [
        {"category": "naming", "verdict": "issues-found", "findings": [
            {"existing_code": "whatever", "comment": "x", "side": "RIGHT", "line": 99}
        ]}
    ]}]}))
    r = chk(TRACES, ev, diff=DIFF_PR2, files=FILES_PR2)
    assert r["pass"] is False
    assert "not on RIGHT" in r["feedback"]

# 34
def test_traces_anchor_bad_range(tmp_path):
    """Bash assertion 34: start_line >= line fails → 'must be <'."""
    ev = tmp_path / "ev-anc-badrange.json"
    ev.write_text(json.dumps({"files": [{"path": "src/cache.js", "verdicts": [
        {"category": "naming", "verdict": "issues-found", "findings": [
            {"existing_code": "x", "comment": "x", "side": "RIGHT", "start_line": 5, "line": 5}
        ]}
    ]}]}))
    r = chk(TRACES, ev, diff=DIFF_PR2, files=FILES_PR2)
    assert r["pass"] is False
    assert "must be <" in r["feedback"]

# 35
def test_traces_anchor_cross_hunk(tmp_path):
    """Bash assertion 35: cross-hunk range fails → 'contiguous'."""
    ev = tmp_path / "ev-anc-crosshunk.json"
    ev.write_text(json.dumps({"files": [{"path": "src/cache.js", "verdicts": [
        {"category": "naming", "verdict": "issues-found", "findings": [
            {"existing_code": "irrelevant", "comment": "x", "side": "RIGHT", "start_line": 5, "line": 22}
        ]}
    ]}]}))
    r = chk(TRACES, ev, diff=DIFF_PR2, files=FILES_PR2)
    assert r["pass"] is False
    assert "contiguous" in r["feedback"]

# 36
def test_traces_examined_path_not_in_diff(tmp_path):
    """Bash assertion 36: examined id absent from file's diff (path renamed) → fail, 'login'."""
    # Rename src/auth.js -> src/authXjs in a copy of diff-pr1.txt
    diff_xjs = tmp_path / "diff-xjs.txt"
    original = (FIXTURES / "diff-pr1.txt").read_text()
    patched = original.replace("b/src/auth.js", "b/src/authXjs").replace("a/src/auth.js", "a/src/authXjs")
    diff_xjs.write_text(patched)

    ev = tmp_path / "ev-regexpath.json"
    ev.write_text(json.dumps({"files": [{"path": "src/auth.js", "verdicts": [
        {"category": "naming", "verdict": "none-found", "examined": ["login"]}
    ]}]}))
    r = chk(TRACES, ev, diff=diff_xjs, files=FILES_PR1)
    assert r["pass"] is False
    assert "login" in r["feedback"]

# 37
def test_traces_missing_args_clean_rejection():
    """Bash assertion 37: missing args → clean JSON rejection, exit 0 (ABI contract)."""
    env = dict(os.environ)
    env["CHECK_PARAMS"] = json.dumps(DEFAULT_PARAMS)
    r = subprocess.run(
        ["python3", str(TRACES)],
        text=True,
        capture_output=True,
        env=env,
    )
    assert r.returncode == 0
    result = json.loads(r.stdout)
    assert result["pass"] is False

# 38
def test_traces_deleted_file_left_anchor(tmp_path):
    """Bash assertion 38: deleted file: LEFT anchor on removed line resolves (regression guard)."""
    ev = tmp_path / "ev-del-left.json"
    ev.write_text(json.dumps({"files": [{"path": "src/legacy.js", "verdicts": [
        {"category": "naming", "verdict": "issues-found", "findings": [
            {"existing_code": "function legacy(a) {", "comment": "bad name", "side": "LEFT", "line": 1}
        ]}
    ]}]}))
    r = chk(TRACES, ev, diff=DIFF_PR3, files=FILES_PR3)
    assert r["pass"] is True

# 39
def test_traces_deleted_file_examined(tmp_path):
    """Bash assertion 39: deleted file: examined identifier from removed code resolves."""
    ev = tmp_path / "ev-del-exam.json"
    ev.write_text(json.dumps({"files": [{"path": "src/legacy.js", "verdicts": [
        {"category": "naming", "verdict": "none-found", "examined": ["legacy"]}
    ]}]}))
    r = chk(TRACES, ev, diff=DIFF_PR3, files=FILES_PR3)
    assert r["pass"] is True


# ===========================================================================
# fix-schema-valid.py — optional original_line guard
# ===========================================================================

def _minimal_fix_ev(**extra):
    """Build a minimal valid fix evidence, merging extra keys into the first fix."""
    fix = {"cluster_id": "c1", "path": "a.py", "line": 1,
           "rationale": "r", "suggested_patch": "x = 1"}
    fix.update(extra)
    return {"mode": "suggest", "fixes": [fix]}


def _run_fix_schema_valid(tmp_path, evidence_dict):
    """Write evidence to a temp file and run fix-schema-valid.py against it."""
    ev = tmp_path / "fix-ev.json"
    ev.write_text(json.dumps(evidence_dict))
    # fix-schema-valid is a check; pass dummy diff/files (it ignores them)
    dummy = tmp_path / "dummy.txt"
    dummy.write_text("")
    return run_check(FIX_SCHEMA_VALID, ev, dummy, dummy, check_params=None)


# 40
def test_fix_schema_valid_optional_original_line(tmp_path):
    """original_line is optional: absent → pass; non-empty string → pass; empty string → fail."""
    # absent original_line: must pass
    r = _run_fix_schema_valid(tmp_path, _minimal_fix_ev())
    assert r["pass"] is True, f"absent original_line should pass, got: {r['feedback']}"

    # non-empty string original_line: must pass
    r = _run_fix_schema_valid(tmp_path, _minimal_fix_ev(original_line="x = 0"))
    assert r["pass"] is True, f"non-empty original_line should pass, got: {r['feedback']}"

    # empty string original_line: must fail with specific message
    r = _run_fix_schema_valid(tmp_path, _minimal_fix_ev(original_line=""))
    assert r["pass"] is False
    assert "original_line must be a non-empty string when present" in r["feedback"]
