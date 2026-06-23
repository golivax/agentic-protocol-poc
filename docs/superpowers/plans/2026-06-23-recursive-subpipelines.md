# Recursive Sub-Pipelines (Problem #2) — Implementation Plan (Stages 1–3: the engine)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the engine's fixed `(phase, branch, substate)` 3-tuple coordinate into an arbitrary-depth **node-path**, with one recursive sequencer and a recursive (bubbling) join, so a protocol may nest fanouts/sub-pipelines past depth 3 — bounded by a configurable `max_depth`.

**Architecture:** The state-file scheme is already a serialized node-path (`".".join(path) + ".yaml"`). We migrate the engine code (`lib.py`, `next.py`, `advance.py`, `join.py`) to operate on a path list internally while keeping the depth-≤3 file layout and behavior byte-identical, then lift the ceiling. Each fanout gets a path-keyed join marker; joins bubble up the tree via one `complete_sequence` primitive.

**Tech Stack:** Python 3 + PyYAML (runtime); pytest (dev-only). No new runtime deps.

**Scope of THIS plan:** Stages 1–3 — the engine, proven entirely by pytest. Stage 4 (GHA wiring in `agentic-engine.yml`/`agentic-orchestrator.yml`, the live `deep-review-stub` protocol + gh-aw agents, live PR verification) is a **separate follow-on plan** written after Stage 3 lands and the engine API is stable, mirroring how `recover-mental-model-stub` and Problem #1 sequenced engine-then-live.

**Spec:** `docs/superpowers/specs/2026-06-23-recursive-subpipelines-design.md`

## Global Constraints

