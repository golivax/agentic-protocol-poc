import pathlib
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

@pytest.fixture
def fixtures_dir():
    return FIXTURES

def load_instance_files(protocol, pr):
    d = FIXTURES / "state" / protocol / f"pr-{pr}"
    return {p.name: p.read_text() for p in d.iterdir() if p.is_file()}
