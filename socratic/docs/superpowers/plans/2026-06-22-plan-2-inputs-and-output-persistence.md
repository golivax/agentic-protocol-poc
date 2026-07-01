# Plan 2 — Inputs Channel + Output Persistence

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist each state's output artifact to the state branch, and let a state declare `inputs:` so the engine resolves + stages prior states' outputs for it to consume.

**Architecture:** On `done`, `advance.py` copies the agent's `evidence.json` to a deterministic path beside the state file (`…<substate>.evidence.json`). A new `lib.resolve_inputs` maps each `{from, as}` ref to that persisted path given the consumer's scope, and `lib.materialize_inputs` copies them into an `inputs/<as>.json` directory. The dispatch action JSON carries the resolved input manifest so the engine workflow can stage files into the agent job (the agent stays read-only — it never touches the state branch).

**Tech Stack:** Python 3 + PyYAML (runtime), pytest (dev). Uses `shutil` (stdlib) for copies.

**Depends on:** Plan 1 (sub-pipeline branches, `state_file(..., substate=)`, `branch_substates`).

## Global Constraints

- Runtime deps limited to **Python 3 + PyYAML** (+ stdlib). No third-party imports.
- Every new field optional; existing suite stays green.
- The agent job is **read-only** and never holds the state PAT. Inputs reach it as staged files, never as a state-branch checkout.
- Persisted artifacts are untrusted (agent-produced): downstream consumers read them as **data**, never eval/interpolate into a shell `run:` block.
- Checks ABI and CAS-push invariants unchanged.

---

### Task 1: `output_artifact_path` — where a state's output is persisted

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add after `state_file`)
- Test: `tests/test_inputs.py` (create)

**Interfaces:**
- Produces: `lib.output_artifact_path(d, pid, instance, branch=None, phase=None, substate=None, kind="evidence") -> str` — the persisted-artifact path, parallel to `state_file` but with a `.<kind>.json` suffix instead of `.yaml`. `kind` is `"evidence"` (agent) or `"answers"` (gate, used in Plan 3).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_inputs.py
import importlib, sys
from conftest import ENGINE
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")


def test_output_artifact_path_substate():
    p = lib.output_artifact_path("/s", "rev", "pr-1", branch="B", substate="draft")
    assert p == "/s/rev/pr-1/B.draft.evidence.json"


def test_output_artifact_path_flat_leg():
    p = lib.output_artifact_path("/s", "rev", "pr-1", branch="A")
    assert p == "/s/rev/pr-1/A.evidence.json"


def test_output_artifact_path_answers_kind():
    p = lib.output_artifact_path("/s", "rev", "pr-1", branch="B", substate="clarify", kind="answers")
    assert p == "/s/rev/pr-1/B.clarify.answers.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_inputs.py -k output_artifact_path -v`
Expected: FAIL — `module 'lib' has no attribute 'output_artifact_path'`.

- [ ] **Step 3: Implement**

Add to `lib.py` after `state_file`:

```python
def output_artifact_path(d, pid, instance, branch=None, phase=None, substate=None, kind="evidence"):
    """Persisted-output path for a state, parallel to state_file but with a
    .<kind>.json suffix. kind is 'evidence' (agent) or 'answers' (gate)."""
    sf = state_file(d, pid, instance, branch=branch, phase=phase, substate=substate)
    return sf[:-len(".yaml")] + f".{kind}.json"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_inputs.py -k output_artifact_path -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_inputs.py
git commit -m "feat(engine): output_artifact_path helper"
```

---

### Task 2: `advance.py` persists evidence on `done`

**Files:**
- Modify: `.github/agent-factory/engine/advance.py` (in the `process == "done"` block, before each `cas_push`)
- Test: `tests/test_inputs.py`

**Interfaces:**
- Consumes: `lib.output_artifact_path`; the `evid` path (argv[5]).
- Produces: when a state reaches `done`, its `evidence.json` is copied to `output_artifact_path(...)` and committed in the same CAS push. Applies to the sub-pipeline leg path (Plan 1, Task 6) and the flat-leg / agent-phase paths.

- [ ] **Step 1: Write the failing test**

```python
import json, subprocess
from conftest import run_engine, read_state_yaml, FIXTURES


def _clone(tmp_path, engine_env):
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    return work


def test_evidence_persisted_on_done(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    verdicts = tmp_path / "v.json"
    verdicts.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    evid = tmp_path / "evidence.json"
    evid.write_text(json.dumps({"summary": "draft output", "questions": []}))
    e = dict(engine_env); e.update(BRANCH="B", SUBSTATE="draft",
                                   PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    out, err, rc = run_engine("advance.py", tmp_path / "dir", "pr-1", proto, verdicts, evid, env=e)
    assert rc == 0, err
    work = _clone(tmp_path, engine_env)
    persisted = work / "subpipeline-mini/pr-1/B.draft.evidence.json"
    assert persisted.exists()
    assert json.loads(persisted.read_text())["summary"] == "draft output"
```

