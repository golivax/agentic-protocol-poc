# Stage 4a — Recursive Engine Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the engine's two code paths (legacy phase machinery + recursive `NODE_PATH` sequencer) into one recursive walk where the protocol root is a sequence node, retiring `protocol-advance`; prove it with a capability test-suite driven entirely offline via `NODE_PATH`.

**Architecture:** A top-level phase becomes a depth-1 tree path (`["review"]`). Entry, phase transitions, the top-level join, the approval gate, and merge/combine all route through the existing recursive `enter_node`/`advance_node`/`complete_sequence`/`_nested_join` sequencer. The root sequence's cursor stays in `_instance.yaml.phase`; nested cursors keep their `<seq>.yaml` files. A phase transition is "continue at the next sibling path" (`dispatch_continue(path=…)`), so the `protocol-advance` dispatch type is deleted.

**Tech Stack:** Python 3 + PyYAML (runtime); pytest (dev-only). No new dependencies. Engine lives in `.github/agent-factory/engine/`.

**Scope:** This plan is **Stage 4a only** (engine + pytest). It produces working, fully pytest-verified software on its own. **Stage 4b** (GitHub Actions wiring to the `NODE_PATH` axis) and **Stage 4c** (live `deep-review-stub` protocol + live PR verification of deep/code-review/recover) are separate follow-on plans, authored after 4a lands and the emitted-action shapes (`legs[]`, `dispatch_continue` payloads) are stable. See `docs/superpowers/specs/2026-06-23-stage4-recursive-engine-unification-design.md` §8.

## Global Constraints

- **Engine is generic.** No protocol-specific logic in `.github/agent-factory/engine/`. The protocol id, state path, checks, and publish hooks are derived from `protocol.json` / the protocol directory. (CLAUDE.md "engine vs protocol".)
- **State advances only by fast-forward CAS push** (`lib.cas_push`). Never force-push `agentic-state`. The single writer of non-initial state is `advance.py` (+ `join.py` for the barrier).
- **`NODE_PATH` is the OS-shadow-safe coordinate name** (never `PATH`). The dot-joined TREE path is rooted at the first top-level node id; node ids must not contain `.` (it is the path separator).
- **TWO path notions** (`.superpowers/sdd/PATH-CONVENTIONS.md`): TREE-nav path (rooted at top node id) vs FILE-naming path (`lib.state_path(proto, tree)` drops the leading id when SINGLE-PHASE). The walker carries TREE paths and converts at every file call via `lib.state_path`.
- **Security:** agent-derived strings (answer body, feedback, verdicts, filenames, `client_payload[path]`) never interpolated into a shell `run:` block — `env:`-passed only. (This matters in 4b; in 4a keep `NODE_PATH`/answer-body parsing strictly via env + argv-as-data.)
- **No byte-identity goal.** Legacy byte-identity fixtures/tests are removed. The oracles are: `code-review`, `recover-mental-model-stub`, `deep-fanout`, `gate-deep`, plus re-added single-agent / simple-fanout capability fixtures.
- **Release bar:** clear, actionable authoring-error messages (include the offending node path); robust handling of malformed `protocol.json`.
- **Tests are pytest** under `tests/` using `tests/conftest.py` fixtures (`engine_env`, `run_engine`, `run_check`, `read_state_yaml`, a bare git origin for `agentic-state`, `ENGINE_LOCAL=1`). Run with `pytest tests/ -q`.

## Key `lib.py` helpers tasks rely on (verified signatures)

- `state_file(d, pid, instance, branch=None, phase=None, substate=None, path=None)`
- `state_path(proto, tree_path)` → FILE-naming path list
- `instance_file(d, pid, instance)`, `output_artifact_path(...)`, `join_marker_file/read_join/write_join`
- `state_by_id(proto, id)`, `_fanout_state(proto)`, `next_phase_id(proto, phase_id)`, `is_multiphase(proto)`, `phase_states(proto)`
- `resolve_agent_unit_path(proto, tree_path)` → `{agent_state, max_iterations, life_state}`
- `open_gate(dir_, pid, instance, proto_path, gate_id, sha, pr, branch=None, questions=None, phase=None)`
- `dispatch_continue(pid, instance, branch=None, substate=None, phase="", path=None)` → fires `protocol-continue`
- `fire_join_dispatch(pid, instance, fanout_path="")` → fires `protocol-join`
- `run_merge_hook(dir_, pid, instance, proto_path, merge_state)` → `{conclusion, summary}`
- `ensure_phase_label`, `apply_setup_label`, `remove_pr_label`, `set_check_run`, `cas_push`
- `render_pipeline_status_body`, `render_instance_status_body`, `upsert_status_comment`, `finalize_superseded_comment`, `ensure_status_comment`
- `paths`: `node_at_path`, `node_kind`, `children`, `first_child_id`, `next_sibling`, `parent_path`, `enclosing_fanout_id`, `enclosing_fanout_path`, `path_depth`, `max_static_depth`

