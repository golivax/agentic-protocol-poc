# M2b — Generic Reusable Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the multi-grumpy-specific `orchestrator.yml` into an engine-owned, protocol-agnostic reusable `workflow_call` workflow plus a thin per-protocol trigger shim, and wire the phase relay (`protocol-advance` → `advance-phase` + `PHASE`, and `protocol-continue` carrying `phase`) so multi-phase pipelines can run live.

**Architecture:** Approach **B** from the spec. A new engine-owned `.github/workflows/agentic-engine.yml` (`on: workflow_call`, input = the protocol.json path) runs the full 4-trust-zone graph generically: it derives the engine command from the event (entry events via a protocol `triggers` block, internal `repository_dispatch` types generically), matrixes the dispatch/checks/advance jobs over a *legs* list (the `[""]` sentinel for a single-agent or agent phase; N branch ids for a fan-out phase), threads `PHASE`/`BRANCH` per leg, and uses the protocol id for the aggregate check-run. Each protocol carries a thin trigger shim (`on:` + `uses:` + `secrets: inherit`); `orchestrator.yml` is renamed to `multi-grumpy-trigger.yml`. The already-generic `protocol-join.yml` is unchanged.

**Tech Stack:** GitHub Actions (reusable `workflow_call` workflows), Python 3 + PyYAML engine, `gh` CLI, `gh-aw` compiled agent locks, `actionlint` for YAML, `pytest` for the pure engine helpers.

## Global Constraints

- **Trust-zone invariant (verbatim from CLAUDE.md):** the engine and the agent never share a job or a credential. Zone 1 `plan` holds the state PAT (`POC_DISPATCH_TOKEN`); zone 2 `dispatch` holds only a read repo token + LLM creds (the sandboxed `gh-aw` agent); zone 3 `checks` holds nothing beyond the default read token; zone 4 `advance` holds the state PAT + publish token. Generalizing the workflow MUST preserve which secret each job references.
- **Agent-derived strings via `env:`, never `run:` interpolation** — `feedback`, `verdicts`, filenames, and any `client_payload` value are passed to shell steps through `env:` blocks, never interpolated into `run:` text, to prevent shell injection into the state-PAT-holding jobs.
- **State advances only by fast-forward CAS push** — never force-push `agentic-state`. (Unchanged here; the engine scripts already own this.)
- **`orchestrator.yml` + agent locks live on the default branch (`main`)** — workflows run from `main` for `issue_comment` / `repository_dispatch`. The new `agentic-engine.yml` + the renamed shim must land on `main` before any live trigger works.
- **Byte-identical legacy paths** — `grumpy-review` (single-agent) and `multi-grumpy` (one fan-out) keep their existing engine behavior. All new orchestrator behavior is gated by event type / `PHASE` / the `[""]` sentinel; the 196-test regression suite must stay green after every engine change.
- **Runtime deps are Python 3 + PyYAML only** — no new third-party imports in `.github/agent-factory/engine/` or `lib.py`. `pytest` is dev-only.
- **Verification reality:** the pure engine helpers (Tasks 1, 2, 6) are pytest-verified and regression-guarded. The YAML workflows (Tasks 3, 4) cannot be pytest-tested — they are gated by `actionlint` + structural assertion + a **post-merge live equivalence run** (Task 5). YAML correctness is not "done" until Task 5's live run passes on `main`.

---

## File Structure

**Created:**
- `.github/workflows/agentic-engine.yml` — the generic engine reusable workflow (`on: workflow_call`).
- `.github/workflows/multi-grumpy-trigger.yml` — the thin shim for the multi-grumpy deployment (replaces `orchestrator.yml`).
- `tests/test_triggers.py` — unit tests for `lib.match_trigger` + `lib.agent_workflow`.
- `tests/test_phase_relay.py` — unit test that an agent-phase iterate re-dispatch carries `phase`.

**Modified:**
- `.github/agent-factory/engine/lib.py` — add `match_trigger()`, `agent_workflow()`, and their `lib.py <subcommand>` CLI dispatch.
- `.github/agent-factory/engine/advance.py` — the `protocol-continue` re-dispatch carries `client_payload[phase]` when `phase` is set.
- `.github/agent-factory/protocols/multi-grumpy/protocol.json` — add a `triggers` block reproducing today's hardcoded event mapping.
- `.github/agent-factory/protocols/grumpy/protocol.json` — add a `triggers` block (so the single-agent topology is also driveable by the generic engine; not deployed live in M2b).
- `tests/test_engine.py` (only if a test asserts the exact `protocol-continue` payload shape — extend, do not weaken).

**Deleted:**
- `.github/workflows/orchestrator.yml` — replaced by `multi-grumpy-trigger.yml` (done as a `git mv` in Task 4).

**Unchanged (call out so nobody "fixes" them):**
- `.github/workflows/protocol-join.yml` — already protocol-agnostic (reads `github.event.client_payload.protocol`). The shim does NOT listen for `protocol-join`; this file owns that dispatch type for every protocol.
- `.github/agent-factory/engine/next.py`, `join.py`, `run-checks.py` — already `PHASE`-aware from M2a.

---

## Interfaces (the contracts later tasks rely on)

- `lib.match_trigger(protocol: dict, event_name: str, action: str = "", comment_body: str = "") -> str` — maps an **entry** GitHub event to an engine command via `protocol["triggers"]`; returns `""` when nothing matches (caller no-ops). Does NOT handle internal `repository_dispatch` re-entries.
- `lib.agent_workflow(protocol: dict, phase: str = "", branch: str = "") -> str` — resolves the gh-aw agent workflow basename (e.g. `"grumpy-agent"`) for a leg; `""` if unresolved.
- `lib.py match-trigger <protocol.json> <event_name> <action> <comment_body>` → prints the command (or empty line).
- `lib.py agent-workflow <protocol.json> <phase> <branch>` → prints the workflow basename (or empty line).
- Engine command surface consumed by the workflow (already exists in `next.py`): `start` | `reset` | `continue` (env `BRANCH`, optional `PHASE`) | `advance-phase` (env `PHASE`). Action JSON keys: `action` ∈ {`run-agent`,`run-fanout`,`halt`}, `iteration`, `feedback`, optional `phase`, and for `run-fanout` a `branches[]` with `{id,workflow,...}`.
- Internal dispatch payloads (already fired by `advance.py`/`join.py`): `protocol-continue {protocol,instance,branch[,phase]}`, `protocol-advance {protocol,instance,phase}`, `protocol-join {protocol,instance}`.

---

### Task 1: Engine helpers — `match_trigger` + `agent_workflow` + CLI

**Files:**
- Modify: `.github/agent-factory/engine/lib.py`
- Test: `tests/test_triggers.py` (create)

**Interfaces:**
- Consumes: `lib.state_by_id(protocol, state_id)` (exists from M2a).
- Produces: `lib.match_trigger(...)`, `lib.agent_workflow(...)`, and `lib.py match-trigger` / `lib.py agent-workflow` CLI subcommands (consumed by `agentic-engine.yml` in Task 3).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_triggers.py`:

```python
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

