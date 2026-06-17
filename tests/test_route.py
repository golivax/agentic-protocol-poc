import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

# A protocol that matches /grumpy comments + PR opened/reopened/synchronize.
GRUMPY_TRIGGERS = [
    {"on": "issue_comment", "comment_prefix": "/grumpy", "command": "start"},
    {"on": "pull_request", "actions": ["opened", "reopened"], "command": "start"},
    {"on": "pull_request", "actions": ["synchronize"], "command": "reset"},
]


def _mk_protocols(tmp_path, protos):
    """protos: {dirname: triggers_list}. Lays down protocols/<dir>/protocol.json."""
    root = tmp_path / "protocols"
    for name, triggers in protos.items():
        d = root / name
        d.mkdir(parents=True)
        (d / "protocol.json").write_text(json.dumps({"name": name, "triggers": triggers}))
    return str(root)


# route() — entry events --------------------------------------------------------

def test_single_protocol_pr_opened_routes():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"multi-grumpy": GRUMPY_TRIGGERS})
        r = lib.route(pdir, "pull_request", "opened", "")
        assert r["skip"] is False
        assert r["protocol"].endswith("multi-grumpy/protocol.json")
        assert r["command"] == "start"


def test_comment_prefix_routes():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"multi-grumpy": GRUMPY_TRIGGERS})
        r = lib.route(pdir, "issue_comment", "", "/grumpy please", is_pr_comment=True)
        assert r["skip"] is False
        assert r["command"] == "start"


def test_no_match_skips():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"multi-grumpy": GRUMPY_TRIGGERS})
        r = lib.route(pdir, "issue_comment", "", "lgtm", is_pr_comment=True)
        assert r["skip"] is True
        assert r["protocol"] == ""


def test_non_pr_comment_skips_without_scanning():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"multi-grumpy": GRUMPY_TRIGGERS})
        r = lib.route(pdir, "issue_comment", "", "/grumpy", is_pr_comment=False)
        assert r["skip"] is True


def test_dispatch_protocol_passthrough():
    # repository_dispatch: protocol is carried on the payload; no scan, no skip.
    r = lib.route("/nonexistent", "repository_dispatch", "",
                  dispatch_protocol=".github/agent-factory/protocols/multi-grumpy/protocol.json")
    assert r["skip"] is False
    assert r["protocol"] == ".github/agent-factory/protocols/multi-grumpy/protocol.json"


def test_ambiguous_match_raises():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {
            "alpha": GRUMPY_TRIGGERS,
            "beta": GRUMPY_TRIGGERS,
        })
        try:
            lib.route(pdir, "pull_request", "opened", "")
            assert False, "expected ValueError on ambiguous match"
        except ValueError as e:
            msg = str(e)
            assert "alpha" in msg and "beta" in msg
            # The message names the PR action that collided, not a raw event/action pair.
            assert 'pull_request action "opened"' in msg


def test_ambiguous_comment_message_names_the_comment():
    # The confusing old message said "issue_comment/created"; it must instead name
    # the actual comment text that two protocols both matched.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"alpha": GRUMPY_TRIGGERS, "beta": GRUMPY_TRIGGERS})
        try:
            lib.route(pdir, "issue_comment", "created", "/grumpy please", is_pr_comment=True)
            assert False, "expected ValueError on ambiguous comment match"
        except ValueError as e:
            msg = str(e)
            assert '/grumpy please' in msg, msg
            assert 'created' not in msg, "should describe the comment text, not the GH action"


def test_globbing_is_sorted_deterministic():
    # Only one matches → no ambiguity; this asserts a non-matching sibling is ignored.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {
            "zeta-nomatch": [{"on": "pull_request", "actions": ["closed"], "command": "x"}],
            "alpha-match": GRUMPY_TRIGGERS,
        })
        r = lib.route(pdir, "pull_request", "opened", "")
        assert r["protocol"].endswith("alpha-match/protocol.json")


# CLI ---------------------------------------------------------------------------

def _cli(*args):
    r = subprocess.run(["python3", str(ENGINE / "lib.py"), "route", *map(str, args)],
                       text=True, capture_output=True)
    return r


def test_cli_route_prints_github_output_lines(tmp_path):
    pdir = _mk_protocols(tmp_path, {"multi-grumpy": GRUMPY_TRIGGERS})
    r = _cli(pdir, "pull_request", "opened", "", "", "false")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "skip=false" in out
    assert "protocol=" in out and "multi-grumpy/protocol.json" in out


def test_cli_route_skip(tmp_path):
    pdir = _mk_protocols(tmp_path, {"multi-grumpy": GRUMPY_TRIGGERS})
    r = _cli(pdir, "issue_comment", "", "lgtm", "", "true")
    assert r.returncode == 0, r.stderr
    assert "skip=true" in r.stdout


def test_cli_route_ambiguous_exits_nonzero(tmp_path):
    pdir = _mk_protocols(tmp_path, {"alpha": GRUMPY_TRIGGERS, "beta": GRUMPY_TRIGGERS})
    r = _cli(pdir, "pull_request", "opened", "", "", "false")
    assert r.returncode != 0
    assert "ambiguous" in r.stderr.lower()


# Regression: the REAL repo protocols must not route ambiguously (live-run guard).
REAL_PROTOCOLS = str(ROOT / ".github/agent-factory/protocols")


def test_real_protocols_grumpy_comment_routes_to_multi_grumpy():
    r = lib.route(REAL_PROTOCOLS, "issue_comment", "", "/grumpy", is_pr_comment=True)
    assert r["skip"] is False
    assert r["protocol"].endswith("multi-grumpy/protocol.json")


def test_real_protocols_pr_opened_routes_to_pipeline():
    # After M3, the pipeline (not multi-grumpy) owns PR auto-triggers.
    r = lib.route(REAL_PROTOCOLS, "pull_request", "opened", "")
    assert r["skip"] is False and r["protocol"].endswith("code-review-pipeline/protocol.json")


def test_real_protocols_review_comment_routes_to_pipeline():
    r = lib.route(REAL_PROTOCOLS, "issue_comment", "", "/review", is_pr_comment=True)
    assert r["skip"] is False and r["protocol"].endswith("code-review-pipeline/protocol.json")


def test_real_protocols_v1_grumpy_comment_routes_to_grumpy():
    r = lib.route(REAL_PROTOCOLS, "issue_comment", "", "/v1-grumpy", is_pr_comment=True)
    assert r["skip"] is False
    assert r["protocol"].endswith("grumpy/protocol.json")
    assert "multi-grumpy" not in r["protocol"]