> Note: `subpipeline-mini` is a single-phase fanout, so branch state paths have NO phase prefix (`B.draft.*`). Do NOT set `PHASE` when invoking advance on this fixture — it must match what `start_fanout` seeded (no phase).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_inputs.py -k evidence_persisted -v`
Expected: FAIL — no `B.draft.evidence.json` is written.

- [ ] **Step 3: Implement persistence**

Add a small helper near the top of `advance.py` (after imports):

```python
import shutil


def persist_output(dir_, pid, instance, branch, phase, substate, evid, kind="evidence"):
    """Copy the agent's artifact to its deterministic persisted path so
    downstream `inputs` can resolve it. Best-effort: a missing/empty evid is a
    no-op (the leg simply has no output to forward)."""
    if not evid or not os.path.isfile(evid):
        return
    dst = lib.output_artifact_path(dir_, pid, instance,
                                   branch=(branch or None), phase=(phase or None),
                                   substate=(substate or None), kind=kind)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(evid, dst)
```

In the `process == "done"` block, call it once at the very top (right after `state_data["state"] = "done"` is written at `:319-321`, before the sub-pipeline branch path from Plan 1):

```python
        persist_output(dir_, pid, instance, branch, phase, substate, evid)
```

Because this runs before the Plan-1 `if branch and substate: … return` block and before the flat/agent-phase logic, every `done` path persists exactly once.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_inputs.py -k evidence_persisted -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: green — persistence is additive; existing tests don't assert on the absence of these files.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/advance.py tests/test_inputs.py
git commit -m "feat(engine): persist evidence artifact on state done"
```

---

### Task 3: `resolve_inputs` — map `{from, as}` refs to persisted paths

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add helpers)
- Test: `tests/test_inputs.py`

**Interfaces:**
- Produces:
  - `lib.branch_output_substate(protocol, branch) -> str|None` — the last sub-state id of a sub-pipeline branch (its leg output), or None for a flat branch.
  - `lib.state_inputs(protocol, state_id) -> list[dict]` — the `inputs` list declared on a state or sub-state (searches phases and branch sub-states), `[]` if none.
  - `lib.resolve_inputs(protocol, d, pid, instance, consuming_branch, consuming_phase, inputs) -> list[dict]` — each `{ "as": <name>, "path": <persisted-artifact-path>, "kind": <evidence|answers> }`. A `from` is resolved as: a sub-state of `consuming_branch` → that sub-state's artifact; a branch id → that branch's leg-output artifact (last sub-state, or flat leg); a phase id → that phase's artifact.

- [ ] **Step 1: Write the failing test**

```python
SUBPIPE = {
    "name": "rev",
    "states": [
        {"id": "review", "kind": "fanout", "branches": [
            {"id": "A", "workflow": "a"},
            {"id": "B", "states": [
                {"id": "draft", "kind": "agent", "workflow": "d"},
                {"id": "finalize", "kind": "agent", "workflow": "f",
                 "inputs": [{"from": "draft", "as": "draft"}]},
            ]},
        ]},
        {"id": "join", "kind": "join", "of": "review", "next": "combine"},
        {"id": "combine", "kind": "merge", "inputs": [
            {"from": "A", "as": "a"}, {"from": "B", "as": "b"}]},
    ],
}


def test_branch_output_substate():
    assert lib.branch_output_substate(SUBPIPE, "B") == "finalize"
    assert lib.branch_output_substate(SUBPIPE, "A") is None


def test_state_inputs():
    assert lib.state_inputs(SUBPIPE, "finalize") == [{"from": "draft", "as": "draft"}]
    assert lib.state_inputs(SUBPIPE, "combine")[0]["from"] == "A"
    assert lib.state_inputs(SUBPIPE, "draft") == []


def test_resolve_inputs_sibling_substate():
    res = lib.resolve_inputs(SUBPIPE, "/s", "rev", "pr-1",
                             consuming_branch="B", consuming_phase=None,
                             inputs=[{"from": "draft", "as": "draft"}])
    assert res == [{"as": "draft",
                    "path": "/s/rev/pr-1/B.draft.evidence.json",
                    "kind": "evidence"}]


def test_resolve_inputs_branch_leg_outputs():
    res = lib.resolve_inputs(SUBPIPE, "/s", "rev", "pr-1",
                             consuming_branch=None, consuming_phase=None,
                             inputs=[{"from": "A", "as": "a"}, {"from": "B", "as": "b"}])
    paths = {r["as"]: r["path"] for r in res}
    assert paths["a"] == "/s/rev/pr-1/A.evidence.json"
    assert paths["b"] == "/s/rev/pr-1/B.finalize.evidence.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_inputs.py -k "branch_output_substate or state_inputs or resolve_inputs" -v`