- Runtime code (`.github/agent-factory/engine/`, protocols) may use only Python 3 + PyYAML. pytest is dev-only and never imported by runtime code.
- **Depth-≤3 behavior must stay byte-identical through Stages 1–2.** The regression oracle is the full existing suite plus the two live protocols' fixtures: `single-agent`, `fanout-mini`, `pipeline-mini`, `subpipeline-mini`, `multiphase-subpipeline`. `pytest tests/ -q` must stay green after every task.
- **Depth = node-path length.** `[phase]`=1, `[phase,branch]`=2, `[phase,branch,substate]`=3; a nested fanout adds two segments. `DEFAULT_MAX_DEPTH = 4`.
- State advances only by fast-forward push (CAS). Never force-push `agentic-state`.
- Security: agent-derived strings (including a leg's `path`) are composed of `protocol.json` node ids and passed via `env:`, never interpolated into `run:`.
- The engine reads no protocol-specific logic; all new helpers live in `lib.py` (generic) and are driven by `protocol.json` data.
- Run the suite from the repo root: `pytest tests/ -q` (one module: `pytest tests/test_paths.py -v`).
- Commit after every task with the shown message.

---

## File Structure

- **Create** `.github/agent-factory/engine/paths.py` — pure tree-navigation over a `protocol` dict + a path list (no I/O, no git). One responsibility: map a node-path to protocol nodes and compute structural relations (kind, children, parent, next sibling, life-state, depth). Importing it has no side effects, so it is unit-testable in isolation.
- **Modify** `.github/agent-factory/engine/lib.py` — `state_file`/`output_artifact_path` gain a `path=` form (kwarg shim retained); `resolve_agent_unit`, `_fanout_state`, `next_phase_id`, `next_substate_id`, `branch_substates`, `branch_config` reimplemented as thin wrappers over `paths.py`; `cas_push` gains a bounded retry loop; new `join_marker_file`/`read_join`/`write_join`.
- **Modify** `.github/agent-factory/engine/next.py` — `start_fanout`/`seed_and_dispatch_phase`/`seed_branch` delegate to a new recursive `enter_node`; the gate/`/answer` helpers take a path; the planner emits `run-fanout` when a continued path points at a fanout.
- **Modify** `.github/agent-factory/engine/advance.py` — the sub-pipeline advance block delegates to `advance_node`/`complete_sequence`; `done`/`failed` bubble via the shared primitive.
- **Modify** `.github/agent-factory/engine/join.py` — evaluate the fanout at a given path; on all-terminal call `complete_sequence` (bubbling) instead of the hardcoded finalize.
- **Create** `tests/fixtures/deep-fanout/` — depth-4 protocol (nested fanout inside a sub-pipeline + a flat sibling) with its checks/schemas.
- **Create** `tests/fixtures/too-deep/` — depth-5 protocol used only to assert `max_depth` refusal.
- **Create** test modules: `tests/test_paths.py`, `tests/test_recursive_sequencer.py`, `tests/test_recursive_join.py`, `tests/test_max_depth.py`, `tests/test_deep_fanout_e2e.py`.

---

# STAGE 1 — Internal path representation (behavior-preserving)

Goal: introduce the path model and route all coordinate logic through it, with **zero behavior change** at depth ≤3. The regression oracle is the existing suite.

## Task 1: `paths.py` — pure tree navigation

**Files:**
- Create: `.github/agent-factory/engine/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Produces (all pure; `proto` is the loaded protocol dict, `path` is a list of str ids):
  - `node_at_path(proto, path) -> dict | None` — the node dict addressed by `path`.
  - `node_kind(proto, path) -> str` — one of `"sequence" | "fanout" | "agent" | "gate" | "merge" | "join"` (a branch with `states` is `"sequence"`; the root is implicit and never addressed directly).
  - `children(proto, path) -> list[dict]` — a sequence's `states`, or a fanout's `branches`.
  - `parent_path(path) -> list[str]` — `path[:-1]`.
  - `next_sibling(proto, path) -> str | None` — id of the next child in the enclosing sequence, else None.
  - `first_child_id(node) -> str | None`.
  - `enclosing_fanout_id(proto, path) -> str | None` — the id of the nearest fanout ancestor (the leg's life-state).
  - `is_fanout/is_sequence/is_leaf(proto, path) -> bool`.
  - `path_depth(path) -> int` — `len(path)`.
  - `max_static_depth(proto) -> int` — the deepest leg path length in the static tree.

**Resolution rule (the core walk):** start at the protocol root (its `states` is the top sequence). For each id in `path`: if the current node is a sequence, the id selects a child of `states`; if the current node is a fanout, the id selects a branch of `branches`; descend. A branch that has `states` is itself a sequence for the next id. This is the same `fanout→branch→substate` ladder `resolve_agent_unit` walks today, generalized to repeat.

- [ ] **Step 1: Write failing tests** (assert the helpers agree with the existing fixtures' known structure)

```python
# tests/test_paths.py
import json, pathlib
import sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import paths

FIX = ROOT / "tests/fixtures"

def _proto(name):
    return json.load(open(FIX / name / "protocol.json"))

def test_node_at_path_top_fanout():
    p = _proto("subpipeline-mini")
    assert paths.node_at_path(p, ["review"])["kind"] == "fanout"

def test_node_kind_branch_subpipeline_is_sequence():
    p = _proto("subpipeline-mini")
    assert paths.node_kind(p, ["review", "B"]) == "sequence"

def test_node_kind_flat_branch_is_agent():
    p = _proto("subpipeline-mini")
    assert paths.node_kind(p, ["review", "A"]) == "agent"

def test_node_at_path_substate_leaf():
    p = _proto("subpipeline-mini")
    assert paths.node_at_path(p, ["review", "B", "clarify"])["kind"] == "gate"

def test_next_sibling_within_subpipeline():
    p = _proto("subpipeline-mini")
    assert paths.next_sibling(p, ["review", "B", "draft"]) == "clarify"
    assert paths.next_sibling(p, ["review", "B", "finalize"]) is None

def test_next_sibling_top_sequence_multiphase():
    p = _proto("multiphase-subpipeline")
    assert paths.next_sibling(p, ["setup"]) == "review"

def test_enclosing_fanout_id():
    p = _proto("subpipeline-mini")
    assert paths.enclosing_fanout_id(p, ["review", "B", "finalize"]) == "review"
    assert paths.enclosing_fanout_id(p, ["review", "A"]) == "review"

def test_max_static_depth_depth3():
    assert paths.max_static_depth(_proto("subpipeline-mini")) == 3
    assert paths.max_static_depth(_proto("single-agent")) == 1
```

- [ ] **Step 2: Run, verify they fail**

Run: `pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paths'`.

- [ ] **Step 3: Implement `paths.py`**

```python
#!/usr/bin/env python3
"""Pure tree navigation over a protocol dict + a node-path (list of ids).
No I/O, no git — addressing and structural relations only. The path is the
arbitrary-depth generalization of the fixed (phase, branch, substate) tuple."""

_LEAF_KINDS = ("agent", "gate", "merge", "join")


def _root_children(proto):
    return proto.get("states", [])


def _child_by_id(node_children, cid):
    for c in node_children:
        if c.get("id") == cid:
            return c
    return None


def _is_sequence_node(node):
    # A branch with `states` is a sub-pipeline (sequence). The protocol root is a
    # sequence too but is never addressed by an id (the empty path).
    return bool(node) and isinstance(node.get("states"), list)


def node_at_path(proto, path):
    """Return the protocol node addressed by `path`, or None."""
    # Level 0 children are the protocol's top-level states (a sequence).
    cur_children = _root_children(proto)
    cur = None
    for i, seg in enumerate(path):
        if cur is None or _is_sequence_node(cur) or i == 0:
            # selecting a child of a sequence (root or sub-pipeline)
            container = cur_children if cur is None else cur.get("states", [])
            cur = _child_by_id(container, seg)
        elif cur.get("kind") == "fanout":
            cur = _child_by_id(cur.get("branches", []), seg)
        else:
            return None  # tried to descend into a leaf
        if cur is None:
            return None
    return cur


def children(proto, path):
    node = node_at_path(proto, path)
    if node is None:
        return []
    if node.get("kind") == "fanout":
        return node.get("branches", [])
    if _is_sequence_node(node):
        return node.get("states", [])
    return []


def node_kind(proto, path):
    node = node_at_path(proto, path)
    if node is None:
        return ""
    if node.get("kind") == "fanout":
        return "fanout"
    if _is_sequence_node(node):
        return "sequence"
    return node.get("kind", "")


def is_fanout(proto, path):
    return node_kind(proto, path) == "fanout"


def is_sequence(proto, path):
    return node_kind(proto, path) == "sequence"


def is_leaf(proto, path):
    return node_kind(proto, path) in _LEAF_KINDS


def parent_path(path):
    return list(path[:-1])


def first_child_id(node):
    if node is None:
        return None
    if node.get("kind") == "fanout":
        bs = node.get("branches", [])
        return bs[0]["id"] if bs else None
    if _is_sequence_node(node):
        ss = node.get("states", [])
        return ss[0]["id"] if ss else None
    return None


def next_sibling(proto, path):
    """Id of the next child within the enclosing sequence, or None.
    Only sequences have an ordered `next`; a fanout's branches are unordered."""
    if not path:
        return None
    parent = node_at_path(proto, parent_path(path)) if len(path) > 1 else None
    if parent is None:
        # enclosing scope is the protocol root (a sequence)
        siblings = _root_children(proto)
    elif _is_sequence_node(parent):
        siblings = parent.get("states", [])
    else:
        return None  # parent is a fanout: branches have no ordered successor
    ids = [c["id"] for c in siblings]
    last = path[-1]
    if last in ids:
        i = ids.index(last)
        if i + 1 < len(ids):
            return ids[i + 1]
    return None


def enclosing_fanout_id(proto, path):
    """Id of the nearest fanout ancestor of `path` (the leg's life-state)."""
    for k in range(len(path) - 1, -1, -1):
        anc = path[:k + 1]
        if node_kind(proto, anc) == "fanout":
            return anc[-1]
    return None


def path_depth(path):
    return len(path)


def _leg_paths(proto, prefix, node):
    """Yield every leaf leg path under `node` (for static depth)."""
    if node.get("kind") == "fanout":
        out = []
        for b in node.get("branches", []):
            out += _leg_paths(proto, prefix + [b["id"]], b)
        return out
    if _is_sequence_node(node):
        out = []
        for s in node.get("states", []):
            out += _leg_paths(proto, prefix + [s["id"]], s)
        return out
    return [prefix]


def max_static_depth(proto):
    depths = [0]
    for s in _root_children(proto):
        for lp in _leg_paths(proto, [s["id"]], s):
            depths.append(len(lp))
    return max(depths)
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_paths.py -v`
Expected: PASS (all 8).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/paths.py tests/test_paths.py
git commit -m "feat(engine): paths.py pure node-path tree navigation"
```

## Task 2: `state_file`/`output_artifact_path` accept a `path=` form

**Files:**
- Modify: `.github/agent-factory/engine/lib.py:41-72`
- Test: `tests/test_paths.py` (extend)

**Interfaces:**
- Produces: `lib.state_file(d, pid, instance, *, path=None, branch=None, phase=None, substate=None)` — when `path` is given (a list), the file is `<base>/<".".join(path)>.yaml` (or `<base>.yaml` for the empty path). The existing `branch/phase/substate` kwargs build the equivalent 3-element path internally, so all current call sites are unchanged. `output_artifact_path` gains the same `path=`.

- [ ] **Step 1: Write failing test** (path form equals the kwarg form for depth ≤3, and supports depth ≥4)

```python
# append to tests/test_paths.py
import importlib
lib = importlib.import_module("lib")  # same engine sys.path as paths

def test_state_file_path_matches_kwargs():
    a = lib.state_file("/s", "p", "pr-1", phase="review", branch="B", substate="draft")
    b = lib.state_file("/s", "p", "pr-1", path=["review", "B", "draft"])
    assert a == b == "/s/p/pr-1/review.B.draft.yaml"

def test_state_file_path_deep():
    got = lib.state_file("/s", "p", "pr-1", path=["pre", "deep", "analyze", "sec"])
    assert got == "/s/p/pr-1/pre.deep.analyze.sec.yaml"

def test_output_artifact_path_deep():
    got = lib.output_artifact_path("/s", "p", "pr-1",
                                   path=["pre", "deep", "analyze", "sec"], kind="evidence")
    assert got == "/s/p/pr-1/pre.deep.analyze.sec.evidence.json"
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/test_paths.py -k path_ -v`
Expected: FAIL — `state_file() got an unexpected keyword argument 'path'`.

- [ ] **Step 3: Implement** — rewrite `state_file` to build a path then join it

```python
def _coord_to_path(branch=None, phase=None, substate=None):
    """Back-compat: collapse the legacy 3 kwargs to a node-path list."""
    p = []
    if phase:
        p.append(phase)
    if branch:
        p.append(branch)
    if substate:
        p.append(substate)
    return p


def state_file(d, pid, instance, branch=None, phase=None, substate=None, path=None):
    """<dir>/<pid>/<instance>/<dot-joined-path>.yaml (or <instance>.yaml for the
    empty path). `path` is the canonical node-path; the branch/phase/substate
    kwargs are a back-compat shim that builds the equivalent 3-element path.
    Depth-<=3 paths are byte-identical to the historical layout."""
    base = f"{d}/{pid}/{instance}"
    p = list(path) if path is not None else _coord_to_path(branch, phase, substate)
    if not p:
        return f"{base}.yaml"
    return f"{base}/{'.'.join(p)}.yaml"


def output_artifact_path(d, pid, instance, branch=None, phase=None, substate=None,
                         kind="evidence", path=None):
    sf = state_file(d, pid, instance, branch=branch, phase=phase, substate=substate, path=path)
    return sf[:-len(".yaml")] + f".{kind}.json"
```

- [ ] **Step 4: Run path tests + full suite (regression)**

Run: `pytest tests/test_paths.py -v && pytest tests/ -q`
Expected: PASS; full suite green (byte-identical paths).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_paths.py
git commit -m "feat(engine): state_file/output_artifact_path accept node-path form"
```

## Task 3: Reimplement the fixed-rung resolvers over `paths.py`

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` — `resolve_agent_unit` (182-225), `_fanout_state` (83-87), `branch_config` (90-98), `branch_substates` (106-111), `next_substate_id` (114-122), `next_phase_id`, `is_subpipeline_branch` (101-103).
- Test: existing `tests/test_resolve_agent_unit.py`, `tests/test_subpipeline.py` are the oracle (must stay green).

**Interfaces:**
- Produces: `resolve_agent_unit(protocol, phase="", branch="", substate="")` — unchanged signature and return shape `{"agent_state", "max_iterations", "life_state"}`; internally builds `path = _coord_to_path(branch, phase, substate)` and reads `paths.node_at_path` + `paths.enclosing_fanout_id`. A new `resolve_agent_unit_path(protocol, path)` is the canonical form; the legacy wrapper delegates to it.

- [ ] **Step 1: Write failing test** for the canonical path form

```python
# tests/test_resolve_agent_unit.py (append)
def test_resolve_unit_by_path_subpipeline():
    import json, pathlib, sys
    ROOT = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
    import lib
    p = json.load(open(ROOT / "tests/fixtures/subpipeline-mini/protocol.json"))
    u = lib.resolve_agent_unit_path(p, ["review", "B", "finalize"])
    assert u == {"agent_state": "finalize", "max_iterations": 2, "life_state": "review"}
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/test_resolve_agent_unit.py -k by_path -v`
Expected: FAIL — `module 'lib' has no attribute 'resolve_agent_unit_path'`.

- [ ] **Step 3: Implement** — add `resolve_agent_unit_path` and rewrite the resolvers as wrappers

```python
import paths as _paths  # at top of lib.py with the other imports


def resolve_agent_unit_path(protocol, path):
    """Canonical: resolve the agent unit for the leaf at `path`."""
    node = _paths.node_at_path(protocol, path)
    if node is None:
        raise ValueError(f"no node at path {'.'.join(path)}")
    life = _paths.enclosing_fanout_id(protocol, path)
    return {"agent_state": path[-1],
            "max_iterations": node.get("max_iterations"),
            "life_state": life if life is not None else path[-1]}


def resolve_agent_unit(protocol, phase="", branch="", substate=""):
    """Back-compat shim preserving the exact error texts next.py/advance.py map."""
    path = _coord_to_path(branch, phase, substate)
    if not path:
        for st in protocol.get("states", []):
            if st.get("kind") == "agent":
                return {"agent_state": st["id"], "max_iterations": st.get("max_iterations"),
                        "life_state": st["id"]}
        raise ValueError("protocol has no agent state")
    # Preserve the historical fanout-without-branch error.
    if phase:
        st = _paths.node_at_path(protocol, [phase])
        if st is None:
            raise ValueError(f"no phase '{phase}' in protocol")
        if st.get("kind") == "fanout" and not branch:
            raise ValueError(f"PHASE='{phase}' is a fanout phase but BRANCH is empty")
    node = _paths.node_at_path(protocol, path)
    if node is None:
        if substate:
            raise ValueError(f"no sub-state '{substate}' in branch '{branch}'")
        if phase and branch:
            raise ValueError(f"no branch '{branch}' in phase '{phase}'")
        raise ValueError(f"no branch '{branch}' in protocol")
    return resolve_agent_unit_path(protocol, path)
```

Then rewrite the small helpers as wrappers (keep their names — many call sites):

```python
def _fanout_state(protocol):
    for s in protocol.get("states", []):
        if s.get("kind") == "fanout":
            return s
    return None  # unchanged: still returns the FIRST top-level fanout

def is_subpipeline_branch(branch_cfg):
    return bool(branch_cfg) and bool(branch_cfg.get("states"))

def branch_config(protocol, branch):
    fo = _fanout_state(protocol)
    return _paths._child_by_id(fo.get("branches", []), branch) if fo else None

def branch_substates(protocol, branch):
    cfg = branch_config(protocol, branch)
    return list(cfg.get("states", [])) if is_subpipeline_branch(cfg) else []

def next_substate_id(protocol, branch, substate):
    fo = _fanout_state(protocol)
    return _paths.next_sibling(protocol, [fo["id"], branch, substate]) if fo else None
```

(`next_phase_id` stays as-is or becomes `_paths.next_sibling(protocol, [phase])`; verify against `test_multiphase.py`.)

- [ ] **Step 4: Run targeted + full suite**

Run: `pytest tests/test_resolve_agent_unit.py tests/test_subpipeline.py tests/test_multiphase.py -v && pytest tests/ -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_resolve_agent_unit.py
git commit -m "refactor(engine): resolvers delegate to paths.py (no behavior change)"
```

## Task 4: `cas_push` bounded retry loop

**Files:**
- Modify: `.github/agent-factory/engine/lib.py:424-440`
- Test: `tests/test_engine.py` (append) or new `tests/test_cas.py`

**Interfaces:**
- Produces: `cas_push(dir_, msg, attempts=5)` — fast-forward push; on rejection `pull --rebase` and retry up to `attempts` times with a tiny sleep between; raises only after the last attempt fails. Single-writer behavior unchanged (succeeds on attempt 1).

- [ ] **Step 1: Write failing test** — a concurrent writer commits between our commit and push; CAS must rebase and still land within the loop.

```python
# tests/test_cas.py
import os, subprocess, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))

