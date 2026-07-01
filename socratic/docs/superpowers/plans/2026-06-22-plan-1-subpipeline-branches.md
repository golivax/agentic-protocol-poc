# Plan 1 — Sub-pipeline Branches (scope unification)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a fanout branch be a *linear sub-pipeline* of agent states that the engine sequences with its own cursor, so a leg can be `draft → finalize` (two agents) and the join still waits for the whole leg.

**Architecture:** Add a third scope coordinate `SUBSTATE` alongside the existing `PHASE`/`BRANCH`. A sub-pipeline branch's `review.<branch>.yaml` file becomes a *cursor* (`sub_state` + leg `state`); each sub-state gets its own `review.<branch>.<substate>.yaml`. The existing per-phase advance loop (`advance.py:364-397`) is generalised to run at branch scope. Flat single-agent branches are detected by the absence of a `states:` array and keep today's byte-identical path.

**Tech Stack:** Python 3 + PyYAML (runtime), pytest (dev). No new dependencies.

## Global Constraints

- Runtime deps limited to **Python 3 + PyYAML** — no new imports in `.github/agent-factory/`. (pytest is dev-only.)
- Every new `protocol.json` field is **optional**; the single-agent path and the current `code-review` pipeline stay byte-identical. Existing suite (`pytest tests/ -q`) must stay green.
- Checks ABI unchanged: `<check> <evidence.json> <diff.txt> <changed-files.txt>` → one JSON line, **always exit 0**.
- State advances only by fast-forward `lib.cas_push`. Never force-push `agentic-state`.
- Agent-derived strings are passed to shell via `env:`, never interpolated into `run:`.
- A "branch" is a parallel agent *leg*, not a git branch. Per-leg state lives under `<pid>/<instance>/`.

---

### Task 1: `state_file` learns the `substate` coordinate

**Files:**
- Modify: `.github/agent-factory/engine/lib.py:39-53` (`state_file`)
- Test: `tests/test_subpipeline.py` (create)

**Interfaces:**
- Produces: `lib.state_file(d, pid, instance, branch=None, phase=None, substate=None)` →
  - `branch` + `substate` (no phase) → `<d>/<pid>/<instance>/<branch>.<substate>.yaml`
  - `phase` + `branch` + `substate` → `<d>/<pid>/<instance>/<phase>.<branch>.<substate>.yaml`
  - all existing arg combinations unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subpipeline.py
from conftest import ENGINE  # noqa: F401  (ensures sys.path includes tests/)
import sys, importlib
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")


def test_state_file_substate_branch_only():
    p = lib.state_file("/s", "rev", "pr-1", branch="B", substate="draft")
    assert p == "/s/rev/pr-1/B.draft.yaml"


def test_state_file_substate_with_phase():
    p = lib.state_file("/s", "rev", "pr-1", branch="B", phase="review", substate="draft")
    assert p == "/s/rev/pr-1/review.B.draft.yaml"


def test_state_file_existing_shapes_unchanged():
    assert lib.state_file("/s", "rev", "pr-1") == "/s/rev/pr-1.yaml"
    assert lib.state_file("/s", "rev", "pr-1", branch="B") == "/s/rev/pr-1/B.yaml"
    assert lib.state_file("/s", "rev", "pr-1", phase="review") == "/s/rev/pr-1/review.yaml"
    assert lib.state_file("/s", "rev", "pr-1", branch="B", phase="review") == "/s/rev/pr-1/review.B.yaml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subpipeline.py -k state_file -v`
Expected: FAIL — `state_file() got an unexpected keyword argument 'substate'`.

- [ ] **Step 3: Implement minimal change**

Replace `state_file` body in `lib.py`:

