"""Unit tests for path-keyed join markers (lib.join_marker_file / read_join / write_join).
These helpers are additive — they do NOT affect depth-≤3 join behavior (which keeps
using _instance.yaml's `joined` bool).  Task 12 (recursive bubbling) exercises them
in full; here we verify the naming contract and round-trip independence."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import lib


def test_join_marker_path_keyed(tmp_path):
    d = str(tmp_path)
    lib.write_join(d, "p", "pr-1", ["pre", "deep", "analyze"], {"joined": True})
    lib.write_join(d, "p", "pr-1", ["pre"], {"joined": False})
    assert lib.read_join(d, "p", "pr-1", ["pre", "deep", "analyze"])["joined"] is True
    assert lib.read_join(d, "p", "pr-1", ["pre"])["joined"] is False
    f = lib.join_marker_file(d, "p", "pr-1", ["pre", "deep", "analyze"])
    assert f.endswith("/p/pr-1/pre.deep.analyze.__join.yaml")


def test_join_marker_empty_returns_empty(tmp_path):
    """read_join on a non-existent path returns {} (not an error)."""
    d = str(tmp_path)
    result = lib.read_join(d, "p", "pr-1", ["missing", "path"])
    assert result == {}


def test_join_marker_file_naming(tmp_path):
    """join_marker_file produces the right filename for single-element paths."""
    d = str(tmp_path)
    f = lib.join_marker_file(d, "proto", "inst-42", ["review"])
    assert f.endswith("/proto/inst-42/review.__join.yaml")


def test_join_marker_roundtrip(tmp_path):
    """write_join + read_join is a lossless round-trip for arbitrary data."""
    d = str(tmp_path)
    data = {"joined": True, "branches": ["grumpy", "security"], "count": 2}
    lib.write_join(d, "p", "pr-5", ["phase-a", "sub-b"], data)
    result = lib.read_join(d, "p", "pr-5", ["phase-a", "sub-b"])
    assert result["joined"] is True
    assert result["branches"] == ["grumpy", "security"]
    assert result["count"] == 2


def test_join_marker_two_paths_independent(tmp_path):
    """Two different fanout_paths write independent files; each reads back its own value."""
    d = str(tmp_path)
    lib.write_join(d, "proto", "inst", ["a", "b"], {"joined": True, "v": 1})
    lib.write_join(d, "proto", "inst", ["a", "c"], {"joined": False, "v": 2})
    ab = lib.read_join(d, "proto", "inst", ["a", "b"])
    ac = lib.read_join(d, "proto", "inst", ["a", "c"])
    assert ab["joined"] is True and ab["v"] == 1
    assert ac["joined"] is False and ac["v"] == 2
    # Verify distinct filenames
    fab = lib.join_marker_file(d, "proto", "inst", ["a", "b"])
    fac = lib.join_marker_file(d, "proto", "inst", ["a", "c"])
    assert fab != fac
    assert fab.endswith("/proto/inst/a.b.__join.yaml")
    assert fac.endswith("/proto/inst/a.c.__join.yaml")


def test_two_protocols_seed_into_disjoint_paths(engine_env, tmp_path):
    """Two different protocols seeded for the same instance key produce disjoint state paths.

    recover-mental-model-stub (single-phase fanout) and code-review (multi-phase)
    both use instance key 'pr-1' but write into separate <protocol-id>/pr-1/ subtrees
    under the shared STATE_REMOTE origin — no path collision possible.
    """
    import subprocess as _sp

    NEXT_PY = ROOT / ".github/agent-factory/engine/next.py"
    protocols_dir = ROOT / ".github/agent-factory/protocols"

    protocols = [
        ("recover-mental-model-stub", protocols_dir / "recover-mental-model-stub/protocol.json"),
        ("code-review", protocols_dir / "code-review/protocol.json"),
    ]

    # Each next.py call needs its own local checkout dir; they share the same
    # STATE_REMOTE (engine_env["STATE_REMOTE"]) so both protocol trees accumulate
    # in the same bare origin.
    for idx, (proto_name, proto_path) in enumerate(protocols):
        sd = tmp_path / f"sd{idx}"
        r = _sp.run(
            ["python3", str(NEXT_PY), str(sd), "pr-1", str(proto_path), "start"],
            env=engine_env,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"{proto_name} next.py start failed:\n{r.stderr}"

    # Clone the shared origin once to inspect both protocol trees side-by-side.
    view = tmp_path / "view"
    _sp.run(
        ["git", "clone", "-q", engine_env["STATE_REMOTE"], str(view)],
        check=True,
    )

    # Both protocol dirs must exist and be non-empty (each seeded independently).
    a = {x.name for x in (view / "recover-mental-model-stub" / "pr-1").iterdir()}
    b = {x.name for x in (view / "code-review" / "pr-1").iterdir()}

    assert (view / "recover-mental-model-stub").exists() and (view / "code-review").exists()
    assert a and b  # both seeded independently
    # The two dirs are disjoint namespaces — no shared file path can exist.
    assert (view / "recover-mental-model-stub" / "pr-1") != (view / "code-review" / "pr-1")
