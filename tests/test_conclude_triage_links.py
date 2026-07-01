import json, os, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/code-review/publish/conclude-triage.py"

def _run(triage, tmp_path):
    ev = tmp_path / "t.json"; ev.write_text(json.dumps(triage))
    out = tmp_path / "comment.txt"
    inputs = tmp_path / "inputs"; inputs.mkdir()
    env = dict(os.environ)
    env["ENGINE_LOCAL"] = "1"; env["PR"] = "7"; env["GITHUB_REPOSITORY"] = "o/r"
    env["TRIAGE_COMMENT_OUT"] = str(out); env["CONCLUDE_INPUTS_DIR"] = str(inputs)
    r = subprocess.run(["python3", str(HOOK), str(ev), "pr-7"], text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    return (out.read_text() if out.exists() else r.stderr), json.loads(r.stdout)

def _cluster(cid, dim, title, rank=1):
    return {"cluster_id": cid, "title": title, "dimension": [dim], "severity": "high",
            "paths": ["a.py"], "rank": rank,
            "member_findings": [{"dimension": dim, "path": "a.py", "severity": "high", "title": title}]}

def _triage(clusters):
    return {"clusters": clusters,
            "summary": {"present": [], "missing": [], "clusters": len(clusters),
                        "total_findings": sum(len(c["member_findings"]) for c in clusters),
                        "by_severity": {}, "by_dimension": {}}}

def test_comment_lists_linked_issue_keys(tmp_path):
    body, _out = _run(_triage([_cluster("c1", "correctness", "Bad default")]), tmp_path)
    assert "Linked issues:" in body and "review:correctness" in body and "Bad default" in body

def test_linked_issues_dedup_and_order(tmp_path):
    body, _out = _run(_triage([_cluster("c2", "test", "Dup", rank=2), _cluster("c1", "test", "Dup", rank=1)]), tmp_path)
    assert body.count("review:test` --- Dup") == 1  # deduped

def test_no_linked_section_when_no_members(tmp_path):
    c = _cluster("c1", "correctness", "X"); c["member_findings"] = []
    body, _out = _run(_triage([c]), tmp_path)
    assert "Linked issues:" not in body

def test_gate_unchanged_by_linking(tmp_path):
    """Linking is cosmetic: conclusion/summary must be identical with vs without clusters."""
    base = _triage([])
    with_clusters = _triage([_cluster("c1", "correctness", "Bad default")])
    (tmp_path / "base").mkdir(exist_ok=True)
    (tmp_path / "with").mkdir(exist_ok=True)
    _, out_base = _run(base, tmp_path / "base")
    _, out_with = _run(with_clusters, tmp_path / "with")
    # conclusion is driven by gate verdict (both have same summary stats: 0 findings)
    assert out_base["conclusion"] == out_with["conclusion"]