def _clone(origin, into):
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", str(origin), str(into)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(into), "config", k, v], check=True)

def test_cas_push_rebases_over_concurrent_writer(state_origin, tmp_path, monkeypatch):
    import lib
    # seed origin with one commit on agentic-state
    seed = tmp_path / "seed"; _clone(state_origin, seed)
    (seed / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "seed"], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "agentic-state"], check=True)
    # our working clone (writes b.txt)
    ours = tmp_path / "ours"; _clone(state_origin, ours)
    # a concurrent writer pushes c.txt AFTER we clone but BEFORE we push
    other = tmp_path / "other"; _clone(state_origin, other)
    (other / "c.txt").write_text("c")
    subprocess.run(["git", "-C", str(other), "add", "."], check=True)
    subprocess.run(["git", "-C", str(other), "commit", "-q", "-m", "other"], check=True)
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "agentic-state"], check=True)
    # now our push would be rejected; cas_push must rebase + land
    (ours / "b.txt").write_text("b")
    monkeypatch.setenv("STATE_BRANCH", "agentic-state")
    lib.cas_push(str(ours), "ours change")
    # both files exist on origin tip
    log = subprocess.run(["git", "-C", str(other), "pull", "-q"], capture_output=True)
    files = set(p.name for p in other.iterdir() if p.suffix == ".txt")
    assert {"a.txt", "b.txt", "c.txt"} <= files
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/test_cas.py -v`
Expected: FAIL — the current single-retry may pass for 1 collision; to force failure, assert the loop param exists: add `assert "attempts" in lib.cas_push.__code__.co_varnames`. (If the single-retry already lands this 1-collision case, keep the test as a guard and proceed; the value is the multi-attempt loop below.)

- [ ] **Step 3: Implement the bounded loop**

```python
def cas_push(dir_, msg, attempts=5):
    """Commit everything and push fast-forward-only, retrying via rebase up to
    `attempts` times. NEVER force-push. A genuinely empty commit is a bug → fail."""
    import time
    git(dir_, *GIT_ID, "add", "-A")
    # empty-commit guard (unchanged)
    staged = subprocess.run(["git", "-C", dir_, "diff", "--cached", "--quiet"]).returncode
    if staged == 0:
        sys.stderr.write("[engine] cas_push: nothing staged — refusing empty commit\n")
        sys.exit(1)
    git(dir_, *GIT_ID, "commit", "-q", "-m", msg)
    last = None
    for i in range(attempts):
        r = subprocess.run(["git", "-C", dir_, "push", "-q", "origin", STATE_BRANCH])
        if r.returncode == 0:
            return
        last = r
        sys.stderr.write(f"[engine] CAS push rejected (attempt {i+1}/{attempts}), rebasing\n")
        git(dir_, *GIT_ID, "pull", "-q", "--rebase", "origin", STATE_BRANCH)
        time.sleep(0.1 * (i + 1))
    sys.stderr.write("[engine] CAS push failed after retries\n")
    sys.exit(1)
