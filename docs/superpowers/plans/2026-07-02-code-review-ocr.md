# `code-review-ocr` Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the full nested `open-code-review`-mimic protocol (`code-review-ocr`) on the dynamic-fanout engine: map over changed files → per-file Plan→Main→Filter (Filter as a nested per-finding fan-out) → reduce → post one GitHub review.

**Architecture:** A new protocol under `.github/agent-factory/protocols/code-review-ocr/` whose `review` fanout expands over changed files; each file's `each` is a sub-pipeline `plan → main-review → findings(nested fanout) → join-findings → reduce`; a top `join-review → merge` reduces across files and posts one review. Two engine seams are added — nested `from_fanout` resolution (incl. a leg-terminal nested merge) and a declarative `matrix_fields` matrix projection — both gated on dynamic markers so the static path stays byte-identical. Reuses `expand-files` (Spec A), `traces-exist-in-diff` + `_review.py` (code-review).

**Tech Stack:** Python 3 + PyYAML (engine, runtime deps only), pytest (dev-only, `ENGINE_LOCAL` offline layer), GitHub Actions YAML, gh-aw (`gh aw compile`) for the three agents.

## Global Constraints

- **Static-path byte-identity:** every change fires only on dynamic markers (`expand`/`matrix_fields`/`from_fanout`/nested merge). Existing protocols and `tests/fixtures/` walks stay green and byte-unchanged. Regression gate: `uv run pytest tests/test_engine.py tests/test_join.py tests/test_publish.py -q`.
- **`matrix_fields` is the ONLY DSL/schema change** (user-approved): optional `expand.matrix_fields` (array of strings). Default (unset) = inline the full item. Do NOT add any other schema field.
- **Check ABI:** `<check> <evidence.json> <diff.txt> <changed-files.txt>` → one JSON `{"check","pass","feedback"}` to stdout, ALWAYS exit 0. Guard non-dict evidence with `isinstance(ev, dict)` before `.get` (the recurring exit-0 bug).
- **Expander ABI:** `<hook> <state-dir> <instance-key>`, `PR` in env, prints `{"items":[...]}`, fails loud. Trusted zone 1. `ENGINE_LOCAL` → read a beside-script fixture.
- **Publish hook ABI:** `<hook> <workdir> <instance-key>`, env `ENGINE_LOCAL`/`GITHUB_REPOSITORY`/`PUBLISH_TOKEN`/`PR`; prints `{"conclusion","summary"}`. Trusted zone 4.
- **Security:** agent-derived strings reach shell via `env:` → `jq --arg/--argjson`, never `${{ }}`/eval in `run:`.
- **Executable + shebang:** every new expander/check/publish file needs `chmod +x` + `#!/usr/bin/env python3` (live `run-checks.py`/`run_expander`/hooks require `os.X_OK`; the offline harness masks a missing `+x`).
- **git add EXPLICIT paths only** — never `-A`/`.`/`commit -a` (the repo has untracked WIP incl. a large binary).
- **Agents read-only** (`contents:read`+`pull-requests:read`), custom endpoint via `engine.env`, `strict:false`/`sandbox.agent:false`, `run-name` cid, **`safe-outputs.noop.report-as-issue:false`** (Spec A lesson). `gh aw compile` re-touches ALL locks — revert every lock except the intended one; commit `.md`+`.lock.yml` together.
- **Run tests with `uv run pytest`.** Full suite baseline is currently 673+ passing.
- **Dates absolute:** today is 2026-07-02.
- **Test-harness idiom** (mirror `tests/test_dynamic_fanout.py`): `from conftest import run_engine, read_state_yaml`; `out,err,rc = run_engine("next.py", str(tmp_path), "pr-N", PROTO, "start", env=engine_env)` returns `(stdout,stderr,rc)`; parse action via `json.loads(out.strip().splitlines()[-1])`; `read_state_yaml(full_path)`; in-process lib via the module's `_load_lib()`.

---

## File Structure

**Engine (modified):**
- `.github/agent-factory/engine/protocol.schema.json` — add optional `expand.matrix_fields`.
- `.github/agent-factory/engine/lib.py` — `run_merge_hook` gains `consuming_path`; nested `from_fanout` resolution; `validate_protocol` matrix_fields + nested from_fanout; size guard helper.
- `.github/agent-factory/engine/next.py` — `enter_node` matrix projection (`matrix_fields`) + size guard; merge arm handles a **nested (leg-terminal) merge**; thread `_p` into `run_merge_hook`.

**Protocol (new — `.github/agent-factory/protocols/code-review-ocr/`):**
- `protocol.json` — the nested tree.
- `expand/expand-files` — copied verbatim from `dyn-fanout-stub` (Spec A).
- `expand/expand-findings` (+ `expand/findings.fixture.json`) — new: main-review evidence → per-finding items.
- `plan.evidence.schema.json`, `main-review.evidence.schema.json`, `filter.evidence.schema.json`.
- `checks/schema-valid.py`, `checks/traces-exist-in-diff.py`, `checks/_paths.py` — copied from code-review; `checks/filter-verdict-valid.py` — new.
- `publish/reduce-file.py` — new (per-file surviving-findings reducer, state-only).
- `publish/post-review.py` + `publish/_review.py` — `_review.py` copied from code-review; `post-review.py` a thin OCR entrypoint (cross-file dedup + one review).

**Agents (new):** `.github/workflows/ocr-plan-agent.md`, `ocr-main-agent.md`, `ocr-filter-agent.md` (+ compiled `.lock.yml`).

**Tests:** `tests/test_dynamic_fanout.py` (extend), `tests/fixtures/ocr-nested/` (offline OCR-shaped fixture).

---

# STAGE 1 — Engine infra (offline)

## Task 1: `matrix_fields` — declarative matrix projection + size guard

**Files:**
- Modify: `.github/agent-factory/engine/protocol.schema.json` (the `expand` object, ~line 199-226)
- Modify: `.github/agent-factory/engine/next.py` (`enter_node` dynamic arm ~line 126-133; add a size-guard call)
- Modify: `.github/agent-factory/engine/lib.py` (add `project_matrix_item` + `check_matrix_size`)
- Modify: `.github/agent-factory/engine/lib.py` `_validate_sequence` (matrix_fields validation)
- Test: `tests/test_dynamic_fanout.py`

**Interfaces:**
- Produces: `lib.project_matrix_item(item, matrix_fields)` → dict (subset when list given, else full item); `lib.check_matrix_size(legs)` → raises `ValueError` if serialized legs exceed the cap. `enter_node` sets `seeded["inputs"] = {as: project_matrix_item(leg["item"], node["expand"].get("matrix_fields"))}` while `stage_item` still stages the FULL item.