MULTI = {
    "name": "multi-grumpy",
    "triggers": [
        {"on": "issue_comment", "comment_prefix": "/grumpy", "command": "start"},
        {"on": "pull_request", "actions": ["opened", "reopened"], "command": "start"},
        {"on": "pull_request", "actions": ["synchronize"], "command": "reset"},
    ],
    "states": [
        {"id": "review", "kind": "fanout", "next": "join", "branches": [
            {"id": "grumpy", "workflow": "grumpy-agent"},
            {"id": "security", "workflow": "security-agent"},
        ]},
        {"id": "join", "kind": "join", "of": "review", "next": "done"},
    ],
}

SINGLE = {
    "name": "grumpy-review",
    "triggers": [{"on": "pull_request", "actions": ["opened"], "command": "start"}],
    "states": [
        {"id": "review", "kind": "agent", "workflow": "grumpy-agent", "next": "publish"},
        {"id": "publish", "kind": "deterministic", "next": None},
    ],
}

PIPELINE = {
    "name": "pipe",
    "states": [
        {"id": "gate", "kind": "agent", "workflow": "preflight-agent", "next": "review"},
        {"id": "review", "kind": "fanout", "next": "join", "branches": [
            {"id": "grumpy", "workflow": "grumpy-agent"},
        ]},
        {"id": "join", "kind": "join", "of": "review", "next": "done"},
    ],
}


# match_trigger ----------------------------------------------------------------

def test_issue_comment_prefix_match():
    assert lib.match_trigger(MULTI, "issue_comment", "", "/grumpy please") == "start"


def test_issue_comment_prefix_no_match():
    assert lib.match_trigger(MULTI, "issue_comment", "", "lgtm") == ""


def test_pull_request_opened_starts():
    assert lib.match_trigger(MULTI, "pull_request", "opened", "") == "start"


def test_pull_request_synchronize_resets():
    assert lib.match_trigger(MULTI, "pull_request", "synchronize", "") == "reset"


def test_pull_request_unlisted_action_no_match():
    assert lib.match_trigger(MULTI, "pull_request", "labeled", "") == ""


def test_no_triggers_block_returns_empty():
    assert lib.match_trigger({"name": "x"}, "pull_request", "opened", "") == ""


# agent_workflow ---------------------------------------------------------------

def test_workflow_single_agent_first_state():
    assert lib.agent_workflow(SINGLE) == "grumpy-agent"


def test_workflow_fanout_branch():
    assert lib.agent_workflow(MULTI, branch="security") == "security-agent"


def test_workflow_fanout_unknown_branch_empty():
    assert lib.agent_workflow(MULTI, branch="nope") == ""


def test_workflow_agent_phase():
    assert lib.agent_workflow(PIPELINE, phase="gate") == "preflight-agent"


def test_workflow_fanout_phase_branch():
    assert lib.agent_workflow(PIPELINE, phase="review", branch="grumpy") == "grumpy-agent"


# CLI --------------------------------------------------------------------------

def _write(tmp_path, proto):
    p = tmp_path / "protocol.json"
    p.write_text(json.dumps(proto))
    return p


