import pathlib
import pytest
import importlib.util

# Import from parent tests/ conftest.py, not from this package
parent_conftest = pathlib.Path(__file__).parent.parent / "conftest.py"
spec = importlib.util.spec_from_file_location("_parent_conftest", parent_conftest)
_parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_parent)

# Re-export main conftest symbols
ENGINE = _parent.ENGINE
PROTOCOLS = _parent.PROTOCOLS
FIXTURES_MAIN = _parent.FIXTURES
run_engine = _parent.run_engine
read_state_yaml = _parent.read_state_yaml
run_check = _parent.run_check
state_origin = _parent.state_origin
engine_env = _parent.engine_env

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

@pytest.fixture
def fixtures_dir():
    return FIXTURES

def load_instance_files(protocol, pr):
    d = FIXTURES / "state" / protocol / f"pr-{pr}"
    return {p.name: p.read_text() for p in d.iterdir() if p.is_file()}
