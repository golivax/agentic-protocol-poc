# HITL Override Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a write-access human comment `/override` to force a *blocked* halt-gate (e.g. preflight) to advance exactly one phase, while the gate's verdict stays `failed` in the audit trail.

**Architecture:** One new durable state primitive — a `halted` marker stamped on `_instance.yaml` by the block-halt path in `advance.py` (the only signal that distinguishes a *blocked* gate from an *exhausted* one, since both write `state: failed`). A new generic `override` command in `next.py` reads that marker, records the override, clears the marker, and reuses the existing `seed_and_dispatch_phase` to advance one phase. Authorization (the GitHub permissions API on the trusted commenter login) lives entirely in the workflow `ctx` step, so `next.py` only ever sees an already-authorized override. The protocol opts in by declaring an `/override` trigger.

**Tech Stack:** Python 3 + PyYAML (engine), pytest (tests), GitHub Actions YAML (`agentic-engine.yml`), `gh` CLI.

## Global Constraints

- **Agent/user-derived strings via `env:`, never interpolated into `run:` blocks** (shell-injection rule from CLAUDE.md). The commenter identity and reason are read from the trusted event context / env, never parsed for privilege from the comment body.
- **Authorization reads only GitHub-stamped trusted fields** — `github.event.comment.user.login` + the permissions API — never the comment body.
- **State advances only by fast-forward CAS push; never force-push `agentic-state`.** Reuse `lib.cas_push` (one rebase retry). Do not add new push logic.
- **The blocked gate's verdict is never rewritten.** The blocked phase keeps `state: failed` and its `failure` check-run; the override is recorded *beside* it.
- **`advance.py` and `next.py` remain the only state writers.** The auth check is read-only.
- **Override is blocked-only.** An *exhausted* gate (no `halted` marker) is not overridable and must return a clear, distinct error message.
- **Override scope is one gate at a time** — a single `/override` advances exactly one phase.
- **The vendored engine needs only Python 3 + PyYAML at runtime.** No new dependencies. pytest is dev-only.
- **Engine stays protocol-agnostic.** The only change inside a protocol directory is the `/override` trigger declaration (data).

## Context an implementer needs

- **Protocol under test:** `.github/agent-factory/protocols/code-review-pipeline/protocol.json`, `name = "code-review-pipeline"`. Phases: `preflight` (kind `agent`, `next: review`, `on_blocked: halt`, `max_iterations: 2`, `conclude: conclude-preflight`) → `review` (kind `fanout`, `next: join`) → `join` (kind `join`, `next: done`). So `next_phase_id(proto, "preflight") == "review"`.
- **State file paths** (`lib.state_file`): agent phase → `<dir>/<pid>/<instance>/<phase>.yaml`; fan-out leg → `<dir>/<pid>/<instance>/<phase>.<branch>.yaml`; instance cursor → `<dir>/<pid>/<instance>/_instance.yaml` (`lib.instance_file`).
- **The block-halt branch** is `advance.py` lines ~324-332 (inside `if process == "done":`). Variables in scope there: `dir_`, `pid`, `instance`, `phase`, `sha` (from `PR_HEAD_SHA`), `inf` (`= lib.instance_file(dir_, pid, instance)`, defined ~line 300), `this_state`, `cr_name`, `csum`.
- **The exhaustion branch** is `advance.py` lines ~398-415 (`else:  # process == "failed"`). It must remain unmarked.
- **`next.py` routing cascade** starts ~line 146. Module-level `def`s above it: `seed_and_dispatch_phase(phase_id, command)` (~line 93). `emit_halt` is defined *below* the cascade (~line 219), so the override handler must NOT call `emit_halt` — it prints the halt JSON inline.
- **`lib` helpers available:** `load_yaml`, `dump_yaml`, `instance_file`, `state_file`, `next_phase_id`, `state_by_id`, `cas_push`, `upsert_status_comment`. `match_trigger` already maps `issue_comment` + `comment_prefix` → command via protocol data.
- **Tests:** pytest under `tests/`. `tests/conftest.py` gives `state_origin` (bare `agentic-state` origin), `engine_env`, `run_engine`, `read_state_yaml`. ENGINE_LOCAL=1 short-circuits all GitHub I/O (`gh_api`, `set_check_run`, `upsert_status_comment`, and the new `post_pr_comment`) to stderr. Both `next.py` and `advance.py` call `lib.state_checkout(dir_)` on startup, which **clones** the origin into a fresh work dir — so every invocation uses its own freshly-`mktemp`'d work dir, and you `clone_state` into a separate dir to assert final state.

---

### Task 1: The `halted` marker (advance.py block-halt path)