- [ ] **Step 1: Write failing tests** (append to `tests/test_dynamic_fanout.py`)

```python
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
```

- [ ] **Step 2: Run to verify fail** — `uv run pytest tests/test_dynamic_fanout.py -k "project_matrix_item or check_matrix_size" -v` → FAIL (functions undefined).

- [ ] **Step 3: Add the helpers to `lib.py`** (place near `stage_item`)

```python
def project_matrix_item(item, matrix_fields):
    """Subset a dynamic leg's item to the keys that ride the GHA matrix.
    matrix_fields None/unset -> the full item (backward-compatible). Absent keys
    are skipped. The FULL item always stays durable on the state branch (stage_item);
    this only trims what is inlined into matrix.leg.inputs."""
    if not matrix_fields:
        return item
    return {k: item[k] for k in matrix_fields if k in item}

# GHA strategy.matrix / $GITHUB_OUTPUT practical ceiling; keep well under 1 MB.
_MATRIX_BYTES_CAP = 900_000

def check_matrix_size(legs):
    """Fail loud if the serialized matrix legs would exceed the GHA output/matrix
    cap. A protocol author who forgot `matrix_fields` gets a clear error, never a
    silent truncation (same discipline as max_legs over-cap)."""
    n = len(json.dumps(legs))
    if n > _MATRIX_BYTES_CAP:
        raise ValueError(
            f"matrix legs serialize to {n} bytes (> {_MATRIX_BYTES_CAP}); "
            f"set the fanout's expand.matrix_fields to inline only small keys "
            f"(large fields stay on the state branch; the agent re-fetches them)")
```

- [ ] **Step 4: Project in `enter_node`** (`next.py` dynamic arm)

Find:
```python
                seeded["inputs"] = {node["expand"]["as"]: leg["item"]}
                branches.append(seeded)
```
Replace with:
```python
                seeded["inputs"] = {node["expand"]["as"]:
                                    lib.project_matrix_item(leg["item"], node["expand"].get("matrix_fields"))}
                branches.append(seeded)
```
(`stage_item` two lines above still stages the FULL `leg["item"]` — unchanged.)

- [ ] **Step 5: Size-guard the emitted legs** (`next.py` `_fanout_action`, just before `return act`)

Find `act["legs"] = legs` and add immediately after:
```python
    lib.check_matrix_size(legs)
```

- [ ] **Step 6: Schema + validator**

In `protocol.schema.json`, in the `expand` object's `properties` (after `max_legs`), add:
```json
            "matrix_fields": {
              "type": "array",
              "items": { "type": "string", "minLength": 1 },
              "description": "Optional: which item keys are inlined into matrix.leg.inputs for the agent. Unset = the full item. The full item is always persisted on the state branch; this only trims what rides the GHA matrix (large re-fetchable fields stay out)."
            }
```
In `lib.py` `_validate_sequence`, in the fanout/expand validation block (find the existing `expand` checks — grep `matrix_fields`/`id_from` in `_validate_sequence`), add:
```python
        mf = expand.get("matrix_fields")
        if mf is not None and (not isinstance(mf, list) or not all(isinstance(x, str) and x for x in mf)):
            errors.append(f"{ctx}: expand.matrix_fields must be an array of non-empty strings")
```

- [ ] **Step 7: Failing test for the projection wiring** (append)

```python
def test_matrix_fields_trims_leg_inputs_full_item_still_staged(engine_env, tmp_path):
    # A dyn fixture whose expand sets matrix_fields:["path"] but items also carry "diff".
    import shutil, json as _json, pathlib
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
```

- [ ] **Step 8: Run** — `uv run pytest tests/test_dynamic_fanout.py -k "matrix_fields or project_matrix_item or check_matrix_size" -v` then `uv run pytest tests/ -q` (full suite green; static legs unchanged because `matrix_fields` is unset for them and `check_matrix_size` passes for small legs).

- [ ] **Step 9: Commit**
```bash
git add .github/agent-factory/engine/protocol.schema.json .github/agent-factory/engine/next.py .github/agent-factory/engine/lib.py tests/test_dynamic_fanout.py
git commit -m "feat(engine): expand.matrix_fields declarative matrix projection + fail-loud size guard"
```

## Task 2: Nested `from_fanout` — resolution + leg-terminal nested merge

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (`run_merge_hook` gains `consuming_path`; nested resolution)
- Modify: `.github/agent-factory/engine/next.py` (merge arm: pass `_p`; leg-terminal vs instance-terminal)
- Modify: `.github/agent-factory/engine/lib.py` `_validate_sequence` (nested from_fanout in-scope)
- Create: `tests/fixtures/ocr-nested/` (offline fixture: file fanout → each `{main → findings-fanout → join → reduce}` → top join → merge)
- Test: `tests/test_dynamic_fanout.py`

**Interfaces:**
- Consumes: `paths.node_at_path`, `collect_fanout_evidence`, `resolve_inputs(consuming_path=...)` (all existing).
- Produces: `run_merge_hook(dir_, pid, instance, proto_path, merge_state, consuming_path=None)` resolves a nested `from_fanout` as `consuming_path[:-1] + [from_fanout_id]`; a nested merge (`len(consuming_path) > 1`) is **leg-terminal** (marks the leg `done`, does not finalize the instance).

- [ ] **Step 1: Build the offline fixture** `tests/fixtures/ocr-nested/`

