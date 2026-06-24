# Stage 4a — Recursive Engine Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the engine's two code paths (legacy phase machinery + recursive `NODE_PATH` sequencer) into one recursive walk where the protocol root is a sequence node, retiring `protocol-advance`; prove it with a capability test-suite driven entirely offline via `NODE_PATH`.

**Architecture:** A top-level phase becomes a depth-1 tree path (`["review"]`). Entry, phase transitions, the top-level join, the approval gate, and merge/combine all route through the existing recursive `enter_node`/`advance_node`/`complete_sequence`/`_nested_join` sequencer. The root sequence's cursor stays in `_instance.yaml.phase`; nested cursors keep their `<seq>.yaml` files. A phase transition becomes "continue at the next sibling path" (`dispatch_continue(path=…)`), so the `protocol-advance` dispatch type is deleted.

**Tech Stack:** Python 3 + PyYAML (runtime); pytest (dev-only). No new dependencies. Engine lives in `.github/agent-factory/engine/`.

**Execution discipline (IMPORTANT):** The refactor is INCREMENTAL — the full pytest suite stays green after every task. `protocol-advance` and the other legacy mechanisms are removed only in the final refactor task (Task 8), after the unified path is proven. Each behavior-changing task updates the existing tests that assert the old behavior IN THE SAME task. Tasks that are pure refactors (no observable change) keep the suite green and add a characterization test.

**Scope:** This plan is **Stage 4a only** (engine + pytest). It produces working, fully pytest-verified software on its own. **Stage 4b** (GitHub Actions wiring to the `NODE_PATH` axis) and **Stage 4c** (live `deep-review-stub` protocol + live PR verification of deep/code-review/recover) are separate follow-on plans, authored after 4a lands and the emitted-action shapes (`legs[]`, `dispatch_continue` payloads) are stable. See `docs/superpowers/specs/2026-06-23-stage4-recursive-engine-unification-design.md` §8.

## Global Constraints

- **Engine is generic.** No protocol-specific logic in `.github/agent-factory/engine/`. Protocol id, state path, checks, publish hooks derived from `protocol.json` / the protocol directory. (CLAUDE.md "engine vs protocol".)
- **State advances only by fast-forward CAS push** (`lib.cas_push`). Never force-push `agentic-state`. Sole writer of non-initial state: `advance.py` (+ `join.py` for the barrier).
- **`NODE_PATH` is the OS-shadow-safe coordinate name** (never `PATH`). The dot-joined TREE path is rooted at the first top-level node id; node ids must not contain `.`.
- **TWO path notions** (`.superpowers/sdd/PATH-CONVENTIONS.md` — READ IT): TREE-nav path (rooted at top node id) vs FILE-naming path (`lib.state_path(proto, tree)` drops the leading id when SINGLE-PHASE). The walker carries TREE paths and converts at every file call via `lib.state_path`.
- **Security:** agent-derived strings (answer body, feedback, verdicts, filenames, `client_payload[path]`) never interpolated into a shell `run:` block — `env:`/argv-as-data only.
- **No byte-identity goal.** Legacy byte-identity fixtures/tests are removed (Task 16). Oracles: `code-review`, `recover-mental-model-stub`, `deep-fanout`, `gate-deep`, plus re-added single-agent / simple-fanout capability fixtures.
- **Release bar:** clear, actionable authoring-error messages (include the offending node path/id); robust handling of malformed `protocol.json`.
- **Tests are pytest** under `tests/` using `tests/conftest.py` fixtures (`engine_env`, `run_engine`, `run_check`, `read_state_yaml`; bare git origin for `agentic-state`; `ENGINE_LOCAL=1`). Run `pytest tests/ -q`. The suite must be GREEN at every task commit.
- **Confirmed protocol shapes** (read each protocol.json; do not re-derive): code-review = `preflight`(agent) → `review`(fanout: `grumpy`,`security`) → `join`(`next: approval`) → `approval`(gate). recover = `recover`(fanout) → `join`(`next: combine`) → `combine`(merge). deep-fanout = `preflight`(fanout: `quick` ∥ `deep`[sub-pipeline]) → `join-preflight`(`next: done`), single-phase.

## Key `lib.py` helpers (verified signatures)

- `state_file(d, pid, instance, branch=None, phase=None, substate=None, path=None)`; `state_path(proto, tree_path)`; `instance_file(d, pid, instance)`; `output_artifact_path(...)`; `join_marker_file`/`read_join`/`write_join`
- `state_by_id(proto, id)`; `_fanout_state(proto)`; `next_phase_id(proto, phase_id)`; `is_multiphase(proto)`; `phase_states(proto)`
- `resolve_agent_unit_path(proto, tree_path)` → `{agent_state, max_iterations, life_state}`
- `open_gate(dir_, pid, instance, proto_path, gate_id, sha, pr, branch=None, questions=None, phase=None)`
- `dispatch_continue(pid, instance, branch=None, substate=None, phase="", path=None)`; `fire_join_dispatch(pid, instance, fanout_path="")`; `run_merge_hook(dir_, pid, instance, proto_path, merge_state)`
- `ensure_phase_label`, `apply_setup_label`, `remove_pr_label`, `set_check_run`, `cas_push`
- `render_pipeline_status_body`, `render_instance_status_body`, `upsert_status_comment`, `finalize_superseded_comment`, `ensure_status_comment`
- `paths`: `node_at_path`, `node_kind`, `children`, `first_child_id`, `next_sibling`, `parent_path`, `enclosing_fanout_id`, `enclosing_fanout_path`, `path_depth`, `max_static_depth`, `root_ids`, `is_root_child`

## File structure

| File | Responsibility | Change |
|---|---|---|
| `engine/paths.py` | pure tree nav | + `root_ids` / `is_root_child` |
| `engine/next.py` | planner | unify entry (`enter_root`); depth-1 continue for gate/merge; gate/override tails → path-continue; delete `start_fanout`/`seed_and_dispatch_phase`/`advance-phase`/single-agent |
| `engine/advance.py` | sole writer | agent-phase clear → root cursor + `dispatch_continue(path)`; delete `protocol-advance` fire + legacy coord block |
| `engine/join.py` | barrier | top join → advance enclosing-seq cursor to `.next` via path-continue; fold mode-2/3/gate tails into recursive `continue` |
| `tests/test_unified_*.py` | NEW oracle + capability tests | the release suite |
| `tests/fixtures/{single-agent,simple-fanout}/` | re-added capability fixtures | recreated under new engine |
| legacy fixtures/tests | removed (Task 16) | `fanout-mini`,`pipeline-mini`,`multiphase-subpipeline`,`subpipeline-mini` + byte-identity tests |
| `docs/STATUS.md` | status | updated (Task 17) |