## File structure

| File | Responsibility | Change |
|---|---|---|
| `engine/paths.py` | pure tree nav | + root-child predicate helper |
| `engine/next.py` | planner: entry / continue / answer / override / resolve-gate | unify entry; route phase transitions + gates through recursive walk; delete `start_fanout`, `seed_and_dispatch_phase`, `advance-phase`, single-agent path; `workflow` on legs |
| `engine/advance.py` | sole writer; iterate/done/failed | delete legacy coord block; agent-phase-clear → root cursor + `dispatch_continue(path)`; delete `protocol-advance` fire |
| `engine/join.py` | barrier | top join → recursive sequence-advance to `.next`; fold mode-2/3/gate into recursive `continue` |
| `engine/lib.py` | shared I/O + helpers | minor: confirm `dispatch_continue(path=…)`, merge arm support |
| `tests/test_unified_*.py` | NEW capability e2e walks | the oracle suite |
| `tests/fixtures/single-agent/`, `tests/fixtures/simple-fanout/` | re-added capability fixtures | recreate under new engine |
| legacy fixtures/tests | removed | `fanout-mini`, `pipeline-mini`, `multiphase-subpipeline`, `subpipeline-mini` + byte-identity tests |
| `docs/STATUS.md` | status | update at end |

---

## Phase A — Coordinate groundwork

### Task 1: Root-child predicate in `paths.py`

**Files:**
- Modify: `.github/agent-factory/engine/paths.py`
- Test: `tests/test_paths.py` (append)

**Interfaces:**
- Produces: `paths.is_root_child(proto, path) -> bool` (True iff `path` names a top-level node, i.e. `len(path)==1` and the id is in `proto["states"]`); `paths.root_ids(proto) -> list[str]`.
- Consumes: existing `paths._root_children`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paths.py
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import paths

CR = json.load(open(ROOT / ".github/agent-factory/protocols/code-review/protocol.json"))

def test_root_ids_lists_top_level_phases():
    assert paths.root_ids(CR) == [s["id"] for s in CR["states"]]

def test_is_root_child_true_for_depth1_phase():
    first = CR["states"][0]["id"]
    assert paths.is_root_child(CR, [first]) is True
    assert paths.is_root_child(CR, [first, "grumpy"]) is False
    assert paths.is_root_child(CR, ["nonesuch"]) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_paths.py -k root -v`
Expected: FAIL — `AttributeError: module 'paths' has no attribute 'root_ids'`

- [ ] **Step 3: Implement**

```python
# paths.py (append near node_at_path)
def root_ids(proto):
    return [c["id"] for c in _root_children(proto)]

def is_root_child(proto, path):
    return len(path) == 1 and path[0] in root_ids(proto)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_paths.py -k root -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/paths.py tests/test_paths.py
git commit -m "feat(paths): root-child predicate for root-as-sequence walk"
```

---

## Phase B — Oracle e2e walks (write failing, then make pass)

These two tests are the **specification** for the refactor. Write them now; they fail against today's engine (which drives multi-phase via `protocol-advance`, not `NODE_PATH`). Tasks 3–10 make them pass. Model them on `tests/test_deep_fanout_e2e.py::test_deep_fanout_walks_to_done`: invoke `next.py`/`advance.py`/`join.py` as subprocesses, re-clone the bare origin between steps, assert state + dispatch stderr at each numbered step.

### Task 2: Code-review unified NODE_PATH walk (oracle)

**Files:**
- Create: `tests/test_unified_codereview_e2e.py`

**Interfaces:**
- Consumes: the live `code-review` protocol.json; engine scripts via subprocess; `engine_env`, `STATE_REMOTE` from `conftest`.
- Produces: the canonical multi-phase call sequence GHA (4b) will replicate: `start` (no NODE_PATH) → advance `preflight` (NODE_PATH=`preflight`) → continue `review` fanout → advance each leg → join → approval gate open → `resolve-gate` approve → done.

- [ ] **Step 1: Write the failing oracle test**

```python
# tests/test_unified_codereview_e2e.py
import json, subprocess, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / ".github/agent-factory/protocols/code-review/protocol.json"
NEXT, ADV, JOIN = ENG/"next.py", ENG/"advance.py", ENG/"join.py"