**Files:**
- Modify: `.github/agent-factory/engine/advance.py` (block-halt branch, ~lines 324-332)
- Test: `tests/test_override.py` (new)

**Interfaces:**
- Consumes: `lib.instance_file`, `lib.load_yaml`, `lib.dump_yaml` (existing).
- Produces: on a block-halt, `_instance.yaml` gains `halted: {phase: <id>, reason: "blocked", sha: <head sha>}`. On exhaustion, no `halted` key is written. This marker is the contract Task 3 reads.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_override.py`:

```python
"""HITL override gate — engine-side behavior (the `halted` marker, the override
command, and its guards). All GitHub I/O is ENGINE_LOCAL stderr no-ops."""
import json
import os
import subprocess

import pytest
import yaml

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
NEXT_PY = ENGINE / "next.py"
ADVANCE_PY = ENGINE / "advance.py"
LIB_PY = ENGINE / "lib.py"
PIPELINE_PROTO = ROOT / ".github/agent-factory/protocols/code-review-pipeline/protocol.json"
PID = json.load(open(PIPELINE_PROTO))["name"]
REVIEW_BRANCHES = [
    b["id"]
    for s in json.load(open(PIPELINE_PROTO))["states"]
    if s["id"] == "review"
    for b in s["branches"]
]


