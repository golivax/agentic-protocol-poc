import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/code-review-demo/publish/conclude-fix.py"


def _run(env, evidence_path, instance="pr-8"):
    r = subprocess.run(["python3", str(HOOK), str(evidence_path), instance],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip())


def test_apply_writes_files_and_records_closes(tmp_path):
    # workdir = a fake PR head checkout
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "a.py").write_text("x = 0\n")

    # triage input (cluster c1 → a correctness finding titled "Bad default")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "triage.json").write_text(json.dumps({
        "clusters": [{
            "cluster_id": "c1", "title": "Bad default", "dimension": ["correctness"],
            "severity": "high", "paths": ["a.py"], "rank": 1,
            "member_findings": [{"dimension": "correctness", "path": "a.py",
                                 "line": 1, "severity": "high", "title": "Bad default"}]
        }]
    }))

    evidence = tmp_path / "fix.json"
    evidence.write_text(json.dumps({
        "mode": "suggest", "skipped": [],
        "fixes": [{"cluster_id": "c1", "path": "a.py", "line": 1,
                   "rationale": "default should be 1", "suggested_patch": "x = 1",
                   "original_line": "x = 0"}]
    }))

    apply_out = tmp_path / "apply.json"
    env = dict(os.environ)
    env.update({
        "ENGINE_LOCAL": "1",
        "CONCLUDE_INPUTS_DIR": str(inputs),
        "APPLY_WORKDIR": str(workdir),
        "APPLY_OUT": str(apply_out),
        "GITHUB_REPOSITORY": "acme/repo", "PR": "8",
        "FIX_REVIEW_OUT": str(tmp_path / "review.json"),
        "FIX_OUT": str(tmp_path / "report.json"),
    })
    out = _run(env, evidence)

    # file actually edited
    assert (workdir / "a.py").read_text() == "x = 1\n"
    # report recorded the applied fix + the issue it would close
    rep = json.loads(apply_out.read_text())
    assert rep["applied"] == 1
    assert any(c["label"] == "review:correctness" and c["title"] == "Bad default"
               for c in rep["close"])
    assert out["conclusion"] == "neutral"


def test_no_fixes_is_noop(tmp_path):
    evidence = tmp_path / "fix.json"
    evidence.write_text(json.dumps({"mode": "suggest", "fixes": [], "skipped": []}))
    apply_out = tmp_path / "apply.json"
    env = dict(os.environ)
    env.update({"ENGINE_LOCAL": "1", "APPLY_OUT": str(apply_out),
                "GITHUB_REPOSITORY": "acme/repo", "PR": "8",
                "FIX_REVIEW_OUT": str(tmp_path / "r.json"), "FIX_OUT": str(tmp_path / "rep.json")})
    out = _run(env, evidence)
    assert out["conclusion"] == "neutral"
    rep = json.loads(apply_out.read_text())
    assert rep["applied"] == 0 and rep["close"] == []
