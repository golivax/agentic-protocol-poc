# Orchestrator B→A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-protocol trigger shim (`multi-grumpy-trigger.yml`) with one engine-owned, self-routing router workflow (`agentic-orchestrator.yml`) so authoring a protocol needs zero workflow YAML.

**Architecture:** A new `route` helper in `lib.py` scans all `protocols/*/protocol.json` `triggers` and returns the single matching protocol path (erroring on ambiguity). A new router workflow declares the union static `on:`, runs a read-only `route` job, then conditionally calls the unchanged reusable engine (`agentic-engine.yml`) with `protocol: <routed path>`. `protocol-join.yml` and `agentic-engine.yml` are untouched.

**Tech Stack:** Python 3 + PyYAML (engine runtime), pytest (dev-only tests), GitHub Actions reusable workflows, actionlint (cross-validation).

**Spec:** `docs/superpowers/specs/2026-06-16-orchestrator-b-to-a-spec.md`.

## Global Constraints

- **Engine stays byte-identical.** Do NOT modify `.github/workflows/agentic-engine.yml`. A diff to it is a red flag to stop. (Spec non-goal.)
- **`protocol-join.yml` stays untouched** — it already reads `client_payload.protocol` and owns the `protocol-join` dispatch type.
- **Permission ceiling:** the router's workflow-level `permissions` AND the `engine` calling job's `permissions` must BOTH grant the union `contents: read, issues: write, pull-requests: write, checks: write, actions: read` — else the reusable engine `startup_failure`s before any job runs. actionlint will NOT catch this.
- **No `inputs` in router `run-name`/`concurrency`** — the router is the triggering workflow (not `workflow_call`), so it uses `github.event.*` only. (It has no `inputs`, so this is automatic, but keep run-name/concurrency `github.event`-based.)
- **Agent-derived strings via env, never `${{ }}` in `run:`.** `COMMENT_BODY`, `DISPATCH_PROTOCOL` flow through `env:` and are passed to the CLI as quoted shell variables (the live-proven `match-trigger` pattern), never interpolated into the `run:` body.
- **`lib.match_trigger` and `lib.agent_workflow` are unchanged** — `route` composes `match_trigger`.
- Runtime deps: Python 3 + PyYAML only (pytest is dev-only). `glob`, `json`, `os` are already imported in `lib.py`.
- Tests are pytest under `tests/test_*.py`; full suite is currently **223 tests** and must stay green.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `.github/agent-factory/engine/lib.py` | add `route()` function + `route` CLI subcommand | modify |
| `tests/test_route.py` | unit + CLI tests for `route` | create |
| `.github/workflows/agentic-orchestrator.yml` | the router workflow | create |
| `.github/workflows/multi-grumpy-trigger.yml` | the old per-protocol shim | delete |
| `CLAUDE.md`, `docs/STATUS.md`, `docs/BACKLOG.md` | architecture-map references to the shim/orchestrator | modify (docs) |

Task order: **T1** (`route` helper, TDD) → **T2** (router workflow) → **T3** (delete shim + docs) → **T4** (actionlint + live equivalence run).

---

### Task 1: `lib.route` helper + CLI subcommand

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add `route()` after `agent_workflow()` ~line 159; add `route` CLI branch in `_cli` near the `match-trigger` branch ~line 504)
- Test: `tests/test_route.py` (create)

**Interfaces:**
- Consumes: `lib.match_trigger(proto_dict, event_name, action, comment_body) -> command|""` (existing, unchanged).
- Produces:
  - `lib.route(protocols_dir, event_name, action="", comment_body="", dispatch_protocol="", is_pr_comment=True) -> {"protocol": str, "command": str, "skip": bool}`. Raises `ValueError` when ≥2 protocols match the same entry event.
  - CLI: `python3 lib.py route <protocols_dir> <event_name> <action> <comment_body> <dispatch_protocol> <is_pr_comment>` → prints two `$GITHUB_OUTPUT`-style lines `protocol=<path>` and `skip=<true|false>` on success; exits non-zero (message to stderr) on ambiguous match.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_route.py`:

```python
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

