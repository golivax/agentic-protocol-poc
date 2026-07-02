import json
from conftest import PROTOCOLS, run_check

CHECK = PROTOCOLS / "code-review/checks/tests-coverage.py"

def _run(ev_obj, changed, tmp_path):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(ev_obj))
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("\n".join(changed) + "\n")
    return run_check(CHECK, ev, diff, files)

CHANGED = ["src/app.py", "tests/test_app.py"]

def test_adequate_passes(tmp_path):
    ev = {"scope": {"code_changed": True},
          "items": [{"path": "tests/test_app.py", "status": "updated_appropriately", "reason": "covers the new branch"}],
          "verdict": "adequate", "examined": ["tests/test_app.py", "src/app.py"]}
    assert _run(ev, CHANGED, tmp_path)["pass"] is True

def test_missing_test_must_be_inadequate(tmp_path):
    ev = {"scope": {"code_changed": True},
          "items": [{"path": "tests/test_app.py", "status": "missing", "reason": "new branch untested"}],
          "verdict": "adequate", "examined": ["tests/test_app.py"]}
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and "inadequate" in r["feedback"].lower()

def test_inadequate_correct_passes(tmp_path):
    ev = {"scope": {"code_changed": True},
          "items": [{"path": "tests/test_app.py", "status": "inadequate", "reason": "asserts nothing"}],
          "verdict": "inadequate", "examined": ["tests/test_app.py"]}
    assert _run(ev, CHANGED, tmp_path)["pass"] is True

def test_non_test_path_fails(tmp_path):
    ev = {"scope": {"code_changed": True},
          "items": [{"path": "docs/guide.md", "status": "updated_appropriately", "reason": "x"}],
          "verdict": "adequate", "examined": ["docs/guide.md"]}
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and "test" in r["feedback"].lower()

def test_na_no_code_passes(tmp_path):
    ev = {"scope": {"code_changed": False}, "items": [], "verdict": "n/a", "examined": ["(no code)"]}
    assert _run(ev, ["README.md"], tmp_path)["pass"] is True

def test_na_but_code_changed_fails(tmp_path):
    ev = {"scope": {"code_changed": False}, "items": [], "verdict": "n/a", "examined": ["x"]}
    r = _run(ev, CHANGED, tmp_path)  # code DID change => scope disagreement
    assert r["pass"] is False

def test_na_null_items_fails(tmp_path):
    # N/A path requires an explicit empty list; items:null must NOT slip through.
    ev = {"scope": {"code_changed": False}, "items": None, "verdict": "n/a", "examined": ["x"]}
    assert _run(ev, ["README.md"], tmp_path)["pass"] is False