def _cli(*args):
    r = subprocess.run(["python3", str(ENGINE / "lib.py"), *map(str, args)],
                       text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_cli_match_trigger(tmp_path):
    p = _write(tmp_path, MULTI)
    assert _cli("match-trigger", p, "pull_request", "synchronize", "") == "reset"


def test_cli_agent_workflow(tmp_path):
    p = _write(tmp_path, PIPELINE)
    assert _cli("agent-workflow", p, "review", "grumpy") == "grumpy-agent"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_triggers.py -q`
Expected: FAIL with `AttributeError: module 'lib' has no attribute 'match_trigger'` (and the CLI tests fail because the subcommands are unknown).

- [ ] **Step 3: Add the two functions to `lib.py`**

Add near the other pure protocol helpers (e.g. after `state_by_id` / `phase_states`):

```python
def match_trigger(protocol, event_name, action="", comment_body=""):
    """Map an ENTRY GitHub event to an engine command via protocol["triggers"].
    Returns the command ("start"/"reset"/...) or "" if nothing matches (the
    workflow then no-ops). Internal re-entry dispatches (protocol-continue /
    protocol-advance / protocol-join) are generic and NOT handled here."""
    for t in protocol.get("triggers", []):
        if t.get("on") != event_name:
            continue
        if event_name == "issue_comment":
            prefix = t.get("comment_prefix", "")
            if not prefix or comment_body.startswith(prefix):
                return t.get("command", "")
        elif event_name == "pull_request":
            actions = t.get("actions", [])
            if not actions or action in actions:
                return t.get("command", "")
        else:
            # generic event (e.g. workflow_dispatch): match on `on` alone.
            return t.get("command", "")
    return ""


def agent_workflow(protocol, phase="", branch=""):
    """Resolve the gh-aw agent workflow basename for a leg.
    phase set + fanout phase -> that branch's workflow;
    phase set + agent phase  -> the phase state's workflow;
    branch only (single-phase fanout) -> that branch's workflow;
    neither -> the first agent state's workflow. "" if unresolved."""
    if phase:
        st = state_by_id(protocol, phase)
        if st and st.get("kind") == "fanout":
            for b in st.get("branches", []):
                if b["id"] == branch:
                    return b.get("workflow", "")
            return ""
        return (st or {}).get("workflow", "")
    if branch:
        for st in protocol.get("states", []):
            if st.get("kind") == "fanout":
                for b in st.get("branches", []):
                    if b["id"] == branch:
                        return b.get("workflow", "")
        return ""
    for st in protocol.get("states", []):
        if st.get("kind") == "agent":
            return st.get("workflow", "")
    return ""
```

- [ ] **Step 4: Wire the CLI subcommands**

The CLI lives in `def _cli(argv):` (dispatched at `if __name__ == "__main__": _cli(sys.argv[1:])`); it sets `cmd, args = argv[0], argv[1:]` and has an `elif cmd == "...":` ladder ending in an `else:` that errors on unknown subcommands. Insert two new branches before that final `else:` (these load the protocol from `args[0]`):

```python
    elif cmd == "match-trigger":
        # match-trigger <protocol.json> <event_name> <action> <comment_body>
        with open(args[0]) as f:
            proto = json.load(f)
        ev = args[1] if len(args) > 1 else ""
        act = args[2] if len(args) > 2 else ""
        body = args[3] if len(args) > 3 else ""
        print(match_trigger(proto, ev, act, body))
    elif cmd == "agent-workflow":
        # agent-workflow <protocol.json> <phase> <branch>
        with open(args[0]) as f:
            proto = json.load(f)
        ph = args[1] if len(args) > 1 else ""
        br = args[2] if len(args) > 2 else ""
        print(agent_workflow(proto, ph, br))
```

(Ensure `json` is imported at module top — it already is, since `protocol_id` and other helpers parse JSON.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_triggers.py -q`
Expected: PASS (14 tests).

- [ ] **Step 6: Run the full suite (regression guard)**

Run: `pytest tests/ -q`
Expected: PASS — 196 prior + 14 new = 210 passed. (Pure additions; no existing behavior touched.)

- [ ] **Step 7: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_triggers.py
git commit -m "feat(engine): lib.match_trigger + lib.agent_workflow (+ CLI) for the generic orchestrator"
```

---

### Task 2: Phase relay — `protocol-continue` carries `phase`

**Files:**
- Modify: `.github/agent-factory/engine/advance.py:423-430` (the re-dispatch block)
- Test: `tests/test_phase_relay.py` (create)

**Interfaces:**
- Consumes: the `pipeline-mini` fixture (`tests/fixtures/pipeline-mini/`) created in M2a — protocol `name` is `pipeline-mini`; its first state is an `agent` phase `gate` (`max_iterations: 2`) with an `always-pass` check. Feeding a failing verdict at iteration 1 makes `decide()` return `iterate` (budget 2 > used 1), so advance re-dispatches.
- Produces: a `protocol-continue` dispatch payload that includes `client_payload[phase]=<phase>` whenever `advance.py` runs with `PHASE` set. The orchestrator (Task 3) relays that back to `PHASE` on re-entry.

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase_relay.py`. The test runs `advance.py` under `ENGINE_LOCAL=1` (so `gh api` is echoed to stderr, not executed) with `PHASE=gate` and a failing iterate-severity verdict, then asserts the echoed `protocol-continue` dispatch carries the phase:

```python
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
FIXTURE = ROOT / "tests/fixtures/pipeline-mini/protocol.json"


def test_continue_redispatch_carries_phase(tmp_path, state_origin):
    # Arrange: a CAS origin + a checked-out work dir seeded with an active gate phase.
    # advance.py re-clones from STATE_REMOTE, so seed via a push to the bare origin.
    work = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(state_origin), str(work)], check=True)
    inst = "pr-1"
    d = work / "pipeline-mini" / inst
    d.mkdir(parents=True, exist_ok=True)
    (d / "gate.yaml").write_text(
        "protocol: pipeline-mini\ninstance: pr-1\nstate: gate\niteration: 1\ngates: {}\nhistory: []\n"
    )
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "seed"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "agentic-state"], check=True)

    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": False, "feedback": "forced fail", "on_fail": "iterate"}
    ]}))
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"type": "object"}))

    env = dict(os.environ)
    env["ENGINE_LOCAL"] = "1"
    env["STATE_REMOTE"] = str(state_origin)
    env["PHASE"] = "gate"
    env["GITHUB_REPOSITORY"] = "owner/repo"

    r = subprocess.run(
        ["python3", str(ENGINE / "advance.py"), str(tmp_path / "adv"), inst,
         str(FIXTURE), str(verdicts), str(evidence)],
        text=True, capture_output=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    # ENGINE_LOCAL echoes `gh api ...` to stderr. The continue dispatch must carry phase.
    assert "event_type=protocol-continue" in r.stderr
    assert "client_payload[phase]=gate" in r.stderr
```

> Verified against the M2a fixture: protocol `name` is `pipeline-mini`, first agent phase is `gate` with `max_iterations: 2`. A failing verdict at iteration 1 → `iterate` (re-dispatch fires), which is what makes this test meaningful. If the fixture changes, re-confirm `decide()` returns `iterate` here.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_phase_relay.py -q`
Expected: FAIL — stderr contains `event_type=protocol-continue` but NOT `client_payload[phase]=gate` (today's re-dispatch omits phase).

- [ ] **Step 3: Make the re-dispatch carry phase**

In `.github/agent-factory/engine/advance.py`, replace the re-dispatch call (currently lines ~423–430):

```python
        # Re-dispatch
        gh_api(
            f"repos/{github_repository}/dispatches",
            "-f", "event_type=protocol-continue",
            "-F", f"client_payload[protocol]={pid}",
            "-F", f"client_payload[instance]={instance}",
            "-F", f"client_payload[branch]={branch}",
        )
```

with:

```python
        # Re-dispatch. Carry `phase` so a multi-phase agent/fan-out phase resumes
        # in the SAME phase on re-entry (the orchestrator relays payload.phase ->
        # PHASE). Empty/absent for single-phase protocols → byte-identical payload.
        redispatch = [
            f"repos/{github_repository}/dispatches",
            "-f", "event_type=protocol-continue",
            "-F", f"client_payload[protocol]={pid}",
            "-F", f"client_payload[instance]={instance}",
            "-F", f"client_payload[branch]={branch}",
        ]
        if phase:
            redispatch += ["-F", f"client_payload[phase]={phase}"]
        gh_api(*redispatch)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_phase_relay.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite (regression guard)**

Run: `pytest tests/ -q`
Expected: PASS — 210 + 1 = 211 passed. The single-phase payload is byte-identical (no `phase` key appended when `phase==""`), so `test_engine.py`/`test_fanout_e2e.py` stay green. If any test asserted the *exact* continue payload and now sees an extra arg, that test was running with `PHASE` set — fix the test's expectation, do not weaken the feature.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/advance.py tests/test_phase_relay.py
git commit -m "feat(engine): protocol-continue re-dispatch carries phase (multi-phase iterate relay)"
```

---

### Task 3: The generic engine workflow `agentic-engine.yml`

**Files:**
- Create: `.github/workflows/agentic-engine.yml`
- Reference (do not edit): `.github/workflows/orchestrator.yml` (the source of truth for the 4-zone graph being generalized)

**Interfaces:**
- Consumes: `lib.py match-trigger`, `lib.py agent-workflow` (Task 1); the `phase`-carrying `protocol-continue` (Task 2); `next.py`/`advance.py`/`run-checks.py` `PHASE`/`BRANCH` env contract (M2a).
- Produces: a `workflow_call` workflow with input `protocol` (string, the protocol.json path), invoked by every trigger shim.

**Verification:** `actionlint` + structural greps in this task; full behavioral verification is the live run in Task 5.

- [ ] **Step 1: Write the workflow file**

Create `.github/workflows/agentic-engine.yml`. This is `orchestrator.yml` generalized along five axes: (1) `on: workflow_call` instead of static triggers; (2) `PROTO` = `inputs.protocol` everywhere the path was hardcoded; (3) the command is derived from the event (entry events via `lib.py match-trigger`; internal dispatch types `protocol-continue`/`protocol-advance` mapped generically) and threads `PHASE`; (4) the branch/leg list gains the `[""]` sentinel so a single-agent or agent phase runs as one matrix leg; (5) the aggregate check-run name is the protocol id, and the per-leg agent workflow is resolved via `lib.py agent-workflow`.

```yaml
name: Agentic Engine
# Reusable, protocol-agnostic engine. A per-protocol trigger shim declares the
# real `on:` triggers and calls this workflow with the protocol.json path.
# run-name + concurrency use the SAME instance expression so a repository_dispatch
# (client_payload.instance="pr-N") and a pull_request (number N) label the same
# logical instance. GHA has no top-level anchors — keep them in sync.
run-name: "${{ inputs.protocol }} · ${{ github.event.client_payload.instance || format('pr-{0}', github.event.issue.number || github.event.pull_request.number) }}"

on:
  workflow_call:
    inputs:
      protocol:
        description: "Path to the protocol.json (e.g. .github/agent-factory/protocols/multi-grumpy/protocol.json)"
        required: true
        type: string

permissions:
  contents: read

concurrency:
  # Scope by instance AND branch (empty for entry events / agent phases): a fan-out
  # iterates each branch via its own protocol-continue; an instance-only group would
  # let one queued branch-continue evict another, stranding a branch mid-review.
  group: engine-${{ inputs.protocol }}-${{ github.event.client_payload.instance || format('pr-{0}', github.event.issue.number || github.event.pull_request.number) }}-${{ github.event.client_payload.branch }}
  cancel-in-progress: false

jobs:
  # ── Zone 1: engine-pre. Holds the state PAT; never runs agent code. ──
  plan:
    if: >
      github.event_name == 'repository_dispatch' ||
      github.event_name == 'pull_request' ||
      (github.event_name == 'issue_comment' && github.event.issue.pull_request != null)
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write
      pull-requests: write
      checks: write
    outputs:
      pr: ${{ steps.ctx.outputs.pr }}
      head_sha: ${{ steps.head.outputs.sha }}
      action: ${{ steps.plan.outputs.action }}
      iteration: ${{ steps.plan.outputs.iteration }}
      feedback: ${{ steps.plan.outputs.feedback }}
      sabotage: ${{ steps.sabotage.outputs.sabotage }}
      instance: ${{ steps.ctx.outputs.instance }}
      command: ${{ steps.ctx.outputs.command }}
      phase: ${{ steps.plan.outputs.phase }}
      branches: ${{ steps.plan.outputs.branches }}
      branch: ${{ steps.ctx.outputs.branch }}
    steps:
      - uses: actions/checkout@v4
      - id: ctx
        env:
          PROTO: ${{ inputs.protocol }}
          DISPATCH_INSTANCE: ${{ github.event.client_payload.instance }}
          DISPATCH_BRANCH: ${{ github.event.client_payload.branch }}
          DISPATCH_PHASE: ${{ github.event.client_payload.phase }}
          DISPATCH_TYPE: ${{ github.event.action }}
          PR_EVENT_ACTION: ${{ github.event.action }}
          COMMENT_BODY: ${{ github.event.comment.body }}
        run: |
          # Derive (instance, command, branch, phase). Agent-derived strings
          # (payload fields, comment body) are read via env, never interpolated.
          # Entry events (pull_request/issue_comment) map through the protocol's
          # `triggers` block; internal repository_dispatch types are generic.
          BRANCH=""
          PHASE=""
          CMD=""
          case "${{ github.event_name }}" in
            repository_dispatch)
              INSTANCE="$DISPATCH_INSTANCE"
              [ -n "$INSTANCE" ] || { echo "[ctx] repository_dispatch with empty instance" >&2; exit 1; }
              PR="${INSTANCE#pr-}"
              case "$DISPATCH_TYPE" in
                protocol-continue) CMD="continue";      BRANCH="$DISPATCH_BRANCH"; PHASE="$DISPATCH_PHASE" ;;
                protocol-advance)  CMD="advance-phase";  BRANCH="";                 PHASE="$DISPATCH_PHASE" ;;
                *) echo "[ctx] unknown repository_dispatch type: $DISPATCH_TYPE" >&2; exit 1 ;;
              esac ;;
            pull_request)
              PR="${{ github.event.pull_request.number }}"
              INSTANCE="pr-$PR"
              CMD=$(python3 .github/agent-factory/engine/lib.py match-trigger "$PROTO" pull_request "$PR_EVENT_ACTION" "") ;;
            issue_comment)
              PR="${{ github.event.issue.number }}"
              INSTANCE="pr-$PR"
              CMD=$(python3 .github/agent-factory/engine/lib.py match-trigger "$PROTO" issue_comment "" "$COMMENT_BODY") ;;
          esac
          echo "pr=$PR" >> "$GITHUB_OUTPUT"
          echo "instance=$INSTANCE" >> "$GITHUB_OUTPUT"
          echo "command=$CMD" >> "$GITHUB_OUTPUT"
          echo "branch=$BRANCH" >> "$GITHUB_OUTPUT"
          echo "phase=$PHASE" >> "$GITHUB_OUTPUT"
      - id: head
        if: steps.ctx.outputs.command != ''
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR: ${{ steps.ctx.outputs.pr }}
        run: |
          SHA=$(gh pr view "$PR" --repo "${{ github.repository }}" --json headRefOid --jq .headRefOid)
          echo "sha=$SHA" >> "$GITHUB_OUTPUT"
      - id: plan
        if: steps.ctx.outputs.command != ''
        env:
          STATE_REMOTE: https://x-access-token:${{ secrets.POC_DISPATCH_TOKEN }}@github.com/${{ github.repository }}.git
          PUBLISH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          HEAD_SHA: ${{ steps.head.outputs.sha }}
          PROTO: ${{ inputs.protocol }}
          BRANCH: ${{ steps.ctx.outputs.branch }}
          PHASE: ${{ steps.ctx.outputs.phase }}
        run: |
          # next.py reads BRANCH/PHASE from env. Empty BRANCH+PHASE → the v1/v2
          # legacy path; PHASE set → multi-phase; advance-phase seeds the cursor's phase.
          .github/agent-factory/engine/next.py /tmp/state "${{ steps.ctx.outputs.instance }}" \
            "$PROTO" "${{ steps.ctx.outputs.command }}" "$HEAD_SHA" > /tmp/action.json
          cat /tmp/action.json
          ACTION=$(jq -r .action /tmp/action.json)
          echo "action=$ACTION" >> "$GITHUB_OUTPUT"
          echo "iteration=$(jq -r .iteration /tmp/action.json)" >> "$GITHUB_OUTPUT"
          echo "phase=$(jq -r '.phase // ""' /tmp/action.json)" >> "$GITHUB_OUTPUT"
          { echo "feedback<<GH_EOF"; jq -r .feedback /tmp/action.json; echo "GH_EOF"; } >> "$GITHUB_OUTPUT"
          # Legs (matrix axis):
          #   run-fanout            → all branch ids
          #   run-agent + branched  → [that branch]      (a fan-out branch's own continue)
          #   run-agent otherwise   → [""]               (single-agent OR agent phase: one sentinel leg)
          #   halt/other            → []                 (downstream jobs skip)
          if [ "$ACTION" = "run-fanout" ]; then
            BRANCHES=$(jq -c '[.branches[].id]' /tmp/action.json)
          elif [ "$ACTION" = "run-agent" ] && [ -n "${{ steps.ctx.outputs.branch }}" ]; then
            BRANCHES=$(jq -cn --arg b "${{ steps.ctx.outputs.branch }}" '[$b]')
          elif [ "$ACTION" = "run-agent" ]; then
            BRANCHES='[""]'
          else
            BRANCHES='[]'
          fi
          echo "branches=$BRANCHES" >> "$GITHUB_OUTPUT"
      - id: sabotage
        if: steps.ctx.outputs.command != ''
        env:
          GH_TOKEN: ${{ secrets.POC_DISPATCH_TOKEN }}
        run: |
          HAS=$(gh api "repos/${{ github.repository }}/issues/${{ steps.ctx.outputs.pr }}/labels" \
            --jq 'any(.[]; .name == "poc:sabotage")' 2>/dev/null || echo false)
          if [ "$HAS" = "true" ]; then
            echo "sabotage=true" >> "$GITHUB_OUTPUT"
          else
            echo "sabotage=false" >> "$GITHUB_OUTPUT"
          fi
      - name: Mark pipeline in progress (aggregate check run)
        if: steps.plan.outputs.branches != '[]' && steps.plan.outputs.branches != ''
        env:
          GITHUB_REPOSITORY: ${{ github.repository }}
          PUBLISH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          SHA: ${{ steps.head.outputs.sha }}
          ITER: ${{ steps.plan.outputs.iteration }}
        run: |
          # The aggregate check-run name is the PROTOCOL ID (generic). It stays
          # in_progress until join.py (fan-out) or advance.py (agent phase / no
          # next phase) completes it.
          PID=$(jq -r .name "${{ inputs.protocol }}")
          python3 .github/agent-factory/engine/lib.py set-check-run "$PID" "$SHA" in_progress "" "Pipeline in progress" "Running (iteration $ITER); merge is gated until the protocol completes."
      - name: Ensure shared status comment
        if: steps.plan.outputs.action == 'run-fanout'
        env:
          STATE_REMOTE: https://x-access-token:${{ secrets.POC_DISPATCH_TOKEN }}@github.com/${{ github.repository }}.git
          PUBLISH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          PR: ${{ steps.ctx.outputs.pr }}
          INSTANCE: ${{ steps.ctx.outputs.instance }}
        run: |
          PID=$(jq -r .name "${{ inputs.protocol }}")
          python3 .github/agent-factory/engine/lib.py ensure-status-comment \
            /tmp/state "$PID" "$INSTANCE" "${{ inputs.protocol }}" "$PR"

  # ── Zone 2: the sandboxed agents, one matrix leg per branch/sentinel. ──
  dispatch:
    needs: plan
    if: ${{ needs.plan.outputs.branches != '[]' && needs.plan.outputs.branches != '' }}
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        branch: ${{ fromJSON(needs.plan.outputs.branches) }}
    steps:
      - uses: actions/checkout@v4
      - id: wait
        env:
          GH_TOKEN: ${{ secrets.POC_DISPATCH_TOKEN }}
          PR: ${{ needs.plan.outputs.pr }}
          ITERATION: ${{ needs.plan.outputs.iteration }}
          FEEDBACK: ${{ needs.plan.outputs.feedback }}
          SABOTAGE: ${{ needs.plan.outputs.sabotage }}
          REPO: ${{ github.repository }}
          PROTO: ${{ inputs.protocol }}
          PHASE: ${{ needs.plan.outputs.phase }}
          LEG: ${{ matrix.branch }}
        run: |
          # Correlation id: unique per dispatch (run id + attempt + leg). The leg
          # is the branch id, or "agent" for the single-agent/agent-phase sentinel
          # (an empty string would make a "…-" suffix; use a stable token instead).
          LEGTOK="${LEG:-agent}"
          CID="${{ github.run_id }}-${{ github.run_attempt }}-${LEGTOK}"
          CTX=$(jq -nc \
            --arg pr "$PR" --arg iteration "$ITERATION" --arg feedback "$FEEDBACK" \
            --argjson sabotage "$SABOTAGE" --arg cid "$CID" \
            '{pr: $pr, iteration: $iteration, feedback: $feedback, sabotage: $sabotage, cid: $cid}')
          # Resolve the agent workflow for this leg from the protocol (NOT from the
          # branch name — an agent phase's workflow lives on the phase state).
          NAME=$(python3 .github/agent-factory/engine/lib.py agent-workflow "$PROTO" "$PHASE" "$LEG")
          [ -n "$NAME" ] || { echo "no agent workflow resolved for phase='$PHASE' leg='$LEG'" >&2; exit 1; }
          WF="$NAME.lock.yml"
          mkdir -p /tmp/meta
          T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
          gh workflow run "$WF" --repo "$REPO" -f aw_context="$CTX"
          RID=""
          for i in $(seq 1 24); do
            sleep 5
            RUNS=$(gh run list --repo "$REPO" --workflow "$WF" --event workflow_dispatch \
              --created ">=$T0" --json databaseId,displayTitle)
            RID=$(python3 .github/agent-factory/engine/lib.py match-run-by-cid "$RUNS" "$CID")
            [ -n "$RID" ] && break
          done
          [ -n "$RID" ] || { echo "no agent run matched cid:[$CID] for $WF" >&2; exit 1; }
          printf '%s' "$RID" > /tmp/meta/run_id
          if gh run watch "$RID" --repo "$REPO" --exit-status; then
            printf '%s' "true" > /tmp/meta/agent_ok
          else
            printf '%s' "false" > /tmp/meta/agent_ok
          fi
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: runmeta-${{ matrix.branch || 'agent' }}
          path: /tmp/meta

  # ── Zone 3: checks. No secrets beyond the default read token. ──
  checks:
    needs: [plan, dispatch]
    if: always() && needs.plan.outputs.branches != '[]' && needs.plan.outputs.branches != '' && needs.dispatch.result != 'skipped'
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        branch: ${{ fromJSON(needs.plan.outputs.branches) }}
    permissions:
      contents: read
      actions: read
      pull-requests: read
    env:
      GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    steps:
      - uses: actions/checkout@v4
      - name: Download run meta
        uses: actions/download-artifact@v4
        with:
          name: runmeta-${{ matrix.branch || 'agent' }}
          path: /tmp/meta
      - id: meta
        run: |
          RID=$(cat /tmp/meta/run_id 2>/dev/null || echo "")
          OK=$(cat /tmp/meta/agent_ok 2>/dev/null || echo "")
          echo "rid=$RID" >> "$GITHUB_OUTPUT"
          echo "ok=$OK" >> "$GITHUB_OUTPUT"
      - name: Download evidence artifact
        run: |
          mkdir -p /tmp/agent
          if [ -n "${{ steps.meta.outputs.rid }}" ]; then
            gh run download "${{ steps.meta.outputs.rid }}" \
              --repo "${{ github.repository }}" -n evidence -D /tmp/agent || echo "no evidence artifact"
          fi
      - name: Fetch independent ground truth
        run: |
          gh pr diff "${{ needs.plan.outputs.pr }}" --repo "${{ github.repository }}" > /tmp/diff.txt
          gh pr diff "${{ needs.plan.outputs.pr }}" --repo "${{ github.repository }}" --name-only > /tmp/files.txt
      - name: Run checks
        env:
          RID: ${{ steps.meta.outputs.rid }}
          OK: ${{ steps.meta.outputs.ok }}
          BRANCH: ${{ matrix.branch }}
          PHASE: ${{ needs.plan.outputs.phase }}
          PROTO: ${{ inputs.protocol }}
        run: |
          # run-checks.py resolves the check LIST + node-scoped params from the
          # protocol given BRANCH/PHASE. The state node id is the phase id when
          # PHASE is set, else the legacy "review".
          EV=/tmp/agent/evidence.json
          if [ ! -f "$EV" ]; then
            echo "WARNING: no evidence artifact (agent run $RID, ok=$OK); checks will report it" >&2
            echo '{}' > "$EV"
          fi
          NODE="${PHASE:-review}"
          VERDICTS=$(.github/agent-factory/engine/run-checks.py \
            "$PROTO" "$NODE" "$EV" /tmp/diff.txt /tmp/files.txt)
          jq . <<<"$VERDICTS"
          mkdir -p /tmp/verdicts
          printf '%s' "$VERDICTS" > /tmp/verdicts/verdicts.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: verdicts-${{ matrix.branch || 'agent' }}
          path: /tmp/verdicts

  # ── Zone 4: engine-post. Reads check verdicts only; sole writer of state. ──
  advance:
    needs: [plan, dispatch, checks]
    if: always() && needs.plan.outputs.branches != '[]' && needs.plan.outputs.branches != '' && needs.dispatch.result != 'skipped' && (needs.checks.result == 'success' || needs.checks.result == 'failure')
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        branch: ${{ fromJSON(needs.plan.outputs.branches) }}
    permissions:
      contents: read
      pull-requests: write
      issues: write
      actions: read
      checks: write
    steps:
      - uses: actions/checkout@v4
      - name: Download run meta
        uses: actions/download-artifact@v4
        with:
          name: runmeta-${{ matrix.branch || 'agent' }}
          path: /tmp/meta
      - name: Download verdicts
        uses: actions/download-artifact@v4
        with:
          name: verdicts-${{ matrix.branch || 'agent' }}
          path: /tmp/verdicts
      - name: Re-download evidence (for publication only)
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          mkdir -p /tmp/agent
          RID=$(cat /tmp/meta/run_id 2>/dev/null || echo "")
          if [ -n "$RID" ]; then
            gh run download "$RID" --repo "${{ github.repository }}" -n evidence -D /tmp/agent || true
          fi
          [ -f /tmp/agent/evidence.json ] || echo '{"files":[]}' > /tmp/agent/evidence.json
      - name: Advance
        env:
          STATE_REMOTE: https://x-access-token:${{ secrets.POC_DISPATCH_TOKEN }}@github.com/${{ github.repository }}.git
          GH_TOKEN: ${{ secrets.POC_DISPATCH_TOKEN }}
          PUBLISH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          PR: ${{ needs.plan.outputs.pr }}
          PR_HEAD_SHA: ${{ needs.plan.outputs.head_sha }}
          BRANCH: ${{ matrix.branch }}
          PHASE: ${{ needs.plan.outputs.phase }}
          PROTO: ${{ inputs.protocol }}
        run: |
          export AGENT_RUN_ID=$(cat /tmp/meta/run_id 2>/dev/null || echo "")
          VERDICTS=$(cat /tmp/verdicts/verdicts.json 2>/dev/null || echo "")
          if [ -z "$VERDICTS" ]; then
            VERDICTS='{"results":[{"check":"checks-job","pass":false,"feedback":"checks job did not complete; treating as failed iteration"}]}'
          fi
          printf '%s' "$VERDICTS" > /tmp/verdicts.json
          jq . /tmp/verdicts.json
          .github/agent-factory/engine/advance.py /tmp/state "${{ needs.plan.outputs.instance }}" \
            "$PROTO" /tmp/verdicts.json /tmp/agent/evidence.json