```

(Preserve the exact pre-existing empty-commit guard wording if it differs; the goal is the loop, not a guard rewrite.)

- [ ] **Step 4: Run cas test + full suite**

Run: `pytest tests/test_cas.py -v && pytest tests/ -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_cas.py
git commit -m "feat(engine): cas_push bounded retry loop for deep-concurrency ref contention"
```

---

# STAGE 2 — Unified recursive sequencer + recursive join (still depth ≤3)

Goal: collapse the four drifted "start the next thing" code paths into one recursive `enter_node`/`advance_node`/`complete_sequence`, and make `join.py` bubble — **without** yet allowing depth >3. Existing fixtures stay byte-identical and are the oracle.

## Task 5: `enter_node` in `next.py` (delegate the three seed paths to it)

**Files:**
- Modify: `.github/agent-factory/engine/next.py` — add `enter_node`; rewrite `seed_branch` (44-70), `start_fanout` (80-100), and the `kind=="fanout"`/`"gate"`/agent arms of `seed_and_dispatch_phase` (172-196) to delegate.
- Test: `tests/test_recursive_sequencer.py`; oracle = `tests/test_engine.py`, `tests/test_multiphase_subpipeline.py`, `tests/test_subpipeline.py`.

**Interfaces:**
- Produces (module-level in `next.py`, using its existing `DIR/PID/INSTANCE/HEAD_SHA/COMMAND` globals and `lib`):
  - `enter_node(proto, path, command, *, emit=True) -> dict|None` — seed the state file(s) for the node at `path` and, when `emit`, print the action JSON (`run-agent` / `run-fanout` / gate-open `noop`). For a fanout it writes the path-keyed join marker and returns nothing (it prints `run-fanout`); for an agent leaf it writes the fresh state file and prints `run-agent`; for a sequence it writes the cursor and recurses into the first child; for a gate it calls `lib.open_gate` (path-aware) and prints the gate `noop`.
- Consumes: `paths.*`, `lib.state_file(..., path=)`, `lib.dump_yaml`, `lib.open_gate`, `lib.write_join` (Task 7).

- [ ] **Step 1: Write failing test** — entering a top fanout via `enter_node` seeds exactly what `start_fanout` seeds today (depth-3 byte check)

```python
# tests/test_recursive_sequencer.py
import json, os, pathlib, subprocess, sys, shutil
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"

def _run_next(state_dir, proto, instance, cmd, env, **coords):
    e = dict(env)
    for k in ("PHASE", "BRANCH", "SUBSTATE"):
        e.pop(k, None)
    for k, v in coords.items():
        e[k.upper()] = v
    return subprocess.run(["python3", str(ENGINE / "next.py"), str(state_dir), instance,
                           str(proto), cmd], text=True, capture_output=True, env=e)

def test_enter_top_fanout_seeds_branches(engine_env, tmp_path):
    sd = tmp_path / "state"; sd.mkdir()
    proto = ROOT / "tests/fixtures/subpipeline-mini/protocol.json"
    r = _run_next(sd, proto, "pr-1", "start", engine_env)
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-fanout"
    # flat branch A: no substate; sub-pipeline branch B: substate=draft
    by = {b["id"]: b for b in action["branches"]}
    assert "substate" not in by["A"]
    assert by["B"]["substate"] == "draft"
    # cursor + first sub-state files written under the instance dir
    base = sd / "subpipeline-mini" / "pr-1"
    assert (base / "B.yaml").exists() and (base / "B.draft.yaml").exists()
```

- [ ] **Step 2: Run, verify pass-or-fail baseline**

Run: `pytest tests/test_recursive_sequencer.py -v`
Expected: PASS even before refactor (it exercises existing behavior). This test is the **byte-identical guard**; keep it, then refactor under it. If it fails, the fixture path is wrong — fix the test first.

- [ ] **Step 3: Implement `enter_node` and delegate**

Add to `next.py` (above `start_fanout`):

```python
def enter_node(proto, path, command, emit=True):
    """Recursive sequencer: seed the node at `path` and dispatch/emit its action.
    Generalizes start_fanout + seed_and_dispatch_phase + seed_branch into one walk."""
    kind = paths.node_kind(proto, path)
    node = paths.node_at_path(proto, path)
    life = paths.enclosing_fanout_id(proto, path)
    if kind == "sequence":
        first = paths.first_child_id(node)
        cf = lib.state_file(DIR, PID, INSTANCE, path=path)
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        lib.dump_yaml(cf, {"protocol": PID, "instance": INSTANCE, "state": life,
                           "sub_state": first, "iteration": 1, "gates": {}, "history": []})
        return enter_node(proto, path + [first], command, emit=emit)
    if kind == "fanout":
        lib.write_join(DIR, PID, INSTANCE, path, {"joined": False})
        branches = []
        for b in node.get("branches", []):
            branches.append(_seed_child(proto, path + [b["id"]], b))
        if emit:
            print(json.dumps({"action": "run-fanout", "iteration": 1, "feedback": "",
                              "reason": f"fanout:{path[-1]}",
                              "phase": (path[0] if lib.is_multiphase(proto) else ""),
                              "legs": [{"path": ".".join(p)} for p in
                                       [path + [b['id']] for b in node.get('branches', [])]],
                              "branches": branches}))
        return None
    if kind == "agent":
        sf = lib.state_file(DIR, PID, INSTANCE, path=path)
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        lib.dump_yaml(sf, {"protocol": PID, "instance": INSTANCE, "state": life or path[-1],
                           "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": []})
        if emit:
            act = {"action": "run-agent", "iteration": 1, "feedback": "",
                   "reason": f"enter:{path[-1]}", "path": ".".join(path)}
            print(json.dumps(act))
        return {"id": path[-1], "workflow": node.get("workflow"), "iteration": 1, "feedback": ""}
    if kind == "gate":
        lib.open_gate(DIR, PID, INSTANCE, PROTO, path[-1], HEAD_SHA,
                      INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE,
                      path=path)
        if emit:
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"gate-open:{path[-1]}"}))
        return None
    return None