---

## Phase A — Coordinate groundwork

### Task 1: Root-child predicate in `paths.py`

**Files:**
- Modify: `.github/agent-factory/engine/paths.py`
- Test: `tests/test_paths.py` (append)

**Interfaces:**
- Produces: `paths.root_ids(proto) -> list[str]`; `paths.is_root_child(proto, path) -> bool` (True iff `len(path)==1` and `path[0]` is a top-level node id).
- Consumes: existing `paths._root_children`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paths.py (append)
def test_root_ids_lists_top_level_phases():
    import json, pathlib, sys
    ROOT = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
    import paths
    CR = json.load(open(ROOT / ".github/agent-factory/protocols/code-review/protocol.json"))
    assert paths.root_ids(CR) == [s["id"] for s in CR["states"]]
    assert paths.is_root_child(CR, ["preflight"]) is True
    assert paths.is_root_child(CR, ["review", "grumpy"]) is False
    assert paths.is_root_child(CR, ["nonesuch"]) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_paths.py -k root_ids -v`
Expected: FAIL — `AttributeError: module 'paths' has no attribute 'root_ids'`

- [ ] **Step 3: Implement**

```python
# paths.py (append after node_at_path)
def root_ids(proto):
    return [c["id"] for c in _root_children(proto)]

def is_root_child(proto, path):
    return len(path) == 1 and path[0] in root_ids(proto)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_paths.py -k root_ids -v` → PASS. Then `pytest tests/ -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/paths.py tests/test_paths.py
git commit -m "feat(paths): root-child predicate for root-as-sequence walk"
```

---

## Phase B — Incremental refactor (suite green every task; protocol-advance deleted in Task 8)

### Task 2: Unified `enter_root` entry (behavior-preserving)

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (entry blocks ~700–776; `start_fanout` ~170–191; `seed_and_dispatch_phase` first-phase arm ~194–284 — leave the function in place for now, callers removed in Task 8)
- Test: `tests/test_unified_entry.py` (new)

**Interfaces:**
- Produces: `enter_root(command, head_sha)` — seeds the first top-level node via `enter_node(proto, [first_id], command, emit=False)`, creates `_instance.yaml` with `phase=<first id>`, applies setup+phase labels, performs the reset wipe on a fresh `start`/`reset`, `cas_push`, then emits the node's action via a shared `_emit_for_node(path, branches)` helper. Routed by `if COMMAND in ("start","reset") and not NODE_PATH`.
- Consumes: `paths.root_ids`, `enter_node`, `lib.ensure_phase_label`, `lib.apply_setup_label`, the existing reset-wipe body.

- [ ] **Step 1: Write the characterization test (must pass before AND after)**

```python
# tests/test_unified_entry.py
import json, subprocess, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT/".github/agent-factory/engine"
def _yaml(p):
    import yaml; return yaml.safe_load(open(p))
def _rc(engine_env, tmp_path, pid, tag):
    d = tmp_path/f"rc-{tag}"
    subprocess.run(["git","clone","-q","-b","agentic-state",
                    engine_env["STATE_REMOTE"],str(d)],check=True)
    return d/pid/"pr-1"
def _start(engine_env, tmp_path, proto, sha="s1"):
    r = subprocess.run(["python3",str(ENG/"next.py"),str(tmp_path/"s"),"pr-1",
                        str(proto),"start",sha],text=True,capture_output=True,env=engine_env)
    assert r.returncode==0, r.stderr
    return json.loads(r.stdout)

def test_start_codereview_seeds_first_phase(engine_env, tmp_path):
    proto = ROOT/".github/agent-factory/protocols/code-review/protocol.json"
    act = _start(engine_env, tmp_path, proto)
    assert act["action"]=="run-agent"           # preflight is an agent phase
    assert _yaml(_rc(engine_env,tmp_path,"code-review","cr")/"_instance.yaml")["phase"]=="preflight"

def test_start_deepfanout_seeds_fanout(engine_env, tmp_path):
    proto = ROOT/"tests/fixtures/deep-fanout/protocol.json"
    act = _start(engine_env, tmp_path, proto)
    assert act["action"]=="run-fanout"
    assert {l["path"] for l in act["legs"]}=={"preflight.quick","preflight.deep"}
```

- [ ] **Step 2: Run to verify current behavior**

Run: `pytest tests/test_unified_entry.py -v`
Expected: PASS today (current entry already seeds these). This is the safety net for the refactor.

- [ ] **Step 3: Implement `enter_root` + `_emit_for_node`**

Extract the reset-wipe block from `seed_and_dispatch_phase`'s `reset_instance=True` body into `_reset_wipe(inf, inst_dir, prev, pr)` (superseded-comment via `finalize_superseded_comment`, `remove_pr_label`, wipe instance dir, refresh head). Then:

```python
# next.py
def _emit_for_node(path, branches):
    kind = paths.node_kind(proto_data, path)
    if kind == "fanout":
        print(json.dumps(_fanout_action(proto_data, path, branches))); return
    if kind == "gate":
        print(json.dumps({"action":"noop","iteration":0,"feedback":"",
                          "reason":f"gate-open:{path[-1]}"})); return
    node = paths.node_at_path(proto_data, path)
    act = {"action":"run-agent","iteration":1,"feedback":"","reason":f"phase:{path[-1]}",
           "workflow": node.get("workflow")}
    if lib.is_multiphase(proto_data):
        act["phase"] = path[-1]
    print(json.dumps(act))

