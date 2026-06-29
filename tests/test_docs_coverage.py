import json
from conftest import PROTOCOLS, run_check

CHECK = PROTOCOLS / "code-review/checks/docs-coverage.py"

def _run(ev_obj, changed, tmp_path):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(ev_obj))
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("\n".join(changed) + "\n")
    return run_check(CHECK, ev, diff, files)

CHANGED = ["src/app.py", "docs/guide.md"]

def test_adequate_passes(tmp_path):
    ev = {"scope": {"code_changed": True},
          "items": [{"path": "docs/guide.md", "status": "updated_appropriately", "reason": "covers the new flag"}],
          "verdict": "adequate", "examined": ["docs/guide.md", "src/app.py"]}
    assert _run(ev, CHANGED, tmp_path)["pass"] is True

def test_no_relevant_docs_adequate_passes(tmp_path):
    ev = {"scope": {"code_changed": True}, "items": [], "verdict": "adequate", "examined": ["src/app.py"]}
    assert _run(ev, CHANGED, tmp_path)["pass"] is True

def test_missing_doc_must_be_inadequate(tmp_path):
    ev = {"scope": {"code_changed": True},
          "items": [{"path": "docs/guide.md", "status": "missing", "reason": "new flag undocumented"}],
          "verdict": "adequate", "examined": ["docs/guide.md"]}  # WRONG: missing => verdict must be inadequate
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and "inadequate" in r["feedback"].lower()

def test_inadequate_correct_passes(tmp_path):
    ev = {"scope": {"code_changed": True},
          "items": [{"path": "docs/guide.md", "status": "inadequate", "reason": "stale example"}],
          "verdict": "inadequate", "examined": ["docs/guide.md"]}
    assert _run(ev, CHANGED, tmp_path)["pass"] is True

def test_handled_doc_not_in_diff_fails(tmp_path):
    ev = {"scope": {"code_changed": True},
          "items": [{"path": "docs/other.md", "status": "updated_appropriately", "reason": "x"}],
          "verdict": "adequate", "examined": ["docs/other.md"]}  # docs/other.md not in CHANGED
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and "diff" in r["feedback"].lower()

def test_non_doc_path_fails(tmp_path):
    ev = {"scope": {"code_changed": True},
          "items": [{"path": "src/app.py", "status": "updated_appropriately", "reason": "x"}],
          "verdict": "adequate", "examined": ["src/app.py"]}
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and "doc" in r["feedback"].lower()

def test_scope_disagreement_fails(tmp_path):
    ev = {"scope": {"code_changed": False}, "items": [], "verdict": "adequate", "examined": ["x"]}
    r = _run(ev, CHANGED, tmp_path)  # CHANGED has src/app.py => code_changed recompute True
    assert r["pass"] is False and "scope" in r["feedback"].lower()

def test_empty_examined_fails(tmp_path):
    ev = {"scope": {"code_changed": True}, "items": [], "verdict": "adequate", "examined": []}
    assert _run(ev, CHANGED, tmp_path)["pass"] is False
