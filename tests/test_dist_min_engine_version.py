import json
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

PROTOCOLS = Path(__file__).resolve().parents[1] / ".github/agent-factory/protocols"


def test_all_shipped_protocols_declare_min_engine_version():
    for name in ["code-review", "deep-review-stub", "recover-mental-model"]:
        proto = json.load(open(PROTOCOLS / name / "protocol.json"))
        assert proto.get("min_engine_version") == "1.0.0", name


def test_validate_protocol_tolerates_min_engine_version():
    proto = {
        "name": "x", "min_engine_version": "1.0.0",
        "states": [{"id": "s", "kind": "agent", "workflow": "w-agent"}],
    }
    lib.validate_protocol(proto)  # must not raise