```python
def state_file(d, pid, instance, branch=None, phase=None, substate=None):
    """
    state_file <dir> <protocol-id> <instance-key> [branch] [phase] [substate]
      no branch, no phase            → single-agent     <dir>/<pid>/<instance>.yaml
      branch, no phase               → fan-out leg       <dir>/<pid>/<instance>/<branch>.yaml
      phase, no branch               → multi-phase agent <dir>/<pid>/<instance>/<phase>.yaml
      phase + branch                 → fan-out leg       <dir>/<pid>/<instance>/<phase>.<branch>.yaml
      branch + substate              → sub-pipeline step <dir>/<pid>/<instance>/<branch>.<substate>.yaml
      phase + branch + substate      → sub-pipeline step <dir>/<pid>/<instance>/<phase>.<branch>.<substate>.yaml
    The branch CURSOR file is the (phase+)branch path WITHOUT substate; a
    sub-pipeline branch stores `sub_state` there and the per-step state in the
    substate path.
    """
    base = f"{d}/{pid}/{instance}"
    if phase and branch and substate:
        return f"{base}/{phase}.{branch}.{substate}.yaml"
    if phase and branch:
        return f"{base}/{phase}.{branch}.yaml"
    if phase:
        return f"{base}/{phase}.yaml"
    if branch and substate:
        return f"{base}/{branch}.{substate}.yaml"
    if branch:
        return f"{base}/{branch}.yaml"
    return f"{base}.yaml"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_subpipeline.py -k state_file -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite (regression guard)**

Run: `pytest tests/ -q`
Expected: all existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_subpipeline.py
git commit -m "feat(engine): state_file gains substate coordinate"
```

---

### Task 2: Branch sub-pipeline introspection helpers

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add helpers after `state_by_id`)
- Test: `tests/test_subpipeline.py`

**Interfaces:**
- Produces:
  - `lib.branch_config(protocol, branch) -> dict|None` — the branch entry from the fanout state.
  - `lib.is_subpipeline_branch(branch_cfg) -> bool` — True iff the entry has a non-empty `states` list.
  - `lib.branch_substates(protocol, branch) -> list[dict]` — ordered sub-state dicts (`[]` for a flat branch).
  - `lib.next_substate_id(protocol, branch, substate) -> str|None` — id of the sub-state after `substate`, or None if it is the last.

- [ ] **Step 1: Write the failing test**

```python
SUBPIPE_PROTO = {
    "name": "rev",
    "states": [
        {"id": "review", "kind": "fanout", "branches": [
            {"id": "A", "workflow": "a-agent", "max_iterations": 2},
            {"id": "B", "states": [
                {"id": "draft", "kind": "agent", "workflow": "draft-agent", "max_iterations": 2},
                {"id": "finalize", "kind": "agent", "workflow": "final-agent", "max_iterations": 2},
            ]},
        ]},
        {"id": "join", "kind": "join", "of": "review", "next": "done"},
    ],
}


def test_branch_config():
    assert lib.branch_config(SUBPIPE_PROTO, "A")["workflow"] == "a-agent"
    assert lib.branch_config(SUBPIPE_PROTO, "B")["id"] == "B"
    assert lib.branch_config(SUBPIPE_PROTO, "missing") is None


def test_is_subpipeline_branch():
    assert lib.is_subpipeline_branch(lib.branch_config(SUBPIPE_PROTO, "B")) is True
    assert lib.is_subpipeline_branch(lib.branch_config(SUBPIPE_PROTO, "A")) is False


def test_branch_substates():
    ids = [s["id"] for s in lib.branch_substates(SUBPIPE_PROTO, "B")]
    assert ids == ["draft", "finalize"]
    assert lib.branch_substates(SUBPIPE_PROTO, "A") == []


def test_next_substate_id():
    assert lib.next_substate_id(SUBPIPE_PROTO, "B", "draft") == "finalize"
    assert lib.next_substate_id(SUBPIPE_PROTO, "B", "finalize") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subpipeline.py -k "branch_config or subpipeline_branch or branch_substates or next_substate" -v`
Expected: FAIL — `module 'lib' has no attribute 'branch_config'`.

- [ ] **Step 3: Implement the helpers**

Add to `lib.py` right after `state_by_id` (around line 61):

```python
def _fanout_state(protocol):
    for s in protocol.get("states", []):
        if s.get("kind") == "fanout":
            return s
    return None


def branch_config(protocol, branch):
    """The branch entry dict from the protocol's fanout state, or None."""
    fo = _fanout_state(protocol)
    if not fo:
        return None
    for b in fo.get("branches", []):
        if b.get("id") == branch:
            return b
    return None


def is_subpipeline_branch(branch_cfg):
    """True iff the branch entry is a linear sub-pipeline (has `states`)."""
    return bool(branch_cfg) and bool(branch_cfg.get("states"))


def branch_substates(protocol, branch):
    """Ordered list of sub-state dicts for a sub-pipeline branch ([] if flat)."""
    cfg = branch_config(protocol, branch)
    if not is_subpipeline_branch(cfg):
        return []
    return list(cfg.get("states", []))


def next_substate_id(protocol, branch, substate):
    """Id of the sub-state following `substate`, or None if it is the last."""
    subs = branch_substates(protocol, branch)
    ids = [s["id"] for s in subs]
    if substate in ids:
        i = ids.index(substate)
        if i + 1 < len(ids):
            return ids[i + 1]
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_subpipeline.py -k "branch_config or subpipeline_branch or branch_substates or next_substate" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_subpipeline.py
git commit -m "feat(engine): branch sub-pipeline introspection helpers"
```

