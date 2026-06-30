import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTO = ROOT / ".github/agent-factory/protocols/code-review-demo/protocol.json"
LINT = ROOT / ".github/agent-factory/engine/protocol-lint.py"


def test_demo_protocol_name_and_sequence():
    p = json.load(open(PROTO))
    assert p["name"] == "code-review-demo"
    ids = [s["id"] for s in p["states"]]
    assert ids == ["review", "join-review", "triage", "fix"]
    fix = next(s for s in p["states"] if s["id"] == "fix")
    assert fix["next"] == "done"


def test_demo_review_legs_open_issues_no_publish():
    p = json.load(open(PROTO))
    review = next(s for s in p["states"] if s["id"] == "review")
    assert len(review["branches"]) == 5
    for b in review["branches"]:
        assert "publish" not in b, f"{b['id']} must not post a PR review (issues-only)"
        assert b["max_iterations"] == 1, f"{b['id']} must be single-pass (issue idempotency)"
        assert b["workflow"] == f"demo-review-{b['id']}-agent"


def test_demo_protocol_lint_clean():
    r = subprocess.run([sys.executable, str(LINT), str(PROTO)],
                       text=True, capture_output=True)
    assert r.returncode == 0, f"protocol-lint failed:\n{r.stdout}\n{r.stderr}"
