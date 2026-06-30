import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/code-review-demo/publish/conclude-triage.py"


def test_comment_lists_linked_issue_keys(tmp_path):
    evidence = tmp_path / "triage.json"
    evidence.write_text(json.dumps({
        "clusters": [{"cluster_id": "c1", "title": "Bad default",
                      "dimension": ["correctness"], "severity": "high",
                      "paths": ["a.py"], "rank": 1,
                      "member_findings": [{"dimension": "correctness", "path": "a.py",
                                           "line": 1, "severity": "high", "title": "Bad default"}]}],
        "summary": {"present": ["correctness"], "missing": ["test", "performance", "security", "maintainability"],
                    "clusters": 1, "total_findings": 1, "by_severity": {"high": 1},
                    "by_dimension": {"correctness": 1}}
    }))
    comment_out = tmp_path / "comment.txt"
    env = dict(os.environ)
    env.update({"ENGINE_LOCAL": "1", "TRIAGE_COMMENT_OUT": str(comment_out),
                "GITHUB_REPOSITORY": "acme/repo", "PR": "8"})
    r = subprocess.run(["python3", str(HOOK), str(evidence), "pr-8"],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    body = comment_out.read_text()
    assert "review:correctness" in body and "Bad default" in body