---

### Task 3: `resolve_agent_unit` resolves a sub-state

**Files:**
- Modify: `.github/agent-factory/engine/lib.py:64-99` (`resolve_agent_unit`)
- Test: `tests/test_subpipeline.py`

**Interfaces:**
- Produces: `lib.resolve_agent_unit(protocol, phase="", branch="", substate="")`. When `substate` is set with a sub-pipeline `branch`, returns `{"agent_state": <substate-id>, "max_iterations": <substate max>, "life_state": <fanout-or-phase id>}`. All existing call shapes unchanged.

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_agent_unit_substate():
    u = lib.resolve_agent_unit(SUBPIPE_PROTO, phase="review", branch="B", substate="finalize")
    assert u["agent_state"] == "finalize"
    assert u["max_iterations"] == 2
    assert u["life_state"] == "review"


def test_resolve_agent_unit_flat_branch_unchanged():
    u = lib.resolve_agent_unit(SUBPIPE_PROTO, phase="review", branch="A")
    assert u["agent_state"] == "A"
    assert u["life_state"] == "review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subpipeline.py -k resolve_agent_unit_substate -v`
Expected: FAIL — `resolve_agent_unit() got an unexpected keyword argument 'substate'`.

- [ ] **Step 3: Implement**

Change the signature and add a substate branch at the top of the `if phase:` fanout block. Replace `resolve_agent_unit` with:

```python
def resolve_agent_unit(protocol, phase="", branch="", substate=""):
    """Resolve the agent unit for a leg: its agent_state id, max_iterations, and
    life_state. Adds a SUBSTATE rung above BRANCH: a sub-pipeline branch resolves
    to its current sub-state. Mirrors the PHASE → BRANCH → single-agent ladder."""
    if phase:
        st = state_by_id(protocol, phase)
        if not st:
            raise ValueError(f"no phase '{phase}' in protocol")
        if st.get("kind") == "fanout":
            if not branch:
                raise ValueError(f"PHASE='{phase}' is a fanout phase but BRANCH is empty")
            for b in st.get("branches", []):
                if b["id"] == branch:
                    if substate:
                        for s in b.get("states", []):
                            if s["id"] == substate:
                                return {"agent_state": substate,
                                        "max_iterations": s.get("max_iterations"),
                                        "life_state": phase}
                        raise ValueError(f"no sub-state '{substate}' in branch '{branch}'")
                    return {"agent_state": branch, "max_iterations": b.get("max_iterations"), "life_state": phase}
            raise ValueError(f"no branch '{branch}' in phase '{phase}'")
        return {"agent_state": phase, "max_iterations": st.get("max_iterations"), "life_state": phase}
    if branch:
        fanout_id = None
        for st in protocol.get("states", []):
            if st.get("kind") == "fanout":
                fanout_id = st["id"]
                for b in st.get("branches", []):
                    if b["id"] == branch:
                        if substate:
                            for s in b.get("states", []):
                                if s["id"] == substate:
                                    return {"agent_state": substate,
                                            "max_iterations": s.get("max_iterations"),
                                            "life_state": fanout_id}
                            raise ValueError(f"no sub-state '{substate}' in branch '{branch}'")
                        return {"agent_state": b["id"], "max_iterations": b.get("max_iterations"), "life_state": fanout_id}
                break
        raise ValueError(f"no branch '{branch}' in protocol")
    for st in protocol.get("states", []):
        if st.get("kind") == "agent":
            return {"agent_state": st["id"], "max_iterations": st.get("max_iterations"), "life_state": st["id"]}
    raise ValueError("protocol has no agent state")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_subpipeline.py -k resolve_agent_unit -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green (this function is on the regression-guarded path).

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_subpipeline.py
git commit -m "feat(engine): resolve_agent_unit resolves a branch sub-state"
```

---

### Task 4: The `subpipeline-mini` test fixture

