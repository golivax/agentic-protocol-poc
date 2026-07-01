# Dynamic (data-driven) Fan-out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the engine's `fanout` node so its leg set can be produced at runtime by a trusted expander hook (a data-driven matrix), with a runtime-cardinality join (`all`/`any`/`quorum:N`) and a reduce over the surviving legs — all backward-compatible with the existing static `branches[]`.

**Architecture:** Approach B from the design spec (`docs/superpowers/specs/2026-06-30-dynamic-fanout-design.md`): keep one `fanout` kind. A dynamic fanout carries `expand` + `each` instead of `branches[]`. On entry the engine runs the expander (trusted, zone-1), bounds + keys the items, and persists a `<tree-path>.__manifest.yaml`; a `resolve_legs` seam then feeds the **existing** recursive enter/advance/join stack, which already handles N legs of arbitrary length. The manifest is the durable dynamic analog of static `branches[]`.

**Tech Stack:** Python 3 + PyYAML (engine runtime); pytest (dev-only test layer, `ENGINE_LOCAL=1`); JSON Schema (`protocol.schema.json`). No new dependencies.

---

## Orientation — read before starting

- **Spec:** `docs/superpowers/specs/2026-06-30-dynamic-fanout-design.md` (the contract this plan implements).
- **Engine files you will touch** (all under `.github/agent-factory/engine/`):
  - `lib.py` — helpers: state paths, join markers, `resolve_executable`, `_validate_sequence`, `resolve_inputs`, `run_merge_hook`. **The bulk of new pure code lands here.**
  - `next.py` — `enter_node` (the fanout branch at lines ~111-125 is the core seam).
  - `join.py` — `main()` (top fanout, line ~182) and `_nested_join` (line ~41): the two `branches = [b["id"] …]` lines are the swap points; policy is applied at the `all_done`/`concl` decision.
  - `protocol.schema.json` — add the new keys.
- **Test layer:** `tests/conftest.py` provides `engine_env` (sets `ENGINE_LOCAL=1` + `STATE_REMOTE`), `run_engine("next.py", …)`, `read_state_yaml`. New tests are `tests/test_*.py` pytest modules. Run with `uv run pytest tests/ -q`.
- **Key existing conventions to imitate:**
  - State files: `<dir>/<pid>/<instance>/<dot-joined-path>.yaml`; `lib.state_path(proto, tree_path)` drops the leading id when single-phase (byte-identity for legacy). **The manifest is a NEW file and keys by the FULL tree path** (never dropped) so it is always unique and non-empty.
  - Join markers use the `__join.yaml` suffix convention (`lib.join_marker_file`); the manifest mirrors it with `__manifest.yaml`.
  - Hooks resolve via `lib.resolve_executable(search_dir, name, protocol_dir, exec_override)` → `"OK\t<path>"` / `"ERR\t<reason>"`.
  - `ENGINE_LOCAL` short-circuits network calls (`gh_api`, check-runs) — offline tests rely on this.

## File Structure (what changes and why)

- **Create** `tests/fixtures/dyn-fanout-flat/` — a single-phase protocol: `expand → N flat legs → join(any) → merge`. Includes `protocol.json`, evidence schema, a stub `expand/expand-items.py`, a stub check, a stub `publish/reduce.py`, and an `items.json` the stub expander echoes.
- **Create** `tests/fixtures/dyn-fanout-subpipeline/` — `each` is a `draft → finalize` sub-pipeline (proves the OCR per-file pipeline shape).
- **Create** `tests/fixtures/dyn-nested/` — a dynamic leg that itself contains a second dynamic fanout (OCR's file→comment nesting).
- **Create** `tests/fixtures/dyn-fanout-badcap/` — expander emits more than `max_legs` (over-cap guard).
- **Create** `tests/test_dynamic_fanout.py` — the new pytest module (unit + offline e2e).
- **Modify** `.github/agent-factory/engine/lib.py` — new pure helpers (manifest I/O, leg-id, expander runner, policy, from_fanout collection) + validation rules.
- **Modify** `.github/agent-factory/engine/next.py` — dynamic branch of `enter_node`'s `fanout` handling.
- **Modify** `.github/agent-factory/engine/join.py` — leg-id resolution from manifest + policy application (both `main` and `_nested_join`).
- **Modify** `.github/agent-factory/engine/protocol.schema.json` — new keys with `additionalProperties:false`.
- **Modify** `docs/PROTOCOL-DSL.md` — document `expand`/`each`/`policy`/`from_fanout` (docs task, last).

The design deliberately concentrates new logic in **pure `lib.py` helpers** (each independently unit-testable) so the `next.py`/`join.py` seams stay thin.

---

## Task 1: Manifest I/O + leg-id helpers (`lib.py`)

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add near the `join_marker_file` block, ~line 107)
- Test: `tests/test_dynamic_fanout.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dynamic_fanout.py
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"


def _load_lib():
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -q`
Expected: FAIL — `AttributeError: module 'lib' has no attribute 'write_manifest'`.

- [ ] **Step 3: Write minimal implementation**

Add to `lib.py` immediately after `write_join` (line ~106). (`hashlib` — add `import hashlib` to the top-of-file imports if absent.)

```python
def manifest_file(d, pid, instance, tree_path):
    """Path to a dynamic fanout's manifest. Unlike leg/join files this is a NEW
    file with no legacy byte-identity constraint, so it keys by the FULL tree
    path (never dropped by state_path) — always unique and non-empty, for the
    top fanout (['review'] -> review.__manifest.yaml) and nested alike."""
    base = f"{d}/{pid}/{instance}"
    return f"{base}/{'.'.join(tree_path)}.__manifest.yaml"


def read_manifest(d, pid, instance, tree_path):
    """Read the manifest dict, or {} if it does not exist yet."""
    f = manifest_file(d, pid, instance, tree_path)
    return load_yaml(f) if os.path.isfile(f) else {}


def write_manifest(d, pid, instance, tree_path, data):
    f = manifest_file(d, pid, instance, tree_path)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    dump_yaml(f, data)


def leg_id(raw_key):
    """Stable, filesystem-safe leg id from an item's raw id_from value.
    A short sha1 hex is alnum by construction (no sanitizing needed)."""
    return hashlib.sha1(str(raw_key).encode("utf-8")).hexdigest()[:8]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dynamic_fanout.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_dynamic_fanout.py
git commit -m "feat(engine): manifest I/O + leg-id helpers for dynamic fanout"
```

