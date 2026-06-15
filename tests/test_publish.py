"""Port of tests/test-publish.sh — unit tests for the publication hooks.

The hooks are run with ENGINE_LOCAL=1 (dry-run): in that mode, the hook prints
the would-be review payload as indented JSON to stderr (preceded by a header
line starting with "[ENGINE_LOCAL]") and prints {"conclusion","summary"} to
stdout.  We capture both streams and parse them.

Bash assertion → pytest mapping
--------------------------------
Grumpy hook — evidence with issues (ev-pub, 3 findings):
  1.  check "event is REQUEST_CHANGES"
      → test_grumpy_issues_event_is_request_changes
  2.  check "three inline comments"
      → test_grumpy_issues_three_inline_comments
  3.  check "single-line comment shape"
      → test_grumpy_issues_single_line_comment_shape
  4.  check "range comment has start_line"
      → test_grumpy_issues_range_comment_has_start_line
  5.  check "LEFT comment side"
      → test_grumpy_issues_left_comment_side
  6.  check "body is a short overview"  (.body test("Grumpy") and test("inline"))
      → test_grumpy_issues_body_contains_grumpy_and_inline

Grumpy hook — clean evidence (no issues):
  7.  check "clean → APPROVE"
      → test_grumpy_clean_approve
  8.  check "clean → no comments"
      → test_grumpy_clean_no_comments

Security hook — evidence-security.json (stderr payload, 1 finding):
  9.  check "publish-security: event is REQUEST_CHANGES"
      → test_security_event_is_request_changes
  10. check "publish-security: body has security heading"  (.body test("🔒"))
      → test_security_body_has_lock_emoji
  11. check "publish-security: has one inline comment"
      → test_security_one_inline_comment

Security hook — stdout:
  12. check "publish-security: conclusion=failure"
      → test_security_conclusion_failure
  13. check "publish-security: summary non-empty"
      → test_security_summary_nonempty
"""

import json
import os
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

import pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTOCOLS = ROOT / ".github/agent-factory/protocols"
GRUMPY_HOOK = PROTOCOLS / "grumpy/publish/publish-review-from-evidence.py"
SECURITY_HOOK = PROTOCOLS / "multi-grumpy/publish/publish-security.py"
FIXTURES = ROOT / "tests/fixtures"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def run_publish_hook(hook_path, evidence_path, instance_key="pr-8"):
    """Run a publish hook under ENGINE_LOCAL=1 and return (stdout_json, stderr_payload).

    stdout_json  : parsed dict from the hook's stdout  ({"conclusion","summary"})
    stderr_payload: parsed dict of the review body the hook would POST (extracted
                    from stderr — the hook writes a header line then indented JSON).
    """
    env = dict(os.environ)
    env["ENGINE_LOCAL"] = "1"
    env["GITHUB_REPOSITORY"] = "acme/repo"
    env["PR"] = "8"
    env.pop("PUBLISH_TOKEN", None)   # not needed in dry-run mode

    result = subprocess.run(
        ["python3", str(hook_path), str(evidence_path), instance_key],
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, f"hook failed: {result.stderr}"

    # Parse stdout: {"conclusion", "summary"}
    stdout_json = json.loads(result.stdout.strip())

    # Parse stderr payload: skip lines until we hit the opening '{', then parse
    # the rest as JSON (mirrors bash: sed -n '/^{/,$p').
    stderr_lines = result.stderr.splitlines()
    payload_lines = []
    capture = False
    for line in stderr_lines:
        if not capture and line.startswith("{"):
            capture = True
        if capture:
            payload_lines.append(line)
    assert payload_lines, f"no JSON payload found in stderr:\n{result.stderr}"
    stderr_payload = json.loads("\n".join(payload_lines))

    return stdout_json, stderr_payload


# ---------------------------------------------------------------------------
# Fixtures — inline evidence (mirrors the bash heredocs exactly)
# ---------------------------------------------------------------------------

EVIDENCE_ISSUES = {
    "files": [
        {
            "path": "src/cache.js",
            "verdicts": [
                {
                    "category": "naming",
                    "verdict": "issues-found",
                    "findings": [
                        {
                            "existing_code": "function set(key, value) {",
                            "comment": "rename it",
                            "side": "RIGHT",
                            "line": 6,
                        }
                    ],
                },
                {
                    "category": "duplication",
                    "verdict": "issues-found",
                    "findings": [
                        {
                            "existing_code": "block",
                            "comment": "dup block",
                            "side": "RIGHT",
                            "start_line": 3,
                            "line": 5,
                        }
                    ],
                },
                {
                    "category": "performance",
                    "verdict": "issues-found",
                    "findings": [
                        {
                            "existing_code": "function set(key, val) {",
                            "comment": "why removed",
                            "side": "LEFT",
                            "line": 6,
                        }
                    ],
                },
            ],
        }
    ]
}

EVIDENCE_CLEAN = {
    "files": [
        {
            "path": "src/cache.js",
            "verdicts": [
                {
                    "category": "naming",
                    "verdict": "none-found",
                    "examined": ["get", "set"],
                }
            ],
        }
    ]
}


@pytest.fixture
def ev_issues_file(tmp_path):
    p = tmp_path / "ev-pub.json"
    p.write_text(json.dumps(EVIDENCE_ISSUES))
    return p


@pytest.fixture
def ev_clean_file(tmp_path):
    p = tmp_path / "ev-clean.json"
    p.write_text(json.dumps(EVIDENCE_CLEAN))
    return p


# ---------------------------------------------------------------------------
# Tests — grumpy hook, evidence with issues
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def grumpy_issues_result(tmp_path_factory):
    p = tmp_path_factory.mktemp("ev") / "ev-pub.json"
    p.write_text(json.dumps(EVIDENCE_ISSUES))
    return run_publish_hook(GRUMPY_HOOK, p)


def test_grumpy_issues_event_is_request_changes(grumpy_issues_result):
    """Bash assertion 1: event is REQUEST_CHANGES."""
    _, payload = grumpy_issues_result
    assert payload["event"] == "REQUEST_CHANGES"


def test_grumpy_issues_three_inline_comments(grumpy_issues_result):
    """Bash assertion 2: three inline comments."""
    _, payload = grumpy_issues_result
    assert len(payload["comments"]) == 3


def test_grumpy_issues_single_line_comment_shape(grumpy_issues_result):
    """Bash assertion 3: single-line comment shape.

    .comments[0] == {path:"src/cache.js", side:"RIGHT", line:6, body:"rename it"}
    """
    _, payload = grumpy_issues_result
    c = payload["comments"][0]
    assert c == {"path": "src/cache.js", "side": "RIGHT", "line": 6, "body": "rename it"}


def test_grumpy_issues_range_comment_has_start_line(grumpy_issues_result):
    """Bash assertion 4: range comment has start_line and start_side.

    .comments[1].start_line == 3 and .comments[1].start_side == "RIGHT" and .comments[1].line == 5
    """
    _, payload = grumpy_issues_result
    c = payload["comments"][1]
    assert c["start_line"] == 3
    assert c["start_side"] == "RIGHT"
    assert c["line"] == 5


def test_grumpy_issues_left_comment_side(grumpy_issues_result):
    """Bash assertion 5: LEFT comment side.

    .comments[2].side == "LEFT" and .comments[2].line == 6
    """
    _, payload = grumpy_issues_result
    c = payload["comments"][2]
    assert c["side"] == "LEFT"
    assert c["line"] == 6


def test_grumpy_issues_body_contains_grumpy_and_inline(grumpy_issues_result):
    """Bash assertion 6: body is a short overview.

    (.body | test("Grumpy")) and (.body | test("inline"))
    """
    _, payload = grumpy_issues_result
    body = payload["body"]
    assert "Grumpy" in body
    assert "inline" in body


# ---------------------------------------------------------------------------
# Tests — grumpy hook, clean evidence
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def grumpy_clean_result(tmp_path_factory):
    p = tmp_path_factory.mktemp("ev") / "ev-clean.json"
    p.write_text(json.dumps(EVIDENCE_CLEAN))
    return run_publish_hook(GRUMPY_HOOK, p)


def test_grumpy_clean_approve(grumpy_clean_result):
    """Bash assertion 7: clean → APPROVE."""
    _, payload = grumpy_clean_result
    assert payload["event"] == "APPROVE"


def test_grumpy_clean_no_comments(grumpy_clean_result):
    """Bash assertion 8: clean → no comments."""
    _, payload = grumpy_clean_result
    assert len(payload["comments"]) == 0


# ---------------------------------------------------------------------------
# Tests — security hook (uses fixtures/evidence-security.json)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def security_result():
    return run_publish_hook(SECURITY_HOOK, FIXTURES / "evidence-security.json")


def test_security_event_is_request_changes(security_result):
    """Bash assertion 9: publish-security: event is REQUEST_CHANGES."""
    _, payload = security_result
    assert payload["event"] == "REQUEST_CHANGES"


def test_security_body_has_lock_emoji(security_result):
    """Bash assertion 10: publish-security: body has security heading (🔒)."""
    _, payload = security_result
    assert "\U0001f512" in payload["body"]


def test_security_one_inline_comment(security_result):
    """Bash assertion 11: publish-security: has one inline comment."""
    _, payload = security_result
    assert len(payload["comments"]) == 1


def test_security_conclusion_failure(security_result):
    """Bash assertion 12: publish-security: conclusion=failure."""
    stdout_json, _ = security_result
    assert stdout_json["conclusion"] == "failure"


def test_security_summary_nonempty(security_result):
    """Bash assertion 13: publish-security: summary non-empty."""
    stdout_json, _ = security_result
    assert len(stdout_json["summary"]) > 0
