# Multi-phase + sub-pipeline fanout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a multi-phase pipeline whose one fanout phase contains a sub-pipeline branch work end-to-end (dispatch first sub-state → `/answer` a nested gate → advance → join), closing the latent `next.py` gap.

**Architecture:** The 3-rung representation (`phase + branch + substate`) already exists across `lib.state_file`, `resolve_agent_unit`, `advance.py`, and `join.py`. Only `next.py`'s planner/emit side doesn't drive it. We fix that by (1) extracting one shared per-branch seeding helper `seed_branch` that both the single-phase (`start_fanout`) and multi-phase (`seed_and_dispatch_phase`) emit paths call, and (2) threading a `phase` qualifier (derived once via `_gate_phase`) through the `/answer` gate path. No new env vars; no changes outside `next.py` and a new test fixture.

**Tech Stack:** Python 3 + PyYAML (engine runtime). pytest (dev-only) with `tests/conftest.py` helpers `run_engine` / `read_state_yaml` / `engine_env` / `state_origin`.

## Global Constraints

- Engine code must stay generic: no protocol-specific logic in `.github/agent-factory/engine/`. (CLAUDE.md)
- Runtime depends only on Python 3 + PyYAML; pytest is dev-only and not vendored. (CLAUDE.md)
- No new env var: reuse the existing `BRANCH`/`PHASE`/`SUBSTATE` seam — one code path, not a second. (spec, CLAUDE.md "BRANCH seam")
- Existing fixtures `single-agent`, `fanout-mini`, `pipeline-mini`, `subpipeline-mini` must keep passing **unchanged** — they regression-guard that the `start_fanout` refactor and the `phase=None` default preserve single-phase behavior. (spec "Risks")
- Engine scripts always exit 0 on success; `ENGINE_LOCAL=1` echoes `gh` calls to stderr instead of executing them (tests assert against stderr). (conftest)
- Each task ends green: run the named tests AND the full suite (`pytest tests/ -q`) before committing.
- Scope is Problem #1 only. Do NOT add arbitrary-depth recursion scaffolding (Problem #2 is a separate spec).

---

## File Structure

- **Create** `tests/fixtures/multiphase-subpipeline/` — a self-contained fixture protocol that is `subpipeline-mini` made multi-phase by prepending a `setup` agent phase. This is the only protocol that exercises the broken cell (multi-phase **and** a sub-pipeline branch).
  - `protocol.json` — `setup` (agent) → `review` (fanout: flat `A` + sub-pipeline `B`) → `join` → `combine` (merge).
  - `setup.evidence.schema.json`, `a.evidence.schema.json`, `draft.evidence.schema.json`, `finalize.evidence.schema.json`
  - `checks/always-pass.py`, `checks/answers-coverage.py`
  - `publish/append-outputs.py`
- **Modify** `.github/agent-factory/engine/next.py` — add `seed_branch` + `_gate_phase`; route `start_fanout` and the `seed_and_dispatch_phase` fanout arm through `seed_branch`; phase-qualify `_find_open_gate_branch` and `do_answer`.
- **Create** `tests/test_multiphase_subpipeline.py` — all new regression tests for this work.

---

### Task 1: Add the `multiphase-subpipeline` test fixture

A new protocol that is multi-phase (`is_multiphase` true) **and** has a sub-pipeline branch — the combination no existing fixture covers. Built by reusing `subpipeline-mini`'s branch `B` (draft→clarify→finalize) and flat branch `A`, with a `setup` agent phase prepended so `len(phase_states) == 2`.

**Files:**
- Create: `tests/fixtures/multiphase-subpipeline/protocol.json`
- Create: `tests/fixtures/multiphase-subpipeline/setup.evidence.schema.json`
- Create: `tests/fixtures/multiphase-subpipeline/a.evidence.schema.json`
- Create: `tests/fixtures/multiphase-subpipeline/draft.evidence.schema.json`
- Create: `tests/fixtures/multiphase-subpipeline/finalize.evidence.schema.json`
- Create: `tests/fixtures/multiphase-subpipeline/checks/always-pass.py`
- Create: `tests/fixtures/multiphase-subpipeline/checks/answers-coverage.py`
- Create: `tests/fixtures/multiphase-subpipeline/publish/append-outputs.py`
- Test: `tests/test_multiphase_subpipeline.py`

