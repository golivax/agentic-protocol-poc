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


def test_run_expander_scrubs_sensitive_env(tmp_path, monkeypatch):
    lib = _load_lib()
    pdir = tmp_path / "proto"
    (pdir / "expand").mkdir(parents=True)
    _write_exec(pdir / "expand" / "expand-items.py", textwrap.dedent("""\
        #!/usr/bin/env python3
        import json, os, sys
        open(os.path.join(sys.argv[1], "envprobe.json"), "w").write(json.dumps(dict(os.environ)))
        print(json.dumps({"items": [{"path": "x"}]}))
    """))
    proto = pdir / "protocol.json"; proto.write_text('{"name":"ocr"}')
    monkeypatch.setenv("STATE_REMOTE", "https://x-access-token:SECRET@github.com/o/r.git")
    monkeypatch.setenv("PUBLISH_TOKEN", "SECRET_PAT")
    monkeypatch.setenv("GH_TOKEN", "SECRET_PAT")
    monkeypatch.setenv("EXPANDER_TOKEN", "read-only-tok")
    node = {"expand": {"hook": "expand-items", "id_from": "$.path", "max_legs": 4, "as": "x"}}
    lib.run_expander(str(tmp_path), "ocr", "pr-1", str(proto), node)
    seen = json.loads((tmp_path / "envprobe.json").read_text())
    assert "STATE_REMOTE" not in seen
    assert seen.get("PUBLISH_TOKEN") is None
    assert seen.get("GH_TOKEN") == "read-only-tok"     # replaced by the read token
    assert json.loads(seen["EXPAND_PARAMS"])["max_legs"] == 4


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


def test_dynamic_fanout_subpipeline_each_seeds_first_substate(engine_env, tmp_path):
    """Sub-pipeline `each` now works: each dynamic leg is a sub-pipeline whose
    cursor + first sub-state (draft) are seeded. (Replaces the old fail-loud test.)"""
    import os, shutil, json as _json
    from conftest import run_engine, read_state_yaml
    pdir = tmp_path / "proto"; pdir.mkdir()
    shutil.copytree(ROOT / "tests/fixtures/dyn-fanout-flat/expand", pdir / "expand")
    proto = {"name": "dyn-subpipeline-each", "states": [
        {"id": "review", "kind": "fanout",
         "expand": {"hook": "expand-items", "as": "file", "id_from": "$.path", "max_legs": 8},
         "each": {"states": [
             {"id": "draft", "kind": "agent", "workflow": "draft-agent", "next": "finalize"},
             {"id": "finalize", "kind": "agent", "workflow": "finalize-agent"}]},
         "next": "join"},
        {"id": "join", "kind": "join", "of": "review", "policy": "any", "next": "done"}]}
    ppath = pdir / "protocol.json"; ppath.write_text(_json.dumps(proto))
    out, err, rc = run_engine("next.py", str(tmp_path / "state"), "pr-1", str(ppath), "start", env=engine_env)
    assert rc == 0, f"sub-pipeline each should now seed, got rc={rc}. err={err}"
    d = str(tmp_path / "state" / "dyn-subpipeline-each" / "pr-1")
    man = read_state_yaml(d + "/review.__manifest.yaml")
    lid = man["legs"][0]["id"]
    cur = read_state_yaml(d + f"/{lid}.yaml")
    assert cur.get("sub_state") == "draft"
    assert os.path.isfile(d + f"/{lid}.draft.yaml")


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


def test_collect_fanout_evidence_tags_state(tmp_path):
    lib = _load_lib()
    d, pid, inst = str(tmp_path), "ocr", "pr-1"
    lib.write_manifest(d, pid, inst, ["review"],
                       {"count": 2, "legs": [{"id": "aa", "key": "a", "item": {"path": "a"}},
                                             {"id": "bb", "key": "b", "item": {"path": "b"}}]})
    base = f"{d}/{pid}/{inst}"
    os.makedirs(base, exist_ok=True)
    # Leg aa: done, with evidence.  Leg bb: failed, no evidence.
    lib.dump_yaml(f"{base}/aa.yaml", {"state": "done"})
    with open(f"{base}/aa.evidence.json", "w") as f:
        json.dump({"finding": 1}, f)
    lib.dump_yaml(f"{base}/bb.yaml", {"state": "failed"})
    rows = lib.collect_fanout_evidence(d, pid, inst, ["review"], {"expand": {"hook": "h"}})
    assert rows == [
        {"leg_id": "aa", "key": "a", "state": "done", "evidence": {"finding": 1}},
        {"leg_id": "bb", "key": "b", "state": "failed", "evidence": None},
    ]


def test_run_merge_hook_from_fanout_reduces(tmp_path):
    lib = _load_lib()
    pid, inst = "dyn-fanout-flat", "pr-1"
    d = str(tmp_path)
    # Seed a manifest + two leg states/evidence as if the fanout had run.
    lib.write_manifest(d, pid, inst, ["review"],
                       {"count": 2, "legs": [{"id": "aa", "key": "src/a.go", "item": {"path": "src/a.go"}},
                                             {"id": "bb", "key": "src/b.go", "item": {"path": "src/b.go"}}]})
    base = f"{d}/{pid}/{inst}"
    os.makedirs(base, exist_ok=True)
    lib.dump_yaml(f"{base}/aa.yaml", {"state": "done"})
    with open(f"{base}/aa.evidence.json", "w") as f:
        json.dump({"finding": 1}, f)
    lib.dump_yaml(f"{base}/bb.yaml", {"state": "done"})
    with open(f"{base}/bb.evidence.json", "w") as f:
        json.dump({"finding": 2}, f)
    proto = str(ROOT / "tests/fixtures/dyn-fanout-flat/protocol.json")
    with open(proto) as f:
        proto_dict = json.load(f)
    merge_state = next(s for s in proto_dict["states"] if s.get("kind") == "merge")
    result = lib.run_merge_hook(d, pid, inst, proto, merge_state)
    assert result["conclusion"] == "success"
    assert "reduced 2/2 legs" in result["summary"]


