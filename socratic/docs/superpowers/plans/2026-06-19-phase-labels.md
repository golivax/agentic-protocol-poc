# Phase Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a single PR label in sync with the protocol state-machine's current head (setup → each phase → terminal), protocol-agnostically.

**Architecture:** A pure resolver `phase_label_text(protocol, key)` maps a state id or engine terminal key to display text, and a best-effort reconciler `ensure_phase_label(...)` makes the PR's label match the head and records the applied label in `_instance.yaml`. Both live in `lib.py`. The reconciler is called at the existing cursor-moving seams in `next.py`, `advance.py`, and `join.py`. Instance-based protocols only; the v1 single-agent `grumpy-review` path is never called and stays byte-identical.

**Tech Stack:** Python 3 + PyYAML (runtime), `gh` CLI (GitHub labels API), pytest (tests).

## Global Constraints

- Runtime deps are **only** Python 3 + PyYAML. No new imports beyond `os`, `sys`, `subprocess`, `json` (all already imported in `lib.py`).
- Every `gh` side-effect is **best-effort**: it must never raise and never break a transition (mirror `lib.set_check_run`).
- Honor `ENGINE_LOCAL=1`: under it, label functions log intent to stderr and perform NO `gh` calls (but `ensure_phase_label` still records `phase_label` in `_instance.yaml` so tests can assert on state).
- Agent-derived strings are never used here — phase ids/labels come from the trusted `protocol.json`. No interpolation into shell `run:` blocks.
- The v1 `grumpy-review` (single-agent) path MUST remain byte-identical: no label call may execute on it. `ensure_phase_label` no-ops when `_instance.yaml` is absent (which is always true for v1).
- GitHub token for `gh`: read `PUBLISH_TOKEN` into `GH_TOKEN` (same pattern as `set_check_run`); `--repo` from `GITHUB_REPOSITORY`.
- Engine terminal/special head keys are exactly: `setup`, `done`, `failed`, `blocked`.
- Run the full suite with `pytest tests/ -q` after each task; it must stay green.

---

## File Structure

- `.github/agent-factory/engine/lib.py` — **Modify.** Add label constants + 4 functions: `phase_label_text`, `_ensure_and_add_label`, `remove_pr_label`, `apply_setup_label`, `ensure_phase_label`.
- `.github/agent-factory/engine/next.py` — **Modify.** Setup label on multiphase start/reset and on `start_fanout`; phase label inside `seed_and_dispatch_phase` and `start_fanout`; old-label cleanup on restart; terminal labels in `do_resolve_gate`.
- `.github/agent-factory/engine/advance.py` — **Modify.** Phase/terminal labels in the agent-phase branches (blocked / advance-to-next / done / agent-phase failed).
- `.github/agent-factory/engine/join.py` — **Modify.** Labels at the three join terminals (open-next-gate / done / failed).
- `.github/agent-factory/protocols/code-review-pipeline/protocol.json` — **Modify.** Add `label` to states that want custom display text.
- `tests/test_phase_labels.py` — **Create.** Unit tests for the resolver + reconciler + integration across the engine seams + a v1 regression assertion.

---

### Task 1: `phase_label_text` resolver + constants

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add near the other `gh` helpers, e.g. after `set_check_run`, around line 361)
- Test: `tests/test_phase_labels.py`

**Interfaces:**
- Produces:
  - `PHASE_LABEL_DEFAULTS: dict[str,str]` — engine terminal/special defaults.
  - `PHASE_LABEL_COLOR: str` — hex color (no `#`) for created labels.
  - `phase_label_text(protocol: dict, key: str) -> str` — `protocol` is the parsed protocol JSON **dict** (not a path). `key` is a state id or one of `setup`/`done`/`failed`/`blocked`. Returns the display string.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_phase_labels.py` with:

```python
import os
import sys
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def test_label_text_explicit_state_label():
    proto = {"states": [{"id": "preflight", "kind": "agent", "label": "pre-flight gate"}]}
    assert lib.phase_label_text(proto, "preflight") == "pre-flight gate"