# A protocol that matches /grumpy comments + PR opened/reopened/synchronize.
GRUMPY_TRIGGERS = [
    {"on": "issue_comment", "comment_prefix": "/grumpy", "command": "start"},
    {"on": "pull_request", "actions": ["opened", "reopened"], "command": "start"},
    {"on": "pull_request", "actions": ["synchronize"], "command": "reset"},
]


def _mk_protocols(tmp_path, protos):
    """protos: {dirname: triggers_list}. Lays down protocols/<dir>/protocol.json."""
    root = tmp_path / "protocols"
    for name, triggers in protos.items():
        d = root / name
        d.mkdir(parents=True)
        (d / "protocol.json").write_text(json.dumps({"name": name, "triggers": triggers}))
    return str(root)


# route() — entry events --------------------------------------------------------

def test_single_protocol_pr_opened_routes():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"multi-grumpy": GRUMPY_TRIGGERS})
        r = lib.route(pdir, "pull_request", "opened", "")
        assert r["skip"] is False
        assert r["protocol"].endswith("multi-grumpy/protocol.json")
        assert r["command"] == "start"


def test_comment_prefix_routes():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"multi-grumpy": GRUMPY_TRIGGERS})
        r = lib.route(pdir, "issue_comment", "", "/grumpy please", is_pr_comment=True)
        assert r["skip"] is False
        assert r["command"] == "start"


def test_no_match_skips():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"multi-grumpy": GRUMPY_TRIGGERS})
        r = lib.route(pdir, "issue_comment", "", "lgtm", is_pr_comment=True)
        assert r["skip"] is True
        assert r["protocol"] == ""


def test_non_pr_comment_skips_without_scanning():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"multi-grumpy": GRUMPY_TRIGGERS})
        r = lib.route(pdir, "issue_comment", "", "/grumpy", is_pr_comment=False)
        assert r["skip"] is True


def test_dispatch_protocol_passthrough():
    # repository_dispatch: protocol is carried on the payload; no scan, no skip.
    r = lib.route("/nonexistent", "repository_dispatch", "",
                  dispatch_protocol=".github/agent-factory/protocols/multi-grumpy/protocol.json")
    assert r["skip"] is False
    assert r["protocol"] == ".github/agent-factory/protocols/multi-grumpy/protocol.json"


def test_ambiguous_match_raises():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {
            "alpha": GRUMPY_TRIGGERS,
            "beta": GRUMPY_TRIGGERS,
        })
        try:
            lib.route(pdir, "pull_request", "opened", "")
            assert False, "expected ValueError on ambiguous match"
        except ValueError as e:
            assert "alpha" in str(e) and "beta" in str(e)


def test_globbing_is_sorted_deterministic():
    # Only one matches → no ambiguity; this asserts a non-matching sibling is ignored.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {
            "zeta-nomatch": [{"on": "pull_request", "actions": ["closed"], "command": "x"}],
            "alpha-match": GRUMPY_TRIGGERS,
        })
        r = lib.route(pdir, "pull_request", "opened", "")
        assert r["protocol"].endswith("alpha-match/protocol.json")


# CLI ---------------------------------------------------------------------------

def _cli(*args):
    r = subprocess.run(["python3", str(ENGINE / "lib.py"), "route", *map(str, args)],
                       text=True, capture_output=True)
    return r


def test_cli_route_prints_github_output_lines(tmp_path):
    pdir = _mk_protocols(tmp_path, {"multi-grumpy": GRUMPY_TRIGGERS})
    r = _cli(pdir, "pull_request", "opened", "", "", "false")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "skip=false" in out
    assert "protocol=" in out and "multi-grumpy/protocol.json" in out


def test_cli_route_skip(tmp_path):
    pdir = _mk_protocols(tmp_path, {"multi-grumpy": GRUMPY_TRIGGERS})
    r = _cli(pdir, "issue_comment", "", "lgtm", "", "true")
    assert r.returncode == 0, r.stderr
    assert "skip=true" in r.stdout


