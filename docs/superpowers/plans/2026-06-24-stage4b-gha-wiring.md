# Stage 4b — GitHub Actions NODE_PATH Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewire the three GitHub Actions workflows to drive the unified engine on the single `NODE_PATH` coordinate (matrix `leg:{path,workflow}`, `client_payload.path` threading, path-keyed artifacts/concurrency, `protocol-advance` dropped), plus the one engine emit change the matrix needs.

**Architecture:** The engine already emits a path-aware `legs[]` companion and path-form `dispatch_continue`/`fire_join`; 4b makes `legs[]` (and `run-agent`) carry the leaf agent path + workflow, then the workflows read that single coordinate instead of the retired `(BRANCH, PHASE, SUBSTATE)` triple. GHA YAML can't be unit-tested end-to-end, so each YAML file is gated by a pytest *structural contract test* (parses the YAML, asserts NODE_PATH wiring + absence of legacy) plus `actionlint`; full behavioral validation is Stage 4c.

**Tech Stack:** Python 3 + PyYAML (engine + structural tests; PyYAML is already a dep), pytest (dev), GitHub Actions YAML, `actionlint` (lint gate). Continues on branch `feat/stage4-recursive-engine-unification` (Stage 4a, HEAD `b037bd2`, 401 tests).

## Global Constraints

- **Protocol DSL is UNTOUCHED.** 4b changes GHA YAML + one engine emit helper only. No new/renamed/changed protocol.json fields. (Standing constraint: keep the DSL human-intuitive; flag any DSL change to the user.)
- **Engine is generic** (no protocol-specific logic in `.github/agent-factory/engine/`); state advances only by CAS push; sole state writer is advance.py (+join.py barrier).
- **`NODE_PATH`** (never `PATH`) is the dot-joined TREE path; node ids contain no `.`. It is REQUIRED by advance.py/join.py after 4a.
- **Security (CLAUDE.md):** `client_payload.path` / `NODE_PATH` and all agent-derived strings (`feedback`, `verdicts`, `comment.body`, filenames) are `env:`-passed ONLY, NEVER interpolated into a `run:` block.
- **4a+4b land on `main` together** (or 4b before any 4a→main merge): the workflows on `main` thread BRANCH/PHASE/SUBSTATE and never set NODE_PATH, which the unified engine now requires.
- **Workflow-on-default-branch rule:** these three workflows run from `main` for `issue_comment`/`repository_dispatch`; never commit them onto a demo PR branch.
- **Tests are pytest** under `tests/`; run `pytest tests/ -q`. The suite stays GREEN at every task commit (baseline 401).
- **Done-bar (spec §8):** engine emit unit test + `actionlint` clean + structural no-legacy contract test. Live behavioral verification is Stage 4c (out of scope here).
- **Confirmed shapes:** `legs[]` currently = `[{"path": <branch path>}]` (no workflow; sub-pipeline branch path stops at the branch). `branches[]` = `[{"id","workflow","substate"?,"iteration","feedback"}]`. `dispatch_continue`/`fire_join` emit `client_payload[path]`.

## File structure

| File | Responsibility | Change |
|---|---|---|
| `engine/next.py` | planner emit | `legs[]` + `run-agent` carry leaf agent `path` + `workflow` |
| `tests/test_emit_legs.py` | NEW | unit-asserts the emit shape for all 4 protocols/fixtures |
| `tests/test_workflow_contract.py` | NEW | structural contract tests parsing the 3 workflow YAMLs |
| `.github/workflows/agentic-engine.yml` | engine matrix | `leg:{path,workflow}`, NODE_PATH threading, path artifacts, drop legacy |
| `.github/workflows/protocol-join.yml` | join | NODE_PATH env + path concurrency |
| `.github/workflows/agentic-orchestrator.yml` | router | path concurrency + drop `protocol-advance` from `on:` |
| `.github/workflows/lint.yml` | NEW | `actionlint` CI gate |

---

## Task 1: Engine emit — leaf path + workflow on every dispatchable action

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (`_fanout_action` ~lines 52-76; the depth-1 entry `run-agent` emit in `enter_node`/`_emit_for_node` ~118-130/213-215; the `continue`-at-agent emit ~700-705 already has `path`+`workflow`)
- Test: `tests/test_emit_legs.py` (new)