def _yaml(p):
    import yaml; return yaml.safe_load(open(p))

def _pass(tmp):
    v = tmp/"v.json"; v.write_text(json.dumps({"results":[
        {"check":"x","pass":True,"feedback":"","on_fail":"iterate"}]}))
    ev = tmp/"e.json"; ev.write_text("{}")
    return v, ev

def test_codereview_walks_via_node_path(engine_env, tmp_path):
    base = dict(engine_env); base["PR_HEAD_SHA"]="sha1"; base["AGENT_RUN_ID"]="r"
    def run(script,*a,**env):
        e=dict(base); e.update(env)
        r=subprocess.run(["python3",str(script),*map(str,a)],text=True,
                         capture_output=True,env=e)
        assert r.returncode==0, f"{script.name} {a}: {r.stderr}"
        return r
    def rc(tag):
        d=tmp_path/f"rc-{tag}"
        subprocess.run(["git","clone","-q","-b","agentic-state",
                        engine_env["STATE_REMOTE"],str(d)],check=True)
        return d/"code-review"/"pr-1"
    v,ev=_pass(tmp_path)
    # 1. start → first phase (preflight, an agent phase) seeded + run-agent emitted.
    r1=run(NEXT, tmp_path/"s1","pr-1",PROTO,"start","sha1")
    a1=json.loads(r1.stdout); assert a1["action"]=="run-agent"
    assert _yaml(rc("1")/"_instance.yaml")["phase"]=="preflight"
    # 2. advance preflight (clear) → cursor advances to review, continue dispatched.
    r2=run(ADV, tmp_path/"s2","pr-1",PROTO,v,ev, NODE_PATH="preflight")
    assert "event_type=protocol-continue" in r2.stderr
    assert "client_payload[path]=review" in r2.stderr
    assert "event_type=protocol-advance" not in r2.stderr  # RETIRED
    assert _yaml(rc("2")/"_instance.yaml")["phase"]=="review"
    # 3. continue review → fanout matrix (grumpy + security) seeded.
    r3=run(NEXT, tmp_path/"s3","pr-1",PROTO,"continue", NODE_PATH="review")
    a3=json.loads(r3.stdout); assert a3["action"]=="run-fanout"
    assert {l["path"] for l in a3["legs"]}=={"review.grumpy","review.security"}
    # 4. advance each leg done → fire_join.
    for leg in ("grumpy","security"):
        run(ADV, tmp_path/f"s4-{leg}","pr-1",PROTO,v,ev, NODE_PATH=f"review.{leg}")
    # 5. join (top) → all done → approval gate opens.
    rj=run(JOIN, tmp_path/"s5","pr-1",PROTO)
    inst=_yaml(rc("5")/"_instance.yaml")
    assert inst["phase"]=="approval"
    # 6. resolve-gate approve → pipeline done.
    run(NEXT, tmp_path/"s6","pr-1",PROTO,"resolve-gate",
        GATE_DECISION="approve", GATE_ACTOR="alice", GATE_REASON="", GATE_PR_AUTHOR="bob")
    # aggregate complete: phase label done; gate state approved.
    final=rc("final")
    assert _yaml(final/"approval.yaml")["gates"]["state"]=="approved"
```

> Adjust leg ids (`grumpy`/`security`) and phase ids to match `code-review/protocol.json` if they differ; read it first.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_unified_codereview_e2e.py -v`
Expected: FAIL (today `start` on a multi-phase protocol uses `seed_and_dispatch_phase`; advancing `preflight` fires `protocol-advance`, not a path-continue; `NODE_PATH=review` continue at a depth-1 fanout phase is not wired to `_instance.yaml`).

- [ ] **Step 3: (no implementation yet)** — leave failing; Tasks 3–10 make it pass.

- [ ] **Step 4: Commit the oracle**

```bash
git add tests/test_unified_codereview_e2e.py
git commit -m "test(oracle): code-review unified NODE_PATH walk (failing; spec for 4a refactor)"
```

### Task 3: Recover unified NODE_PATH walk (oracle)

**Files:**
- Create: `tests/test_unified_recover_e2e.py`

**Interfaces:**
- Consumes: the live `recover-mental-model-stub` protocol.json.
- Produces: the fanout(summary ∥ rationale sub-pipeline) → join → combine(merge/agent) walk via `NODE_PATH`, including the data-gate `/answer` in the rationale leg.

- [ ] **Step 1: Write the failing oracle test**