---

## Task 2: Item-key extraction + manifest builder (`lib.py`)

Builds the manifest from an items list: extracts each leg's id via `id_from`, hashes it, and **fails loud** on over-cap or duplicate keys.

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (after `leg_id`)
- Test: `tests/test_dynamic_fanout.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -k build_manifest -q`
Expected: FAIL — `AttributeError: module 'lib' has no attribute 'build_manifest'`.

- [ ] **Step 3: Write minimal implementation**

Add to `lib.py` after `leg_id`:

```python
def extract_key(item, id_from):
    """Resolve a simple JSONPath (`$.a.b`) against an item dict. Only the
    dotted-`$.`-rooted form is supported (YAGNI — no wildcards/filters)."""
    if not id_from.startswith("$."):
        raise ValueError(f"id_from must start with '$.', got {id_from!r}")
    cur = item
    for seg in id_from[2:].split("."):
        if not isinstance(cur, dict) or seg not in cur:
            raise ValueError(f"id_from {id_from!r} did not resolve on item {item!r}")
        cur = cur[seg]
    return cur


def build_manifest(items, id_from, max_legs):
    """Turn the expander's items list into a manifest dict. Fails loud on
    over-cap (> max_legs) and on duplicate leg keys."""
    if len(items) > max_legs:
        raise ValueError(f"expander emitted {len(items)} items > max_legs {max_legs}")
    legs, seen = [], {}
    for item in items:
        key = extract_key(item, id_from)
        lid = leg_id(key)
        if lid in seen:
            raise ValueError(f"two items map to leg id '{lid}' (keys {seen[lid]!r} and {key!r})")
        seen[lid] = key
        legs.append({"id": lid, "key": key, "item": item})
    return {"count": len(legs), "legs": legs}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dynamic_fanout.py -k build_manifest -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_dynamic_fanout.py
git commit -m "feat(engine): build_manifest with over-cap + duplicate-key guards"
```

---

## Task 3: Expander runner (`lib.py`)

Resolves and runs the trusted expander hook, parsing its `{"items":[…]}` stdout. Fails loud on unresolved/nonzero/malformed output.

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (after `build_manifest`)
- Test: `tests/test_dynamic_fanout.py`

- [ ] **Step 1: Write the failing test**

```python
import os, stat, textwrap


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -k run_expander -q`
Expected: FAIL — `AttributeError: module 'lib' has no attribute 'run_expander'`.

- [ ] **Step 3: Write minimal implementation**

Add to `lib.py` after `build_manifest`. (`subprocess`, `json`, `os` are already imported at top of `lib.py`.)

```python
def run_expander(dir_, pid, instance, proto_path, fanout_node):
    """Run a dynamic fanout's trusted expander hook and return its items list.
    Resolved from <protocol-dir>/expand/<hook>. Raises ValueError (fail loud) on
    unresolved / non-executable / nonzero / non-JSON / missing-`items` output.

    Runs in zone 1 (plan); the hook re-fetches the diff itself and is handed only
    a read token via the ambient env (never the state PAT beyond plan's, never the
    publish token). Under ENGINE_LOCAL the stub reads a fixture file instead."""
    pdir = os.path.dirname(os.path.abspath(proto_path))
    expand = fanout_node.get("expand", {})
    res = resolve_executable(f"{pdir}/expand", expand.get("hook", ""), pdir, expand.get("exec", ""))
    kind, path = res.split("\t", 1)
    if kind == "ERR" or not os.access(path, os.X_OK):
        raise ValueError(f"expander '{expand.get('hook')}' unresolved/not-exec: {path}")
    env = dict(os.environ)
    env.setdefault("PR", instance[len("pr-"):] if instance.startswith("pr-") else instance)
    r = subprocess.run([path, dir_, instance], text=True, capture_output=True, env=env)
    if r.returncode != 0:
        raise ValueError(f"expander '{expand.get('hook')}' failed (exit {r.returncode}): {r.stderr.strip()}")
    try:
        parsed = json.loads(r.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        raise ValueError(f"expander '{expand.get('hook')}' returned non-JSON: {r.stdout[:200]!r}")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        raise ValueError(f"expander '{expand.get('hook')}' output missing 'items' array")
    return parsed["items"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dynamic_fanout.py -k run_expander -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_dynamic_fanout.py
git commit -m "feat(engine): trusted expander-hook runner (fail-loud)"
```

---

## Task 4: Join policy predicate (`lib.py`)

Pure decision function: given a policy string and (done, total), is the join satisfied?

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (near `decide`, ~line 718)
- Test: `tests/test_dynamic_fanout.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -k join_policy -q`
Expected: FAIL — `AttributeError: module 'lib' has no attribute 'join_policy_satisfied'`.

- [ ] **Step 3: Write minimal implementation**

Add to `lib.py` just above `decide` (line ~718):

```python
import math  # add to the imports at the top of lib.py if not already present


def join_policy_satisfied(policy, done, total):
    """Is a dynamic join's barrier satisfied given `done` legs out of `total`?
      all (default) : every leg done (vacuously true when total==0)
      any           : >=1 leg done (false when total==0)
      quorum:N      : >=N done, N an int count OR a percentage of total ('80%')
    Raises ValueError on an unparseable quorum."""
    policy = (policy or "all").strip()
    if policy == "all":
        return done == total
    if policy == "any":
        return done >= 1
    if policy.startswith("quorum:"):
        spec = policy[len("quorum:"):].strip()
        if spec.endswith("%"):
            try:
                pct = float(spec[:-1])
            except ValueError:
                raise ValueError(f"unparseable quorum percentage: {policy!r}")
            need = math.ceil(total * pct / 100.0)
        else:
            try:
                need = int(spec)
            except ValueError:
                raise ValueError(f"unparseable quorum count: {policy!r}")
        return done >= need
    raise ValueError(f"unknown join policy: {policy!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dynamic_fanout.py -k join_policy -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_dynamic_fanout.py
git commit -m "feat(engine): join_policy_satisfied (all|any|quorum:N)"
```