`protocol.json` (mirrors `dyn-nested` + a per-file `reduce` and a top `merge`):
```json
{
  "$schema": "../../../.github/agent-factory/engine/protocol.schema.json",
  "name": "ocr-nested",
  "states": [
    { "id": "review", "kind": "fanout",
      "expand": { "hook": "expand-files", "as": "file", "id_from": "$.path", "max_legs": 8, "matrix_fields": ["path"] },
      "each": { "states": [
        { "id": "main", "kind": "agent", "workflow": "main-agent",
          "evidence": "leg.evidence.schema.json", "checks": [ { "run": "schema-valid" } ], "next": "findings" },
        { "id": "findings", "kind": "fanout",
          "expand": { "hook": "expand-findings", "as": "finding", "id_from": "$.fid", "max_legs": 8 },
          "each": { "workflow": "filter-agent", "evidence": "leg.evidence.schema.json",
                    "checks": [ { "run": "schema-valid" } ] },
          "next": "jf" },
        { "id": "jf", "kind": "join", "of": "findings", "policy": "any", "next": "reduce" },
        { "id": "reduce", "kind": "merge", "hook": "reduce-file",
          "inputs": [ { "from_fanout": "findings", "as": "findings" } ] }
      ] },
      "next": "jr" },
    { "id": "jr", "kind": "join", "of": "review", "policy": "any", "next": "merge" },
    { "id": "merge", "kind": "merge", "hook": "post", "inputs": [ { "from_fanout": "review", "as": "files" } ] }
  ]
}
```
Add: `leg.evidence.schema.json` (`{"type":"object","required":["ok"],"properties":{"ok":{"type":"boolean"}}}`), `checks/schema-valid.py` (copy from `tests/fixtures/dyn-fanout-flat/checks/schema-valid.py`, `chmod +x`), `expand/expand-files.py` + `expand/items.json` (2 files) and `expand/expand-findings.py` + `expand/findings.json` (stub reading a fixture, 2 findings each — copy the `dyn-nested` fixture's expanders and rename keys to `fid`), `publish/reduce-file.py` and `publish/post.py` (each: read `inputs/<as>.json`, print `{"conclusion":"success","summary":"..."}` — trivial for the offline walk). All executables `chmod +x`.

- [ ] **Step 2: Write the failing nested-reduce test**

```python
def test_nested_from_fanout_reduces_over_nested_legs(tmp_path):
    lib = _load_lib()
    d, pid, inst = str(tmp_path), "ocr-nested", "pr-1"
    proto = str(ROOT / "tests/fixtures/ocr-nested/protocol.json")
    fileleg = "abc123de"   # a synthetic file leg id
    # seed a nested findings manifest + two filter leg evidences under the file leg
    findings_path = ["review", fileleg, "findings"]
    lib.write_manifest(d, pid, inst, findings_path,
        {"count": 2, "legs": [{"id": "f1", "key": "f1", "item": {"fid": "f1"}},
                              {"id": "f2", "key": "f2", "item": {"fid": "f2"}}]})
    # per-leg evidence files (collect_fanout_evidence reads them by tree path)
    for fid, keep in [("f1", True), ("f2", False)]:
        sf = lib.state_file(d, pid, inst, path=lib.state_path(proto_load(proto), findings_path + [fid]))
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        lib.dump_yaml(sf, {"state": "done"})
        ev = lib.output_artifact_path(d, pid, inst, path=lib.state_path(proto_load(proto), findings_path + [fid]))
        with open(ev, "w") as f: json.dump({"fid": fid, "keep": keep}, f)
    fo_node = paths_node_at(proto, findings_path)     # helper: load proto, paths.node_at_path
    rows = lib.collect_fanout_evidence(d, pid, inst, findings_path, fo_node)
    assert {r["leg_id"] for r in rows} == {"f1", "f2"}   # collected the NESTED legs, not a top fanout
```
(Add small test helpers `proto_load(path)` = `json.load(open(path))` and `paths_node_at(path, tree)` importing `paths` via `_load_lib`-style loader; or inline them.)

- [ ] **Step 3: Run to verify fail** — the test exercises `collect_fanout_evidence` by nested tree-path; it should already PASS for `collect_fanout_evidence` (it reads by arbitrary tree-path). If it PASSES, that confirms the collector is path-general; the real change is in `run_merge_hook` (Step 4). Note this in the report.

- [ ] **Step 4: Rewrite `run_merge_hook` for nested resolution** (`lib.py`)

Change the signature and the `from_fanout` block:
```python
def run_merge_hook(dir_, pid, instance, proto_path, merge_state, consuming_path=None):
```
Replace the phase/resolve_inputs setup + the `from_fanout` loop body:
```python
    ...
    plain_inputs = [inp for inp in merge_inputs if "from" in inp]
    resolved = resolve_inputs(proto, dir_, pid, instance,
                              consuming_branch=None, consuming_phase=phase,
                              inputs=plain_inputs, consuming_path=consuming_path)
    workdir = tempfile.mkdtemp(prefix="merge-")
    materialize_inputs(resolved, workdir)
    for inp in merge_inputs:
        if inp.get("from_fanout"):
            fo_id = inp["from_fanout"]
            # Resolve the fanout RELATIVE TO the merge's node-path: it is the merge's
            # sibling in the same (sub-)sequence -> parent-of-merge + fanout id.
            # Top merge (consuming_path None or len 1) -> the top fanout ([fo_id]).
            if consuming_path and len(consuming_path) > 1:
                fo_tree_path = list(consuming_path[:-1]) + [fo_id]
            else:
                fo_tree_path = [fo_id]
            fo_node = _paths.node_at_path(proto, fo_tree_path)   # nested fanout is NOT a top-level state
            if fo_node is None or not os.path.isfile(manifest_file(dir_, pid, instance, fo_tree_path)):
                raise ValueError(
                    f"merge from_fanout='{fo_id}': no manifest at {'.'.join(fo_tree_path)} "
                    f"(fanout not materialized or misnamed)")
            rows = collect_fanout_evidence(dir_, pid, instance, fo_tree_path, fo_node)
            inputs_dir = os.path.join(workdir, "inputs")
            os.makedirs(inputs_dir, exist_ok=True)
            with open(os.path.join(inputs_dir, f"{inp['as']}.json"), "w") as f:
                json.dump(rows, f)
    ...
```
(`lib.py` already imports `paths as _paths` at the top — use `_paths.node_at_path`, do NOT add a new `import paths`.)

- [ ] **Step 5: Merge arm — leg-terminal (nested) vs instance-terminal (top)** (`next.py` merge arm ~line 768)

**The exact leg-terminal pattern to mirror already exists** — `advance.py::complete_sequence` (marks the leg cursor `done`, updates status, cas_push, then `fire_join`) and `join.py` (lines ~106-110): the enclosing-fanout join is fired **path-less when the enclosing fanout is TOP-level** (`len(efp) == 1`) and **path-keyed only when nested** (`len(efp) > 1`), via `_paths.enclosing_fanout_path`. For OCR the per-file `reduce`'s enclosing fanout is `review` (top-level → **path-less** join). Note `lib.py` imports `paths` as **`_paths`** — in `lib.run_merge_hook` use `_paths.node_at_path`; in `next.py` use its own `paths` import (grep `next.py` for `import paths`).

Find `res = lib.run_merge_hook(DIR, PID, INSTANCE, PROTO, node)` and the instance-finalize block after it. Replace with:
```python
        node = paths.node_at_path(proto_data, _p)
        res = lib.run_merge_hook(DIR, PID, INSTANCE, PROTO, node, consuming_path=_p)
        if len(_p) > 1:
            # NESTED merge (a per-file `reduce`): LEG-TERMINAL, mirroring
            # advance.complete_sequence. (1) persist the merge result as THIS leg's
            # output evidence so the enclosing fanout's from_fanout collects the
            # survivors; (2) mark the file-leg SEQUENCE CURSOR done; (3) fire the
            # enclosing fanout's join exactly as join.py does (path-less for a
            # top-level enclosing fanout, path-keyed if nested).
            leg_path = _p[:-1]                       # the file-leg sub-pipeline cursor
            ev = lib.output_artifact_path(DIR, PID, INSTANCE, path=lib.state_path(proto_data, _p))
            os.makedirs(os.path.dirname(ev), exist_ok=True)
            with open(ev, "w") as f:
                json.dump(res, f)                    # res carries {conclusion, summary, survivors}
            cursor_sf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto_data, leg_path))
            cur = lib.load_yaml(cursor_sf) if os.path.isfile(cursor_sf) else {}
            cur["state"] = "done"
            lib.dump_yaml(cursor_sf, cur)
            lib.cas_push(DIR, f"{INSTANCE}: nested merge {'.'.join(_p)} → leg done")
            efp = paths.enclosing_fanout_path(proto_data, _p)
            fields = {"protocol": PID, "instance": INSTANCE}
            if efp and len(efp) > 1:
                fields["path"] = ".".join(efp)
            lib._gh_dispatch("protocol-join", fields)
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"nested-merge-done:{'.'.join(_p)}"}))
            sys.exit(0)
        # TOP merge (existing behavior, unchanged): finalize the instance.
        inf = lib.instance_file(DIR, PID, INSTANCE)
        ...
```
Verify against the real functions before implementing: `advance.py::complete_sequence` (leg-terminal shape), `join.py` lines ~106-110 (the `efp`/path-less-vs-keyed `_gh_dispatch("protocol-join", fields)`), and `_paths.enclosing_fanout_path`. The offline walk (Step 7) is the behavioral pin — it must show a per-file `reduce` completing → the `review` join re-evaluating → the top `merge` running.

- [ ] **Step 6: Validator — nested from_fanout in scope** (`lib.py` `_validate_sequence`)

Find the existing Rule 6 (`from_fanout` names an in-scope fanout). Extend it so a merge inside a sub-pipeline validates `from_fanout` against a fanout sibling **at its own level** (a sibling sub-state of kind `fanout`), not only a top-level state. Concretely, when validating a `merge` node, search the merge's containing `states[]` list for a sibling with `id == from_fanout` and `kind == "fanout"`; error if none.

- [ ] **Step 7: Offline OCR-nested walk test** (append) — drive the full fixture to completion.

```python
def test_ocr_nested_walk_reduces_and_merges(engine_env, tmp_path):
    # Full offline walk: file fanout -> per file (main -> findings fanout -> jf -> reduce)
    # -> jr -> merge. Assert each per-file reduce produced leg evidence and the top
    # merge collected both files. (Drive via run_engine start + the join/continue hops;
    # mirror the existing dyn-nested walk in this module for the hop sequence.)
    ...
```
Model the hop sequence on the existing `dyn-nested` walk already in `tests/test_dynamic_fanout.py` (find it: `grep -n "dyn-nested\|dyn_nested" tests/test_dynamic_fanout.py`), extending it through the new `reduce`/`merge` states. The assertions: after the walk, `_instance.yaml` is `joined: true`; each file leg's `reduce` evidence exists; the top merge ran over both file legs.

- [ ] **Step 8: Run** — `uv run pytest tests/test_dynamic_fanout.py -k "nested_from_fanout or ocr_nested" -v`, then the static regression gate `uv run pytest tests/test_engine.py tests/test_join.py tests/test_publish.py -q`, then `uv run pytest tests/ -q`.

- [ ] **Step 9: Commit**
```bash
git add .github/agent-factory/engine/lib.py .github/agent-factory/engine/next.py tests/test_dynamic_fanout.py tests/fixtures/ocr-nested
git commit -m "feat(engine): nested from_fanout resolution + leg-terminal nested merge"
```

---

# STAGE 2 — Protocol, expanders, checks, publish (offline)

## Task 3: `code-review-ocr` protocol skeleton + `expand-files` + lint

**Files:**
- Create: `.github/agent-factory/protocols/code-review-ocr/protocol.json`
- Create: `.github/agent-factory/protocols/code-review-ocr/expand/expand-files` (+ `expand/items.json`)
- Test: `tests/test_dynamic_fanout.py`

**Interfaces:** Produces the protocol id `code-review-ocr`; fanout ids `review` (files) and `findings`; sub-states `plan`/`main-review`/`findings`/`join-findings`/`reduce`; top `join-review`/`merge`.

- [ ] **Step 1: Write `protocol.json`**
```json
{
  "$schema": "../../engine/protocol.schema.json",
  "name": "code-review-ocr",
  "triggers": [ { "on": "issue_comment", "comment_prefix": "/ocr-review", "command": "start" } ],
  "states": [
    { "id": "review", "kind": "fanout",
      "expand": { "hook": "expand-files", "as": "file", "id_from": "$.path", "max_legs": 32, "matrix_fields": ["path"] },
      "each": { "states": [
        { "id": "plan", "kind": "agent", "workflow": "ocr-plan-agent",
          "evidence": "plan.evidence.schema.json", "max_iterations": 1,
          "checks": [ { "run": "schema-valid" } ], "next": "main-review" },
        { "id": "main-review", "kind": "agent", "workflow": "ocr-main-agent",
          "evidence": "main-review.evidence.schema.json", "max_iterations": 1,
          "inputs": [ { "from": "plan", "as": "plan" } ],
          "checks": [ { "run": "schema-valid" }, { "run": "traces-exist-in-diff" } ], "next": "findings" },
        { "id": "findings", "kind": "fanout",
          "expand": { "hook": "expand-findings", "as": "finding", "id_from": "$.finding_id", "max_legs": 32 },
          "each": { "workflow": "ocr-filter-agent", "evidence": "filter.evidence.schema.json", "max_iterations": 1,
                    "checks": [ { "run": "schema-valid" }, { "run": "filter-verdict-valid" } ] },
          "next": "join-findings" },
        { "id": "join-findings", "kind": "join", "of": "findings", "policy": "any", "next": "reduce" },
        { "id": "reduce", "kind": "merge", "hook": "reduce-file",
          "inputs": [ { "from_fanout": "findings", "as": "findings" } ] }
      ] },
      "next": "join-review" },
    { "id": "join-review", "kind": "join", "of": "review", "policy": "any", "next": "merge" },
    { "id": "merge", "kind": "merge", "hook": "post-review", "inputs": [ { "from_fanout": "review", "as": "files" } ] }
  ]
}
```

- [ ] **Step 2: Copy `expand-files` from Spec A**
```bash
mkdir -p .github/agent-factory/protocols/code-review-ocr/expand
cp .github/agent-factory/protocols/dyn-fanout-stub/expand/expand-files .github/agent-factory/protocols/code-review-ocr/expand/expand-files
cp .github/agent-factory/protocols/dyn-fanout-stub/expand/items.json .github/agent-factory/protocols/code-review-ocr/expand/items.json
chmod +x .github/agent-factory/protocols/code-review-ocr/expand/expand-files
```

- [ ] **Step 3: Lint + validate (expect a clean tree; agents/checks not yet present is OK — validate_protocol does not require check files on disk)**
```bash
python3 .github/agent-factory/engine/protocol-lint.py .github/agent-factory/protocols/code-review-ocr/protocol.json
```
Expected: validation passes (semantic-only if `jsonschema` absent); the dynamic-leg-aware tree (Spec A) renders both fanouts' `each` templates. A hard validation error blocks; fix the exact rule it names.

- [ ] **Step 4: Depth guard test** (append) — assert the tree is within `max_depth`.
```python
def test_ocr_protocol_validates_and_within_depth():
    lib = _load_lib()
    proto = json.load(open(ROOT / ".github/agent-factory/protocols/code-review-ocr/protocol.json"))
    errs = lib.validate_protocol(proto)     # returns [] on success (match the real API; grep def validate_protocol)
    assert errs == [], errs
```
(Confirm `validate_protocol`'s return contract by reading it; adapt the assertion to match — some builds return a list, some raise.)

- [ ] **Step 5: Run** — `uv run pytest tests/test_dynamic_fanout.py -k ocr_protocol -v`; then `uv run pytest tests/ -q`.

- [ ] **Step 6: Commit**
```bash
git add .github/agent-factory/protocols/code-review-ocr/protocol.json .github/agent-factory/protocols/code-review-ocr/expand tests/test_dynamic_fanout.py
git commit -m "feat(code-review-ocr): protocol.json nested tree + expand-files (from Spec A)"
```

## Task 4: Evidence schemas + `expand-findings` expander

**Files:**
- Create: `.../code-review-ocr/plan.evidence.schema.json`, `main-review.evidence.schema.json`, `filter.evidence.schema.json`
- Create: `.../code-review-ocr/expand/expand-findings` (+ `expand/findings.fixture.json`)
- Test: `tests/test_dynamic_fanout.py`

**Interfaces:** `expand-findings` reads the per-file `main-review` evidence (path from its arg/env) → `{"items":[{finding_id, path, existing_code, side, line[/start_line], comment}, ...]}`. `id_from: $.finding_id`.

- [ ] **Step 1: Evidence schemas**

`plan.evidence.schema.json`:
```json
{ "$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
  "required": ["examined", "plan_items"],
  "properties": {
    "examined": { "type": "array", "items": {"type": "string"}, "minItems": 1 },
    "plan_items": { "type": "array", "items": {"type": "string"} } } }
```
`main-review.evidence.schema.json` (the code-review `grumpy` finding shape + `finding_id`):
```json
{ "$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
  "required": ["files"],
  "properties": { "files": { "type": "array", "items": {
    "type": "object", "required": ["path", "findings"],
    "properties": { "path": {"type": "string"}, "findings": { "type": "array", "items": {
      "type": "object", "required": ["finding_id", "existing_code", "side", "line", "comment"],
      "properties": {
        "finding_id": {"type": "string"}, "existing_code": {"type": "string"},
        "side": {"enum": ["LEFT", "RIGHT"]}, "line": {"type": "integer"},
        "start_line": {"type": "integer"}, "comment": {"type": "string"} } } } } } } } }
```
`filter.evidence.schema.json`:
```json
{ "$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
  "required": ["finding_id", "keep"],
  "properties": {
    "finding_id": {"type": "string"}, "keep": {"type": "boolean"},
    "anchor": { "type": "object", "properties": {
      "side": {"enum": ["LEFT","RIGHT"]}, "line": {"type": "integer"}, "start_line": {"type": "integer"} } },
    "reason": {"type": "string"} } }
```

- [ ] **Step 2: Failing unit test for `expand-findings`**
```python
EXPF = ".github/agent-factory/protocols/code-review-ocr/expand/expand-findings"
def test_expand_findings_one_item_per_finding(tmp_path):
    ev = tmp_path / "main.json"
    import json as J
    J.dump({"files": [{"path": "a.py", "findings": [
        {"finding_id": "a.py:1", "existing_code": "x=1", "side": "RIGHT", "line": 1, "comment": "c1"},
        {"finding_id": "a.py:2", "existing_code": "y=2", "side": "RIGHT", "line": 2, "comment": "c2"}]}]}, open(ev, "w"))
    import subprocess, os
    r = subprocess.run([EXPF, str(tmp_path), "pr-1"], capture_output=True, text=True,
                       env={**os.environ, "EXPAND_FINDINGS_EVIDENCE": str(ev)})
    assert r.returncode == 0, r.stderr
    items = J.loads(r.stdout)["items"]
    assert [i["finding_id"] for i in items] == ["a.py:1", "a.py:2"]
    assert items[0]["path"] == "a.py" and items[0]["comment"] == "c1"
```

- [ ] **Step 3: Write `expand-findings`**
```python
#!/usr/bin/env python3
"""OCR per-finding expander. Reads the per-file main-review evidence and emits one
item per candidate finding: {finding_id, path, existing_code, side, line[,start_line],
comment}. id_from: $.finding_id. Fails loud on malformed evidence.

Live: the engine surfaces the sibling main-review evidence path via the fanout's
input wiring; here we read EXPAND_FINDINGS_EVIDENCE (test) or the conventional
per-leg main-review evidence file (live — resolved by the plan job, passed in env)."""
import json, os, sys

def _load():
    p = os.environ.get("EXPAND_FINDINGS_EVIDENCE")
    if not p or not os.path.isfile(p):
        raise SystemExit(f"expand-findings: no main-review evidence at {p!r}")
    with open(p) as f:
        return json.load(f)

def main():
    ev = _load()
    items = []
    for fobj in ev.get("files", []):
        path = fobj.get("path", "")
        for fi in fobj.get("findings", []):
            it = {"finding_id": fi["finding_id"], "path": path,
                  "existing_code": fi.get("existing_code", ""), "side": fi.get("side", "RIGHT"),
                  "line": fi.get("line"), "comment": fi.get("comment", "")}
            if "start_line" in fi:
                it["start_line"] = fi["start_line"]
            items.append(it)
    print(json.dumps({"items": items}))

if __name__ == "__main__":
    main()
```
`chmod +x`. Create `expand/findings.fixture.json` = the sample above (for `ENGINE_LOCAL` fixture walks; wire `EXPAND_FINDINGS_EVIDENCE` to it in tests).

**NOTE (resolve in this task):** how `expand-findings` learns the live per-leg main-review evidence path. The engine runs the expander in the plan job with the state branch checked out; the sibling `main-review` evidence for the current file leg lives at a deterministic path. Read `lib.run_expander`'s env (it forwards `PR` + the allowlist) and confirm whether the fanout node can pass its sibling-evidence path. If not directly available, extend `run_expander` to export the enclosing leg's main-review evidence path as `EXPAND_FINDINGS_EVIDENCE` (a small, generic addition: "expander sees its enclosing sub-pipeline's prior-phase evidence"). Keep this within the allowlist. Decide + implement here; document in the report.

- [ ] **Step 4: Run** — `uv run pytest tests/test_dynamic_fanout.py -k expand_findings -v`, then `uv run pytest tests/ -q`.

- [ ] **Step 5: Commit**
```bash
git add .github/agent-factory/protocols/code-review-ocr/*.evidence.schema.json .github/agent-factory/protocols/code-review-ocr/expand/expand-findings .github/agent-factory/protocols/code-review-ocr/expand/findings.fixture.json tests/test_dynamic_fanout.py
git commit -m "feat(code-review-ocr): evidence schemas + expand-findings expander"
```

## Task 5: Checks — reuse `traces-exist-in-diff`/`schema-valid` + new `filter-verdict-valid`

**Files:**
- Copy: `code-review/checks/{schema-valid.py, traces-exist-in-diff.py, _paths.py}` → `code-review-ocr/checks/`
- Create: `code-review-ocr/checks/filter-verdict-valid.py`
- Test: `tests/test_dynamic_fanout.py`

- [ ] **Step 1: Copy the reused checks**
```bash
mkdir -p .github/agent-factory/protocols/code-review-ocr/checks
cp .github/agent-factory/protocols/code-review/checks/schema-valid.py .github/agent-factory/protocols/code-review/checks/traces-exist-in-diff.py .github/agent-factory/protocols/code-review/checks/_paths.py .github/agent-factory/protocols/code-review-ocr/checks/
chmod +x .github/agent-factory/protocols/code-review-ocr/checks/*.py
```
(Verify `schema-valid.py` resolves the evidence schema by the node's `evidence` field generically — it does in code-review; if it hardcodes a schema name, adapt. Read it before relying.)

- [ ] **Step 2: Failing tests for `filter-verdict-valid`**
```python
FVCHECK = str(ROOT / ".github/agent-factory/protocols/code-review-ocr/checks/filter-verdict-valid.py")
from conftest import run_check
@pytest.mark.parametrize("ev,ok", [
    ({"finding_id": "a.py:1", "keep": True, "anchor": {"side": "RIGHT", "line": 3}}, True),
    ({"finding_id": "a.py:1", "keep": False}, True),                 # dropped: no anchor needed
    ({"finding_id": "a.py:1", "keep": True}, False),                 # kept but no anchor
    ({"keep": True, "anchor": {"side": "RIGHT", "line": 3}}, False), # no finding_id
    ([], False), ("x", False), ({"finding_id": "a", "keep": "yes"}, False),  # garbage / non-bool
])
def test_filter_verdict_valid(ev, ok, tmp_path):
    import json as J
    p = tmp_path / "e.json"; P = tmp_path / "d.txt"; C = tmp_path / "c.txt"
    P.write_text(""); C.write_text("")
    J.dump(ev, open(p, "w"))
    r = run_check(FVCHECK, p, P, C)     # raises if the check crashed / non-JSON stdout
    assert r["check"] == "filter-verdict-valid"
    assert r["pass"] is ok
```

- [ ] **Step 3: Write `filter-verdict-valid.py`**
```python
#!/usr/bin/env python3
"""code-review-ocr check: a filter leg's verdict is well-formed. finding_id present;
keep is boolean; a KEPT finding carries an anchor (side + line). Form check only
(never judges whether keep is correct). ABI: <evidence> <diff> <changed>; exit 0."""
import json, sys

def main():
    try:
        ev = json.load(open(sys.argv[1]))
    except Exception as e:
        print(json.dumps({"check": "filter-verdict-valid", "pass": False,
                          "feedback": f"unreadable evidence: {e}"})); return
    if not isinstance(ev, dict):
        print(json.dumps({"check": "filter-verdict-valid", "pass": False,
                          "feedback": "evidence must be an object"})); return
    fid = ev.get("finding_id"); keep = ev.get("keep")
    ok = isinstance(fid, str) and fid.strip() and isinstance(keep, bool)
    fb = ""
    if not ok:
        fb = "finding_id must be a non-empty string and keep a boolean"
    elif keep:
        a = ev.get("anchor")
        if not (isinstance(a, dict) and a.get("side") in ("LEFT", "RIGHT") and isinstance(a.get("line"), int)):
            ok, fb = False, "a kept finding must carry an anchor {side, line}"
    print(json.dumps({"check": "filter-verdict-valid", "pass": bool(ok), "feedback": fb}))

if __name__ == "__main__":
    main()
```
`chmod +x`.

- [ ] **Step 4: Run** — `uv run pytest tests/test_dynamic_fanout.py -k filter_verdict -v`, then `uv run pytest tests/ -q`.

- [ ] **Step 5: Commit**
```bash
git add .github/agent-factory/protocols/code-review-ocr/checks
git commit -m "feat(code-review-ocr): reuse schema-valid/traces-exist-in-diff + filter-verdict-valid check"
```

## Task 6: Publish — per-file `reduce-file` + top `post-review` (reuse `_review.py`) + full offline walk

**Files:**
- Create: `code-review-ocr/publish/reduce-file.py`
- Copy: `code-review/publish/_review.py` → `code-review-ocr/publish/_review.py`
- Create: `code-review-ocr/publish/post-review.py`
- Test: `tests/test_dynamic_fanout.py`

**Interfaces:** `reduce-file.py <workdir> <instance>` reads `inputs/findings.json` (the from_fanout rows: `{leg_id,key,state,evidence}` per filter leg) → emits `{conclusion, summary}` and writes the file's surviving findings (keep:true) as the leg output. `post-review.py <workdir> <instance>` reads `inputs/files.json` (from_fanout rows over file legs, each carrying its `reduce` output) → dedups cross-file → posts one review via `_review.py`.

- [ ] **Step 1: Failing test — reduce-file keeps only keep:true**
```python
REDUCE = str(ROOT / ".github/agent-factory/protocols/code-review-ocr/publish/reduce-file.py")
def test_reduce_file_keeps_survivors(tmp_path):
    import json as J, subprocess, os
    wd = tmp_path / "wd"; (wd / "inputs").mkdir(parents=True)
    J.dump([{"leg_id": "f1", "state": "done", "evidence": {"finding_id": "a:1", "keep": True,
             "anchor": {"side": "RIGHT", "line": 1}, "path": "a.py", "comment": "c1"}},
            {"leg_id": "f2", "state": "done", "evidence": {"finding_id": "a:2", "keep": False}}],
           open(wd / "inputs" / "findings.json", "w"))
    r = subprocess.run([REDUCE, str(wd), "pr-1"], capture_output=True, text=True,
                       env={**os.environ, "ENGINE_LOCAL": "1"})
    assert r.returncode == 0, r.stderr
    out = J.loads(r.stdout)
    assert out["conclusion"] in ("success", "neutral")
    survivors = J.load(open(wd / "surviving.json"))     # reduce-file writes its output here
    assert [s["finding_id"] for s in survivors] == ["a:1"]
```

- [ ] **Step 2: Write `reduce-file.py`**
```python
#!/usr/bin/env python3
"""code-review-ocr per-file reduce (zone 4). Reads inputs/findings.json (from_fanout
rows over this file's filter legs) and keeps only findings whose filter verdict was
keep:true, using the (possibly relocated) anchor from the filter evidence. Writes the
survivors to <workdir>/surviving.json (the file leg's output, collected by the top
merge) and prints {conclusion, summary}. State-only: no GitHub write."""
import json, os, sys

def main():
    workdir, instance = sys.argv[1], sys.argv[2]
    rows = json.load(open(os.path.join(workdir, "inputs", "findings.json")))
    survivors = []
    for r in rows:
        ev = r.get("evidence") or {}
        if isinstance(ev, dict) and ev.get("keep") is True:
            a = ev.get("anchor") or {}
            survivors.append({"finding_id": ev.get("finding_id"), "path": ev.get("path"),
                              "existing_code": ev.get("existing_code", ""), "comment": ev.get("comment", ""),
                              "side": a.get("side", ev.get("side", "RIGHT")),
                              "line": a.get("line", ev.get("line")),
                              **({"start_line": a["start_line"]} if "start_line" in a else {})})
    with open(os.path.join(workdir, "surviving.json"), "w") as f:
        json.dump(survivors, f)
    print(json.dumps({"conclusion": "success", "summary": f"{len(survivors)} finding(s) kept"}))

if __name__ == "__main__":
    main()
```
`chmod +x`.

- [ ] **Step 3: Copy `_review.py` + write `post-review.py`**
```bash
cp .github/agent-factory/protocols/code-review/publish/_review.py .github/agent-factory/protocols/code-review-ocr/publish/_review.py
```
`post-review.py` (dedup + one review). Read `_review.py`'s `run()` signature first (`head -40 code-review/publish/_review.py` and the grumpy entrypoint `publish-grumpy.py`) and call it with the OCR wording. Skeleton:
```python
#!/usr/bin/env python3
"""code-review-ocr top merge (zone 4): collect every file's surviving findings
(inputs/files.json = from_fanout rows over file legs, each row's evidence is the
reduce-file {conclusion,summary} and its surviving.json was the leg output), dedup
cross-file by (path, side, line, existing_code), and post ONE GitHub review via the
shared _review.py mechanism. Under ENGINE_LOCAL, print instead of posting."""
import json, os, sys
import _review    # shared mechanism (same dir)

def _dedup(findings):
    seen, out = set(), []
    for f in findings:
        k = (f.get("path"), f.get("side"), f.get("line"), f.get("existing_code"))
        if k in seen: continue
        seen.add(k); out.append(f)
    return out

def main():
    workdir, instance = sys.argv[1], sys.argv[2]
    rows = json.load(open(os.path.join(workdir, "inputs", "files.json")))
    findings = []
    for r in rows:
        # each file leg's output evidence is its reduce's result; the survivors were
        # written to that leg's surviving.json, surfaced here as evidence.survivors
        ev = r.get("evidence") or {}
        findings.extend(ev.get("survivors", []) if isinstance(ev, dict) else [])
    findings = _dedup(findings)
    # Delegate to the shared review poster (APPROVE if empty, else COMMENT/REQUEST).
    result = _review.run(findings=findings, instance=instance,
                         approve_wording="OCR review: no findings",
                         changes_wording="OCR review: findings below")
    print(json.dumps(result))

if __name__ == "__main__":
    main()
```
**Adapt to the real `_review.run` signature** — it may take a different shape (read it). The load-bearing requirement: post ONE review with the deduped, anchor-validated findings. If `_review.run` expects the `{files:[{path,findings:[...]}]}` shape, regroup `findings` by path before calling.

**NOTE (resolve in this task):** the top merge collects the file legs' *output evidence*. Confirm what `collect_fanout_evidence` returns as a file leg's `evidence` — it is the leg's last sub-state (`reduce`) evidence, which Task 2's nested-merge arm wrote as the reduce hook's `{conclusion,summary}` result. To carry the survivors up, have `reduce-file.py` include the survivors IN its printed result (`{"conclusion","summary","survivors":[...]}`) so the nested-merge arm persists them as the leg evidence. Adjust Step 2 accordingly (add `"survivors": survivors` to the printed dict) and update the Step-1 assertion to read them from the leg evidence rather than `surviving.json` if that is cleaner. Decide + document in the report.

- [ ] **Step 4: Full offline OCR walk** — extend the Task 2 `ocr-nested` walk pattern to the real `code-review-ocr` protocol with stub `main-review` evidence (a fixture), driving files → plan/main/findings/reduce → merge, asserting the merge received both files' survivors. (Model on the existing nested walk; stub agents via `ENGINE_LOCAL` fixtures.)

- [ ] **Step 5: Run** — `uv run pytest tests/test_dynamic_fanout.py -k "reduce_file or ocr_walk" -v`, then `uv run pytest tests/ -q`, then `protocol-lint` clean.

- [ ] **Step 6: Commit**
```bash
git add .github/agent-factory/protocols/code-review-ocr/publish tests/test_dynamic_fanout.py
git commit -m "feat(code-review-ocr): reduce-file + post-review publish (reuse _review.py) + offline walk"
```

---

# STAGE 3 — gh-aw agents

## Task 7: The three OCR agents (`ocr-plan`, `ocr-main`, `ocr-filter`)

**Files:**
- Create: `.github/workflows/ocr-plan-agent.md`, `ocr-main-agent.md`, `ocr-filter-agent.md`
- Create (compiled): the three `.lock.yml` via `gh aw compile`

**Interfaces:** workflow names `ocr-plan-agent` / `ocr-main-agent` / `ocr-filter-agent` (match `protocol.json` `workflow` fields). Each reads its item from `aw_context.inputs`, re-fetches diff as needed, emits evidence matching its schema.

- [ ] **Step 1: Author the three `.md`** — model each on `.github/workflows/dyn-stub-agent.md` (Spec A) for frontmatter (endpoint via `engine.env`, `strict:false`, `sandbox.agent:false`, read-only `permissions`, `run-name` cid, evidence upload, **`safe-outputs: {noop: {report-as-issue: false}, threat-detection: false}`**), and on `grumpy-agent.md` for the `gh pr diff` tool + finding wording. Bodies:
  - **`ocr-plan-agent`**: reads `aw_context.inputs.file.path`; `tools.bash: ["gh pr diff *"]`; runs `gh pr diff -- <path>`; writes `/tmp/gh-aw/evidence.json` = `{"examined":["<path>"], "plan_items":[...]}`.
  - **`ocr-main-agent`**: reads `inputs.file.path` + `inputs.plan`; re-fetches `gh pr diff -- <path>`; emits `{"files":[{"path","findings":[{finding_id, existing_code, side, line[,start_line], comment}]}]}`. finding_id must be stable + unique (e.g. `<path>:<line>:<n>`). Anchors must be real diff positions (the `traces-exist-in-diff` check enforces this).
  - **`ocr-filter-agent`**: reads `inputs.finding`; re-fetches the relevant hunk; emits `{"finding_id","keep":bool,"anchor":{side,line[,start_line]},"reason"}` — validate/relocate the anchor (OCR ReviewFilter/ReLocateComment); drop hallucinated/duplicate/low-value findings.

- [ ] **Step 2: Compile + revert drift**
```bash
gh aw compile
git status --porcelain      # revert EVERY *.lock.yml + actions-lock.json except the 3 ocr-*-agent.lock.yml
# git checkout -- <each unintended lock>
```

- [ ] **Step 3: Verify** — each `ocr-*-agent.lock.yml`: `grep -q workflow_dispatch`; the AGENT job permissions are `contents: read` + `pull-requests: read` (no write); `safe-outputs.noop.report-as-issue:false` present. `actionlint` (if present) no new errors. `uv run pytest tests/ -q` unchanged.

- [ ] **Step 4: Commit** (the 3 `.md` + their 3 `.lock.yml` only)
```bash
git add .github/workflows/ocr-plan-agent.md .github/workflows/ocr-plan-agent.lock.yml .github/workflows/ocr-main-agent.md .github/workflows/ocr-main-agent.lock.yml .github/workflows/ocr-filter-agent.md .github/workflows/ocr-filter-agent.lock.yml
git commit -m "feat(code-review-ocr): ocr-plan/main/filter gh-aw agents (read-only, noop-suppressed)"
```

---

# STAGE 4 — Live verification

## Task 8: Gated merge to `main` + live `/ocr-review` + live-debug

**Files:** none (operational). **Interactive & gated** — the user pre-authorizes the merge+live pattern per session; confirm before pushing to `main`.

- [ ] **Step 1: Full offline gate** — `uv run pytest tests/ -q` (all green) + `protocol-lint` clean on `code-review-ocr`.
- [ ] **Step 2: Confirm merge** — summarize what lands on `main` (engine matrix_fields + nested from_fanout; `code-review-ocr/`; 3 agent locks; workflow edits) and get sign-off.
- [ ] **Step 3: Merge** — `git checkout main && git merge --no-ff feat/code-review-ocr && git push origin main` (verify the branch diff has NO stray untracked WIP first: `git diff --name-only main..feat/code-review-ocr` = only intended files).
- [ ] **Step 4: Live walk** — open a PR touching **2–3 small source files with real reviewable content**; comment `/ocr-review`. Verify on Actions: `review` fanned out per file → per file `plan`+`main-review` ran (agents re-fetched their diff) → `findings` fanned out per candidate finding → `filter` per finding → per-file `reduce` → `join-review` → `merge` posted **one** GitHub review with valid inline anchors. Check the status comment renders the file legs.
- [ ] **Step 5: Edges** — a file with zero findings (findings fanout vacuous, reduce empty); an all-dropped file; confirm no matrix-size failure (path-only) and no spurious gh-aw issue (noop-suppressed).
- [ ] **Step 6: Live-debug** — expect 1–3 live-only bugs (matrix wiring for the nested findings fanout, the `expand-findings` live evidence-path resolution, join re-dispatch fields, or PUBLISH_TOKEN for the review post). Fix on a branch, re-verify, land on `main`. Record in `docs/STATUS.md`.
- [ ] **Step 7: STATUS.md** — flip `code-review-ocr` to live-verified; note remaining minors.

---

## Self-Review

**Spec coverage:**
- §2 tree → Task 3 protocol.json. ✓
- §3 expanders (expand-files reuse, expand-findings) → Tasks 3, 4. ✓
- §4.1 nested from_fanout → Task 2. ✓
- §4.2 matrix_fields + size guard → Task 1. ✓
- §5 evidence + checks (reuse traces/schema-valid, new filter-verdict-valid) → Tasks 4, 5. ✓
- §6 reduce/publish/no-gate/any → Task 6 + protocol.json (Task 3). ✓
- §7 three agents → Task 7. ✓
- §8 byte-identity → Global Constraints + Task 1/2 static-regression gates. ✓
- §9 testing → each task's offline tests + Task 6 walk. ✓
- §10 deploy/staged plan → Task 8; stages 1-4. ✓

**Placeholder scan:** the two `NOTE (resolve in this task)` blocks (Task 4 live evidence-path, Task 6 survivor carry-up) are genuine design decisions the implementer must make WITH a concrete default stated — not vague "handle it" placeholders. They are flagged because the *live* data-path is only fully knowable against the running engine; each names the concrete option to implement and to document. Task 2 Step 5's join re-dispatch fields say "mirror join.py's call" — a concrete, checkable instruction (the exact fields live in one function).

**Type/name consistency:** protocol ids (`code-review-ocr`), fanout ids (`review`, `findings`), agent names (`ocr-plan-agent`/`ocr-main-agent`/`ocr-filter-agent`), `finding_id`, `matrix_fields`, `run_merge_hook(..., consuming_path=)`, `project_matrix_item`/`check_matrix_size`, hook names (`reduce-file`, `post-review`) — used identically across tasks.

**Risks carried from the spec:** R3 (run_merge_hook node-path plumbing — Task 2 threads `_p`), R4 (live cost — Task 8 uses a small PR), R5 (plan→main input threading — protocol.json `main-review.inputs`, resolved by the existing path-aware `resolve_inputs`). The highest-uncertainty task is **Task 2's leg-terminal nested merge** (novel engine semantics) — its offline walk test is the pin.