def test_collect_fanout_evidence_robust_to_missing_and_corrupt(tmp_path):
    lib = _load_lib()
    d, pid, inst = str(tmp_path), "ocr", "pr-1"
    lib.write_manifest(d, pid, inst, ["review"], {"count": 3, "legs": [
        {"id": "aa", "key": "a", "item": {}},   # no state file at all
        {"id": "bb", "key": "b", "item": {}},   # corrupt YAML state file
        {"id": "cc", "key": "c", "item": {}},   # done state, corrupt JSON evidence
    ]})
    base = f"{d}/{pid}/{inst}"
    os.makedirs(base, exist_ok=True)
    # aa: no state file, no evidence.
    # bb: corrupt YAML.
    with open(f"{base}/bb.yaml", "w") as f:
        f.write("::: not: valid: yaml: [")
    # cc: valid state, corrupt evidence JSON.
    lib.dump_yaml(f"{base}/cc.yaml", {"state": "done"})
    with open(f"{base}/cc.evidence.json", "w") as f:
        f.write("{not json")
    rows = lib.collect_fanout_evidence(d, pid, inst, ["review"], {"expand": {"hook": "h"}})
    assert rows[0] == {"leg_id": "aa", "key": "a", "state": "", "evidence": None}
    assert rows[1]["leg_id"] == "bb" and rows[1]["state"] == "" and rows[1]["evidence"] is None
    assert rows[2] == {"leg_id": "cc", "key": "c", "state": "done", "evidence": None}