def _env(state_origin, **extra):
    e = dict(os.environ)
    e["ENGINE_LOCAL"] = "1"
    e["STATE_REMOTE"] = str(state_origin)
    e["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    e.update(extra)
    return e


def _run(script, args, env):
    r = subprocess.run(
        ["python3", str(script), *map(str, args)],
        text=True, capture_output=True, env=env,
    )
    return r.stdout, r.stderr, r.returncode


def _clone(state_origin, target):
    subprocess.run(
        ["git", "clone", "-q", "--branch", "agentic-state", str(state_origin), str(target)],
        check=True,
    )


def seed_preflight(state_origin, work, instance, *, state, iteration, head_sha):
    """Seed a preflight phase state + _instance cursor (no halted marker) and push."""
    _run(LIB_PY, ["state-checkout", str(work)], _env(state_origin))
    base = work / PID / instance
    base.mkdir(parents=True, exist_ok=True)
    (base / "preflight.yaml").write_text(yaml.safe_dump({
        "protocol": PID, "instance": instance, "state": state,
        "iteration": iteration, "gates": {}, "head_sha": head_sha, "history": [],
    }))
    (base / "_instance.yaml").write_text(yaml.safe_dump({
        "protocol": PID, "instance": instance, "phase": "preflight",
        "head_sha": head_sha, "joined": False,
    }))
    _run(LIB_PY, ["cas-push", str(work), f"seed {instance}"], _env(state_origin))


def write_json(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return p


# Verdicts that drive advance.py to each terminal:
#   block: a block-severity check fails, no iterate fail → process=done, blocking=True
VERDICTS_BLOCK = {"results": [
    {"check": "spec-present", "pass": False, "on_fail": "block", "feedback": "missing spec"},
    {"check": "preflight-schema-valid", "pass": True, "on_fail": "iterate", "feedback": ""},
]}
#   exhaust: an iterate-severity check fails with no iterations remaining → process=failed
VERDICTS_ITER_FAIL = {"results": [
    {"check": "preflight-schema-valid", "pass": False, "on_fail": "iterate", "feedback": "bad evidence"},
]}
EVIDENCE_MIN = {"checks": [], "examined": []}


def test_block_halt_stamps_halted_marker(state_origin, tmp_path):
    inst = "pr-1"
    seed_preflight(state_origin, tmp_path / "seed", inst, state="preflight", iteration=1, head_sha="sha-block")
    v = write_json(tmp_path, "verdicts.json", VERDICTS_BLOCK)
    ev = write_json(tmp_path, "evidence.json", EVIDENCE_MIN)
    env = _env(state_origin, PHASE="preflight", PR="1", PR_HEAD_SHA="sha-block")
    _run(ADVANCE_PY, [tmp_path / "adv", inst, PIPELINE_PROTO, v, ev], env)

    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load((tmp_path / "verify" / PID / inst / "_instance.yaml").read_text())
    assert inf["halted"] == {"phase": "preflight", "reason": "blocked", "sha": "sha-block"}
    pf = yaml.safe_load((tmp_path / "verify" / PID / inst / "preflight.yaml").read_text())
    assert pf["state"] == "failed"


def test_exhaustion_writes_no_halted_marker(state_origin, tmp_path):
    inst = "pr-2"
    # iteration == max_iterations (2) → no iterations remaining → process=failed
    seed_preflight(state_origin, tmp_path / "seed", inst, state="preflight", iteration=2, head_sha="sha-exh")
    v = write_json(tmp_path, "verdicts.json", VERDICTS_ITER_FAIL)
    ev = write_json(tmp_path, "evidence.json", EVIDENCE_MIN)
    env = _env(state_origin, PHASE="preflight", PR="2", PR_HEAD_SHA="sha-exh")
    _run(ADVANCE_PY, [tmp_path / "adv", inst, PIPELINE_PROTO, v, ev], env)

    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load((tmp_path / "verify" / PID / inst / "_instance.yaml").read_text())
    assert "halted" not in inf
    pf = yaml.safe_load((tmp_path / "verify" / PID / inst / "preflight.yaml").read_text())
    assert pf["state"] == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_override.py -k "halted or exhaustion" -v`
Expected: `test_block_halt_stamps_halted_marker` FAILS (KeyError `halted` — marker not written yet). `test_exhaustion_writes_no_halted_marker` PASSES (no marker is written today anyway — it guards against regression).

- [ ] **Step 3: Stamp the marker in the block-halt branch**

In `.github/agent-factory/engine/advance.py`, the block-halt branch currently reads:

```python
        if is_agent_phase and conclude is not None and conclude.get("blocked") and (this_state.get("on_blocked") == "halt"):
            # GATE BLOCKED → terminate the pipeline before the next phase.
            state_data = lib.load_yaml(sf)
            state_data["state"] = "failed"
            lib.dump_yaml(sf, state_data)
            lib.set_check_run(pid, sha, "completed", "failure", "Gate blocked",
                              csum or "A required gate did not pass; pipeline halted.")
            lib.set_check_run(cr_name, sha, "completed", "failure", "Gate blocked", csum)
            lib.cas_push(dir_, f"{instance}: phase {phase} blocked → pipeline halted")
```

Insert the marker write immediately before the `lib.cas_push(...)` line so it lands in the same push:

```python
            lib.set_check_run(cr_name, sha, "completed", "failure", "Gate blocked", csum)
            # Stamp a durable marker distinguishing this BLOCK from an exhaustion
            # (both write state:failed). The HITL /override command (next.py) reads
            # this to know there is a blocked gate to force past, and which phase.
            inst_data = lib.load_yaml(inf) if os.path.isfile(inf) else {}
            inst_data["halted"] = {"phase": phase, "reason": "blocked", "sha": sha}
            lib.dump_yaml(inf, inst_data)
            lib.cas_push(dir_, f"{instance}: phase {phase} blocked → pipeline halted")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_override.py -k "halted or exhaustion" -v`
Expected: both PASS.

- [ ] **Step 5: Run the full suite (regression guard)**

Run: `pytest tests/ -q`
Expected: all green (the change is additive; the exhaustion path is untouched).

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/advance.py tests/test_override.py
git commit -m "feat(engine): stamp halted marker on block-halt (HITL override substrate)"
```

---

### Task 2: `lib.post_pr_comment` helper

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add function; register a CLI subcommand is NOT required — only `next.py` imports it)
- Test: `tests/test_override.py` (append)

**Interfaces:**
- Produces: `post_pr_comment(pr, body)` — posts a **new** (untracked) PR/issue comment. Honors `ENGINE_LOCAL` (stderr no-op) and `PUBLISH_TOKEN`/`GITHUB_REPOSITORY` env exactly like `upsert_status_comment`. Returns `None`. Used by `next.py` for override announcements and guard/refusal messages.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_override.py`:

```python
def test_post_pr_comment_local_noop(state_origin, capfd):
    """In ENGINE_LOCAL the helper must not call gh; it logs to stderr and returns None."""
    out, err, rc = _run(
        LIB_PY, ["post-pr-comment", "7", "hello world"],
        _env(state_origin),
    )
    assert rc == 0
    assert "pr#7" in err and "hello world" in err
```

(The CLI subcommand exists only to make the helper unit-testable in isolation; `next.py` calls the Python function directly.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_override.py -k post_pr_comment -v`
Expected: FAIL — `lib.py: unknown subcommand post-pr-comment` (rc 2).

- [ ] **Step 3: Add the helper and CLI subcommand**

In `.github/agent-factory/engine/lib.py`, add the function right after `upsert_status_comment` (it mirrors that function's token/env handling):

```python
def post_pr_comment(pr, body):
    """
    post_pr_comment <pr> <body>
    Post a NEW (untracked) PR/issue comment — used for one-off engine notices
    (e.g. HITL override announcements and refusals). Unlike upsert_status_comment
    it does not track or edit an id. Best-effort; ENGINE_LOCAL short-circuits.
    """
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] pr comment pr#{pr}: {body}\n")
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    publish_token = os.environ.get("PUBLISH_TOKEN", "")
    env = dict(os.environ)
    if publish_token:
        env["GH_TOKEN"] = publish_token
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{pr}/comments", "-f", f"body={body}"],
        text=True, capture_output=True, env=env,
    )
    if result.returncode != 0:
        sys.stderr.write("[engine] pr comment post failed (needs issues:write)\n")
```

Then register the CLI subcommand inside `_cli`, next to `upsert-status-comment`:

```python
    elif cmd == "post-pr-comment":
        # post-pr-comment <pr> <body>
        post_pr_comment(args[0], args[1])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_override.py -k post_pr_comment -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_override.py
git commit -m "feat(engine): lib.post_pr_comment (untracked one-off PR comment)"
```

---

### Task 3: The `override` command in next.py (happy path + guards)

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (add `do_override`; dispatch it from the routing cascade)
- Test: `tests/test_override.py` (append)

**Interfaces:**
- Consumes: the `halted` marker from Task 1; `lib.post_pr_comment` from Task 2; existing `seed_and_dispatch_phase`, `lib.next_phase_id`, `lib.state_file`, `lib.instance_file`, `lib.load_yaml`, `lib.dump_yaml`. Reads `OVERRIDE_ACTOR` / `OVERRIDE_REASON` from env (set by the workflow in Task 5).
- Produces: command `override`. On a valid blocked marker → appends `overrides: [{phase, actor, reason}]` to `_instance.yaml`, removes `halted`, advances the cursor one phase, and emits the next phase's `run-agent`/`run-fanout` action (via `seed_and_dispatch_phase`). On any guard failure → posts an explanatory comment and emits `{"action":"halt",...}` with no state change.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_override.py`:

```python
def _seed_blocked(state_origin, tmp_path, inst, head_sha="sha-blk"):
    """Produce a real blocked-halt state (preflight failed + halted marker)."""
    seed_preflight(state_origin, tmp_path / "seed", inst, state="preflight", iteration=1, head_sha=head_sha)
    v = write_json(tmp_path, "v.json", VERDICTS_BLOCK)
    ev = write_json(tmp_path, "ev.json", EVIDENCE_MIN)
    env = _env(state_origin, PHASE="preflight", PR=inst[3:], PR_HEAD_SHA=head_sha)
    _run(ADVANCE_PY, [tmp_path / "adv", inst, PIPELINE_PROTO, v, ev], env)


def test_override_advances_one_phase(state_origin, tmp_path):
    inst = "pr-10"
    _seed_blocked(state_origin, tmp_path, inst)
    env = _env(state_origin, OVERRIDE_ACTOR="alice", OVERRIDE_REASON="ship it")
    out, err, rc = _run(NEXT_PY, [tmp_path / "ovr", inst, PIPELINE_PROTO, "override", "sha-blk"], env)
    assert rc == 0, err
    action = json.loads(out)
    assert action["action"] == "run-fanout"
    assert action["phase"] == "review"

    _clone(state_origin, tmp_path / "verify")
    base = tmp_path / "verify" / PID / inst
    inf = yaml.safe_load((base / "_instance.yaml").read_text())
    assert inf["phase"] == "review"
    assert "halted" not in inf
    assert inf["overrides"] == [{"phase": "preflight", "actor": "alice", "reason": "ship it"}]
    # The blocked gate's verdict is preserved — never rewritten.
    assert yaml.safe_load((base / "preflight.yaml").read_text())["state"] == "failed"
    # The next phase (review fan-out) was seeded.
    for b in REVIEW_BRANCHES:
        assert (base / f"review.{b}.yaml").is_file()


def test_override_announcement_names_actor(state_origin, tmp_path):
    inst = "pr-11"
    _seed_blocked(state_origin, tmp_path, inst)
    env = _env(state_origin, OVERRIDE_ACTOR="bob", OVERRIDE_REASON="")
    _, err, rc = _run(NEXT_PY, [tmp_path / "ovr", inst, PIPELINE_PROTO, "override", "sha-blk"], env)
    assert rc == 0
    assert "overridden by @bob" in err  # the ENGINE_LOCAL comment log


def test_override_on_exhausted_gate_refuses(state_origin, tmp_path):
    inst = "pr-12"
    # preflight failed, but NO halted marker (exhaustion shape).
    seed_preflight(state_origin, tmp_path / "seed", inst, state="failed", iteration=2, head_sha="s")
    out, err, rc = _run(NEXT_PY, [tmp_path / "ovr", inst, PIPELINE_PROTO, "override", "s"], _env(state_origin))
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "exhausted" in err
    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load((tmp_path / "verify" / PID / inst / "_instance.yaml").read_text())
    assert inf["phase"] == "preflight" and "overrides" not in inf


def test_override_no_instance_refuses(state_origin, tmp_path):
    out, err, rc = _run(NEXT_PY, [tmp_path / "ovr", "pr-99", PIPELINE_PROTO, "override", "s"], _env(state_origin))
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "no" in err.lower() and "run exists" in err


def test_override_not_halted_refuses(state_origin, tmp_path):
    inst = "pr-13"
    # preflight still active (state == life_state, not failed), no halted marker.
    seed_preflight(state_origin, tmp_path / "seed", inst, state="preflight", iteration=1, head_sha="s")
    out, err, rc = _run(NEXT_PY, [tmp_path / "ovr", inst, PIPELINE_PROTO, "override", "s"], _env(state_origin))
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "not currently halted" in err


def test_override_is_idempotent(state_origin, tmp_path):
    inst = "pr-14"
    _seed_blocked(state_origin, tmp_path, inst)
    env = _env(state_origin, OVERRIDE_ACTOR="alice", OVERRIDE_REASON="")
    _run(NEXT_PY, [tmp_path / "ovr1", inst, PIPELINE_PROTO, "override", "sha-blk"], env)
    # second override: marker already cleared, cursor on review → "not halted", no double-advance
    out, err, rc = _run(NEXT_PY, [tmp_path / "ovr2", inst, PIPELINE_PROTO, "override", "sha-blk"], env)
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load((tmp_path / "verify" / PID / inst / "_instance.yaml").read_text())
    assert len(inf["overrides"]) == 1  # not doubled


def test_override_reason_is_inert_data(state_origin, tmp_path):
    inst = "pr-15"
    _seed_blocked(state_origin, tmp_path, inst)
    nasty = "$(rm -rf /); `id`; <b>x</b>"
    env = _env(state_origin, OVERRIDE_ACTOR="alice", OVERRIDE_REASON=nasty)
    _run(NEXT_PY, [tmp_path / "ovr", inst, PIPELINE_PROTO, "override", "sha-blk"], env)
    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load((tmp_path / "verify" / PID / inst / "_instance.yaml").read_text())
    assert inf["overrides"][0]["reason"] == nasty  # stored verbatim, never executed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_override.py -k override -v`
Expected: the `override`-command tests FAIL — `next.py` reports `[next] unknown command: override` (rc 2) or `multi-phase 'override' needs a PHASE`.

- [ ] **Step 3: Add `do_override` and dispatch it**

In `.github/agent-factory/engine/next.py`, add this function in the module-level def block, immediately after `seed_and_dispatch_phase` ends (right before the long comment that begins `# Unbranched start/reset ...`):

```python
def do_override():
    """HITL escape-hatch: a write-access human forces a *blocked* gate to advance
    one phase. Authorization happened in the workflow (ctx step); next.py only ever
    sees an authorized override. Reads the `halted` marker on _instance.yaml. On a
    valid blocked marker, records the override beside the failure, clears the
    marker, and seeds+dispatches the next phase. Otherwise posts an explanatory
    comment and halts — no state change. emit_halt is defined below this point in
    the script, so the halt JSON is printed inline here."""
    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    inf = lib.instance_file(DIR, PID, INSTANCE)

    def refuse(message, reason):
        lib.post_pr_comment(pr, message)
        print(json.dumps({"action": "halt", "iteration": 0, "feedback": "", "reason": reason}))

    if not os.path.isfile(inf):
        refuse(f"Nothing to override — no {PID} run exists for this PR.",
               "override: no instance")
        return

    inst = lib.load_yaml(inf)
    halted = inst.get("halted") or {}

    if halted.get("reason") == "blocked":
        blocked_phase = halted.get("phase")
        nxt = lib.next_phase_id(proto_data, blocked_phase)
        if not nxt:
            refuse("The blocked gate is the final phase; there is nothing to advance to.",
                   "override: no next phase")
            return
        actor = os.environ.get("OVERRIDE_ACTOR", "")
        reason = os.environ.get("OVERRIDE_REASON", "")
        inst.setdefault("overrides", []).append(
            {"phase": blocked_phase, "actor": actor, "reason": reason})
        inst.pop("halted", None)
        lib.dump_yaml(inf, inst)  # persist before seed_and_dispatch_phase reloads inf
        note = f"⚠️ {blocked_phase} gate was blocked — overridden by @{actor}; proceeding to {nxt}."
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        # Advance exactly one phase. seed_and_dispatch_phase reloads _instance.yaml
        # (keeping the overrides[] record + cleared halted just written), sets the
        # cursor to nxt, CAS-pushes, and emits that phase's run action.
        seed_and_dispatch_phase(nxt, "override")
        return

    # Not a blocked halt → give a precise message: exhausted vs simply not-halted.
    cursor = inst.get("phase") or ""
    cursor_sf = lib.state_file(DIR, PID, INSTANCE, phase=cursor) if cursor else ""
    cursor_state = (lib.load_yaml(cursor_sf).get("state")
                    if cursor_sf and os.path.isfile(cursor_sf) else "")
    if cursor_state == "failed":
        refuse(f"The {cursor} gate is exhausted (it could not produce a valid result), "
               f"not blocked. Override only applies to a gate that ran and returned a "
               f"blocking verdict; re-run the pipeline instead.",
               "override: exhausted")
    else:
        refuse("Nothing to override — the pipeline is not currently halted at a "
               f"blocked gate (current phase: {cursor}).",
               "override: not halted")
```

Then dispatch it at the very top of the routing cascade. Immediately after the block comment ending `# ... single-agent path both fall through this guard unchanged.` and **before** the line `if lib.is_multiphase(proto_data) and not PHASE and not BRANCH:`, insert:

```python
if COMMAND == "override":
    do_override()
    sys.exit(0)

```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_override.py -k override -v`
Expected: all `override` tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_override.py
git commit -m "feat(engine): /override command advances a blocked gate one phase"
```

---

### Task 4: Declare the `/override` trigger + routing regression

**Files:**
- Modify: `.github/agent-factory/protocols/code-review-pipeline/protocol.json` (add a trigger)
- Test: `tests/test_override.py` (append)

**Interfaces:**
- Consumes: `lib.route` / `lib.match_trigger` (existing).
- Produces: an `issue_comment` whose body starts with `/override` routes to `code-review-pipeline` with command `override`, and does not collide with `/review` or any other protocol's prefixes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_override.py`:

```python
def test_override_trigger_maps_to_command():
    out = subprocess.run(
        ["python3", str(LIB_PY), "match-trigger", str(PIPELINE_PROTO),
         "issue_comment", "", "/override please"],
        text=True, capture_output=True,
    ).stdout.strip()
    assert out == "override"


def test_override_routes_unambiguously():
    protocols_dir = str(ROOT / ".github/agent-factory/protocols")
    r = subprocess.run(
        ["python3", str(LIB_PY), "route", protocols_dir, "issue_comment", "", "/override", "", "true"],
        text=True, capture_output=True,
    )
    assert r.returncode == 0, r.stderr  # no ambiguity raised
    assert f"protocols/{PID}/protocol.json" in r.stdout
    assert "skip=false" in r.stdout


def test_review_still_routes_after_adding_override():
    protocols_dir = str(ROOT / ".github/agent-factory/protocols")
    r = subprocess.run(
        ["python3", str(LIB_PY), "route", protocols_dir, "issue_comment", "", "/review", "", "true"],
        text=True, capture_output=True,
    )
    assert r.returncode == 0, r.stderr
    assert f"protocols/{PID}/protocol.json" in r.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_override.py -k "trigger or routes" -v`
Expected: `test_override_trigger_maps_to_command` FAILS (prints empty string — no `/override` trigger yet); `test_override_routes_unambiguously` FAILS (`skip=true`).

- [ ] **Step 3: Add the trigger**

In `.github/agent-factory/protocols/code-review-pipeline/protocol.json`, add to the `triggers` array (after the existing `/review` entry):

```json
    { "on": "issue_comment", "comment_prefix": "/override", "command": "override" }
```

(Verify the array still parses: `python3 -c "import json; json.load(open('.github/agent-factory/protocols/code-review-pipeline/protocol.json'))"`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_override.py -k "trigger or routes or review_still" -v`
Expected: all PASS — `/override` maps to `override` and routes uniquely; `/review` still routes.

- [ ] **Step 5: Run the full suite (catches cross-protocol ambiguity)**

Run: `pytest tests/ -q`
Expected: all green — in particular `tests/test_route.py` must still pass (no `/override` prefix collides with another protocol's `comment_prefix`).

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/protocols/code-review-pipeline/protocol.json tests/test_override.py
git commit -m "feat(pipeline): declare /override trigger (HITL escape-hatch)"
```

---

### Task 5: Workflow auth gate + override env wiring (agentic-engine.yml)

**Files:**
- Modify: `.github/workflows/agentic-engine.yml` (`ctx` step: auth gate + outputs; `plan` step: env)

**Interfaces:**
- Consumes: the `override` command from `match-trigger`; the trusted `github.event.comment.user.login`; `secrets.POC_DISPATCH_TOKEN` (the permissions API needs push access — the default `GITHUB_TOKEN` may not read collaborator permission, so use the PAT).
- Produces: when authorized, `command=override` flows to `plan` with `OVERRIDE_ACTOR` / `OVERRIDE_REASON` in env; when denied, a denial comment is posted and `command` is cleared (the engine no-ops). This step has no pytest coverage — it is GitHub Actions glue, verified by `actionlint` and the live checkpoint in Task 6.

- [ ] **Step 1: Add the commenter login + token to the `ctx` step env**

In `.github/workflows/agentic-engine.yml`, the `ctx` step's `env:` block (currently ending with `COMMENT_BODY: ${{ github.event.comment.body }}`) gains two entries:

```yaml
        env:
          PROTO: ${{ inputs.protocol }}
          DISPATCH_INSTANCE: ${{ github.event.client_payload.instance }}
          DISPATCH_BRANCH: ${{ github.event.client_payload.branch }}
          DISPATCH_PHASE: ${{ github.event.client_payload.phase }}
          DISPATCH_TYPE: ${{ github.event.action }}
          PR_EVENT_ACTION: ${{ github.event.action }}
          COMMENT_BODY: ${{ github.event.comment.body }}
          COMMENTER_LOGIN: ${{ github.event.comment.user.login }}
          GH_TOKEN: ${{ secrets.POC_DISPATCH_TOKEN }}
```

- [ ] **Step 2: Add the auth gate in the `issue_comment` branch**

Replace the `issue_comment)` case in the `ctx` step's `case` statement:

```bash
            issue_comment)
              PR="${{ github.event.issue.number }}"
              INSTANCE="pr-$PR"
              CMD=$(python3 .github/agent-factory/engine/lib.py match-trigger "$PROTO" issue_comment "" "$COMMENT_BODY")
              if [ "$CMD" = "override" ]; then
                # Authorize from TRUSTED fields only (login from the event, repo
                # permission from the API) — never from the comment body. The body
                # is used only to match the prefix and carry an optional reason.
                PERM=$(gh api "repos/${{ github.repository }}/collaborators/$COMMENTER_LOGIN/permission" \
                  --jq '.permission' 2>/dev/null || echo none)
                if [ "$PERM" = "write" ] || [ "$PERM" = "admin" ]; then
                  REASON="${COMMENT_BODY#/override}"   # strip the command prefix
                  REASON="${REASON# }"                 # strip one leading space
                  echo "override_actor=$COMMENTER_LOGIN" >> "$GITHUB_OUTPUT"
                  { echo "override_reason<<GH_EOF"; printf '%s\n' "$REASON"; echo "GH_EOF"; } >> "$GITHUB_OUTPUT"
                else
                  gh api "repos/${{ github.repository }}/issues/$PR/comments" \
                    -f "body=@$COMMENTER_LOGIN /override requires write access to this repository." >/dev/null 2>&1 || true
                  CMD=""   # not authorized → the engine no-ops (plan/dispatch skip on empty command)
                fi
              fi
              ;;
```

(`COMMENTER_LOGIN`, `COMMENT_BODY`, `REASON` are shell variables sourced from `env:` — never `${{ }}`-interpolated into the script. `$COMMENTER_LOGIN` is GitHub-stamped and is passed to `gh api` as a data argument, not eval'd.)

- [ ] **Step 3: Surface the override outputs from the `plan` job**

In the `plan` job's `outputs:` map, add:

```yaml
      override_actor: ${{ steps.ctx.outputs.override_actor }}
      override_reason: ${{ steps.ctx.outputs.override_reason }}
```

- [ ] **Step 4: Pass the override env into the `plan` step**

In the `plan` step (`id: plan`) `env:` block (which already sets `STATE_REMOTE`, `PUBLISH_TOKEN`, `GITHUB_REPOSITORY`, `HEAD_SHA`, `PROTO`, `BRANCH`, `PHASE`), add:

```yaml
          OVERRIDE_ACTOR: ${{ steps.ctx.outputs.override_actor }}
          OVERRIDE_REASON: ${{ steps.ctx.outputs.override_reason }}
```

`next.py`'s `do_override` reads these from env. The `plan` step already runs only when `steps.ctx.outputs.command != ''`, so a denied override (empty command) correctly skips it.

- [ ] **Step 5: Lint the workflow**

Run: `actionlint .github/workflows/agentic-engine.yml`
Expected: no errors. (If `actionlint` is not installed: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/agentic-engine.yml'))"` must succeed, and manually confirm the `case` block is balanced and every agent-derived value is referenced as a shell variable, never `${{ }}`-interpolated into `run:`.)

- [ ] **Step 6: Run the full suite (unchanged engine behavior)**

Run: `pytest tests/ -q`
Expected: all green (no Python changed in this task; this guards against accidental edits).

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/agentic-engine.yml
git commit -m "feat(engine): /override auth gate + actor/reason wiring (workflow)"
```

---

### Task 6: Documentation + live-checkpoint note

**Files:**
- Modify: `docs/BACKLOG.md` (re-scope the v4 entry)
- Modify: `docs/STATUS.md` (record the escape-hatch as shipped)

**Interfaces:** none (docs only).

- [ ] **Step 1: Re-scope the v4 backlog entry**

In `docs/BACKLOG.md`, under `## v4 — Human-in-the-loop (approval gate)`, add a note at the top of that section distinguishing what shipped from what remains:

```markdown
**Update (2026-06-17):** the *override escape-hatch* — a write-access human forcing
a **blocked** halt-gate past one phase via `/override` — shipped separately (see
`docs/superpowers/specs/2026-06-17-hitl-override-gate-design.md`). What remains in
THIS item is the broader **pause-and-require** `kind:"gate"` approval state (a human
sign-off as a *required* transition), which is still not started.
```

- [ ] **Step 2: Record in STATUS.md**

In `docs/STATUS.md`, add a short subsection (near the other shipped-feature notes) stating: the `/override` escape-hatch is implemented and live-verified; it advances a **blocked** gate one phase; it is **blocked-only** (an exhausted gate returns a distinct refusal); authorization is the GitHub permissions API (`write`/`admin`) on the trusted commenter login; the blocked gate's `failed` verdict is never rewritten; the override is recorded in `_instance.yaml.overrides[]` and the git log.

- [ ] **Step 3: Commit**

```bash
git add docs/BACKLOG.md docs/STATUS.md
git commit -m "docs: record /override escape-hatch; re-scope v4 approval gate"
```

- [ ] **Step 4: Final full-suite run**

Run: `pytest tests/ -q`
Expected: all green.

---

## Live checkpoint (post-merge, manual — not a code task)

A workflow change to `agentic-engine.yml` only takes effect once on `main`. After merge, on a PR whose preflight **blocks** (use a PR that adds code without a matching spec, or apply the `poc:sabotage` label per CLAUDE.md to force a preflight block):

1. **Denied:** a user *without* write access comments `/override` → a denial comment is posted; the pipeline does not advance; preflight check-run stays red.
2. **Authorized:** a user *with* write access comments `/override accepting the risk` → the review fan-out launches; a `⚠️ … overridden by @<user>` comment appears; `git log agentic-state -- code-review-pipeline/pr-N/_instance.yaml` shows the `overrides[]` record; the preflight check-run remains `failure`.
3. **Exhausted:** on a PR whose preflight *exhausted* (never produced valid evidence), `/override` → the "gate is exhausted … not blocked" comment; no advance.

---

## Self-review (run against the spec)

**Spec coverage:**
- halted marker distinguishing block vs exhaustion → Task 1 ✓
- `/override` comment trigger → Task 4 ✓
- permissions-API auth on trusted login, never body → Task 5 ✓ (Global Constraints + Step 2)
- one-gate-at-a-time advance → Task 3 (`seed_and_dispatch_phase(next_phase_id(...))`) ✓
- blocked-only; exhausted refused with distinct message → Task 3 + test `test_override_on_exhausted_gate_refuses` ✓
- verdict never rewritten → Task 3 (preflight `state:failed` untouched) + test assertion ✓
- audit: `overrides[]` + announcement comment + git log → Task 3 ✓; aggregate check-run returns to in_progress via the existing "Mark pipeline in progress" step when the review fan-out launches (no extra code) ✓
- guard/error matrix (5 rows) → Task 3 (`do_override` branches) + Task 5 (auth denial) + tests ✓
- idempotency → Task 3 test `test_override_is_idempotent` ✓
- reason is inert data → Task 3 test `test_override_reason_is_inert_data` ✓
- testing plan (`tests/test_override.py`, regression guard, live checkpoint) → Tasks 1-6 ✓

**Refinement vs spec:** the spec said "status comment updated"; this plan posts a **dedicated** override comment (via `post_pr_comment`) instead of editing the tracked fan-out status comment. Rationale: the fan-out status comment is create-once and is re-rendered with branch progress by the subsequent review legs, which would clobber an override note; a dedicated comment is always-visible and cannot be overwritten. The aggregate check-run + git log audit are unchanged. This is a strictly-more-robust realization of the spec's audit intent.

**Placeholder scan:** none — every code/test/YAML step contains complete content.

**Type/name consistency:** `halted` marker shape `{phase, reason, sha}` is written in Task 1 and read in Task 3; `overrides[]` element shape `{phase, actor, reason}` is written and asserted consistently; `OVERRIDE_ACTOR`/`OVERRIDE_REASON` env names match between Task 5 (set) and Task 3 (read); `post_pr_comment(pr, body)` signature matches between Task 2 (def) and Task 3 (calls).
