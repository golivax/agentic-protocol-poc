"""Tests for the authoring helper `protocol-lint.py` — validate a protocol.json
against the schema + engine rules and render an ASCII tree.

The tool ships inside the engine (so `dist/` vendors it), but `jsonschema` is a
DEV-ONLY dependency: the tool degrades to semantic-only validation when it is
absent. Both paths are exercised here.
"""

import importlib.util
import json
import subprocess
import sys

import pytest

from conftest import ENGINE, PROTOCOLS, FIXTURES

TOOL = ENGINE / "protocol-lint.py"


def _load_tool():
    """Import the dashed-name script as a module (for unit-level calls)."""
    # The engine dir must be importable so the tool can `import lib`/`paths`.
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    spec = importlib.util.spec_from_file_location("protocol_lint", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lint = _load_tool()


def _run(*args):
    """Invoke the tool as a CLI; return (stdout, stderr, returncode)."""
    r = subprocess.run(
        ["python3", str(TOOL), *map(str, args)],
        text=True,
        capture_output=True,
    )
    return r.stdout, r.stderr, r.returncode


# `too-deep` is a deliberately-invalid fixture (depth 6 > max_depth 5) used to
# exercise the depth guard — it must be rejected, not validated.
INVALID_FIXTURES = {"too-deep"}

ALL_PROTOS = [
    p
    for p in sorted(PROTOCOLS.glob("*/protocol.json"))
    + sorted(FIXTURES.glob("*/protocol.json"))
    if p.parent.name not in INVALID_FIXTURES
]


@pytest.mark.parametrize("path", ALL_PROTOS, ids=lambda p: p.parent.name)
def test_shipped_and_fixture_protocols_validate(path):
    out, err, rc = _run(path)
    assert rc == 0, f"{path.parent.name} should validate cleanly:\n{out}\n{err}"


@pytest.mark.parametrize("path", ALL_PROTOS, ids=lambda p: p.parent.name)
def test_tree_renders_every_node_id(path):
    """The ASCII tree must mention every node id declared in the protocol."""
    proto = json.loads(path.read_text())
    out, _, _ = _run(path)

    def ids(states):
        for s in states:
            if s.get("id"):
                yield s["id"]
            for br in s.get("branches", []):
                if br.get("id"):
                    yield br["id"]
                yield from ids(br.get("states", []))

    for nid in ids(proto.get("states", [])):
        assert nid in out, f"node id {nid!r} missing from tree:\n{out}"


def test_protocol_name_and_terminals_shown():
    cr = PROTOCOLS / "code-review/protocol.json"
    out, _, rc = _run(cr)
    assert rc == 0
    assert "code-review" in out
    # implicit terminals are surfaced
    assert "done" in out and "failed" in out


def test_too_deep_fixture_is_rejected():
    out, err, rc = _run(FIXTURES / "too-deep/protocol.json")
    assert rc == 1
    assert "max_depth" in (out + err)


def test_invalid_join_of_is_rejected(tmp_path):
    bad = tmp_path / "protocol.json"
    bad.write_text(
        json.dumps(
            {
                "name": "bad",
                "states": [
                    {"id": "f", "kind": "fanout", "next": "j",
                     "branches": [{"id": "a", "workflow": "a-agent"}]},
                    {"id": "j", "kind": "join", "of": "nonexistent"},
                ],
            }
        )
    )
    out, err, rc = _run(bad)
    assert rc == 1
    assert "nonexistent" in (out + err)


def test_agent_missing_workflow_is_rejected(tmp_path):
    bad = tmp_path / "protocol.json"
    bad.write_text(
        json.dumps({"name": "bad", "states": [{"id": "solo", "kind": "agent"}]})
    )
    out, err, rc = _run(bad)
    assert rc == 1
    assert "workflow" in (out + err)


def test_unparseable_json_exits_2(tmp_path):
    bad = tmp_path / "protocol.json"
    bad.write_text("{ not valid json ]")
    out, err, rc = _run(bad)
    assert rc == 2


def test_missing_file_exits_2():
    out, err, rc = _run("/no/such/protocol.json")
    assert rc == 2


def test_schema_typo_caught_when_jsonschema_present(tmp_path):
    pytest.importorskip("jsonschema")
    bad = tmp_path / "protocol.json"
    # 'wokflow' is a typo the schema (additionalProperties:false) must catch,
    # while the engine's semantic rules would miss it (agent has no workflow ->
    # that *is* caught too, so use a fanout flat-branch typo that semantics pass).
    bad.write_text(
        json.dumps(
            {
                "name": "bad",
                "states": [
                    {"id": "f", "kind": "fanout", "next": "j",
                     "branches": [{"id": "a", "workflow": "a-agent", "wokflow": "x"}]},
                    {"id": "j", "kind": "join", "of": "f"},
                ],
            }
        )
    )
    out, err, rc = _run(bad)
    assert rc == 1
    assert "wokflow" in (out + err) or "additional" in (out + err).lower()


@pytest.mark.parametrize("path", ALL_PROTOS, ids=lambda p: p.parent.name)
def test_block_view_renders_every_node_id(path):
    proto = json.loads(path.read_text())
    out, _, rc = _run(path, "--view", "block")
    assert rc == 0

    def ids(states):
        # join nodes are depicted as the closing fork/join bar (labeled by the
        # fan-out they barrier), not as a separate box — so skip their ids.
        for s in states:
            if s.get("id") and s.get("kind") != "join":
                yield s["id"]
            for br in s.get("branches", []):
                if br.get("id"):
                    yield br["id"]
                yield from ids(br.get("states", []))

    for nid in ids(proto.get("states", [])):
        assert nid in out, f"node id {nid!r} missing from block view:\n{out}"


def test_block_view_has_fork_join_and_events():
    cr = PROTOCOLS / "code-review/protocol.json"
    out, _, rc = _run(cr, "--view", "block")
    assert rc == 0
    assert "○ start" in out and "◉ done" in out
    assert "fork ▸ review" in out and "join ▸ review" in out


def test_view_both_shows_tree_and_block():
    cr = PROTOCOLS / "code-review/protocol.json"
    out, _, rc = _run(cr, "--view", "both")
    assert rc == 0
    assert "├" in out          # tree connector
    assert "╔═ fork" in out    # block fork bar


def test_block_view_omits_tree():
    cr = PROTOCOLS / "code-review/protocol.json"
    out, _, _ = _run(cr, "--view", "block")
    assert "(flow)" in out
    assert "(protocol)" not in out  # the tree header is absent


def test_bad_view_value_exits_2():
    cr = PROTOCOLS / "code-review/protocol.json"
    _, _, rc = _run(cr, "--view", "bogus")
    assert rc == 2


def test_note_key_is_accepted(tmp_path):
    """`_note` is the conventional free-text annotation (JSON has no comments);
    the schema allows it on any object while still rejecting real typos."""
    pytest.importorskip("jsonschema")
    p = tmp_path / "protocol.json"
    p.write_text(
        json.dumps(
            {
                "name": "annotated",
                "_note": "top-level annotation",
                "states": [
                    {"id": "solo", "kind": "agent", "workflow": "solo-agent",
                     "_note": "per-node annotation",
                     "checks": [{"run": "x", "_note": "per-check annotation"}]},
                ],
            }
        )
    )
    out, _, rc = _run(p, "--no-viz")
    assert rc == 0, out


def test_schema_only_nit_still_renders_best_effort(tmp_path):
    """An unknown top-level key is a strict-schema nit the engine would ignore.
    The tool exits 1 but still draws the (best-effort) diagram."""
    pytest.importorskip("jsonschema")
    p = tmp_path / "protocol.json"
    p.write_text(
        json.dumps(
            {
                "name": "annotated",
                "xyzzy": "not a known key, and not an _note annotation",
                "states": [{"id": "solo", "kind": "agent", "workflow": "solo-agent"}],
            }
        )
    )
    out, err, rc = _run(p, "--view", "both")
    assert rc == 1                       # invalid (schema nit)
    assert "xyzzy" in (out + err)        # the nit is reported
    assert "best-effort" in out          # and the render note is shown
    assert "solo" in out                 # the diagram still drew the node


def test_semantic_error_suppresses_render(tmp_path):
    """A real structural problem (bad join.of) means the tree is unsound — no
    best-effort render."""
    p = tmp_path / "protocol.json"
    p.write_text(
        json.dumps(
            {
                "name": "broken",
                "states": [
                    {"id": "f", "kind": "fanout", "next": "j",
                     "branches": [{"id": "a", "workflow": "a-agent"}]},
                    {"id": "j", "kind": "join", "of": "nope"},
                ],
            }
        )
    )
    out, _, rc = _run(p, "--view", "block")
    assert rc == 1
    assert "best-effort" not in out
    assert "○ start" not in out


def test_no_viz_flag_suppresses_tree(tmp_path):
    cr = PROTOCOLS / "code-review/protocol.json"
    out, _, rc = _run(cr, "--no-viz")
    assert rc == 0
    # the tree connector should be absent; the OK line should still be present
    assert "preflight" not in out or "├" not in out


def test_semantic_only_when_jsonschema_absent(monkeypatch):
    """With jsonschema unimportable, the tool still validates via engine rules
    and prints a skip note rather than crashing."""
    cr = PROTOCOLS / "code-review/protocol.json"
    proto = json.loads(cr.read_text())
    report = lint.validate(proto, schema_path=ENGINE / "protocol.schema.json",
                           jsonschema_module=None)
    assert report.ok
    assert report.schema_skipped


def test_impl_feature_auto_protocol_lints_clean():
    proto = PROTOCOLS / "impl-feature-auto/protocol.json"
    out, err, rc = _run(proto)
    assert rc == 0, f"lint failed:\n{out}\n{err}"
