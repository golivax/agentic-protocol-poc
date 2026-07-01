# Dynamic Fan-out — Live GitHub-Actions Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Milestone-1's dynamic (data-driven) fan-out run live on GitHub Actions, proven end-to-end by a minimal `dyn-fanout-stub` protocol driven by a real diff-parsing `expand-files` expander.

**Architecture:** Milestone 1 already seeds a dynamic fanout's legs into a runtime `__manifest.yaml` in the `plan` job and emits a path-aware `legs[]` action that the live matrix consumes — so the runtime-matrix "wiring" (brief item 1) is largely **already present** and this plan mostly *verifies* it. The genuinely new work is: (item 2) threading each dynamic leg's runtime item to its sandboxed agent per-leg via the matrix, (item 3) scoping the expander subprocess to a read-only token, and (item 5) making the human status comment + lint tree render dynamic legs. Every change fires **only** on the dynamic markers (`expand`/`policy`/`from_fanout`), so the static path stays byte-identical.

**Tech Stack:** Python 3 + PyYAML (engine, runtime deps only), pytest (dev-only, `ENGINE_LOCAL` offline layer), GitHub Actions YAML, gh-aw (`gh aw compile`) for the agent.

## Global Constraints

- **Static path byte-identity:** every existing protocol and `tests/fixtures/` walk must stay green and byte-unchanged. Dynamic code fires only when a node has `expand` (or a leg carries `inputs`), a join has `policy`, or a merge has `from_fanout`. Copied verbatim from the spec §7.
- **Check ABI:** a check is invoked `<check> <evidence.json> <diff.txt> <changed-files.txt>`, prints one JSON `{"check","pass","feedback"}` to stdout, and **always exits 0** (nonzero = runner error only).
- **Expander ABI:** invoked `<hook> <state-dir> <instance-key>` with `PR` in env; prints `{"items":[...]}`; **fails loud** (nonzero exit / raise) on error. Trusted, runs in zone 1 (plan).
- **Four trust zones unchanged:** the expander stays in zone 1 (plan job); item 3 only scopes *what credential it sees*, never moves it. Agents (zone 2) stay read-only and never hold the state PAT.
- **Security rule:** agent-derived strings (`inputs`, `feedback`, filenames) reach shell steps via `env:`, NEVER interpolated into `run:` blocks.
- **Workflows run from `main`:** the orchestrator, engine, `protocol-join`, the new protocol dir, and the compiled agent lock must land on the default branch before the live walk (spec §9).
- **Run tests with:** `uv run pytest tests/ -q` (whole suite) or `uv run pytest tests/test_dynamic_fanout.py -v` (this module).
- **Dates are absolute:** today is 2026-07-01.

---

## File Structure

**New — the live stub protocol** (`.github/agent-factory/protocols/dyn-fanout-stub/`):
- `protocol.json` — single top-level dynamic `fanout` (`review`) → `join(policy:all)` → `done`.
- `expand/expand-files` — the real diff-parsing expander (reused verbatim by Spec B). Dual-mode: `ENGINE_LOCAL` reads a beside-script fixture; live parses `gh pr diff`.
- `expand/items.json` — the `ENGINE_LOCAL` fixture item list (offline determinism only).
- `leg.evidence.schema.json` — the trivial per-file rubric (`examined` attestation).
- `checks/examined-file.py` — deterministic check: evidence `examined` names the leg's file.

**New — the stub agent:**
- `.github/workflows/dyn-stub-agent.md` → compiled `dyn-stub-agent.lock.yml`.

**Modified — engine:**
- `.github/agent-factory/engine/next.py` — `enter_node` dynamic arm attaches the per-leg item; `_fanout_action` copies it into `legs[].inputs` (item 2).
- `.github/agent-factory/engine/lib.py` — `run_expander` env allowlist-scrub + `EXPAND_PARAMS` (item 3); `render_fanout_status_body` manifest-aware (item 5).
- `.github/agent-factory/engine/protocol-lint.py` — tree renderer dynamic-leg-aware (item 5).

**Modified — workflows:**
- `.github/workflows/agentic-engine.yml` — `plan` job: `EXPANDER_TOKEN` + `permissions` (item 3); `dispatch` job: per-leg `matrix.leg.inputs` → `aw_context.inputs` (item 2).

**Modified — tests & docs:**
- `tests/test_dynamic_fanout.py` — all new offline coverage.
- `docs/STATUS.md` — flip item-3 "known deviation" and item-5 "cosmetic gap" to done.

---

## Task 1: Real `expand-files` diff-parsing expander

**Files:**
- Create: `.github/agent-factory/protocols/dyn-fanout-stub/expand/expand-files`
- Create: `.github/agent-factory/protocols/dyn-fanout-stub/expand/items.json`
- Test: `tests/test_dynamic_fanout.py` (append)

