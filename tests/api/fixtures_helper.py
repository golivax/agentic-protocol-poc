import pathlib

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

def load_instance_files(protocol, pr):
    d = FIXTURES / "state" / protocol / f"pr-{pr}"
    return {p.name: p.read_text() for p in d.iterdir() if p.is_file()}