def _seed_child(proto, path, cfg):
    """Seed one fanout child (flat agent OR sub-pipeline) WITHOUT emitting; return
    its run-fanout branch dict (carrying `substate` for a sub-pipeline)."""
    if paths.is_sequence(proto, path):
        first = paths.first_child_id(cfg)
        cf = lib.state_file(DIR, PID, INSTANCE, path=path)
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        life = paths.enclosing_fanout_id(proto, path)
        lib.dump_yaml(cf, {"protocol": PID, "instance": INSTANCE, "state": life,
                           "sub_state": first, "iteration": 1, "gates": {}, "history": []})
        sf = lib.state_file(DIR, PID, INSTANCE, path=path + [first])
        lib.dump_yaml(sf, {"protocol": PID, "instance": INSTANCE, "state": life,
                           "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": []})
        fc = paths.node_at_path(proto, path + [first])
        return {"id": path[-1], "workflow": fc.get("workflow"),
                "substate": first, "iteration": 1, "feedback": ""}
    sf = lib.state_file(DIR, PID, INSTANCE, path=path)
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    life = paths.enclosing_fanout_id(proto, path)
    flat = {"protocol": PID, "instance": INSTANCE, "state": life,
            "iteration": 1, "gates": {}, "history": []}
    if lib.is_multiphase(proto):
        flat["head_sha"] = HEAD_SHA
    lib.dump_yaml(sf, flat)
    return {"id": path[-1], "workflow": cfg.get("workflow"), "iteration": 1, "feedback": ""}
```

Then make the existing `seed_branch`, `start_fanout`, and `seed_and_dispatch_phase` delegate: `start_fanout` builds the top path `[fanout_id]`, writes the instance file + label as today, then calls `enter_node(proto_data, [fanout_id], COMMAND)`; `seed_and_dispatch_phase` for a `fanout` phase calls `enter_node(proto_data, [phase_id], command)`. **Keep the `branches` key in the emitted JSON** (the GHA layer still reads it in Stages 1–3; `legs` is added alongside for Stage 4). Preserve all instance-file / label / cas_push side-effects exactly.

- [ ] **Step 4: Run targeted + full suite (the real gate)**

Run: `pytest tests/test_recursive_sequencer.py tests/test_engine.py tests/test_multiphase_subpipeline.py tests/test_subpipeline.py -v && pytest tests/ -q`
Expected: PASS; full suite green (byte-identical emit + files).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_recursive_sequencer.py
git commit -m "refactor(engine): unify seed paths into recursive enter_node (depth<=3 identical)"
```

## Task 6: `advance_node`/`complete_sequence` in `advance.py`

**Files:**
- Modify: `.github/agent-factory/engine/advance.py` — replace the `if branch and substate:` sub-pipeline block (349-408) and the `failed` sub-pipeline block (536-541) with calls to a shared `advance_node`/`complete_sequence`.
- Test: oracle = `tests/test_subpipeline.py`, `tests/test_multiphase_subpipeline.py`, `tests/test_gate_data.py`.

**Interfaces:**
- Produces (in `advance.py`):
  - `advance_node(proto, proto_path, dir_, pid, instance, path, process, sha, github_repository)` — on `done`: if `next_sibling` exists, seed+dispatch it (agent → seed file + `protocol-continue` carrying `path`; gate → `lib.open_gate(path=...)`); else `complete_sequence(parent_path(path))`. On `failed`: mark the leg cursor failed and bubble.
  - `complete_sequence(proto, ..., seq_path, ok)` — the bubbling primitive: mark the sequence done/failed; if `seq_path` is a fanout branch, fire that fanout's join (`fire_join` carrying the fanout path); if it is a top phase, advance the instance phase (today's behavior).
- Consumes: `lib.next_substate_id`→`paths.next_sibling`, `lib.write_join`, `lib.read_join`.

- [ ] **Step 1: Write failing test** — a depth-3 sub-pipeline still advances draft→(gate)→finalize→leg-done via the new function

```python
# tests/test_recursive_sequencer.py (append) — drive advance.py on branch B draft done
import yaml
def test_advance_subpipeline_draft_to_gate(engine_env, tmp_path):
    sd = tmp_path / "state"; sd.mkdir()
    proto = ROOT / "tests/fixtures/subpipeline-mini/protocol.json"
    # seed via next start
    _run_next(sd, proto, "pr-1", "start", engine_env)
    base = sd / "subpipeline-mini" / "pr-1"
    # craft a passing verdicts file + empty evidence, run advance for B/draft
    ver... # (use the same verdicts/evidence helpers test_subpipeline.py uses)
```

> Implementer note: copy the exact verdicts/evidence construction from `tests/test_subpipeline.py` (it already drives `advance.py` for branch B). The assertion: after `draft` done, `B.yaml` cursor `sub_state == "clarify"` and `B.clarify.yaml` gate state is `open`. This reproduces an existing test — its passing under the refactor is the guard.

- [ ] **Step 2: Run, verify the existing subpipeline suite is the oracle**

Run: `pytest tests/test_subpipeline.py -v`
Expected: PASS (pre-refactor baseline).

- [ ] **Step 3: Implement** `advance_node`/`complete_sequence`, route the `done`/`failed` sub-pipeline blocks through them. The agent re-dispatch payload gains `client_payload[path]` alongside the existing `branch`/`substate`/`phase` (kept for Stage-1/2 back-compat). `complete_sequence` for a fanout-branch leg calls the existing `fire_join(pid, instance, branch)` (Stage 2 keeps the single-join path); the path-keyed bubbling is exercised in Stage 3.

- [ ] **Step 4: Run targeted + full suite**

Run: `pytest tests/test_subpipeline.py tests/test_multiphase_subpipeline.py tests/test_gate_data.py -v && pytest tests/ -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/advance.py tests/test_recursive_sequencer.py
git commit -m "refactor(engine): advance.py sub-pipeline via shared advance_node/complete_sequence"
```

## Task 7: Path-keyed join markers + `join.py` bubbling primitive

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` — add `join_marker_file`, `read_join`, `write_join`.
- Modify: `.github/agent-factory/engine/join.py` — evaluate the fanout at `cursor`'s path; on all-terminal route through `complete_sequence`-equivalent logic. Keep `_instance.yaml.joined` for the top fanout (status renderer + back-compat).
- Test: oracle = `tests/test_join.py`, `tests/test_fanout_e2e.py`, `tests/test_merge.py`, `tests/test_recover_mental_model.py`.

**Interfaces:**
- Produces:
  - `lib.join_marker_file(d, pid, instance, fanout_path) -> str` — `<base>/<dot-path>.__join.yaml`. For the root single fanout this is a NEW file; the top-level `_instance.yaml.joined` bool is still written for back-compat.
  - `lib.read_join(d, pid, instance, fanout_path) -> dict` / `lib.write_join(d, pid, instance, fanout_path, data)`.

- [ ] **Step 1: Write failing test** — join marker round-trips per path; two distinct fanout paths are independent

```python
# tests/test_recursive_join.py
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import lib

def test_join_marker_path_keyed(tmp_path):
    d = str(tmp_path)
    lib.write_join(d, "p", "pr-1", ["pre", "deep", "analyze"], {"joined": True})
    lib.write_join(d, "p", "pr-1", ["pre"], {"joined": False})
    assert lib.read_join(d, "p", "pr-1", ["pre", "deep", "analyze"])["joined"] is True
    assert lib.read_join(d, "p", "pr-1", ["pre"])["joined"] is False
    f = lib.join_marker_file(d, "p", "pr-1", ["pre", "deep", "analyze"])
    assert f.endswith("/p/pr-1/pre.deep.analyze.__join.yaml")
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/test_recursive_join.py -v`
Expected: FAIL — `module 'lib' has no attribute 'write_join'`.

- [ ] **Step 3: Implement** the three lib helpers; then update `join.py` to compute `fanout_path` (the cursor phase path for multiphase, else `[fanout_id]`), read the per-branch cursor at `fanout_path + [b]`, and on all-terminal perform today's finalize/gate/merge/agent logic (unchanged) but recorded against the path-keyed marker. For Stage 2 the bubble target is still the root, so behavior is identical.

```python
def join_marker_file(d, pid, instance, fanout_path):
    base = f"{d}/{pid}/{instance}"
    return f"{base}/{'.'.join(fanout_path)}.__join.yaml"