def test_label_text_humanizes_id_when_no_label():
    proto = {"states": [{"id": "code-review", "kind": "agent"}]}
    assert lib.phase_label_text(proto, "code-review") == "Code review"


def test_label_text_terminal_default():
    proto = {"states": []}
    assert lib.phase_label_text(proto, "done") == "✅ done"
    assert lib.phase_label_text(proto, "failed") == "❌ failed"
    assert lib.phase_label_text(proto, "blocked") == "⛔ blocked"
    assert lib.phase_label_text(proto, "setup") == "⚙ setup"


def test_label_text_terminal_override():
    proto = {"states": [], "phase_labels": {"done": "shipped 🚀"}}
    assert lib.phase_label_text(proto, "done") == "shipped 🚀"
    # unknown override key still falls back to default
    assert lib.phase_label_text(proto, "failed") == "❌ failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phase_labels.py -v`
Expected: FAIL (`AttributeError: module 'lib' has no attribute 'phase_label_text'`).

- [ ] **Step 3: Implement the resolver in `lib.py`**

Add after `set_check_run` (around line 361):

```python
# --- Phase labels -----------------------------------------------------------
# Engine-level head keys that are NOT protocol states. Protocols may override
# any of these via a top-level "phase_labels" map in protocol.json.
PHASE_LABEL_DEFAULTS = {
    "setup": "⚙ setup",
    "done": "✅ done",
    "failed": "❌ failed",
    "blocked": "⛔ blocked",
}
PHASE_LABEL_COLOR = "5319e7"  # one color for every engine-managed phase label


def _humanize_state_id(state_id):
    return state_id.replace("-", " ").replace("_", " ").strip().capitalize()


def phase_label_text(protocol, key):
    """Resolve a state id OR a terminal/special key to a PR label string.

    Live phase (key matches a states[] id): the state's `label` if present, else
    a humanized id. Terminal/special key (setup/done/failed/blocked): the
    protocol's optional top-level `phase_labels[key]` override if present, else
    the engine default. `protocol` is the parsed protocol JSON dict.
    """
    st = state_by_id(protocol, key)
    if st is not None:
        return st.get("label") or _humanize_state_id(key)
    overrides = protocol.get("phase_labels", {}) or {}
    if key in overrides:
        return overrides[key]
    return PHASE_LABEL_DEFAULTS.get(key, _humanize_state_id(key))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_phase_labels.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_phase_labels.py
git commit -m "feat(engine): phase_label_text resolver for PR phase labels

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: label gh helpers + `ensure_phase_label` reconciler

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (immediately after `phase_label_text`)
- Test: `tests/test_phase_labels.py`

**Interfaces:**
- Consumes: `phase_label_text`, `PHASE_LABEL_COLOR`, `instance_file`, `load_yaml`, `dump_yaml` (all in `lib.py`).
- Produces:
  - `apply_setup_label(protocol: dict, pr) -> None` — best-effort add of the `setup` label; no state tracking (called before `_instance.yaml` exists).
  - `remove_pr_label(pr, label: str) -> None` — best-effort remove of one label (used on restart cleanup).
  - `ensure_phase_label(dir_, pid, instance, protocol: dict, pr, head_key: str) -> None` — reconcile the PR's phase label to `head_key`; no-op without `_instance.yaml`; records `phase_label` on `_instance.yaml` (caller `cas_push`es).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phase_labels.py`:

```python
def _engine_local_env(monkeypatch):
    monkeypatch.setenv("ENGINE_LOCAL", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")


def _write_instance(tmp_path, pid, instance, data):
    inf = tmp_path / pid / instance / "_instance.yaml"
    inf.parent.mkdir(parents=True, exist_ok=True)
    with open(inf, "w") as fh:
        yaml.safe_dump(data, fh)
    return inf


def test_ensure_phase_label_records_applied(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "preflight", "kind": "agent", "label": "pre-flight gate"}]}
    inf = _write_instance(tmp_path, "p", "pr-1", {"protocol": "p", "instance": "pr-1"})
    lib.ensure_phase_label(str(tmp_path), "p", "pr-1", proto, "1", "preflight")
    assert yaml.safe_load(inf.read_text())["phase_label"] == "pre-flight gate"


def test_ensure_phase_label_idempotent_noop(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "review", "kind": "fanout"}]}
    inf = _write_instance(tmp_path, "p", "pr-2",
                          {"protocol": "p", "instance": "pr-2", "phase_label": "Review"})
    lib.ensure_phase_label(str(tmp_path), "p", "pr-2", proto, "1", "review")
    # unchanged; still "Review"
    assert yaml.safe_load(inf.read_text())["phase_label"] == "Review"


def test_ensure_phase_label_noop_without_instance_file(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "review", "kind": "agent"}]}
    # no _instance.yaml written → must not raise, must not create one
    lib.ensure_phase_label(str(tmp_path), "p", "pr-3", proto, "1", "review")
    assert not (tmp_path / "p" / "pr-3" / "_instance.yaml").exists()


def test_ensure_phase_label_terminal_key(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "approval", "kind": "gate"}]}
    inf = _write_instance(tmp_path, "p", "pr-4",
                          {"protocol": "p", "instance": "pr-4", "phase_label": "Approval"})
    lib.ensure_phase_label(str(tmp_path), "p", "pr-4", proto, "1", "done")
    assert yaml.safe_load(inf.read_text())["phase_label"] == "✅ done"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phase_labels.py -k ensure_phase_label -v`
Expected: FAIL (`AttributeError: module 'lib' has no attribute 'ensure_phase_label'`).

- [ ] **Step 3: Implement the helpers in `lib.py`**

Add immediately after `phase_label_text`:

```python
def _gh_label_cmd(args):
    """Run a best-effort `gh` command for labels/PR-edit. Returns (ok, stderr).
    Never raises. Uses PUBLISH_TOKEN (as GH_TOKEN) + GITHUB_REPOSITORY."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    env = dict(os.environ)
    token = os.environ.get("PUBLISH_TOKEN", "")
    if token:
        env["GH_TOKEN"] = token
    try:
        result = subprocess.run(
            ["gh"] + args + (["--repo", repo] if repo else []),
            text=True, capture_output=True, env=env,
        )
        return result.returncode == 0, result.stderr
    except Exception as e:  # gh missing, etc. — never break a transition
        return False, str(e)


def _ensure_and_add_label(text, pr):
    """Ensure the label exists (idempotent --force create) then add it to the PR.
    Best-effort. ENGINE_LOCAL → log only."""
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] add-label pr={pr}: {text}\n")
        return
    # gh pr edit --add-label errors on a nonexistent label, so create-first.
    _gh_label_cmd(["label", "create", text, "--color", PHASE_LABEL_COLOR, "--force"])
    ok, err = _gh_label_cmd(["pr", "edit", str(pr), "--add-label", text])
    if not ok:
        sys.stderr.write(f"[engine] add-label failed for '{text}': {err}\n")


def remove_pr_label(pr, label):
    """Best-effort remove one label from the PR. ENGINE_LOCAL → log only."""
    if not label:
        return
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] remove-label pr={pr}: {label}\n")
        return
    _gh_label_cmd(["pr", "edit", str(pr), "--remove-label", label])


def apply_setup_label(protocol, pr):
    """Add the engine 'setup' label to the PR. Best-effort, no state tracking —
    called before _instance.yaml exists. ensure_phase_label removes it later."""
    _ensure_and_add_label(phase_label_text(protocol, "setup"), pr)


def ensure_phase_label(dir_, pid, instance, protocol, pr, head_key):
    """Reconcile the PR's phase label to `head_key`.

    Reads the applied label from _instance.yaml; if it differs from the resolved
    new text, removes {prev} ∪ {setup-label} and adds the new one; records the
    new text back on _instance.yaml. No-op when there is no _instance.yaml (this
    excludes the single-agent v1 path). Best-effort. ENGINE_LOCAL → log + still
    record state. The CALLER cas_pushes the instance file."""
    inf = instance_file(dir_, pid, instance)
    if not os.path.isfile(inf):
        return
    inst = load_yaml(inf) or {}
    new = phase_label_text(protocol, head_key)
    prev = inst.get("phase_label", "") or ""
    if prev == new:
        return
    setup_text = phase_label_text(protocol, "setup")
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] phase-label {instance}: {prev or '∅'} → {new}\n")
        inst["phase_label"] = new
        dump_yaml(inf, inst)
        return
    for old in {prev, setup_text}:
        if old and old != new:
            remove_pr_label(pr, old)
    _ensure_and_add_label(new, pr)
    inst["phase_label"] = new
    dump_yaml(inf, inst)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_phase_labels.py -v`