Model on Task 2 + `tests/test_gate_data.py` for the `/answer` step. Walk: `start` → fanout legs seeded → advance `summary` leg → drive `rationale` sub-pipeline (`draft` → gate → `/answer` → `finalize`) via `NODE_PATH=recover.rationale.<sub>` → join → combine. Assert `_instance.yaml.phase` lands on the combine state and `joined: true`. Read `recover-mental-model-stub/protocol.json` for exact ids first.

```python
# tests/test_unified_recover_e2e.py  (skeleton — fill ids from the protocol)
import json, subprocess, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTO = ROOT/".github/agent-factory/protocols/recover-mental-model-stub/protocol.json"
# ... same run()/rc() helpers as Task 2 ...
def test_recover_walks_via_node_path(engine_env, tmp_path):
    # 1. start → fanout(summary ∥ rationale) seeded
    # 2. advance summary leg done
    # 3. drive rationale: continue NODE_PATH=recover.rationale (if sub-pipeline entered via continue)
    #    advance draft → gate opens; answer; advance finalize done
    # 4. join → combine (merge/agent) → joined:true, phase=combine
    ...  # assert at each step; mirrors test_gate_data + Task 2
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_unified_recover_e2e.py -v` → FAIL.

- [ ] **Step 3: (no implementation)** — Tasks 7–9 make it pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_unified_recover_e2e.py
git commit -m "test(oracle): recover unified NODE_PATH walk (failing; spec for 4a refactor)"
```

---

## Phase C — The refactor (make oracles pass)

### Task 4: Unified entry — route `start`/`reset` through `enter_node` at the root

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (the `start`/`reset` entry: lines ~700–776; `start_fanout` ~170–191; `seed_and_dispatch_phase` ~194–284)

**Interfaces:**
- Produces: a single `enter_root(command, head_sha)` that seeds the first top-level node and emits its action (run-agent / run-fanout / gate-open), creating `_instance.yaml` with `phase=<first id>`, applying setup/phase labels, performing the `reset_instance` wipe on a fresh `start`/`reset`, and `cas_push`. Replaces both `start_fanout` and the first-phase arm of `seed_and_dispatch_phase`.
- Consumes: `enter_node(proto, [first_id], command, emit=False)` (already exists), `lib.ensure_phase_label`, `lib.apply_setup_label`, the existing `reset_instance` body (superseded-comment + label removal + instance-dir wipe + head refresh).

- [ ] **Step 1: Failing test already exists** — Task 2 step 1 (the `start` step). Confirm it fails at step 1 of the walk.

Run: `pytest tests/test_unified_codereview_e2e.py -v`
Expected: FAIL at the `start`/`_instance.yaml phase` assertion.

- [ ] **Step 2: Implement `enter_root`**

Extract the reset-wipe block from `seed_and_dispatch_phase` (the `reset_instance=True` body: `finalize_superseded_comment`, `remove_pr_label`, wipe `inst_dir`, `head_sha` refresh) into a helper `_reset_wipe(inf, inst_dir, prev, pr)`. Then:

```python
# next.py
def enter_root(command, head_sha):
    first = paths.root_ids(proto_data)[0]
    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    inf = lib.instance_file(DIR, PID, INSTANCE)
    inst_dir = os.path.dirname(inf)
    os.makedirs(inst_dir, exist_ok=True)
    prev = lib.load_yaml(inf) if os.path.isfile(inf) else {}
    _reset_wipe(inf, inst_dir, prev, pr)           # fresh start/reset always wipes
    lib.apply_setup_label(proto_data, pr)
    lib.dump_yaml(inf, {"protocol": PID, "instance": INSTANCE,
                        "head_sha": head_sha, "phase": first, "joined": False})
    lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, first)
    branches = enter_node(proto_data, [first], command, emit=False)  # seeds node
    lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter root phase {first} ({command})")
    _emit_for_node([first], branches)              # see Step 3
```

Add `_emit_for_node(path, branches)` that emits the right action by kind: fanout → `_fanout_action(proto_data, path, branches)`; agent → `run-agent` (+ `phase` if multiphase + `workflow`); gate → `noop` gate-open. (This is the deferred-emit tail the three old call sites shared.)

Route entry: replace the `is_multiphase and not PHASE and not BRANCH` block AND the `not BRANCH and is_fanout and not PHASE` block with a single:

```python
if COMMAND in ("start", "reset") and not NODE_PATH:
    enter_root(COMMAND, HEAD_SHA)
    sys.exit(0)
