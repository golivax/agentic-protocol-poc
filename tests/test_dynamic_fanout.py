import importlib.util
import json
import os, stat, textwrap
import pathlib
import subprocess
import sys

import pytest
import yaml

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


@pytest.mark.parametrize("policy,done,total,ok", [
    ("all", 3, 3, True), ("all", 2, 3, False),
    ("any", 1, 3, True), ("any", 0, 3, False),
    ("quorum:2", 2, 3, True), ("quorum:2", 1, 3, False),
    ("quorum:80%", 8, 10, True), ("quorum:80%", 7, 10, False),
    ("all", 0, 0, True),          # vacuous: no legs, all() holds
    ("any", 0, 0, False),         # vacuous: any() needs >=1
])
def test_join_policy_satisfied(policy, done, total, ok):
    lib = _load_lib()
    assert lib.join_policy_satisfied(policy, done, total) is ok


def test_join_policy_bad_quorum_raises():
    lib = _load_lib()
    with pytest.raises(ValueError):
        lib.join_policy_satisfied("quorum:x", 1, 3)


def test_validate_rejects_branches_and_expand_together():
    lib = _load_lib()
    proto = {"name": "x", "states": [
        {"id": "f", "kind": "fanout", "branches": [{"id": "a", "workflow": "w"}],
         "expand": {"hook": "h", "as": "i", "id_from": "$.p", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "j"},
        {"id": "j", "kind": "join", "of": "f"}]}
    with pytest.raises(ValueError) as e:
        lib.validate_protocol(proto)
    assert "exactly one of" in str(e.value) and "'f'" in str(e.value)


def test_validate_rejects_bad_max_legs():
    lib = _load_lib()
    proto = {"name": "x", "states": [
        {"id": "f", "kind": "fanout",
         "expand": {"hook": "h", "as": "i", "id_from": "$.p", "max_legs": 999},
         "each": {"workflow": "w"}, "next": "j"},
        {"id": "j", "kind": "join", "of": "f"}]}
    with pytest.raises(ValueError) as e:
        lib.validate_protocol(proto)
    assert "max_legs" in str(e.value)


def test_validate_rejects_bad_join_policy():
    lib = _load_lib()
    proto = {"name": "x", "states": [
        {"id": "f", "kind": "fanout",
         "expand": {"hook": "h", "as": "i", "id_from": "$.p", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "j"},
        {"id": "j", "kind": "join", "of": "f", "policy": "most"}]}
    with pytest.raises(ValueError) as e:
        lib.validate_protocol(proto)
    assert "policy" in str(e.value)


def test_validate_accepts_wellformed_dynamic():
    lib = _load_lib()
    proto = {"name": "x", "states": [
        {"id": "f", "kind": "fanout",
         "expand": {"hook": "h", "as": "i", "id_from": "$.p", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "j"},
        {"id": "j", "kind": "join", "of": "f", "policy": "quorum:50%"}]}
    lib.validate_protocol(proto)  # no raise


def test_schema_and_runtime_agree_on_fractional_quorum():
    """Regression: the schema policy pattern must not reject a fractional
    percent the runtime join_policy_satisfied accepts (else protocol-lint
    falsely reports INVALID). See code review of 84fb95e."""
    import json as _json
    jsonschema = pytest.importorskip("jsonschema")
    lib = _load_lib()
    # runtime accepts it
    assert lib.join_policy_satisfied("quorum:33.3%", 4, 10) is True   # ceil(10*33.3/100)=4
    # schema accepts it too
    with open(ENGINE / "protocol.schema.json") as f:
        schema = _json.load(f)
    proto = {"name": "x", "states": [
        {"id": "f", "kind": "fanout",
         "expand": {"hook": "h", "as": "i", "id_from": "$.p", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "j"},
        {"id": "j", "kind": "join", "of": "f", "policy": "quorum:33.3%"}]}
    jsonschema.Draft7Validator(schema).validate(proto)   # must not raise


def test_dynamic_fanout_start_seeds_manifest_and_legs(engine_env, tmp_path):
    import os, json
    from conftest import run_engine, read_state_yaml
    proto = str(ROOT / "tests/fixtures/dyn-fanout-flat/protocol.json")
    out, err, rc = run_engine("next.py", str(tmp_path), "pr-1", proto, "start", env=engine_env)
    assert rc == 0, err
    d = str(tmp_path / "dyn-fanout-flat" / "pr-1")
    man = read_state_yaml(d + "/review.__manifest.yaml")
    assert man["count"] == 2
    ids = [leg["id"] for leg in man["legs"]]
    for lid in ids:
        assert os.path.isfile(d + f"/{lid}.yaml")            # one leg state file per item
        assert os.path.isfile(d + f"/{lid}.file.item.json")  # item staged for inputs/<as>.json
    action = json.loads(out.strip().splitlines()[-1])
    assert action["action"] == "run-fanout"
    assert {leg_dict["path"].split(".")[-1] for leg_dict in action["legs"]} == set(ids)


def test_dynamic_fanout_subpipeline_each_fails_loud(engine_env, tmp_path):
    """Until sub-pipeline `each` is supported, running one must fail loud, not
    silently emit workflow:null legs. See spec review of 8426128."""
    import shutil, json as _json
    from conftest import run_engine
    # Build a protocol dir with a sub-pipeline `each`, reusing the flat fixture's expander.
    pdir = tmp_path / "proto"
    (pdir).mkdir()
    shutil.copytree(ROOT / "tests/fixtures/dyn-fanout-flat/expand", pdir / "expand")
    proto = {
        "name": "dyn-subpipeline-unsupported",
        "states": [
            {"id": "review", "kind": "fanout",
             "expand": {"hook": "expand-items", "as": "file", "id_from": "$.path", "max_legs": 8},
             "each": {"states": [
                 {"id": "draft", "kind": "agent", "workflow": "draft-agent", "next": "finalize"},
                 {"id": "finalize", "kind": "agent", "workflow": "finalize-agent"}]},
             "next": "join"},
            {"id": "join", "kind": "join", "of": "review", "policy": "any", "next": "done"}]}
    ppath = pdir / "protocol.json"
    ppath.write_text(_json.dumps(proto))
    out, err, rc = run_engine("next.py", str(tmp_path / "state"), "pr-1", str(ppath), "start", env=engine_env)
    assert rc != 0, f"expected fail-loud, got rc=0. out={out}"
    assert "sub-pipeline" in (err + out)
    # The guard must fire before the expander/manifest write — no manifest file
    # should exist for this aborted run.
    manifest_path = tmp_path / "state" / "dyn-subpipeline-unsupported" / "pr-1" / "review.__manifest.yaml"
    assert not manifest_path.exists(), f"manifest was written despite fail-loud guard: {manifest_path}"


# ---------------------------------------------------------------------------
# Task 7 — resolve_leg_ids unit test
# ---------------------------------------------------------------------------


def test_resolve_leg_ids_prefers_manifest(tmp_path):
    lib = _load_lib()
    d, pid, inst = str(tmp_path), "ocr", "pr-1"
    lib.write_manifest(d, pid, inst, ["review"],
                       {"count": 2, "legs": [{"id": "aa", "key": "a", "item": {}},
                                             {"id": "bb", "key": "b", "item": {}}]})
    dyn_node = {"id": "review", "kind": "fanout", "expand": {"hook": "h"}}
    static_node = {"id": "review", "kind": "fanout",
                   "branches": [{"id": "grumpy"}, {"id": "security"}]}
    assert lib.resolve_leg_ids(d, pid, inst, ["review"], dyn_node) == ["aa", "bb"]
    assert lib.resolve_leg_ids(d, pid, inst, ["review"], static_node) == ["grumpy", "security"]


# ---------------------------------------------------------------------------
# Task 7 — dynamic join reads manifest + applies policy (real join.py run)
# ---------------------------------------------------------------------------

LIB_PY = ENGINE / "lib.py"
JOIN_PY = ENGINE / "join.py"


def _join_env(origin):
    env = dict(os.environ)
    env["ENGINE_LOCAL"] = "1"
    env["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    env["STATE_REMOTE"] = str(origin)
    env["PR_HEAD_SHA"] = "dynsha"
    return env


def _seed_dynamic(origin, workdir, pid, inst, leg_states):
    """Seed a single-phase dynamic-fanout instance into the bare origin:
    _instance.yaml (joined:false) + review.__manifest.yaml + one <lid>.yaml per leg.
    `leg_states` is an ordered dict-like list of (leg_id, state)."""
    env = _join_env(origin)
    subprocess.run(["python3", str(LIB_PY), "state-checkout", str(workdir)],
                   env=env, check=True, capture_output=True, text=True)
    d = pathlib.Path(workdir) / pid / inst
    d.mkdir(parents=True, exist_ok=True)
    legs = [{"id": lid, "key": lid, "item": {"path": lid}} for lid, _ in leg_states]
    (d / "review.__manifest.yaml").write_text(json.dumps({"count": len(legs), "legs": legs}))
    (d / "_instance.yaml").write_text(json.dumps({
        "protocol": pid, "instance": inst, "head_sha": "dynsha", "joined": False}))
    for lid, st in leg_states:
        (d / f"{lid}.yaml").write_text(json.dumps({
            "protocol": pid, "instance": inst, "state": st,
            "iteration": 1, "gates": {}, "history": []}))
    subprocess.run(["python3", str(LIB_PY), "cas-push", str(workdir), f"seed {inst}"],
                   env=env, check=True, capture_output=True, text=True)


def _run_join(origin, workdir, inst, proto):
    env = _join_env(origin)
    env["PR"] = "1"
    r = subprocess.run(["python3", str(JOIN_PY), str(workdir), inst, str(proto)],
                       env=env, text=True, capture_output=True)
    return r.stdout + r.stderr, r.returncode


def _verify_instance(origin, workdir, pid, inst):
    env = _join_env(origin)
    subprocess.run(["python3", str(LIB_PY), "state-checkout", str(workdir)],
                   env=env, check=True, capture_output=True, text=True)
    p = pathlib.Path(workdir) / pid / inst / "_instance.yaml"
    return yaml.safe_load(p.read_text())


def test_dynamic_join_policy_any_one_failed_succeeds(tmp_path):
    """policy:any with 1 done + 1 failed → join clears (advances to .next=reduce),
    NOT a failure. Uses the committed dyn-fanout-flat fixture (policy 'any')."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "agentic-state", str(origin)],
                   check=True)
    proto = ROOT / "tests/fixtures/dyn-fanout-flat/protocol.json"
    pid = "dyn-fanout-flat"
    _seed_dynamic(origin, tmp_path / "seed", pid, "pr-1",
                  [("aa", "done"), ("bb", "failed")])
    out, rc = _run_join(origin, tmp_path / "join", "pr-1", proto)
    assert rc == 0, out
    # policy any satisfied → advance to reduce via protocol-continue (no failure).
    assert "event_type=protocol-continue" in out, out
    assert "client_payload[path]=reduce" in out, out
    assert "conclusion=failure" not in out, out
    inst = _verify_instance(origin, tmp_path / "verify", pid, "pr-1")
    assert inst["joined"] is True
    assert inst["phase"] == "reduce"


def test_dynamic_join_policy_all_one_failed_fails(tmp_path):
    """policy:all with the SAME 1 done + 1 failed → join concludes FAILURE
    (merge gated). Ad-hoc protocol identical shape but policy 'all'."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "agentic-state", str(origin)],
                   check=True)
    proto = {
        "name": "dyn-all-test",
        "states": [
            {"id": "review", "kind": "fanout",
             "expand": {"hook": "expand-items", "as": "file", "id_from": "$.path", "max_legs": 8},
             "each": {"workflow": "w"}, "next": "join"},
            {"id": "join", "kind": "join", "of": "review", "policy": "all", "next": "reduce"},
            {"id": "reduce", "kind": "merge", "hook": "reduce", "next": "done"}]}
    ppath = tmp_path / "protocol.json"
    ppath.write_text(json.dumps(proto))
    pid = "dyn-all-test"
    _seed_dynamic(origin, tmp_path / "seed", pid, "pr-1",
                  [("aa", "done"), ("bb", "failed")])
    out, rc = _run_join(origin, tmp_path / "join", "pr-1", ppath)
    assert rc == 0, out
    # policy all NOT met (1/2) → aggregate failure, no advance.
    assert "conclusion=failure" in out, out
    assert "event_type=protocol-continue" not in out, out
    inst = _verify_instance(origin, tmp_path / "verify", pid, "pr-1")
    assert inst["joined"] is True