```

> Key generalizations vs. `orchestrator.yml`, called out for the reviewer:
> - `BRANCH`/`PHASE` are passed via `env:` (not inline) in plan/checks/advance — preserves the injection rule.
> - `matrix.branch` is the empty string `""` for the single-agent / agent-phase sentinel leg. Artifact names use `${{ matrix.branch || 'agent' }}` because an artifact name cannot be empty. The CID uses `${LEG:-agent}` for the same reason. `BRANCH=${{ matrix.branch }}` (empty) still routes the engine scripts down the legacy/agent-phase path — the engine treats empty `BRANCH` as "no branch".
> - The aggregate check-run + status-comment use the protocol id (`jq -r .name`), not the literal `multi-grumpy`.
> - `run-checks.py`'s node argument is `${PHASE:-review}`: the phase id for multi-phase, else the legacy `review` node (multi-grumpy/grumpy both name their agent/fanout state `review`).

- [ ] **Step 2: Lint the workflow**

Run: `actionlint .github/workflows/agentic-engine.yml`
Expected: no errors. (If `actionlint` is not installed: `go install github.com/rhysd/actionlint/cmd/actionlint@latest` or `brew install actionlint`. As a fallback, validate YAML parses: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/agentic-engine.yml'))"`.)

- [ ] **Step 3: Structural assertions**