def enter_root(command, head_sha):
    first = paths.root_ids(proto_data)[0]
    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    inf = lib.instance_file(DIR, PID, INSTANCE)
    inst_dir = os.path.dirname(inf); os.makedirs(inst_dir, exist_ok=True)
    prev = lib.load_yaml(inf) if os.path.isfile(inf) else {}
    _reset_wipe(inf, inst_dir, prev, pr)
    lib.apply_setup_label(proto_data, pr)
    lib.dump_yaml(inf, {"protocol": PID, "instance": INSTANCE,
                        "head_sha": head_sha, "phase": first, "joined": False})
    lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, first)
    branches = enter_node(proto_data, [first], command, emit=False)
    lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter root phase {first} ({command})")
    _emit_for_node([first], branches)
```

Replace BOTH old entry guards (`is_multiphase and not PHASE and not BRANCH` block AND `not BRANCH and is_fanout and not PHASE` block) with:

```python
if COMMAND in ("start", "reset") and not NODE_PATH:
    enter_root(COMMAND, HEAD_SHA)
    sys.exit(0)
```

> Note: `enter_node`'s fanout arm writes `__join.yaml` only for `len(path)>1`; a depth-1 fanout phase relies on `_instance.yaml.joined` (set above) — correct, no change needed.

- [ ] **Step 4: Run**

Run: `pytest tests/test_unified_entry.py tests/test_deep_fanout_e2e.py tests/test_recover_mental_model.py tests/test_multiphase.py -v` then `pytest tests/ -q`
Expected: ALL PASS (behavior preserved). Fix `_emit_for_node` until green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_unified_entry.py
git commit -m "feat(next): unified enter_root replaces start_fanout + seed_and_dispatch_phase first-phase"
```

### Task 3: Depth-1 fanout-phase `continue`

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (the `continue`-at-`NODE_PATH` fanout arm ~722–733)
- Test: `tests/test_unified_entry.py` (append)

**Interfaces:**
- Produces: `continue` with `NODE_PATH=<depth-1 fanout phase>` (e.g. `review`) seeds the phase's child legs and emits run-fanout with `legs`, using the `_instance.yaml` marker (no `__join.yaml`).
- Consumes: existing `enter_node` fanout arm, `_fanout_action`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_unified_entry.py (append)
def test_continue_review_phase_emits_fanout_legs(engine_env, tmp_path):
    proto = ROOT/".github/agent-factory/protocols/code-review/protocol.json"
    _start(engine_env, tmp_path, proto)                       # seeds _instance(phase=preflight)
    e = dict(engine_env); e["NODE_PATH"]="review"
    r = subprocess.run(["python3",str(ENG/"next.py"),str(tmp_path/"c"),"pr-1",
                        str(proto),"continue"],text=True,capture_output=True,env=e)
    assert r.returncode==0, r.stderr
    act = json.loads(r.stdout)
    assert act["action"]=="run-fanout"
    assert {l["path"] for l in act["legs"]}=={"review.grumpy","review.security"}
    fdir = _rc(engine_env,tmp_path,"code-review","rev")
    assert (fdir/"review.grumpy.yaml").is_file() and (fdir/"review.security.yaml").is_file()
    assert not (fdir/"review.__join.yaml").is_file()          # depth-1 uses _instance marker
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_unified_entry.py -k continue_review -v`
Expected: FAIL (today a multi-phase fanout phase is entered via `protocol-advance`/`seed_and_dispatch_phase`, not a depth-1 `NODE_PATH` continue; the file/marker assertions fail).

- [ ] **Step 3: Implement**

The `continue`-at-`NODE_PATH` fanout arm already calls `enter_node(...emit=False)` → `cas_push` → `_fanout_action`. Ensure it also fires for a depth-1 path (it does — `node_kind=="fanout"`). The only requirement is that `enter_node`'s fanout arm not write `__join.yaml` at `len==1` (already gated). Reuse `_emit_for_node` if convenient. If the arm currently special-cases nested-only, generalize it to any fanout path.

- [ ] **Step 4: Run** — `pytest tests/test_unified_entry.py -v` PASS; `pytest tests/test_deep_fanout_e2e.py -q` green; `pytest tests/ -q` green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_unified_entry.py
git commit -m "feat(next): depth-1 fanout-phase continue (NODE_PATH) uses _instance marker"
```

### Task 4: `advance.py` agent-phase clear → root cursor + path-continue

**Files:**
- Modify: `.github/agent-factory/engine/advance.py` (agent-phase-clear block ~654–687; iterate re-dispatch keeps working)
- Modify: `tests/test_phase_relay.py` and/or `tests/test_multiphase.py` (update assertions from `protocol-advance` to `protocol-continue`+path)
- Test: `tests/test_unified_advance.py` (new)

**Interfaces:**
- Produces: when a depth-1 AGENT phase clears with a next sibling, set `_instance.yaml.phase = next` and `lib.dispatch_continue(pid, instance, path=next)` — NO `protocol-advance`. No-next → finalize aggregate (unchanged). Pre-flight-gate block (`on_blocked:halt` → `halted` marker) retained; only its clear-tail uses path-continue.
- Consumes: `paths.next_sibling(proto, [phase])`, `paths.is_root_child`, `lib.dispatch_continue(path=…)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_unified_advance.py
import json, subprocess, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT/".github/agent-factory/engine"
PROTO = ROOT/".github/agent-factory/protocols/code-review/protocol.json"
def _yaml(p):
    import yaml; return yaml.safe_load(open(p))
def _rc(engine_env, tmp_path, tag):
    d=tmp_path/f"rc-{tag}"
    subprocess.run(["git","clone","-q","-b","agentic-state",
                    engine_env["STATE_REMOTE"],str(d)],check=True)
    return d/"code-review"/"pr-1"
def test_preflight_clear_advances_via_path_continue(engine_env, tmp_path):
    base=dict(engine_env); base["PR_HEAD_SHA"]="s1"; base["AGENT_RUN_ID"]="r"
    subprocess.run(["python3",str(ENG/"next.py"),str(tmp_path/"s"),"pr-1",str(PROTO),
                    "start","s1"],text=True,capture_output=True,env=base,check=True)
    v=tmp_path/"v.json"; v.write_text(json.dumps({"results":[
        {"check":"x","pass":True,"feedback":"","on_fail":"iterate"}]}))
    ev=tmp_path/"e.json"; ev.write_text("{}")
    e=dict(base); e["NODE_PATH"]="preflight"
    r=subprocess.run(["python3",str(ENG/"advance.py"),str(tmp_path/"a"),"pr-1",str(PROTO),
                      str(v),str(ev)],text=True,capture_output=True,env=e)
    assert r.returncode==0, r.stderr
    assert "event_type=protocol-continue" in r.stderr
    assert "client_payload[path]=review" in r.stderr
    assert "protocol-advance" not in r.stderr
    assert _yaml(_rc(engine_env,tmp_path,"pf")/"_instance.yaml")["phase"]=="review"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_unified_advance.py -v`