Expected: PASS (8 tests total).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_phase_labels.py
git commit -m "feat(engine): ensure_phase_label reconciler + setup/remove label helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire label calls into `next.py`

**Files:**
- Modify: `.github/agent-factory/engine/next.py`
- Test: `tests/test_phase_labels.py`

**Interfaces:**
- Consumes: `lib.apply_setup_label`, `lib.ensure_phase_label`, `lib.remove_pr_label` (from Task 2).

Five edits. The `pr` value is derived the same way the file already does it: `INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE`.

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_phase_labels.py`:

```python
from conftest import run_engine, read_state_yaml  # noqa: E402

CRP_PROTO = ROOT / ".github/agent-factory/protocols/code-review-pipeline/protocol.json"


def test_start_seeds_first_phase_label(engine_env, tmp_path):
    """A fresh `start` on code-review-pipeline records the first phase's label."""
    state_dir = tmp_path / "state"
    out, err, rc = run_engine(
        "next.py", state_dir, "pr-700", CRP_PROTO, "start", "deadbeef",
        env=engine_env,
    )
    assert rc == 0, err
    inf = state_dir / "code-review-pipeline" / "pr-700" / "_instance.yaml"
    data = read_state_yaml(inf)
    # preflight gets the explicit label added in Task 6; until then it is the
    # humanized id. Assert against the resolved value to stay decoupled:
    assert data["phase"] == "preflight"
    assert data["phase_label"]  # non-empty: a label was recorded
```

Note: this test only asserts a label was recorded; Task 6 adds the exact `"pre-flight gate"` text and a stricter assertion.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase_labels.py::test_start_seeds_first_phase_label -v`
Expected: FAIL (`KeyError: 'phase_label'` — next.py does not yet record it).

- [ ] **Step 3: Edit `next.py` — (a) setup + seed phase label in `seed_and_dispatch_phase`**

In `seed_and_dispatch_phase`, the reset block (around lines 121-139) currently reads `prev` and, when `reset_instance`, wipes files. Add old-label cleanup. Change the reset block so that right after `prev = lib.load_yaml(inf) if os.path.isfile(inf) else {}` and inside `if reset_instance:`, before wiping files:

```python
    prev = lib.load_yaml(inf) if os.path.isfile(inf) else {}
    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    if reset_instance:
        # Abandon the prior run's status comment so this run gets a FRESH one.
        old_cid = prev.get("status_comment_id")
        if old_cid:
            frozen = lib.render_instance_status_body(DIR, PID, INSTANCE, PROTO)
            banner = (f"↻ _Superseded — a newer run started (new commit or "
                      f"`/review`); see the newest **{PID} · {INSTANCE}** comment below._")
            lib.finalize_superseded_comment(pr, old_cid, f"{banner}\n\n{frozen}")
        # Remove the prior run's phase label so a restart from e.g. "approval
        # gate" does not orphan it (the wipe below drops our tracking of it).
        lib.remove_pr_label(pr, prev.get("phase_label", ""))
        for name in os.listdir(inst_dir):
            p = os.path.join(inst_dir, name)
            if os.path.isfile(p):
                os.remove(p)
        inst = {}
    else:
        inst = prev
```

(The existing inner `pr = ...` line inside `if old_cid:` is removed — `pr` is now computed once above.)

Then, immediately after `lib.dump_yaml(inf, inst)` (the cursor write, ~line 154) and BEFORE the `if kind == ...` dispatch, add the reconcile call:

```python
    lib.dump_yaml(inf, inst)

    # Sync the PR's phase label to this phase (removes setup / prior label).
    lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, phase_id)
```