**Interfaces:**
- Produces: an executable expander honoring the Expander ABI. Emits `{"items":[{"path","diff"}...]}`. Reads filter thresholds from `EXPAND_PARAMS` (JSON of the node's `expand` object) with defaults; under `ENGINE_LOCAL` reads `items.json` beside the script. Consumed by `lib.run_expander`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_dynamic_fanout.py`)

```python
import json, os, subprocess, textwrap
EXPANDER = ".github/agent-factory/protocols/dyn-fanout-stub/expand/expand-files"

def _run_expander(env_extra, cwd="."):
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
    body = "\n".join(f"+line{i}" for i in range(2000))
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -k expand_files -v`
Expected: FAIL — expander file does not exist (`No such file or directory`).

- [ ] **Step 3: Write the expander**

Create `.github/agent-factory/protocols/dyn-fanout-stub/expand/expand-files`:

```python
#!/usr/bin/env python3
"""Real diff-parsing expander for dyn-fanout-stub (and, verbatim, code-review-ocr
in Spec B). Emits one item per changed file: {"path","diff"}. OCR-style pre-filters
skip binary, vendored/generated, and oversized-diff files. Fails loud on a gh error.

Modes:
  ENGINE_LOCAL=1  -> read items.json beside this script (deterministic offline test).
  EXPAND_FILES_DIFF_FILE=<path> -> parse that file (test injection, no gh needed).
  otherwise       -> parse `gh pr diff <PR>` (live, zone 1, read-only token).

Thresholds come from EXPAND_PARAMS (JSON of the node's `expand` object) with defaults.
"""
import json, os, re, subprocess, sys

VENDOR_RE = re.compile(
    r'(^|/)(vendor|node_modules|dist|build|third_party|\.venv)(/|$)'
    r'|\.min\.(js|css)$|(^|/)(go\.sum|package-lock\.json|uv\.lock|yarn\.lock)$'
)

def _params():
    try:
        return json.loads(os.environ.get("EXPAND_PARAMS", "") or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}

def _load_diff(pr):
    inj = os.environ.get("EXPAND_FILES_DIFF_FILE")
    if inj:
        with open(inj) as f:
            return f.read()
    r = subprocess.run(["gh", "pr", "diff", str(pr)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"expand-files: gh pr diff {pr} failed: {r.stderr.strip()}")
    return r.stdout

def _split_files(diff_text):
    """Yield (path, chunk) per `diff --git a/<p> b/<p>` header."""
    chunks = re.split(r'(?m)^(?=diff --git )', diff_text)
    for chunk in chunks:
        if not chunk.startswith("diff --git "):
            continue
        m = re.match(r'diff --git a/(.+?) b/(.+)', chunk.splitlines()[0])
        if not m:
            continue
        yield m.group(2).strip(), chunk

def _is_binary(chunk):
    return "\nBinary files " in ("\n" + chunk) or chunk.count("GIT binary patch")

def _diff_lines(chunk):
    return sum(1 for ln in chunk.splitlines()
               if (ln.startswith("+") or ln.startswith("-")) and not ln.startswith(("+++", "---")))

def main():
    pr = os.environ.get("PR", "")
    max_diff_lines = int(_params().get("max_diff_lines", 1500))
    if os.environ.get("ENGINE_LOCAL"):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "items.json")) as f:
            print(json.dumps({"items": json.load(f)}))
        return
    items = []
    for path, chunk in _split_files(_load_diff(pr)):
        if _is_binary(chunk):
            continue
        if VENDOR_RE.search(path):
            continue
        if _diff_lines(chunk) > max_diff_lines:
            continue
        items.append({"path": path, "diff": chunk})
    print(json.dumps({"items": items}))

if __name__ == "__main__":
    main()
```

Create `.github/agent-factory/protocols/dyn-fanout-stub/expand/items.json`:

```json
[
  { "path": "src/example_one.py", "diff": "diff --git a/src/example_one.py b/src/example_one.py\n@@ -1 +1 @@\n-old\n+new\n" },
  { "path": "src/example_two.py", "diff": "diff --git a/src/example_two.py b/src/example_two.py\n@@ -1 +1 @@\n-old\n+new\n" }
]
```

- [ ] **Step 4: Make the expander executable and run tests**

```bash
chmod +x .github/agent-factory/protocols/dyn-fanout-stub/expand/expand-files
uv run pytest tests/test_dynamic_fanout.py -k expand_files -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/protocols/dyn-fanout-stub/expand tests/test_dynamic_fanout.py
git commit -m "feat(dyn-fanout): real diff-parsing expand-files expander with OCR pre-filters"
```

---

## Task 2: `dyn-fanout-stub` protocol + offline engine walk

**Files:**
- Create: `.github/agent-factory/protocols/dyn-fanout-stub/protocol.json`
- Create: `.github/agent-factory/protocols/dyn-fanout-stub/leg.evidence.schema.json`
- Create: `.github/agent-factory/protocols/dyn-fanout-stub/checks/examined-file.py`
- Test: `tests/test_dynamic_fanout.py` (append)

**Interfaces:**
- Consumes: `expand/expand-files` (Task 1).
- Produces: a lint-clean, offline-walkable single-phase dynamic protocol. The fanout id is `review`; the `each` agent workflow name is `dyn-stub-agent` (Task 7 compiles it). Manifest lands at `dyn-fanout-stub/<instance>/review.__manifest.yaml`; leg state at `review.<legid>.yaml` (single-phase drops the leading id).

- [ ] **Step 1: Write the protocol + schema + check**

`.github/agent-factory/protocols/dyn-fanout-stub/protocol.json`:

```json
{
  "$schema": "../../engine/protocol.schema.json",
  "name": "dyn-fanout-stub",
  "triggers": [
    { "on": "issue_comment", "command": "/dyn-stub" }
  ],
  "states": [
    { "id": "review", "kind": "fanout",
      "expand": { "hook": "expand-files", "as": "file", "id_from": "$.path", "max_legs": 32,
                  "max_diff_lines": 1500 },
      "each": { "workflow": "dyn-stub-agent", "evidence": "leg.evidence.schema.json",
                "max_iterations": 1,
                "checks": [ { "run": "examined-file", "on_fail": "iterate" } ] },
      "next": "join" },
    { "id": "join", "kind": "join", "of": "review", "policy": "all", "next": "done" }
  ]
}
```

`.github/agent-factory/protocols/dyn-fanout-stub/leg.evidence.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["examined"],
  "properties": {
    "examined": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1
    }
  }
}
```

`.github/agent-factory/protocols/dyn-fanout-stub/checks/examined-file.py`:

```python
#!/usr/bin/env python3
"""dyn-fanout-stub check: the evidence attests it `examined` this leg's file.
The file path is the leg's staged item, surfaced to the agent as inputs.file.path;
here we accept any non-empty `examined` array (form check, per the engine thesis:
verify the shape of evidence, never its substance). ABI: <evidence> <diff> <changed>."""
import json, sys

def main():
    try:
        ev = json.load(open(sys.argv[1]))
    except Exception as e:
        print(json.dumps({"check": "examined-file", "pass": False,
                          "feedback": f"unreadable evidence: {e}"}))
        return
    examined = ev.get("examined")
    ok = isinstance(examined, list) and len(examined) >= 1 and all(
        isinstance(x, str) and x.strip() for x in examined)
    print(json.dumps({"check": "examined-file", "pass": bool(ok),
                      "feedback": "" if ok else "evidence.examined must be a non-empty list of file paths"}))

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make the check executable and lint the protocol**

```bash
chmod +x .github/agent-factory/protocols/dyn-fanout-stub/checks/examined-file.py
python3 .github/agent-factory/engine/protocol-lint.py .github/agent-factory/protocols/dyn-fanout-stub/protocol.json
```
Expected: validation passes (may warn "jsonschema absent → semantic-only"); tree prints the `review [fanout]` node. **Note:** the tree may show zero legs for the dynamic fanout — that blind spot is fixed in Task 6; it is not a lint failure.

- [ ] **Step 3: Write the failing offline-walk test** (append to `tests/test_dynamic_fanout.py`)

Use the existing `run_engine` / `read_state_yaml` fixtures from `tests/conftest.py`. Model this test on the existing `dyn-fanout-flat` walk in the same module (find it with `grep -n "dyn-fanout-flat" tests/test_dynamic_fanout.py` and mirror its setup — same `run_engine` signature, `ENGINE_LOCAL` env, and `read_state_yaml` assertions).

```python
STUB = ".github/agent-factory/protocols/dyn-fanout-stub/protocol.json"

def test_dyn_stub_start_materializes_legs(engine_env, tmp_path):
    # `run_engine` drives next.py with ENGINE_LOCAL; expand-files reads items.json (2 items).
    action = run_engine(STUB, "pr-7", "start")           # returns the emitted action dict
    assert action["action"] == "run-fanout"
    legs = action["legs"]
    assert len(legs) == 2                                 # one leg per fixture item
    assert all(l["workflow"] == "dyn-stub-agent" for l in legs)
    # single-phase: leg path is the bare leg id (no leading "review.")
    assert all("." not in l["path"] for l in legs)
    # manifest persisted at the FULL tree path
    man = read_state_yaml("dyn-fanout-stub", "pr-7", "review.__manifest.yaml")
    assert man["count"] == 2
```

(Adjust `run_engine`/`read_state_yaml` call shapes to match the module's existing helpers exactly — do not invent a new signature.)

- [ ] **Step 4: Run the walk test**

Run: `uv run pytest tests/test_dynamic_fanout.py -k dyn_stub_start -v`
Expected: PASS. If it fails on helper signatures, align them to the existing `dyn-fanout-flat` test (same file) — the engine behavior is already implemented.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/protocols/dyn-fanout-stub tests/test_dynamic_fanout.py
git commit -m "feat(dyn-fanout): dyn-fanout-stub protocol (single-phase fanout->join->done) + offline walk"
```

---

## Task 3: Item 1 — assert runtime-matrix legs + multi-phase `state_path` de-risk

**Files:**
- Test: `tests/test_dynamic_fanout.py` (append)
- (Likely no engine change — this task is verification. If an assertion fails, fix the specific `_fanout_action`/`enter_node` line it points to.)

**Interfaces:**
- Consumes: Task 2's protocol; the M1 sub-pipeline fixture `tests/fixtures/dyn-fanout-subpipeline`.
- Produces: proof that `action.legs` is runtime-sized with correct `path`/`workflow` for both a flat and a sub-pipeline-each dynamic leg, and that multi-phase leg state-file naming keeps the leading id (de-risks Spec B).

- [ ] **Step 1: Write the failing tests** (append)

```python
SUBPIPE = "tests/fixtures/dyn-fanout-subpipeline/protocol.json"

def test_dyn_subpipeline_leg_path_includes_first_substate(engine_env):
    # A sub-pipeline `each` leg path = <fanout>.<legid>.<first-substate>.
    action = run_engine(SUBPIPE, "pr-9", "start")
    assert action["action"] == "run-fanout"
    for leg in action["legs"]:
        parts = leg["path"].split(".")
        # depth: fanout id + leg id + first substate (>=3 for a multi-phase sub-pipeline)
        assert parts[-1], "first substate must be present in a sub-pipeline leg path"
        assert leg["workflow"], "each-template workflow must be carried per leg"

def test_dyn_matrix_cap_matches_max_legs():
    # GHA strategy.matrix caps at 256; M1 max_legs must never exceed it.
    import json
    proto = json.load(open(STUB))
    fo = next(s for s in proto["states"] if s.get("kind") == "fanout")
    assert fo["expand"]["max_legs"] <= 256
```

For the multi-phase de-risk, add a tiny fixture `tests/fixtures/dyn-multiphase/` (a `preflight` agent phase → dynamic `review` fanout → `join` → `done`, expander = a fixed 2-item stub like `dyn-fanout-flat`'s `expand-items.py`). Then:

```python
MP = "tests/fixtures/dyn-multiphase/protocol.json"

def test_multiphase_dynamic_leg_keeps_leading_id(engine_env):
    # Multi-phase: state_path keeps the full path -> leg file is review.<legid>.yaml,
    # NOT <legid>.yaml. Walk to the fanout, then assert the leg state-file name.
    run_engine(MP, "pr-11", "start")                    # runs preflight agent
    advance_engine(MP, "pr-11", node="preflight", verdicts=[{"check": "noop", "pass": True}])
    # after preflight advances, review fanout materializes; assert leg file naming
    man = read_state_yaml("dyn-multiphase", "pr-11", "review.__manifest.yaml")
    legid = man["legs"][0]["id"]
    assert state_file_exists("dyn-multiphase", "pr-11", f"review.{legid}.yaml")
```

(Use whatever advance/exists helpers `tests/conftest.py` provides — `grep -n "def " tests/conftest.py`; if none exist for "file exists", assert via `read_state_yaml` returning non-None.)

- [ ] **Step 2: Run to verify — expect PASS or a precise failure**

Run: `uv run pytest tests/test_dynamic_fanout.py -k "subpipeline_leg_path or matrix_cap or multiphase" -v`
Expected: PASS (the M1 engine already builds these). If `multiphase` fails, the fix is in `lib.state_path`/`_fanout_action` for the multi-phase branch — fix the exact line, re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/test_dynamic_fanout.py tests/fixtures/dyn-multiphase
git commit -m "test(dyn-fanout): assert runtime-matrix leg shape + multi-phase state_path (item 1)"
```

---

## Task 4: Item 2 — per-leg runtime item to the agent via the matrix

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (`enter_node` dynamic arm ~line 126-132; `_fanout_action` ~line 79-83)
- Modify: `.github/workflows/agentic-engine.yml` (`dispatch` job `wait` step, ~line 372-393)
- Test: `tests/test_dynamic_fanout.py` (append)

**Interfaces:**
- Consumes: the manifest legs' `item` (already in memory in `enter_node`).
- Produces: each dynamic leg dict carries `inputs: {<as>: <item>}`; `_fanout_action` copies it to `legs[].inputs`; the dispatch job injects it per-leg into `aw_context.inputs`. Static legs have no `inputs` key (byte-identical).

- [ ] **Step 1: Write the failing test** (append)

```python
def test_dyn_legs_carry_per_leg_inputs(engine_env):
    action = run_engine(STUB, "pr-13", "start")
    legs = action["legs"]
    assert len(legs) == 2
    for leg in legs:
        assert "inputs" in leg, "dynamic leg must carry its runtime item"
        assert "file" in leg["inputs"], "keyed by the expand `as` name"
        assert "path" in leg["inputs"]["file"]

def test_static_fanout_legs_have_no_inputs(engine_env):
    # Regression: a static fanout's legs are byte-identical (no inputs key).
    action = run_engine("tests/fixtures/simple-fanout/protocol.json", "pr-13", "start")
    assert all("inputs" not in leg for leg in action["legs"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -k "per_leg_inputs or static_fanout_legs" -v`
Expected: `test_dyn_legs_carry_per_leg_inputs` FAILS (`"inputs" not in leg`); the static one PASSES.

- [ ] **Step 3: Attach the item in `enter_node`** (`next.py`, dynamic arm)

Find (around line 126-132):

```python
            for leg in manifest["legs"]:
                cfg = dict(each)
                cfg["id"] = leg["id"]
                seeded = _seed_child(proto, path + [leg["id"]], cfg)
                lib.stage_item(DIR, PID, INSTANCE, lib.state_path(proto, path + [leg["id"]]),
                               node["expand"]["as"], leg["item"])
                branches.append(seeded)
```

Replace the `branches.append(seeded)` with:

```python
                seeded["inputs"] = {node["expand"]["as"]: leg["item"]}
                branches.append(seeded)
```

- [ ] **Step 4: Copy the item into the leg in `_fanout_action`** (`next.py` ~line 79-83)

Find:

```python
    legs = []
    for b in branches:
        leaf = path + [b["id"]] + ([b["substate"]] if b.get("substate") else [])
        legs.append({"path": ".".join(leaf), "workflow": b.get("workflow")})
    act["legs"] = legs
```

Replace with:

```python
    legs = []
    for b in branches:
        leaf = path + [b["id"]] + ([b["substate"]] if b.get("substate") else [])
        leg = {"path": ".".join(leaf), "workflow": b.get("workflow")}
        if b.get("inputs"):            # dynamic legs only; static branches never carry this
            leg["inputs"] = b["inputs"]
        legs.append(leg)
    act["legs"] = legs
```

- [ ] **Step 5: Run the engine tests**

Run: `uv run pytest tests/test_dynamic_fanout.py -k "per_leg_inputs or static_fanout_legs" -v && uv run pytest tests/test_engine.py -q`
Expected: both new tests PASS; `test_engine.py` (static regression) stays green.

- [ ] **Step 6: Wire the dispatch job to prefer per-leg inputs** (`agentic-engine.yml`, `dispatch` job `wait` step)

In the `wait` step's `env:` block (currently ends with `SHA:` around line 376), add:

```yaml
          LEG_INPUTS: ${{ toJSON(matrix.leg.inputs) }}
```

Then in that step's `run:` script, find:

```bash
          INPUTS="$INPUTS_JSON"; [ -n "$INPUTS" ] || INPUTS='{}'
```

Replace with:

```bash
          # Prefer this leg's per-leg runtime item (dynamic fanout); fall back to the
          # shared declared-inputs blob (static). LEG_INPUTS is 'null' when absent.
          if [ -n "$LEG_INPUTS" ] && [ "$LEG_INPUTS" != "null" ]; then
            INPUTS="$LEG_INPUTS"
          else
            INPUTS="$INPUTS_JSON"; [ -n "$INPUTS" ] || INPUTS='{}'
          fi
```

(The item reaches the agent via `aw_context.inputs.<as>` — the existing `--argjson inputs "$INPUTS"` at line ~392 is unchanged. `INPUTS` is agent-derived data flowing through `env:` → `jq --argjson`, never interpolated into the shell, per the security rule.)

- [ ] **Step 7: Lint the workflow**

```bash
actionlint .github/workflows/agentic-engine.yml || true   # if actionlint present
uv run pytest tests/ -q                                    # full suite still green
```
Expected: no new actionlint errors; suite green.

- [ ] **Step 8: Commit**

```bash
git add .github/agent-factory/engine/next.py .github/workflows/agentic-engine.yml tests/test_dynamic_fanout.py
git commit -m "feat(dyn-fanout): thread each dynamic leg's runtime item to its agent via matrix.leg.inputs (item 2)"
```

---

## Task 5: Item 3 — expander credential-scoping (env allowlist)

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (`run_expander`, ~line 208-233)
- Modify: `.github/workflows/agentic-engine.yml` (`plan` job env + `permissions`)
- Test: `tests/test_dynamic_fanout.py` (append)

**Interfaces:**
- Produces: `run_expander` passes the subprocess a strict allowlist env (`PATH`,`HOME`,`LANG`,`LC_ALL`,`PR`,`ENGINE_LOCAL`,`GITHUB_REPOSITORY`), sets `GH_TOKEN` to `EXPANDER_TOKEN` (read-only) when present, sets `EXPAND_PARAMS` to the node's `expand` JSON, and **omits** `STATE_REMOTE`/`PUBLISH_TOKEN`/the broad PAT.

- [ ] **Step 1: Write the failing test** (append)

Use a probe expander that dumps its env to a file, then assert the sensitive vars are gone. Create `tests/fixtures/dyn-envprobe/expand/expand-items` (executable):

```python
#!/usr/bin/env python3
import json, os
open(os.environ["ENVPROBE_OUT"], "w").write(json.dumps(dict(os.environ)))
print(json.dumps({"items": [{"path": "x"}]}))
```

Add a fixture `tests/fixtures/dyn-envprobe/protocol.json` (single dynamic fanout, `hook: expand-items`, `id_from: $.path`, `max_legs: 4`, minimal `each`). Test:

```python
def test_run_expander_scrubs_sensitive_env(engine_env, tmp_path, monkeypatch):
    from importlib import import_module
    lib = import_module("lib")   # tests already put the engine dir on sys.path
    out = tmp_path / "env.json"
    monkeypatch.setenv("ENVPROBE_OUT", str(out))
    monkeypatch.setenv("STATE_REMOTE", "https://x-access-token:SECRET@github.com/o/r.git")
    monkeypatch.setenv("PUBLISH_TOKEN", "SECRET_PAT")
    monkeypatch.setenv("GH_TOKEN", "SECRET_PAT")
    monkeypatch.setenv("EXPANDER_TOKEN", "read-only-tok")
    proto = "tests/fixtures/dyn-envprobe/protocol.json"
    node = {"expand": {"hook": "expand-items", "id_from": "$.path", "max_legs": 4, "as": "x"}}
    lib.run_expander(str(tmp_path), "dyn-envprobe", "pr-1", proto, node)
    seen = json.loads(out.read_text())
    assert "STATE_REMOTE" not in seen
    assert seen.get("PUBLISH_TOKEN") is None
    assert seen.get("GH_TOKEN") == "read-only-tok"     # replaced by the read token
    assert json.loads(seen["EXPAND_PARAMS"])["max_legs"] == 4
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -k scrubs_sensitive_env -v`
Expected: FAIL — `STATE_REMOTE`/`PUBLISH_TOKEN` present (current code does `env = dict(os.environ)`).

- [ ] **Step 3: Rewrite `run_expander`'s env construction** (`lib.py`)

Find:

```python
    env = dict(os.environ)
    env.setdefault("PR", instance[len("pr-"):] if instance.startswith("pr-") else instance)
    r = subprocess.run([path, dir_, instance], text=True, capture_output=True, env=env)
```

Replace with:

```python
    # SECURITY (spec §5): scope the expander to a read-only token. Build the env
    # from a strict ALLOWLIST — never the plan job's full env — so STATE_REMOTE /
    # PUBLISH_TOKEN / the broad dispatch PAT are dropped by default (a future added
    # plan-job env var cannot leak). The expander gets only a read token to fetch
    # the diff.
    _ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "PR", "ENGINE_LOCAL", "GITHUB_REPOSITORY")
    env = {k: os.environ[k] for k in _ALLOW if k in os.environ}
    env.setdefault("PR", instance[len("pr-"):] if instance.startswith("pr-") else instance)
    tok = os.environ.get("EXPANDER_TOKEN")
    if tok:
        env["GH_TOKEN"] = tok                       # read-only; never the state/publish PAT
    env["EXPAND_PARAMS"] = json.dumps(fanout_node.get("expand", {}))
    r = subprocess.run([path, dir_, instance], text=True, capture_output=True, env=env)
```

Also update the docstring: change the "handed only a read token" sentence to state it is now **enforced via an allowlist** (no longer aspirational).

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_dynamic_fanout.py -k scrubs_sensitive_env -v && uv run pytest tests/test_dynamic_fanout.py -q`
Expected: PASS; the rest of the dynamic-fanout suite (which runs under `ENGINE_LOCAL`, in the allowlist) stays green.

- [ ] **Step 5: Thread the read-only token + permissions into the workflow** (`agentic-engine.yml`, `plan` job)

In the `plan` step's `env:` block (the one with `STATE_REMOTE`/`GH_TOKEN`/`PUBLISH_TOKEN`, ~line 241-254) add:

```yaml
          # Read-only token for the expander subprocess (lib.run_expander scopes the
          # expander to THIS token only; the broad PAT above never reaches it).
          EXPANDER_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Ensure the `plan` job (or the workflow) grants the default token diff-read scope. At the `plan:` job level (below `runs-on:`), add or confirm:

```yaml
    permissions:
      contents: read
      pull-requests: read
```

**Verify (R2):** confirm an `issue_comment`-triggered run on `main` gets a `GITHUB_TOKEN` with these scopes — a live `/dyn-stub` (Task 9) is the real test; if `gh pr diff` 403s there, fall back to `EXPANDER_TOKEN: ${{ secrets.POC_DISPATCH_TOKEN }}` (still scoped by the allowlist, just a broader token) and note it in STATUS.md.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py .github/workflows/agentic-engine.yml tests/test_dynamic_fanout.py tests/fixtures/dyn-envprobe
git commit -m "harden(dyn-fanout): scope expander subprocess to a read-only token via env allowlist (item 3)"
```

---

## Task 6: Item 5 — dynamic-leg-aware rendering (status comment + lint tree)

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (`render_fanout_status_body`, branch-collection loop)
- Modify: `.github/agent-factory/engine/protocol-lint.py` (`_children` ~line 131-132; `_render_parallel` ~line 372)
- Test: `tests/test_dynamic_fanout.py` (append)

**Interfaces:**
- Consumes: `lib.read_manifest` / `resolve_leg_ids`.
- Produces: for a fanout with `expand`, `render_fanout_status_body` renders one section per manifest leg; the lint tree shows the `each` template as the leg shape with an `inputs: legs ← <id_from>` annotation.

- [ ] **Step 1: Write the failing renderer test** (append)

```python
def test_status_body_renders_dynamic_legs(engine_env, tmp_path):
    from importlib import import_module
    lib = import_module("lib")
    run_engine(STUB, "pr-15", "start")     # materializes 2 legs + manifest
    body = lib.render_fanout_status_body(state_dir(), "dyn-fanout-stub", "pr-15", STUB)
    # both dynamic leg ids appear as sections (was: zero sections pre-fix)
    man = read_state_yaml("dyn-fanout-stub", "pr-15", "review.__manifest.yaml")
    for leg in man["legs"]:
        assert leg["id"] in body
```

(`state_dir()` / `state_dir` — use the same helper the module already uses to locate the `ENGINE_LOCAL` state root; grep the existing tests.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_dynamic_fanout.py -k renders_dynamic_legs -v`
Expected: FAIL — body contains zero leg sections (current loop reads only `state["branches"]`).

- [ ] **Step 3: Make `render_fanout_status_body` manifest-aware** (`lib.py`)

Find the branch-collection loop:

```python
    # Find the fanout state and its branches
    branches = []
    for state in protocol.get("states", []):
        if state.get("kind") == "fanout":
            for b in state.get("branches", []):
                branches.append(b)
            break
```

Replace with:

```python
    # Find the fanout state and its legs. Static: the declared branches[]. Dynamic
    # (expand present): synthesize one leg per persisted manifest entry so the human
    # status comment renders dynamic legs (check-run gating already uses the manifest).
    branches = []
    for state in protocol.get("states", []):
        if state.get("kind") == "fanout":
            fo_id = state.get("id")
            if state.get("expand"):
                each = state.get("each", {})
                man = read_manifest(dir_, pid, instance, [fo_id])
                for leg in man.get("legs", []):
                    branches.append({"id": leg["id"],
                                     "max_iterations": each.get("max_iterations", "?")})
            else:
                for b in state.get("branches", []):
                    branches.append(b)
            break
```

(`read_manifest` is already defined in this module. The downstream `state_file(dir_, pid, instance, bid)` call resolves the single-phase leg file `<legid>.yaml` correctly.)

- [ ] **Step 4: Run the renderer test**

Run: `uv run pytest tests/test_dynamic_fanout.py -k renders_dynamic_legs -v`
Expected: PASS.

- [ ] **Step 5: Make the lint tree renderer dynamic-leg-aware** (`protocol-lint.py`)

Find (`_children`, ~line 131-132):

```python
    if node.get("kind") == "fanout":
        return node.get("branches", [])
```

Replace with:

```python
    if node.get("kind") == "fanout":
        # Dynamic fanout: no static branches[] — show the `each` template as the
        # single (runtime-replicated) leg shape so the tree isn't empty.
        if node.get("expand"):
            each = dict(node.get("each", {}))
            each.setdefault("id", f"«each ×{node['expand'].get('id_from','?')}»")
            return [each]
        return node.get("branches", [])
```

And in `_render_parallel` (~line 372), find:

```python
    legs = fanout.get("branches", []) or []
```

Replace with:

```python
    legs = fanout.get("branches", []) or (
        [dict(fanout.get("each", {}), id=f"«each»")] if fanout.get("expand") else [])
```

- [ ] **Step 6: Verify the lint tree now shows the leg**

```bash
python3 .github/agent-factory/engine/protocol-lint.py .github/agent-factory/protocols/dyn-fanout-stub/protocol.json
```
Expected: the `review [fanout]` node now prints a child leg (the `«each»` template) instead of nothing.

- [ ] **Step 7: Full suite + commit**

```bash
uv run pytest tests/ -q
git add .github/agent-factory/engine/lib.py .github/agent-factory/engine/protocol-lint.py tests/test_dynamic_fanout.py
git commit -m "feat(dyn-fanout): render dynamic legs in status comment + lint tree (item 5)"
```

---

## Task 7: The `dyn-stub-agent` gh-aw agent

**Files:**
- Create: `.github/workflows/dyn-stub-agent.md`
- Create (compiled): `.github/workflows/dyn-stub-agent.lock.yml` (via `gh aw compile`)

**Interfaces:**
- Consumes: `aw_context.inputs.file.path` (the per-leg item from Task 4).
- Produces: `/tmp/gh-aw/evidence.json` = `{"examined": ["<path>"]}` satisfying `examined-file` (Task 2). Workflow name `dyn-stub-agent` matches the protocol's `each.workflow`.

- [ ] **Step 1: Write the agent source** (`.github/workflows/dyn-stub-agent.md`)

Model the frontmatter on `.github/workflows/grumpy-agent.md` (custom Anthropic endpoint via `engine.env`, `strict:false`, `sandbox.agent:false`, `run-name` cid, read-only permissions, evidence upload post-step). Body:

```markdown
---
name: "Dyn Stub Agent (protocol state: review)"
run-name: "Dyn Stub Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
strict: false
sandbox:
  agent: false
engine:
  id: claude
  model: claude-sonnet-4-6
  env:
    ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
    ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
permissions:
  contents: read
  pull-requests: read
tools:
  cli-proxy: true
  edit: true
pre-agent-steps:
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
      cat /tmp/gh-aw/task-context.json
post-steps:
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 10
---

# Dyn Stub Agent — plumbing proof

This is a STUB agent proving the dynamic-fanout live wiring. Do no real review.

## Task context

Read `/tmp/gh-aw/task-context.json`. Its `.inputs.file.path` is the changed file this
leg was fanned out for. (`.pr`, `.iteration`, `.feedback` are also present.)

## Your job

Write `/tmp/gh-aw/evidence.json` containing exactly:

    { "examined": ["<the .inputs.file.path value>"] }

Use the file path from the task context verbatim. Do not add other keys.
```

- [ ] **Step 2: Compile the lock**

```bash
gh aw compile
git status --porcelain .github/workflows/dyn-stub-agent.lock.yml
```
Expected: `dyn-stub-agent.lock.yml` created/updated.

- [ ] **Step 3: Sanity-check the compiled lock**

```bash
grep -q "workflow_dispatch" .github/workflows/dyn-stub-agent.lock.yml && echo OK
actionlint .github/workflows/dyn-stub-agent.lock.yml || true
```
Expected: `OK`; no new actionlint errors.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/dyn-stub-agent.md .github/workflows/dyn-stub-agent.lock.yml
git commit -m "feat(dyn-fanout): dyn-stub-agent (reads per-leg inputs.file, emits examined evidence)"
```

---

## Task 8: Documentation — flip STATUS.md deviations to done

**Files:**
- Modify: `docs/STATUS.md` (Dynamic fan-out section, ~line 115-142)

**Interfaces:** none (docs only).

- [ ] **Step 1: Update the "Deferred to milestone 2" bullet**

In `docs/STATUS.md`, in the dynamic-fanout section, edit the deferred/deviation bullets to record what Spec A shipped:
- Live GHA runtime-matrix wiring for a manifest-expanded leg set: **done** (verified by the `dyn-fanout-stub` live walk).
- Staging the per-leg item to the agent: **done** via `matrix.leg.inputs` → `aw_context.inputs` (note: not a filesystem `inputs/<as>.json` — a refinement of the spec's framing to match the live inline-context mechanism).
- Expander credential-scoping: **enforced** — replace the "Known deviation" paragraph with a note that `run_expander` now builds a read-only allowlist env (`EXPANDER_TOKEN`).
- Status-comment / lint tree dynamic-leg rendering: **done** (item 5).
- Still deferred to **Spec B**: nested `from_fanout`, the real `code-review-ocr` protocol, per-finding nested fan-out.

- [ ] **Step 2: Commit**

```bash
git add docs/STATUS.md
git commit -m "docs(status): dynamic fan-out live wiring shipped (M2 Spec A: items 1,2,3,5)"
```

---

## Task 9: Gated merge to `main` + live `/dyn-stub` verification

**Files:** none (operational). This task is **interactive and gated** — it pushes to `main` and runs real Actions. Do NOT perform the merge without explicit user confirmation.

**Interfaces:** Consumes everything above.

- [ ] **Step 1: Full offline gate**

```bash
uv run pytest tests/ -q
```
Expected: entire suite green (all static regression + new dynamic tests).

- [ ] **Step 2: Confirm the merge with the user**

Per CLAUDE.md, workflows/agent-locks run from `main`. Summarize what will land on `main` (engine changes, `dyn-fanout-stub` protocol, `dyn-stub-agent.lock.yml`, workflow edits) and get explicit sign-off before merging `feat/dynamic-fanout-live-wiring` → `main`.

- [ ] **Step 3: Merge to `main`** (after sign-off)

```bash
git checkout main && git merge --no-ff feat/dynamic-fanout-live-wiring
git push origin main
```

- [ ] **Step 4: Live walk on a real PR**

Open (or reuse) a PR touching ≥2 non-binary/non-vendored files, comment `/dyn-stub`, and verify on Actions:
- `plan` job expands the real diff → manifest with one leg per changed file.
- `dispatch` matrix runs one `dyn-stub-agent` per leg; each receives its `inputs.file.path`.
- `checks` pass; `join(all)` → protocol `done`; aggregate check-run success.
- The PR status comment renders one section per file (item 5).
- Edge: a PR whose files are all filtered (binary/vendored/oversized) → zero legs, vacuous fanout, no silent hang.

- [ ] **Step 5: Live-debug pass**

Expect 1–3 live-only bugs (every prior milestone had them — e.g. a missing token in a job, a path mismatch). Fix on a branch, re-verify, and land the fixes on `main`. Record any residual gap in `docs/STATUS.md`.

- [ ] **Step 6: Final commit / status**

```bash
git add docs/STATUS.md   # if live-debug notes were added
git commit -m "docs(status): dyn-fanout live-verified on PR #<N> (M2 Spec A complete)"
git push origin main
```

---

## Self-Review

**Spec coverage** (against `2026-07-01-dynamic-fanout-live-wiring-design.md`):
- §2 stub protocol + §2.1 real `expand-files` + §2.2 stub agent → Tasks 1, 2, 7. ✓
- §2 multi-phase `state_path` de-risk (offline) → Task 3. ✓
- §3 item 1 runtime-matrix wiring → Task 3 (verification; R1 confirmed already-present). ✓
- §4 item 2 stage per-leg item → Task 4 (refined to `matrix.leg.inputs`; noted in Task 8). ✓
- §5 item 3 expander credential-scoping → Task 5. ✓
- §6 item 5 dynamic-leg rendering (status + lint) → Task 6. ✓
- §7 byte-identity invariant → Global Constraints + Task 4 static-regression test + Task 6 full-suite gate. ✓
- §8 testing (offline primary + live) → Tasks 1-6 offline, Task 9 live. ✓
- §9 deploy/merge-to-main → Task 9. ✓
- §1 non-goals (item 4 nested from_fanout, OCR protocol) → explicitly out; not tasked. ✓

**Placeholder scan:** no TBD/TODO; every code step shows concrete code. The two "grep the existing helper" notes (Tasks 2, 3, 6) point at real, existing `tests/conftest.py` fixtures the implementer must match rather than invent — they are alignment instructions, not missing content.

**Type/name consistency:** fanout id `review`, `as` name `file`, agent workflow `dyn-stub-agent`, check `examined-file`, evidence key `examined`, env var `EXPANDER_TOKEN`/`EXPAND_PARAMS`, leg field `inputs` — all used identically across Tasks 1-9.

**Open risks carried from the spec (revisit during execution):** R1 (largely resolved — engine already emits dynamic legs, so Task 3 is verification); R2 (default-token diff-read scope — Task 5 Step 5 has the fallback); R3 (staged-item timing — obviated by Task 4's in-memory matrix approach, no state re-read in zone 2); R4 (live-only bugs — Task 9 budgets a debug pass).