def read_join(d, pid, instance, fanout_path):
    f = join_marker_file(d, pid, instance, fanout_path)
    return load_yaml(f) if os.path.isfile(f) else {}

def write_join(d, pid, instance, fanout_path, data):
    f = join_marker_file(d, pid, instance, fanout_path)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    dump_yaml(f, data)
```

- [ ] **Step 4: Run targeted + full suite**

Run: `pytest tests/test_recursive_join.py tests/test_join.py tests/test_fanout_e2e.py tests/test_merge.py tests/test_recover_mental_model.py -v && pytest tests/ -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py .github/agent-factory/engine/join.py tests/test_recursive_join.py
git commit -m "feat(engine): path-keyed join markers; join.py routes via fanout path"
```

## Task 8: `open_gate` and `/answer` take a path

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` — `open_gate(..., path=None)` (it already has `branch=`/`phase=`; add `path=` building the qualified file).
- Modify: `.github/agent-factory/engine/next.py` — `_find_open_gate_branch`→`_find_open_gate` returns a path; `do_answer` uses the path; `_gate_phase` subsumed by `paths.enclosing_fanout_id`.
- Test: oracle = `tests/test_gate_data.py`, `tests/test_gate.py`, plus a new assertion.

**Interfaces:**
- Produces: `_find_open_gate(proto, want="") -> list[str] | None` (the gate's full path). `do_answer` uses `lib.state_file(..., path=gate_path)` and `paths.enclosing_fanout_id(proto, gate_path)` for the life-state.

- [ ] **Step 1: Write failing test** — `/answer` to the depth-3 gate still advances B to finalize (existing behavior, new code path)

```python
# tests/test_recursive_sequencer.py (append) — reuse test_gate_data.py's /answer driver,
# asserting B.yaml cursor advances to "finalize" after full coverage.
```

> Implementer note: lift the `/answer` invocation + answers fixture from `tests/test_gate_data.py`; assert the post-answer cursor `sub_state == "finalize"`.

- [ ] **Step 2: Run baseline** `pytest tests/test_gate_data.py -v` → PASS.

- [ ] **Step 3: Implement** the path-aware `open_gate`, `_find_open_gate` (walk the tree for a `gate` leaf in state `open`), and route `do_answer` through paths. Keep the legacy `branch=`/`phase=` kwargs on `open_gate` working (build the 3-element path).

- [ ] **Step 4: Run targeted + full suite**

Run: `pytest tests/test_gate_data.py tests/test_gate.py tests/test_recursive_sequencer.py -v && pytest tests/ -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py .github/agent-factory/engine/next.py tests/test_recursive_sequencer.py
git commit -m "refactor(engine): gates + /answer operate on a node-path"
```

## Task 9: Stage-2 regression gate — re-verify the two live protocols

**Files:** none (verification task).

- [ ] **Step 1: Full suite**

Run: `pytest tests/ -q`
Expected: PASS — all tests (count ≥ the 408 baseline; new tests add to it).

- [ ] **Step 2: Diff the two live protocols' planner output** for a representative command and assert byte-identical emit vs. `main`.

```bash
git stash list >/dev/null
# code-review start (multiphase) and recover (single-phase fanout): capture next.py stdout
for P in code-review recover-mental-model-stub; do
  PROTO=.github/agent-factory/protocols/$P/protocol.json
  ENGINE_LOCAL=1 STATE_REMOTE=$(mktemp -d) \
    python3 .github/agent-factory/engine/next.py /tmp/s-$P pr-999 "$PROTO" start 2>/dev/null \
    | python3 -m json.tool > /tmp/emit-$P.new.json || true
done
echo "compare /tmp/emit-*.new.json against the same run on origin/main"
```

Expected: the `action`/`branches` structure matches `main` (the new `legs`/`path` keys are additive). Record the comparison in the commit message.

- [ ] **Step 3: Commit** (a no-op marker commit or just proceed). If anything differs beyond additive keys, STOP and fix before Stage 3.

```bash
git commit --allow-empty -m "test: Stage 2 regression gate — code-review + recover emit unchanged (additive keys only)"
```

---

# STAGE 3 — Lift the ceiling: deep nesting + bubbling + max_depth

Goal: allow a sub-state to be a fanout/sequence; make joins bubble across levels; enforce `max_depth`; prove it all on a real depth-4 fixture.

## Task 10: `deep-fanout` fixture (depth-4)

**Files:**
- Create: `tests/fixtures/deep-fanout/protocol.json`
- Create: `tests/fixtures/deep-fanout/checks/always-pass.py` (copy from `multiphase-subpipeline/checks/always-pass.py`)
- Create: evidence schemas referenced below (minimal, copy the shape of `multiphase-subpipeline/a.evidence.schema.json`).

**Interfaces:** the fixture shape (depth-4 deepest leg `["preflight","deep","analyze","sec"]`):

- [ ] **Step 1: Create `protocol.json`**

```json
{
  "name": "deep-fanout",
  "version": "0.1.0",
  "max_depth": 4,
  "triggers": [{ "on": "pull_request", "actions": ["opened"], "command": "start" }],
  "states": [
    {
      "id": "preflight",
      "kind": "fanout",
      "branches": [
        { "id": "quick", "workflow": "quick-agent", "evidence": "leaf.evidence.schema.json",
          "max_iterations": 2, "checks": [{ "run": "always-pass", "on_fail": "iterate" }],
          "publish": "noop" },
        {
          "id": "deep",
          "states": [
            { "id": "triage", "kind": "agent", "workflow": "triage-agent",
              "evidence": "leaf.evidence.schema.json", "max_iterations": 2,
              "checks": [{ "run": "always-pass", "on_fail": "iterate" }] },
            {
              "id": "analyze",
              "kind": "fanout",
              "branches": [
                { "id": "sec", "workflow": "sec-agent", "evidence": "leaf.evidence.schema.json",
                  "max_iterations": 2, "checks": [{ "run": "always-pass", "on_fail": "iterate" }],
                  "publish": "noop" },
                { "id": "perf", "workflow": "perf-agent", "evidence": "leaf.evidence.schema.json",
                  "max_iterations": 2, "checks": [{ "run": "always-pass", "on_fail": "iterate" }],
                  "publish": "noop" }
              ],
              "next": "join-analyze"
            },
            { "id": "join-analyze", "kind": "join", "of": "analyze", "next": "report" },
            { "id": "report", "kind": "agent", "workflow": "report-agent",
              "evidence": "leaf.evidence.schema.json", "max_iterations": 2,
              "inputs": [{ "from": "sec", "as": "sec" }, { "from": "perf", "as": "perf" }],
              "checks": [{ "run": "always-pass", "on_fail": "iterate" }] }
          ]
        }
      ],
      "next": "join-preflight"
    },
    { "id": "join-preflight", "kind": "join", "of": "preflight", "next": "done" }
  ]
}
```

> Note: `join-analyze` lives **inside** branch `deep`'s `states` (a sequence) — a join scoped to a nested fanout. `paths.next_sibling` after `analyze` returns `join-analyze`; after it, `report`. This is the structural novelty Stage 3 must handle.

- [ ] **Step 2: Create the check + schema** (copy `always-pass.py`; write a permissive `leaf.evidence.schema.json`). Run `python3 tests/fixtures/deep-fanout/checks/always-pass.py /dev/null /dev/null /dev/null` → prints a JSON verdict, exits 0.

- [ ] **Step 3: Assert the fixture parses + depth** 

```python
# tests/test_deep_fanout_e2e.py
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import paths
def test_deep_fixture_depth_is_4():
    p = json.load(open(ROOT / "tests/fixtures/deep-fanout/protocol.json"))
    assert paths.max_static_depth(p) == 4
    assert paths.node_kind(p, ["preflight", "deep", "analyze"]) == "fanout"
    assert paths.next_sibling(p, ["preflight", "deep", "analyze"]) == "join-analyze"
```

- [ ] **Step 4: Run** `pytest tests/test_deep_fanout_e2e.py::test_deep_fixture_depth_is_4 -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/deep-fanout tests/test_deep_fanout_e2e.py
git commit -m "test(fixture): deep-fanout depth-4 protocol (nested fanout in a sub-pipeline)"
```

## Task 11: Planner emits `run-fanout` for a continued nested fanout

**Files:**
- Modify: `.github/agent-factory/engine/next.py` — in the continue path, before single-agent resolution, if the dispatched path points at a fanout, call `enter_node(proto, path, "continue")` and exit.
- Test: `tests/test_deep_fanout_e2e.py`.

**Interfaces:** the continue path now accepts a `PATH` env var (dot-joined). For Stages 1–3 the test harness sets `PATH`/`BRANCH`/`SUBSTATE`; the canonical is `PATH`. When `PATH` resolves to a fanout node → emit its matrix.

- [ ] **Step 1: Write failing test** — continue at `preflight.deep.analyze` emits a run-fanout for sec+perf

```python
def test_continue_at_nested_fanout_emits_matrix(engine_env, tmp_path):
    import subprocess, json
    sd = tmp_path / "state"; sd.mkdir()
    proto = ROOT / "tests/fixtures/deep-fanout/protocol.json"
    # seed the top fanout + drive 'deep' to the analyze cursor first via enter (helper),
    # then: continue with PATH=preflight.deep.analyze
    e = dict(engine_env); e["NODE_PATH"] = "preflight.deep.analyze"
    r = subprocess.run(["python3", str(ROOT/".github/agent-factory/engine/next.py"),
                        str(sd), "pr-1", str(proto), "continue"],
                       text=True, capture_output=True, env=e)
    act = json.loads(r.stdout)
    assert act["action"] == "run-fanout"
    assert {l["path"] for l in act["legs"]} == {"preflight.deep.analyze.sec", "preflight.deep.analyze.perf"}
```

> Use env var name `NODE_PATH` (not `PATH` — `PATH` is the OS executable path and must not be shadowed). Update `run_engine`/ctx accordingly. **This supersedes the spec's prose "path" env var: the concrete name is `NODE_PATH`.**

- [ ] **Step 2: Run, verify fail** → `action` is not `run-fanout` (falls through to single-agent resolution / error).

- [ ] **Step 3: Implement** — near the top of `next.py` (after the command dispatch, before `resolve_agent_unit`), add:

```python
NODE_PATH = os.environ.get("NODE_PATH", "")
if COMMAND == "continue" and NODE_PATH:
    _p = NODE_PATH.split(".")
    if paths.is_fanout(proto_data, _p):
        enter_node(proto_data, _p, "continue")
        sys.exit(0)
```

(Keep the legacy `BRANCH`/`PHASE`/`SUBSTATE` resolution intact for the flat/leaf continue.)

- [ ] **Step 4: Run** `pytest tests/test_deep_fanout_e2e.py -v && pytest tests/ -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_deep_fanout_e2e.py
git commit -m "feat(engine): planner emits run-fanout for a continued nested fanout path"
```

## Task 12: `advance.py` re-dispatches a nested fanout; bubbling join

**Files:**
- Modify: `.github/agent-factory/engine/advance.py` — when a sub-state's `next_sibling` is a fanout, re-dispatch `protocol-continue` with `client_payload[path]=<fanout path>` (so Task 11's planner enters it) instead of seeding an agent file.
- Modify: `.github/agent-factory/engine/join.py` — on all-done for a NON-root fanout (its path length > the top phase), call `complete_sequence` to advance the enclosing sequence (e.g. `join-analyze` → `report`), and when a nested fanout is the last child, fire the parent fanout's join.
- Test: `tests/test_deep_fanout_e2e.py`, `tests/test_recursive_join.py`.