def test_cli_route_ambiguous_exits_nonzero(tmp_path):
    pdir = _mk_protocols(tmp_path, {"alpha": GRUMPY_TRIGGERS, "beta": GRUMPY_TRIGGERS})
    r = _cli(pdir, "pull_request", "opened", "", "", "false")
    assert r.returncode != 0
    assert "ambiguous" in r.stderr.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_route.py -v`
Expected: FAIL — `AttributeError: module 'lib' has no attribute 'route'` (and the CLI tests exit non-zero with "unknown subcommand route").

- [ ] **Step 3: Add the `route()` function**

In `.github/agent-factory/engine/lib.py`, immediately AFTER the `agent_workflow()` function (the block ending ~line 159, before the next top-level `def`), insert:

```python
def route(protocols_dir, event_name, action="", comment_body="",
          dispatch_protocol="", is_pr_comment=True):
    """Pick the protocol to run for an incoming event by scanning all
    protocols/*/protocol.json `triggers` blocks. Protocol-agnostic router core.

    Returns {"protocol": <path>, "command": <cmd>, "skip": <bool>}:
      - repository_dispatch (dispatch_protocol set): pass it through, no scan.
        The engine re-derives the command from the dispatch type.
      - issue_comment on a non-PR issue: skip (the engine ignores these anyway).
      - entry event (pull_request / PR issue_comment): glob protocols in sorted
        order, run match_trigger on each; 0 matches -> skip, exactly 1 -> route,
        >=2 -> raise ValueError (ambiguous; the router job then fails loudly).
    """
    if dispatch_protocol:
        return {"protocol": dispatch_protocol, "command": "", "skip": False}
    if event_name == "issue_comment" and not is_pr_comment:
        return {"protocol": "", "command": "", "skip": True}
    matches = []
    for path in sorted(glob.glob(os.path.join(protocols_dir, "*", "protocol.json"))):
        with open(path) as f:
            proto = json.load(f)
        cmd = match_trigger(proto, event_name, action, comment_body)
        if cmd:
            matches.append((path, cmd))
    if not matches:
        return {"protocol": "", "command": "", "skip": True}
    if len(matches) > 1:
        names = ", ".join(p for p, _ in matches)
        raise ValueError(
            f"ambiguous route: {len(matches)} protocols match "
            f"{event_name}/{action or comment_body}: {names}")
    path, cmd = matches[0]
    return {"protocol": path, "command": cmd, "skip": False}
```

- [ ] **Step 4: Add the `route` CLI subcommand**

In `.github/agent-factory/engine/lib.py`, in the `_cli` function, AFTER the `agent-workflow` branch (ends ~line 518, `print(agent_workflow(proto, ph, br))`) and BEFORE the `else:` unknown-subcommand branch, insert:

```python
    elif cmd == "route":
        # route <protocols_dir> <event_name> <action> <comment_body> <dispatch_protocol> <is_pr_comment>
        pdir = args[0]
        ev = args[1] if len(args) > 1 else ""
        act = args[2] if len(args) > 2 else ""
        body = args[3] if len(args) > 3 else ""
        disp = args[4] if len(args) > 4 else ""
        ispr = (args[5].lower() == "true") if len(args) > 5 else True
        try:
            r = route(pdir, ev, act, body, disp, ispr)
        except ValueError as e:
            sys.stderr.write(f"lib.py route: {e}\n")
            sys.exit(1)
        print(f"protocol={r['protocol']}")
        print(f"skip={'true' if r['skip'] else 'false'}")