Expected: FAIL — today fires `protocol-advance`, not a path-continue.

- [ ] **Step 3: Implement (incremental — DO NOT delete the legacy block yet)**

**Keep the suite green:** many existing tests still invoke `advance.py` with legacy `BRANCH`/`PHASE`/`SUBSTATE` coords (`test_multiphase`, `test_phase_relay`, `test_join`, `test_subpipeline`, `test_recover_mental_model`). Do **NOT** delete the legacy coordinate-derivation `else:` block and do **NOT** make `node_path_env` required in this task — that deletion + the legacy-test migration is **deferred to Task 8**. In this task, change ONLY the **NODE_PATH-mode** `is_agent_phase` clear tail: where it currently fires `protocol-advance`, replace that `gh_api(...)` with the path-continue below. Leave the legacy-mode agent-phase-clear (which still fires `protocol-advance`) untouched so legacy-coord tests stay green.

```python
nxt = _paths.next_sibling(proto, tree_path)   # tree_path is depth-1 (a root phase)
if nxt:
    inst = lib.load_yaml(inf) if os.path.isfile(inf) else {}
    inst["phase"] = nxt
    lib.dump_yaml(inf, inst)
    update_status_comment(sf, inf, branch, pr, pid, instance, proto_path, dir_,
                          "⏳ advancing", max_iter, github_repository)
    lib.ensure_phase_label(dir_, pid, instance, proto, pr, nxt)
    lib.cas_push(dir_, f"{instance}: phase {tree_path[-1]} clear → advancing to {nxt}")
    lib.dispatch_continue(pid, instance, path=nxt)
else:
    # unchanged: aggregate check-run success + done label + cas_push
    ...
```

Derive `is_agent_phase` in NODE_PATH mode as `paths.is_root_child(proto, tree_path) and node_kind(proto, tree_path)=="agent"`. Because the legacy path is untouched, **no existing test changes in this task** — only the new `tests/test_unified_advance.py` is added. (The legacy `protocol-advance` agent-phase-clear and its tests are removed/migrated in Task 8.)

- [ ] **Step 4: Run** — `pytest tests/test_unified_advance.py tests/test_phase_relay.py tests/test_multiphase.py tests/test_conclude_preflight.py -v` PASS (legacy tests still green, new test green); then `pytest tests/ -q` green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/advance.py tests/test_unified_advance.py
git commit -m "feat(advance): NODE_PATH agent-phase clear advances root cursor via path-continue"
```

### Task 5: `join.py` top join → advance to `.next` + continue `merge` arm

**Files:**
- Modify: `.github/agent-factory/engine/join.py` (all-done branch ~211–264; keep `_nested_join` unchanged)
- Modify: `.github/agent-factory/engine/next.py` (add the `merge`-kind arm to the `continue`-at-`NODE_PATH` dispatch)
- Modify: `tests/test_join.py`, `tests/test_merge.py`, `tests/test_recover_mental_model.py` (update old inline-tail assertions to the unified flow)
- Test: `tests/test_unified_join.py` (new)

**Interfaces:**
- Produces: on all-done, the top join sets `_instance.yaml.phase = join_state.next` and `lib.dispatch_continue(pid, instance, path=join_state.next)`. The `continue`-at-`NODE_PATH` dispatch gains a `merge`-kind arm (code in Step 4) that runs `lib.run_merge_hook` + finalizes — so recover's `join → combine(merge)` chain works end-to-end and its tests stay green. (The depth-1 GATE case — code-review `join → approval` — is entered by the continue gate arm that already exists; Task 6 verifies/hardens it.) No-`.next` (or `.next` not a real state, e.g. deep-fanout `done`) → finalize aggregate success (unchanged). Not-all-done → finalize failure (unchanged).
- Consumes: the join state's `.next`, `lib.dispatch_continue(path=…)`, `lib.run_merge_hook`, `lib.render_instance_status_body`.

> **Why the merge arm is HERE (not Task 6):** the join change makes recover's top join dispatch `continue(path=combine)`; without the `merge` arm the merge would be un-run and `test_merge`/`test_recover_mental_model` would break. Folding the merge arm in keeps the suite green within this task. Task 6 covers the depth-1 GATE path + capability.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_unified_join.py
import json, subprocess, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT/".github/agent-factory/engine"
PROTO = ROOT/".github/agent-factory/protocols/code-review/protocol.json"
def _yaml(p):
    import yaml; return yaml.safe_load(open(p))
def _rc(engine_env, tmp_path, tag):
    d=tmp_path/f"rc-{tag}"
    subprocess.run(["git","clone","-q","-b","agentic-state",
                    engine_env["STATE_REMOTE"],str(d)],check=True)
    return d/"code-review"/"pr-1"
def test_top_join_advances_to_approval_via_continue(engine_env, tmp_path):
    base=dict(engine_env); base["PR_HEAD_SHA"]="s1"; base["AGENT_RUN_ID"]="r"
    def run(s,*a,**env):
        e=dict(base); e.update(env)
        r=subprocess.run(["python3",str(ENG/s),*map(str,a)],text=True,capture_output=True,env=e)
        assert r.returncode==0, r.stderr; return r
    v=tmp_path/"v.json"; v.write_text(json.dumps({"results":[
        {"check":"x","pass":True,"feedback":"","on_fail":"iterate"}]}))
    ev=tmp_path/"e.json"; ev.write_text("{}")
    run("next.py",tmp_path/"s","pr-1",PROTO,"start","s1")
    run("advance.py",tmp_path/"a0","pr-1",PROTO,v,ev, NODE_PATH="preflight")  # → review
    run("next.py",tmp_path/"c","pr-1",PROTO,"continue", NODE_PATH="review")   # seed legs
    for leg in ("grumpy","security"):
        run("advance.py",tmp_path/f"a-{leg}","pr-1",PROTO,v,ev, NODE_PATH=f"review.{leg}")
    rj=run("join.py",tmp_path/"j","pr-1",PROTO)
    assert "event_type=protocol-continue" in rj.stderr
    assert "client_payload[path]=approval" in rj.stderr
    assert _yaml(_rc(engine_env,tmp_path,"j")/"_instance.yaml")["phase"]=="approval"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_unified_join.py -v`
