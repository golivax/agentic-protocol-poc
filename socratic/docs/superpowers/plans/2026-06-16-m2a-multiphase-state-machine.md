# Milestone 2a — Cursor-Based Multi-Phase State Machine + Conclude/Publish Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the engine from two hardcoded topologies (single-agent, fan-out) into a cursor-driven state machine that runs a *sequence* of heterogeneous phases (e.g. an agent gate → a fan-out → a join), where an agent phase's `conclude` verdict gates progression to the next phase — while keeping `grumpy-review` and `multi-grumpy` byte-identical.

**Architecture:** A protocol is **multi-phase** iff it has more than one state of kind `agent`|`fanout`. Multi-phase instances gain a `phase` cursor in `_instance.yaml` and phase-prefixed state files; single-phase protocols keep today's exact paths and code paths. `next.py` seeds+dispatches the phase named by the cursor; `advance.py` runs an optional `conclude` hook (the decide→act seam) at an agent phase's completion and, on a non-blocking conclusion, advances the cursor and fires a `protocol-advance` dispatch to launch the next phase; `join.py` advances the cursor past a completed fan-out phase instead of always ending. All new behavior is gated behind the multi-phase check, so the existing regression suite stays green.

**Tech Stack:** Python 3 + PyYAML (runtime); pytest (dev-only). No new dependencies. Engine scripts communicate by writing YAML state to a git "state branch" and printing action JSON / firing `gh api` dispatches (no-op'd under `ENGINE_LOCAL=1`).

**Scope note:** This is **M2a** of the spec at `docs/superpowers/specs/2026-06-16-code-review-pipeline-design.md`. It is the engine half of Milestone 2 and is fully pytest-testable via a fixture pipeline — no GitHub Actions needed. **M2b** (the generic reusable orchestrator YAML, `triggers` block, trigger shim, rename) is a separate plan written after M2a lands, because it is verified by `actionlint`/review/live-run rather than pytest and depends on the dispatch-event names and state-file paths this plan finalizes. M1 (DECIDE + `on_fail` severities + `lib.decide`) is already merged and is a dependency.

---

## Key contracts finalized by this milestone

**Env vars driving the engine scripts (additive — empty = today's behavior):**
- `BRANCH` (existing) — the branch leg within a fan-out phase.
- `PHASE` (new) — the current phase (a state id) for multi-phase protocols. Empty for single-phase protocols, preserving every existing code path.

**The phase cursor** lives in `_instance.yaml`: `phase: <state-id>` is the source of truth for which phase is active. It also keeps the existing `head_sha` and `joined` keys.

**`conclude` hook (new, optional per agent state) — the decide→act seam:**
- Invoked: `<hook> <evidence.json> <instance-key>`, with env `BLOCKING` (`"1"` if `lib.decide()` reported a `block`-severity check failed, else `"0"`) plus the usual trusted-zone env.
- Prints one JSON object: `{"conclusion": <str>, "summary": <str>, "blocked": <bool>}`.
- The engine uses `blocked` to gate (with `on_blocked`) and uses `conclusion`/`summary` for the phase check-run. A state with no `conclude` field behaves exactly as today (the `publish` hook alone provides `{conclusion,summary}`).

**`publish` hook (unchanged ABI):** `<hook> <evidence.json> <instance-key>` → `{"conclusion","summary"}`, trusted zone 4. Runs for side effects. Still resolved/run as today. (No `payload` plumbing — YAGNI; `publish` reads evidence directly.)

**New dispatch event `protocol-advance`** (fired by `advance.py`, no-op under `ENGINE_LOCAL`): `event_type=protocol-advance`, `client_payload{protocol,instance,phase}` where `phase` is the phase to launch next. The orchestrator (M2b) maps it to a `next.py` call with command `advance-phase`.

**New `next.py` command `advance-phase`:** seed + dispatch the phase named by the cursor (which `advance.py` already set). Reuses the same seeding code as a fresh `start`.

---

## File Structure

**Modified (engine):**
- `.github/agent-factory/engine/lib.py` — add phase arg to `state_file()`; add pure protocol-introspection helpers (`is_multiphase`, `phase_states`, `next_phase_id`, `state_by_id`).
- `.github/agent-factory/engine/next.py` — multi-phase seeding+dispatch driven by the cursor; new `advance-phase` command; single-phase paths untouched.
- `.github/agent-factory/engine/advance.py` — optional `conclude` hook; agent-phase completion advances the cursor + fires `protocol-advance` (gated by `conclude.blocked` + `on_blocked`); fan-out phase writes phase-prefixed branch files.
- `.github/agent-factory/engine/join.py` — on a completed fan-out *phase*, advance the cursor to `next` (and dispatch it) instead of always ending; pipeline aggregate check-run.

**Created (test fixtures — a self-contained mini pipeline):**
- `tests/fixtures/pipeline-mini/protocol.json`
- `tests/fixtures/pipeline-mini/checks/always-pass.py`
- `tests/fixtures/pipeline-mini/publish/conclude-gate.py`
- `tests/fixtures/pipeline-mini/publish/publish-gate.py`
- `tests/fixtures/pipeline-mini/publish/publish-alpha.py`
- `tests/fixtures/pipeline-mini/gate.evidence.schema.json`, `alpha.evidence.schema.json` (minimal; presence only)

**Created (tests):**
- `tests/test_multiphase.py` — unit tests for lib helpers + next.py/advance.py/join.py multi-phase behavior + the e2e walk.

**Regression anchors (run unchanged, must stay green):**
- `tests/test_engine.py`, `tests/test_fanout_e2e.py`, `tests/test_runchecks.py`, `tests/test_publish.py`, `tests/test_join.py`, `tests/test_decide.py`.

---

## Task 1: lib — phase-aware paths + protocol introspection

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (extend `state_file`; add four helpers near `protocol_id`/`state_file`, ~lines 33-53)
- Test: `tests/test_multiphase.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_multiphase.py` with this header + the lib unit tests:

```python
"""M2a — multi-phase state machine tests.

lib helpers are pure; the next/advance/join tests drive the engine scripts in
ENGINE_LOCAL mode against a self-contained fixture protocol (tests/fixtures/
pipeline-mini): a `gate` agent phase → a single-branch `work` fan-out → `join`.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
PROTOCOLS = ROOT / ".github/agent-factory/protocols"
FIXTURES = ROOT / "tests/fixtures"
MINI = FIXTURES / "pipeline-mini/protocol.json"
GRUMPY = PROTOCOLS / "grumpy/protocol.json"
MULTI = PROTOCOLS / "multi-grumpy/protocol.json"

sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def load(path):
    with open(path) as f:
        return json.load(f)


# --- lib.state_file phase arg ---

def test_state_file_legacy_single_agent():
    assert lib.state_file("/d", "p", "pr-1") == "/d/p/pr-1.yaml"


def test_state_file_legacy_fanout_branch():
    assert lib.state_file("/d", "p", "pr-1", branch="g") == "/d/p/pr-1/g.yaml"


def test_state_file_multiphase_agent():
    assert lib.state_file("/d", "p", "pr-1", phase="gate") == "/d/p/pr-1/gate.yaml"


def test_state_file_multiphase_fanout_branch():
    assert lib.state_file("/d", "p", "pr-1", branch="g", phase="work") == "/d/p/pr-1/work.g.yaml"


# --- protocol introspection ---

def test_is_multiphase_grumpy_false():
    assert lib.is_multiphase(load(GRUMPY)) is False


def test_is_multiphase_multigrumpy_false():
    assert lib.is_multiphase(load(MULTI)) is False


def test_is_multiphase_pipeline_true():
    assert lib.is_multiphase(load(MINI)) is True


def test_phase_states_are_agent_and_fanout_in_order():
    ids = [s["id"] for s in lib.phase_states(load(MINI))]
    assert ids == ["gate", "work"]


def test_next_phase_id_follows_next():
    assert lib.next_phase_id(load(MINI), "gate") == "work"


def test_next_phase_id_terminal_is_none():
    # `work`.next is "join", a join state — not another phase → None
    assert lib.next_phase_id(load(MINI), "work") is None


def test_state_by_id():
    assert lib.state_by_id(load(MINI), "join")["kind"] == "join"
    assert lib.state_by_id(load(MINI), "missing") is None
```

> This step also requires the fixture `tests/fixtures/pipeline-mini/protocol.json` to exist for the `MINI` tests. It is created in Task 2. To keep Task 1 runnable in isolation, create ONLY the protocol.json now (the checks/publish stubs come in Task 2). Create `tests/fixtures/pipeline-mini/protocol.json`:

```json
{
  "name": "pipeline-mini",
  "states": [
    { "id": "gate", "kind": "agent", "workflow": "gate-agent",
      "evidence": "gate.evidence.schema.json", "max_iterations": 2,
      "checks": [ { "run": "always-pass", "on_fail": "iterate" } ],
      "conclude": "conclude-gate", "publish": "publish-gate",
      "on_blocked": "halt", "next": "work" },
    { "id": "work", "kind": "fanout",
      "branches": [
        { "id": "alpha", "workflow": "alpha-agent",
          "evidence": "alpha.evidence.schema.json", "max_iterations": 2,
          "checks": [ { "run": "always-pass", "on_fail": "iterate" } ],
          "publish": "publish-alpha" }
      ],
      "next": "join" },
    { "id": "join", "kind": "join", "of": "work", "next": "done" }
  ]
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/gustavo/huawei/agent-factory/poc && pytest tests/test_multiphase.py -q`
Expected: FAIL — `state_file()` rejects the `phase=` kwarg / `lib` has no `is_multiphase`.

- [ ] **Step 3: Extend `state_file` and add the introspection helpers in `lib.py`**

Replace the existing `state_file` (lines 39-47) with:

```python
def state_file(d, pid, instance, branch=None, phase=None):
    """
    state_file <dir> <protocol-id> <instance-key> [branch] [phase]
      no branch, no phase → single-agent path     <dir>/<pid>/<instance>.yaml
      branch, no phase    → fan-out per-branch     <dir>/<pid>/<instance>/<branch>.yaml
      phase, no branch    → multi-phase agent      <dir>/<pid>/<instance>/<phase>.yaml
      phase + branch      → multi-phase fan-out leg <dir>/<pid>/<instance>/<phase>.<branch>.yaml
    """
    if phase and branch:
        return f"{d}/{pid}/{instance}/{phase}.{branch}.yaml"
    if phase:
        return f"{d}/{pid}/{instance}/{phase}.yaml"
    if branch:
        return f"{d}/{pid}/{instance}/{branch}.yaml"
    return f"{d}/{pid}/{instance}.yaml"
```

Then add these four pure helpers immediately after `state_file` (before `instance_file`):

```python
def state_by_id(protocol, state_id):
    """Return the state dict with the given id, or None."""
    for s in protocol.get("states", []):
        if s.get("id") == state_id:
            return s
    return None


def phase_states(protocol):
    """The ordered list of 'phase' states — those of kind agent or fanout.
    (join/deterministic states are transitions/terminals, not phases.)"""
    return [s for s in protocol.get("states", []) if s.get("kind") in ("agent", "fanout")]


def is_multiphase(protocol):
    """A protocol is multi-phase iff it has more than one agent|fanout phase.
    Single-phase protocols (grumpy=1 agent, multi-grumpy=1 fanout) keep the
    legacy layout + code paths untouched."""
    return len(phase_states(protocol)) > 1


def next_phase_id(protocol, phase_id):
    """The next PHASE (agent|fanout state) reached by following `.next` from
    phase_id. Returns None if `.next` is absent or is not itself a phase
    (e.g. a join or a terminal) — i.e. there is no further phase to launch."""
    cur = state_by_id(protocol, phase_id)
    if not cur:
        return None
    nxt = cur.get("next")
    nxt_state = state_by_id(protocol, nxt) if nxt else None
    if nxt_state and nxt_state.get("kind") in ("agent", "fanout"):
        return nxt
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_multiphase.py -q`
Expected: PASS (12 tests).

- [ ] **Step 5: Run the full suite (state_file signature change is the risk)**

Run: `pytest tests/ -q`
Expected: PASS — the added `phase=None` kwarg is backward-compatible, so `test_engine.py`/`test_fanout_e2e.py`/`test_join.py` (which call `state_file` with 3-4 positional args) are unaffected. Count: prior 168 + 12 = **180 passed**.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_multiphase.py tests/fixtures/pipeline-mini/protocol.json
git commit -m "feat(engine): phase-aware state_file + multiphase protocol introspection

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: the mini-pipeline fixture (stubs)

**Files:**
- Create: `tests/fixtures/pipeline-mini/checks/always-pass.py`
- Create: `tests/fixtures/pipeline-mini/publish/conclude-gate.py`
- Create: `tests/fixtures/pipeline-mini/publish/publish-gate.py`
- Create: `tests/fixtures/pipeline-mini/publish/publish-alpha.py`
- Create: `tests/fixtures/pipeline-mini/gate.evidence.schema.json`, `tests/fixtures/pipeline-mini/alpha.evidence.schema.json`
- Test: `tests/test_multiphase.py` (add fixture-sanity tests)

These stubs honor the engine ABIs so the engine scripts can resolve+run them under `ENGINE_LOCAL`. The `conclude-gate` stub is the test's control knob for the gate verdict: it reports `blocked` when the evidence JSON contains `{"gate": "blocked"}` OR the `BLOCKING` env var is `"1"`.

- [ ] **Step 1: Write the failing fixture-sanity tests**

Append to `tests/test_multiphase.py`:

```python
# --- fixture stub sanity (the engine resolves+runs these) ---

MINI_DIR = FIXTURES / "pipeline-mini"


def _run(path, *args, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([str(path), *args], text=True, capture_output=True, env=env)


def test_always_pass_check_abi():
    r = _run(MINI_DIR / "checks/always-pass.py", "ev", "diff", "files")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out == {"check": "always-pass", "pass": True, "feedback": ""}


def test_conclude_gate_clear_by_default(tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"gate": "clear"}))
    r = _run(MINI_DIR / "publish/conclude-gate.py", str(ev), "pr-1",
             env_extra={"BLOCKING": "0", "ENGINE_LOCAL": "1"})
    out = json.loads(r.stdout)
    assert out["blocked"] is False and out["conclusion"] and out["summary"]


def test_conclude_gate_blocked_by_evidence(tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"gate": "blocked"}))
    r = _run(MINI_DIR / "publish/conclude-gate.py", str(ev), "pr-1",
             env_extra={"BLOCKING": "0", "ENGINE_LOCAL": "1"})
    assert json.loads(r.stdout)["blocked"] is True


def test_conclude_gate_blocked_by_env(tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"gate": "clear"}))
    r = _run(MINI_DIR / "publish/conclude-gate.py", str(ev), "pr-1",
             env_extra={"BLOCKING": "1", "ENGINE_LOCAL": "1"})
    assert json.loads(r.stdout)["blocked"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_multiphase.py -k "always_pass or conclude_gate" -q`
Expected: FAIL — the stub files don't exist yet (FileNotFoundError / non-zero).

- [ ] **Step 3: Create the check stub**

`tests/fixtures/pipeline-mini/checks/always-pass.py`:

```python
#!/usr/bin/env python3
"""Mini-pipeline test check: always passes. Honors the check ABI."""
import json
print(json.dumps({"check": "always-pass", "pass": True, "feedback": ""}))
```

- [ ] **Step 4: Create the conclude/publish stubs**

`tests/fixtures/pipeline-mini/publish/conclude-gate.py`:

```python
#!/usr/bin/env python3
"""Mini-pipeline gate conclude hook (the decide->act seam, decide half).

ABI: <hook> <evidence.json> <instance-key>; env BLOCKING in {"0","1"}.
Prints {"conclusion","summary","blocked"}. Blocked iff BLOCKING==1 OR the
evidence carries {"gate":"blocked"} — this is the test's control knob.
"""
import json
import os
import sys

blocked = os.environ.get("BLOCKING", "0") == "1"
try:
    with open(sys.argv[1]) as f:
        ev = json.load(f)
    if isinstance(ev, dict) and ev.get("gate") == "blocked":
        blocked = True
except (OSError, ValueError, IndexError):
    pass

if blocked:
    print(json.dumps({"conclusion": "blocked", "summary": "gate blocked", "blocked": True}))
else:
    print(json.dumps({"conclusion": "clear", "summary": "gate clear", "blocked": False}))
```

`tests/fixtures/pipeline-mini/publish/publish-gate.py`:

```python
#!/usr/bin/env python3
"""Mini-pipeline gate publish hook (side-effects half). No-op echo for tests.
ABI: <hook> <evidence.json> <instance-key> -> {"conclusion","summary"}."""
import json
print(json.dumps({"conclusion": "neutral", "summary": "gate published"}))
```

`tests/fixtures/pipeline-mini/publish/publish-alpha.py`:

```python
#!/usr/bin/env python3
"""Mini-pipeline fan-out branch publish hook. No-op echo for tests.
ABI: <hook> <evidence.json> <instance-key> -> {"conclusion","summary"}."""
import json
print(json.dumps({"conclusion": "success", "summary": "alpha published"}))
```

Make all four executable:
```bash
chmod +x tests/fixtures/pipeline-mini/checks/always-pass.py \
         tests/fixtures/pipeline-mini/publish/conclude-gate.py \
         tests/fixtures/pipeline-mini/publish/publish-gate.py \
         tests/fixtures/pipeline-mini/publish/publish-alpha.py
```

- [ ] **Step 5: Create the minimal evidence schemas** (presence-only; the engine never parses them under test, but resolution/realism expects them):

`tests/fixtures/pipeline-mini/gate.evidence.schema.json`:
```json
{ "type": "object" }
```

`tests/fixtures/pipeline-mini/alpha.evidence.schema.json`:
```json
{ "type": "object" }
```

- [ ] **Step 6: Run the fixture-sanity tests**

Run: `pytest tests/test_multiphase.py -k "always_pass or conclude_gate" -q`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/pipeline-mini tests/test_multiphase.py
git commit -m "test(engine): mini multi-phase fixture protocol + ABI stubs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: next.py — multi-phase initial seed + dispatch

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (the fan-out guard block, ~lines 96-106, and `start_fanout`/`start_fresh`)
- Test: `tests/test_multiphase.py` (add next.py start tests)

**Design:** Introduce `seed_and_dispatch_phase(phase_id, command)` that seeds the named phase's state (agent → one phase state file; fan-out → per-branch phase files) + the `_instance.yaml` cursor (`phase=phase_id`), CAS-pushes, and emits the matching action (`run-agent` with a `phase` field for an agent phase; `run-fanout` for a fan-out phase). On a multi-phase `start`/`reset`, call it with the FIRST phase. Single-phase protocols never enter this branch (guarded by `lib.is_multiphase`), so their behavior is unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_multiphase.py` (uses the `run_engine`/`read_state_yaml` style but the engine scripts are invoked directly here so the cursor file can be inspected). Add a helper and tests:

```python
from conftest import state_origin, engine_env  # noqa: F401  (fixtures)


def run_next(work_dir, instance, proto, command, env, phase="", branch="", head=""):
    e = dict(env)
    e["PHASE"] = phase
    e["BRANCH"] = branch
    r = subprocess.run(
        ["python3", str(ENGINE / "next.py"), str(work_dir), instance, str(proto), command, head],
        text=True, capture_output=True, env=e,
    )
    return r


def test_multiphase_start_seeds_cursor_at_first_phase(tmp_path, engine_env):
    work = tmp_path / "state"
    r = run_next(work, "pr-1", MINI, "start", engine_env, head="abc")
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    # gate is an agent phase → run-agent with the phase named
    assert action["action"] == "run-agent"
    assert action["phase"] == "gate"
    # cursor seeded
    inst = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/_instance.yaml")
    assert inst["phase"] == "gate"
    assert inst["head_sha"] == "abc"
    assert inst["joined"] is False
    # the gate phase state file exists with state=gate, iteration 1
    gate = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/gate.yaml")
    assert gate["state"] == "gate" and gate["iteration"] == 1


def test_multiphase_start_does_not_seed_later_phases(tmp_path, engine_env):
    work = tmp_path / "state"
    run_next(work, "pr-1", MINI, "start", engine_env, head="abc")
    assert not os.path.exists(str(work) + "/pipeline-mini/pr-1/work.alpha.yaml")
```

Also add regression assertions that single-phase start is unchanged:

```python
def test_singlephase_grumpy_start_unchanged(tmp_path, engine_env):
    work = tmp_path / "state"
    r = run_next(work, "pr-1", GRUMPY, "start", engine_env, head="abc")
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-agent"
    assert "phase" not in action            # single-phase emits no phase field
    assert os.path.exists(str(work) + "/grumpy-review/pr-1.yaml")  # legacy path


def test_singlephase_multigrumpy_start_unchanged(tmp_path, engine_env):
    work = tmp_path / "state"
    r = run_next(work, "pr-1", MULTI, "start", engine_env, head="abc")
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-fanout"   # legacy fan-out start
    assert os.path.exists(str(work) + "/multi-grumpy/pr-1/_instance.yaml")
    assert os.path.exists(str(work) + "/multi-grumpy/pr-1/grumpy.yaml")  # legacy branch path
```

- [ ] **Step 2: Run to verify the new multiphase tests fail (and the regression ones pass)**

Run: `pytest tests/test_multiphase.py -k "multiphase_start or singlephase" -q`
Expected: the two `singlephase_*` tests PASS (current behavior already correct); the two `multiphase_start_*` tests FAIL (today `next.py` treats the pipeline as a plain fan-out via `is_fanout()` and jumps to seeding the `work` fan-out, emitting `run-fanout` with no cursor at `gate`).

- [ ] **Step 3: Add the multi-phase seeding function and route to it in `next.py`**

In `.github/agent-factory/engine/next.py`, after the existing `import lib` and the top-level arg parsing (after `BRANCH = os.environ.get("BRANCH", "")`, ~line 24), add:

```python
PHASE = os.environ.get("PHASE", "")
```

Add this function near `start_fanout` (after `start_fanout`, ~line 90). It generalizes seeding to an arbitrary phase:

```python
def seed_and_dispatch_phase(phase_id, command):
    """Multi-phase: seed the named phase's state + the instance cursor, push,
    and emit the phase's run action. Used for the first phase (start/reset) and
    for each subsequent phase (advance-phase)."""
    phase_state = lib.state_by_id(proto_data, phase_id)
    kind = phase_state.get("kind")
    inf = lib.instance_file(DIR, PID, INSTANCE)
    os.makedirs(os.path.dirname(inf), exist_ok=True)

    # Upsert the cursor (preserve head_sha/joined across phase transitions).
    inst = lib.load_yaml(inf) if os.path.isfile(inf) else {}
    inst.setdefault("protocol", PID)
    inst.setdefault("instance", INSTANCE)
    inst["phase"] = phase_id
    if HEAD_SHA:
        inst.setdefault("head_sha", HEAD_SHA)
    inst.setdefault("joined", False)
    lib.dump_yaml(inf, inst)

    if kind == "fanout":
        branches_config = phase_state.get("branches", [])
        for b in branches_config:
            sf = lib.state_file(DIR, PID, INSTANCE, b["id"], phase=phase_id)
            os.makedirs(os.path.dirname(sf), exist_ok=True)
            lib.dump_yaml(sf, {
                "protocol": PID, "instance": INSTANCE, "state": phase_id,
                "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": [],
            })
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter fan-out phase {phase_id} ({command})")
        branches = [{"id": b["id"], "workflow": b["workflow"], "iteration": 1, "feedback": ""}
                    for b in branches_config]
        print(json.dumps({"action": "run-fanout", "iteration": 1, "feedback": "",
                          "reason": f"phase:{phase_id}", "phase": phase_id, "branches": branches}))
    else:  # agent phase
        sf = lib.state_file(DIR, PID, INSTANCE, phase=phase_id)
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        lib.dump_yaml(sf, {
            "protocol": PID, "instance": INSTANCE, "state": phase_id,
            "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": [],
        })
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter agent phase {phase_id} ({command})")
        print(json.dumps({"action": "run-agent", "iteration": 1, "feedback": "",
                          "reason": f"phase:{phase_id}", "phase": phase_id}))
```

Now route multi-phase starts to it. Find the existing guard (lines 96-106):

```python
if not BRANCH and is_fanout():
    if COMMAND in ("start", "reset"):
        start_fanout()
        sys.exit(0)
    elif COMMAND == "continue":
        sys.stderr.write("[next] fanout 'continue' requires a BRANCH\n")
        sys.exit(2)
    else:
        sys.stderr.write(f"[next] unknown command: {COMMAND}\n")
        sys.exit(2)
```

Replace it with a multi-phase-first guard (multi-phase routing takes precedence; the single-fan-out path is preserved for `multi-grumpy`):

```python
if lib.is_multiphase(proto_data) and not PHASE and not BRANCH:
    # Multi-phase protocol, unbranched/unphased entry → seed the FIRST phase.
    if COMMAND in ("start", "reset"):
        first = lib.phase_states(proto_data)[0]["id"]
        seed_and_dispatch_phase(first, COMMAND)
        sys.exit(0)
    else:
        sys.stderr.write(f"[next] multi-phase '{COMMAND}' needs a PHASE\n")
        sys.exit(2)

if lib.is_multiphase(proto_data) and PHASE and COMMAND == "advance-phase":
    # Phase transition (advance.py already set the cursor to PHASE) → seed+dispatch it.
    seed_and_dispatch_phase(PHASE, COMMAND)
    sys.exit(0)

if not BRANCH and is_fanout() and not PHASE:
    if COMMAND in ("start", "reset"):
        start_fanout()
        sys.exit(0)
    elif COMMAND == "continue":
        sys.stderr.write("[next] fanout 'continue' requires a BRANCH\n")
        sys.exit(2)
    else:
        sys.stderr.write(f"[next] unknown command: {COMMAND}\n")
        sys.exit(2)
```

> Note: `is_multiphase` short-circuits BEFORE the single-fan-out guard, so `multi-grumpy` (not multi-phase) still takes the `is_fanout()` branch unchanged, and `grumpy` (neither) falls through to the existing single-agent path below — both byte-identical.

- [ ] **Step 4: Run the new tests + regression**

Run: `pytest tests/test_multiphase.py -k "multiphase_start or singlephase" -q` → PASS (4).
Run: `pytest tests/ -q` → PASS. Count: **184 passed** (180 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_multiphase.py
git commit -m "feat(engine): next.py seeds + dispatches multi-phase by cursor

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: next.py — continue (iterate) within a phase

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (the agent-unit/lifecycle block, ~lines 110-226)
- Test: `tests/test_multiphase.py`

**Design:** When `PHASE` is set with `command=continue` (the iterate loop within a phase), the existing single-agent lifecycle logic must operate on the phase-prefixed state file and read its agent-unit (max_iterations) from the phase (agent phase) or branch (fan-out phase). The existing logic already keys the agent unit on `BRANCH`; extend it to also honor `PHASE`. We make the state-file path and the agent-unit/LIFE_STATE resolution phase-aware, leaving the single-phase (`PHASE==""`) path identical.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_multiphase.py`:

```python
def test_phase_continue_resumes_gate(tmp_path, engine_env):
    work = tmp_path / "state"
    # seed the gate phase
    run_next(work, "pr-1", MINI, "start", engine_env, head="abc")
    # simulate a failed-iteration state: bump gate.yaml to iteration 2 w/ feedback
    sf = str(work) + "/pipeline-mini/pr-1/gate.yaml"
    data = lib.load_yaml(sf)
    data["iteration"] = 2
    data["history"] = [{"iteration": 1, "feedback": "fix the rubric"}]
    lib.dump_yaml(sf, data)
    # continue within the gate phase
    r = run_next(work, "pr-1", MINI, "continue", engine_env, phase="gate")
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-agent"
    assert action["iteration"] == 2
    assert action["feedback"] == "fix the rubric"
    assert action["phase"] == "gate"


def test_phase_continue_terminal_halts(tmp_path, engine_env):
    work = tmp_path / "state"
    run_next(work, "pr-1", MINI, "start", engine_env, head="abc")
    sf = str(work) + "/pipeline-mini/pr-1/gate.yaml"
    data = lib.load_yaml(sf)
    data["state"] = "done"      # phase already terminal
    lib.dump_yaml(sf, data)
    r = run_next(work, "pr-1", MINI, "continue", engine_env, phase="gate")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["action"] == "halt"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_multiphase.py -k "phase_continue" -q`
Expected: FAIL — without `PHASE` handling, `next.py` resolves the legacy single-agent state file (`pipeline-mini/pr-1.yaml`, which doesn't exist) and/or can't find the agent unit, so the action is wrong (`start_fresh`/error) rather than a phase-aware resume.

- [ ] **Step 3: Make the agent-unit, LIFE_STATE, and state-file resolution phase-aware**

In `next.py`, the block that resolves `AGENT_STATE`/`MAX` (currently `if BRANCH: ... else: ...`, ~lines 110-133). Extend it so a `PHASE` agent phase resolves its unit from the phase state, and a `PHASE`+`BRANCH` fan-out leg resolves from the branch. Replace that block with:

```python
if PHASE:
    phase_state = lib.state_by_id(proto_data, PHASE)
    if not phase_state:
        sys.stderr.write(f"[next] no phase '{PHASE}' in protocol\n")
        sys.exit(1)
    if phase_state.get("kind") == "fanout":
        # a fan-out leg within a phase → agent unit is the branch
        AGENT_STATE = None
        MAX = None
        for b in phase_state.get("branches", []):
            if b["id"] == BRANCH:
                AGENT_STATE = b["id"]
                MAX = b.get("max_iterations")
                break
        if not AGENT_STATE:
            sys.stderr.write(f"[next] no branch '{BRANCH}' in phase '{PHASE}'\n")
            sys.exit(1)
    else:
        AGENT_STATE = PHASE
        MAX = phase_state.get("max_iterations")
elif BRANCH:
    AGENT_STATE = None
    MAX = None
    for s in proto_data.get("states", []):
        if s.get("kind") == "fanout":
            for b in s.get("branches", []):
                if b["id"] == BRANCH:
                    AGENT_STATE = b["id"]
                    MAX = b.get("max_iterations")
                    break
    if not AGENT_STATE:
        sys.stderr.write(f"[engine] no branch '{BRANCH}' in protocol\n")
        sys.exit(1)
else:
    AGENT_STATE = None
    MAX = None
    for s in proto_data.get("states", []):
        if s.get("kind") == "agent":
            AGENT_STATE = s["id"]
            MAX = s.get("max_iterations")
            break
    if not AGENT_STATE:
        sys.stderr.write("[engine] protocol has no agent state\n")
        sys.exit(1)
```

Next, `LIFE_STATE` (currently `if BRANCH: ... else: LIFE_STATE = AGENT_STATE`, ~lines 145-152). For a phase, the live state value stamped in the phase's state file is the PHASE id (see Task 3's seeding, which writes `state: <phase_id>`). Replace with:

```python
if PHASE:
    LIFE_STATE = PHASE
elif BRANCH:
    LIFE_STATE = None
    for s in proto_data.get("states", []):
        if s.get("kind") == "fanout":
            LIFE_STATE = s["id"]
            break
else:
    LIFE_STATE = AGENT_STATE
```

Finally, the state-file path (currently `SF = lib.state_file(DIR, PID, INSTANCE, BRANCH if BRANCH else None)`, ~line 155). Make it phase-aware:

```python
SF = lib.state_file(DIR, PID, INSTANCE,
                    branch=(BRANCH if BRANCH else None),
                    phase=(PHASE if PHASE else None))
```

The rest of the lifecycle/continue logic (`emit_run_agent`, `start_fresh`, the `if COMMAND == ...` ladder) is unchanged — but `emit_run_agent` must include the phase when set. Update `emit_run_agent` (currently prints `{action,iteration,feedback,reason}`) to:

```python
def emit_run_agent(iteration, feedback, reason):
    action = {"action": "run-agent", "iteration": iteration, "feedback": feedback, "reason": reason}
    if PHASE:
        action["phase"] = PHASE
    print(json.dumps(action))
```

> The single-phase path (`PHASE==""`) produces the identical JSON as before (no `phase` key), preserving byte-identical behavior for grumpy and for multi-grumpy branch continues.

- [ ] **Step 4: Run the new tests + regression**

Run: `pytest tests/test_multiphase.py -k "phase_continue" -q` → PASS (2).
Run: `pytest tests/ -q` → PASS. Count: **186 passed**.

> If any `test_engine.py` next.py test fails, the `PHASE`/`BRANCH`/`LIFE_STATE` resolution changed single-phase output — STOP and reconcile (the `else` branches must be byte-identical to the originals).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_multiphase.py
git commit -m "feat(engine): next.py resumes the iterate loop within a phase

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: advance.py — conclude seam + agent-phase gate transition

**Files:**
- Modify: `.github/agent-factory/engine/advance.py` (`run_publish_hook` area + the `main()` resolution and the `all_pass`/done branch)
- Test: `tests/test_multiphase.py`

**Design:** Add `PHASE` handling parallel to `BRANCH`. When advancing an **agent phase** that reached `done`:
1. Compute `blocking` from `lib.decide()` (already returned; currently discarded as `_blocking`).
2. Resolve+run the optional `conclude` hook with env `BLOCKING`; read its `{conclusion,summary,blocked}`.
3. Run the `publish` hook (if any) for side effects (unchanged).
4. Set the phase sub check-run from the conclusion.
5. If `blocked` and the phase's `on_blocked == "halt"`: mark the phase `failed`, set the aggregate check-run to `failure`, CAS-push, STOP (no transition).
6. Else: advance the cursor (`_instance.yaml.phase = next_phase_id`), CAS-push, and fire `protocol-advance` with that phase. If there is no next phase, fall back to today's terminal behavior.

Single-phase protocols (`PHASE==""`, no `conclude`) keep today's exact done-branch behavior.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_multiphase.py`. These drive `advance.py` directly (like `test_engine.py`'s `run_advance`). Add a helper:

```python
def run_advance(work_dir, instance, proto, verdicts_path, evidence_path, env,
                phase="", branch=""):
    e = dict(env)
    e["PHASE"] = phase
    e["BRANCH"] = branch
    r = subprocess.run(
        ["python3", str(ENGINE / "advance.py"), str(work_dir), instance, str(proto),
         str(verdicts_path), str(evidence_path)],
        text=True, capture_output=True, env=e,
    )
    return r


def _seed_gate(work, engine_env):
    run_next(work, "pr-1", MINI, "start", engine_env, head="abc")


def test_agent_phase_clear_advances_cursor_and_fires_protocol_advance(tmp_path, engine_env):
    work = tmp_path / "state"
    _seed_gate(work, engine_env)
    verdicts = tmp_path / "v.json"
    verdicts.write_text(json.dumps({"results": [{"check": "always-pass", "pass": True,
                                                 "feedback": "", "on_fail": "iterate"}]}))
    evidence = tmp_path / "ev.json"
    evidence.write_text(json.dumps({"gate": "clear"}))
    r = run_advance(work, "pr-1", MINI, verdicts, evidence, engine_env, phase="gate")
    assert r.returncode == 0, r.stderr
    # cursor advanced to the next phase
    inst = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/_instance.yaml")
    assert inst["phase"] == "work"
    # gate phase marked done
    gate = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/gate.yaml")
    assert gate["state"] == "done"
    # protocol-advance dispatch intent emitted (ENGINE_LOCAL echoes gh api to stderr)
    assert "protocol-advance" in r.stderr
    assert "phase]=work" in r.stderr or "phase=work" in r.stderr


def test_agent_phase_blocked_halts_pipeline(tmp_path, engine_env):
    work = tmp_path / "state"
    _seed_gate(work, engine_env)
    verdicts = tmp_path / "v.json"
    verdicts.write_text(json.dumps({"results": [{"check": "always-pass", "pass": True,
                                                 "feedback": "", "on_fail": "iterate"}]}))
    evidence = tmp_path / "ev.json"
    evidence.write_text(json.dumps({"gate": "blocked"}))   # conclude-gate → blocked
    r = run_advance(work, "pr-1", MINI, verdicts, evidence, engine_env, phase="gate")
    assert r.returncode == 0, r.stderr
    gate = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/gate.yaml")
    assert gate["state"] == "failed"
    inst = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/_instance.yaml")
    assert inst["phase"] == "gate"           # cursor did NOT advance
    assert "protocol-advance" not in r.stderr  # no transition fired
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_multiphase.py -k "agent_phase" -q`
Expected: FAIL — `advance.py` ignores `PHASE`, resolves the legacy single-agent state file, never runs `conclude`, and never advances a cursor.

- [ ] **Step 3: Add phase resolution to `advance.py` `main()`**

In `advance.py` `main()`, the block resolving `agent_state`/`max_iter`/`life_state` (currently `if branch: ... else: ...`, ~lines 179-209). Add a `phase = os.environ.get("PHASE", "")` near the other env reads (~line 167), then prepend a `PHASE` branch:

```python
    phase = os.environ.get("PHASE", "")
```

Replace the `if branch:` / `else:` agent-unit block with:

```python
    if phase:
        phase_state = lib.state_by_id(proto, phase)
        if phase_state and phase_state.get("kind") == "fanout":
            # a fan-out leg within a phase
            agent_state = branch
            max_iter = None
            for b in phase_state.get("branches", []):
                if b["id"] == branch:
                    max_iter = b.get("max_iterations")
                    break
            life_state = phase
        else:
            agent_state = phase
            max_iter = phase_state.get("max_iterations") if phase_state else None
            life_state = phase
    elif branch:
        agent_state = branch
        max_iter = None
        for state in proto.get("states", []):
            if state.get("kind") == "fanout":
                for b in state.get("branches", []):
                    if b["id"] == branch:
                        max_iter = b.get("max_iterations")
                        break
                break
        life_state = None
        for state in proto.get("states", []):
            if state.get("kind") == "fanout":
                life_state = state["id"]
                break
    else:
        agent_state = None
        for state in proto.get("states", []):
            if state.get("kind") == "agent":
                agent_state = state["id"]
                break
        if not agent_state:
            sys.stderr.write("[engine] protocol has no agent state\n")
            sys.exit(1)
        max_iter = None
        for state in proto.get("states", []):
            if state.get("id") == agent_state:
                max_iter = state.get("max_iterations")
                break
        life_state = agent_state
```

Make the state-file + check-run name phase-aware (currently `sf = lib.state_file(dir_, pid, instance, branch)` and the `cr_name` block, ~lines 212-216):

```python
    sf = lib.state_file(dir_, pid, instance,
                        branch=(branch if branch else None),
                        phase=(phase if phase else None))
    if phase and branch:
        cr_name = f"{pid}/{phase}/{branch}"
    elif phase:
        cr_name = f"{pid}/{phase}"
    elif branch:
        cr_name = f"{pid}/{branch}"
    else:
        cr_name = pid
```

- [ ] **Step 4: Add a `conclude` resolver and the agent-phase transition**

Add a helper near `run_publish_hook` in `advance.py`:

```python
def run_conclude_hook(proto_path, proto, state_id, evid, instance, blocking):
    """Resolve+run the optional `conclude` hook for an agent state. Returns
    {conclusion,summary,blocked} or None if the state declares none.
    Trusted (zone 4). Receives BLOCKING via env."""
    state = lib.state_by_id(proto, state_id)
    action = (state or {}).get("conclude") or None
    if not action:
        return None
    pdir = os.path.dirname(os.path.abspath(proto_path))
    res = lib.resolve_executable(f"{pdir}/publish", action, pdir, "")
    kind, path = res.split("\t", 1)
    if kind == "ERR" or not os.access(path, os.X_OK):
        sys.stderr.write(f"[advance] conclude hook unresolved/not-exec: {path}\n")
        return {"conclusion": "neutral", "summary": "conclude hook unresolved", "blocked": False}
    env = dict(os.environ)
    env["BLOCKING"] = "1" if blocking else "0"
    result = subprocess.run([path, evid, instance], text=True,
                            stdout=subprocess.PIPE, env=env)
    try:
        parsed = json.loads(result.stdout.strip())
        if isinstance(parsed, dict) and "blocked" in parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return {"conclusion": "neutral", "summary": "conclude hook returned no verdict", "blocked": False}
```

Now wire the agent-phase transition into the `all_pass`/done branch. Currently (after M1) the done branch is `if process == "done":` and runs the publish hook, sets the check-run, updates the status comment, CAS-pushes, then `fire_join(...)`. We need: for an **agent phase** with a `conclude` hook, replace `fire_join` with the gate transition. The cleanest, lowest-risk shape: compute `blocking` (rename `_blocking` → `blocking` where decide is called) and, INSIDE the `process == "done"` branch, branch on whether this is an agent phase with a conclude hook.

First, change the decide call to keep `blocking`:
```python
    process, blocking = lib.decide(results, iterations_remaining=(iter_ < max_iter))
```

Then locate, inside `if process == "done":`, the lines that currently mark done + run publish + set check-run + comment + cas_push + `fire_join(pid, instance, branch)`. Wrap the terminal action so an agent phase gates instead of joining. Replace the body of the `process == "done"` branch with:

```python
        # Mark this phase/unit done.
        state_data = lib.load_yaml(sf)
        state_data["state"] = "done"
        lib.dump_yaml(sf, state_data)

        this_state = lib.state_by_id(proto, agent_state)
        is_agent_phase = phase and this_state and this_state.get("kind") == "agent"
        conclude = run_conclude_hook(proto_path, proto, agent_state, evid, instance, blocking) if is_agent_phase else None

        # Side-effects: run publish (unchanged ABI). Conclusion/summary come from
        # conclude when present, else from the publish hook (legacy behavior).
        hook = run_publish_hook(proto_path, proto, branch, agent_state, evid, instance, pid)
        if conclude is not None:
            concl_text = conclude.get("conclusion", "neutral")
            csum = conclude.get("summary", "")
        else:
            concl_text = hook.get("conclusion", "neutral")
            csum = hook.get("summary", "")

        if is_agent_phase and conclude is not None and conclude.get("blocked") and (this_state.get("on_blocked") == "halt"):
            # GATE BLOCKED → terminate the pipeline before the next phase.
            state_data = lib.load_yaml(sf)
            state_data["state"] = "failed"
            lib.dump_yaml(sf, state_data)
            lib.set_check_run(pid, sha, "completed", "failure", "Gate blocked",
                              csum or "A required gate did not pass; pipeline halted.")
            lib.set_check_run(cr_name, sha, "completed", "failure", "Gate blocked", csum)
            lib.cas_push(dir_, f"{instance}: phase {phase} blocked → pipeline halted")
        elif is_agent_phase:
            # GATE CLEAR → advance the cursor and launch the next phase.
            nxt = lib.next_phase_id(proto, agent_state)
            lib.set_check_run(cr_name, sha, "completed", "success" if concl_text != "blocked" else "failure",
                              "Gate complete", csum)
            inf2 = lib.instance_file(dir_, pid, instance)
            inst = lib.load_yaml(inf2) if os.path.isfile(inf2) else {}
            if nxt:
                inst["phase"] = nxt
                lib.dump_yaml(inf2, inst)
                lib.cas_push(dir_, f"{instance}: phase {phase} clear → advancing to {nxt}")
                gh_api(
                    f"repos/{github_repository}/dispatches",
                    "-f", "event_type=protocol-advance",
                    "-F", f"client_payload[protocol]={pid}",
                    "-F", f"client_payload[instance]={instance}",
                    "-F", f"client_payload[phase]={nxt}",
                )
            else:
                # No further phase (gate was the last phase) → terminal success.
                lib.set_check_run(pid, sha, "completed", "success", "Complete", csum)
                lib.cas_push(dir_, f"{instance}: phase {phase} clear → done (no further phase)")
        else:
            # Single-agent or fan-out leg → today's behavior unchanged.
            lib.set_check_run(cr_name, sha, "completed", concl_text, "Review complete", csum)
            update_status_comment(
                sf, inf, branch, pr, pid, instance, proto_path, dir_,
                "✅ done — published.", max_iter, github_repository)
            lib.cas_push(dir_, f"{instance}: checks passed at iteration {iter_} → published, done")
            fire_join(pid, instance, branch)
```

> Important: `gh_api`, `lib.set_check_run`, `update_status_comment`, `fire_join`, `run_publish_hook`, `sha`, `github_repository`, `inf`, `pr` are all already defined/in scope in `main()` (they are used by the existing branches). The `else` branch above is the original `process == "done"` body verbatim — confirm it byte-matches the pre-edit code so single-agent/fan-out is unchanged.

- [ ] **Step 5: Run the new tests + regression**

Run: `pytest tests/test_multiphase.py -k "agent_phase" -q` → PASS (2).
Run: `pytest tests/ -q` → PASS. Count: **188 passed**.

> If `test_engine.py`'s advance tests (single-agent done/publish/REQUEST_CHANGES) or `test_fanout_e2e.py` fail, the `else` branch diverged from the original — STOP and restore it byte-for-byte.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/advance.py tests/test_multiphase.py
git commit -m "feat(engine): conclude seam + agent-phase gate transition in advance

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: fan-out phase within a pipeline + join advances the cursor

**Files:**
- Modify: `.github/agent-factory/engine/join.py` (branch-state path + cursor advance on success)
- Test: `tests/test_multiphase.py`

**Design:** When the `work` fan-out phase completes, `advance.py` (the fan-out leg path, already phase-aware after Task 5) writes `work.alpha.yaml` and fires `protocol-join`. `join.py` must (a) read branch states from the phase-prefixed paths, and (b) on all-terminal-success, advance the cursor to the fan-out phase's `next_phase_id` (if any) and fire `protocol-advance`; if there is no further phase (the `work` phase's `.next` is `join`→`done`), finalize the pipeline as today (aggregate check-run + joined). For `multi-grumpy` (single-phase fan-out, `PHASE` unset), behavior is unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_multiphase.py`:

```python
def test_join_reads_phase_prefixed_branch_states_and_finalizes(tmp_path, engine_env):
    work = tmp_path / "state"
    # seed gate, advance to work phase manually by seeding the work fan-out
    run_next(work, "pr-1", MINI, "start", engine_env, head="abc")
    # advance cursor to work + seed the work phase (advance-phase path)
    run_next(work, "pr-1", MINI, "advance-phase", engine_env, phase="work")
    # mark the single branch leg done
    sf = str(work) + "/pipeline-mini/pr-1/work.alpha.yaml"
    data = lib.load_yaml(sf)
    data["state"] = "done"
    lib.dump_yaml(sf, data)
    # set the cursor to work (advance-phase already did, but be explicit)
    inf = str(work) + "/pipeline-mini/pr-1/_instance.yaml"
    inst = lib.load_yaml(inf); inst["phase"] = "work"; lib.dump_yaml(inf, inst)
    # run join
    e = dict(engine_env); e["PR"] = "1"; e["PR_HEAD_SHA"] = "abc"
    r = subprocess.run(["python3", str(ENGINE / "join.py"), str(work), "pr-1", str(MINI)],
                       text=True, capture_output=True, env=e)
    assert r.returncode == 0, r.stderr
    inst = lib.load_yaml(inf)
    # work is the last phase before join→done, so the pipeline finalizes (joined)
    assert inst["joined"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_multiphase.py -k "join_reads_phase" -q`
Expected: FAIL — `join.py` reads branch states from the legacy `work/alpha.yaml`-style path (no phase prefix), so it sees the `work.alpha` leg as missing → "not all terminal" → never joins.

- [ ] **Step 3: Make `join.py` phase-aware**

In `join.py`, after loading `instance_data` (it has the `phase` cursor for multi-phase), determine the active fan-out phase. Replace the branch-collection + state-file loop. Currently it finds the first `kind=="fanout"` state and reads `lib.state_file(dir_, pid, instance, b)`. Make it read the cursor's phase when multi-phase:

```python
    # Determine the fan-out phase to evaluate. Multi-phase: the cursor's phase.
    # Single-phase (multi-grumpy): the sole fan-out state (cursor absent).
    cursor_phase = instance_data.get("phase", "") or ""
    protocol_obj = None
    with open(proto) as f:
        protocol_obj = json.load(f)
    multiphase = lib.is_multiphase(protocol_obj)
    fanout_state = None
    if multiphase and cursor_phase:
        st = lib.state_by_id(protocol_obj, cursor_phase)
        if st and st.get("kind") == "fanout":
            fanout_state = st
    if fanout_state is None:
        for st in protocol_obj.get("states", []):
            if st.get("kind") == "fanout":
                fanout_state = st
                break

    branches = [b["id"] for b in (fanout_state.get("branches", []) if fanout_state else [])]
    phase_for_path = cursor_phase if (multiphase and cursor_phase) else None
```

Then change the per-branch state-file read to pass the phase:

```python
    for b in branches:
        sf = lib.state_file(dir_, pid, instance, b, phase=phase_for_path)
        ...
```

(Replace the existing `sf = lib.state_file(dir_, pid, instance, b)` inside the terminal-collection loop with the phase-aware call. The rest of the all_terminal/all_done logic is unchanged.)

Finally, after the existing finalize block (which sets the aggregate check-run and marks `joined`), add cursor-advance for a non-final fan-out phase. Locate where it currently does `instance_data["joined"] = True; lib.dump_yaml(inf, instance_data); lib.cas_push(...)`. Replace that finalize tail with:

```python
    nxt = lib.next_phase_id(protocol_obj, fanout_state["id"]) if (multiphase and fanout_state) else None
    if nxt:
        # Fan-out phase complete and a further phase follows → advance the cursor.
        instance_data["phase"] = nxt
        lib.dump_yaml(inf, instance_data)
        lib.cas_push(dir_, f"{instance}: fan-out phase {fanout_state['id']} done → advancing to {nxt}")
        if os.environ.get("ENGINE_LOCAL", "0") == "1":
            sys.stderr.write(f"[ENGINE_LOCAL] gh api dispatches protocol-advance phase={nxt}\n")
        else:
            import subprocess as _sp
            _sp.run(["gh", "api", f"repos/{os.environ.get('GITHUB_REPOSITORY','')}/dispatches",
                     "-f", "event_type=protocol-advance",
                     "-F", f"client_payload[protocol]={pid}",
                     "-F", f"client_payload[instance]={instance}",
                     "-F", f"client_payload[phase]={nxt}"], text=True, capture_output=True)
    else:
        # Final fan-out phase (or single-phase multi-grumpy) → finalize the instance.
        instance_data["joined"] = True
        lib.dump_yaml(inf, instance_data)
        lib.cas_push(dir_, f"{instance}: join → {concl} (all branches terminal)")
```

> The `set_check_run` + `upsert_status_comment` calls ABOVE this tail stay as-is. For `multi-grumpy` (`multiphase` False), `nxt` is always None → the `else` branch runs the original finalize, byte-identical.

- [ ] **Step 4: Run the new test + regression**

Run: `pytest tests/test_multiphase.py -k "join_reads_phase" -q` → PASS.
Run: `pytest tests/ -q` and especially `pytest tests/test_join.py tests/test_fanout_e2e.py -q` → PASS. Count: **189 passed**.

> If `test_join.py` fails, the single-phase finalize path diverged — STOP and confirm the `else` branch matches the original finalize lines exactly.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/join.py tests/test_multiphase.py
git commit -m "feat(engine): join advances the cursor past a completed fan-out phase

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: end-to-end — drive the full mini pipeline walk

**Files:**
- Test: `tests/test_multiphase.py` (add the integration test)

**Design:** Tie it together in `ENGINE_LOCAL` mode, simulating the orchestrator's role (calling the engine scripts in sequence, threading `PHASE`/`BRANCH` and verdicts/evidence). Walk: `start` (seed gate) → `advance` gate clear (cursor→work, protocol-advance) → `advance-phase work` (seed fan-out leg) → `advance` alpha leg done (fire protocol-join) → `join` (finalize). Assert the cursor progression and final `joined`.

- [ ] **Step 1: Write the e2e test**

Append to `tests/test_multiphase.py`:

```python
def test_e2e_mini_pipeline_clear_path(tmp_path, engine_env):
    work = tmp_path / "state"
    inst_path = str(work) + "/pipeline-mini/pr-1/_instance.yaml"

    def verdicts_pass(p):
        p.write_text(json.dumps({"results": [{"check": "always-pass", "pass": True,
                                              "feedback": "", "on_fail": "iterate"}]}))
        return p

    # 1. start → seed gate
    run_next(work, "pr-1", MINI, "start", engine_env, head="abc")
    assert lib.load_yaml(inst_path)["phase"] == "gate"

    # 2. gate advance, clear → cursor advances to work
    v = verdicts_pass(tmp_path / "v1.json")
    ev = tmp_path / "ev1.json"; ev.write_text(json.dumps({"gate": "clear"}))
    r = run_advance(work, "pr-1", MINI, v, ev, engine_env, phase="gate")
    assert r.returncode == 0, r.stderr
    assert lib.load_yaml(inst_path)["phase"] == "work"

    # 3. advance-phase work → seed the fan-out leg
    run_next(work, "pr-1", MINI, "advance-phase", engine_env, phase="work")
    assert os.path.exists(str(work) + "/pipeline-mini/pr-1/work.alpha.yaml")

    # 4. alpha leg advance, checks pass → leg done, fires protocol-join
    v2 = verdicts_pass(tmp_path / "v2.json")
    ev2 = tmp_path / "ev2.json"; ev2.write_text(json.dumps({}))
    r = run_advance(work, "pr-1", MINI, v2, ev2, engine_env, phase="work", branch="alpha")
    assert r.returncode == 0, r.stderr
    assert lib.load_yaml(str(work) + "/pipeline-mini/pr-1/work.alpha.yaml")["state"] == "done"
    assert "protocol-join" in r.stderr

    # 5. join → finalize the instance
    e = dict(engine_env); e["PR"] = "1"; e["PR_HEAD_SHA"] = "abc"
    r = subprocess.run(["python3", str(ENGINE / "join.py"), str(work), "pr-1", str(MINI)],
                       text=True, capture_output=True, env=e)
    assert r.returncode == 0, r.stderr
    assert lib.load_yaml(inst_path)["joined"] is True
```

- [ ] **Step 2: Run the e2e test**

Run: `pytest tests/test_multiphase.py::test_e2e_mini_pipeline_clear_path -q`
Expected: PASS. If a step fails, the failure pinpoints which transition is wrong (cursor not advancing, leg not seeded, join not firing) — fix the responsible script from the earlier task and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/test_multiphase.py
git commit -m "test(engine): e2e mini multi-phase pipeline walk (clear path)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: full-suite verification + milestone close-out

**Files:** none (verification only)

- [ ] **Step 1: Run the entire suite**

Run: `pytest tests/ -q`
Expected: PASS — **190 passed** (M1's 168 + the 22 new multiphase tests; the exact count may vary slightly with the e2e test — the requirement is zero failures and that every regression module is green).

- [ ] **Step 2: Confirm the regression anchors specifically**

Run: `pytest tests/test_engine.py tests/test_fanout_e2e.py tests/test_join.py tests/test_publish.py tests/test_runchecks.py tests/test_decide.py -q`
Expected: PASS — all single-phase behavior byte-identical.

- [ ] **Step 3: Confirm a clean, scoped diff**

Run: `git diff --stat main` (the M2a branch vs main).
Expected: only `lib.py`, `next.py`, `advance.py`, `join.py`, `tests/test_multiphase.py`, and the `tests/fixtures/pipeline-mini/**` files changed. No edits to existing protocols or other tests.

---

## Self-Review (completed by plan author)

**1. Spec coverage (M2a portion of the spec):**
- Cursor-based multi-phase state machine; planner starts at first state, follows `next` → Tasks 3, 4 (next.py), with `lib` introspection in Task 1. ✓
- `advance.py` "enter next phase" on agent `done`+`clear`; `on_blocked: halt` terminates → Task 5. ✓
- Conclude/publish seam (the M1-deferred item lands here) → Task 5 (`run_conclude_hook`, opt-in; legacy publish unchanged). ✓
- Decision A — `protocol-advance` dispatch fired by advance/join → Tasks 5, 6. ✓
- Decision B — `_instance.yaml` phase cursor + phase-prefixed paths; single-phase layouts preserved → Task 1 (`state_file` phase arg), used throughout. ✓
- Decision C — aggregate `code-review-pipeline` run + per-phase sub-runs → Task 5 (`cr_name` = `pid/phase[/branch]`; aggregate `pid` set on halt/terminal) and Task 6 (join aggregate). ✓
- Multi-phase rule (`>1 agent|fanout state`) → Task 1 (`is_multiphase`), gating every new path. ✓
- Regression byte-identical (M1's invariant extended) → every task's regression step + Task 8. ✓
- **Out of M2a scope (correctly deferred to M2b):** the generic reusable orchestrator YAML, the `triggers` block in protocol.json, the trigger shim, and the orchestrator rename. The `protocol-advance`→`advance-phase` command mapping is defined here as a contract but *consumed* by M2b. ✓ (Stated in the scope note.)

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step shows complete code; every test step shows the test body; commands have expected outcomes/counts. The one soft spot — the exact pre-edit text of advance.py's `process == "done"` body that Task 5's `else` branch must reproduce — is explicitly called out as "must byte-match the pre-edit code," with a regression gate (Task 5 Step 5) that fails loudly if it diverges. This is the correct treatment for a refactor that wraps existing code: the regression suite is the proof.

**3. Type/signature consistency:** `state_file(d, pid, instance, branch=None, phase=None)` is defined in Task 1 and called with `phase=`/`branch=` kwargs consistently in Tasks 3-6. `is_multiphase`, `phase_states`, `next_phase_id`, `state_by_id` are defined in Task 1 and used with those exact names/signatures in next.py (Tasks 3-4), advance.py (Task 5), join.py (Task 6). The `PHASE` env var, the `phase` field in `run-agent`/`run-fanout` actions, the `_instance.yaml` `phase` cursor key, and the `protocol-advance`/`advance-phase` event/command names are used identically across tasks. The `conclude` hook output keys (`conclusion`/`summary`/`blocked`) match between the fixture stub (Task 2) and the consumer (`run_conclude_hook`, Task 5).