**Files:**
- Create: `tests/fixtures/subpipeline-mini/protocol.json`
- Create: `tests/fixtures/subpipeline-mini/draft.evidence.schema.json`
- Create: `tests/fixtures/subpipeline-mini/finalize.evidence.schema.json`
- Create: `tests/fixtures/subpipeline-mini/a.evidence.schema.json`
- Create: `tests/fixtures/subpipeline-mini/checks/always-pass.py`
- Test: `tests/test_subpipeline.py`

**Interfaces:**
- Produces: a minimal two-branch fanout protocol — `A` (flat single agent) and `B` (sub-pipeline `draft → finalize`) — joined. Used by Tasks 5-8. The `always-pass` check makes every leg pass immediately so sequencing (not check logic) is under test.

- [ ] **Step 1: Create `protocol.json`**

```json
{
  "name": "subpipeline-mini",
  "version": "0.1.0",
  "triggers": [
    { "on": "pull_request", "actions": ["opened"], "command": "start" }
  ],
  "states": [
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
              "id": "finalize",
              "kind": "agent",
              "workflow": "finalize-agent",
              "evidence": "finalize.evidence.schema.json",
              "max_iterations": 2,
              "checks": [{ "run": "always-pass", "on_fail": "iterate" }]
            }
          ]
        }
      ],
      "next": "join"
    },
    { "id": "join", "kind": "join", "of": "review", "next": "done" }
  ]
}
```

- [ ] **Step 2: Create the three evidence schemas (identical permissive stub)**

Write the same content to `a.evidence.schema.json`, `draft.evidence.schema.json`, and `finalize.evidence.schema.json`:

```json
{ "$schema": "http://json-schema.org/draft-07/schema#", "type": "object" }
```

- [ ] **Step 3: Create the `always-pass` check**

```python
# tests/fixtures/subpipeline-mini/checks/always-pass.py
#!/usr/bin/env python3
import json
print(json.dumps({"check": "always-pass", "pass": True, "feedback": ""}))
```

- [ ] **Step 4: Make the check executable**

Run: `chmod +x tests/fixtures/subpipeline-mini/checks/always-pass.py`
Expected: no output.

- [ ] **Step 5: Write a fixture smoke test**

```python
import json, pathlib
from conftest import FIXTURES


def test_subpipeline_mini_loads():
    proto = json.loads((FIXTURES / "subpipeline-mini/protocol.json").read_text())
    assert proto["name"] == "subpipeline-mini"
    b = next(x for x in proto["states"][0]["branches"] if x["id"] == "B")
    assert [s["id"] for s in b["states"]] == ["draft", "finalize"]
    chk = FIXTURES / "subpipeline-mini/checks/always-pass.py"
    assert chk.stat().st_mode & 0o111  # executable
```

- [ ] **Step 6: Run the smoke test**

Run: `pytest tests/test_subpipeline.py -k subpipeline_mini_loads -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/subpipeline-mini tests/test_subpipeline.py
git commit -m "test(engine): subpipeline-mini fixture (A flat || B draft->finalize)"
```

---

### Task 5: `next.py` seeds + dispatches the first sub-state on fanout start

**Files:**
- Modify: `.github/agent-factory/engine/next.py:49-93` (`start_fanout`)
- Modify: `tests/conftest.py` (`run_engine` accepts `phase`/`substate`)
- Test: `tests/test_subpipeline.py`

**Interfaces:**
- Consumes: `lib.is_subpipeline_branch`, `lib.branch_substates`, `lib.state_file(..., substate=...)`.
- Produces: on `start`, for a sub-pipeline branch, `start_fanout` seeds the branch **cursor** file `{sub_state: <first>, state: review, ...}` AND the first sub-state file, and the emitted `run-fanout` branch entry carries `"substate": "<first>"` and the first sub-state's `workflow`. Flat branches emit unchanged (no `substate` key).

- [ ] **Step 1: Extend the test harness**

In `tests/conftest.py`, replace `run_engine` body's env wiring to also forward `phase`/`substate`:

```python
def run_engine(script, *args, env=None, branch=None, phase=None, substate=None):
    e = dict(env or os.environ)
    if branch is not None:
        e["BRANCH"] = branch
    if phase is not None:
        e["PHASE"] = phase
    if substate is not None:
        e["SUBSTATE"] = substate
    r = subprocess.run(
        ["python3", str(ENGINE / script), *map(str, args)],
        text=True, capture_output=True, env=e,
    )
    return r.stdout, r.stderr, r.returncode
```