Expected: FAIL — today join opens the approval gate inline (bespoke gate tail), does not dispatch a path-continue.

- [ ] **Step 3: Implement**

Replace the all-done bespoke tails (gate-open / agent-combine / merge — ~211–264) with:

```python
join_state = None
fo_id = fanout_state.get("id") if fanout_state else None
for st in protocol.get("states", []):
    if st.get("kind") == "join" and st.get("of") == fo_id:
        join_state = st; break
if join_state is None:
    for st in protocol.get("states", []):
        if st.get("kind") == "join": join_state = st; break
nxt = (join_state or {}).get("next")
if all_done and nxt:
    instance_data["joined"] = True
    instance_data["phase"] = nxt
    lib.dump_yaml(inf, instance_data)
    lib.ensure_phase_label(dir_, pid, instance, protocol, pr, nxt)
    lib.cas_push(dir_, f"{instance}: join clear → continue {nxt}")
    lib.dispatch_continue(pid, instance, path=nxt)
    return
# else fall through to the existing finalize (success no-.next / failure) tail
```

**Guard the `.next`-is-not-a-real-state case:** deep-fanout's `join-preflight` has `next: done`, where `done` is a sentinel, not a state. So gate the advance on the target existing: `if all_done and nxt and lib.state_by_id(proto, nxt):` — otherwise fall through to the finalize tail (aggregate success). Keep the existing aggregate-check-run finalize for the no-`.next`/sentinel and failure cases.

Then add the **`merge` arm** to next.py's `continue`-at-`NODE_PATH` dispatch (so recover's `join → continue(path=combine)` runs the reduce hook + finalizes):

```python
if _kind == "merge":
    node = paths.node_at_path(proto_data, _p)
    res = lib.run_merge_hook(DIR, PID, INSTANCE, PROTO, node)
    inf = lib.instance_file(DIR, PID, INSTANCE)
    inst = lib.load_yaml(inf) if os.path.isfile(inf) else {}
    inst["phase"] = _p[-1]; inst["joined"] = True
    lib.dump_yaml(inf, inst)
    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    lib.set_check_run(PID, HEAD_SHA, "completed", res.get("conclusion","neutral"),
                      "Combined", res.get("summary",""))
    lib.post_pr_comment(pr, f"🧬 **{_p[-1]}**: {res.get('summary','')}")
    lib.upsert_status_comment(inf, pr, lib.render_instance_status_body(DIR, PID, INSTANCE, PROTO))
    lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "done")
    lib.cas_push(DIR, f"{INSTANCE}: merge {_p[-1]} → done")
    print(json.dumps({"action":"noop","iteration":0,"feedback":"","reason":f"merge:{_p[-1]}"}))
    sys.exit(0)
```

Update the affected tests to the unified flow: `tests/test_join.py` (join now dispatches `protocol-continue`+path instead of opening gate / running merge inline); `tests/test_merge.py` and `tests/test_recover_mental_model.py` (the merge runs via `join.py` → `next.py continue NODE_PATH=combine` rather than inline in join.py — drive both steps and assert the merge result). If a recover/merge test is a pure legacy-coord driver superseded by the Task 8 unified oracle, it may be deleted instead of migrated.

- [ ] **Step 4: Run** — `pytest tests/test_unified_join.py tests/test_join.py tests/test_merge.py tests/test_recover_mental_model.py tests/test_deep_fanout_e2e.py -v` PASS; then `pytest tests/ -q` green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/join.py .github/agent-factory/engine/next.py tests/test_unified_join.py tests/test_join.py tests/test_merge.py tests/test_recover_mental_model.py
git commit -m "feat(join+next): top join advances to .next via path-continue; continue merge arm runs reduce hook"
```

### Task 6: Recursive `continue` opens a depth-1 gate (approval)

> The `merge` arm was folded into Task 5 (coupling with the join change). This task confirms+hardens the GATE path: `continue` with `NODE_PATH` at a depth-1 `gate` node (code-review's `approval`) opens the gate at the path `do_resolve_gate` reads, so the join → continue(approval) → `/approve` chain is coherent.

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (the `continue`-at-`NODE_PATH` gate arm ~756–763) — only if a depth-1 gate is not already opened at the path `do_resolve_gate` reads (`lib.state_file(... phase=cursor)`); otherwise this task is test-only.
- Test: `tests/test_unified_continue_kinds.py` (new)

**Interfaces:**
- Produces: `continue` with `NODE_PATH=<depth-1 gate>` opens the gate (gate file at the path `do_resolve_gate` will read; `gates.state == "open"`). The gate arm already exists for nested gates; the deliverable is proving (and fixing if needed) the depth-1 root-gate case.
- Consumes: `lib.open_gate`, `paths.node_at_path`, the existing `enter_node` gate arm.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_unified_continue_kinds.py
import json, subprocess, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT/".github/agent-factory/engine"
def _yaml(p):
    import yaml; return yaml.safe_load(open(p))
def _rc(engine_env, tmp_path, pid, tag):
    d=tmp_path/f"rc-{tag}"
    subprocess.run(["git","clone","-q","-b","agentic-state",
                    engine_env["STATE_REMOTE"],str(d)],check=True)
    return d/pid/"pr-1"

def test_continue_at_approval_gate_opens_it(engine_env, tmp_path):
    proto = ROOT/".github/agent-factory/protocols/code-review/protocol.json"
    base=dict(engine_env); base["PR_HEAD_SHA"]="s1"
    # seed an _instance with phase=approval (simulate post-join cursor)
    subprocess.run(["python3",str(ENG/"next.py"),str(tmp_path/"s"),"pr-1",str(proto),
                    "start","s1"],text=True,capture_output=True,env=base,check=True)
    e=dict(base); e["NODE_PATH"]="approval"
    r=subprocess.run(["python3",str(ENG/"next.py"),str(tmp_path/"c"),"pr-1",str(proto),
                      "continue"],text=True,capture_output=True,env=e)
    assert r.returncode==0, r.stderr
    assert json.loads(r.stdout)["reason"].startswith("gate-open")
    g=_yaml(_rc(engine_env,tmp_path,"code-review","g")/"approval.yaml")
    assert g.get("gates",{}).get("state")=="open"

```

