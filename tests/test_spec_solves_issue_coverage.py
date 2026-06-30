import importlib.util, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("_locate", ROOT / ".github/agent-factory/protocols/code-review/checks/_locate.py")
_loc = importlib.util.module_from_spec(spec); spec.loader.exec_module(_loc)
import pytest
pytestmark = pytest.mark.skipif(not hasattr(_loc, "detect_issue_link"),
                                reason="blocked on extend-_locate cluster: _locate.detect_issue_link")

import base64, json, os, stat, subprocess
from conftest import PROTOCOLS
CHECK = PROTOCOLS / "code-review/checks/spec-solves-issue-coverage.py"
ISSUE_BODY = "Problem: tokens are never validated.\nProblem: denials are not logged."
SPEC_TEXT = "The system MUST validate the token.\nIt MUST log every denial."


def _gh(tmp_path, issue_fail=False):
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True)
    issue_b64 = ISSUE_BODY  # issues --jq .body returns raw text
    spec_b64 = base64.b64encode(SPEC_TEXT.encode()).decode()
    script = f"""#!/usr/bin/env python3
import sys
j = " ".join(sys.argv[1:])
if "issues/" in j:
    if {issue_fail!r}: sys.exit(1)
    sys.stdout.write({issue_b64!r}); sys.exit(0)
if "contents/" in j: sys.stdout.write({spec_b64!r}); sys.exit(0)
sys.exit(1)
"""
    gh = bindir / "gh"; gh.write_text(script)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def _run(ev_obj, changed, tmp_path, pr_body="Closes #7", issue_fail=False):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(ev_obj))
    diff = tmp_path / "d.txt"; diff.write_text("")
    files = tmp_path / "f.txt"; files.write_text("\n".join(changed) + "\n")
    env = dict(os.environ)
    env["PATH"] = f"{_gh(tmp_path, issue_fail)}{os.pathsep}" + env["PATH"]
    env["PR_BODY"] = pr_body; env["GITHUB_REPOSITORY"] = "o/r"; env.setdefault("PR", "1")
    r = subprocess.run([sys.executable, str(CHECK), str(ev), str(diff), str(files)],
                       text=True, capture_output=True, env=env)
    return json.loads(r.stdout)


CHANGED = ["docs/superpowers/specs/s.md", "src/auth.py"]


def _solves_ev():
    return {"scope": {"issue_linked": True, "spec_present": True},
            "matrix": [
                {"problem": "tokens are never validated.", "status": "addressed_by_spec",
                 "spec_quote": "The system MUST validate the token.", "location": "s.md:1"},
                {"problem": "denials are not logged.", "status": "addressed_by_spec",
                 "spec_quote": "It MUST log every denial.", "location": "s.md:2"}],
            "verdict": "solves", "examined": ["#7", "docs/superpowers/specs/s.md"]}


def test_solves_passes(tmp_path):
    assert _run(_solves_ev(), CHANGED, tmp_path)["pass"] is True


def test_incomplete_matrix_fails(tmp_path):
    ev = _solves_ev(); ev["matrix"] = ev["matrix"][:1]  # second problem uncovered
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and ("denials" in r["feedback"] or "problem" in r["feedback"].lower())


def test_fabricated_spec_quote_fails(tmp_path):
    ev = _solves_ev(); ev["matrix"][0]["spec_quote"] = "We MUST nuke prod."  # not in spec
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False and "verbatim" in r["feedback"].lower()


def test_issue_fetch_fail_closed(tmp_path):
    # issue_linked True but gh issue fetch fails => pass:false, DISTINCT 'fetch failed'
    r = _run(_solves_ev(), CHANGED, tmp_path, issue_fail=True)
    assert r["pass"] is False and "fetch" in r["feedback"].lower()


def test_scope_disagreement_no_link(tmp_path):
    # agent says issue_linked True but PR body has no closing keyword => recompute disagrees
    r = _run(_solves_ev(), CHANGED, tmp_path, pr_body="No link here")
    assert r["pass"] is False and "scope" in r["feedback"].lower()


def test_not_addressed_cell_but_solves_verdict_fails(tmp_path):
    # matrix has a not_addressed cell → expected verdict is "does-not-solve";
    # claiming "solves" is a verdict/cell inconsistency → pass: False.
    ev = _solves_ev()
    # Change first cell to not_addressed, remove spec_quote (not needed for not_addressed)
    ev["matrix"][0]["status"] = "not_addressed"
    ev["matrix"][0].pop("spec_quote", None)
    # verdict still "solves" — this is the inconsistency we pin
    assert ev["verdict"] == "solves"
    r = _run(ev, CHANGED, tmp_path)
    assert r["pass"] is False
    fb = r["feedback"].lower()
    assert "does-not-solve" in fb or "inconsistent" in fb


def test_na_no_issue_passes(tmp_path):
    ev = {"scope": {"issue_linked": False, "spec_present": True}, "matrix": [],
          "verdict": "n/a", "examined": ["(no linked issue)"]}
    assert _run(ev, CHANGED, tmp_path, pr_body="No link")["pass"] is True