---

## Task 5: Validation rules + JSON Schema

Reject malformed dynamic fanouts before any state is written, with actionable messages.

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` — `_validate_sequence` (line ~932)
- Modify: `.github/agent-factory/engine/protocol.schema.json`
- Test: `tests/test_dynamic_fanout.py`

- [ ] **Step 1: Write the failing test**

```python
def test_validate_rejects_branches_and_expand_together():
    lib = _load_lib()
    proto = {"name": "x", "states": [
        {"id": "f", "kind": "fanout", "branches": [{"id": "a", "workflow": "w"}],
         "expand": {"hook": "h", "as": "i", "id_from": "$.p", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "j"},
        {"id": "j", "kind": "join", "of": "f"}]}
    with __import__("pytest").raises(ValueError) as e:
        lib.validate_protocol(proto)
    assert "exactly one of" in str(e.value) and "'f'" in str(e.value)


def test_validate_rejects_bad_max_legs():
    lib = _load_lib()
    proto = {"name": "x", "states": [
        {"id": "f", "kind": "fanout",
         "expand": {"hook": "h", "as": "i", "id_from": "$.p", "max_legs": 999},
         "each": {"workflow": "w"}, "next": "j"},
        {"id": "j", "kind": "join", "of": "f"}]}
    with __import__("pytest").raises(ValueError) as e:
        lib.validate_protocol(proto)
    assert "max_legs" in str(e.value)


def test_validate_rejects_bad_join_policy():
    lib = _load_lib()
    proto = {"name": "x", "states": [
        {"id": "f", "kind": "fanout",
         "expand": {"hook": "h", "as": "i", "id_from": "$.p", "max_legs": 8},
         "each": {"workflow": "w"}, "next": "j"},
        {"id": "j", "kind": "join", "of": "f", "policy": "most"}]}
    with __import__("pytest").raises(ValueError) as e:
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -k validate -q`
Expected: FAIL — the first assertion fails because `validate_protocol` currently ignores `expand`.

- [ ] **Step 3: Write minimal implementation**

In `_validate_sequence` (`lib.py`), inside the `for st in states:` loop, extend the `join` rule to check `policy`, and extend the `fanout` handling to validate the dynamic keys. Replace the existing `if kind == "join":` block and the `if kind == "fanout":` block with:

```python
        # Rule 1 — join references unknown fanout (+ policy validity)
        if kind == "join":
            of = st.get("of", "")
            if of and of not in fanout_ids:
                raise ValueError(
                    f"join '{sid}' references unknown fanout of='{of}' — "
                    f"make sure a fanout with id='{of}' exists as a sibling of '{sid}'"
                )
            pol = st.get("policy")
            if pol is not None:
                try:
                    join_policy_satisfied(pol, 0, 0)  # parse-check only
                except ValueError:
                    raise ValueError(
                        f"join '{sid}' has invalid policy='{pol}' — use "
                        f"'all', 'any', or 'quorum:<N|P%>'"
                    )

        # Rule 3 — gate.questions_from nonexistent sibling
        if kind == "gate":
            qf = st.get("questions_from", "")
            if qf and qf not in sibling_ids:
                raise ValueError(
                    f"gate '{sid}' has questions_from='{qf}' but no sibling state "
                    f"with id='{qf}' exists — add the source state or correct the name"
                )

        # Recurse into fanout branches / validate dynamic expand+each
        if kind == "fanout":
            has_static = bool(st.get("branches"))
            has_dynamic = bool(st.get("expand")) or bool(st.get("each"))
            if has_static == has_dynamic:
                raise ValueError(
                    f"fanout '{sid}' must have exactly one of branches[] (static) "
                    f"or expand+each (dynamic) — not both, not neither"
                )
            if has_dynamic:
                exp = st.get("expand") or {}
                for req in ("hook", "as", "id_from", "max_legs"):
                    if not exp.get(req) and exp.get(req) != 0:
                        raise ValueError(
                            f"fanout '{sid}' expand missing '{req}' — expand needs "
                            f"hook, as, id_from, and max_legs"
                        )
                ml = exp.get("max_legs")
                if not isinstance(ml, int) or isinstance(ml, bool) or not (1 <= ml <= 256):
                    raise ValueError(
                        f"fanout '{sid}' expand.max_legs must be an int in [1,256], got {ml!r}"
                    )
                each = st.get("each") or {}
                if bool(each.get("states")) == bool(each.get("workflow")):
                    raise ValueError(
                        f"fanout '{sid}' each must be a flat leg (workflow) XOR a "
                        f"sub-pipeline (states) — not both, not neither"
                    )
                if each.get("states"):
                    _validate_sequence(each["states"], path_hint + [sid, "each"])
            else:
                for br in st.get("branches", []):
                    bid = br.get("id", "<unnamed>")
                    if br.get("states"):
                        _validate_sequence(br["states"], path_hint + [bid])
                    else:
                        if not br.get("workflow"):
                            raise ValueError(
                                f"agent node '{bid}' missing 'workflow' — add a "
                                f"\"workflow\": \"<name>\" key to the '{bid}' branch"
                            )
```

Then add the schema keys to `protocol.schema.json`. Find the `fanout` node definition (the object schema with `"kind": {"const": "fanout"}`) and add `expand`, `each` to its `properties`; make `branches` no longer `required`. Add to the `join` definition's `properties` a `policy` string. Add `from_fanout` to the `inputs[]` item schema. Concretely, in the fanout properties object insert:

```json
"expand": {
  "type": "object",
  "additionalProperties": false,
  "required": ["hook", "as", "id_from", "max_legs"],
  "properties": {
    "hook": {"type": "string"},
    "exec": {"type": "string"},
    "as": {"type": "string"},
    "id_from": {"type": "string", "pattern": "^\\$\\."},
    "max_legs": {"type": "integer", "minimum": 1, "maximum": 256}
  }
},
"each": {"type": "object"}
```

and in the join properties object insert:

```json
"policy": {"type": "string", "pattern": "^(all|any|quorum:(\\d+|\\d+%))$"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dynamic_fanout.py -k validate -q`
Expected: PASS (4 tests).
Then run the full suite to confirm no regression in existing validation:
Run: `uv run pytest tests/ -q`
Expected: PASS (all existing tests green — the new rules only fire on `expand`/`policy` keys no existing protocol uses).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py .github/agent-factory/engine/protocol.schema.json tests/test_dynamic_fanout.py
git commit -m "feat(engine): validate dynamic fanout (expand/each/policy) + schema"
```

---

## Task 6: `resolve_legs` seam + dynamic entry in `next.py`

Make `enter_node` materialize legs from the manifest when the fanout is dynamic. This is the core integration.

**Files:**
- Modify: `.github/agent-factory/engine/next.py` — `enter_node`, `fanout` branch (lines ~111-125)
- Test: `tests/test_dynamic_fanout.py` (offline, via `run_engine`)

- [ ] **Step 1: Write the failing test**

This test drives the real `next.py` `start` command on the `dyn-fanout-flat` fixture (created in Task 9; if executing strictly in order, write the fixture first — see Task 9 — or gate this test with `pytest.importorskip`-style skip until the fixture exists). It asserts the manifest is written and one leg file per item is seeded.

```python
def test_dynamic_fanout_start_seeds_manifest_and_legs(engine_env, tmp_path):
    from conftest import run_engine, read_state_yaml  # conftest helpers
    import os, json
    proto = str(ROOT / "tests/fixtures/dyn-fanout-flat/protocol.json")
    env = dict(engine_env)
    env["NODE_PATH"] = ""  # start
    out, err, rc = run_engine("next.py", tmp_path, "pr-1", proto, "start", env=env)
    assert rc == 0, err
    # Manifest written with the two fixture items.
    d = tmp_path / "dyn-fanout-flat" / "pr-1"
    man = read_state_yaml(d.parent / "dyn-fanout-flat" / "pr-1" / "review.__manifest.yaml") \
        if False else read_state_yaml(str(d) + "/review.__manifest.yaml")
    assert man["count"] == 2
    ids = [leg["id"] for leg in man["legs"]]
    # One leg state file per manifest entry (single-phase → <legid>.yaml).
    for lid in ids:
        assert os.path.isfile(str(d) + f"/{lid}.yaml")
    # run-fanout action emitted with the materialized legs.
    action = json.loads(out.strip().splitlines()[-1])
    assert action["action"] == "run-fanout"
    assert {leg_dict["path"].split(".")[-1] for leg_dict in action["legs"]} == set(ids)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -k start_seeds_manifest -q`
Expected: FAIL — no manifest file (the fanout branch treats the node as static and finds no `branches`, seeding zero legs).

- [ ] **Step 3: Write minimal implementation**

In `next.py`, `enter_node`, replace the `if kind == "fanout":` block (lines ~111-125) with a version that branches on `expand`:

```python
    if kind == "fanout":
        if len(path) > 1:
            lib.write_join(DIR, PID, INSTANCE, lib.state_path(proto, path), {"joined": False})
        if node.get("expand"):
            # --- DYNAMIC fanout: materialize legs from the expander manifest. ---
            items = lib.run_expander(DIR, PID, INSTANCE, PROTO, node)   # fail-loud on hook error
            manifest = lib.build_manifest(items, node["expand"]["id_from"],
                                          node["expand"]["max_legs"])    # fail-loud on over-cap/dupe
            lib.write_manifest(DIR, PID, INSTANCE, path, manifest)
            each = node.get("each", {})
            branches = []
            for leg in manifest["legs"]:
                # Build a per-leg branch cfg from the `each` template, keyed by leg id.
                cfg = dict(each)
                cfg["id"] = leg["id"]
                seeded = _seed_child(proto, path + [leg["id"]], cfg)
                # Stage the item so the leg's agent can read inputs/<as>.json.
                lib.stage_item(DIR, PID, INSTANCE, lib.state_path(proto, path + [leg["id"]]),
                               node["expand"]["as"], leg["item"])
                branches.append(seeded)
            if not manifest["legs"]:
                # Zero items → vacuous no-op fanout. Emit an empty run-fanout so the
                # GHA layer skips the matrix; join.py (Task 7) advances past it.
                if emit:
                    print(json.dumps(_fanout_action(proto, path, [])))
                    return None
                return []
        else:
            branches = [_seed_child(proto, path + [b["id"]], b) for b in node.get("branches", [])]
        if emit:
            print(json.dumps(_fanout_action(proto, path, branches)))
            return None
        return branches
```

Add the `stage_item` helper to `lib.py` (after `materialize_inputs`, ~line 1231):

```python
def stage_item(dir_, pid, instance, file_path, as_, item):
    """Persist a dynamic leg's item beside its state file as
    <...>.<as>.item.json, so the dispatch/materialize step can surface it as
    inputs/<as>.json for the leg's agent. Keyed by the leg's file-naming path."""
    dst = output_artifact_path(dir_, pid, instance, path=file_path, kind=f"{as_}.item")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w") as f:
        json.dump(item, f)
```

> **Note for the implementer:** `DIR`, `PID`, `INSTANCE`, `PROTO`, `HEAD_SHA` are module globals in `next.py` set from argv/env in `_cli`/`main` (see the bottom of `next.py`). `_seed_child` already accepts an arbitrary `cfg` dict and keys files by `path[-1]` (the leg id) — no change needed there. Full GHA dispatch of `inputs/<as>.json` from the staged item is milestone 2; this task proves the manifest + leg seeding + staging offline.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dynamic_fanout.py -k start_seeds_manifest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py .github/agent-factory/engine/lib.py tests/test_dynamic_fanout.py
git commit -m "feat(engine): dynamic fanout entry — expander→manifest→legs in next.py"
```

---

## Task 7: Join reads the manifest + applies policy (`join.py`)

Make both the top-level and nested join barriers resolve leg ids from the manifest for a dynamic fanout, and decide success via `join_policy_satisfied`.

**Files:**
- Modify: `.github/agent-factory/engine/join.py` — `main()` (line ~182, ~211) and `_nested_join` (line ~41, ~74)
- Modify: `.github/agent-factory/engine/lib.py` — add `resolve_leg_ids` helper
- Test: `tests/test_dynamic_fanout.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

Add an offline e2e in Task 9 that walks a dynamic fanout to a policy decision; this step unit-tests the resolver.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -k resolve_leg_ids -q`
Expected: FAIL — `AttributeError: module 'lib' has no attribute 'resolve_leg_ids'`.

- [ ] **Step 3: Write minimal implementation**

Add to `lib.py` (near `read_manifest`):

```python
def resolve_leg_ids(dir_, pid, instance, tree_path, fanout_node):
    """The leg-id list for a fanout: the persisted manifest's ids when dynamic
    (expand present), else the static branches[] ids. The single seam that lets
    join.py treat dynamic and static fanouts uniformly."""
    if fanout_node and fanout_node.get("expand"):
        man = read_manifest(dir_, pid, instance, tree_path)
        return [leg["id"] for leg in man.get("legs", [])]
    return [b["id"] for b in (fanout_node.get("branches", []) if fanout_node else [])]
```

In `join.py` `main()`, replace line ~182:

```python
    branches = [b["id"] for b in (fanout_state.get("branches", []) if fanout_state else [])]
```

with (top fanout's tree path is `[fanout_state["id"]]`):

```python
    fo_tree_path = [fanout_state["id"]] if fanout_state else []
    branches = lib.resolve_leg_ids(dir_, pid, instance, fo_tree_path, fanout_state)
```

Then apply the policy at the `all_done` decision. Replace the `if all_done:` … `else:` that sets `concl` (lines ~211-243) so the aggregate uses the join's policy. After the loop that computes `all_terminal`/`all_done`, add a done-count and consult the policy:

```python
    done_count = 0
    for b in branches:
        sf = lib.state_file(dir_, pid, instance, b, phase=phase_for_path)
        if os.path.isfile(sf):
            try:
                if (lib.load_yaml(sf).get("state") or "") == "done":
                    done_count += 1
            except Exception:
                pass
    join_state_pol = (join_state or {}).get("policy", "all") if 'join_state' in dir() else "all"
```

then compute `policy_ok = lib.join_policy_satisfied(<join.policy>, done_count, len(branches))` and use it instead of the raw `all_done` when choosing `concl`/advancing. Because the existing `main()` computes `join_state` only inside the `if all_done:` arm, refactor so `join_state` and its `policy` are resolved BEFORE the decision:

```python
    # Resolve the join state + policy up front (needed for the policy decision).
    join_state = None
    fo_id = fanout_state.get("id") if fanout_state else None
    for st in protocol.get("states", []):
        if st.get("kind") == "join" and st.get("of") == fo_id:
            join_state = st
            break
    if join_state is None:
        for st in protocol.get("states", []):
            if st.get("kind") == "join":
                join_state = st
                break
    policy = (join_state or {}).get("policy", "all")
    policy_ok = lib.join_policy_satisfied(policy, done_count, len(branches))

    if policy_ok:
        nxt = (join_state or {}).get("next")
        if nxt and lib.state_by_id(protocol, nxt):
            instance_data["joined"] = True
            instance_data["phase"] = nxt
            lib.dump_yaml(inf, instance_data)
            lib.ensure_phase_label(dir_, pid, instance, protocol, pr, nxt)
            lib.cas_push(dir_, f"{instance}: join clear → continue {nxt}")
            lib.dispatch_continue(pid, instance, path=nxt)
            return
        concl, title, summary = "success", "Review complete", "All required review legs completed."
    else:
        concl, title, summary = "failure", "Review incomplete", \
            f"Join policy '{policy}' not met ({done_count}/{len(branches)} legs done); merge gated."
```

Apply the analogous change in `_nested_join`: replace `branches = [b["id"] for b in …]` (line ~41) with `lib.resolve_leg_ids(dir_, instance? …)` — note `_nested_join`'s signature is `(dir_, instance, proto_path, pid)`, so call `lib.resolve_leg_ids(dir_, pid, instance, fanout_path, fanout_node)`; and gate its `all_done` bubble on `lib.join_policy_satisfied(policy, done_count, len(branches))` where `policy` comes from the nested join state found at lines ~96-100.

> **Implementer note:** keep the `all_terminal` wait-gate unchanged (a dynamic join still waits for *every* leg to reach a terminal state before deciding); `policy` only governs the success/fail verdict over the terminal set, exactly as the spec's process-vs-verdict axis requires. For the vacuous zero-leg case, `len(branches)==0` → `all_terminal` stays `True`, `done_count==0`; `all` → advances, `any`/`quorum` → gated. This matches the spec's zero-items rule.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dynamic_fanout.py -k resolve_leg_ids -q`
Expected: PASS.
Run the full suite to confirm the `join.py` refactor didn't regress static joins:
Run: `uv run pytest tests/test_join.py tests/test_fanout_e2e.py -q`
Expected: PASS (existing static-fanout joins unchanged — `policy` defaults to `all`, `resolve_leg_ids` returns static ids).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/join.py .github/agent-factory/engine/lib.py tests/test_dynamic_fanout.py
git commit -m "feat(engine): join resolves dynamic legs from manifest + applies policy"
```

---

## Task 8: Merge `from_fanout` — reduce over the dynamic set (`lib.py`)

Let a `merge` node collect every leg's persisted evidence (tagged with state) into one array input.

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` — `resolve_inputs` (line ~216) and/or `run_merge_hook` (line ~1234)
- Test: `tests/test_dynamic_fanout.py`

- [ ] **Step 1: Write the failing test**

```python
def test_collect_fanout_evidence_tags_state(tmp_path):
    lib = _load_lib()
    import json, os
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -k collect_fanout_evidence -q`
Expected: FAIL — `AttributeError: module 'lib' has no attribute 'collect_fanout_evidence'`.

- [ ] **Step 3: Write minimal implementation**

Add to `lib.py` (near `resolve_inputs`). Uses `state_file`/`output_artifact_path` with a single-phase-style path (the leg id) — note for a single-phase dynamic fanout the leg file is `<instance>/<legid>.yaml`, matching `state_file(..., legid)`:

```python
def collect_fanout_evidence(dir_, pid, instance, tree_path, fanout_node):
    """Assemble the reduce input for a `merge` with from_fanout: one row per leg
    in the manifest, carrying its terminal state + persisted evidence (or None).
    Reads from the state branch, never job outputs — resilient to matrix clobber."""
    man = read_manifest(dir_, pid, instance, tree_path)
    rows = []
    for leg in man.get("legs", []):
        lid = leg["id"]
        sf = state_file(dir_, pid, instance, lid)          # single-phase leg file
        state = ""
        if os.path.isfile(sf):
            try:
                state = load_yaml(sf).get("state", "") or ""
            except Exception:
                state = ""
        evid_path = output_artifact_path(dir_, pid, instance, branch=lid, kind="evidence")
        evidence = None
        if os.path.isfile(evid_path):
            try:
                with open(evid_path) as f:
                    evidence = json.load(f)
            except (json.JSONDecodeError, ValueError):
                evidence = None
        rows.append({"leg_id": lid, "key": leg.get("key"), "state": state, "evidence": evidence})
    return rows
```

Then wire it into `run_merge_hook`: when a merge input is `{"from_fanout": "<id>", "as": "<name>"}`, write the collected rows to `<workdir>/inputs/<name>.json` before invoking the hook. In `run_merge_hook`, after `materialize_inputs(resolved, workdir)` (line ~1247) add:

```python
    for inp in merge_state.get("inputs", []):
        if inp.get("from_fanout"):
            fo = state_by_id(proto, inp["from_fanout"])
            fo_tree_path = [inp["from_fanout"]]  # top fanout; nested merges pass full path (milestone 2)
            rows = collect_fanout_evidence(dir_, pid, instance, fo_tree_path, fo)
            inputs_dir = os.path.join(workdir, "inputs")
            os.makedirs(inputs_dir, exist_ok=True)
            with open(os.path.join(inputs_dir, f"{inp['as']}.json"), "w") as f:
                json.dump(rows, f)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dynamic_fanout.py -k collect_fanout_evidence -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_dynamic_fanout.py
git commit -m "feat(engine): merge from_fanout reduces over surviving legs"
```

---

## Task 9: Fixtures + offline end-to-end walks

Create the OCR-shaped fixtures and a walk test that exercises expander → manifest → legs → advance → join(policy) → merge offline.

**Files:**
- Create: `tests/fixtures/dyn-fanout-flat/{protocol.json,leg.evidence.schema.json,expand/expand-items.py,expand/items.json,checks/schema-valid.py,publish/reduce.py}`
- Create: `tests/fixtures/dyn-fanout-subpipeline/protocol.json` (+ reuse flat's expander/checks)
- Create: `tests/fixtures/dyn-nested/protocol.json` (+ a second expander)
- Create: `tests/fixtures/dyn-fanout-badcap/{protocol.json,expand/expand-items.py}`
- Test: `tests/test_dynamic_fanout.py`

- [ ] **Step 1: Write the fixture protocol + stub expander (the "test" here is the fixture data the later steps drive)**

`tests/fixtures/dyn-fanout-flat/protocol.json`:

```json
{
  "$schema": "../../../.github/agent-factory/engine/protocol.schema.json",
  "name": "dyn-fanout-flat",
  "states": [
    { "id": "review", "kind": "fanout",
      "expand": { "hook": "expand-items", "as": "file", "id_from": "$.path", "max_legs": 8 },
      "each": { "workflow": "review-file-agent", "evidence": "leg.evidence.schema.json",
                "checks": [ { "run": "schema-valid", "on_fail": "iterate" } ],
                "publish": "reduce" },
      "next": "join" },
    { "id": "join", "kind": "join", "of": "review", "policy": "any", "next": "reduce" },
    { "id": "reduce", "kind": "merge", "hook": "reduce",
      "inputs": [ { "from_fanout": "review", "as": "legs" } ], "next": "done" }
  ]
}
```

`tests/fixtures/dyn-fanout-flat/expand/expand-items.py` (stub — echoes a fixture file; ABI `<hook> <state_dir> <instance>`):

```python
#!/usr/bin/env python3
"""Stub expander: emits a fixed items list from items.json beside this script.
A real expander would re-fetch the PR diff. Deterministic + offline for tests."""
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "items.json")) as f:
    items = json.load(f)
print(json.dumps({"items": items}))
```

`tests/fixtures/dyn-fanout-flat/expand/items.json`:

```json
[ { "path": "src/a.go" }, { "path": "src/b.go" } ]
```

Make the expander executable:

```bash
chmod +x tests/fixtures/dyn-fanout-flat/expand/expand-items.py
```

`tests/fixtures/dyn-fanout-flat/checks/schema-valid.py` (trivial always-pass check, ABI from CLAUDE.md):

```python
#!/usr/bin/env python3
import json, sys
print(json.dumps({"check": "schema-valid", "pass": True, "feedback": ""}))
```

```bash
chmod +x tests/fixtures/dyn-fanout-flat/checks/schema-valid.py
```

`tests/fixtures/dyn-fanout-flat/publish/reduce.py` (trusted merge hook; ABI `<hook> <workdir> <instance>` → `{conclusion,summary}`):

```python
#!/usr/bin/env python3
import json, os, sys
workdir = sys.argv[1]
legs = []
p = os.path.join(workdir, "inputs", "legs.json")
if os.path.isfile(p):
    with open(p) as f:
        legs = json.load(f)
done = [row for row in legs if row.get("state") == "done"]
print(json.dumps({"conclusion": "success",
                  "summary": f"reduced {len(done)}/{len(legs)} legs"}))
```

```bash
chmod +x tests/fixtures/dyn-fanout-flat/publish/reduce.py
```

`tests/fixtures/dyn-fanout-flat/leg.evidence.schema.json`:

```json
{ "type": "object", "properties": { "examined": { "type": "array" } } }
```

- [ ] **Step 2: Run the linter to verify the fixture protocol is well-formed**

Run: `python3 .github/agent-factory/engine/protocol-lint.py tests/fixtures/dyn-fanout-flat/protocol.json`
Expected: PASS (semantic validation succeeds; the dynamic-fanout rules from Task 5 accept it).

- [ ] **Step 3: Write the offline e2e walk test**

```python
def test_dyn_flat_start_to_manifest(engine_env, tmp_path):
    """Start walks: expander → manifest(count=2) → two leg files + run-fanout."""
    from conftest import run_engine, read_state_yaml
    import os, json
    proto = str(ROOT / "tests/fixtures/dyn-fanout-flat/protocol.json")
    out, err, rc = run_engine("next.py", tmp_path, "pr-1", proto, "start", env=engine_env)
    assert rc == 0, err
    d = str(tmp_path / "dyn-fanout-flat" / "pr-1")
    man = read_state_yaml(d + "/review.__manifest.yaml")
    assert man["count"] == 2
    for leg in man["legs"]:
        assert os.path.isfile(d + f"/{leg['id']}.yaml")
        # item staged beside the leg for inputs/<as>.json surfacing
        assert os.path.isfile(d + f"/{leg['id']}.file.item.json")


def test_dyn_badcap_fails_loud(engine_env, tmp_path):
    from conftest import run_engine
    proto = str(ROOT / "tests/fixtures/dyn-fanout-badcap/protocol.json")
    out, err, rc = run_engine("next.py", tmp_path, "pr-1", proto, "start", env=engine_env)
    assert rc != 0
    assert "max_legs" in (err + out)
```

Create `tests/fixtures/dyn-fanout-badcap/` mirroring flat but with `max_legs: 2` and an `items.json` of 5 entries.

> **Note:** the exact argv order for `next.py` (`<state_dir> <instance> <protocol.json> <command>`) and the module-global wiring are defined in `next.py`'s `_cli`/`main`. Confirm by reading the bottom of `next.py` before writing the walk; adjust the `run_engine(...)` argument order to match. If `next.py` reads the command from a different position or env var, align the test accordingly (do not change `next.py`'s CLI contract).

- [ ] **Step 4: Run the walk tests**

Run: `uv run pytest tests/test_dynamic_fanout.py -q`
Expected: PASS (all module tests green).

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/dyn-fanout-flat tests/fixtures/dyn-fanout-badcap tests/test_dynamic_fanout.py
git commit -m "test(engine): dynamic-fanout fixtures + offline start walk"
```

---

## Task 10: Sub-pipeline + nested dynamic fixtures (OCR shapes)

Prove `each` as a sub-pipeline and a dynamic leg nesting a second dynamic fanout.

**Files:**
- Create: `tests/fixtures/dyn-fanout-subpipeline/protocol.json`
- Create: `tests/fixtures/dyn-nested/{protocol.json,expand/expand-comments.py,expand/items.json}`
- Test: `tests/test_dynamic_fanout.py`

- [ ] **Step 1: Write the sub-pipeline fixture + failing test**

`tests/fixtures/dyn-fanout-subpipeline/protocol.json` — `each` is a `draft → finalize` sequence (reuse flat's `expand/`, `checks/`, schema by relative path or copy):

```json
{
  "$schema": "../../../.github/agent-factory/engine/protocol.schema.json",
  "name": "dyn-fanout-subpipeline",
  "states": [
    { "id": "review", "kind": "fanout",
      "expand": { "hook": "expand-items", "as": "file", "id_from": "$.path", "max_legs": 8 },
      "each": { "states": [
        { "id": "draft",    "kind": "agent", "workflow": "draft-agent",    "next": "finalize" },
        { "id": "finalize", "kind": "agent", "workflow": "finalize-agent" }
      ] },
      "next": "join" },
    { "id": "join", "kind": "join", "of": "review", "policy": "all", "next": "done" }
  ]
}
```

Copy flat's expander into this fixture:

```bash
mkdir -p tests/fixtures/dyn-fanout-subpipeline/expand
cp tests/fixtures/dyn-fanout-flat/expand/expand-items.py tests/fixtures/dyn-fanout-subpipeline/expand/
cp tests/fixtures/dyn-fanout-flat/expand/items.json      tests/fixtures/dyn-fanout-subpipeline/expand/
```

Test:

```python
def test_dyn_subpipeline_seeds_first_substate(engine_env, tmp_path):
    from conftest import run_engine, read_state_yaml
    import os
    proto = str(ROOT / "tests/fixtures/dyn-fanout-subpipeline/protocol.json")
    out, err, rc = run_engine("next.py", tmp_path, "pr-1", proto, "start", env=engine_env)
    assert rc == 0, err
    d = str(tmp_path / "dyn-fanout-subpipeline" / "pr-1")
    man = read_state_yaml(d + "/review.__manifest.yaml")
    lid = man["legs"][0]["id"]
    # Each dynamic leg is a sub-pipeline → its cursor file + first sub-state (draft) seeded.
    cur = read_state_yaml(d + f"/{lid}.yaml")
    assert cur["sub_state"] == "draft"
    assert os.path.isfile(d + f"/{lid}.draft.yaml")
```

- [ ] **Step 2: Run test to verify it fails (or passes if Task 6 already handled sub-pipeline `each`)**

Run: `uv run pytest tests/test_dynamic_fanout.py -k subpipeline -q`
Expected: PASS if Task 6's `_seed_child` reuse already handles a sub-pipeline `each` (it should, since `_seed_child` branches on `paths.is_sequence`). If FAIL, the fix is in `next.py`'s dynamic branch: ensure `cfg = dict(each)` preserves `states`, so `_seed_child` sees a sub-pipeline. No new code beyond that.

- [ ] **Step 3: Write the nested fixture**

`tests/fixtures/dyn-nested/protocol.json` — a dynamic `review` whose `each` sub-pipeline contains a second dynamic fanout `comments`:

```json
{
  "$schema": "../../../.github/agent-factory/engine/protocol.schema.json",
  "name": "dyn-nested",
  "states": [
    { "id": "review", "kind": "fanout",
      "expand": { "hook": "expand-items", "as": "file", "id_from": "$.path", "max_legs": 8 },
      "each": { "states": [
        { "id": "comments", "kind": "fanout",
          "expand": { "hook": "expand-comments", "as": "comment", "id_from": "$.cid", "max_legs": 8 },
          "each": { "workflow": "comment-agent" },
          "next": "cjoin" },
        { "id": "cjoin", "kind": "join", "of": "comments", "policy": "any" }
      ] },
      "next": "join" },
    { "id": "join", "kind": "join", "of": "review", "policy": "any", "next": "done" }
  ]
}
```

Add `expand/expand-comments.py` (emits 2 comment items) + `expand/items.json` (the file list). Make both expanders executable.

- [ ] **Step 4: Run the nested walk + lint**

Run: `python3 .github/agent-factory/engine/protocol-lint.py tests/fixtures/dyn-nested/protocol.json`
Expected: PASS (depth within `max_depth` 5; the `each` sub-tree validates).
Run: `uv run pytest tests/test_dynamic_fanout.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/dyn-fanout-subpipeline tests/fixtures/dyn-nested tests/test_dynamic_fanout.py
git commit -m "test(engine): sub-pipeline + nested dynamic fanout fixtures (OCR shapes)"
```

---

## Task 11: Docs — DSL reference

**Files:**
- Modify: `docs/PROTOCOL-DSL.md`

- [ ] **Step 1: Document the new keys**

In the `fanout` + `join` section of `docs/PROTOCOL-DSL.md`, add a **Dynamic fan-out** subsection documenting: the `expand` object (`hook`/`as`/`id_from`/`max_legs`), `each` as a branch template (flat xor sub-pipeline), `join.policy` (`all`/`any`/`quorum:N`), and `inputs[].from_fanout` on a `merge`. Include the `dyn-fanout-flat` protocol as the worked example. Add a one-line pointer from the `merge` and `inputs[]` tables to `from_fanout`. State explicitly: a `fanout` has **exactly one** of `branches[]` xor `expand+each`; the expander is a trusted zone-1 hook; over-cap fails loud.

- [ ] **Step 2: Verify the doc references match the code**

Run: `python3 .github/agent-factory/engine/protocol-lint.py tests/fixtures/dyn-fanout-flat/protocol.json`
Expected: PASS — confirms the documented example is valid.

- [ ] **Step 3: Commit**

```bash
git add docs/PROTOCOL-DSL.md
git commit -m "docs(dsl): document dynamic fanout (expand/each/policy/from_fanout)"
```

---

## Final verification

- [ ] **Run the whole suite:**

Run: `uv run pytest tests/ -q`
Expected: PASS — all existing modules green (no regression) + `test_dynamic_fanout.py` green.

- [ ] **Confirm backward-compatibility explicitly:**

Run: `uv run pytest tests/test_engine.py tests/test_join.py tests/test_fanout_e2e.py -q`
Expected: PASS — `code-review` and static-fanout fixtures unchanged (static `branches[]`, `policy` default `all`).

- [ ] **Lint every shipped + fixture protocol:**

Run: `for p in .github/agent-factory/protocols/*/protocol.json tests/fixtures/*/protocol.json; do python3 .github/agent-factory/engine/protocol-lint.py "$p" || echo "LINT FAIL: $p"; done`
Expected: no `LINT FAIL` lines.

---

## Self-Review (completed by plan author)

**1. Spec coverage** — every spec section maps to a task:
- §5 DSL surface → Task 5 (validation+schema) + Tasks 6–8 (behavior).
- §6 execution model (expander→manifest→legs) → Tasks 3, 6.
- §7 state layout (`__manifest.yaml`, leg keys) → Tasks 1, 6.
- §8 trust zones / expander ABI → Task 3 (`run_expander` doc + env).
- §9 data flow (manifest schema, item injection, from_fanout) → Tasks 1–2 (manifest), 6 (`stage_item`), 8 (`from_fanout`).
- §10 join policy → Tasks 4, 7.
- §11 failure/edge (expander fail, over-cap, zero-items, dup key, per-leg×policy) → Tasks 2, 3, 6, 7.
- §12 validation → Task 5.
- §13 test strategy (flat/subpipeline/nested/badcap fixtures + matrix) → Tasks 9, 10.
- §14 deferred (GHA dispatch of `inputs/<as>.json`, real expander, live matrix) → called out in Task 6's implementer note; not implemented here (correct).
- §15 engine touch-point summary → Tasks 5–8 map 1:1 to `lib.py`/`next.py`/`join.py`/schema.

**2. Placeholder scan** — no "TBD/TODO/handle edge cases"; every code step carries real code. Two *implementer notes* (Task 6, Task 9) point at reading `next.py`'s module globals / CLI arg order before wiring — these are verification instructions, not missing code.

**3. Type consistency** — helper names are stable across tasks: `manifest_file`/`read_manifest`/`write_manifest` (Task 1) reused in 6/7/8; `leg_id` (1) used by `build_manifest` (2); `build_manifest`/`run_expander` (2/3) called in `next.py` (6); `join_policy_satisfied` (4) used in validation (5) and join (7); `resolve_leg_ids`/`collect_fanout_evidence` (7/8) read the same manifest shape `{count, legs:[{id,key,item}]}` written in Task 6. Manifest tree-path keying (full path) is consistent between writer (Task 6) and readers (Tasks 7, 8).

**Known integration risk (flagged, not a placeholder):** Tasks 6 & 9 depend on `next.py`'s exact module-global wiring and CLI arg order, which the implementer must read at the top of Task 6 (the relevant code — `enter_node`, `_seed_child`, `_cli` — is cited by line). This is the one place the plan says "confirm against the source," because those globals aren't reproduced verbatim here.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-30-dynamic-fanout.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
