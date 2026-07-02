"""Guard: every shipped protocol and every fixture protocol must validate against
the authoring schema (.github/agent-factory/engine/protocol.schema.json).

This keeps the JSON Schema honest as the DSL evolves — if a real protocol grows a
field the schema doesn't know about, this test fails and forces the schema (and
docs/PROTOCOL-DSL.md) to be updated in lockstep.

jsonschema is a DEV-ONLY dependency (tests/requirements-dev.txt). It is NOT part
of the vendored engine runtime, which stays Python 3 + PyYAML only.
"""

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO / ".github/agent-factory/engine/protocol.schema.json"
SHIPPED = sorted((REPO / ".github/agent-factory/protocols").glob("*/protocol.json"))
FIXTURES = sorted((REPO / "tests/fixtures").glob("*/protocol.json"))


@pytest.fixture(scope="module")
def validator():
    schema = json.loads(SCHEMA_PATH.read_text())
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)  # the schema itself must be a valid draft-07 schema
    return cls(schema)


@pytest.mark.parametrize("path", SHIPPED + FIXTURES, ids=lambda p: p.parent.name)
def test_protocol_validates_against_schema(validator, path):
    proto = json.loads(path.read_text())
    errors = sorted(validator.iter_errors(proto), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{path.parent.name}: {'/'.join(str(p) for p in e.path)}: {e.message}"
        for e in errors
    )


def test_we_actually_found_protocols():
    # Guard against a glob that silently matches nothing.
    assert SHIPPED, "no shipped protocols discovered"
    assert FIXTURES, "no fixture protocols discovered"


def test_schema_rejects_a_typo(validator):
    # Negative check: a misspelled required key must be caught.
    bad = {
        "name": "x",
        "states": [{"id": "solo", "kind": "agent", "wokflow": "solo-agent"}],
    }
    assert list(validator.iter_errors(bad)), "schema should reject 'wokflow' typo"


def test_trigger_target_field_allowed():
    """The trigger schema must accept an optional `target` of pr|issue."""
    import json, pathlib, jsonschema
    root = pathlib.Path(__file__).resolve().parent.parent
    schema = json.load(open(root / ".github/agent-factory/engine/protocol.schema.json"))
    proto = {
        "name": "t",
        "triggers": [
            {"on": "issue_comment", "comment_prefix": "/x", "command": "start", "target": "issue"}
        ],
        "states": [{"id": "a", "kind": "agent", "workflow": "w"}],
    }
    jsonschema.validate(proto, schema)  # must not raise


def test_trigger_target_rejects_unknown_value():
    import json, pathlib, jsonschema, pytest
    root = pathlib.Path(__file__).resolve().parent.parent
    schema = json.load(open(root / ".github/agent-factory/engine/protocol.schema.json"))
    proto = {"name": "t",
             "triggers": [{"on": "issue_comment", "command": "start", "target": "wat"}],
             "states": [{"id": "a", "kind": "agent", "workflow": "w"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(proto, schema)
