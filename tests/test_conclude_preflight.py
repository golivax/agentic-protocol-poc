import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/code-review-pipeline/publish/conclude-preflight.py"


def _conclude(evidence_obj, blocking, tmp_path):
    ev = tmp_path / "evidence.json"; ev.write_text(json.dumps(evidence_obj))
    env = dict(os.environ)
    env["BLOCKING"] = "1" if blocking else "0"   # matches advance.py's contract
    env["VERDICT_OUT"] = str(tmp_path / "verdict.json")  # see hook: honor override for tests
    r = subprocess.run(["python3", str(HOOK), str(ev), "pr-7"],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout), (tmp_path / "verdict.json")


def test_clear_when_no_blocking_and_all_pass(tmp_path):
    ev = {"checks": [{"id": "spec-adherence", "status": "pass"}], "examined": ["a"]}
    out, vpath = _conclude(ev, blocking=False, tmp_path=tmp_path)
    assert out["blocked"] is False and out["conclusion"] != "blocked"


def test_blocked_when_blocking_true(tmp_path):
    ev = {"checks": [{"id": "spec-adherence", "status": "pass"}], "examined": ["a"]}
    out, _ = _conclude(ev, blocking=True, tmp_path=tmp_path)
    assert out["blocked"] is True


def test_blocked_when_adherence_fails(tmp_path):
    ev = {"checks": [{"id": "spec-adherence", "status": "fail", "summary": "nope"}], "examined": ["a"]}
    out, _ = _conclude(ev, blocking=False, tmp_path=tmp_path)
    assert out["blocked"] is True


def test_verdict_json_shape(tmp_path):
    ev = {"checks": [{"id": "spec-adherence", "status": "pass"}], "examined": ["a"]}
    _out, vpath = _conclude(ev, blocking=False, tmp_path=tmp_path)
    v = json.loads(vpath.read_text())
    assert "records" in v and isinstance(v["records"], list)
    assert any(r.get("type") == "verdict" for r in v["records"])
