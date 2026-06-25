import json
import yaml
from tests.api.conftest import load_instance_files, FIXTURES

def test_instance_fixtures_parse_as_yaml():
    files = load_instance_files("code-review", 62)
    assert "_instance.yaml" in files
    inst = yaml.safe_load(files["_instance.yaml"])
    assert inst["protocol"] == "code-review" and inst["phase"] == "approval"

def test_protocol_fixture_parses_as_json():
    txt = (FIXTURES / "protocols" / "code-review.protocol.json").read_text()
    proto = json.loads(txt)
    assert proto["name"] == "code-review"
    assert any(s["id"] == "preflight" for s in proto["states"])