**Interfaces:**
- `complete_sequence(proto, dir_, pid, instance, seq_path, ok, sha, github_repository)` now handles three enclosing-scope cases: (a) parent is a **sub-pipeline branch** with a following sibling → seed+dispatch the sibling; (b) parent sequence ended and is itself a **fanout branch** → set the branch-cursor terminal + `fire_join` for that fanout's path; (c) parent is the **top sequence** → advance instance phase (today's path).

- [ ] **Step 1: Write failing test** — full depth-4 walk to done

```python
def test_deep_fanout_walks_to_done(engine_env, tmp_path):
    # 1. start → preflight fanout (quick flat ∥ deep sub-pipeline)
    # 2. advance quick → leg done; advance deep/triage → next sibling 'analyze' is a
    #    fanout → advance re-dispatches protocol-continue path=preflight.deep.analyze
    # 3. enter analyze → sec ∥ perf; advance both → join-analyze fires
    # 4. join (analyze) → complete_sequence advances 'deep' to 'report'
    # 5. advance report → 'deep' sequence ends → branch 'deep' leg done → join-preflight
    # 6. join-preflight all done → instance done
    # Assert: _instance.yaml joined True; preflight.__join.yaml joined True;
    #         preflight.deep.analyze.__join.yaml joined True.
```

> Implementer note: drive `next.py`/`advance.py`/`join.py` as subprocesses with `NODE_PATH` set per leg, using the always-pass check so every leg goes `done` in one iteration. Assert the three join markers and the cursor files at each step. This is the keystone test of the whole feature — write the steps explicitly following the numbered walk.