**Interfaces:**
- Produces: every `run-fanout` action's `legs[]` entry = `{"path": <leaf agent tree path>, "workflow": <agent workflow>}` where leaf path = `fanout_path + branch_id` for a flat branch, `fanout_path + branch_id + first_substate` for a sub-pipeline branch. Every `run-agent` action carries top-level `"path"` (its tree path) + `"workflow"`. `branches[]` is unchanged (kept; the YAML stops reading it in Task 2).
- Consumes: `branches[]` dicts from `_seed_child`/`enter_node` (already carry `id`, `workflow`, `substate?`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_emit_legs.py
import json, subprocess, pathlib, os
ROOT = pathlib.Path(__file__).resolve().parent.parent
NEXT = ROOT / ".github/agent-factory/engine/next.py"

def _emit(engine_env, tmp_path, proto_rel, command, *args, node_path=None):
    proto = ROOT / proto_rel
    e = dict(engine_env)
    if node_path is not None:
        e["NODE_PATH"] = node_path
    r = subprocess.run(["python3", str(NEXT), str(tmp_path/"s"), "pr-1", str(proto),
                        command, *args], text=True, capture_output=True, env=e)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)

def test_codereview_review_legs_carry_leaf_path_and_workflow(engine_env, tmp_path):
    # code-review start lands at preflight (agent). Continue at the review fanout.
    _emit(engine_env, tmp_path, ".github/agent-factory/protocols/code-review/protocol.json",
          "start", "sha1")
    act = _emit(engine_env, tmp_path,
                ".github/agent-factory/protocols/code-review/protocol.json",
                "continue", node_path="review")
    assert act["action"] == "run-fanout"
    legs = {l["path"]: l["workflow"] for l in act["legs"]}
    assert legs == {"review.grumpy": "grumpy-agent", "review.security": "security-agent"}

def test_recover_legs_subpipeline_branch_points_at_first_substate(engine_env, tmp_path):
    act = _emit(engine_env, tmp_path,
                ".github/agent-factory/protocols/recover-mental-model-stub/protocol.json",
                "start", "sha1")
    assert act["action"] == "run-fanout"
    legs = {l["path"]: l["workflow"] for l in act["legs"]}
    # flat branch → branch path; sub-pipeline branch → first sub-state (draft).
    assert legs == {"recover.summary": "rmm-summary-agent",
                    "recover.rationale.draft": "rmm-draft-agent"}

def test_codereview_preflight_run_agent_carries_path_and_workflow(engine_env, tmp_path):
    act = _emit(engine_env, tmp_path,
                ".github/agent-factory/protocols/code-review/protocol.json",
                "start", "sha1")
    assert act["action"] == "run-agent"
    assert act["path"] == "preflight"
    assert act["workflow"] == "preflight-agent"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_emit_legs.py -v`
Expected: FAIL — `legs` entries currently lack `workflow`; the recover sub-pipeline leg is `recover.rationale` (not `…draft`); the preflight `run-agent` lacks `path`/`workflow`.

- [ ] **Step 3: Implement the emit changes**

In `_fanout_action`, replace the `legs` line:

```python
# was: act["legs"] = [{"path": ".".join(path + [b["id"]])} for b in branches]
legs = []
for b in branches:
    leaf = path + [b["id"]] + ([b["substate"]] if b.get("substate") else [])
    legs.append({"path": ".".join(leaf), "workflow": b.get("workflow")})
act["legs"] = legs
```

In the depth-1 agent-phase `run-agent` emit (`_emit_for_node` and/or `enter_node`'s agent arm — wherever the entry `run-agent` is printed), add `path` + `workflow`:

```python
act = {"action": "run-agent", "iteration": 1, "feedback": "",
       "reason": f"phase:{path[-1]}", "path": ".".join(path),
       "workflow": paths.node_at_path(proto, path).get("workflow")}
if lib.is_multiphase(proto):
    act["phase"] = path[-1]
```

(The `continue`-at-agent emit already includes `path` + `workflow`; leave it.) Keep `branches[]` in the action unchanged.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_emit_legs.py -v` → PASS. Then `pytest tests/ -q` → still green (the `legs` key is additive; existing tests index by `branches`/`action`/`path` and the deep-fanout e2e asserts `legs` paths — verify `test_deep_fanout_e2e` still green, as its legs are flat agents so leaf path == branch path, unchanged).