Run and eyeball each:
```bash
# It is a reusable workflow with the protocol input.
grep -q "workflow_call" .github/workflows/agentic-engine.yml && echo OK-callable
grep -q "protocol:" .github/workflows/agentic-engine.yml && echo OK-input
# Both internal dispatch types are routed.
grep -q "protocol-continue" .github/workflows/agentic-engine.yml && echo OK-continue
grep -q "protocol-advance" .github/workflows/agentic-engine.yml && echo OK-advance
# The sentinel leg exists.
grep -q "'\[\"\"\]'" .github/workflows/agentic-engine.yml || grep -q '\[""\]' .github/workflows/agentic-engine.yml && echo OK-sentinel
# No hardcoded protocol path remains.
! grep -q "protocols/multi-grumpy/protocol.json" .github/workflows/agentic-engine.yml && echo OK-no-hardcoded-path
# The trust-zone tokens are unchanged: plan/advance use the PAT, checks does not.
grep -c "POC_DISPATCH_TOKEN" .github/workflows/agentic-engine.yml   # expect plan(2: STATE_REMOTE+sabotage) + dispatch(1) + advance(2) usages
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/agentic-engine.yml
git commit -m "feat(orchestrator): generic engine-owned reusable workflow_call workflow"
```

---

### Task 4: The trigger shim + rename `orchestrator.yml`