- [ ] **Step 2: Run, verify fail** (the analyze→report bubble and the nested re-dispatch don't exist yet).

- [ ] **Step 3: Implement** the nested-fanout re-dispatch in `advance.py` and the bubbling cases in `join.py`/`complete_sequence`. Key rule: `join.py` computes its fanout path from the dispatch (`NODE_PATH` of the fanout, or the cursor); on all-done it calls `complete_sequence(parent_path(fanout_path), ok=True)`; `complete_sequence` uses `paths.next_sibling`/`paths.node_kind` to pick case a/b/c above.

- [ ] **Step 4: Run** `pytest tests/test_deep_fanout_e2e.py -v && pytest tests/ -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/advance.py .github/agent-factory/engine/join.py tests/test_deep_fanout_e2e.py
git commit -m "feat(engine): nested-fanout re-dispatch + recursive join bubbling (depth 4)"
```

## Task 13: `max_depth` guard

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` — `DEFAULT_MAX_DEPTH = 4`; `effective_max_depth(proto)`; `check_depth(proto)` raising `ValueError` when `paths.max_static_depth(proto) > effective_max_depth(proto)`.
- Modify: `.github/agent-factory/engine/next.py` — call `lib.check_depth(proto_data)` immediately after loading the protocol (before any seed); on failure print a clear error + exit 2.
- Create: `tests/fixtures/too-deep/protocol.json` (depth-5, no `max_depth` override).
- Test: `tests/test_max_depth.py`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_max_depth.py
import json, pathlib, subprocess, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import lib

def test_default_cap_rejects_depth5():
    p = json.load(open(ROOT / "tests/fixtures/too-deep/protocol.json"))
    import pytest
    with pytest.raises(ValueError):
        lib.check_depth(p)

def test_explicit_max_depth_allows_depth5():
    p = json.load(open(ROOT / "tests/fixtures/too-deep/protocol.json"))
    p["max_depth"] = 5
    lib.check_depth(p)  # no raise

def test_next_refuses_too_deep(engine_env, tmp_path):
    proto = ROOT / "tests/fixtures/too-deep/protocol.json"
    r = subprocess.run(["python3", str(ROOT/".github/agent-factory/engine/next.py"),
                        str(tmp_path), "pr-1", str(proto), "start"],
                       text=True, capture_output=True, env=engine_env)
    assert r.returncode == 2
    assert "max_depth" in r.stderr or "too deep" in r.stderr.lower()
```

- [ ] **Step 2: Create `too-deep` fixture** — a `deep-fanout` clone where `sec` is replaced by a sub-pipeline (adds a 5th segment), and **no** `max_depth` field.

- [ ] **Step 3: Run, verify fail** → `module 'lib' has no attribute 'check_depth'`.

- [ ] **Step 4: Implement**

```python
DEFAULT_MAX_DEPTH = 4

def effective_max_depth(proto):
    v = proto.get("max_depth")
    return int(v) if isinstance(v, int) else DEFAULT_MAX_DEPTH

def check_depth(proto):
    d = _paths.max_static_depth(proto)
    cap = effective_max_depth(proto)
    if d > cap:
        raise ValueError(f"protocol depth {d} exceeds max_depth {cap}")
```

In `next.py`, after `proto_data = json.load(...)`:

```python
try:
    lib.check_depth(proto_data)
except ValueError as _e:
    sys.stderr.write(f"[next] {_e}\n")
    sys.exit(2)
```

- [ ] **Step 5: Run + commit**

Run: `pytest tests/test_max_depth.py -v && pytest tests/ -q` → PASS.

```bash
git add .github/agent-factory/engine/lib.py .github/agent-factory/engine/next.py tests/fixtures/too-deep tests/test_max_depth.py
git commit -m "feat(engine): configurable max_depth guard (default 4)"
```

## Task 14: Deep `inputs` + nested-gate `/answer` coverage

**Files:**
- Test only: `tests/test_deep_fanout_e2e.py` (append) — assert `report`'s `inputs:[sec,perf]` resolve to the persisted nested-leg evidence; and (using a variant fixture or an added gate) that `/answer` finds a gate nested at depth 4.
- Modify (only if a gap surfaces): `lib.resolve_inputs` to walk up the path for cross-scope `from` ids.

**Interfaces:** `resolve_inputs` resolution order becomes path-relative (sibling in same sequence → sibling branch leg-output → walk up).

- [ ] **Step 1: Write failing test** — `report` inputs resolve

```python
def test_report_inputs_resolve_nested_legs(engine_env, tmp_path):
    # After sec/perf legs persist evidence at
    #   preflight.deep.analyze.sec.evidence.json / ...perf.evidence.json
    # emit_run_agent for report must include inputs mapping as=sec/perf to those paths.
```

- [ ] **Step 2: Run, verify fail** (resolution returns empty / wrong paths if scope-walk missing).

- [ ] **Step 3: Implement** the path-relative resolution in `resolve_inputs` (extend the existing function to accept a consuming `path` and search: same-sequence earlier siblings, then sibling branches under the enclosing fanout, then outward).

- [ ] **Step 4: Run** `pytest tests/test_deep_fanout_e2e.py -v && pytest tests/ -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_deep_fanout_e2e.py
git commit -m "feat(engine): path-relative inputs resolution across nested scopes"
```

## Task 15: Stage-3 final gate + cross-protocol isolation test

**Files:**
- Test: `tests/test_recursive_join.py` (append) — cross-protocol non-overlap.

- [ ] **Step 1: Write the isolation test**

```python
def test_two_protocols_seed_into_disjoint_paths(engine_env, tmp_path):
    import subprocess
    sd = tmp_path / "state"; sd.mkdir()
    for proto, inst in (("subpipeline-mini", "pr-1"), ("multiphase-subpipeline", "pr-1")):
        p = ROOT / f"tests/fixtures/{proto}/protocol.json"
        subprocess.run(["python3", str(ROOT/".github/agent-factory/engine/next.py"),
                        str(sd), inst, str(p), "start"], env=engine_env, capture_output=True)
    a = {x.name for x in (sd / "subpipeline-mini" / "pr-1").iterdir()}
    b = {x.name for x in (sd / "multiphase-subpipeline" / "pr-1").iterdir()}
    # different protocol dirs → no path can collide
    assert (sd / "subpipeline-mini").exists() and (sd / "multiphase-subpipeline").exists()
    assert a and b  # both seeded independently
```

- [ ] **Step 2: Run full suite**

Run: `pytest tests/ -q`
Expected: PASS — all modules green (existing + `test_paths`, `test_cas`, `test_recursive_sequencer`, `test_recursive_join`, `test_max_depth`, `test_deep_fanout_e2e`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_recursive_join.py
git commit -m "test(engine): cross-protocol path isolation; Stage 3 complete (recursive engine)"
```

---

## Stage 4 (separate follow-on plan, written after Stage 3 lands)

Not in this plan — to be planned against the stabilized engine API:
- `agentic-engine.yml`: matrix axis `leg: {path}`; `ctx` parses `NODE_PATH`; re-dispatch payloads carry `client_payload[path]`; artifact names key on a path-slug.
- `agentic-orchestrator.yml`: concurrency group `agentic-<instance>-<dot-path>`; `protocol-join.yml` group `join-<instance>-<fanout-path>`.
- The live `deep-review-stub` protocol + gh-aw agents (`*-agent.md` → compiled `.lock.yml`).
- Live PR end-to-end verification (the depth-4 walk on a throwaway PR), in the spirit of the PR #82 verification that caught two live-only bugs.

---

## Self-Review

**Spec coverage:**
- Node-path coordinate → Tasks 1–3. State-file scheme unchanged → Task 2. Tree-nav helpers → Task 1. `resolve_agent_unit`/`life_state` generalization → Tasks 1, 3. Recursive `enter_node`/`advance_node`/`complete_sequence` → Tasks 5, 6, 12. Planner emits non-top fanout → Task 11. Recursive join (path-keyed markers + bubbling) → Tasks 7, 12. Gates/`/answer`/inputs on a path → Tasks 8, 14. `max_depth` guard → Task 13. Concurrency: path-keyed join files → Task 7; `cas_push` retry → Task 4; concurrency group keys → Stage 4 (GHA, deferred). Migrate-first staging → Stages 1–2 + Task 9 gate. Deep live protocol + GHA wiring → Stage 4 (deferred, by design). Testing matrix → all tasks + Tasks 9, 15.
- **Deferred-by-design (not gaps):** concurrency-group key changes and the live protocol are GHA-layer work, correctly in the Stage-4 follow-on; they cannot be unit-tested and depend on the final engine API.

**Placeholder scan:** Test bodies in Tasks 6, 8, 12, 14 intentionally reference "lift the driver from `test_subpipeline.py`/`test_gate_data.py`" rather than re-pasting another fixture's verdict/evidence plumbing — this is a deliberate DRY pointer to existing, in-repo code (not an unwritten step), with the exact assertion stated. The keystone walk (Task 12) lists its six numbered transitions explicitly. No `TBD`/`TODO`/"add error handling".

**Type consistency:** `node_at_path`, `node_kind`, `parent_path`, `next_sibling`, `first_child_id`, `enclosing_fanout_id`, `max_static_depth`, `path_depth` used consistently across Tasks 1/3/5/11/13. `enter_node(proto, path, command, emit=)`, `_seed_child`, `complete_sequence`, `advance_node`, `join_marker_file`/`read_join`/`write_join`, `check_depth`/`effective_max_depth`/`DEFAULT_MAX_DEPTH`, `resolve_agent_unit_path` consistent across their defining and consuming tasks. Env var is `NODE_PATH` everywhere (Task 11 fixes the spec's generic "path" to avoid shadowing OS `PATH`).