```

- [ ] **Step 3: Run the walk's start step** — `pytest tests/test_unified_codereview_e2e.py -v` should now pass step 1 (`_instance.yaml.phase == "preflight"`) and fail later. Also run `pytest tests/test_deep_fanout_e2e.py -v` (single-phase start still works — `enter_root` first id = the fanout). Fix `_emit_for_node` until deep-fanout's `start` step is green again.

- [ ] **Step 4: Commit**

```bash
git add .github/agent-factory/engine/next.py
git commit -m "feat(next): unified enter_root replaces start_fanout + seed_and_dispatch_phase first-phase"
```

### Task 5: Continue at a depth-1 fanout phase uses `_instance.yaml`

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (the `continue`-at-`NODE_PATH` fanout arm ~722–733; `enter_node` fanout arm ~97–111)

**Interfaces:**
- Produces: `continue` with `NODE_PATH=<depth-1 fanout phase>` seeds the phase's child legs against the `_instance.yaml` `joined` marker (NOT a `__join.yaml` file), emits run-fanout with `legs`.
- Consumes: `paths.is_root_child`, existing `enter_node`.

- [ ] **Step 1: Confirm failing** — Task 2 walk step 3 (continue `review`) fails today (depth-1 continue path enters but the marker handling differs).

- [ ] **Step 2: Implement**

In `enter_node`'s fanout arm, the `len(path) > 1` guard already writes a `__join.yaml` only for nested fanouts; a depth-1 phase fanout (`len==1`) writes nothing extra — correct, the `_instance.yaml.joined` set in `enter_root`/cursor is the top marker. Ensure the `continue`-at-NODE_PATH fanout arm calls `enter_node(... emit=False)` then `_fanout_action`, identical to deep-fanout. No new code if Task 4's `_emit_for_node` is reused; otherwise align.

- [ ] **Step 3: Run** — `pytest tests/test_unified_codereview_e2e.py -v` passes through walk step 3 (`legs == {review.grumpy, review.security}`). `pytest tests/test_deep_fanout_e2e.py -v` still green.

- [ ] **Step 4: Commit**

```bash
git add .github/agent-factory/engine/next.py
git commit -m "feat(next): depth-1 fanout-phase continue uses _instance marker"
```

### Task 6: `advance.py` — agent-phase clear → root cursor + path-continue (retire `protocol-advance`)

**Files:**
- Modify: `.github/agent-factory/engine/advance.py` (agent-phase-clear block ~654–687; legacy coord block ~465–504; iterate re-dispatch ~720–735)

**Interfaces:**
- Produces: when a depth-1 AGENT phase clears, set `_instance.yaml.phase = next_root_sibling` and `dispatch_continue(path=<next sibling>)`; when no next sibling, finalize (aggregate check-run success). NO `protocol-advance` dispatch anywhere. `advance.py` always runs in `NODE_PATH` mode.
- Consumes: `paths.next_sibling(proto, tree_path)`, `lib.dispatch_continue(path=…)`, `lib.next_phase_id` (may be removed if unused), existing pre-flight-gate block.

- [ ] **Step 1: Confirm failing** — Task 2 walk step 2 asserts `client_payload[path]=review` and NO `protocol-advance`; fails today.

- [ ] **Step 2: Implement**

Delete the `else:` legacy coordinate-derivation block (~465–504); make `node_path_env` required (error with a clear message if unset). In the `is_agent_phase` clear branch, replace the `protocol-advance` `gh_api(...)` with:

```python
nxt = _paths.next_sibling(proto, tree_path)   # tree_path is depth-1 here
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

Determine `is_agent_phase` from `paths.is_root_child(proto, tree_path) and node_kind==agent`. The pre-flight-gate block (`conclude`/`on_blocked:halt` → `halted` marker) stays; only its clear-tail uses the path-continue above.

- [ ] **Step 3: Run** — Task 2 walk step 2 passes (`client_payload[path]=review`, no `protocol-advance`, `_instance.phase==review`). Run full suite for regressions: `pytest tests/test_deep_fanout_e2e.py tests/test_engine.py -v` (expect some legacy multi-phase tests to fail — they assert `protocol-advance`; those are rewritten/removed in Phase E).

- [ ] **Step 4: Commit**

```bash
git add .github/agent-factory/engine/advance.py
git commit -m "feat(advance): agent-phase clear advances root cursor via path-continue; retire protocol-advance fire; NODE_PATH-only"
```

### Task 7: `join.py` — top join performs recursive sequence-advance to `.next`

**Files:**
- Modify: `.github/agent-factory/engine/join.py` (top-level `main` body ~152–287; mode-2/3/gate tails ~211–261)