**Files:**
- Create (via rename): `.github/workflows/multi-grumpy-trigger.yml`
- Delete: `.github/workflows/orchestrator.yml`
- Modify: `.github/agent-factory/protocols/multi-grumpy/protocol.json` (add `triggers`)
- Modify: `.github/agent-factory/protocols/grumpy/protocol.json` (add `triggers`)

**Interfaces:**
- Consumes: `agentic-engine.yml` (Task 3) via `uses:`; `lib.match_trigger` reads the `triggers` block (Task 1).
- Produces: the deployed multi-grumpy trigger; the protocol `triggers` blocks that drive command resolution.

- [ ] **Step 1: Add the `triggers` block to multi-grumpy**

Edit `.github/agent-factory/protocols/multi-grumpy/protocol.json` — insert a `triggers` array after `"version"` (before `"states"`), reproducing today's hardcoded mapping (`/grumpy` comment → start; PR opened/reopened → start; synchronize → reset):

```json
  "triggers": [
    { "on": "issue_comment", "comment_prefix": "/grumpy", "command": "start" },
    { "on": "pull_request",  "actions": ["opened", "reopened"], "command": "start" },
    { "on": "pull_request",  "actions": ["synchronize"], "command": "reset" }
  ],
```

- [ ] **Step 2: Add a `triggers` block to grumpy (single-agent)**

Edit `.github/agent-factory/protocols/grumpy/protocol.json` — insert after `"version"`:

```json
  "triggers": [
    { "on": "issue_comment", "comment_prefix": "/grumpy", "command": "start" },
    { "on": "pull_request",  "actions": ["opened", "reopened"], "command": "start" },
    { "on": "pull_request",  "actions": ["synchronize"], "command": "reset" }
  ],
```

- [ ] **Step 3: Verify both protocols still parse + triggers resolve**