Note: this test seeds via `start` (which lands at `preflight`), then drives `continue NODE_PATH=approval` directly to exercise the depth-1 gate-open in isolation — it does not require the full join walk (that is the Task 8 oracle). The `_instance.yaml.phase` is `preflight` from the `start`; the gate-open arm must still open the `approval` gate file at the path `do_resolve_gate` reads regardless of the cursor value.

- [ ] **Step 2: Run to verify it fails (or passes)**

Run: `pytest tests/test_unified_continue_kinds.py -v`
Expected: the gate arm may already handle this (it was built for nested gates). If it PASSES, this task is the test (a regression guard for the depth-1 root-gate case) + a one-line confirmation in the report. If it FAILS (e.g. the gate file lands at a path `do_resolve_gate` cannot read for a depth-1 multi-phase root gate), fix the gate arm in Step 3.

- [ ] **Step 3: Implement (only if Step 2 failed)**

Ensure the `continue`-at-`NODE_PATH` gate arm opens a depth-1 root gate at the FILE path `do_resolve_gate` reads. `do_resolve_gate` reads `lib.state_file(DIR, PID, INSTANCE, phase=cursor)`; `enter_node`'s gate arm calls `lib.open_gate(..., gate_id, ..., phase=(path[-1] if is_multiphase else None))`. Confirm these resolve to the same file for a depth-1 multi-phase gate (`approval.yaml`). If they diverge, align the gate arm's `phase=`/`path=` so the written file matches `do_resolve_gate`'s read. Add a focused assertion that `do_resolve_gate` (approve) then finds the gate (this is fully exercised in Task 7; here just confirm the file location).

- [ ] **Step 4: Run** — `pytest tests/test_unified_continue_kinds.py tests/test_gate.py -v` PASS; `pytest tests/ -q` green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_unified_continue_kinds.py
git commit -m "test(next): depth-1 root gate-open via continue (regression guard; fix if needed)"
```

### Task 7: Gate resolution + override tails → path-continue

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (`do_resolve_gate` approve arm ~405–429; `do_override` advance arm ~310–335)
- Modify: `tests/test_gate.py`, `tests/test_override.py` (update advance-tail assertions)

**Interfaces:**
- Produces: on `approve` (and `/override` of a blocked gate) with a next sibling, set `_instance.yaml.phase=next` and `lib.dispatch_continue(pid, instance, path=next)` instead of `seed_and_dispatch_phase(nxt, …)`. `request-changes`/`reject` and all refusal/auth semantics unchanged. No-next approve → existing finalize tail.
- Consumes: `paths.next_sibling(proto, [cursor])`, `lib.dispatch_continue(path=…)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_unified_gate_resolve.py
import json, subprocess, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT/".github/agent-factory/engine"
PROTO = ROOT/".github/agent-factory/protocols/code-review/protocol.json"
def _drive_to_gate(engine_env, tmp_path):
    base=dict(engine_env); base["PR_HEAD_SHA"]="s1"; base["AGENT_RUN_ID"]="r"
    def run(s,*a,**env):
        e=dict(base); e.update(env)
        r=subprocess.run(["python3",str(ENG/s),*map(str,a)],text=True,capture_output=True,env=e)
        assert r.returncode==0, r.stderr; return r
    v=tmp_path/"v.json"; v.write_text(json.dumps({"results":[
        {"check":"x","pass":True,"feedback":"","on_fail":"iterate"}]}))
    ev=tmp_path/"e.json"; ev.write_text("{}")
    run("next.py",tmp_path/"s","pr-1",PROTO,"start","s1")
    run("advance.py",tmp_path/"a0","pr-1",PROTO,v,ev,NODE_PATH="preflight")
    run("next.py",tmp_path/"c","pr-1",PROTO,"continue",NODE_PATH="review")
    for leg in ("grumpy","security"):
        run("advance.py",tmp_path/f"a-{leg}","pr-1",PROTO,v,ev,NODE_PATH=f"review.{leg}")
    run("join.py",tmp_path/"j","pr-1",PROTO)
    run("next.py",tmp_path/"cg","pr-1",PROTO,"continue",NODE_PATH="approval")  # opens gate
    return base, run
def test_approve_finalizes_pipeline(engine_env, tmp_path):
    base, run = _drive_to_gate(engine_env, tmp_path)
    r=run("next.py",tmp_path/"ap","pr-1",PROTO,"resolve-gate",
          GATE_DECISION="approve",GATE_ACTOR="alice",GATE_REASON="",GATE_PR_AUTHOR="bob")
    d=tmp_path/"rcz"
    subprocess.run(["git","clone","-q","-b","agentic-state",engine_env["STATE_REMOTE"],str(d)],check=True)
    import yaml
    assert yaml.safe_load(open(d/"code-review"/"pr-1"/"approval.yaml"))["gates"]["state"]=="approved"
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_unified_gate_resolve.py -v` → FAIL (approval is the last phase, so this exercises the no-next finalize; but the drive-to-gate path relies on Tasks 3-6; the approve arm still calls `seed_and_dispatch_phase` which is being removed). Confirm the failure mode is the `seed_and_dispatch_phase` call.

- [ ] **Step 3: Implement** — in `do_resolve_gate` approve arm replace `seed_and_dispatch_phase(nxt, "approve")` with:

```python
inst = lib.load_yaml(inf); inst["phase"] = nxt; lib.dump_yaml(inf, inst)
lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, nxt)
lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} approved by {actor} → continue {nxt}")
lib.dispatch_continue(PID, INSTANCE, path=nxt)
```

with `nxt = paths.next_sibling(proto_data, [cursor])`. Keep the no-next finalize tail. Apply the same substitution in `do_override`'s advance arm (`nxt = paths.next_sibling(proto_data, [blocked_phase])`). Update `tests/test_gate.py` / `tests/test_override.py` advance-tail assertions to `protocol-continue`+path.

- [ ] **Step 4: Run** — `pytest tests/test_unified_gate_resolve.py tests/test_gate.py tests/test_override.py -v` PASS; `pytest tests/ -q` green.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_unified_gate_resolve.py tests/test_gate.py tests/test_override.py
git commit -m "feat(next): gate approve + override advance root cursor via path-continue"
```