**Interfaces:**
- Produces: on all-done, the top join sets the **enclosing sequence cursor** to the join's `.next` and dispatches `protocol-continue` with `client_payload[path]=<.next>` (root sequence → `_instance.yaml.phase`). The gate/agent-combine/merge that follows is entered by the recursive `continue` (Task 8 adds the `merge` arm). On not-all-done, finalize as failure (unchanged).
- Consumes: `paths.next_sibling`/the join state's `.next`, `lib.dispatch_continue(path=…)`.

- [ ] **Step 1: Confirm failing** — Task 2 walk step 5 (`_instance.phase == approval` after join) fails today (join opens the gate inline via the bespoke gate tail).

- [ ] **Step 2: Implement**

Replace the bespoke gate-open / agent-combine / merge tails in the all-done branch with the uniform sequence-advance:

```python
# all_done branch, after computing join_state and gate_next = join_state.next
nxt = (join_state or {}).get("next")
instance_data["joined"] = True
if nxt:
    instance_data["phase"] = nxt            # root cursor
    lib.dump_yaml(inf, instance_data)
    lib.ensure_phase_label(dir_, pid, instance, protocol, pr, nxt)
    lib.cas_push(dir_, f"{instance}: join clear → continue {nxt}")
    lib.dispatch_continue(pid, instance, path=nxt)
    return
# no .next → finalize aggregate success (unchanged tail)
```

The `continue` at `path=nxt` then opens the gate (gate arm), seeds+dispatches an agent (agent arm), or runs the merge (Task 8). This deletes join.py's mode-2/mode-3/gate-open special cases. Keep the `_nested_join` path unchanged (it already advances the enclosing sub-pipeline cursor + continues).

- [ ] **Step 3: Run** — Task 2 walk step 5 passes (`_instance.phase==approval`). `pytest tests/test_deep_fanout_e2e.py -v` green (deep-fanout top join `.next` is `done` → no-next finalize). `pytest tests/test_join.py -v` — failures expected for bespoke-tail assertions (rewritten in Phase E).

- [ ] **Step 4: Commit**

```bash
git add .github/agent-factory/engine/join.py
git commit -m "feat(join): top join advances enclosing-sequence cursor via path-continue (fold mode-2/3/gate into recursive continue)"
```

### Task 8: Recursive `continue` learns the `merge` kind

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (the `continue`-at-`NODE_PATH` arms ~722–763)

**Interfaces:**
- Produces: `continue` with `NODE_PATH` at a `merge`-kind node runs `lib.run_merge_hook`, finalizes the instance (aggregate check-run + status comment + done label), `cas_push`, emits `noop`.
- Consumes: `lib.run_merge_hook(dir_, pid, instance, proto_path, merge_state)`, `lib.render_instance_status_body`.

- [ ] **Step 1: Failing test** — Task 3 (recover) walk's combine step asserts the merge runs via continue; fails until this arm exists. (If recover's combine is `agent`-kind not `merge`, the existing agent arm already handles it — verify against the protocol; add the `merge` arm regardless for the capability.)

Add a focused unit test:

```python
# tests/test_unified_merge.py
def test_continue_at_merge_runs_hook_and_finalizes(engine_env, tmp_path):
    # use a small fixture with fanout → join → merge; seed cursor at merge via continue;
    # assert ENGINE_LOCAL merge-hook ran and _instance.yaml phase==merge id.
    ...
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_unified_merge.py -v` → FAIL.

- [ ] **Step 3: Implement** — add to the `continue`-at-NODE_PATH dispatch:

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

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_unified_merge.py -v` PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_unified_merge.py
git commit -m "feat(next): continue handles merge kind (reduce hook + finalize)"
```

### Task 9: Gate resolution + override tails → path-continue

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (`do_resolve_gate` ~353–468, `do_override` ~287–351)

**Interfaces:**
- Produces: on `approve` (and on `/override` of a blocked gate), advance the root cursor to the next sibling and `dispatch_continue(path=<next>)` instead of calling `seed_and_dispatch_phase(nxt)`. `request-changes`/`reject` tails unchanged. Auth + refusal semantics unchanged.
- Consumes: `paths.next_sibling`, `lib.dispatch_continue(path=…)`.

- [ ] **Step 1: Confirm failing** — Task 2 walk step 6 (resolve-gate approve → done) fails (`seed_and_dispatch_phase` is being deleted).

- [ ] **Step 2: Implement** — in `do_resolve_gate`'s approve arm, replace `seed_and_dispatch_phase(nxt, "approve")` with:

```python
inst = lib.load_yaml(inf); inst["phase"] = nxt; lib.dump_yaml(inf, inst)
lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, nxt)
lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} approved by {actor} → continue {nxt}")
lib.dispatch_continue(PID, INSTANCE, path=nxt)
```

