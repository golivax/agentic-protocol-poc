import importlib.util
import os, stat, textwrap
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"


def _load_lib():
    # lib.py does `import paths as _paths`; spec_from_file_location does not
    # add its own directory to sys.path, so ENGINE must be there for that
    # sibling import to resolve when this test runs standalone (matches the
    # sys.path.insert(0, ENGINE) convention used by the other test modules).
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    spec = importlib.util.spec_from_file_location("lib", ENGINE / "lib.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_manifest_roundtrip_and_path(tmp_path):
    lib = _load_lib()
    d, pid, inst = str(tmp_path), "ocr", "pr-1"
    data = {"count": 2, "legs": [{"id": "a1b2c3d4", "key": "src/a.go", "item": {"path": "src/a.go"}}]}
    lib.write_manifest(d, pid, inst, ["review"], data)
    assert lib.manifest_file(d, pid, inst, ["review"]).endswith("/ocr/pr-1/review.__manifest.yaml")
    assert lib.read_manifest(d, pid, inst, ["review"]) == data
    assert lib.read_manifest(d, pid, inst, ["nope"]) == {}


def test_leg_id_is_stable_and_fs_safe():
    lib = _load_lib()
    a = lib.leg_id("src/a.go")
    b = lib.leg_id("src/a.go")
    c = lib.leg_id("src/b.go")
    assert a == b and a != c
    assert a.isalnum() and len(a) == 8


def test_build_manifest_keys_and_bounds():
    lib = _load_lib()
    items = [{"path": "src/a.go"}, {"path": "src/b.go"}]
    m = lib.build_manifest(items, id_from="$.path", max_legs=256)
    assert m["count"] == 2
    assert [leg["key"] for leg in m["legs"]] == ["src/a.go", "src/b.go"]
    assert m["legs"][0]["id"] == lib.leg_id("src/a.go")
    assert m["legs"][0]["item"] == {"path": "src/a.go"}


def test_build_manifest_over_cap_fails_loud():
    lib = _load_lib()
    items = [{"path": f"f{i}"} for i in range(5)]
    try:
        lib.build_manifest(items, id_from="$.path", max_legs=3)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "5 items" in str(e) and "max_legs 3" in str(e)


def test_build_manifest_duplicate_key_fails_loud():
    lib = _load_lib()
    items = [{"path": "dup"}, {"path": "dup"}]
    try:
        lib.build_manifest(items, id_from="$.path", max_legs=256)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "two items" in str(e).lower() and "dup" in str(e)


def _write_exec(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_run_expander_parses_items(tmp_path):
    lib = _load_lib()
    pdir = tmp_path / "proto"
    (pdir / "expand").mkdir(parents=True)
    _write_exec(pdir / "expand" / "expand-items.py", textwrap.dedent("""\
        #!/usr/bin/env python3
        import json
        print(json.dumps({"items": [{"path": "a"}, {"path": "b"}]}))
    """))
    proto = pdir / "protocol.json"
    proto.write_text('{"name":"ocr"}')
    items = lib.run_expander(str(tmp_path), "ocr", "pr-1", str(proto),
                             {"expand": {"hook": "expand-items"}})
    assert items == [{"path": "a"}, {"path": "b"}]


def test_run_expander_nonzero_raises(tmp_path):
    lib = _load_lib()
    pdir = tmp_path / "proto"
    (pdir / "expand").mkdir(parents=True)
    _write_exec(pdir / "expand" / "expand-items.py", "#!/usr/bin/env python3\nimport sys; sys.exit(3)\n")
    proto = pdir / "protocol.json"; proto.write_text('{"name":"ocr"}')
    try:
        lib.run_expander(str(tmp_path), "ocr", "pr-1", str(proto), {"expand": {"hook": "expand-items"}})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "expander" in str(e).lower()