```

- [ ] **Step 5: Run the tests and the full suite**

Run: `pytest tests/test_route.py -v && pytest tests/ -q`
Expected: `test_route.py` all PASS; full suite PASS (224 tests — 223 prior + the new module's count; the prior 223 must all still pass).

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_route.py
git commit -m "feat(engine): lib.route — scan all protocols' triggers, error on ambiguous

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `agentic-orchestrator.yml` router workflow

**Files:**
- Create: `.github/workflows/agentic-orchestrator.yml`

**Interfaces:**
- Consumes: `lib.py route <protocols_dir> <event_name> <action> <comment_body> <dispatch_protocol> <is_pr_comment>` (Task 1) — prints `protocol=`/`skip=` lines; `.github/workflows/agentic-engine.yml` reusable workflow with input `protocol` (existing, unchanged).
- Produces: a workflow that on PR/comment/dispatch routes to the matching protocol and calls the engine. No downstream task consumes its outputs.

- [ ] **Step 1: Create the router workflow**

Create `.github/workflows/agentic-orchestrator.yml`:

```yaml
name: Agentic Orchestrator
# Engine-owned, protocol-agnostic router. Declares the UNION of all protocols'
# entry triggers (a reusable workflow_call workflow cannot declare `on:` itself),
# runs a read-only `route` job to pick the matching protocol, then calls the
# generic engine. run-name + concurrency live HERE (the caller), github.event-based,
# never `inputs` (which would startup_failure a workflow_call workflow).
run-name: "agentic · ${{ github.event.client_payload.instance || format('pr-{0}', github.event.issue.number || github.event.pull_request.number) }}"

on:
  pull_request:
    types: [opened, synchronize, reopened]
  issue_comment:
    types: [created]
  repository_dispatch:
    types: [protocol-continue, protocol-advance]   # protocol-join owned by protocol-join.yml

# The calling job's permissions are the CEILING for the reusable engine. Grant the
# UNION the engine's jobs need; the engine's per-job permissions scope down within it.
# (State writes use POC_DISPATCH_TOKEN, not GITHUB_TOKEN, so contents stays read.)
permissions:
  contents: read
  issues: write
  pull-requests: write
  checks: write
  actions: read

concurrency:
  # Static prefix + instance + branch. Protocol is unknown at concurrency-eval
  # time, so all protocols share this namespace (fine for one-pipeline-per-repo).
  # branch is empty for entry events / agent phases; set for fan-out branch continues.
  group: agentic-${{ github.event.client_payload.instance || format('pr-{0}', github.event.issue.number || github.event.pull_request.number) }}-${{ github.event.client_payload.branch }}
  cancel-in-progress: false

jobs:
  route:
    # Read-only. Picks the protocol path (entry events) or passes through the
    # dispatch protocol; decides skip. Holds NO state PAT.
    if: >
      github.event_name == 'repository_dispatch' ||
      github.event_name == 'pull_request' ||
      (github.event_name == 'issue_comment' && github.event.issue.pull_request != null)
    runs-on: ubuntu-latest
    outputs:
      protocol: ${{ steps.r.outputs.protocol }}
      skip: ${{ steps.r.outputs.skip }}
    steps:
      - uses: actions/checkout@v4
      - id: r
        env:
          EVENT_NAME: ${{ github.event_name }}
          PR_EVENT_ACTION: ${{ github.event.action }}
          COMMENT_BODY: ${{ github.event.comment.body }}
          DISPATCH_PROTOCOL: ${{ github.event.client_payload.protocol }}
          IS_PR_COMMENT: ${{ github.event.issue.pull_request != null }}
        run: |
          # Agent-derived strings (COMMENT_BODY, DISPATCH_PROTOCOL) are read via env
          # and passed as quoted shell vars, NEVER interpolated into this run: block.
          # lib.py route prints `protocol=`/`skip=` to stdout and exits non-zero only
          # on a genuine error (incl. an ambiguous multi-protocol match -> red job).
          python3 .github/agent-factory/engine/lib.py route \
            .github/agent-factory/protocols \
            "$EVENT_NAME" "$PR_EVENT_ACTION" "$COMMENT_BODY" \
            "$DISPATCH_PROTOCOL" "$IS_PR_COMMENT" \
            >> "$GITHUB_OUTPUT"

  engine:
    needs: route
    if: ${{ needs.route.outputs.skip != 'true' }}
    uses: ./.github/workflows/agentic-engine.yml
    with:
      protocol: ${{ needs.route.outputs.protocol }}
    secrets: inherit
    permissions:
      contents: read
      issues: write
      pull-requests: write
      checks: write
      actions: read