Expected: FAIL — helpers undefined.

- [ ] **Step 3: Implement the helpers**

Add to `lib.py`:

```python
def branch_output_substate(protocol, branch):
    """The last sub-state id of a sub-pipeline branch (its leg output), else None."""
    subs = branch_substates(protocol, branch)
    return subs[-1]["id"] if subs else None


def state_inputs(protocol, state_id):
    """The `inputs` list declared on a top-level state OR a branch sub-state."""
    st = state_by_id(protocol, state_id)
    if st is not None:
        return list(st.get("inputs", []))
    fo = _fanout_state(protocol)
    if fo:
        for b in fo.get("branches", []):
            for s in b.get("states", []):
                if s.get("id") == state_id:
                    return list(s.get("inputs", []))
    return []


def _branch_ids(protocol):
    fo = _fanout_state(protocol)
    return [b["id"] for b in fo.get("branches", [])] if fo else []


def resolve_inputs(protocol, d, pid, instance, consuming_branch, consuming_phase, inputs):
    """Map each {from, as} to {as, path, kind}. Resolution order for `from`:
      1) a sub-state of the consuming branch  → that sub-state's evidence
      2) a fanout branch id                   → that branch's leg-output evidence
                                                 (last sub-state, or the flat leg)
      3) a phase id                           → that phase's evidence
    `kind` is 'evidence' unless the source sub-state is a gate (then 'answers')."""
    phase = consuming_phase or None
    out = []
    sub_ids = {s["id"]: s for s in branch_substates(protocol, consuming_branch)} if consuming_branch else {}
    branch_ids = set(_branch_ids(protocol))
    for ref in inputs:
        frm, as_ = ref["from"], ref["as"]
        if frm in sub_ids:
            kind = "answers" if sub_ids[frm].get("kind") == "gate" else "evidence"
            path = output_artifact_path(d, pid, instance, branch=consuming_branch,
                                        phase=phase, substate=frm, kind=kind)
        elif frm in branch_ids:
            last = branch_output_substate(protocol, frm)
            path = output_artifact_path(d, pid, instance, branch=frm, phase=phase,
                                        substate=last, kind="evidence")
        else:
            path = output_artifact_path(d, pid, instance, phase=frm, kind="evidence")
            kind = "evidence"
            out.append({"as": as_, "path": path, "kind": kind})
            continue
        out.append({"as": as_, "path": path, "kind": kind})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_inputs.py -k "branch_output_substate or state_inputs or resolve_inputs" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_inputs.py
git commit -m "feat(engine): resolve_inputs maps input refs to persisted paths"
```

---

### Task 4: `materialize_inputs` — stage resolved inputs into a directory

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add helper)
- Test: `tests/test_inputs.py`

**Interfaces:**
- Produces: `lib.materialize_inputs(resolved, target_dir) -> list[dict]` — for each resolved input whose `path` exists, copy it to `target_dir/inputs/<as>.json`; return a manifest of `{as, staged_path}` (skipping any source that does not exist). Creates `target_dir/inputs/` if missing.

- [ ] **Step 1: Write the failing test**

```python
def test_materialize_inputs(tmp_path):
    src = tmp_path / "src.json"; src.write_text('{"k": 1}')
    resolved = [{"as": "draft", "path": str(src), "kind": "evidence"},
                {"as": "missing", "path": str(tmp_path / "nope.json"), "kind": "evidence"}]
    manifest = lib.materialize_inputs(resolved, tmp_path / "agentwork")
    staged = {m["as"]: m["staged_path"] for m in manifest}
    assert set(staged) == {"draft"}   # missing source skipped
    assert (tmp_path / "agentwork/inputs/draft.json").read_text() == '{"k": 1}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_inputs.py -k materialize_inputs -v`
Expected: FAIL — helper undefined.

- [ ] **Step 3: Implement**