Compute `nxt = paths.next_sibling(proto_data, [cursor])` (root sibling of the gate phase). The `else` (no next) finalize tail stays. Apply the same substitution in `do_override`'s advance arm.

- [ ] **Step 3: Run** — Task 2 (`test_unified_codereview_e2e`) PASSES fully. Task 3 (`test_unified_recover_e2e`) PASSES fully. `pytest tests/test_override.py tests/test_gate*.py -v` — fix or mark for Phase E rewrite.

- [ ] **Step 4: Commit**

```bash
git add .github/agent-factory/engine/next.py
git commit -m "feat(next): gate approve + override advance root cursor via path-continue"
```

### Task 10: Delete dead code paths

**Files:**
- Modify: `.github/agent-factory/engine/next.py`, `advance.py`, `join.py`

**Interfaces:**
- Produces: removal of `start_fanout`, `seed_and_dispatch_phase`, the `advance-phase` command branch, the single-agent bespoke planner/advancer paths, and any now-unused `lib` helpers (`next_phase_id` if unreferenced). The `protocol-advance` string appears nowhere in the engine.

- [ ] **Step 1: Implement deletions** — remove the functions/branches; run `grep -rn "protocol-advance\|seed_and_dispatch_phase\|start_fanout\|advance-phase" .github/agent-factory/engine/` and confirm zero hits.

- [ ] **Step 2: Run the oracle suite** — `pytest tests/test_unified_codereview_e2e.py tests/test_unified_recover_e2e.py tests/test_deep_fanout_e2e.py tests/test_gate_data.py -v` all PASS.

- [ ] **Step 3: Commit**

```bash
git add .github/agent-factory/engine/
git commit -m "refactor(engine): delete legacy phase machinery (start_fanout, seed_and_dispatch_phase, advance-phase, single-agent path)"
```

---

## Phase D — Capability fixtures + tests (release bar)

### Task 11: Single-agent capability fixture + walk

**Files:**
- Create: `tests/fixtures/single-agent/protocol.json` (+ schema/checks as the old one had, minimal), `tests/test_cap_single_agent.py`

**Interfaces:**
- Produces: proof the unified engine handles a one-`agent`-state protocol (root sequence, single child): `start` → run-agent at depth-1 path; advance done → aggregate complete; advance fail-loop → exhaust.

- [ ] **Step 1: Write fixture + failing walk test** — a protocol with one `agent` state (`id: solo`, `max_iterations: 2`, one always-pass check). Test: `start` (no NODE_PATH) emits run-agent + seeds; `advance` NODE_PATH=`solo` pass → `_instance` done / aggregate success.
- [ ] **Step 2: Run to verify fail** (fixture absent / behavior gap).
- [ ] **Step 3: Implement** — fix any depth-1 single-child-sequence handling in `enter_root`/`advance` finalize (no-next root agent → finalize aggregate).
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `test(cap): single-agent shape under unified engine`.

### Task 12: Simple-fanout capability fixture + walk

**Files:**
- Create: `tests/fixtures/simple-fanout/protocol.json`, `tests/test_cap_simple_fanout.py`

**Interfaces:**
- Produces: proof of a single-phase fanout → join → done (two flat agent legs). Walk mirrors deep-fanout's top level without the nesting.

- [ ] **Step 1–5:** fixture (fanout `f` with legs `a`,`b`; join `of: f`, `next: done`); failing test; (should pass with no new code — confirms the degenerate case); commit `test(cap): simple fanout shape`.

### Task 13: Approval-gate decisions capability

**Files:**
- Create: `tests/test_cap_approval_gate.py`

**Interfaces:**
- Produces: coverage of `approve` (→ next/done), `request-changes` (→ halt, re-runnable), `reject` (→ failed), self-approve refusal, on the `code-review` approval gate.

- [ ] **Step 1–5:** drive code-review to the approval gate (reuse Task 2 helper), then exercise each decision in isolation; assert gate state + labels + check-runs. Commit `test(cap): approval-gate decisions`.

### Task 14: Override capability

**Files:**
- Create: `tests/test_cap_override.py`

**Interfaces:**
- Produces: a blocked pre-flight gate (`poc:sabotage`-style or a fixture with `on_blocked: halt`) → `/override` advances one phase via path-continue; refusal messages for not-halted / exhausted.

- [ ] **Step 1–5:** fixture or code-review preflight block path; assert `halted` marker, override clears it + dispatches continue at next phase. Commit `test(cap): /override blocked gate`.

### Task 15: Restart/reset capability

**Files:**
- Create: `tests/test_cap_restart.py`