> Note: `test_deep_fanout_e2e` asserts `legs` paths like `preflight.deep.analyze.sec` (flat nested agents) — unchanged by this task. If any existing `legs` assertion breaks, it is because that fixture has a sub-pipeline branch whose first sub-state now appears — reconcile the assertion to the leaf path.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_emit_legs.py
git commit -m "feat(next): legs[] + run-agent carry leaf agent path + workflow (GHA matrix seam)"
```

---

## Task 2: `agentic-engine.yml` — NODE_PATH matrix + threading

**Files:**
- Modify: `.github/workflows/agentic-engine.yml`
- Test: `tests/test_workflow_contract.py` (new; engine.yml assertions)

**Interfaces:**
- Consumes: Task 1's `legs:[{path,workflow}]` and `run-agent` `path`+`workflow`.
- Produces: a workflow whose plan job emits a `legs` matrix output `[{path,workflow}]`; dispatch/checks/advance run with `NODE_PATH=${{ matrix.leg.path }}`; no `BRANCH`/`PHASE`/`SUBSTATE` env on those jobs; ctx sets `NODE_PATH` from `client_payload.path`; dispatch reads `matrix.leg.workflow`; artifacts keyed on the dot-path.

This task rewrites the plan→dispatch→checks→advance jobs together (the matrix shape must match what the downstream jobs read — splitting risks an inconsistent intermediate). The structural contract test is the offline gate.

- [ ] **Step 1: Write the failing structural test**

```python
# tests/test_workflow_contract.py
import pathlib, yaml
ROOT = pathlib.Path(__file__).resolve().parent.parent
WF = ROOT / ".github/workflows"

def _load(name):
    # GitHub 'on' parses to python True; that's fine, we read as text + yaml.
    return WF.joinpath(name).read_text()

def test_engine_yml_threads_node_path_not_legacy():
    t = _load("agentic-engine.yml")
    # NODE_PATH is threaded from the matrix leg.
    assert "NODE_PATH: ${{ matrix.leg.path }}" in t
    assert "matrix.leg.workflow" in t
    assert "github.event.client_payload.path" in t
    # legacy coordinate wiring is gone from the engine jobs.
    assert "client_payload.branch" not in t
    assert "client_payload.substate" not in t
    assert "client_payload.phase" not in t
    assert "advance-phase" not in t
    assert "agent-workflow" not in t   # dispatch reads matrix.leg.workflow now
    # matrix is fed from the action's legs.
    assert "fromJSON(needs.plan.outputs.legs)" in t