- [ ] **Step 2: Write the failing test**

```python
import json, shutil, subprocess
from conftest import run_engine, read_state_yaml, FIXTURES


def _state_dir(tmp_path, engine_env):
    """Clone the fake origin so we can read pushed state files back."""
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    return work


def test_start_seeds_subpipeline_first_substate(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    workdir = tmp_path / "dir"
    out, err, rc = run_engine("next.py", workdir, "pr-1", proto, "start", "abc123", env=engine_env)
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-fanout"
    b = next(x for x in action["branches"] if x["id"] == "B")
    assert b["substate"] == "draft"
    assert b["workflow"] == "draft-agent"
    a = next(x for x in action["branches"] if x["id"] == "A")
    assert "substate" not in a  # flat branch unchanged

    # State files: branch cursor carries sub_state; sub-state file seeded.
    work = _state_dir(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "draft"
    assert cursor["state"] == "review"
    sub = read_state_yaml(work / "subpipeline-mini/pr-1/B.draft.yaml")
    assert sub["state"] == "review" and sub["iteration"] == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_subpipeline.py -k start_seeds_subpipeline -v`
Expected: FAIL — `KeyError: 'substate'` (current `start_fanout` emits flat entries only).

- [ ] **Step 4: Implement `start_fanout` sub-pipeline seeding**

Replace the body of `start_fanout` (next.py:49) so each branch is seeded per its kind:

```python
def start_fanout():
    fstate = None
    branches_config = []
    for s in proto_data.get("states", []):
        if s.get("kind") == "fanout":
            fstate = s["id"]
            branches_config = s.get("branches", [])
            break

    branches = []
    for b in branches_config:
        bid = b["id"]
        if lib.is_subpipeline_branch(b):
            first = b["states"][0]
            # Branch CURSOR: sub_state + leg life-state (the fanout id).
            cf = lib.state_file(DIR, PID, INSTANCE, bid)
            os.makedirs(os.path.dirname(cf), exist_ok=True)
            lib.dump_yaml(cf, {
                "protocol": PID, "instance": INSTANCE, "state": fstate,
                "sub_state": first["id"], "iteration": 1, "gates": {}, "history": [],
            })
            # First SUB-STATE file (the per-step iterate state).
            sf = lib.state_file(DIR, PID, INSTANCE, bid, substate=first["id"])
            lib.dump_yaml(sf, {
                "protocol": PID, "instance": INSTANCE, "state": fstate,
                "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": [],
            })
            branches.append({"id": bid, "workflow": first["workflow"],
                             "substate": first["id"], "iteration": 1, "feedback": ""})
        else:
            sf = lib.state_file(DIR, PID, INSTANCE, bid)
            os.makedirs(os.path.dirname(sf), exist_ok=True)
            lib.dump_yaml(sf, {
                "protocol": PID, "instance": INSTANCE, "state": fstate,
                "iteration": 1, "gates": {}, "history": [],
            })
            branches.append({"id": bid, "workflow": b["workflow"],
                             "iteration": 1, "feedback": ""})

    inf = lib.instance_file(DIR, PID, INSTANCE)
    os.makedirs(os.path.dirname(inf), exist_ok=True)
    lib.dump_yaml(inf, {
        "protocol": PID, "instance": INSTANCE, "head_sha": HEAD_SHA, "joined": False,
    })

    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, fstate)
    lib.cas_push(DIR, f"{PID}/{INSTANCE}: fan-out review ({COMMAND})")
    emit_run_fanout(branches)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_subpipeline.py -k start_seeds_subpipeline -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest tests/ -q`
Expected: green — flat-branch fixtures (`tests/fixtures/fanout-mini`) emit unchanged.

- [ ] **Step 7: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/conftest.py tests/test_subpipeline.py
git commit -m "feat(engine): seed + dispatch first sub-state of a sub-pipeline branch"
```

---

### Task 6: `advance.py` advances the branch cursor through sub-states

**Files:**
- Modify: `.github/agent-factory/engine/advance.py:209-211` (read `SUBSTATE`), `:242-244` (state-file path), `:316-407` (the `done` branch)
- Test: `tests/test_subpipeline.py`

**Interfaces:**
- Consumes: `SUBSTATE` env var; `lib.next_substate_id`, `lib.branch_substates`, `lib.resolve_agent_unit(..., substate=...)`.
- Produces: when `BRANCH` is a sub-pipeline leg and the current sub-state's checks pass:
  - **not last** → advance the branch cursor `sub_state` to the next sub-state, seed it, `repository_dispatch protocol-continue` with `branch` + `substate=<next>`; the sub-state's own file is marked `state: done`.
  - **last** → set the branch cursor `state: done` and `fire_join`.
  - on iterate → re-dispatch the **same** sub-state (payload carries `substate`).
  - on failed → branch cursor `state: failed`, `fire_join`.

- [ ] **Step 1: Write the failing test (draft → finalize advance)**

```python
import os