```

- [ ] **Step 2: Lint the workflow locally (YAML well-formedness)**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/agentic-orchestrator.yml'))" && echo OK`
Expected: `OK` (no YAML parse error).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/agentic-orchestrator.yml
git commit -m "feat(orchestrator): self-routing agentic-orchestrator.yml (router → engine)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Delete the shim + update docs

**Files:**
- Delete: `.github/workflows/multi-grumpy-trigger.yml`
- Modify: `CLAUDE.md` (architecture map — the `.github/workflows/` block lists `orchestrator.yml`; update to `agentic-orchestrator.yml` + note the router; remove the multi-grumpy-trigger/`orchestrator.yml` shim mention)
- Modify: `docs/STATUS.md`, `docs/BACKLOG.md` (mark Orchestrator B→A done; describe the router)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing (cleanup + docs).

- [ ] **Step 1: Confirm nothing references the shim**

Run:
```bash
grep -rn "multi-grumpy-trigger" .github docs CLAUDE.md || echo "no refs"
grep -rln "uses:.*multi-grumpy-trigger" .github/workflows || echo "no callers"
```
Expected: the only hits are the file itself + any doc prose you will update next. No workflow `uses:` it (it's a triggered workflow, not reusable).

- [ ] **Step 2: Delete the shim**

```bash
git rm .github/workflows/multi-grumpy-trigger.yml
```

- [ ] **Step 3: Update `CLAUDE.md` architecture map**

In `CLAUDE.md`, the `.github/workflows/` section currently reads (around the orchestrator line):

```
  orchestrator.yml     the 4 trust zones; maps GitHub events -> engine commands
```

Replace that line with:

```
  agentic-orchestrator.yml  the router: union static on:, read-only route job
                       (lib.route scans all protocols' triggers), then calls the
                       reusable engine. Replaces the per-protocol trigger shim.
  agentic-engine.yml   reusable on:workflow_call engine — the 4 trust zones
                       (plan→dispatch→checks→advance) for one protocol path.
```

Also update the prose in the "What this is" / deploys paragraph if it names `orchestrator.yml` as the deployer — the router now selects the protocol via `lib.route`; the hardcoded `multi-grumpy/protocol.json` path is gone. (Search `CLAUDE.md` for `orchestrator.yml` and `multi-grumpy-trigger` and reconcile each mention with the router model.)

- [ ] **Step 4: Update `docs/STATUS.md` and `docs/BACKLOG.md`**

- In `docs/BACKLOG.md`: mark the "Orchestrator B→A" item DONE (or move it to a done/changelog section), referencing `agentic-orchestrator.yml` + `lib.route`.
- In `docs/STATUS.md`: where it describes the orchestrator/shim wiring, update to the router model (one `agentic-orchestrator.yml` + `lib.route`, no per-protocol shim). Keep the four-trust-zone description (that's the engine, unchanged).

- [ ] **Step 5: Verify the test suite is still green (no code changed, sanity)**

Run: `pytest tests/ -q`
Expected: PASS (same count as Task 1 Step 5).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(orchestrator): delete per-protocol shim; docs → router model

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: actionlint cross-validation + LIVE equivalence run (binding proof)

**Files:** none (verification only).

**Interfaces:**
- Consumes: the merged router + engine on `main`.
- Produces: the binding live proof that B→A == B behavior.

> **This task is the real acceptance gate.** pytest + actionlint are necessary but NOT sufficient — actionlint does NOT catch the reusable-workflow permission ceiling. Only the live GitHub run proves the router→engine call works.

- [ ] **Step 1: Install actionlint (ephemeral — reinstall per session)**

Run: `GOBIN=/tmp/gobin go install github.com/rhysd/actionlint/cmd/actionlint@latest`
Expected: exits 0; `/tmp/gobin/actionlint` exists. (curl-to-bash install is sandbox-blocked; `go` is available.)

- [ ] **Step 2: Run actionlint in project mode**

Run: `/tmp/gobin/actionlint`
Expected: no errors for `agentic-orchestrator.yml` (it cross-validates the `uses: ./.github/workflows/agentic-engine.yml` reusable call — input names, output refs, expression syntax). REMEMBER: it will NOT flag a permission-ceiling problem — that is caught only by the live run.

- [ ] **Step 3: Merge to `main`**

This plan's branch is `feat/orchestrator-b-to-a`. Fast-forward merge to `main` and push (workflows run from the default branch for `issue_comment`/`repository_dispatch`):
```bash
git checkout main && git merge --ff-only feat/orchestrator-b-to-a && git push origin main
```

- [ ] **Step 4: LIVE — trigger via `pull_request: opened`**

Use PR #55 / branch `m2b-live2` (the throwaway `examples/clamp.py` diff) or open a fresh PR. Reopen or push to trigger `opened`/`synchronize`.
Verify on the GitHub web UI (the API hides `startup_failure` causes):
- The **Agentic Orchestrator** run appears; its `route` job picks `multi-grumpy` (check the `route` step log: `protocol=...multi-grumpy/protocol.json`, `skip=false`).
- The `engine` job runs the full fan-out (grumpy + security legs), aggregate + sub check-runs appear, a single shared status comment is posted, and the join completes.
Expected: outcome IDENTICAL to the M2b live result (PR #55, run 27654871019). No `startup_failure`.

- [ ] **Step 5: LIVE — trigger via `/grumpy` comment**

Comment `/grumpy` on the same PR.
Expected: same as Step 4 — router selects `multi-grumpy`, full fan-out, single status comment, join completes.

- [ ] **Step 6: Negative control — confirm no-match no-ops**

Comment something without a trigger prefix (e.g. `lgtm`) on the PR, OR observe an unrelated `issue_comment`.
Expected: the **Agentic Orchestrator** run's `route` job emits `skip=true`, the `engine` job is skipped (green run, no engine work). Confirms the repo-wide firing no-ops cleanly.

- [ ] **Step 7: Record the result**

Update the project memory (`code-review-pipeline-progress.md`) and `docs/STATUS.md`: B→A live-verified, router run id, any live-only bug caught. If a `startup_failure` appears, treat it as the permission-ceiling trap (re-check the union grant on BOTH the workflow-level `permissions` and the `engine` job `permissions`) — do NOT mark the task done until both live triggers succeed.

---

## Self-Review (against the spec)

**Spec coverage:**
- Decision 1 (error-on-ambiguous) → Task 1 `route()` raises `ValueError`; `test_ambiguous_match_raises` + `test_cli_route_ambiguous_exits_nonzero`. ✓
- Decision 2 (join separate) → Global Constraint + router `on:` omits `protocol-join`; `protocol-join.yml` untouched. ✓
- Decision 3 (concurrency key) → Task 2 `concurrency.group: agentic-<instance>-<branch>`. ✓
- Decision 4 (naming/cleanup) → Task 2 creates `agentic-orchestrator.yml`; Task 3 deletes `multi-grumpy-trigger.yml`. ✓
- Decision 5 (engine input = protocol only) → Task 2 `with: { protocol: ... }`; engine untouched (Global Constraint). ✓
- `lib.route` contract (dispatch passthrough / non-PR-comment skip / 0→skip / 1→route / ≥2→raise) → Task 1 function + all 7 `route()` tests. ✓
- CLI prints `$GITHUB_OUTPUT` lines, exits non-zero on ambiguous → Task 1 Step 4 + CLI tests. ✓
- Verification (pytest 223 green + actionlint + live PR-open & /grumpy & negative control) → Task 1 Step 5, Task 4. ✓
- Permission-ceiling union on BOTH workflow + engine job → Task 2 (both `permissions:` blocks) + Global Constraint + Task 4 Step 7. ✓

**Placeholder scan:** no TBD/TODO; every code step shows full code; no "similar to Task N". ✓

**Type consistency:** `route(protocols_dir, event_name, action, comment_body, dispatch_protocol, is_pr_comment) -> {"protocol","command","skip"}` used identically in the function def (T1 S3), CLI (T1 S4), tests (T1 S1), and the router CLI invocation (T2 S1). CLI prints `protocol=`/`skip=`; router consumes `steps.r.outputs.protocol`/`.skip` and `needs.route.outputs.protocol`/`.skip`. Consistent. ✓