**Interfaces:**
- Consumes: nothing (fixture data + `conftest` helpers).
- Produces: fixture path `tests/fixtures/multiphase-subpipeline/protocol.json` with: top-level phases `["setup", "review"]`; fanout `review` with branches `A` (flat, `workflow: a-agent`) and `B` (sub-pipeline `draft`→`clarify`→`finalize`, `clarify` a gate with `questions_from: draft` + `answers-coverage` check). Later tasks drive this fixture.

- [ ] **Step 1: Write the fixture sanity test (failing — fixture absent)**

Create `tests/test_multiphase_subpipeline.py`:

```python
import json
import shutil
import subprocess
from pathlib import Path

from conftest import run_engine, read_state_yaml, FIXTURES, ENGINE  # noqa: F401

import sys
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

PROTO = FIXTURES / "multiphase-subpipeline/protocol.json"


def _load():
    return json.loads(PROTO.read_text())


def _state_dir(tmp_path, engine_env, suffix=""):
    """Clone the fake origin so we can read pushed state files back."""
    work = tmp_path / f"work{suffix}"
    if work.exists():
        shutil.rmtree(work)
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    return work


def test_fixture_is_multiphase_with_subpipeline_branch():
    proto = _load()
    # Two phases (setup agent + review fanout) → multi-phase.
    assert lib.is_multiphase(proto) is True
    assert [s["id"] for s in lib.phase_states(proto)] == ["setup", "review"]
    # review fanout: A flat, B sub-pipeline (draft -> clarify -> finalize).
    assert lib.is_subpipeline_branch(lib.branch_config(proto, "A")) is False
    assert lib.is_subpipeline_branch(lib.branch_config(proto, "B")) is True
    assert [s["id"] for s in lib.branch_substates(proto, "B")] == ["draft", "clarify", "finalize"]
    # The fanout phase id is what _gate_phase will derive.
    assert lib._fanout_state(proto)["id"] == "review"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/test_multiphase_subpipeline.py::test_fixture_is_multiphase_with_subpipeline_branch -v`
Expected: FAIL (FileNotFoundError / cannot read `protocol.json` — fixture not yet created).

- [ ] **Step 3: Create the fixture files**

`tests/fixtures/multiphase-subpipeline/protocol.json`:

```json
{
  "name": "multiphase-subpipeline",
  "version": "0.1.0",
  "triggers": [
    { "on": "pull_request", "actions": ["opened"], "command": "start" }
  ],
  "states": [
    {
      "id": "setup",
      "kind": "agent",
      "workflow": "setup-agent",
      "evidence": "setup.evidence.schema.json",
      "max_iterations": 2,
      "checks": [{ "run": "always-pass", "on_fail": "iterate" }],
      "next": "review"
    },
    {
      "id": "review",
      "kind": "fanout",
      "branches": [
        {
          "id": "A",
          "workflow": "a-agent",
          "evidence": "a.evidence.schema.json",
          "max_iterations": 2,
          "checks": [{ "run": "always-pass", "on_fail": "iterate" }],
          "publish": "noop"
        },
        {
          "id": "B",
          "states": [
            {
              "id": "draft",
              "kind": "agent",
              "workflow": "draft-agent",
              "evidence": "draft.evidence.schema.json",
              "max_iterations": 2,
              "checks": [{ "run": "always-pass", "on_fail": "iterate" }]
            },
            {
              "id": "clarify",
              "kind": "gate",
              "questions_from": "draft",
              "checks": [{ "run": "answers-coverage", "on_fail": "iterate" }]
            },
            {
              "id": "finalize",
              "kind": "agent",
              "workflow": "finalize-agent",
              "evidence": "finalize.evidence.schema.json",
              "max_iterations": 2,
              "inputs": [{ "from": "clarify", "as": "answers" },
                         { "from": "draft", "as": "draft" }],
              "checks": [{ "run": "always-pass", "on_fail": "iterate" }]
            }
          ]
        }
      ],
      "next": "join"
    },
    { "id": "join", "kind": "join", "of": "review", "next": "combine" },
    {
      "id": "combine",
      "kind": "merge",
      "hook": "append-outputs",
      "inputs": [{ "from": "A", "as": "a" }, { "from": "B", "as": "b" }],
      "next": "done"
    }
  ]
}
```