### Task 8: Delete legacy machinery + add full e2e oracle walks

**Files:**
- Modify: `.github/agent-factory/engine/next.py`, `advance.py`, `join.py`
- Test: `tests/test_unified_codereview_e2e.py`, `tests/test_unified_recover_e2e.py` (new — the integration oracles, GREEN)

**Interfaces:**
- Produces: removal of `start_fanout`, `seed_and_dispatch_phase`, the `advance-phase` command branch, the **advance.py legacy coordinate-derivation `else:` block** (so `NODE_PATH` becomes required, with a clear error if unset), the single-agent bespoke planner/advancer paths, and any now-dead `lib` helper (`next_phase_id` if unreferenced). `protocol-advance` appears nowhere in the engine. Two full e2e walks prove the unified path end-to-end.
- **Test migration (interlocked with the deletion):** deleting the legacy engine machinery breaks every test that drives the engine via legacy coords (no `NODE_PATH`) — `test_multiphase`, `test_phase_relay`, and the legacy-coord `advance.py` invocations in `test_join` / `test_subpipeline` / `test_recover_mental_model`. Remove or migrate each in THIS task so the suite is green at commit. The replacement coverage is the two unified e2e oracles (added here) plus the kept `NODE_PATH` fixtures (`deep-fanout`, `gate-deep`); granular capability coverage (approval/override/restart) is restored in Tasks 11-13. If this task proves too large in practice, split it (engine-delete + test-migrate) and tell the controller.

- [ ] **Step 1: Write the integration oracle walks (expected GREEN)**

`tests/test_unified_codereview_e2e.py`: the full `_drive_to_gate` walk from Task 7 + approve → assert final `approval.yaml` gates `approved`, aggregate complete, `_instance.joined`. `tests/test_unified_recover_e2e.py`: `start` → advance `summary` leg + drive `rationale` sub-pipeline (`recover.rationale.draft` → gate `/answer` → `recover.rationale.finalize`) → `join` → continue `combine` (merge) → `_instance.joined:true, phase=combine`. Model the `/answer` step on `tests/test_gate_data.py`. Read both protocol.json files for exact sub-state ids.

- [ ] **Step 2: Run to confirm they pass** — `pytest tests/test_unified_codereview_e2e.py tests/test_unified_recover_e2e.py -v` PASS (the unified path is complete after Tasks 2-7).

- [ ] **Step 3: Delete dead code + migrate legacy tests** — remove `start_fanout`, `seed_and_dispatch_phase`, the `advance-phase` branch, the advance.py legacy coord-derivation `else:` block (make `NODE_PATH` required), and the single-agent bespoke paths in `next.py`/`advance.py`. Then remove/migrate the legacy-coord tests named in the Interfaces block so the suite stays green. Confirm:

Run: `grep -rn "protocol-advance\|seed_and_dispatch_phase\|start_fanout\|advance-phase" .github/agent-factory/engine/`
Expected: zero hits.

