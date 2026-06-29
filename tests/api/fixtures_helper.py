import pathlib

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

def load_instance_files(protocol, pr):
    return load_instance_dir(protocol, f"pr-{pr}")

def load_instance_dir(protocol, instance):
    """Load an instance by its full dir name (e.g. `pr-62` or `ref-main`)."""
    d = FIXTURES / "state" / protocol / instance
    return {p.name: p.read_text() for p in d.iterdir() if p.is_file()}