**Interfaces:**
- Produces: a second `start`/`reset` mid-pipeline wipes the instance dir, abandons the old status comment (superseded banner via `finalize_superseded_comment`, then drops `status_comment_id`), removes the old phase label, refreshes `head_sha`, re-seeds phase one.

- [ ] **Step 1–5:** run to mid-pipeline, re-`start`, assert wipe + fresh `_instance.yaml` + superseded call recorded (ENGINE_LOCAL stderr). Commit `test(cap): restart/reset wipe`.

### Task 16: Authoring-error UX + max_depth guard

**Files:**
- Create: `tests/test_cap_authoring_errors.py`, `tests/fixtures/too-deep/` (exists — reuse), small malformed fixtures inline (tmp_path-written protocol.json)

**Interfaces:**
- Produces: clear, actionable errors (exit 2, message names the offending node path) for: `max_depth` exceeded; join with unknown `of`; agent node missing `workflow`; gate missing source; sequence node missing `next` where required.

- [ ] **Step 1: Write failing tests** — each writes a tiny bad protocol to `tmp_path`, runs `next.py start`, asserts non-zero exit + a message containing the node id/path and a fix hint.
- [ ] **Step 2: Run to verify fail** (today: opaque stack trace or silent).
- [ ] **Step 3: Implement** — add validation in `next.py`/`lib` (e.g. a `lib.validate_protocol(proto)` called at load) producing messages like `"[next] join 'jx' references unknown fanout of='zzz'"`. Reuse `lib.check_depth` for depth.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(engine): actionable protocol-authoring error messages + validation`.

### Task 17: Security regression

**Files:**
- Create: `tests/test_cap_security.py`

**Interfaces:**
- Produces: assertions that a malicious answer body / feedback / leg id containing shell metacharacters is treated as data — `_parse_answers` never executes it; `NODE_PATH` segments are validated (`node_at_path` → None → clean error, no path traversal in `state_path`).

- [ ] **Step 1–5:** feed `"; rm -rf / #"` style strings through `do_answer` (ANSWER_BODY env) + a bogus `NODE_PATH` to `advance.py`; assert no execution + clean handling. Commit `test(cap): security regressions for agent-derived strings`.

---

## Phase E — Cleanup + status

### Task 18: Remove legacy byte-identity fixtures + tests

**Files:**
- Delete: `tests/fixtures/{fanout-mini,pipeline-mini,multiphase-subpipeline,subpipeline-mini}/`; legacy assertions in `tests/test_engine.py`, `tests/test_join.py`, status-comment tests that assert `protocol-advance` / `seed_and_dispatch_phase` behavior.

**Interfaces:**
- Produces: a green suite with NO references to retired mechanisms.

- [ ] **Step 1:** `grep -rn "protocol-advance\|fanout-mini\|pipeline-mini\|multiphase-subpipeline\|subpipeline-mini" tests/` → delete/rewrite each hit. Where a deleted fixture covered a real capability, ensure Tasks 11–17 cover it (they do: single-agent, simple-fanout, gates, merge, inputs via deep-fanout).
- [ ] **Step 2: Run full suite** — `pytest tests/ -q` → ALL PASS, no skips for retired behavior.
- [ ] **Step 3: Commit** `test: remove legacy byte-identity fixtures + tests (superseded by capability suite)`.

### Task 19: Update `docs/STATUS.md`

**Files:**
- Modify: `docs/STATUS.md`

- [ ] **Step 1:** document the unified recursive engine (root-as-sequence), the retirement of `protocol-advance`, the single `NODE_PATH` coordinate, and that Stage 4b (GHA) / 4c (live) follow. Note the no-in-flight-migration deploy requirement.
- [ ] **Step 2: Commit** `docs(status): recursive engine unification (Stage 4a) — protocol-advance retired`.

---

## Self-review checklist (run before handing off to execution)

- [ ] Full suite green: `pytest tests/ -q`.
- [ ] `grep -rn "protocol-advance\|start_fanout\|seed_and_dispatch_phase\|advance-phase" .github/agent-factory/engine/` → zero hits.
- [ ] Both oracles (`test_unified_codereview_e2e`, `test_unified_recover_e2e`) pass.
- [ ] `deep-fanout` (depth-4) + `gate-deep` (depth-5) keystones still pass.
- [ ] Capability suite covers: single-agent, simple-fanout, multi-phase, sub-pipeline, depth-4/5, gates (data + approval), override, restart, inputs, merge, max_depth, authoring errors, security.
- [ ] No `NODE_PATH`/answer-body/feedback string reaches an injection point.