def test_engine_yml_matrix_leg_has_path_and_workflow():
    t = _load("agentic-engine.yml")
    assert "matrix.leg.path" in t
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_workflow_contract.py -k engine -v`
Expected: FAIL (today the engine.yml uses `matrix.leg.branch`/`substate`, `client_payload.branch`, `advance-phase`, `agent-workflow`).

- [ ] **Step 3: Edit `agentic-engine.yml`**

Make these concrete edits (line refs are approximate — match by content):

1. **plan job `outputs`:** replace `branches: ${{ steps.plan.outputs.branches }}` with `legs: ${{ steps.plan.outputs.legs }}`. Remove `phase:` and `branch:` outputs (no longer consumed). Keep `pr/head_sha/action/iteration/feedback/sabotage/instance/command/inputs_json/override_*`.
2. **`ctx` step env:** delete `DISPATCH_BRANCH`, `DISPATCH_PHASE`, `DISPATCH_SUBSTATE`. Add `DISPATCH_PATH: ${{ github.event.client_payload.path }}`.
3. **`ctx` step run:** in the `repository_dispatch` case, replace the `protocol-continue) CMD="continue"; BRANCH=…; PHASE=…; SUBSTATE=…` line with `protocol-continue) CMD="continue"; NODE_PATH="$DISPATCH_PATH" ;;` and DELETE the `protocol-advance) CMD="advance-phase"; …` case entirely. At the end of ctx, replace the `echo "branch=…"/"phase=…"/"substate=…"` output lines with `echo "node_path=$NODE_PATH" >> "$GITHUB_OUTPUT"`. (Leave the `issue_comment` command derivation — start/override/resolve-gate/answer — intact; it sets no branch/phase/substate.)
4. **`plan` step env:** delete `BRANCH`/`PHASE`/`SUBSTATE`; add `NODE_PATH: ${{ steps.ctx.outputs.node_path }}`. (next.py reads `NODE_PATH` from env.)
5. **`plan` step run — matrix building:** replace the `if [ "$ACTION" = "run-fanout" ]; then BRANCHES=$(jq -c '[.branches[] | {branch,substate}]' …) … fi` block with:
   ```bash
   if [ "$ACTION" = "run-fanout" ]; then
     LEGS=$(jq -c '.legs' /tmp/action.json)
   elif [ "$ACTION" = "run-agent" ]; then
     LEGS=$(jq -c '[{path: .path, workflow: .workflow}]' /tmp/action.json)
   else
     LEGS='[]'
   fi
   echo "legs=$LEGS" >> "$GITHUB_OUTPUT"
   ```
   (Rename the output from `branches` to `legs` everywhere in the job.)
6. **`dispatch` / `checks` / `advance` jobs:** change `matrix: leg: ${{ fromJSON(needs.plan.outputs.legs) }}`. In each job's env, delete `BRANCH`/`SUBSTATE`/`PHASE`; add `NODE_PATH: ${{ matrix.leg.path }}`. In `dispatch`, resolve the workflow from `NAME="${{ matrix.leg.workflow }}"` (delete the `lib.agent-workflow` call); derive the CID/leg token from the path (e.g. `LEGTOK=$(printf '%s' "$NODE_PATH" | tr '.' '-')`). Artifact names: `runmeta-${{ matrix.leg.path }}` and `verdicts-${{ matrix.leg.path }}` (dots are legal in artifact names) in dispatch (upload), checks (download+upload), advance (download).
7. **`checks` step:** replace the `NODE="${PHASE:-…}"` derivation with `NODE="$NODE_PATH"` and pass it to `run-checks.py` (confirm run-checks reads the path coordinate — 4a made it NODE_PATH-aware; if it still expects PHASE/BRANCH, pass `NODE_PATH` env and let run-checks resolve, matching how advance.py resolves).
8. **`advance` step:** invoke `advance.py` with `NODE_PATH` env only (no BRANCH/PHASE/SUBSTATE). Keep the verdicts/evidence wiring.
9. The `if:` guards that referenced `needs.plan.outputs.branches != '[]'` become `needs.plan.outputs.legs != '[]'`.

- [ ] **Step 4: Run to verify it passes + actionlint**

Run: `pytest tests/test_workflow_contract.py -k engine -v` → PASS. Then `pytest tests/ -q` → green.
Run: `actionlint .github/workflows/agentic-engine.yml` (install if needed: `bash <(curl -s https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash)` → `./actionlint <file>`). Expected: no errors. If actionlint is unavailable in the environment, note it and rely on the structural test; the CI lint gate (Task 5) will enforce it.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/agentic-engine.yml tests/test_workflow_contract.py
git commit -m "feat(ci): agentic-engine.yml drives the NODE_PATH matrix (leg:{path,workflow})"
```

---

## Task 3: `protocol-join.yml` — NODE_PATH + path concurrency

**Files:**
- Modify: `.github/workflows/protocol-join.yml`
- Test: `tests/test_workflow_contract.py` (append join assertions)

**Interfaces:**
- Produces: the join workflow passes `NODE_PATH=${{ github.event.client_payload.path }}` to the `join.py` step (empty → top join, unchanged), and serializes per `(instance, path)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_contract.py (append)
def test_join_yml_threads_node_path_and_path_concurrency():
    t = _load("protocol-join.yml")
    assert "NODE_PATH: ${{ github.event.client_payload.path }}" in t
    # concurrency group is path-aware so nested joins don't serialize against the top join
    assert "join-${{ github.event.client_payload.instance }}-${{ github.event.client_payload.path }}" in t
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_workflow_contract.py -k join -v` → FAIL.

- [ ] **Step 3: Edit `protocol-join.yml`**
1. Concurrency group → `join-${{ github.event.client_payload.instance }}-${{ github.event.client_payload.path }}`.
2. In the `Evaluate join` step env, add `NODE_PATH: ${{ github.event.client_payload.path }}` (alongside the existing `STATE_REMOTE`/`PUBLISH_TOKEN`/`PR`/`PR_HEAD_SHA`). join.py reads `NODE_PATH` (empty → top join).

