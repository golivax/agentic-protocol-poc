import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / ".github/agent-factory/protocols/code-review/checks"
sys.path.insert(0, str(CHECKS))


def _run(check_name, changed_files, tmp_path):
    """Invoke a check with an empty evidence + diff and the given changed-files."""
    from conftest import run_check  # provided by tests/conftest.py
    ev = tmp_path / "ev.json"; ev.write_text("{}")
    diff = tmp_path / "diff.txt"; diff.write_text("")
    files = tmp_path / "files.txt"; files.write_text("\n".join(changed_files) + "\n")
    return run_check(CHECKS / check_name, ev, diff, files)


# _paths classifiers -----------------------------------------------------------

def test_paths_classifiers():
    import _paths as P
    assert P.is_spec_path("docs/specs/foo.md")
    assert P.is_spec_path("docs/superpowers/specs/x.md")
    assert P.is_spec_path("REQUIREMENTS.md")
    assert not P.is_spec_path("src/app.py")
    assert P.is_plan_path("docs/superpowers/plans/p.md")
    assert P.is_plan_path("PLAN.md")
    assert not P.is_plan_path("docs/specs/foo.md")
    assert P.is_doc("README.md") and P.is_doc("docs/x.md")
    assert P.is_test("tests/test_x.py") and P.is_test("foo.test.js")
    assert P.is_code("src/app.py")
    assert not P.is_code("README.md") and not P.is_code("tests/test_x.py")