```python
def materialize_inputs(resolved, target_dir):
    """Copy each existing resolved input to <target_dir>/inputs/<as>.json.
    Returns [{as, staged_path}] for the ones that existed."""
    inputs_dir = os.path.join(str(target_dir), "inputs")
    os.makedirs(inputs_dir, exist_ok=True)
    manifest = []
    for r in resolved:
        if not os.path.isfile(r["path"]):
            continue
        dst = os.path.join(inputs_dir, f"{r['as']}.json")
        shutil.copyfile(r["path"], dst)
        manifest.append({"as": r["as"], "staged_path": dst})
    return manifest
```

Add `import shutil` at the top of `lib.py` if not already present (it imports `glob, json, os, subprocess, sys, yaml` — add `shutil`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_inputs.py -k materialize_inputs -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_inputs.py
git commit -m "feat(engine): materialize_inputs stages resolved inputs into inputs/"
```

---

### Task 5: Dispatch carries the resolved input manifest

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (`emit_run_agent`, and the sub-state seed in `start_fanout`/advance re-dispatch path)
- Test: `tests/test_inputs.py`

**Interfaces:**
- Consumes: `lib.state_inputs`, `lib.resolve_inputs`.
- Produces: when emitting `run-agent` (or a `run-fanout` branch entry) for a state that declares `inputs`, the action JSON includes `"inputs": [{as, path, kind}, …]` (resolved, not yet staged — staging happens in the engine workflow, which downloads them to the agent job). States without `inputs` emit no `inputs` key (byte-identical).

- [ ] **Step 1: Write the failing test**

Add `inputs` to the fixture's `finalize` sub-state first. Edit `tests/fixtures/subpipeline-mini/protocol.json` branch B `finalize`:

```json
{
  "id": "finalize",
  "kind": "agent",
  "workflow": "finalize-agent",
  "evidence": "finalize.evidence.schema.json",
  "max_iterations": 2,
  "inputs": [{ "from": "draft", "as": "draft" }],
  "checks": [{ "run": "always-pass", "on_fail": "iterate" }]
}
```

Then the test:

```python
def test_run_agent_action_carries_inputs(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    # Resume finalize → its action should carry resolved inputs.
    out, err, rc = run_engine("next.py", tmp_path / "dir", "pr-1", proto, "continue",
                              env=engine_env, branch="B", substate="finalize")
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-agent"
    names = {i["as"]: i for i in action.get("inputs", [])}
    assert "draft" in names
    assert names["draft"]["path"].endswith("B.draft.evidence.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_inputs.py -k action_carries_inputs -v`
Expected: FAIL — `action` has no `inputs` key.

- [ ] **Step 3: Implement in `next.py`**

Extend `emit_run_agent` (already touched in Plan 1, Task 7) to attach resolved inputs:

```python
def emit_run_agent(iteration, feedback, reason):
    action = {"action": "run-agent", "iteration": iteration, "feedback": feedback, "reason": reason}
    if PHASE:
        action["phase"] = PHASE
    if SUBSTATE:
        action["substate"] = SUBSTATE
    declared = lib.state_inputs(proto_data, AGENT_STATE)
    if declared:
        action["inputs"] = lib.resolve_inputs(
            proto_data, DIR, PID, INSTANCE,
            consuming_branch=(BRANCH or None), consuming_phase=(PHASE or None),
            inputs=declared)
    print(json.dumps(action))
```

`AGENT_STATE` is already resolved above (`_unit["agent_state"]`). For the `run-fanout` first-dispatch case, a sub-pipeline branch's first sub-state rarely has inputs (the first step has no predecessor), so `start_fanout` need not resolve them; inputs are resolved on the per-sub-state `continue`/seed path. (If a first sub-state ever declares inputs from another branch, that is a Plan-4 concern.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_inputs.py -k action_carries_inputs -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: green — states without `inputs` emit no `inputs` key.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/fixtures/subpipeline-mini/protocol.json tests/test_inputs.py
git commit -m "feat(engine): dispatch action carries resolved input manifest"
```

---

### Task 6: End-to-end — `draft` output reaches `finalize`'s inputs

**Files:**
- Test: `tests/test_inputs.py`

**Interfaces:**
- Consumes: Tasks 2 (persist), 3 (resolve), 5 (dispatch manifest).
- Produces: after `draft` runs to `done`, advancing to `finalize` and resolving its inputs yields a path that exists and contains the draft output. Then `materialize_inputs` stages it as `inputs/draft.json`.

- [ ] **Step 1: Write the end-to-end test**

```python
def test_draft_output_flows_to_finalize_inputs(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)

    # draft → done, persisting evidence with a distinctive payload.
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "draft-evidence.json"
    ev.write_text(json.dumps({"summary": "DRAFT-PAYLOAD"}))
    e = dict(engine_env); e.update(BRANCH="B", SUBSTATE="draft",
                                   PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir", "pr-1", proto, v, ev, env=e)

    # Resolve finalize's inputs from the freshly pushed state, then materialize.
    work = _clone(tmp_path, engine_env)
    import importlib, sys
    sys.path.insert(0, str(ENGINE)); _lib = importlib.import_module("lib")
    declared = _lib.state_inputs(json.loads(proto.read_text()), "finalize")
    resolved = _lib.resolve_inputs(json.loads(proto.read_text()), str(work),
                                   "subpipeline-mini", "pr-1",
                                   consuming_branch="B", consuming_phase=None,
                                   inputs=declared)
    manifest = _lib.materialize_inputs(resolved, tmp_path / "agentwork")
    staged = (tmp_path / "agentwork/inputs/draft.json")
    assert staged.exists()
    assert json.loads(staged.read_text())["summary"] == "DRAFT-PAYLOAD"
```

(`ENGINE` is importable from `conftest`; add `from conftest import ENGINE` at the top of the test file if not already present.)

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_inputs.py -k draft_output_flows -v`
Expected: PASS.

- [ ] **Step 3: Run the full suite**

Run: `pytest tests/ -q`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_inputs.py
git commit -m "test(engine): e2e draft evidence flows into finalize inputs"
```

---

### Task 7: Document the engine-workflow staging seam (integration)

**Files:**
- Modify: `docs/STATUS.md` (add an "inputs channel" note)
- Modify: `.github/workflows/agentic-engine.yml` (stage `action.inputs` into the agent job)

**Interfaces:**
- Consumes: the `inputs` array on the dispatch action JSON (Task 5).
- Produces: the engine workflow, after `next.py`/`advance.py` emit an action with `inputs`, reads each `{path}` from the checked-out `agentic-state` branch (the plan/dispatch job already has it), uploads them as a workflow artifact, and the agent job downloads them to `inputs/<as>.json` in its workspace before the agent runs. Agent prompt docs reference `inputs/<name>.json`.

> This task is YAML/integration wiring with no pytest coverage. Validate by reading the workflow and, if a live PR is available, a manual smoke run. Keep agent-derived content out of `run:` interpolation — pass paths via `env:`.

- [ ] **Step 1: Add the STATUS.md note**

Document under a new "## Inputs channel" heading: outputs persist to `<pid>/<instance>/<scope>.evidence.json`; a state's `inputs:[{from,as}]` are resolved by `lib.resolve_inputs`, staged by the engine workflow as `inputs/<as>.json`, and the agent reads them read-only. Untrusted-data handling rule restated.

- [ ] **Step 2: Wire `agentic-engine.yml`**

In the dispatch job, after capturing the action JSON, add a step that (a) `jq`-extracts `.inputs`, (b) copies each `path` from the state checkout into a staging dir, (c) `actions/upload-artifact`. In the agent job, add an `actions/download-artifact` step into the agent workspace under `inputs/`. Pass the action JSON via `env:`, never inline into `run:`.

- [ ] **Step 3: Lint the workflow**

Run: `gh aw compile` (if agent frontmatter changed) and `actionlint .github/workflows/agentic-engine.yml` if available.
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add docs/STATUS.md .github/workflows/agentic-engine.yml
git commit -m "feat(workflow): stage resolved inputs into the agent job"
```

---

## Self-Review (Plan 2)

- **Spec coverage:** §3 inputs channel + output persistence → Tasks 1-6 (pytest) + Task 7 (workflow staging). The "transport = workflow-artifact" plan-time decision is realised in Task 7.
- **Placeholder scan:** Task 7 is intentionally integration-level (YAML); its steps name exact files/actions. All pytest tasks have complete code.
- **Type consistency:** `output_artifact_path(kind=)`, `resolve_inputs(consuming_branch=, consuming_phase=, inputs=)`, `materialize_inputs(resolved, target_dir)`, manifest keys `as`/`path`/`kind` and `as`/`staged_path` used consistently across Tasks 1-6 and consumed in Plans 3-4.

## Plan 2 → Plans 3/4 handoff

- Plan 3 (gate) reuses `output_artifact_path(kind="answers")` to persist the gate's answers and `resolve_inputs` so `finalize` consumes them.
- Plan 4 (merge) reuses `resolve_inputs` with branch-id refs (`{from:"A"}`, `{from:"B"}`) and `materialize_inputs` to feed the merge hook.