- [ ] **Step 4: Run** — `pytest tests/test_workflow_contract.py -k join -v` → PASS; `pytest tests/ -q` green; `actionlint .github/workflows/protocol-join.yml` clean.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/protocol-join.yml tests/test_workflow_contract.py
git commit -m "feat(ci): protocol-join.yml threads NODE_PATH + path-aware concurrency"
```

---

## Task 4: `agentic-orchestrator.yml` — path concurrency + drop protocol-advance

**Files:**
- Modify: `.github/workflows/agentic-orchestrator.yml`
- Test: `tests/test_workflow_contract.py` (append orchestrator assertions)

**Interfaces:**
- Produces: the router serializes per `(instance, path)` and no longer listens for `protocol-advance`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_contract.py (append)
def test_orchestrator_yml_path_concurrency_and_no_protocol_advance():
    t = _load("agentic-orchestrator.yml")
    assert "agentic-${{ github.event.client_payload.instance" in t
    assert "github.event.client_payload.path }}" in t   # concurrency keyed on path
    assert "protocol-advance" not in t                  # dropped from on: types
    # protocol-continue is still accepted; protocol-join still owned by protocol-join.yml
    assert "protocol-continue" in t
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_workflow_contract.py -k orchestrator -v` → FAIL (today concurrency is `…-<branch>`, `on:` lists `protocol-advance`).

- [ ] **Step 3: Edit `agentic-orchestrator.yml`**
1. `on: repository_dispatch: types:` → `[protocol-continue]` (drop `protocol-advance`; `protocol-join` was already owned by protocol-join.yml).
2. Concurrency group → `agentic-${{ github.event.client_payload.instance || format('pr-{0}', github.event.issue.number || github.event.pull_request.number) }}-${{ github.event.client_payload.path }}` (replace the trailing `-${{ github.event.client_payload.branch }}`).
3. (Optional) add the path to `run-name` for debuggability.

- [ ] **Step 4: Run** — `pytest tests/test_workflow_contract.py -k orchestrator -v` → PASS; `pytest tests/ -q` green; `actionlint .github/workflows/agentic-orchestrator.yml` clean.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/agentic-orchestrator.yml tests/test_workflow_contract.py
git commit -m "feat(ci): agentic-orchestrator.yml path-aware concurrency; drop protocol-advance"
```

---

## Task 5: `actionlint` CI gate + final contract sweep

**Files:**
- Create: `.github/workflows/lint.yml`
- Test: `tests/test_workflow_contract.py` (append a cross-file sweep)

**Interfaces:**
- Produces: a CI workflow that runs `actionlint` on push/PR; a structural sweep test asserting NO workflow references the retired coordinate/dispatch.

- [ ] **Step 1: Write the failing sweep test**

```python
# tests/test_workflow_contract.py (append)
def test_no_workflow_references_retired_mechanisms():
    for name in ("agentic-engine.yml", "protocol-join.yml", "agentic-orchestrator.yml"):
        t = _load(name)
        assert "protocol-advance" not in t, name
        assert "client_payload.branch" not in t, name
        assert "client_payload.substate" not in t, name

def test_lint_workflow_runs_actionlint():
    t = _load("lint.yml")
    assert "actionlint" in t
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_workflow_contract.py -k "retired or lint_workflow" -v` → FAIL (lint.yml absent).

- [ ] **Step 3: Create `.github/workflows/lint.yml`**

```yaml
name: Lint workflows
on:
  push:
    paths: [".github/workflows/**"]
  pull_request:
    paths: [".github/workflows/**"]
permissions:
  contents: read
jobs:
  actionlint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - name: Run actionlint
        run: |
          bash <(curl -s https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash)
          ./actionlint -color
```

- [ ] **Step 4: Run** — `pytest tests/test_workflow_contract.py -v` → all PASS; `pytest tests/ -q` green. If `actionlint` is available locally, run it on all four workflows and confirm clean.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/lint.yml tests/test_workflow_contract.py
git commit -m "feat(ci): actionlint workflow + cross-file no-legacy contract sweep"
```

---

## Self-review checklist (run before the final whole-branch review)

- [ ] `pytest tests/ -q` green (baseline 401 + the new emit/contract tests).
- [ ] `tests/test_emit_legs.py`: legs + run-agent carry leaf path + workflow for code-review, recover, deep-fanout.
- [ ] `grep -rn "client_payload.branch\|client_payload.substate\|client_payload.phase\|protocol-advance\|agent-workflow" .github/workflows/` → only expected hits (none for the retired ones).
- [ ] `actionlint` clean on all four workflow files (or noted unavailable + CI gate added).
- [ ] No `NODE_PATH`/`client_payload.path`/feedback string interpolated into a `run:` block (env-only).
- [ ] Suite green at every task commit.

## Out of scope (Stage 4c)

Live `deep-review-stub` protocol + gh-aw agents + live PR verification of deep/code-review/recover; dropping the residual `client_payload[branch]/[substate]` from advance.py's iterate redispatch.