- [ ] **Step 4: Run full suite** — `pytest tests/ -q` ALL PASS (legacy-coord tests removed/migrated in Step 3; the kept `deep-fanout`/`gate-deep` NODE_PATH walks + the two unified oracles are green).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/ tests/test_unified_codereview_e2e.py tests/test_unified_recover_e2e.py
git commit -m "refactor(engine): delete legacy phase machinery; add full unified e2e oracle walks"
```

---

## Phase C — Capability suite (release bar)

### Task 9: Single-agent capability fixture + walk

**Files:**
- Create: `tests/fixtures/single-agent/protocol.json` (+ minimal leaf schema + an always-pass check), `tests/test_cap_single_agent.py`

**Interfaces:**
- Produces: proof the unified engine handles a one-`agent`-state protocol (root sequence, single child): `start` → run-agent at depth-1; advance pass → aggregate complete; advance fail-loop → exhaust → failed.

- [ ] **Step 1: Write fixture + failing walk test** — protocol `{name: single-agent, states:[{id: solo, kind: agent, workflow: solo-agent, evidence: leaf.evidence.schema.json, max_iterations: 2, checks:[{run: always-pass, on_fail: iterate}]}]}` + permissive `leaf.evidence.schema.json` + `checks/always-pass` (copy from deep-fanout). Test: `start` emits run-agent + seeds; `advance` NODE_PATH=`solo` pass → `_instance` finalized (aggregate success).
- [ ] **Step 2: Run to verify fail** — `pytest tests/test_cap_single_agent.py -v` (fixture absent or no-next root-agent finalize gap).
- [ ] **Step 3: Implement** — ensure `enter_root` + `advance` no-next root-agent finalize works for a single root child (the `else` finalize in Task 4 covers it; add only if a gap shows).
- [ ] **Step 4: Run** — PASS; `pytest tests/ -q` green.
- [ ] **Step 5: Commit** — `git add tests/fixtures/single-agent tests/test_cap_single_agent.py && git commit -m "test(cap): single-agent shape under unified engine"`

### Task 10: Simple-fanout capability fixture + walk

**Files:**
- Create: `tests/fixtures/simple-fanout/protocol.json` (+ reuse leaf schema/check), `tests/test_cap_simple_fanout.py`

**Interfaces:**
- Produces: proof of single-phase fanout(`a` ∥ `b`) → join(`of: f`, `next: done`) → done.

- [ ] **Step 1:** fixture (fanout `f` two flat agent legs; join). **Failing test:** `start` → run-fanout legs {f.a, f.b}; advance both pass → join → aggregate success.
- [ ] **Step 2:** run to verify (should pass with no new engine code — confirms degenerate case).
- [ ] **Step 3:** implement only if a gap shows.
- [ ] **Step 4:** `pytest tests/ -q` green.
- [ ] **Step 5: Commit** — `test(cap): simple fanout shape`

### Task 11: Approval-gate decisions capability

**Files:**
- Create: `tests/test_cap_approval_gate.py`

**Interfaces:**
- Produces: coverage of `approve` (→ done), `request-changes` (→ halt, re-runnable), `reject` (→ failed), self-approve refusal, using the code-review approval gate via the Task 7 `_drive_to_gate` helper (import or duplicate minimally).

- [ ] **Step 1:** drive to the approval gate; exercise each decision in isolation; assert gate `gates.state` + phase label + check-run conclusion + (self-approve) refusal comment.
- [ ] **Step 2:** run to verify fail (where behavior gaps exist).
- [ ] **Step 3:** implement fixes if any.
- [ ] **Step 4:** `pytest tests/ -q` green.
- [ ] **Step 5: Commit** — `test(cap): approval-gate decisions`

### Task 12: Override capability

**Files:**
- Create: `tests/test_cap_override.py` (+ if needed a fixture with an agent phase `on_blocked: halt`)

**Interfaces:**
- Produces: a blocked agent-gate phase → `halted:{reason:blocked}` marker; `/override` (authorized) advances one phase via path-continue; refusal messages for not-halted / exhausted.

- [ ] **Step 1–5:** induce a blocked gate (reuse `tests/test_conclude_preflight.py` setup or a small fixture), assert `halted` marker then `/override` clears it + `dispatch_continue(path=<next>)`; refusal arms. Commit `test(cap): /override blocked gate`.

### Task 13: Restart/reset capability

**Files:**
- Create: `tests/test_cap_restart.py`

**Interfaces:**
- Produces: a second `start`/`reset` mid-pipeline wipes the instance dir, abandons the old status comment (superseded banner via `finalize_superseded_comment`, drops `status_comment_id`), removes the old phase label, refreshes `head_sha`, re-seeds phase one.

- [ ] **Step 1–5:** run to mid-pipeline (e.g. after preflight), re-`start` with a new sha; assert wipe (old leg files gone), fresh `_instance.yaml` (`phase`=first, new head), and the `finalize_superseded_comment` ENGINE_LOCAL call recorded. Commit `test(cap): restart/reset wipe`.

### Task 14: Authoring-error UX + max_depth guard

**Files:**
- Create: `tests/test_cap_authoring_errors.py` (writes tiny malformed protocols to `tmp_path`)
- Modify: `.github/agent-factory/engine/next.py` / `lib.py` (add `lib.validate_protocol(proto)` called at load)

**Interfaces:**
- Produces: exit-2 + actionable messages naming the offending node for: `max_depth` exceeded (reuse `tests/fixtures/too-deep`); join with unknown `of`; agent node missing `workflow`; gate missing source (`questions_from` referencing nothing) — keep the rule set to what the oracles + spec imply, don't over-validate.

- [ ] **Step 1: Write failing tests** — each writes a minimal bad protocol, runs `next.py start`, asserts non-zero exit + message contains the node id + a fix hint.
- [ ] **Step 2: Run to verify fail** (today: opaque traceback / silent).
- [ ] **Step 3: Implement** `lib.validate_protocol` raising `ValueError(f"join '{jid}' references unknown fanout of='{of}'")` etc.; `next.py` calls it right after load (after `check_depth`), maps `ValueError`→stderr+exit 2.
- [ ] **Step 4: Run** PASS; `pytest tests/ -q` green.
- [ ] **Step 5: Commit** — `feat(engine): actionable protocol-authoring validation + error messages`

### Task 15: Security regression

**Files:**
- Create: `tests/test_cap_security.py`

**Interfaces:**
- Produces: assertions that a malicious `ANSWER_BODY` / feedback / `NODE_PATH` is treated as data — `_parse_answers` never executes it; a bogus `NODE_PATH` segment yields a clean error (`node_at_path`→None), no path traversal in `state_path`.

- [ ] **Step 1–5:** feed `"; rm -rf / #"`-style strings through `do_answer` (ANSWER_BODY env) and a `../../etc` `NODE_PATH` to `advance.py`; assert no execution + clean non-zero handling + no file written outside the instance dir. Commit `test(cap): security regressions for agent-derived strings`.

---

## Phase D — Cleanup + status

### Task 16: Remove legacy byte-identity fixtures + tests

**Files:**
- Delete: `tests/fixtures/{fanout-mini,pipeline-mini,multiphase-subpipeline,subpipeline-mini}/`; legacy assertions/modules superseded by the unified suite.

**Interfaces:**
- Produces: a green suite with NO references to retired mechanisms or deleted fixtures.

- [ ] **Step 1:** `grep -rln "fanout-mini\|pipeline-mini\|multiphase-subpipeline\|subpipeline-mini\|protocol-advance" tests/` → for each, delete the fixture/test or rewrite onto a capability fixture (Tasks 9-15 cover the real capabilities). Keep `deep-fanout`, `gate-deep`, `too-deep`.
- [ ] **Step 2: Run full suite** — `pytest tests/ -q` ALL PASS, no skips for retired behavior.
- [ ] **Step 3: Commit** — `test: remove legacy byte-identity fixtures + tests (superseded by capability suite)`

### Task 17: Update `docs/STATUS.md`

**Files:**
- Modify: `docs/STATUS.md`

- [ ] **Step 1:** document the unified recursive engine (root-as-sequence), `protocol-advance` retirement, the single `NODE_PATH` coordinate, the no-in-flight-migration deploy requirement, and that Stage 4b (GHA) / 4c (live) follow.
- [ ] **Step 2: Commit** — `docs(status): recursive engine unification (Stage 4a) — protocol-advance retired`

---

## Self-review checklist (run before the final whole-branch review)

- [ ] `pytest tests/ -q` fully green.
- [ ] `grep -rn "protocol-advance\|start_fanout\|seed_and_dispatch_phase\|advance-phase" .github/agent-factory/engine/` → zero hits.
- [ ] Both e2e oracles (`test_unified_codereview_e2e`, `test_unified_recover_e2e`) pass.
- [ ] `deep-fanout` (depth-4) + `gate-deep` (depth-5) keystones still pass.
- [ ] Capability suite covers: single-agent, simple-fanout, multi-phase, sub-pipeline, depth-4/5, gates (data + approval), override, restart, inputs, merge, max_depth, authoring errors, security.
- [ ] No `NODE_PATH`/answer-body/feedback string reaches an injection point.
- [ ] Suite was green at every task commit (incremental refactor, not a big-bang).