This single call covers all three kinds (fanout/gate/agent) because each subsequent kind-branch only writes per-branch state or refreshes the status comment (preserving `phase_label`) and then `cas_push`es.

- [ ] **Step 4: Edit `next.py` — (b) setup label at the multiphase entry**

In the multiphase entry block (around lines 386-392):

```python
if lib.is_multiphase(proto_data) and not PHASE and not BRANCH:
    if COMMAND in ("start", "reset"):
        first = lib.phase_states(proto_data)[0]["id"]
        pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
        lib.apply_setup_label(proto_data, pr)
        seed_and_dispatch_phase(first, COMMAND, reset_instance=True)
        sys.exit(0)
    else:
        sys.stderr.write(f"[next] multi-phase '{COMMAND}' needs a PHASE\n")
        sys.exit(2)
```

- [ ] **Step 5: Edit `next.py` — (c) setup + phase label for `start_fanout` (multi-grumpy)**

In `start_fanout`, after seeding `_instance.yaml` (the `lib.dump_yaml(inf, {...})` around line 76-81) and before `lib.cas_push` (line 83):

```python
    lib.dump_yaml(inf, {
        "protocol": PID,
        "instance": INSTANCE,
        "head_sha": HEAD_SHA,
        "joined": False,
    })

    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, fstate)

    lib.cas_push(DIR, f"{PID}/{INSTANCE}: fan-out review ({COMMAND})")
```

And in the single-phase fan-out entry block (around lines 402-405), apply setup before `start_fanout()`:

```python
if not BRANCH and is_fanout() and not PHASE:
    if COMMAND in ("start", "reset"):
        pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
        lib.apply_setup_label(proto_data, pr)
        start_fanout()
        sys.exit(0)
```

- [ ] **Step 6: Edit `next.py` — (d) terminal labels in `do_resolve_gate`**

In `do_resolve_gate`, the `approve` + no-next branch (around lines 322-332), add before `lib.cas_push`:

```python
        else:
            lib.set_check_run(PID, sha, "completed", "success", "Complete", f"Approved by @{actor}.")
            note = f"✅ {cursor} gate approved by @{actor}; pipeline complete."
            if reason:
                note += f"\n\n> {reason}"
            lib.post_pr_comment(pr, note)
            body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
            lib.upsert_status_comment(inf, pr, body)
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "done")
            lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} approved by {actor} → done")
```

In the `reject` branch (around lines 353-369), add before `lib.cas_push`:

```python
        lib.post_pr_comment(pr, note)
        lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "failed")
        lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} rejected by {actor} → failed")
```

(The `approve` + next-phase branch needs NO change — it calls `seed_and_dispatch_phase(nxt)`, which already reconciles to `nxt`. The `request-changes` branch needs NO change — the PR stays at the gate phase.)

- [ ] **Step 7: Run the integration test + full suite**

Run: `pytest tests/test_phase_labels.py::test_start_seeds_first_phase_label -v`
Expected: PASS.

Run: `pytest tests/ -q`
Expected: all green (no regressions).

- [ ] **Step 8: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_phase_labels.py
git commit -m "feat(engine): label PR phase from next.py (setup/seed/restart/gate terminals)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Wire label calls into `advance.py`

**Files:**
- Modify: `.github/agent-factory/engine/advance.py`
- Test: `tests/test_phase_labels.py`

**Interfaces:**
- Consumes: `lib.ensure_phase_label`. Uses existing `dir_`, `pid`, `instance`, `proto` (dict), `pr`, `phase`, `agent_state` locals.