Each of `setup.evidence.schema.json`, `a.evidence.schema.json`, `draft.evidence.schema.json`, `finalize.evidence.schema.json` (identical minimal schema):

```json
{ "$schema": "http://json-schema.org/draft-07/schema#", "type": "object" }
```

Copy the two checks and the publish hook from `subpipeline-mini` (identical content), preserving the executable bit:

```bash
mkdir -p tests/fixtures/multiphase-subpipeline/checks tests/fixtures/multiphase-subpipeline/publish
cp tests/fixtures/subpipeline-mini/checks/always-pass.py     tests/fixtures/multiphase-subpipeline/checks/
cp tests/fixtures/subpipeline-mini/checks/answers-coverage.py tests/fixtures/multiphase-subpipeline/checks/
cp tests/fixtures/subpipeline-mini/publish/append-outputs.py  tests/fixtures/multiphase-subpipeline/publish/
chmod +x tests/fixtures/multiphase-subpipeline/checks/*.py tests/fixtures/multiphase-subpipeline/publish/*.py
```

- [ ] **Step 4: Run the sanity test to confirm it passes**

Run: `pytest tests/test_multiphase_subpipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/multiphase-subpipeline tests/test_multiphase_subpipeline.py
git commit -m "test: add multiphase-subpipeline fixture (multi-phase + sub-pipeline branch)"
```

---

### Task 2: Extract the shared `seed_branch` helper (refactor `start_fanout`, no behavior change)