```bash
python3 -c "import json; json.load(open('.github/agent-factory/protocols/multi-grumpy/protocol.json'))" && echo OK-multi
python3 -c "import json; json.load(open('.github/agent-factory/protocols/grumpy/protocol.json'))" && echo OK-grumpy
python3 .github/agent-factory/engine/lib.py match-trigger .github/agent-factory/protocols/multi-grumpy/protocol.json pull_request synchronize ""   # → reset
python3 .github/agent-factory/engine/lib.py match-trigger .github/agent-factory/protocols/multi-grumpy/protocol.json issue_comment "" "/grumpy go" # → start
python3 .github/agent-factory/engine/lib.py match-trigger .github/agent-factory/protocols/multi-grumpy/protocol.json issue_comment "" "lgtm"       # → (empty)
```
Expected: `reset`, `start`, empty line.

- [ ] **Step 4: Create the shim by renaming the orchestrator**

```bash
git mv .github/workflows/orchestrator.yml .github/workflows/multi-grumpy-trigger.yml
```

Then replace its entire contents with the thin shim:

```yaml
name: multi-grumpy
# Thin per-protocol trigger shim. Declares the real `on:` triggers (which a
# reusable workflow_call workflow cannot declare itself) and hands off to the
# generic engine. The engine derives the command from the event via this
# protocol's `triggers` block; the only protocol-specific value here is the path.
on:
  pull_request:
    types: [opened, synchronize, reopened]
  issue_comment:
    types: [created]
  repository_dispatch:
    types: [protocol-continue, protocol-advance]   # protocol-join is owned by protocol-join.yml

permissions:
  contents: read

jobs:
  engine:
    uses: ./.github/workflows/agentic-engine.yml
    with:
      protocol: .github/agent-factory/protocols/multi-grumpy/protocol.json
    secrets: inherit
```

> Why `secrets: inherit`: the engine's jobs each reference the specific secret their trust zone needs (`POC_DISPATCH_TOKEN` in plan/advance, `GITHUB_TOKEN` in checks). `inherit` makes the repo secrets available without re-listing them; the per-job references preserve zone separation. Why no `protocol-join` here: `protocol-join.yml` already listens for that dispatch type for all protocols and is fully generic — duplicating it in the shim would double-fire the join.

- [ ] **Step 5: Lint the shim**

Run: `actionlint .github/workflows/multi-grumpy-trigger.yml`
Expected: no errors. (`actionlint` validates `uses:` of a local reusable workflow and the `with:`/`secrets:` keys.) Fallback: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/multi-grumpy-trigger.yml'))"`.

- [ ] **Step 6: Confirm the old name is gone and join is untouched**

```bash
! test -f .github/workflows/orchestrator.yml && echo OK-renamed
test -f .github/workflows/protocol-join.yml && echo OK-join-present
git diff --name-only HEAD -- .github/workflows/protocol-join.yml | grep -q . && echo "CHANGED-join (unexpected)" || echo OK-join-unchanged
```

- [ ] **Step 7: Run the full suite (protocol JSON changed)**

Run: `pytest tests/ -q`
Expected: PASS (211). The `triggers` block is additive data; any test that loads these protocols ignores the new key. If a test asserts the exact protocol JSON, update its expected fixture to include `triggers`.

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/multi-grumpy-trigger.yml \
        .github/agent-factory/protocols/multi-grumpy/protocol.json \
        .github/agent-factory/protocols/grumpy/protocol.json
git add -u .github/workflows/orchestrator.yml
git commit -m "feat(orchestrator): multi-grumpy trigger shim + protocol triggers block (replaces orchestrator.yml)"
```

---

### Task 5: Live equivalence checkpoint (post-merge, manual)

**This task is a gated manual checkpoint, not code.** A reusable workflow + shim only run once they are on `main` (the default branch is where `issue_comment`/`repository_dispatch`/`pull_request` workflows resolve from). So the live verification happens after the branch merges to `main`.

**Files:** none (verification + a runbook note).

- [ ] **Step 1: Merge the M2b branch to `main`**

```bash
git checkout main && git merge --ff-only <m2b-branch>
git push origin main
```
(Per CLAUDE.md, `agentic-engine.yml`, `multi-grumpy-trigger.yml`, and the agent locks must be on `main`.)

- [ ] **Step 2: Trigger a fan-out review on a fresh PR**

Open a PR with a small code change, or comment `/grumpy` on an existing PR. Watch:
```bash
gh run list --workflow multi-grumpy-trigger.yml --limit 5
gh run watch <run-id>
```

- [ ] **Step 3: Assert observable equivalence with the old orchestrator**

The pre-M2b behavior must reproduce exactly:
- [ ] The `multi-grumpy` aggregate check-run goes `in_progress` → `success`/`failure` (now created under the protocol id, which is `multi-grumpy` — same name).
- [ ] Two agent reviews run (grumpy + security), each as its own matrix leg.
- [ ] Per-branch sub check-runs `multi-grumpy/grumpy`, `multi-grumpy/security` appear.
- [ ] The shared status comment is created once and PATCHed (not duplicated).
- [ ] The join fires (`protocol-join.yml` run) and completes the aggregate check-run once both branches are terminal.
- [ ] A failing-then-recovering branch (apply the `poc:sabotage` label) still iterates via `protocol-continue` (now carrying an empty `phase`, byte-identical payload) and self-recovers.

- [ ] **Step 4: Record the result**

If all assertions hold, M2b is observably-equivalent and the generic engine is live. Note the run URLs in the PR / commit message. If anything diverges, capture the failing run's logs and fix forward (do not revert the rename — fix the engine workflow).

> The single-agent (`grumpy-review`) topology is exercised by `pytest` + structural checks only in M2b — there is no deployed `grumpy-review` shim. The multi-phase (`code-review-pipeline`) live path is M3's checkpoint.

---

### Task 6 (optional polish): Extract `lib.resolve_agent_unit`

**De-risking note:** this is the deferred M2a TODO (`advance.py:207-209`). It is pure internal cleanup — the orchestrator does not call it. Do it ONLY after Tasks 1–5 are green and merged, and keep it strictly behavior-preserving. If review or time pressure makes it risky, defer it to the backlog; the orchestrator does not depend on it.

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add `resolve_agent_unit`)
- Modify: `.github/agent-factory/engine/next.py:176-244`, `.github/agent-factory/engine/advance.py:207-264` (call the shared helper)
- Test: `tests/test_resolve_agent_unit.py` (create)

**Interfaces:**
- Produces: `lib.resolve_agent_unit(protocol, phase="", branch="") -> dict` returning `{"agent_state": str, "max_iterations": int|None, "life_state": str}` — the agent unit id, its iteration budget, and the value a live state file's `.state` carries while in flight. Encapsulates the PHASE-first → BRANCH → single-agent ladder duplicated in `next.py` and `advance.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_resolve_agent_unit.py` asserting the three resolution modes against the fixtures from Task 1 (`SINGLE`, `MULTI`, `PIPELINE`), e.g.:

```python
import sys
from pathlib import Path
ENGINE = Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

from test_triggers import SINGLE, MULTI, PIPELINE  # reuse the protocol fixtures


def test_single_agent_unit():
    u = lib.resolve_agent_unit(SINGLE)
    assert u["agent_state"] == "review" and u["life_state"] == "review"