Labels are set ONLY in agent-phase code paths. Fan-out legs (the `else` at ~line 395) and v1 single-agent (the `failed` branch at ~line 438 when not a phase) must NOT label — the phase outcome for a fan-out is decided by `join.py`, and v1 is out of scope.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase_labels.py`:

```python
def test_advance_agent_phase_block_sets_blocked_label(tmp_path, monkeypatch):
    """When an agent phase with on_blocked=halt blocks, the label becomes blocked.

    Unit-level: call ensure_phase_label with head_key='blocked' against an
    instance file mirroring the blocked seam, to lock the contract advance.py
    relies on (full e2e block path is covered by the engine's own gate tests)."""
    monkeypatch.setenv("ENGINE_LOCAL", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    proto = {"name": "p", "states": [{"id": "preflight", "kind": "agent"}]}
    inf = tmp_path / "p" / "pr-9" / "_instance.yaml"
    inf.parent.mkdir(parents=True, exist_ok=True)
    inf.write_text("protocol: p\ninstance: pr-9\nphase_label: Preflight\n")
    lib.ensure_phase_label(str(tmp_path), "p", "pr-9", proto, "9", "blocked")
    assert yaml.safe_load(inf.read_text())["phase_label"] == "⛔ blocked"
```

This test passes already (it exercises Task 2). It documents the contract; the wiring below is verified by the full-suite run plus the e2e test in Task 7.

- [ ] **Step 2: Run it (sanity, should pass)**

Run: `pytest tests/test_phase_labels.py::test_advance_agent_phase_block_sets_blocked_label -v`
Expected: PASS.

- [ ] **Step 3: Edit `advance.py` — blocked branch**

In the `is_agent_phase and conclude ... blocked ... halt` branch, before `lib.cas_push(dir_, f"{instance}: phase {phase} blocked → pipeline halted")` (~line 362):

```python
            lib.post_pr_comment(pr, notice)
            lib.ensure_phase_label(dir_, pid, instance, proto, pr, "blocked")
            lib.cas_push(dir_, f"{instance}: phase {phase} blocked → pipeline halted")
```

- [ ] **Step 4: Edit `advance.py` — advance-to-next branch**

In the `elif is_agent_phase:` / `if nxt:` branch, before `lib.cas_push(dir_, f"{instance}: phase {phase} clear → advancing to {nxt}")` (~line 379):

```python
                lib.cas_push... # (see below; insert the label call ABOVE this line)
```

Concretely, insert between `update_status_comment(...)` and `lib.cas_push`:

```python
                update_status_comment(
                    sf, inf, branch, pr, pid, instance, proto_path, dir_,
                    "⏳ advancing", max_iter, github_repository
                )
                lib.ensure_phase_label(dir_, pid, instance, proto, pr, nxt)
                lib.cas_push(dir_, f"{instance}: phase {phase} clear → advancing to {nxt}")
```

- [ ] **Step 5: Edit `advance.py` — no-further-phase (done) branch**

In the `else:` (no `nxt`) branch, before `lib.cas_push(dir_, f"{instance}: phase {phase} clear → done (no further phase)")` (~line 394):

```python
                update_status_comment(
                    sf, inf, branch, pr, pid, instance, proto_path, dir_,
                    "✅ complete", max_iter, github_repository
                )
                lib.ensure_phase_label(dir_, pid, instance, proto, pr, "done")
                lib.cas_push(dir_, f"{instance}: phase {phase} clear → done (no further phase)")
```

- [ ] **Step 6: Edit `advance.py` — agent-phase failed (exhausted) branch**

In the `else:  # process == "failed"` branch (~line 438), add a guarded label call. Insert after `lib.dump_yaml(sf, state_data)` and before `lib.set_check_run(...)`:

```python
    else:  # process == "failed"
        # Exhausted
        state_data = lib.load_yaml(sf)
        state_data["state"] = "failed"
        lib.dump_yaml(sf, state_data)

        # An agent PHASE that exhausts its iterations is a terminal phase failure
        # (label it). A fan-out leg / single-agent v1 reaching here is NOT a phase
        # terminal — join.py (fan-out) owns that, and v1 has no instance label.
        _failed_state = lib.state_by_id(proto, agent_state)
        if phase and _failed_state and _failed_state.get("kind") == "agent":
            lib.ensure_phase_label(dir_, pid, instance, proto, pr, "failed")

        lib.set_check_run(
            cr_name, sha, "completed", "failure",
            "Review failed",
            f"Could not produce a valid review after {max_iter} iterations."
        )
```

(The label write lands on `_instance.yaml`, which the existing `lib.cas_push(dir_, f"{instance}: iterations exhausted → failed")` at the end of this branch will push.)

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add .github/agent-factory/engine/advance.py tests/test_phase_labels.py
git commit -m "feat(engine): label PR phase from advance.py agent-phase transitions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Wire label calls into `join.py`

**Files:**
- Modify: `.github/agent-factory/engine/join.py`
- Test: covered by full-suite run + Task 7 e2e.

**Interfaces:**
- Consumes: `lib.ensure_phase_label`. Uses existing `dir_`, `pid`, `instance`, `protocol` (dict), `pr` locals.

Three terminals: join→open-next-gate, join→done (no gate), join→failed.

- [ ] **Step 1: Edit `join.py` — join clears into a following gate**

In the `if gns and gns.get("kind") == "gate":` block (~lines 101-107), before `lib.cas_push`:

```python
        if gns and gns.get("kind") == "gate":
            instance_data["joined"] = True
            instance_data["phase"] = gate_next
            lib.dump_yaml(inf, instance_data)
            lib.open_gate(dir_, pid, instance, proto, gate_next, sha, pr)
            lib.ensure_phase_label(dir_, pid, instance, protocol, pr, gate_next)
            lib.cas_push(dir_, f"{instance}: join clear → gate {gate_next} open")
            return
```

- [ ] **Step 2: Edit `join.py` — join finalizes (done or failed)**

At the finalize tail (~lines 129-131), after `instance_data["joined"] = True` / `lib.dump_yaml(inf, instance_data)` and before `lib.cas_push`, map the aggregate conclusion to a terminal label. `concl` is `"success"` or `"failure"` at this point:

```python
    instance_data["joined"] = True
    lib.dump_yaml(inf, instance_data)
    lib.ensure_phase_label(dir_, pid, instance, protocol, pr,
                           "done" if concl == "success" else "failed")
    lib.cas_push(dir_, f"{instance}: join → {concl} (all branches terminal)")
```

- [ ] **Step 3: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add .github/agent-factory/engine/join.py
git commit -m "feat(engine): label PR phase from join.py terminals (gate/done/failed)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Add display labels to `code-review-pipeline/protocol.json`

**Files:**
- Modify: `.github/agent-factory/protocols/code-review-pipeline/protocol.json`
- Test: `tests/test_phase_labels.py`

**Interfaces:**
- Consumes: `phase_label_text` reads the new `label` fields.

- [ ] **Step 1: Tighten the start test to assert exact text**

In `tests/test_phase_labels.py`, replace the loose assertion in `test_start_seeds_first_phase_label` with the exact label:

```python
    assert data["phase"] == "preflight"
    assert data["phase_label"] == "pre-flight gate"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_phase_labels.py::test_start_seeds_first_phase_label -v`
Expected: FAIL (label is currently the humanized `"Preflight"`).

- [ ] **Step 3: Add `label` fields to the protocol's states**

Edit `.github/agent-factory/protocols/code-review-pipeline/protocol.json`. Add a `"label"` key to each phase/gate state:

- `preflight` state: add `"label": "pre-flight gate"`
- `review` state: add `"label": "review"`
- `approval` state: add `"label": "approval gate"`

For example the `preflight` state head becomes:

```json
    {
      "id": "preflight",
      "kind": "agent",
      "label": "pre-flight gate",
      "workflow": "preflight-agent",
```

(The `join` state is a transition, never a head the engine labels — leave it unlabeled.)

- [ ] **Step 4: Run the test + validate JSON**

Run: `python3 -c "import json; json.load(open('.github/agent-factory/protocols/code-review-pipeline/protocol.json'))" && echo OK`
Expected: `OK`.

Run: `pytest tests/test_phase_labels.py::test_start_seeds_first_phase_label -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/protocols/code-review-pipeline/protocol.json tests/test_phase_labels.py
git commit -m "feat(code-review-pipeline): display labels for pre-flight/review/approval phases

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: End-to-end phase-label progression + v1 regression guard

**Files:**
- Test: `tests/test_phase_labels.py`

**Interfaces:**
- Consumes: the full wiring from Tasks 3-6 via `run_engine`.

- [ ] **Step 1: Write the e2e + regression tests**

Append to `tests/test_phase_labels.py`:

```python
GRUMPY_PROTO = ROOT / ".github/agent-factory/protocols/grumpy/protocol.json"


def test_phase_advance_relabels(engine_env, tmp_path):
    """Driving the cursor to the next phase via advance-phase relabels the PR."""
    state_dir = tmp_path / "state"
    # start → preflight
    _, err, rc = run_engine("next.py", state_dir, "pr-701", CRP_PROTO,
                            "start", "cafe1234", env=engine_env)
    assert rc == 0, err
    inf = state_dir / "code-review-pipeline" / "pr-701" / "_instance.yaml"
    assert read_state_yaml(inf)["phase_label"] == "pre-flight gate"

    # advance-phase to the review fanout (orchestrator would set PHASE=review)
    env2 = dict(engine_env)
    env2["PHASE"] = "review"
    _, err, rc = run_engine("next.py", state_dir, "pr-701", CRP_PROTO,
                            "advance-phase", "cafe1234", env=env2)
    assert rc == 0, err
    assert read_state_yaml(inf)["phase_label"] == "review"


def test_v1_grumpy_records_no_phase_label(engine_env, tmp_path):
    """The single-agent v1 path has no _instance.yaml → no phase label, and the
    state file it writes carries no phase_label key (byte-identical baseline)."""
    state_dir = tmp_path / "state"
    _, err, rc = run_engine("next.py", state_dir, "pr-702", GRUMPY_PROTO,
                            "start", "f00dface", env=engine_env)
    assert rc == 0, err
    # no instance file at all for v1
    assert not (state_dir / "grumpy-review" / "pr-702" / "_instance.yaml").exists()
    sf = state_dir / "grumpy-review" / "pr-702" / "review.yaml"
    assert "phase_label" not in read_state_yaml(sf)
```

- [ ] **Step 2: Run the new tests**

Run: `pytest tests/test_phase_labels.py -k "phase_advance_relabels or v1_grumpy" -v`
Expected: PASS (both).

- [ ] **Step 3: Run the FULL suite (final regression gate)**

Run: `pytest tests/ -q`
Expected: all green, including the v1+v2 regression guard in `test_engine.py`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_phase_labels.py
git commit -m "test(engine): e2e phase-label progression + v1 no-label regression guard

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Explicit per-state `label` + humanize fallback → Task 1 (resolver) + Task 6 (data). ✓
- Terminal defaults, protocol override → Task 1 (`PHASE_LABEL_DEFAULTS` + `phase_labels` lookup). ✓
- Track applied label in `_instance.yaml`, remove-exactly-one (∪ setup) → Task 2 (`ensure_phase_label`). ✓
- Terminal outcomes labeled (setup/done/failed/blocked) → Tasks 3-5 cover every seam: setup (Task 3), done (Tasks 3/4/5), failed (Tasks 3/4/5), blocked (Task 4). ✓
- Setup pre-instance + always-remove-setup → Task 2 (removal set includes setup), Task 3 (`apply_setup_label`). ✓
- Restart old-label cleanup → Task 3 step 3 (`remove_pr_label(prev.phase_label)`). ✓
- Instance-based scope only; v1 untouched/byte-identical → `ensure_phase_label` no-ops without `_instance.yaml`; no v1 call site; Task 7 regression test. ✓
- Best-effort, ENGINE_LOCAL-aware, PUBLISH_TOKEN, no run:-block interpolation → Task 2 helpers. ✓
- Tests (resolver, reconciler, transitions, regression) → Tasks 1,2,7. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; every command has expected output. ✓

**Type consistency:** `phase_label_text(protocol_dict, key)`, `ensure_phase_label(dir_, pid, instance, protocol_dict, pr, head_key)`, `apply_setup_label(protocol_dict, pr)`, `remove_pr_label(pr, label)` — names and arg orders are identical across the resolver definition (Task 1/2) and all call sites (Tasks 3-5). All callers pass the protocol **dict** (`proto_data` in next.py, `proto` in advance.py, `protocol` in join.py), matching the resolver's `state_by_id(protocol, ...)` usage. ✓