Factor the per-branch seeding currently inlined in `start_fanout` (`next.py:60-88`) into one helper that both emit paths will call. This task is a pure refactor: the existing `subpipeline-mini` / `fanout-mini` tests are the safety net and must stay green unchanged.

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (add `seed_branch`; rewrite `start_fanout`'s branch loop)

**Interfaces:**
- Produces: `seed_branch(b, fanout_id, phase=None) -> dict`. Writes the branch's state file(s) under the `phase`-qualified path (or unqualified when `phase is None`) and returns the branch dict for the `run-fanout` emit:
  - sub-pipeline branch → writes cursor file (`sub_state=<first>`, no `head_sha`) + first sub-state file (`head_sha=HEAD_SHA`); returns `{"id", "workflow": <first.workflow>, "substate": <first.id>, "iteration": 1, "feedback": ""}`.
  - flat branch → writes one branch file (`head_sha` included **only** when `phase` is set, matching the pre-existing divergence between the two paths); returns `{"id", "workflow": <b.workflow>, "iteration": 1, "feedback": ""}`.
- Consumes: module globals `DIR, PID, INSTANCE, HEAD_SHA` and `lib.state_file`, `lib.is_subpipeline_branch`, `lib.dump_yaml`.

- [ ] **Step 1: Write the refactor-equivalence test**

Add to `tests/test_multiphase_subpipeline.py` (this asserts `start_fanout`'s single-phase behavior is byte-compatible after the refactor, using `subpipeline-mini`):

```python
def test_start_fanout_single_phase_unchanged(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    out, err, rc = run_engine("next.py", tmp_path / "d", "pr-1", proto, "start", "abc123",
                              env=engine_env)
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-fanout"
    b = next(x for x in action["branches"] if x["id"] == "B")
    assert b["substate"] == "draft" and b["workflow"] == "draft-agent"
    a = next(x for x in action["branches"] if x["id"] == "A")
    assert "substate" not in a
    work = _state_dir(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "draft" and cursor["state"] == "review"
    assert "head_sha" not in cursor                      # single-phase cursor omits head_sha
    sub = read_state_yaml(work / "subpipeline-mini/pr-1/B.draft.yaml")
    assert sub["state"] == "review" and sub["iteration"] == 1
    flat = read_state_yaml(work / "subpipeline-mini/pr-1/A.yaml")
    assert "head_sha" not in flat                        # single-phase flat omits head_sha
```

- [ ] **Step 2: Run it to confirm it passes against the CURRENT code**

Run: `pytest tests/test_multiphase_subpipeline.py::test_start_fanout_single_phase_unchanged -v`
Expected: PASS (this codifies current behavior before we touch `start_fanout`).

- [ ] **Step 3: Add `seed_branch` and route `start_fanout` through it**

In `next.py`, add `seed_branch` immediately after `emit_run_fanout` (around line 41):

```python
def seed_branch(b, fanout_id, phase=None):
    """Seed one fan-out branch's state file(s) and return its run-fanout emit dict.
    Used by BOTH the single-phase (start_fanout, phase=None) and multi-phase
    (seed_and_dispatch_phase, phase set) paths — one seeding logic, two callers.
    `phase` qualifies the state-file path; head_sha is included on the flat file
    only when `phase` is set (preserving the pre-existing single/multi divergence)."""
    bid = b["id"]
    if lib.is_subpipeline_branch(b):
        first = b["states"][0]
        cf = lib.state_file(DIR, PID, INSTANCE, bid, phase=phase)
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        cur = {"protocol": PID, "instance": INSTANCE, "state": fanout_id,
               "sub_state": first["id"], "iteration": 1, "gates": {}, "history": []}
        lib.dump_yaml(cf, cur)
        sf = lib.state_file(DIR, PID, INSTANCE, bid, phase=phase, substate=first["id"])
        lib.dump_yaml(sf, {"protocol": PID, "instance": INSTANCE, "state": fanout_id,
                           "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": []})
        return {"id": bid, "workflow": first["workflow"],
                "substate": first["id"], "iteration": 1, "feedback": ""}
    sf = lib.state_file(DIR, PID, INSTANCE, bid, phase=phase)
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    flat = {"protocol": PID, "instance": INSTANCE, "state": fanout_id,
            "iteration": 1, "gates": {}, "history": []}
    if phase:
        flat["head_sha"] = HEAD_SHA
    lib.dump_yaml(sf, flat)
    return {"id": bid, "workflow": b["workflow"], "iteration": 1, "feedback": ""}
```

Then replace the branch loop in `start_fanout` (`next.py:60-88`, the `branches = []` / `for b in branches_config:` block through the flat `else:` branch append) with:

```python
    branches = [seed_branch(b, fstate) for b in branches_config]
```

Leave the rest of `start_fanout` (the `_instance.yaml` write at lines 90-94, `ensure_phase_label`, `cas_push`, `emit_run_fanout`) unchanged.

- [ ] **Step 4: Run the equivalence test + the existing sub-pipeline/fanout suites**

Run: `pytest tests/test_multiphase_subpipeline.py tests/test_subpipeline.py tests/test_fanout_e2e.py tests/test_gate_data.py -q`
Expected: PASS (all). The refactor is behavior-preserving.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: PASS (404+ passed — baseline 403 plus the new tests).

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_multiphase_subpipeline.py
git commit -m "refactor(engine): extract seed_branch helper from start_fanout (no behavior change)"
```

---

### Task 3: Make the multi-phase fanout emit sub-pipeline-aware

Replace the flat-only branch loop in `seed_and_dispatch_phase`'s `kind=="fanout"` arm (`next.py:171-186`) with a `seed_branch(..., phase=phase_id)` loop, so a sub-pipeline branch nested in a multi-phase fanout phase seeds its cursor + first sub-state and emits `substate`.

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (the `if kind == "fanout":` block inside `seed_and_dispatch_phase`)
- Test: `tests/test_multiphase_subpipeline.py`

**Interfaces:**
- Consumes: `seed_branch(b, fanout_id, phase)` from Task 2.
- Produces: entering the `review` fanout phase emits `run-fanout` with branch `B` carrying `"substate": "draft"` and writes `review.B.yaml` (cursor, `sub_state=draft`) + `review.B.draft.yaml`; branch `A` emits without `substate` and writes `review.A.yaml`.

- [ ] **Step 1: Write the failing emit test**

Add to `tests/test_multiphase_subpipeline.py`:

```python
def test_advance_phase_into_fanout_seeds_subpipeline(tmp_path, engine_env):
    # Drive the multi-phase advance-phase entry directly into the fanout phase.
    out, err, rc = run_engine("next.py", tmp_path / "d", "pr-1", str(PROTO),
                              "advance-phase", "abc123", env=engine_env, phase="review")
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-fanout"
    assert action.get("phase") == "review"

    b = next(x for x in action["branches"] if x["id"] == "B")
    assert b["substate"] == "draft"          # sub-pipeline branch now dispatches its first sub-state
    assert b["workflow"] == "draft-agent"
    a = next(x for x in action["branches"] if x["id"] == "A")
    assert "substate" not in a               # flat branch still flat

    work = _state_dir(tmp_path, engine_env)
    # Phase-qualified paths: <phase>.<branch>[.<substate>].yaml
    cursor = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.yaml")
    assert cursor["sub_state"] == "draft" and cursor["state"] == "review"
    sub = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.draft.yaml")
    assert sub["state"] == "review" and sub["iteration"] == 1
    flat = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.A.yaml")
    assert flat["head_sha"] == "abc123"      # multi-phase flat carries head_sha
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/test_multiphase_subpipeline.py::test_advance_phase_into_fanout_seeds_subpipeline -v`
Expected: FAIL — `B` has no `"substate"` key (KeyError / `assert`), and `review.B.draft.yaml` does not exist (current code writes only a flat `review.B.yaml`).

- [ ] **Step 3: Rewrite the fanout arm to use `seed_branch`**

In `next.py`, inside `seed_and_dispatch_phase`, replace the `if kind == "fanout":` block (`next.py:171-186`) with:

```python
    if kind == "fanout":
        branches_config = phase_state.get("branches", [])
        # Seed each branch (flat OR sub-pipeline) under the phase-qualified path
        # via the shared helper — same logic the single-phase start_fanout uses.
        branches = [seed_branch(b, phase_id, phase=phase_id) for b in branches_config]
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter fan-out phase {phase_id} ({command})")
        print(json.dumps({"action": "run-fanout", "iteration": 1, "feedback": "",
                          "reason": f"phase:{phase_id}", "phase": phase_id, "branches": branches}))
```

(`seed_branch` passes `fanout_id=phase_id`; in a multi-phase protocol the fanout state id **is** the phase id, so the seeded `state` field matches the prior flat-file `state: phase_id`.)

- [ ] **Step 4: Run the emit test to confirm it passes**

Run: `pytest tests/test_multiphase_subpipeline.py::test_advance_phase_into_fanout_seeds_subpipeline -v`
Expected: PASS.

- [ ] **Step 5: Run the multi-phase + full suites**

Run: `pytest tests/test_multiphase.py tests/test_phase_relay.py tests/test_multiphase_subpipeline.py -q && pytest tests/ -q`
Expected: PASS (all). `pipeline-mini` (multi-phase, flat-only fanout) still passes — its flat branches route through `seed_branch`'s flat arm identically.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_multiphase_subpipeline.py
git commit -m "fix(engine): multi-phase fanout emit seeds sub-pipeline branches via seed_branch"
```

---

### Task 4: Thread the `phase` qualifier through the `/answer` gate path

`_find_open_gate_branch` and `do_answer` build `branch+substate` state-file paths without `phase=`, so in a multi-phase run they look in `<branch>.<sub>.yaml` while the run wrote `<phase>.<branch>.<sub>.yaml`. Add a single derivation `_gate_phase(proto)` and pass it through every `state_file` / `output_artifact_path` / `dispatch_continue` call. Single-phase stays `None` (unchanged).

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (`_find_open_gate_branch`, `do_answer`; add `_gate_phase`)
- Test: `tests/test_multiphase_subpipeline.py`

**Interfaces:**
- Produces: `_gate_phase(proto) -> str | None` — `lib._fanout_state(proto)["id"]` when `lib.is_multiphase(proto)`, else `None`.
- Consumes: `lib.is_multiphase`, `lib._fanout_state`, `lib.dispatch_continue(pid, instance, branch, substate, phase="")` (already forwards `phase`).

- [ ] **Step 1: Write the failing `/answer` test**

Add to `tests/test_multiphase_subpipeline.py` (drive: enter fanout → advance `B.draft` into the `clarify` gate → `/answer`, all at phase-qualified paths; note `/answer` carries **no** `phase` env — `do_answer` must derive it):

```python
def _advance_substate(tmp_path, engine_env, instance, branch, substate, sha="abc123", n=0):
    v = tmp_path / f"v-{branch}-{substate}-{n}.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / f"ev-{branch}-{substate}-{n}.json"; ev.write_text("{}")
    e = dict(engine_env)
    e.update(BRANCH=branch, SUBSTATE=substate, PHASE="review", PR_HEAD_SHA=sha, AGENT_RUN_ID="r")
    return run_engine("advance.py", tmp_path / f"adv-{branch}-{substate}-{n}", instance,
                      str(PROTO), v, ev, env=e)


def test_answer_finds_nested_gate_in_multiphase(tmp_path, engine_env):
    # Enter the fanout phase (seeds B.draft).
    run_engine("next.py", tmp_path / "d0", "pr-1", str(PROTO), "advance-phase", "abc123",
               env=engine_env, phase="review")
    # Advance B.draft → opens the clarify gate at review.B.clarify.yaml.
    out, err, rc = _advance_substate(tmp_path, engine_env, "pr-1", "B", "draft")
    assert rc == 0, err
    work = _state_dir(tmp_path, engine_env, suffix="-g")
    gate = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.clarify.yaml")
    assert gate["gates"]["state"] == "open"
    qid = gate["gates"]["questions"][0]["id"]

    # /answer with NO phase env — do_answer must derive phase="review" itself.
    e = dict(engine_env)
    e["ANSWER_BODY"] = f"/answer {qid}: postgres"
    e["ANSWER_ACTOR"] = "alice"
    e["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("next.py", tmp_path / "d1", "pr-1", str(PROTO), "answer", env=e)
    assert rc == 0, err

    work = _state_dir(tmp_path, engine_env, suffix="-a")
    gate = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.clarify.yaml")
    assert gate["gates"]["state"] == "answered"
    cursor = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.yaml")
    assert cursor["sub_state"] == "finalize"          # advanced to the next sub-state
    answers = json.loads((work / "multiphase-subpipeline/pr-1/review.B.clarify.answers.json").read_text())
    assert answers["answers"][qid] == "postgres"
    # The continue re-dispatch must carry the phase so the resumed leg uses qualified paths.
    assert "client_payload[phase]=review" in err
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/test_multiphase_subpipeline.py::test_answer_finds_nested_gate_in_multiphase -v`
Expected: FAIL — `do_answer` builds `pr-1/B.clarify.yaml` (unqualified) and reports "No open question gate" (its `_find_open_gate_branch` reads the unqualified cursor, which doesn't exist), so the gate never reaches `answered` and the `phase=review` payload is absent.

- [ ] **Step 3: Add `_gate_phase` and thread it through both functions**

In `next.py`, add `_gate_phase` just above `_find_open_gate_branch` (near line 390):

```python
def _gate_phase(proto):
    """Phase qualifier for sub-pipeline gate/cursor state files: the fanout phase
    id in a multi-phase protocol, else None (single-phase → unqualified paths)."""
    if lib.is_multiphase(proto):
        fo = lib._fanout_state(proto)
        return fo["id"] if fo else None
    return None
```

In `_find_open_gate_branch`, derive the phase once and pass it to both `state_file` calls. Change:

```python
    fo = lib._fanout_state(proto)
    if not fo:
        return None, None
    for b in fo.get("branches", []):
        if want_branch and b["id"] != want_branch:
            continue
        cf = lib.state_file(DIR, PID, INSTANCE, branch=b["id"])
```
to:
```python
    fo = lib._fanout_state(proto)
    if not fo:
        return None, None
    ph = _gate_phase(proto)
    for b in fo.get("branches", []):
        if want_branch and b["id"] != want_branch:
            continue
        cf = lib.state_file(DIR, PID, INSTANCE, branch=b["id"], phase=ph)
```
and the gate lookup inside the loop, from:
```python
                gsf = lib.state_file(DIR, PID, INSTANCE, branch=b["id"], substate=sub)
```
to:
```python
                gsf = lib.state_file(DIR, PID, INSTANCE, branch=b["id"], substate=sub, phase=ph)
```

In `do_answer`, compute the phase once right after `branch, gate = _find_open_gate_branch(...)` succeeds (after the `if not branch:` guard, before line 446):

```python
    ph = _gate_phase(proto_data)
```

Then add `phase=ph` to each of these calls in `do_answer`:
- `gsf = lib.state_file(DIR, PID, INSTANCE, branch=branch, substate=gate)` → add `, phase=ph`
- `apath = lib.output_artifact_path(DIR, PID, INSTANCE, branch=branch, substate=gate, kind="answers")` → add `, phase=ph`
- `cf = lib.state_file(DIR, PID, INSTANCE, branch=branch)` → add `, phase=ph`
- `nsf = lib.state_file(DIR, PID, INSTANCE, branch=branch, substate=nxt_sub)` → add `, phase=ph`
- `lib.dispatch_continue(PID, INSTANCE, branch, nxt_sub)` → `lib.dispatch_continue(PID, INSTANCE, branch, nxt_sub, phase=ph or "")`

Leave the `life = lib._fanout_state(proto_data)["id"]` line unchanged — in a multi-phase protocol that id already equals the phase.

- [ ] **Step 4: Run the `/answer` test to confirm it passes**

Run: `pytest tests/test_multiphase_subpipeline.py::test_answer_finds_nested_gate_in_multiphase -v`
Expected: PASS.

- [ ] **Step 5: Run the gate + full suites (single-phase `/answer` must still pass)**

Run: `pytest tests/test_gate_data.py tests/test_subpipeline.py tests/test_multiphase_subpipeline.py -q && pytest tests/ -q`
Expected: PASS (all). `subpipeline-mini`'s `/answer` tests still pass because `_gate_phase` returns `None` for single-phase, preserving unqualified paths.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_multiphase_subpipeline.py
git commit -m "fix(engine): phase-qualify the /answer gate path for multi-phase sub-pipelines"
```

---

### Task 5: End-to-end walk (dispatch → answer → finalize → join)

One integration test that walks the full broken cell from entering the fanout phase to the leg completing and firing the join, proving all three fixes compose. No production code changes — if this fails, the fix is incomplete.

**Files:**
- Test: `tests/test_multiphase_subpipeline.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4. Uses the same `_advance_substate` helper added in Task 4.

- [ ] **Step 1: Write the end-to-end test**

Add to `tests/test_multiphase_subpipeline.py`:

```python
def test_full_subpipeline_leg_walk_to_join(tmp_path, engine_env):
    # Enter fanout (seeds B.draft + flat A).
    run_engine("next.py", tmp_path / "d0", "pr-1", str(PROTO), "advance-phase", "abc123",
               env=engine_env, phase="review")
    # Finish flat leg A.
    out, err, rc = _advance_substate(tmp_path, engine_env, "pr-1", "A", "", n=1)  # flat: no substate
    # NOTE: flat branches advance with BRANCH set + SUBSTATE empty.
    assert rc == 0, err

    # B: draft → opens clarify gate.
    assert _advance_substate(tmp_path, engine_env, "pr-1", "B", "draft", n=2)[2] == 0
    work = _state_dir(tmp_path, engine_env, suffix="-1")
    qid = read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.clarify.yaml"
                          )["gates"]["questions"][0]["id"]

    # Answer the gate → advances cursor to finalize.
    e = dict(engine_env, ANSWER_BODY=f"/answer {qid}: pg", ANSWER_ACTOR="al", PR_HEAD_SHA="abc123")
    assert run_engine("next.py", tmp_path / "d1", "pr-1", str(PROTO), "answer", env=e)[2] == 0

    # Finish B.finalize → leg done.
    assert _advance_substate(tmp_path, engine_env, "pr-1", "B", "finalize", n=3)[2] == 0
    work = _state_dir(tmp_path, engine_env, suffix="-2")
    assert read_state_yaml(work / "multiphase-subpipeline/pr-1/review.B.yaml")["state"] == "done"

    # Join: both legs done → instance joins.
    ej = dict(engine_env, PR_HEAD_SHA="abc123")
    assert run_engine("join.py", tmp_path / "j", "pr-1", str(PROTO), env=ej)[2] == 0
    work = _state_dir(tmp_path, engine_env, suffix="-3")
    assert read_state_yaml(work / "multiphase-subpipeline/pr-1/_instance.yaml").get("joined") is True
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_multiphase_subpipeline.py::test_full_subpipeline_leg_walk_to_join -v`
Expected: PASS. If the flat-leg advance step (`SUBSTATE=""`) errors, check `advance.py`'s flat-branch handling under `PHASE` set — the durable side already threads phase (`advance.py:30`), so a failure here indicates a path-derivation mismatch to fix before this task closes. (Do not modify `advance.py` unless this test proves it necessary; the spec scopes the fix to `next.py`.)

- [ ] **Step 3: Run the full suite**

Run: `pytest tests/ -q`
Expected: PASS (all). Final count ≈ 403 baseline + ~6 new tests.

- [ ] **Step 4: Commit**

```bash
git add tests/test_multiphase_subpipeline.py
git commit -m "test: end-to-end multi-phase sub-pipeline leg walk to join"
```

---

## Self-Review

**1. Spec coverage**

- Spec defect #1 (emit omits sub-pipeline) → Task 3. ✓
- Spec defect #2 (`/answer` phase-blind) → Task 4. ✓
- Spec "extract shared `seed_branch`" → Task 2. ✓
- Spec "derive phase via `_fanout_state`, centralize in `_gate_phase`" → Task 4 Step 3. ✓
- Spec "thread phase to `dispatch_continue`" → Task 4 Step 3 (last bullet). ✓
- Spec verification fixture (multi-phase × sub-pipeline, flat + sub-pipeline branch, agent + gate sub-states) → Task 1. ✓
- Spec tests (a) emit seeds + `substate` → Task 3; (b) `/answer` locates + advances → Task 4; (c) leg done → join → Task 5. ✓
- Spec "existing fixtures pass unchanged" → Task 2 Step 1-2 (equivalence test) + every task's full-suite run. ✓
- Spec non-goal "no changes outside next.py" → honored; Task 5 Step 2 explicitly defers any `advance.py` change back to evidence. ✓

**2. Placeholder scan** — no TBD/TODO; every code step shows complete code; commands have expected output. ✓

**3. Type consistency** — `seed_branch(b, fanout_id, phase=None)` is defined in Task 2 and called as `seed_branch(b, fstate)` (Task 2) and `seed_branch(b, phase_id, phase=phase_id)` (Task 3) — signature matches. `_gate_phase(proto)` defined and called consistently in Task 4. `dispatch_continue(..., phase=ph or "")` matches the existing `lib.dispatch_continue(pid, instance, branch, substate, phase="")` signature (verified in lib.py:1009). Phase-qualified paths `review.B.yaml` / `review.B.draft.yaml` / `review.B.clarify.yaml` match `lib.state_file`'s documented `<phase>.<branch>[.<substate>].yaml` scheme. ✓

---

## Execution Notes

- The one residual unknown is the flat-branch advance under `PHASE` set (Task 5 Step 1, the `A` leg with empty `SUBSTATE`). `advance.py` already threads `phase` on the write side, so this is expected to work; Task 5 Step 2 calls out how to recognize and where to look if it doesn't, without pre-emptively widening scope.
- Keep commits per-task; each task leaves the suite green.