def test_fanout_branch_unit():
    u = lib.resolve_agent_unit(MULTI, branch="security")
    # life_state is the owning fan-out state's id, not the branch id.
    assert u["agent_state"] == "security" and u["life_state"] == "review"


def test_agent_phase_unit():
    u = lib.resolve_agent_unit(PIPELINE, phase="gate")
    assert u["agent_state"] == "gate" and u["life_state"] == "gate"


def test_fanout_phase_unit():
    u = lib.resolve_agent_unit(PIPELINE, phase="review", branch="grumpy")
    assert u["agent_state"] == "grumpy" and u["life_state"] == "review"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_resolve_agent_unit.py -q`
Expected: FAIL — `AttributeError: module 'lib' has no attribute 'resolve_agent_unit'`.

- [ ] **Step 3: Implement `resolve_agent_unit` in `lib.py`**

Port the ladder from `next.py:176-244` exactly (PHASE-first: fanout phase → branch within it, else the phase itself; elif BRANCH → fan-out branch with life_state = owning fan-out id; else first agent state). Return the dict. Raise `ValueError` with the same messages the scripts emit today on unresolved branch/phase so callers can convert to the existing `exit(1)`.

```python
def resolve_agent_unit(protocol, phase="", branch=""):
    """Resolve the agent unit for a leg: its agent_state id, max_iterations, and
    life_state (the .state value a live state file carries in flight). Mirrors the
    PHASE-first → BRANCH → single-agent ladder. Raises ValueError if unresolved."""
    if phase:
        st = state_by_id(protocol, phase)
        if not st:
            raise ValueError(f"no phase '{phase}' in protocol")
        if st.get("kind") == "fanout":
            if not branch:
                raise ValueError(f"PHASE='{phase}' is a fanout phase but BRANCH is empty")
            for b in st.get("branches", []):
                if b["id"] == branch:
                    return {"agent_state": branch, "max_iterations": b.get("max_iterations"), "life_state": phase}
            raise ValueError(f"no branch '{branch}' in phase '{phase}'")
        return {"agent_state": phase, "max_iterations": st.get("max_iterations"), "life_state": phase}
    if branch:
        fanout_id = None
        max_it = None
        for st in protocol.get("states", []):
            if st.get("kind") == "fanout":
                fanout_id = st["id"]
                for b in st.get("branches", []):
                    if b["id"] == branch:
                        max_it = b.get("max_iterations")
                break
        if fanout_id is None:
            raise ValueError(f"no branch '{branch}' in protocol")
        return {"agent_state": branch, "max_iterations": max_it, "life_state": fanout_id}
    for st in protocol.get("states", []):
        if st.get("kind") == "agent":
            return {"agent_state": st["id"], "max_iterations": st.get("max_iterations"), "life_state": st["id"]}
    raise ValueError("protocol has no agent state")
```

- [ ] **Step 4: Refactor `next.py` and `advance.py` to call it**

Replace the inlined ladders (next.py:176-244, advance.py:207-264) with a `try: unit = lib.resolve_agent_unit(proto, PHASE, BRANCH) except ValueError as e: sys.stderr.write(...); sys.exit(1)` and read `AGENT_STATE`/`MAX`/`LIFE_STATE` (next.py) and `agent_state`/`max_iter`/`life_state` (advance.py) from `unit`. Preserve the exact stderr messages and exit codes the scripts emit today.

- [ ] **Step 5: Run the targeted test + the FULL regression suite**

Run: `pytest tests/test_resolve_agent_unit.py -q && pytest tests/ -q`
Expected: PASS — 211 + 4 = 215. The full suite is the byte-identical proof: `test_engine.py`, `test_multiphase.py`, `test_fanout_e2e.py` must all stay green. If any regression test moves, the refactor changed behavior — revert and re-port more carefully.

- [ ] **Step 6: Remove the deferred-TODO comment**

Delete the `# NOTE: this PHASE/branch agent-unit resolution mirrors next.py's ... extracted into a shared lib.resolve_agent_unit() in M2b` comment at `advance.py:207-209`.

- [ ] **Step 7: Commit**

```bash
git add .github/agent-factory/engine/lib.py .github/agent-factory/engine/next.py \
        .github/agent-factory/engine/advance.py tests/test_resolve_agent_unit.py
git commit -m "refactor(engine): extract lib.resolve_agent_unit (dedupe next.py/advance.py)"
```

---

## Self-Review

**1. Spec coverage** (against the M2 section of `2026-06-16-code-review-pipeline-design.md`):
- "engine-owned `agentic-engine.yml` (`on: workflow_call`; inputs: protocol path…)" → Task 3. ✓
- "matrix over `branches` (one sentinel entry mapping to `BRANCH=""` for an agent phase; N entries for a fan-out phase)" → Task 3 leg logic (`[""]` sentinel). ✓
- "a `join` job gated by …" → the spec predates M2a; the actual model fires `protocol-join` as a separate dispatch handled by the already-generic `protocol-join.yml`. Documented in File Structure + Task 4 Step 6 (left unchanged). ✓ (deliberate deviation, recorded)
- "job-level concurrency keyed `protocol·instance·branch`" → implemented as workflow-level concurrency keyed `protocol·instance·branch` in the engine (correct because each branch's `protocol-continue` is a *separate* run; matrix legs share one run). Recorded as a deviation in Task 3. ✓
- "thin, generatable trigger shim … `orchestrator.yml` renamed `multi-grumpy-trigger.yml`" → Task 4. ✓
- "`triggers` block" → Task 1 (`match_trigger`) + Task 4 (data). ✓
- "Decision A — `advance` fires `protocol-advance`; orchestrator routes it into `plan` at the new phase" → already fired by advance.py (M2a); routed in Task 3 ctx step (`protocol-advance` → `advance-phase` + `PHASE`). ✓
- The `protocol-continue`-carries-`phase` gap (needed for a multi-phase agent-phase iterate) → Task 2 (not explicit in the spec but required for the phase relay to be correct end-to-end; recorded as a found gap). ✓
- "extract `lib.resolve_agent_unit`" (M2a deferred TODO) → Task 6. ✓
- Regression proof = multi-grumpy live equivalence → Task 5. ✓

**2. Placeholder scan:** No `TBD`/`handle edge cases`/"similar to Task N". The one explicit implementer judgment (Task 2's fixture iteration-budget check) is spelled out with the exact command to confirm it. YAML is given in full.

**3. Type/name consistency:** `match_trigger(protocol, event_name, action, comment_body)`, `agent_workflow(protocol, phase, branch)`, `resolve_agent_unit(protocol, phase, branch) -> {agent_state, max_iterations, life_state}`, CLI `match-trigger`/`agent-workflow`, dispatch types `protocol-continue`/`protocol-advance`/`protocol-join`, leg sentinel `[""]` with artifact suffix `'agent'` — all used consistently across Tasks 1, 3, 4, 6. The engine command names (`start`/`reset`/`continue`/`advance-phase`) match `next.py`. The aggregate check-run name = protocol id, matching M2a's `advance.py`/`join.py`.

**Verified before handoff:** the `pipeline-mini` fixture (`name: pipeline-mini`, first phase `gate`, `max_iterations: 2`) and the `lib.py` CLI shape (`_cli(argv)` with a `cmd ==` ladder) — Tasks 1, 2, 4 reference these accurately.