def _advance(tmp_path, engine_env, instance, branch, substate, proto, sha="abc123"):
    """Run advance.py for a leg with an all-pass verdict + empty evidence."""
    verdicts = tmp_path / f"verdicts-{branch}-{substate}.json"
    verdicts.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    evid = tmp_path / "evidence.json"
    evid.write_text("{}")
    e = dict(engine_env)
    e["BRANCH"] = branch
    e["SUBSTATE"] = substate
    e["PR_HEAD_SHA"] = sha
    e["AGENT_RUN_ID"] = "run-1"
    out, err, rc = run_engine("advance.py", tmp_path / "dir", instance, proto,
                              verdicts, evid, env=e)
    return out, err, rc


def test_advance_draft_moves_cursor_to_finalize(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    out, err, rc = _advance(tmp_path, engine_env, "pr-1", "B", "draft", proto)
    assert rc == 0, err

    work = _state_dir(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "finalize"
    assert cursor.get("state") == "review"   # leg still in flight
    fin = read_state_yaml(work / "subpipeline-mini/pr-1/B.finalize.yaml")
    assert fin["state"] == "review" and fin["iteration"] == 1


def test_advance_finalize_marks_leg_done(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    _advance(tmp_path, engine_env, "pr-1", "B", "draft", proto)
    out, err, rc = _advance(tmp_path, engine_env, "pr-1", "B", "finalize", proto)
    assert rc == 0, err
    work = _state_dir(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["state"] == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subpipeline.py -k "advance_draft or advance_finalize" -v`
Expected: FAIL — advance.py ignores `SUBSTATE`, writes `B.yaml` as the agent state and fires join after `draft`.

- [ ] **Step 3: Read `SUBSTATE` and route the state-file path**

In `advance.py` after line 210 (`phase = os.environ.get("PHASE", "")`) add:

```python
    substate = os.environ.get("SUBSTATE", "")
```

Pass it into `resolve_agent_unit` (replace the call at `:226`):

```python
        _unit = lib.resolve_agent_unit(proto, phase, branch, substate)
```

And the state-file resolution (replace `:242-244`):

```python
    sf = lib.state_file(dir_, pid, instance,
                        branch=(branch if branch else None),
                        phase=(phase if phase else None),
                        substate=(substate if substate else None))
```

- [ ] **Step 4: Add the sub-pipeline `done` handling**

In the `if process == "done":` block, **before** the existing `is_agent_phase` logic, insert a sub-pipeline branch path. Add near the top of the `done` block (after `state_data["state"] = "done"` is written to the sub-state file at `:319-321`):

```python
        # --- Sub-pipeline branch leg: advance the BRANCH CURSOR, not the phase. ---
        if branch and substate:
            cursor_sf = lib.state_file(dir_, pid, instance, branch=branch,
                                       phase=(phase if phase else None))
            nxt_sub = lib.next_substate_id(proto, branch, substate)
            # Mark this sub-state's own file done (already set above), then move on.
            lib.set_check_run(cr_name, sha, "completed", "success",
                              f"{substate} complete", "")
            cur = lib.load_yaml(cursor_sf) if os.path.isfile(cursor_sf) else {}
            if nxt_sub:
                cur["sub_state"] = nxt_sub
                cur["state"] = life_state         # leg stays in flight
                lib.dump_yaml(cursor_sf, cur)
                # Seed the next sub-state's per-step file.
                nsf = lib.state_file(dir_, pid, instance, branch=branch,
                                     phase=(phase if phase else None), substate=nxt_sub)
                lib.dump_yaml(nsf, {
                    "protocol": pid, "instance": instance, "state": life_state,
                    "iteration": 1, "gates": {}, "head_sha": sha, "history": [],
                })
                lib.cas_push(dir_, f"{instance}: branch {branch} {substate} done → {nxt_sub}")
                redispatch = [
                    f"repos/{github_repository}/dispatches",
                    "-f", "event_type=protocol-continue",
                    "-F", f"client_payload[protocol]={pid}",
                    "-F", f"client_payload[instance]={instance}",
                    "-F", f"client_payload[branch]={branch}",
                    "-F", f"client_payload[substate]={nxt_sub}",
                ]
                if phase:
                    redispatch += ["-F", f"client_payload[phase]={phase}"]
                gh_api(*redispatch)
            else:
                cur["state"] = "done"             # last sub-state → leg terminal
                lib.dump_yaml(cursor_sf, cur)
                update_status_comment(sf, inf, branch, pr, pid, instance, proto_path, dir_,
                                      "✅ done — published.", max_iter, github_repository)
                lib.cas_push(dir_, f"{instance}: branch {branch} {substate} done → leg done")
                fire_join(pid, instance, branch)
            return
```

> The `return` exits `main()` before the existing flat-leg / agent-phase logic runs, so those paths stay byte-identical for non-sub-pipeline legs.

- [ ] **Step 5: Make iterate + failed carry `substate`**

In the `elif process == "iterate":` re-dispatch list (`:430-438`) add, after the `branch` line:

```python
        if substate:
            redispatch += ["-F", f"client_payload[substate]={substate}"]
```

In the `else:  # failed` block, the existing `fire_join(pid, instance, branch)` already fires; but first mark the cursor failed for a sub-pipeline leg. After `state_data["state"] = "failed"` (`:445`) add:

```python
        if branch and substate:
            cursor_sf = lib.state_file(dir_, pid, instance, branch=branch,
                                       phase=(phase if phase else None))
            cur = lib.load_yaml(cursor_sf) if os.path.isfile(cursor_sf) else {}
            cur["state"] = "failed"
            lib.dump_yaml(cursor_sf, cur)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_subpipeline.py -k "advance_draft or advance_finalize" -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/ -q`
Expected: green — flat legs never set `substate`, so the new block is skipped.

- [ ] **Step 8: Commit**

```bash
git add .github/agent-factory/engine/advance.py tests/test_subpipeline.py
git commit -m "feat(engine): advance branch cursor through sub-pipeline sub-states"
```

---

### Task 7: `next.py` resumes (`continue`) within a sub-state

**Files:**
- Modify: `.github/agent-factory/engine/next.py:431-451` (unit resolution reads `SUBSTATE`), `:449-451` (state-file path)
- Test: `tests/test_subpipeline.py`

**Interfaces:**
- Consumes: `SUBSTATE` env var.
- Produces: a `continue` command with `BRANCH`+`SUBSTATE` set resumes the named sub-state (re-emits `run-agent` carrying `substate` at the current iteration with the last feedback), exactly like the flat-leg continue but scoped to the sub-state file.

- [ ] **Step 1: Write the failing test**

```python
def test_continue_resumes_substate(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    # Resume the draft sub-state explicitly.
    out, err, rc = run_engine("next.py", tmp_path / "dir", "pr-1", proto, "continue",
                              env=engine_env, branch="B", substate="draft")
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-agent"
    assert "phase" not in action
    assert action.get("substate") == "draft"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subpipeline.py -k continue_resumes_substate -v`
Expected: FAIL — `next.py` reads no `SUBSTATE`; the action has no `substate` and the state path resolves to `B.yaml` (the cursor), not `B.draft.yaml`.

- [ ] **Step 3: Read `SUBSTATE` and thread it through**

In `next.py`, the env read at `:25` already grabs `PHASE`; add after it:

```python
SUBSTATE = os.environ.get("SUBSTATE", "")
```

Replace the unit resolution call (`:432`) with:

```python
    _unit = lib.resolve_agent_unit(proto_data, PHASE, BRANCH, SUBSTATE)
```

Replace the `SF` path (`:449-451`) with:

```python
SF = lib.state_file(DIR, PID, INSTANCE,
                    branch=(BRANCH if BRANCH else None),
                    phase=(PHASE if PHASE else None),
                    substate=(SUBSTATE if SUBSTATE else None))
```

Replace `emit_run_agent` (`:467-471`) to also carry `substate`:

```python
def emit_run_agent(iteration, feedback, reason):
    action = {"action": "run-agent", "iteration": iteration, "feedback": feedback, "reason": reason}
    if PHASE:
        action["phase"] = PHASE
    if SUBSTATE:
        action["substate"] = SUBSTATE
    print(json.dumps(action))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_subpipeline.py -k continue_resumes_substate -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_subpipeline.py
git commit -m "feat(engine): next.py resumes a sub-pipeline sub-state on continue"
```

---

### Task 8: End-to-end — `join` waits for the whole sub-pipeline leg

**Files:**
- Modify: `.github/agent-factory/engine/join.py` (no logic change expected; add a guard + comment confirming it reads the branch cursor)
- Test: `tests/test_subpipeline.py`

**Interfaces:**
- Consumes: branch cursor files `B.yaml` / `A.yaml` carrying `state: done|failed`.
- Produces: the join evaluator marks the instance joined + the aggregate check `success` only after **both** `A` (flat) and `B`'s last sub-state (`finalize`) are `done`. After `draft` done but `finalize` pending, the leg is NOT terminal.

- [ ] **Step 1: Write the failing/￼regression test**

```python
def _run_join(tmp_path, engine_env, instance, proto, sha="abc123"):
    e = dict(engine_env)
    e["PR_HEAD_SHA"] = sha
    return run_engine("join.py", tmp_path / "dir", instance, proto, env=e)


def test_join_waits_for_subpipeline_then_joins(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    # Finish flat leg A.
    _advance(tmp_path, engine_env, "pr-1", "A", None, proto) if False else None
    # A is flat: advance with substate=None path. Use a small helper inline:
    va = tmp_path / "va.json"; va.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "ev.json"; ev.write_text("{}")
    ea = dict(engine_env); ea.update(BRANCH="A", PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir", "pr-1", proto, va, ev, env=ea)

    # B: draft done, finalize NOT yet → join must wait.
    _advance(tmp_path, engine_env, "pr-1", "B", "draft", proto)
    _run_join(tmp_path, engine_env, "pr-1", proto)
    work = _state_dir(tmp_path, engine_env)
    inst = read_state_yaml(work / "subpipeline-mini/pr-1/_instance.yaml")
    assert not inst.get("joined")   # still waiting on B.finalize

    # Finish B.
    _advance(tmp_path, engine_env, "pr-1", "B", "finalize", proto)
    _run_join(tmp_path, engine_env, "pr-1", proto)
    work = _state_dir(tmp_path, engine_env)
    inst = read_state_yaml(work / "subpipeline-mini/pr-1/_instance.yaml")
    assert inst.get("joined") is True
```

- [ ] **Step 2: Run test to verify current behaviour**

Run: `pytest tests/test_subpipeline.py -k join_waits_for_subpipeline -v`
Expected: PASS already IF Task 6 correctly marks the `B.yaml` cursor terminal only on `finalize`. If it FAILS (joined True after draft), Task 6's cursor handling is wrong — fix there, not here. This task is the integration guard.

- [ ] **Step 3: Add a clarifying guard/comment in `join.py`**

`join.py:65-66` already reads `lib.state_file(dir_, pid, instance, b, phase=phase_for_path)` — the branch cursor (no `substate`). Add a comment above line 65 to lock the invariant:

```python
    # NOTE: a sub-pipeline branch's terminal state lives in its CURSOR file
    # (review.<b>.yaml), written by advance.py only when the LAST sub-state is
    # done. We deliberately read the cursor here, never a sub-state file.
```

No behavioural change.

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green, including existing `test_join.py` and `test_fanout_e2e.py`.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/join.py tests/test_subpipeline.py
git commit -m "test(engine): e2e join waits for full sub-pipeline leg"
```

---

## Self-Review (Plan 1)

- **Spec coverage:** §1 scope model → Tasks 1,5,6 (cursor + nested files); §2 advance loop → Task 6; §8 testing/fixture → Tasks 4,8. Inputs/gate/merge are explicitly **out of scope** for Plan 1 (Plans 2-4).
- **Placeholder scan:** none — every step has concrete code/commands.
- **Type consistency:** `state_file(..., substate=)`, `resolve_agent_unit(..., substate=)`, `branch_substates`, `next_substate_id`, `is_subpipeline_branch`, `branch_config` used identically across Tasks 1-8. Cursor field name `sub_state` (state file) vs payload key `substate` (dispatch/env) is intentional and consistent throughout.

## Plan 1 → Plan 2 handoff

Plan 2 assumes: a sub-pipeline leg sequences correctly and each sub-state writes its own `…<substate>.yaml`. Plan 2 adds **output persistence** (write the evidence/answers artifact beside that file) and the **inputs channel**.