def test_validate_rejects_bad_from_fanout(tmp_path):
    lib = _load_lib()
    proto = {"name": "x", "states": [
        {"id": "f", "kind": "fanout",
         "expand": {"hook": "h", "as": "i", "id_from": "$.p", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "j"},
        {"id": "j", "kind": "join", "of": "f", "next": "m"},
        {"id": "m", "kind": "merge", "hook": "hk",
         "inputs": [{"from_fanout": "nope", "as": "legs"}], "next": "done"}]}
    with pytest.raises(ValueError) as e:
        lib.validate_protocol(proto)
    assert "from_fanout" in str(e.value) and "nope" in str(e.value)


def test_validate_accepts_good_from_fanout(tmp_path):
    lib = _load_lib()
    proto = {"name": "x", "states": [
        {"id": "review", "kind": "fanout",
         "expand": {"hook": "h", "as": "i", "id_from": "$.p", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "j"},
        {"id": "j", "kind": "join", "of": "review", "next": "m"},
        {"id": "m", "kind": "merge", "hook": "hk",
         "inputs": [{"from_fanout": "review", "as": "legs"}], "next": "done"}]}
    lib.validate_protocol(proto)  # no raise


# ---------------------------------------------------------------------------
# Task 9 — edge-case coverage: over-cap, expander-failure, zero-items
# ---------------------------------------------------------------------------


def test_dynamic_fanout_over_cap_fails_loud(engine_env, tmp_path):
    """The committed dyn-fanout-badcap fixture emits 5 items against max_legs:2 —
    build_manifest must fail loud (ValueError, uncaught by next.py's start/reset
    arm) before any manifest/leg state is written."""
    from conftest import run_engine
    import os
    proto = str(ROOT / "tests/fixtures/dyn-fanout-badcap/protocol.json")
    out, err, rc = run_engine("next.py", str(tmp_path), "pr-1", proto, "start", env=engine_env)
    assert rc != 0, f"expected fail-loud on over-cap, got rc=0. out={out}"
    assert "max_legs" in (err + out)
    # No manifest / legs should have been written for the aborted run.
    d = str(tmp_path / "dyn-fanout-badcap" / "pr-1")
    assert not os.path.isfile(d + "/review.__manifest.yaml")


def test_dynamic_fanout_expander_failure_halts(engine_env, tmp_path):
    """An expander hook that exits nonzero must halt next.py loud (ValueError from
    lib.run_expander, uncaught), mirroring the subpipeline-each guard's inline
    protocol-in-tmp_path pattern but with a failing expander instead of a copied
    good one."""
    import os, stat, json as _json
    from conftest import run_engine
    pdir = tmp_path / "proto"; (pdir / "expand").mkdir(parents=True)
    hook = pdir / "expand" / "expand-items.py"
    hook.write_text("#!/usr/bin/env python3\nimport sys; sys.stderr.write('boom\\n'); sys.exit(2)\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    proto = {"name": "dyn-expander-fail", "states": [
        {"id": "review", "kind": "fanout",
         "expand": {"hook": "expand-items", "as": "file", "id_from": "$.path", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "join"},
        {"id": "join", "kind": "join", "of": "review", "policy": "any", "next": "done"}]}
    ppath = pdir / "protocol.json"; ppath.write_text(_json.dumps(proto))
    out, err, rc = run_engine("next.py", str(tmp_path / "state"), "pr-1", str(ppath), "start", env=engine_env)
    assert rc != 0, f"expected halt on expander failure, got rc=0. out={out}"
    assert "expander" in (err + out).lower()


def test_dynamic_fanout_zero_items_is_vacuous(engine_env, tmp_path):
    """Zero-items is a vacuous fanout, not an error (spec §11): manifest is
    written with count 0, no legs are seeded, and the emitted run-fanout carries
    empty branches/legs so the engine still advances toward the join (whose
    `all` policy is vacuously satisfied for 0/0)."""
    import os, stat, json as _json
    from conftest import run_engine, read_state_yaml
    pdir = tmp_path / "proto"; (pdir / "expand").mkdir(parents=True)
    hook = pdir / "expand" / "expand-items.py"
    hook.write_text("#!/usr/bin/env python3\nimport json; print(json.dumps({'items': []}))\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    proto = {"name": "dyn-zero", "states": [
        {"id": "review", "kind": "fanout",
         "expand": {"hook": "expand-items", "as": "file", "id_from": "$.path", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "join"},
        {"id": "join", "kind": "join", "of": "review", "policy": "all", "next": "done"}]}
    ppath = pdir / "protocol.json"; ppath.write_text(_json.dumps(proto))
    out, err, rc = run_engine("next.py", str(tmp_path / "state"), "pr-1", str(ppath), "start", env=engine_env)
    assert rc == 0, f"zero-items should be a vacuous success, got rc={rc}. err={err}"
    d = str(tmp_path / "state" / "dyn-zero" / "pr-1")
    man = read_state_yaml(d + "/review.__manifest.yaml")
    assert man["count"] == 0 and man["legs"] == []
    # The emitted run-fanout has no legs.
    import json as __json
    action = __json.loads(out.strip().splitlines()[-1])
    assert action["action"] == "run-fanout"
    assert action.get("legs", []) == []


# ---------------------------------------------------------------------------
# Task 9.5 — each-aware path navigation (paths.py unit tests)
# ---------------------------------------------------------------------------


def _load_paths():
    import importlib.util, sys
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    spec = importlib.util.spec_from_file_location("paths", ENGINE / "paths.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


def test_paths_node_at_path_resolves_dynamic_leg_to_each():
    paths = _load_paths()
    proto = {"name": "x", "states": [
        {"id": "review", "kind": "fanout",
         "expand": {"hook": "h", "as": "f", "id_from": "$.p", "max_legs": 8},
         "each": {"states": [
             {"id": "draft", "kind": "agent", "workflow": "d", "next": "finalize"},
             {"id": "finalize", "kind": "agent", "workflow": "f"}]},
         "next": "join"},
        {"id": "join", "kind": "join", "of": "review"}]}
    # A dynamic leg path resolves to the each sub-pipeline (a sequence).
    assert paths.node_kind(proto, ["review", "RUNTIMEID"]) == "sequence"
    # A sub-state under the leg resolves into the each template's states.
    assert paths.node_kind(proto, ["review", "RUNTIMEID", "draft"]) == "agent"
    n = paths.node_at_path(proto, ["review", "RUNTIMEID", "finalize"])
    assert n and n.get("workflow") == "f"
    # next_sibling walks the each sub-pipeline.
    assert paths.next_sibling(proto, ["review", "RUNTIMEID", "draft"]) == "finalize"


def test_paths_flat_each_resolves_to_agent():
    paths = _load_paths()
    proto = {"name": "x", "states": [
        {"id": "review", "kind": "fanout",
         "expand": {"hook": "h", "as": "f", "id_from": "$.p", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "join"},
        {"id": "join", "kind": "join", "of": "review"}]}
    assert paths.node_kind(proto, ["review", "RUNTIMEID"]) == "agent"


def test_paths_max_static_depth_counts_each_subtree():
    paths = _load_paths()
    # dynamic review whose each nests a dynamic comments fanout with a flat each
    proto = {"name": "x", "states": [
        {"id": "review", "kind": "fanout",
         "expand": {"hook": "h", "as": "f", "id_from": "$.p", "max_legs": 8},
         "each": {"states": [
             {"id": "comments", "kind": "fanout",
              "expand": {"hook": "h2", "as": "c", "id_from": "$.c", "max_legs": 8},
              "each": {"workflow": "w"}, "next": "cjoin"},
             {"id": "cjoin", "kind": "join", "of": "comments"}]},
         "next": "join"},
        {"id": "join", "kind": "join", "of": "review"}]}
    # review → <each> → comments → <each>  == depth 4 (descends into each subtrees)
    assert paths.max_static_depth(proto) >= 4


# ---------------------------------------------------------------------------
# Task 10 — offline end-to-end walks over a shared git origin (mirroring the
# deep-fanout e2e harness): drive next.py / advance.py / join.py as subprocesses
# with NODE_PATH per leg + always-pass verdicts, asserting on-disk state at each
# step. Exercises the previously-untested ADVANCE walk of a dynamic sub-pipeline
# leg (dyn-fanout-subpipeline) and a NESTED dynamic fanout (dyn-nested).
# ---------------------------------------------------------------------------

NEXT = ENGINE / "next.py"
ADVANCE = ENGINE / "advance.py"
JOIN = ENGINE / "join.py"

SUBPIPE_PROTO = ROOT / "tests/fixtures/dyn-fanout-subpipeline/protocol.json"
NESTED_PROTO = ROOT / "tests/fixtures/dyn-nested/protocol.json"


def _pass_verdicts_t10(tmp_path):
    """always-pass verdicts + blank evidence, mirroring the deep-fanout harness."""
    v = tmp_path / "verdicts.json"
    v.write_text(json.dumps({"results": [
        {"check": "schema-valid", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "evidence.json"
    ev.write_text("{}")
    return v, ev


def _walker(engine_env, tmp_path, pid):
    """Return (run, reclone, ry) bound to a fresh instance dir under this origin.
    `run` invokes an engine script (asserting rc==0) with per-call NODE_PATH env
    overrides; `reclone` re-checks-out the state branch (proving cas_push ran);
    `ry` loads a YAML state file."""
    base = dict(engine_env)
    base["PR_HEAD_SHA"] = "abc123"
    base["AGENT_RUN_ID"] = "r"
    base["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"

    def run(script, *args, **env_extra):
        e = dict(base); e.update(env_extra)
        r = subprocess.run(["python3", str(script), *map(str, args)],
                           text=True, capture_output=True, env=e)
        assert r.returncode == 0, f"{pathlib.Path(script).name} {args}: {r.stderr}"
        return r

    def reclone(tag):
        fresh = tmp_path / f"rc-{tag}"
        subprocess.run(["git", "clone", "-q", "-b", "agentic-state",
                        engine_env["STATE_REMOTE"], str(fresh)], check=True)
        return fresh / pid / "pr-1"

    def ry(p):
        with open(p) as fh:
            return yaml.safe_load(fh)

    return run, reclone, ry


def test_dynamic_subpipeline_walks_to_done(engine_env, tmp_path):
    """REQUIRED: the FULL offline walk of a dynamic SUB-PIPELINE `each`
    (draft→finalize) — the previously-untested advance.py path for a dynamic
    sub-pipeline leg. start seeds N legs each at sub_state `draft`; each leg is
    driven draft→finalize→leg-done; the `all` join then clears the aggregate.

    On-disk layout (single-phase → state_path drops the leading `review` id):
      review.__manifest.yaml   ·   <lid>.yaml (leg cursor)
      <lid>.draft.yaml   ·   <lid>.finalize.yaml (sub-states)."""
    run, reclone, ry = _walker(engine_env, tmp_path, "dyn-fanout-subpipeline")
    v, ev = _pass_verdicts_t10(tmp_path)

    # 1. start → dynamic review fanout seeds a per-file sub-pipeline leg, each at
    #    its first sub-state `draft`.
    r1 = run(NEXT, tmp_path / "s1", "pr-1", SUBPIPE_PROTO, "start", "abc123")
    act = json.loads(r1.stdout.strip().splitlines()[-1])
    assert act["action"] == "run-fanout"
    d = reclone("1")
    man = ry(d / "review.__manifest.yaml")
    assert man["count"] == 2
    lids = [leg["id"] for leg in man["legs"]]
    # Every leg's cursor points at draft; the draft sub-state file is seeded.
    for lid in lids:
        cur = ry(d / f"{lid}.yaml")
        assert cur["sub_state"] == "draft"
        assert cur["state"] == "review"          # leg life-state = enclosing fanout id
        assert (d / f"{lid}.draft.yaml").is_file()
        assert not (d / f"{lid}.finalize.yaml").is_file()  # not seeded yet

    # 2. drive each leg: draft→finalize (cursor advances, continue seeds finalize),
    #    finalize→leg-done (cursor state=done, path-less top join fired).
    for lid in lids:
        rd = run(ADVANCE, tmp_path / f"ad-{lid}", "pr-1", SUBPIPE_PROTO, v, ev,
                 NODE_PATH=f"review.{lid}.draft")
        # draft→finalize is an agent→agent hop: a continue re-dispatch, not a join.
        assert "event_type=protocol-continue" in rd.stderr
        assert f"client_payload[path]=review.{lid}.finalize" in rd.stderr
        dd = reclone(f"{lid}-draft")
        cur = ry(dd / f"{lid}.yaml")
        assert cur["sub_state"] == "finalize"
        assert cur["state"] == "review"          # leg still in flight
        assert ry(dd / f"{lid}.draft.yaml")["state"] == "done"

        # The dispatched continue seeds the finalize sub-state file.
        run(NEXT, tmp_path / f"cf-{lid}", "pr-1", SUBPIPE_PROTO, "continue",
            NODE_PATH=f"review.{lid}.finalize")
        assert (reclone(f"{lid}-finc") / f"{lid}.finalize.yaml").is_file()

        rf = run(ADVANCE, tmp_path / f"af-{lid}", "pr-1", SUBPIPE_PROTO, v, ev,
                 NODE_PATH=f"review.{lid}.finalize")
        # finalize is the LAST sub-state → leg terminal → fire_join. `review` is
        # the TOP fanout, so the join is path-LESS (byte-identical to legacy).
        assert "event_type=protocol-join" in rf.stderr
        assert "client_payload[path]=" not in rf.stderr
        df = reclone(f"{lid}-fin")
        assert ry(df / f"{lid}.yaml")["state"] == "done"   # leg cursor terminal

    # 3. top join → policy `all`, both legs done → aggregate success, joined.
    run(JOIN, tmp_path / "j", "pr-1", SUBPIPE_PROTO)
    inst = ry(reclone("final") / "_instance.yaml")
    assert inst["joined"] is True


def test_dynamic_nested_materializes_comments_fanout(engine_env, tmp_path):
    """REQUIRED: a review leg's `prep` agent advances so its sub-pipeline enters
    the NESTED `comments` dynamic fanout; assert the nested manifest is
    materialized and its comment legs are seeded.

    Nested paths (manifest keys by the FULL tree path; leg/join files drop the
    leading single-phase `review` id):
      review.<lid>.comments.__manifest.yaml   (nested manifest)
      <lid>.comments.__join.yaml              (nested join marker)
      <lid>.comments.<cid>.yaml               (flat comment legs)."""
    run, reclone, ry = _walker(engine_env, tmp_path, "dyn-nested")
    v, ev = _pass_verdicts_t10(tmp_path)

    # 1. start → per-file review legs, each a sub-pipeline at sub_state `prep`.
    run(NEXT, tmp_path / "s1", "pr-1", NESTED_PROTO, "start", "abc123")
    d = reclone("1")
    man = ry(d / "review.__manifest.yaml")
    lids = [leg["id"] for leg in man["legs"]]
    L = lids[0]
    assert ry(d / f"{L}.yaml")["sub_state"] == "prep"
    assert (d / f"{L}.prep.yaml").is_file()

    # 2. advance the leg's prep. Its next sibling is a FANOUT → advance.py moves
    #    the cursor onto `comments` and re-dispatches protocol-continue with the
    #    fanout path, WITHOUT seeding the fanout legs (the continue does that).
    rp = run(ADVANCE, tmp_path / "ap", "pr-1", NESTED_PROTO, v, ev,
             NODE_PATH=f"review.{L}.prep")
    assert "event_type=protocol-continue" in rp.stderr
    assert f"client_payload[path]=review.{L}.comments" in rp.stderr
    dp = reclone("prep")
    assert ry(dp / f"{L}.yaml")["sub_state"] == "comments"
    assert ry(dp / f"{L}.yaml")["state"] == "review"        # leg stays in flight
    # NOT materialized yet — the fanout is entered by the follow-on continue.
    assert not (dp / f"review.{L}.comments.__manifest.yaml").is_file()

    # 3. continue NODE_PATH=review.<L>.comments → seeds the nested manifest, the
    #    per-comment leg files, and the path-keyed nested __join marker.
    rc = run(NEXT, tmp_path / "cc", "pr-1", NESTED_PROTO, "continue",
             NODE_PATH=f"review.{L}.comments")
    assert json.loads(rc.stdout.strip().splitlines()[-1])["action"] == "run-fanout"
    dc = reclone("comments")
    cman = ry(dc / f"review.{L}.comments.__manifest.yaml")   # FULL tree-path key
    assert cman["count"] == 2
    clids = [leg["id"] for leg in cman["legs"]]
    for cid in clids:
        assert (dc / f"{L}.comments.{cid}.yaml").is_file()   # nested-scope leg file
    marker = ry(dc / f"{L}.comments.__join.yaml")
    assert marker["joined"] is False


def test_dynamic_nested_walks_to_done(engine_env, tmp_path):
    """DESIRABLE: the full NESTED walk — every review leg driven
    prep→comments(dynamic fanout)→cjoin, the nested join bubbling each review leg
    to done, then the top `review` join clearing the aggregate to joined=True."""
    run, reclone, ry = _walker(engine_env, tmp_path, "dyn-nested")
    v, ev = _pass_verdicts_t10(tmp_path)

    run(NEXT, tmp_path / "s1", "pr-1", NESTED_PROTO, "start", "abc123")
    man = ry(reclone("1") / "review.__manifest.yaml")
    rlids = [leg["id"] for leg in man["legs"]]

    for L in rlids:
        # prep → enter comments fanout.
        run(ADVANCE, tmp_path / f"ap-{L}", "pr-1", NESTED_PROTO, v, ev,
            NODE_PATH=f"review.{L}.prep")
        run(NEXT, tmp_path / f"cc-{L}", "pr-1", NESTED_PROTO, "continue",
            NODE_PATH=f"review.{L}.comments")
        cman = ry(reclone(f"cm-{L}") / f"review.{L}.comments.__manifest.yaml")
        clids = [leg["id"] for leg in cman["legs"]]

        # Drive every comment leg to done.
        for cid in clids:
            rcv = run(ADVANCE, tmp_path / f"ac-{L}-{cid}", "pr-1", NESTED_PROTO, v, ev,
                      NODE_PATH=f"review.{L}.comments.{cid}")
            # A flat nested-fanout child fires the ENCLOSING fanout's path-keyed join.
            assert f"client_payload[path]=review.{L}.comments" in rcv.stderr
        dc = reclone(f"cd-{L}")
        for cid in clids:
            assert ry(dc / f"{L}.comments.{cid}.yaml")["state"] == "done"
        # A flat fanout child must NOT write the enclosing fanout's cursor file.
        assert not (dc / f"{L}.comments.yaml").is_file()

        # nested join (policy `any`, all comment legs done) → cjoin has no `.next`,
        # so the review leg's sub-pipeline ends: mark the leg cursor done + bubble
        # to the TOP fanout's (path-less) join.
        rj = run(JOIN, tmp_path / f"nj-{L}", "pr-1", NESTED_PROTO,
                 NODE_PATH=f"review.{L}.comments")
        assert "event_type=protocol-join" in rj.stderr
        assert "client_payload[path]=" not in rj.stderr  # enclosing is the TOP fanout
        dnj = reclone(f"nj-{L}")
        assert ry(dnj / f"{L}.comments.__join.yaml")["joined"] is True
        assert ry(dnj / f"{L}.yaml")["state"] == "done"   # review leg cursor terminal

    # top join → policy `any`, both review legs done → aggregate joined.
    run(JOIN, tmp_path / "tj", "pr-1", NESTED_PROTO)
    final = reclone("final")
    assert ry(final / "_instance.yaml")["joined"] is True
    # Both review-leg cursors are done; both nested join markers cleared cleanly.
    for L in rlids:
        assert ry(final / f"{L}.yaml")["state"] == "done"
        assert ry(final / f"{L}.comments.__join.yaml")["joined"] is True
        assert not ry(final / f"{L}.comments.__join.yaml").get("failed")


def test_run_merge_hook_missing_manifest_fails_loud(tmp_path):
    lib = _load_lib()
    import json
    pid, inst = "dyn-fanout-flat", "pr-1"
    d = str(tmp_path)
    proto = str(ROOT / "tests/fixtures/dyn-fanout-flat/protocol.json")
    with open(proto) as f:
        merge_state = next(s for s in json.load(f)["states"] if s.get("kind") == "merge")
    # No manifest written at all → must fail loud, not silently reduce zero legs.
    try:
        lib.run_merge_hook(d, pid, inst, proto, merge_state)
        assert False, "expected ValueError on missing manifest"
    except ValueError as e:
        assert "from_fanout" in str(e) and "manifest" in str(e)


def test_run_merge_hook_zero_item_manifest_is_ok(tmp_path):
    """A manifest that EXISTS with count 0 is a legit vacuous reduce, not an error."""
    lib = _load_lib()
    import json
    pid, inst = "dyn-fanout-flat", "pr-1"
    d = str(tmp_path)
    lib.write_manifest(d, pid, inst, ["review"], {"count": 0, "legs": []})
    proto = str(ROOT / "tests/fixtures/dyn-fanout-flat/protocol.json")
    with open(proto) as f:
        merge_state = next(s for s in json.load(f)["states"] if s.get("kind") == "merge")
    result = lib.run_merge_hook(d, pid, inst, proto, merge_state)
    assert result["conclusion"] == "success"  # reduce hook runs over 0 legs, returns its verdict
    assert "reduced 0/0 legs" in result["summary"]


EXPANDER = ".github/agent-factory/protocols/dyn-fanout-stub/expand/expand-files"


def _run_expander(env_extra):
    env = {**os.environ, **env_extra}
    r = subprocess.run([EXPANDER, "/tmp/state", "pr-1"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["items"]


def test_expand_files_parses_one_item_per_file(tmp_path):
    diff = tmp_path / "diff.txt"
    diff.write_text(textwrap.dedent("""\
        diff --git a/src/a.py b/src/a.py
        index 111..222 100644
        --- a/src/a.py
        +++ b/src/a.py
        @@ -1 +1,2 @@
         x = 1
        +y = 2
        diff --git a/src/b.py b/src/b.py
        index 333..444 100644
        --- a/src/b.py
        +++ b/src/b.py
        @@ -1 +1 @@
        -old
        +new
        """))
    items = _run_expander({"EXPAND_FILES_DIFF_FILE": str(diff)})
    assert [i["path"] for i in items] == ["src/a.py", "src/b.py"]
    assert "y = 2" in items[0]["diff"]

def test_expand_files_skips_binary_vendored_oversized(tmp_path):
    diff = tmp_path / "diff.txt"
    body = "\n".join(f"        +line{i}" for i in range(2000))
    diff.write_text(textwrap.dedent(f"""\
        diff --git a/img.png b/img.png
        Binary files a/img.png and b/img.png differ
        diff --git a/vendor/dep.py b/vendor/dep.py
        index 1..2 100644
        --- a/vendor/dep.py
        +++ b/vendor/dep.py
        @@ -1 +1 @@
        -a
        +b
        diff --git a/big.py b/big.py
        index 5..6 100644
        --- a/big.py
        +++ b/big.py
        @@ -0,0 +1,2000 @@
        {body}
        diff --git a/keep.py b/keep.py
        index 7..8 100644
        --- a/keep.py
        +++ b/keep.py
        @@ -1 +1 @@
        -a
        +b
        """))
    items = _run_expander({
        "EXPAND_FILES_DIFF_FILE": str(diff),
        "EXPAND_PARAMS": json.dumps({"max_diff_lines": 1500}),
    })
    assert [i["path"] for i in items] == ["keep.py"]

def test_expand_files_engine_local_reads_fixture():
    items = _run_expander({"ENGINE_LOCAL": "1"})
    assert len(items) >= 2 and all("path" in i for i in items)


STUB = str(ROOT / ".github/agent-factory/protocols/dyn-fanout-stub/protocol.json")


def test_dyn_stub_start_materializes_legs(engine_env, tmp_path):
    """Offline engine walk for the dyn-fanout-stub protocol (Task 2): `start`
    on the real (non-fixture) protocol drives the real expand-files expander
    (ENGINE_LOCAL reads its beside-script items.json, 2 entries) and
    materializes one leg per item, joined under policy:all."""
    from conftest import run_engine, read_state_yaml
    out, err, rc = run_engine("next.py", str(tmp_path), "pr-7", STUB, "start", env=engine_env)
    assert rc == 0, err
    action = json.loads(out.strip().splitlines()[-1])
    assert action["action"] == "run-fanout"
    legs = action["legs"]
    assert len(legs) == 2                                 # one leg per fixture item
    assert all(l["workflow"] == "dyn-stub-agent" for l in legs)
    # Leg paths are fanout_path + leg_id ("review.<legid>") — the top-level
    # fanout id is always the leaf's first segment, per _fanout_action; take
    # the last dot-segment as the leg id, mirroring
    # test_dynamic_fanout_start_seeds_manifest_and_legs above.
    assert all(l["path"].split(".")[0] == "review" for l in legs)
    d = str(tmp_path) + "/dyn-fanout-stub/pr-7"
    man = read_state_yaml(d + "/review.__manifest.yaml")
    assert man["count"] == 2


# ---------------------------------------------------------------------------
# Task 2 fix — exit-0 ABI guard
# ---------------------------------------------------------------------------


from conftest import run_check

CHECK = str(ROOT / ".github/agent-factory/protocols/dyn-fanout-stub/checks/examined-file.py")


@pytest.mark.parametrize("bad", ["[]", "null", "\"x\"", "5", "{}", "{\"examined\": []}", "not json"])
def test_examined_file_check_always_exits_zero(bad, tmp_path):
    """The examined-file check must ALWAYS exit 0 (per Check ABI) even with
    garbage evidence. Non-dict top-level JSON, missing 'examined', or empty
    examined list must all yield pass:false without crashing."""
    ev = tmp_path / "evidence.json"
    ev.write_text(bad)
    diff = tmp_path / "d.txt"
    diff.write_text("")
    ch = tmp_path / "c.txt"
    ch.write_text("")
    # run_check raises if the check crashed (non-JSON stdout); this test passes
    # only if the check printed valid JSON and exited 0.
    result = run_check(CHECK, ev, diff, ch)
    assert result["check"] == "examined-file"
    assert result["pass"] is False


def test_examined_file_check_accepts_valid_evidence(tmp_path):
    """Well-formed evidence with a non-empty examined list yields pass:true."""
    ev = tmp_path / "evidence.json"
    ev.write_text(json.dumps({"examined": ["src/a.py"]}))
    diff = tmp_path / "d.txt"
    diff.write_text("")
    ch = tmp_path / "c.txt"
    ch.write_text("")
    result = run_check(CHECK, ev, diff, ch)
    assert result["check"] == "examined-file"
    assert result["pass"] is True


# ---------------------------------------------------------------------------
# Task 3 — matrix cap + multi-phase state_path naming
# ---------------------------------------------------------------------------


def test_dyn_matrix_cap_matches_max_legs():
    # GHA strategy.matrix hard-caps at 256; M1 max_legs must never exceed it.
    proto = json.load(open(STUB))
    fo = next(s for s in proto["states"] if s.get("kind") == "fanout")
    assert fo["expand"]["max_legs"] <= 256


def test_state_path_multiphase_keeps_leading_id_singlephase_drops_it():
    # B de-risk: a leg's on-disk file name depends on is_multiphase. Multi-phase
    # (>=2 phase states) keeps the full tree path -> review.<legid>.yaml; single-phase
    # (one top fanout) drops the leading id -> <legid>.yaml. Pure lib.state_path unit.
    lib = _load_lib()
    mp = {"name": "mp", "states": [
        {"id": "preflight", "kind": "agent", "workflow": "a"},
        {"id": "review", "kind": "fanout",
         "expand": {"hook": "e", "as": "f", "id_from": "$.path", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "join"},
        {"id": "join", "kind": "join", "of": "review", "next": "done"}]}
    sp = {"name": "sp", "states": [
        {"id": "review", "kind": "fanout",
         "expand": {"hook": "e", "as": "f", "id_from": "$.path", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "join"},
        {"id": "join", "kind": "join", "of": "review", "next": "done"}]}
    assert lib.state_path(mp, ["review", "abcd1234"]) == ["review", "abcd1234"]
    assert lib.state_path(sp, ["review", "abcd1234"]) == ["abcd1234"]


# ---------------------------------------------------------------------------
# Task 4 — per-leg runtime item threaded to the agent via matrix.leg.inputs
# ---------------------------------------------------------------------------


def test_dyn_legs_carry_per_leg_inputs(engine_env, tmp_path):
    from conftest import run_engine
    out, err, rc = run_engine("next.py", str(tmp_path), "pr-13", STUB, "start", env=engine_env)
    assert rc == 0, err
    action = json.loads(out.strip().splitlines()[-1])
    legs = action["legs"]
    assert len(legs) == 2
    for leg in legs:
        assert "inputs" in leg, "dynamic leg must carry its runtime item"
        assert "file" in leg["inputs"], "keyed by the expand `as` name"
        assert "path" in leg["inputs"]["file"]
    # per-leg correctness: the two legs must carry DIFFERENT items (not leg0's item cloned)
    paths = [leg["inputs"]["file"]["path"] for leg in legs]
    assert len(set(paths)) == len(paths), f"legs must carry distinct items, got {paths}"


def test_static_fanout_legs_have_no_inputs(engine_env, tmp_path):
    from conftest import run_engine
    # Regression: a static fanout's legs are byte-identical (no inputs key).
    SF = str(ROOT / "tests/fixtures/simple-fanout/protocol.json")
    out, err, rc = run_engine("next.py", str(tmp_path), "pr-13", SF, "start", env=engine_env)
    assert rc == 0, err
    action = json.loads(out.strip().splitlines()[-1])
    assert all("inputs" not in leg for leg in action["legs"])


# ---------------------------------------------------------------------------
# Task 6 — dynamic-leg-aware rendering (status comment)
# ---------------------------------------------------------------------------


def test_status_body_renders_dynamic_legs(engine_env, tmp_path):
    from conftest import run_engine, read_state_yaml
    lib = _load_lib()
    out, err, rc = run_engine("next.py", str(tmp_path), "pr-15", STUB, "start", env=engine_env)
    assert rc == 0, err
    # dir_ for the renderer is the state ROOT (manifest_file keys as <dir>/<pid>/<inst>/...)
    body = lib.render_fanout_status_body(str(tmp_path), "dyn-fanout-stub", "pr-15", STUB)
    d = str(tmp_path) + "/dyn-fanout-stub/pr-15"
    man = read_state_yaml(d + "/review.__manifest.yaml")
    for leg in man["legs"]:                    # both dynamic leg ids appear (zero pre-fix)
        assert leg["id"] in body


# ---------------------------------------------------------------------------
# Task 1 (M2 Spec B) — expand.matrix_fields declarative matrix projection +
# fail-loud size guard.
# ---------------------------------------------------------------------------


def test_project_matrix_item_subsets_when_fields_given():
    lib = _load_lib()
    item = {"path": "src/a.py", "diff": "x" * 10000}
    assert lib.project_matrix_item(item, ["path"]) == {"path": "src/a.py"}
    assert lib.project_matrix_item(item, None) == item          # default = full item
    assert lib.project_matrix_item(item, ["path", "missing"]) == {"path": "src/a.py"}  # skip absent


def test_check_matrix_size_raises_over_cap():
    lib = _load_lib()
    big = [{"path": "l", "workflow": "w", "inputs": {"f": {"diff": "x" * 900_000}}} for _ in range(3)]
    try:
        lib.check_matrix_size(big); assert False, "expected ValueError"
    except ValueError as e:
        assert "matrix" in str(e).lower()
    lib.check_matrix_size([{"path": "l", "workflow": "w", "inputs": {"f": {"path": "a"}}}])  # small: ok


def test_matrix_fields_trims_leg_inputs_full_item_still_staged(engine_env, tmp_path):
    # A dyn fixture whose expand sets matrix_fields:["path"] but items also carry "diff".
    import shutil, json as _json, pathlib
    from conftest import run_engine, read_state_yaml
    src = ROOT / "tests/fixtures/dyn-fanout-flat"
    dst = tmp_path / "proto"; shutil.copytree(src, dst)
    proto = _json.load(open(dst / "protocol.json"))
    proto["states"][0]["expand"]["matrix_fields"] = ["path"]
    _json.dump(proto, open(dst / "protocol.json", "w"))
    # items carry an extra big field
    items = [{"path": "src/a.go", "diff": "X" * 5000}, {"path": "src/b.go", "diff": "Y" * 5000}]
    _json.dump(items, open(dst / "expand" / "items.json", "w"))
    out, err, rc = run_engine("next.py", str(tmp_path / "state"), "pr-1", str(dst / "protocol.json"), "start", env=engine_env)
    assert rc == 0, err
    action = json.loads(out.strip().splitlines()[-1])
    for leg in action["legs"]:
        assert set(leg["inputs"]["file"].keys()) == {"path"}          # trimmed for the matrix
    # full item (with diff) is still staged on the state branch
    d = str(tmp_path / "state") + "/dyn-fanout-flat/pr-1"
    staged = _json.load(open(d + "/" + read_state_yaml(d + "/review.__manifest.yaml")["legs"][0]["id"] + ".file.item.json"))
    assert "diff" in staged and staged["diff"]


# ---------------------------------------------------------------------------
# Task 2 — nested from_fanout resolution + leg-terminal nested merge (ocr-nested)
# ---------------------------------------------------------------------------

OCR_NESTED_PROTO = ROOT / "tests/fixtures/ocr-nested/protocol.json"


def _proto_load(path):
    with open(path) as f:
        return json.load(f)


def test_nested_from_fanout_reduces_over_nested_legs(tmp_path):
    """The collector is path-general: a NESTED findings fanout (keyed by the full
    tree path) yields its own legs, not a top fanout's."""
    lib = _load_lib()
    paths = _load_paths()
    d, pid, inst = str(tmp_path), "ocr-nested", "pr-1"
    proto = str(OCR_NESTED_PROTO)
    proto_data = _proto_load(proto)
    fileleg = "abc123de"   # a synthetic file leg id
    findings_path = ["review", fileleg, "findings"]
    lib.write_manifest(d, pid, inst, findings_path,
        {"count": 2, "legs": [{"id": "f1", "key": "f1", "item": {"fid": "f1"}},
                              {"id": "f2", "key": "f2", "item": {"fid": "f2"}}]})
    # per-leg evidence + state files, keyed by the NESTED tree path.
    for fid, keep in [("f1", True), ("f2", False)]:
        sf = lib.state_file(d, pid, inst, path=lib.state_path(proto_data, findings_path + [fid]))
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        lib.dump_yaml(sf, {"state": "done"})
        ev = lib.output_artifact_path(d, pid, inst, path=lib.state_path(proto_data, findings_path + [fid]))
        with open(ev, "w") as f:
            json.dump({"fid": fid, "keep": keep}, f)
    fo_node = paths.node_at_path(proto_data, findings_path)
    rows = lib.collect_fanout_evidence(d, pid, inst, findings_path, fo_node, proto=proto_data)
    assert {r["leg_id"] for r in rows} == {"f1", "f2"}   # NESTED legs, not a top fanout
    # Resolved from the REAL nested files (not a flat/empty lookup): state is
    # "done" and evidence CONTENT round-trips per leg.
    by_id = {r["leg_id"]: r for r in rows}
    assert by_id["f1"]["state"] == "done" and by_id["f1"]["evidence"]["keep"] is True
    assert by_id["f2"]["state"] == "done" and by_id["f2"]["evidence"]["keep"] is False


def test_run_merge_hook_nested_from_fanout_resolves(tmp_path):
    """A per-file `reduce` (a nested merge) resolves its from_fanout RELATIVE to
    its node-path: consuming_path[:-1] + [findings], NOT the top-level [findings].
    Before the fix this raised 'nested from_fanout is not supported yet'."""
    lib = _load_lib()
    d, pid, inst = str(tmp_path), "ocr-nested", "pr-1"
    proto = str(OCR_NESTED_PROTO)
    proto_data = _proto_load(proto)
    fileleg = "abc123de"
    findings_path = ["review", fileleg, "findings"]
    consuming_path = ["review", fileleg, "reduce"]
    lib.write_manifest(d, pid, inst, findings_path,
        {"count": 2, "legs": [{"id": "f1", "key": "f1", "item": {"fid": "f1"}},
                              {"id": "f2", "key": "f2", "item": {"fid": "f2"}}]})
    reduce_state = next(s for s in proto_data["states"][0]["each"]["states"]
                        if s.get("id") == "reduce")
    res = lib.run_merge_hook(d, pid, inst, proto, reduce_state, consuming_path=consuming_path)
    assert res["conclusion"] == "success"
    assert "findings" in res["summary"]


def test_validate_accepts_nested_from_fanout(tmp_path):
    """Rule 6 validates a merge's from_fanout against a sibling fanout AT ITS OWN
    LEVEL (a nested sub-pipeline), not only a top-level state."""
    lib = _load_lib()
    proto = _proto_load(OCR_NESTED_PROTO)
    lib.validate_protocol(proto)   # must not raise: `reduce` ← `findings` (nested sibling)


def test_ocr_nested_walk_reduces_and_merges(engine_env, tmp_path):
    """Full offline walk: file fanout -> per file (main -> findings fanout -> jf ->
    reduce) -> jr -> merge. A per-file `reduce` is LEG-TERMINAL: it marks the file
    leg done and fires the enclosing (top-level → path-less) `review` join; the top
    `merge` then reduces over both file legs."""
    run, reclone, ry = _walker(engine_env, tmp_path, "ocr-nested")
    v, ev = _pass_verdicts_t10(tmp_path)

    # 1. start → per-file review legs, each a sub-pipeline at sub_state `main`.
    run(NEXT, tmp_path / "s1", "pr-1", OCR_NESTED_PROTO, "start", "abc123")
    man = ry(reclone("1") / "review.__manifest.yaml")
    rlids = [leg["id"] for leg in man["legs"]]
    assert len(rlids) == 2

    for L in rlids:
        # main → enter findings fanout (next sibling is a FANOUT).
        run(ADVANCE, tmp_path / f"am-{L}", "pr-1", OCR_NESTED_PROTO, v, ev,
            NODE_PATH=f"review.{L}.main")
        run(NEXT, tmp_path / f"cf-{L}", "pr-1", OCR_NESTED_PROTO, "continue",
            NODE_PATH=f"review.{L}.findings")
        fman = ry(reclone(f"fm-{L}") / f"review.{L}.findings.__manifest.yaml")
        flids = [leg["id"] for leg in fman["legs"]]
        assert len(flids) == 2

        # drive every finding leg to done → each fires the nested findings join.
        for fid in flids:
            rfv = run(ADVANCE, tmp_path / f"af-{L}-{fid}", "pr-1", OCR_NESTED_PROTO, v, ev,
                      NODE_PATH=f"review.{L}.findings.{fid}")
            assert f"client_payload[path]=review.{L}.findings" in rfv.stderr
        dfd = reclone(f"fd-{L}")
        for fid in flids:
            assert ry(dfd / f"{L}.findings.{fid}.yaml")["state"] == "done"

        # nested findings join (policy `any`) clears → `.next` is `reduce`: dispatch
        # a path-continue onto the merge sub-state.
        rj = run(JOIN, tmp_path / f"jf-{L}", "pr-1", OCR_NESTED_PROTO,
                 NODE_PATH=f"review.{L}.findings")
        assert f"client_payload[path]=review.{L}.reduce" in rj.stderr
        assert ry(reclone(f"jfd-{L}") / f"{L}.findings.__join.yaml")["joined"] is True

        # continue onto the per-file `reduce` merge → LEG-TERMINAL: run reduce hook,
        # persist leg evidence, mark the file-leg cursor done, fire the (path-less)
        # enclosing `review` join.
        rr = run(NEXT, tmp_path / f"rd-{L}", "pr-1", OCR_NESTED_PROTO, "continue",
                 NODE_PATH=f"review.{L}.reduce")
        assert "event_type=protocol-join" in rr.stderr
        assert "client_payload[path]=" not in rr.stderr   # enclosing review is TOP-level
        drd = reclone(f"rd-{L}")
        assert ry(drd / f"{L}.yaml")["state"] == "done"    # file leg cursor terminal
        reduce_ev_path = drd / f"{L}.reduce.evidence.json"
        assert reduce_ev_path.is_file()   # reduce leg evidence
        # The per-file reduce hook (reduce-file.py) counts findings legs whose
        # terminal STATE it read as "done", via collect_fanout_evidence resolving
        # the NESTED findings legs by their real tree path (review.<L>.findings.<fid>).
        # Before the collector was made nested-aware, the flat lookup found no
        # state files here and this would read "reduced 0/2 findings".
        with open(reduce_ev_path) as f:
            reduce_result = json.load(f)
        assert reduce_result["summary"] == f"reduced {len(flids)}/{len(flids)} findings"

    # 2. top `review` join → policy `any`, both file legs done → advance to `merge`.
    rtj = run(JOIN, tmp_path / "tj", "pr-1", OCR_NESTED_PROTO)
    assert "client_payload[path]=merge" in rtj.stderr
    assert ry(reclone("tj") / "_instance.yaml")["joined"] is True

    # 3. continue onto the top `merge` → reduce over both file legs, finalize.
    run(NEXT, tmp_path / "m", "pr-1", OCR_NESTED_PROTO, "continue", NODE_PATH="merge")
    final = reclone("final")
    inst = ry(final / "_instance.yaml")
    assert inst["joined"] is True
    assert inst["phase"] == "merge"
