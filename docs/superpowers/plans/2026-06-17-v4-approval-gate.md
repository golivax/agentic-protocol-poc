# v4 — Pause-and-Require Approval Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a generic `kind:"gate"` protocol state — a human sign-off (`/approve` · `/request-changes` · `/reject`) as a *required* transition — and place one as a final sign-off gate after the `code-review-pipeline` join.

**Architecture:** A gate phase dispatches no agent and runs no checks. When the cursor lands on it, the engine seeds a per-phase state file carrying the reserved `gates:` field (`state: open`), emits an `in_progress` "awaiting approval" check-run, and the workflow run ends — state is durable. A `/approve`-family comment wakes the engine; a new `resolve-gate` command in `next.py` validates authorization (write/admin in the workflow; self-approval in `next.py`), records the decision in `gates.history`, and advances the cursor (approve) or halts (request-changes / reject). The shipped `/override` escape-hatch is deliberately NOT wired to gate decisions.

**Tech Stack:** Python 3 + PyYAML (engine, runtime deps only), pytest (dev-only tests), GitHub Actions YAML (orchestrator/engine workflows), `gh` CLI.

## Global Constraints

- **Engine/protocol separation:** all gate *logic* lives in `.github/agent-factory/engine/`; the only protocol-directory change is the gate state + trigger declarations (data) in `protocol.json`. Copied verbatim from the spec.
- **v1/v2 regression guard:** a protocol with no `gate` phase and no gate trigger MUST produce byte-identical state. `grumpy` (1 agent) and `multi-grumpy` (1 fanout) stay unchanged.
- **`phase_states()` stays `agent|fanout`** — do NOT add `gate` to it (it feeds `is_multiphase`, first-phase seeding, agent-unit resolution, the join, and the render loop's leg logic). Gate visibility is handled by a *separate* `pipeline_states()` helper used only by the renderer.
- **Security (standing CLAUDE.md rule):** agent/human-derived strings (`GATE_REASON`, decision, actor) pass to shell steps via `env:`, NEVER interpolated into `run:` blocks. Identity (`GATE_ACTOR`, `GATE_PR_AUTHOR`) comes only from the trusted event context, never the comment body.
- **State advances only by fast-forward `cas_push`** — never force-push `agentic-state`.
- **Check ABI / state model unchanged:** the engine only ever emits check-run `status` ∈ {`in_progress`,`completed`}; an open gate is `in_progress` with no conclusion (renders as a pending dot indefinitely).
- **Tests are pytest** under `tests/test_*.py`; reuse `tests/conftest.py` fixtures (`state_origin`, `engine_env`) and the `ENGINE_LOCAL=1` stderr-noop convention for all GitHub I/O.
- **A "gate is live"** ⟺ cursor is on it AND `gates.state ∈ {open, changes_requested}`. `approved` (cursor advanced) and `rejected` (terminal) are not live.

---

## File structure

- `.github/agent-factory/engine/lib.py` — new `pipeline_states()`, `open_gate()`; extend `next_phase_id()` (treat `gate` as a valid next); gate branch in `render_pipeline_status_body()`.
- `.github/agent-factory/engine/next.py` — gate branch in `seed_and_dispatch_phase()`; new `resolve-gate` command + `do_resolve_gate()`.
- `.github/agent-factory/engine/join.py` — open a following gate instead of finalizing, when the join's `.next` is a `kind:"gate"` state.
- `.github/agent-factory/protocols/code-review-pipeline/protocol.json` — add the `approval` gate state, point `join.next` at it, add the three resolve triggers (data).
- `.github/workflows/agentic-engine.yml` — `ctx` step `resolve-gate` branch (decision derivation + write/admin auth + denial comment); `plan` step `GATE_*` env.
- `tests/test_gate.py` — new test module (lib helpers, seed/open, resolve decisions, guards, idempotency, inertness, routing).
- `docs/BACKLOG.md`, `docs/STATUS.md` — mark v4 shipped; document the gate primitive.

---

## Task 1: `next_phase_id` treats a gate as a valid next phase

**Files:**
- Modify: `.github/agent-factory/engine/lib.py:212-223` (`next_phase_id`)
- Test: `tests/test_gate.py` (new)

**Interfaces:**
- Produces: `lib.next_phase_id(protocol, phase_id)` now returns the next id when the `.next` state's kind ∈ {`agent`,`fanout`,`gate`} (was {`agent`,`fanout`}).

- [ ] **Step 1: Write the failing test**

Create `tests/test_gate.py` with the module header and first test:

```python
"""v4 pause-and-require approval gate — engine-side behavior. All GitHub I/O is
ENGINE_LOCAL stderr no-ops we assert on. Mirrors tests/test_override.py style."""
import json
import os
import subprocess
import sys

import pytest
import yaml

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
NEXT_PY = ENGINE / "next.py"
JOIN_PY = ENGINE / "join.py"
LIB_PY = ENGINE / "lib.py"
PIPELINE_PROTO = ROOT / ".github/agent-factory/protocols/code-review-pipeline/protocol.json"
PID = json.load(open(PIPELINE_PROTO))["name"]

sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def test_next_phase_id_returns_gate_kind():
    proto = {"states": [
        {"id": "a", "kind": "agent", "next": "g"},
        {"id": "g", "kind": "gate", "next": "done"},
    ]}
    assert lib.next_phase_id(proto, "a") == "g"
    # a gate whose next is a terminal → None (finalize)
    assert lib.next_phase_id(proto, "g") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate.py::test_next_phase_id_returns_gate_kind -v`
Expected: FAIL — `next_phase_id` returns `None` for `"a"` because `gate` is not yet an accepted kind.

- [ ] **Step 3: Implement**

In `lib.py`, change the kind tuple in `next_phase_id` (around line 221):

```python
    if nxt_state and nxt_state.get("kind") in ("agent", "fanout", "gate"):
        return nxt
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gate.py::test_next_phase_id_returns_gate_kind -v`
Expected: PASS

- [ ] **Step 5: Run the multiphase regression for `next_phase_id`**

Run: `pytest tests/test_multiphase.py -k next_phase_id -v`
Expected: PASS (the `pipeline-mini` fixture has no gate-kind state, so `work→join` still returns `None`).

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_gate.py
git commit -m "feat(engine): next_phase_id treats a gate as a valid next phase"
```

---

## Task 2: `pipeline_states()` and `open_gate()` lib helpers

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add two functions; `pipeline_states` next to `phase_states` ~line 105; `open_gate` after `instance_file` ~line 228)
- Test: `tests/test_gate.py`

**Interfaces:**
- Produces:
  - `lib.pipeline_states(protocol) -> list[dict]` — ordered states of kind ∈ {`agent`,`fanout`,`gate`}.
  - `lib.open_gate(dir_, pid, instance, proto_path, gate_id, sha, pr) -> None` — seeds `<instance>/<gate_id>.yaml` with `{state: <gate_id>, head_sha, gates: {state: "open", history: []}}`, emits the `<pid>/<gate_id>` `in_progress` check-run, and (if the instance file exists) refreshes the shared status comment. Does NOT set the cursor and does NOT `cas_push` — the caller owns both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gate.py`:

```python
def test_pipeline_states_includes_gate_in_order():
    proto = {"states": [
        {"id": "a", "kind": "agent"},
        {"id": "f", "kind": "fanout"},
        {"id": "j", "kind": "join"},
        {"id": "g", "kind": "gate"},
    ]}
    assert [s["id"] for s in lib.pipeline_states(proto)] == ["a", "f", "g"]


def test_open_gate_seeds_file_and_check_run(tmp_path, capfd, monkeypatch):
    monkeypatch.setenv("ENGINE_LOCAL", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "golivax/agentic-protocol-poc")
    d = tmp_path / "state"
    base = d / PID / "pr-1"
    base.mkdir(parents=True)
    # an instance cursor file must exist for the status-comment refresh path
    (base / "_instance.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": "pr-1", "phase": "approval",
         "head_sha": "sha9", "joined": True, "status_comment_id": 5}))
    lib.open_gate(str(d), PID, "pr-1", str(PIPELINE_PROTO), "approval", "sha9", "1")

    gate = yaml.safe_load((base / "approval.yaml").read_text())
    assert gate["gates"] == {"state": "open", "history": []}
    assert gate["state"] == "approval"
    assert gate["head_sha"] == "sha9"
    err = capfd.readouterr().err
    assert "check-run code-review-pipeline/approval" in err
    assert "status=in_progress" in err
```

(`test_open_gate_*` references `approval` in `PIPELINE_PROTO`; the gate state is added in Task 4. Run this test's assertions only after Task 4, OR temporarily point `proto_path` at a tmp proto. To keep tasks independently green, write a tmp proto here instead:)

Replace the `open_gate` call line with a self-contained tmp protocol so the test passes before Task 4:

```python
    proto_path = tmp_path / "p.json"
    proto_path.write_text(json.dumps({"name": PID, "states": [
        {"id": "approval", "kind": "gate", "next": "done"}]}))
    lib.open_gate(str(d), PID, "pr-1", str(proto_path), "approval", "sha9", "1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gate.py -k "pipeline_states or open_gate" -v`
Expected: FAIL — `lib` has no `pipeline_states` / `open_gate`.

- [ ] **Step 3: Implement `pipeline_states`**

In `lib.py`, immediately after `phase_states` (after line 105):

```python
def pipeline_states(protocol):
    """Ordered agent|fanout|GATE states — the full human-visible pipeline.
    Used ONLY by the status renderer. phase_states() stays agent|fanout so the
    agent-unit / seed / join logic is unaffected by gates."""
    return [s for s in protocol.get("states", []) if s.get("kind") in ("agent", "fanout", "gate")]
```

- [ ] **Step 4: Implement `open_gate`**

In `lib.py`, after `instance_file` (after line 228):

```python
def open_gate(dir_, pid, instance, proto_path, gate_id, sha, pr):
    """Seed a gate phase's state file (gates.state=open), emit the awaiting
    check-run, and refresh the shared status comment. Does NOT set the cursor
    (caller owns _instance.yaml) and does NOT cas_push (caller pushes)."""
    sf = state_file(dir_, pid, instance, phase=gate_id)
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    dump_yaml(sf, {
        "protocol": pid, "instance": instance, "state": gate_id,
        "head_sha": sha, "gates": {"state": "open", "history": []},
    })
    set_check_run(f"{pid}/{gate_id}", sha, "in_progress", "",
                  "Awaiting human approval",
                  "Comment `/approve`, `/request-changes`, or `/reject` on this PR.")
    inf = instance_file(dir_, pid, instance)
    if os.path.isfile(inf):
        body = render_pipeline_status_body(dir_, pid, instance, proto_path)
        upsert_status_comment(inf, pr, body)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_gate.py -k "pipeline_states or open_gate" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_gate.py
git commit -m "feat(engine): pipeline_states + open_gate gate-phase helpers"
```

---

## Task 3: render a gate row in the pipeline status comment

**Files:**
- Modify: `.github/agent-factory/engine/lib.py:579-612` (the `for ph in phase_states(...)` loop and headline in `render_pipeline_status_body`)
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `lib.pipeline_states` (Task 2).
- Produces: `render_pipeline_status_body` renders a `**<gate>**` section when the gate file exists (`⏳ awaiting` / `✅ approved by @x` / `🔁 changes requested by @x` / `⛔ rejected by @x`) and an "Awaiting human approval" headline while a gate is open. A **missing** gate file renders **no** row (pre-gate output stays byte-identical).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gate.py`:

```python
def _seed_instance_with_gate(tmp_path, gate_state, actor="bob"):
    d = tmp_path / "state"
    base = d / PID / "pr-7"
    base.mkdir(parents=True)
    (base / "_instance.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": "pr-7", "phase": "approval",
         "head_sha": "s", "joined": True}))
    hist = [] if gate_state == "open" else [{"decision": gate_state, "actor": actor, "reason": ""}]
    (base / "approval.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": "pr-7", "state": "approval", "head_sha": "s",
         "gates": {"state": gate_state, "history": hist}}))
    return d


def test_render_gate_open_row_and_headline(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "golivax/agentic-protocol-poc")
    d = _seed_instance_with_gate(tmp_path, "open")
    body = lib.render_pipeline_status_body(str(d), PID, "pr-7", str(PIPELINE_PROTO))
    assert "**approval**" in body
    assert "awaiting human sign-off" in body
    assert "Awaiting human approval" in body  # headline


def test_render_gate_approved_row(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "golivax/agentic-protocol-poc")
    d = _seed_instance_with_gate(tmp_path, "approved", actor="carol")
    body = lib.render_pipeline_status_body(str(d), PID, "pr-7", str(PIPELINE_PROTO))
    assert "approved by @carol" in body
```

(These reference `approval` in `PIPELINE_PROTO`; they will pass after Task 4 adds the gate state. If running this task before Task 4, the renderer's `pipeline_states` won't see a gate in the real proto and the gate row won't appear. ORDER NOTE: implement Task 4's protocol.json edit before these two assertions can pass. The renderer code itself is exercised regardless.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate.py -k render_gate -v`
Expected: FAIL — no gate branch in the renderer.

- [ ] **Step 3: Implement**

In `render_pipeline_status_body`: (a) change the loop header from `for ph in phase_states(protocol):` to `for ph in pipeline_states(protocol):`; (b) add a `gate_open = False` next to `any_active = any_failed = False` (line 576); (c) add a gate branch in the loop, before the final `else: # agent phase` — make the structure `if kind == "fanout": ... elif kind == "gate": ... else: # agent`:

```python
        elif ph.get("kind") == "gate":
            sf = state_file(dir_, pid, instance, phase=ph_id)
            if not os.path.isfile(sf):
                continue  # gate not reached yet → no row (pre-gate output unchanged)
            g = (load_yaml(sf).get("gates") or {})
            gstate = g.get("state", "")
            hist = g.get("history") or []
            who = (hist[-1].get("actor") if hist else "") or ""
            if gstate == "approved":
                note = f"✅ approved by @{who}"
            elif gstate == "rejected":
                note = f"⛔ rejected by @{who}"
                any_failed = True
            elif gstate == "changes_requested":
                note = f"🔁 changes requested by @{who} — push a fix or `/approve`"
                gate_open = True
            else:  # open
                note = "⏳ awaiting human sign-off (`/approve` · `/request-changes` · `/reject`)"
                gate_open = True
            sections += f"**{ph_id}**\n\n{note}\n\n"
```

(d) In the headline ladder (lines 614-622), insert a gate clause after the `blocked_phase` clause and before `any_failed`:

```python
    if blocked_phase:
        headline = (f"⛔ Blocked at **{blocked_phase}** — a write-access user can comment "
                    f"`/override <reason>` to proceed past this gate.")
    elif gate_open:
        headline = ("⏳ Awaiting human approval — comment `/approve`, "
                    "`/request-changes`, or `/reject`.")
    elif any_failed:
        ...
```

- [ ] **Step 4: Run test to verify it passes** (after Task 4's protocol.json edit is in place)

Run: `pytest tests/test_gate.py -k render_gate -v`
Expected: PASS

- [ ] **Step 5: Run the status-comment regression**

Run: `pytest tests/test_pipeline_status.py -v`
Expected: PASS — no gate file is seeded in those tests, so the `continue` keeps every rendered body byte-identical.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_gate.py
git commit -m "feat(engine): render the human gate row + awaiting headline"
```

---

## Task 4: `protocol.json` — add the `approval` gate + triggers, point join at it

**Files:**
- Modify: `.github/agent-factory/protocols/code-review-pipeline/protocol.json`
- Test: `tests/test_gate.py`

**Interfaces:**
- Produces: the `code-review-pipeline` protocol now has a `kind:"gate"` state `approval` (with `approve_excludes_author: true`, `next: "done"`), `join.next == "approval"`, and three `issue_comment` triggers (`/approve`, `/request-changes`, `/reject`) → command `resolve-gate`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gate.py`:

```python
def _match(body):
    return subprocess.run(
        ["python3", str(LIB_PY), "match-trigger", str(PIPELINE_PROTO),
         "issue_comment", "", body],
        text=True, capture_output=True).stdout.strip()


@pytest.mark.parametrize("body", ["/approve", "/approve ship it",
                                  "/request-changes do X", "/reject nope"])
def test_resolve_triggers_map_to_command(body):
    assert _match(body) == "resolve-gate"


def test_review_still_routes_with_gate_triggers():
    assert _match("/review") == "start"


def test_protocol_has_gate_state():
    proto = json.load(open(PIPELINE_PROTO))
    g = next(s for s in proto["states"] if s["id"] == "approval")
    assert g["kind"] == "gate" and g["next"] == "done"
    assert g["approve_excludes_author"] is True
    j = next(s for s in proto["states"] if s["kind"] == "join")
    assert j["next"] == "approval"


def test_route_unambiguous_for_approve():
    pdir = str(ROOT / ".github/agent-factory/protocols")
    r = subprocess.run(["python3", str(LIB_PY), "route", pdir,
                        "issue_comment", "", "/approve", "", "true"],
                       text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    assert f"protocols/{PID}/protocol.json" in r.stdout and "skip=false" in r.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gate.py -k "resolve_triggers or protocol_has_gate or route_unambiguous or review_still_routes" -v`
Expected: FAIL — no gate state / triggers yet.

- [ ] **Step 3: Implement the protocol.json edits**

Add the three triggers to the `triggers` array (after the `/override` line):

```json
    { "on": "issue_comment", "comment_prefix": "/approve",         "command": "resolve-gate" },
    { "on": "issue_comment", "comment_prefix": "/request-changes", "command": "resolve-gate" },
    { "on": "issue_comment", "comment_prefix": "/reject",          "command": "resolve-gate" },
```

Change the `join` state's `next` from `"done"` to `"approval"` and append the gate state after it, so the `states` array tail reads:

```json
    {
      "id": "join",
      "kind": "join",
      "of": "review",
      "next": "approval"
    },
    {
      "id": "approval",
      "kind": "gate",
      "approve_excludes_author": true,
      "next": "done"
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gate.py -k "resolve_triggers or protocol_has_gate or route_unambiguous or review_still_routes" -v`
Expected: PASS

- [ ] **Step 5: Re-run Task 2/3 gate-dependent assertions + the route/override suites**

Run: `pytest tests/test_gate.py tests/test_route.py tests/test_override.py -q`
Expected: PASS — `/override` and `/review` still route; the open_gate/render tests that referenced `approval` now resolve against the real proto.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/protocols/code-review-pipeline/protocol.json tests/test_gate.py
git commit -m "feat(protocol): add the approval gate + resolve triggers to code-review-pipeline"
```

---

## Task 5: `seed_and_dispatch_phase` opens a gate (the generic advance-into-gate path)

**Files:**
- Modify: `.github/agent-factory/engine/next.py:93-139` (`seed_and_dispatch_phase`)
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `lib.open_gate` (Task 2).
- Produces: when the seeded phase's kind is `gate`, `seed_and_dispatch_phase` opens the gate (via `open_gate`), `cas_push`es, and prints `{"action": "noop", "iteration": 0, "feedback": "", "reason": "gate-open:<id>"}` — no agent dispatch. (This is exercised by `advance-phase` into a gate and by `resolve-gate` advancing into a *following* gate.)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gate.py`:

```python
def _env(state_origin, **extra):
    e = dict(os.environ)
    e["ENGINE_LOCAL"] = "1"
    e["STATE_REMOTE"] = str(state_origin)
    e["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    e.update(extra)
    return e


def _run(script, args, env):
    r = subprocess.run(["python3", str(script), *map(str, args)],
                       text=True, capture_output=True, env=env)
    return r.stdout, r.stderr, r.returncode


def _clone(state_origin, target):
    subprocess.run(["git", "clone", "-q", "--branch", "agentic-state",
                    str(state_origin), str(target)], check=True)


def _seed_cursor(state_origin, work, instance, phase, *, head_sha="s"):
    """Seed an _instance.yaml cursor on `phase` and push (no per-phase files)."""
    _run(LIB_PY, ["state-checkout", str(work)], _env(state_origin))
    base = work / PID / instance
    base.mkdir(parents=True, exist_ok=True)
    (base / "_instance.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": instance, "phase": phase,
         "head_sha": head_sha, "joined": True}))
    _run(LIB_PY, ["cas-push", str(work), f"seed {instance}"], _env(state_origin))


def test_advance_phase_into_gate_opens_it(state_origin, tmp_path):
    inst = "pr-20"
    _seed_cursor(state_origin, tmp_path / "seed", inst, "review")
    env = _env(state_origin, PHASE="approval")
    out, err, rc = _run(NEXT_PY, [tmp_path / "w", inst, PIPELINE_PROTO,
                                  "advance-phase", "s"], env)
    assert rc == 0, err
    assert json.loads(out)["action"] == "noop"
    assert json.loads(out)["reason"] == "gate-open:approval"
    _clone(state_origin, tmp_path / "verify")
    base = tmp_path / "verify" / PID / inst
    gate = yaml.safe_load((base / "approval.yaml").read_text())
    assert gate["gates"]["state"] == "open"
    assert yaml.safe_load((base / "_instance.yaml").read_text())["phase"] == "approval"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate.py::test_advance_phase_into_gate_opens_it -v`
Expected: FAIL — `seed_and_dispatch_phase` has no gate branch; it falls into the `else` agent path and emits `run-agent` (and would try to seed an agent file).

- [ ] **Step 3: Implement**

In `next.py` `seed_and_dispatch_phase`, change the `if kind == "fanout": ... else:` into a three-way. Insert this branch between the `fanout` block (ends line 129) and the final `else:` (line 130):

```python
    elif kind == "gate":
        pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
        # cursor already written above; open_gate seeds the gate file + check-run
        # + status comment. No agent dispatch — the run ends and waits for a human.
        lib.open_gate(DIR, PID, INSTANCE, PROTO, phase_id, HEAD_SHA, pr)
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: open gate {phase_id} ({command})")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"gate-open:{phase_id}"}))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gate.py::test_advance_phase_into_gate_opens_it -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_gate.py
git commit -m "feat(engine): seed_and_dispatch_phase opens a gate (noop, no dispatch)"
```

---

## Task 6: `join.py` opens a following gate instead of finalizing

**Files:**
- Modify: `.github/agent-factory/engine/join.py:86-109` (the `all_done` finalize block)
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `lib.open_gate`, `lib.state_by_id`.
- Produces: when all branches are `done` AND the join state's `.next` resolves to a `kind:"gate"` state, `join.py` sets `joined: true`, advances the cursor to the gate, opens it, `cas_push`es `"<instance>: join clear → gate <id> open"`, and returns — leaving the aggregate `<pid>` check-run untouched (still `in_progress`). Otherwise (no gate, or a branch failed) it finalizes exactly as before.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gate.py`:

```python
REVIEW_BRANCHES = [b["id"] for s in json.load(open(PIPELINE_PROTO))["states"]
                   if s["id"] == "review" for b in s["branches"]]


def _seed_review_all_done(state_origin, work, instance, *, head_sha="js"):
    _run(LIB_PY, ["state-checkout", str(work)], _env(state_origin))
    base = work / PID / instance
    base.mkdir(parents=True, exist_ok=True)
    (base / "_instance.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": instance, "phase": "review",
         "head_sha": head_sha, "joined": False, "status_comment_id": 9}))
    for b in REVIEW_BRANCHES:
        (base / f"review.{b}.yaml").write_text(yaml.safe_dump(
            {"protocol": PID, "instance": instance, "state": "done",
             "iteration": 1, "gates": {}, "head_sha": head_sha, "history": []}))
    _run(LIB_PY, ["cas-push", str(work), f"seed {instance}"], _env(state_origin))


def test_join_opens_following_gate(state_origin, tmp_path):
    inst = "pr-21"
    _seed_review_all_done(state_origin, tmp_path / "seed", inst)
    env = _env(state_origin, PR="21", PR_HEAD_SHA="js")
    out, err, rc = _run(JOIN_PY, [tmp_path / "w", inst, PIPELINE_PROTO], env)
    assert rc == 0, err
    _clone(state_origin, tmp_path / "verify")
    base = tmp_path / "verify" / PID / inst
    inf = yaml.safe_load((base / "_instance.yaml").read_text())
    assert inf["phase"] == "approval" and inf["joined"] is True
    gate = yaml.safe_load((base / "approval.yaml").read_text())
    assert gate["gates"]["state"] == "open"
    # the aggregate pid check-run is NOT completed at the gate (still in_progress)
    assert "check-run code-review-pipeline sha=js status=completed" not in err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate.py::test_join_opens_following_gate -v`
Expected: FAIL — `join.py` finalizes (sets `pid` success, marks joined) and never seeds `approval.yaml`.

- [ ] **Step 3: Implement**

In `join.py`, replace the `if all_done:` / `else:` headline block + the finalize tail (lines 86-109) so the gate case is handled first and returns. Insert this at the start of the `if all_done:` branch (line 86), before `concl = "success"`:

```python
    if all_done:
        # If a human gate follows the join, OPEN it instead of finalizing.
        join_state = None
        fo_id = fanout_state.get("id") if fanout_state else None
        for st in protocol.get("states", []):
            if st.get("kind") == "join" and st.get("of") == fo_id:
                join_state = st
                break
        if join_state is None:
            for st in protocol.get("states", []):
                if st.get("kind") == "join":
                    join_state = st
                    break
        gate_next = (join_state or {}).get("next")
        gns = lib.state_by_id(protocol, gate_next) if gate_next else None
        if gns and gns.get("kind") == "gate":
            instance_data["joined"] = True
            instance_data["phase"] = gate_next
            lib.dump_yaml(inf, instance_data)
            lib.open_gate(dir_, pid, instance, proto, gate_next, sha, pr)
            lib.cas_push(dir_, f"{instance}: join clear → gate {gate_next} open")
            return
```

Wrap the existing finalize tail (the `if all_done: concl=success ... else: concl=failure ...` headline assignment plus `set_check_run`/render/`joined`/`cas_push`, lines 86-109) so it still runs for the non-gate path. Since the gate case `return`s, leave the rest of the function unchanged.

NOTE: `main()` currently has no early `return` helper — it runs top-to-bottom and ends. Adding `return` inside `main()` is fine (it just exits `main`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gate.py::test_join_opens_following_gate -v`
Expected: PASS

- [ ] **Step 5: Run the join/fanout/multiphase regression**

Run: `pytest tests/test_join.py tests/test_fanout_e2e.py tests/test_multiphase.py -q`
Expected: PASS — `multi-grumpy` and `pipeline-mini` joins point at terminals (not gates), so the new branch is skipped and finalization is byte-identical.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/join.py tests/test_gate.py
git commit -m "feat(engine): join opens a following human gate instead of finalizing"
```

---

## Task 7: `resolve-gate` command in `next.py` (approve / request-changes / reject + guards)

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (add `do_resolve_gate()` after `do_override()` ~line 205; add the command dispatch after the `override` hook ~line 214)
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `GATE_DECISION` ∈ {`approve`,`request-changes`,`reject`}, `GATE_ACTOR`, `GATE_REASON`, `GATE_PR_AUTHOR` (env); `lib.next_phase_id`, `seed_and_dispatch_phase`, `lib.open_gate`, `lib.render_pipeline_status_body`, `lib.set_check_run`, `lib.post_pr_comment`, `lib.cas_push`.
- Produces: command `resolve-gate`. On a live gate: appends `{decision,actor,reason}` to `gates.history`; **approve** → `gates.state=approved`, gate check-run `success`, then advance (`seed_and_dispatch_phase(next)`) or finalize (`pid` check-run `success`, `noop`); **request-changes** → `gates.state=changes_requested`, gate check-run `failure`, `noop` (cursor unchanged); **reject** → `gates.state=rejected`, file `state=failed`, gate + `pid` check-runs `failure`, `noop`. Guards (no instance / not-a-gate / rejected / not-live / self-approval) post one PR comment and emit `{"action":"halt",...}` with no state change.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gate.py`:

```python
def _seed_open_gate(state_origin, work, instance, *, gstate="open", head_sha="gs"):
    _run(LIB_PY, ["state-checkout", str(work)], _env(state_origin))
    base = work / PID / instance
    base.mkdir(parents=True, exist_ok=True)
    (base / "_instance.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": instance, "phase": "approval",
         "head_sha": head_sha, "joined": True, "status_comment_id": 9}))
    hist = [] if gstate == "open" else [{"decision": "request-changes", "actor": "x", "reason": ""}]
    (base / "approval.yaml").write_text(yaml.safe_dump(
        {"protocol": PID, "instance": instance, "state": "approval",
         "head_sha": head_sha, "gates": {"state": gstate, "history": hist}}))
    _run(LIB_PY, ["cas-push", str(work), f"seed {instance}"], _env(state_origin))


def _resolve(state_origin, tmp_path, inst, decision, actor, reason="", pr_author="someone"):
    env = _env(state_origin, GATE_DECISION=decision, GATE_ACTOR=actor,
               GATE_REASON=reason, GATE_PR_AUTHOR=pr_author)
    return _run(NEXT_PY, [tmp_path / f"w-{decision}", inst, PIPELINE_PROTO,
                          "resolve-gate", "gs"], env)


def test_resolve_approve_finalizes_last_gate(state_origin, tmp_path):
    inst = "pr-30"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "bob", "lgtm")
    assert rc == 0, err
    assert json.loads(out)["action"] == "noop"
    _clone(state_origin, tmp_path / "verify")
    g = yaml.safe_load((tmp_path / "verify" / PID / inst / "approval.yaml").read_text())
    assert g["gates"]["state"] == "approved"
    assert g["gates"]["history"][-1] == {"decision": "approve", "actor": "bob", "reason": "lgtm"}
    # final gate → aggregate pid check-run completed success
    assert "check-run code-review-pipeline sha=gs status=completed conclusion=success" in err


def test_resolve_request_changes_halts_no_cursor_move(state_origin, tmp_path):
    inst = "pr-31"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    out, err, rc = _resolve(state_origin, tmp_path, inst, "request-changes", "alice", "fix it")
    assert rc == 0, err
    assert json.loads(out)["action"] == "noop"
    _clone(state_origin, tmp_path / "verify")
    base = tmp_path / "verify" / PID / inst
    assert yaml.safe_load((base / "_instance.yaml").read_text())["phase"] == "approval"
    g = yaml.safe_load((base / "approval.yaml").read_text())
    assert g["gates"]["state"] == "changes_requested"
    assert g["state"] == "approval"  # NOT failed (non-terminal)


def test_resolve_changes_then_approve(state_origin, tmp_path):
    inst = "pr-32"
    _seed_open_gate(state_origin, tmp_path / "seed", inst, gstate="changes_requested")
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "bob")
    assert rc == 0, err
    assert json.loads(out)["action"] == "noop"
    _clone(state_origin, tmp_path / "verify")
    g = yaml.safe_load((tmp_path / "verify" / PID / inst / "approval.yaml").read_text())
    assert g["gates"]["state"] == "approved"


def test_resolve_reject_is_terminal(state_origin, tmp_path):
    inst = "pr-33"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    out, err, rc = _resolve(state_origin, tmp_path, inst, "reject", "carol", "no")
    assert rc == 0, err
    _clone(state_origin, tmp_path / "verify")
    g = yaml.safe_load((tmp_path / "verify" / PID / inst / "approval.yaml").read_text())
    assert g["gates"]["state"] == "rejected" and g["state"] == "failed"
    assert "check-run code-review-pipeline sha=gs status=completed conclusion=failure" in err


def test_resolve_reject_then_approve_refused(state_origin, tmp_path):
    inst = "pr-34"
    _seed_open_gate(state_origin, tmp_path / "seed", inst, gstate="rejected")
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "bob")
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "rejected" in err.lower()


def test_resolve_self_approval_refused(state_origin, tmp_path):
    inst = "pr-35"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "dave", pr_author="dave")
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "cannot approve their own" in err.lower()
    _clone(state_origin, tmp_path / "verify")
    g = yaml.safe_load((tmp_path / "verify" / PID / inst / "approval.yaml").read_text())
    assert g["gates"]["state"] == "open"  # unchanged


def test_resolve_not_a_gate_refused(state_origin, tmp_path):
    inst = "pr-36"
    _seed_cursor(state_origin, tmp_path / "seed", inst, "preflight")
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "bob")
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "no approval gate" in err.lower()


def test_resolve_no_instance_refused(state_origin, tmp_path):
    out, err, rc = _resolve(state_origin, tmp_path, "pr-99", "approve", "bob")
    assert rc == 0
    assert json.loads(out)["action"] == "halt"
    assert "no" in err.lower() and "run exists" in err


def test_resolve_idempotent_double_approve(state_origin, tmp_path):
    inst = "pr-37"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    _resolve(state_origin, tmp_path, inst, "approve", "bob")
    out, err, rc = _resolve(state_origin, tmp_path, inst, "approve", "bob")
    # second approve: gate already approved (state != open/changes) → refused
    assert json.loads(out)["action"] == "halt"


def test_resolve_reason_is_inert(state_origin, tmp_path):
    inst = "pr-38"
    _seed_open_gate(state_origin, tmp_path / "seed", inst)
    nasty = "$(rm -rf /); `id`; <b>x</b>"
    _resolve(state_origin, tmp_path, inst, "reject", "carol", reason=nasty)
    _clone(state_origin, tmp_path / "verify")
    g = yaml.safe_load((tmp_path / "verify" / PID / inst / "approval.yaml").read_text())
    assert g["gates"]["history"][-1]["reason"] == nasty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gate.py -k resolve -v`
Expected: FAIL — `next.py` rejects the unknown command `resolve-gate` (exit 2).

- [ ] **Step 3: Implement `do_resolve_gate()`**

In `next.py`, add this function immediately after `do_override()` (after line 205):

```python
def do_resolve_gate():
    """Human approval gate resolution. write/admin auth happened in the workflow;
    next.py sees only an authorized actor. Reads GATE_DECISION/ACTOR/REASON/PR_AUTHOR
    from env, mutates the cursor gate's `gates` record, and advances (approve) or
    halts (request-changes / reject). Guards refuse with one PR comment + a halt
    action — no state change. A gate is 'live' when gates.state in {open,
    changes_requested}."""
    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    inf = lib.instance_file(DIR, PID, INSTANCE)
    decision = os.environ.get("GATE_DECISION", "")
    actor = os.environ.get("GATE_ACTOR", "")
    reason = os.environ.get("GATE_REASON", "")
    pr_author = os.environ.get("GATE_PR_AUTHOR", "")

    def refuse(message, code):
        lib.post_pr_comment(pr, message)
        print(json.dumps({"action": "halt", "iteration": 0, "feedback": "", "reason": code}))

    if not os.path.isfile(inf):
        refuse(f"Nothing to resolve — no {PID} run exists for this PR.", "gate: no instance")
        return
    inst = lib.load_yaml(inf)
    cursor = inst.get("phase") or ""
    cur_state = lib.state_by_id(proto_data, cursor)
    if not cursor or not cur_state or cur_state.get("kind") != "gate":
        refuse(f"Nothing to resolve — no approval gate is currently open for this PR "
               f"(current phase: {cursor or 'none'}).", "gate: not a gate")
        return

    sf = lib.state_file(DIR, PID, INSTANCE, phase=cursor)
    gdata = lib.load_yaml(sf) if os.path.isfile(sf) else {}
    g = gdata.get("gates") or {}
    gstate = g.get("state", "")
    sha = gdata.get("head_sha", "") or HEAD_SHA
    cr_name = f"{PID}/{cursor}"

    if gstate == "rejected":
        refuse("This gate was rejected; push a new commit or comment `/review` to "
               "restart the pipeline.", "gate: rejected")
        return
    if gstate not in ("open", "changes_requested"):
        refuse(f"Nothing to resolve — the {cursor} gate is not awaiting a decision "
               f"(state: {gstate or 'unknown'}).", "gate: not live")
        return
    if (decision == "approve" and cur_state.get("approve_excludes_author")
            and actor and actor == pr_author):
        refuse(f"@{actor} the PR author cannot approve their own gate; another "
               f"write-access reviewer must `/approve`.", "gate: self-approve")
        return

    g.setdefault("history", []).append({"decision": decision, "actor": actor, "reason": reason})

    if decision == "approve":
        g["state"] = "approved"
        gdata["gates"] = g
        lib.dump_yaml(sf, gdata)
        lib.set_check_run(cr_name, sha, "completed", "success", "Approved", f"Approved by @{actor}.")
        nxt = lib.next_phase_id(proto_data, cursor)
        if nxt:
            note = f"✅ {cursor} gate approved by @{actor}; proceeding to {nxt}."
            if reason:
                note += f"\n\n> {reason}"
            lib.post_pr_comment(pr, note)
            seed_and_dispatch_phase(nxt, "approve")   # sets cursor, pushes, emits run action
        else:
            lib.set_check_run(PID, sha, "completed", "success", "Complete", f"Approved by @{actor}.")
            note = f"✅ {cursor} gate approved by @{actor}; pipeline complete."
            if reason:
                note += f"\n\n> {reason}"
            lib.post_pr_comment(pr, note)
            body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
            lib.upsert_status_comment(inf, pr, body)
            lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} approved by {actor} → done")
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"gate:approved:{cursor}"}))
        return

    if decision == "request-changes":
        g["state"] = "changes_requested"
        gdata["gates"] = g
        lib.dump_yaml(sf, gdata)
        lib.set_check_run(cr_name, sha, "completed", "failure", "Changes requested",
                          f"Changes requested by @{actor}.")
        body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
        lib.upsert_status_comment(inf, pr, body)
        note = (f"🔁 {cursor} gate — changes requested by @{actor}. Push a new commit to "
                f"re-run the pipeline, or a reviewer can `/approve`.")
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} changes requested by {actor}")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"gate:changes:{cursor}"}))
        return

    if decision == "reject":
        g["state"] = "rejected"
        gdata["gates"] = g
        gdata["state"] = "failed"
        lib.dump_yaml(sf, gdata)
        lib.set_check_run(cr_name, sha, "completed", "failure", "Rejected", f"Rejected by @{actor}.")
        lib.set_check_run(PID, sha, "completed", "failure", "Pipeline rejected", f"Rejected by @{actor}.")
        body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
        lib.upsert_status_comment(inf, pr, body)
        note = f"⛔ {cursor} gate rejected by @{actor}. Push a new commit or `/review` to restart."
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} rejected by {actor} → failed")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"gate:rejected:{cursor}"}))
        return

    refuse(f"Unknown gate decision '{decision}'.", "gate: unknown decision")
```

- [ ] **Step 4: Add the command dispatch**

In `next.py`, right after the existing override hook (lines 212-214):

```python
if COMMAND == "override":
    do_override()
    sys.exit(0)

if COMMAND == "resolve-gate":
    do_resolve_gate()
    sys.exit(0)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_gate.py -k resolve -v`
Expected: PASS (all 11 resolve cases).

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test_gate.py
git commit -m "feat(engine): resolve-gate command (approve/request-changes/reject + guards)"
```

---

## Task 8: wire the `resolve-gate` event seam in `agentic-engine.yml`

**Files:**
- Modify: `.github/workflows/agentic-engine.yml` (the `ctx` step `issue_comment` case ~lines 85-107; the `ctx` step `echo ... >> GITHUB_OUTPUT` block ~lines 109-113; the `plan` step `env:` ~lines 124-133)
- Test: none (workflow YAML; verified by `python3 -c` compile of the embedded scripts + the live checkpoint). The pure logic is already covered by `test_gate.py`.

**Interfaces:**
- Consumes: `github.event.comment.body`, `github.event.comment.user.login`, `github.event.issue.user.login` (trusted); the collaborator-permission API.
- Produces: `steps.ctx.outputs.gate_decision|gate_actor|gate_reason|gate_pr_author`; on auth failure, posts a denial comment and clears `command` (engine no-ops). Passes `GATE_*` to `next.py` via the `plan` step env.

- [ ] **Step 1: Add the `resolve-gate` auth branch in the `issue_comment` case**

In the `ctx` step, after the closing `fi` of the `if [ "$CMD" = "override" ]` block (after line 106), insert:

```bash
              if [ "$CMD" = "resolve-gate" ]; then
                # Decision derived from the matched prefix (mutually exclusive; no
                # prefix is a prefix of another). Identity from TRUSTED event fields.
                case "$COMMENT_BODY" in
                  /approve*)         GATE_DECISION=approve;         PREFIX=/approve ;;
                  /request-changes*) GATE_DECISION=request-changes; PREFIX=/request-changes ;;
                  /reject*)          GATE_DECISION=reject;          PREFIX=/reject ;;
                  *)                 GATE_DECISION="";              PREFIX="" ;;
                esac
                PERM=$(gh api "repos/${{ github.repository }}/collaborators/$COMMENTER_LOGIN/permission" \
                  --jq '.permission' 2>/dev/null || echo none)
                if [ "$PERM" = "write" ] || [ "$PERM" = "admin" ]; then
                  REASON="${COMMENT_BODY#$PREFIX}"   # strip the matched prefix
                  REASON="${REASON# }"               # strip one leading space
                  echo "gate_decision=$GATE_DECISION" >> "$GITHUB_OUTPUT"
                  echo "gate_actor=$COMMENTER_LOGIN" >> "$GITHUB_OUTPUT"
                  echo "gate_pr_author=${{ github.event.issue.user.login }}" >> "$GITHUB_OUTPUT"
                  DELIM="gate_$(openssl rand -hex 16)"
                  { echo "gate_reason<<$DELIM"; printf '%s\n' "$REASON"; echo "$DELIM"; } >> "$GITHUB_OUTPUT"
                else
                  gh api "repos/${{ github.repository }}/issues/$PR/comments" \
                    -f "body=@$COMMENTER_LOGIN resolving this gate requires write access to this repository." >/dev/null 2>&1 || true
                  CMD=""   # not authorized → engine no-ops
                fi
              fi
```

- [ ] **Step 2: Pass `GATE_*` to `next.py`**

In the `plan` step `env:` block (after the `OVERRIDE_REASON` line, line 133), add:

```yaml
          GATE_DECISION: ${{ steps.ctx.outputs.gate_decision }}
          GATE_ACTOR: ${{ steps.ctx.outputs.gate_actor }}
          GATE_REASON: ${{ steps.ctx.outputs.gate_reason }}
          GATE_PR_AUTHOR: ${{ steps.ctx.outputs.gate_pr_author }}
```

- [ ] **Step 3: Sanity-check the YAML parses**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/agentic-engine.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Confirm the whole suite is green**

Run: `pytest tests/ -q`
Expected: PASS (all modules; the new `test_gate.py` plus every regression guard).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/agentic-engine.yml
git commit -m "feat(engine): wire resolve-gate event seam + write/admin auth in agentic-engine"
```

---

## Task 9: documentation — mark v4 shipped, document the gate primitive

**Files:**
- Modify: `docs/BACKLOG.md` (v4 section), `docs/STATUS.md`

**Interfaces:** none (docs).

- [ ] **Step 1: Update `docs/BACKLOG.md`**

In the "v4 — Human-in-the-loop (approval gate)" section, change **Status:** to:

```markdown
**Status:** DONE (2026-06-17). The `kind:"gate"` pause-and-require approval state
ships in the generic engine and is wired into `code-review-pipeline` as a final
sign-off gate after the join. `/approve` · `/request-changes` · `/reject` comments,
write/admin auth, no self-approval, distinct from `/override`. See
`docs/superpowers/specs/2026-06-17-v4-approval-gate-design.md` and the plan
`docs/superpowers/plans/2026-06-17-v4-approval-gate.md`. Out of scope (follow-up):
native PR-review (`pull_request_review`) as an alternative resolve trigger.
```

- [ ] **Step 2: Document the gate in `docs/STATUS.md`**

Add a short subsection describing: the `kind:"gate"` state (no agent, no checks); the per-phase `gates: {state, history}` record (first use of the reserved field); the open→awaiting check-run; `join.py` opening a following gate; the `resolve-gate` command + the three decisions and their state effects; and that `/override` deliberately does not apply to gate decisions. Keep it consistent with the surrounding STATUS.md voice.

- [ ] **Step 3: Commit**

```bash
git add docs/BACKLOG.md docs/STATUS.md
git commit -m "docs: mark v4 approval gate shipped; document the gate primitive"
```

---

## Final verification

- [ ] **Run the full suite**

Run: `pytest tests/ -q`
Expected: PASS — every module including `test_gate.py`; zero regressions in `test_engine.py`, `test_join.py`, `test_fanout_e2e.py`, `test_multiphase.py`, `test_override.py`, `test_pipeline_status.py`.

- [ ] **Live checkpoint (post-merge, manual)** on a PR taken through to the gate:
  1. fanout completes → join opens the gate → `code-review-pipeline/approval` check-run shows pending ("Awaiting human approval"); status comment shows the awaiting row.
  2. non-write user `/approve` → denial comment, no advance.
  3. PR author `/approve` → self-approval denial, no advance.
  4. reviewer `/request-changes` → gate check-run red + status note; a new commit (`synchronize`) reruns the pipeline from preflight and reopens the gate.
  5. reviewer `/approve` → cursor advances to `done`; aggregate `code-review-pipeline` check-run goes green; status comment shows ✅; `git log agentic-state -- code-review-pipeline/pr-N/approval.yaml` shows the decision history.

---

## Self-review notes (already reconciled into the tasks above)

- **Spec coverage:** gate state primitive (T4/T5), per-phase `gates` file (T2/T5), open check-run (T2), join→gate advance (T6), resolve-gate 3-way + guards + self-approval + idempotency + inertness (T7), write/admin auth (T8), status rendering (T3), `/override` separation (no `halted` write — confirmed absent from T7), docs (T9). All covered.
- **Ordering caveat (called out in T2/T3):** two tests assert against the real `approval` state, which only exists after T4. They are written self-contained (tmp proto) where possible; the renderer assertions are re-run in T4 Step 5. Implement in task order to keep each commit green.
- **Type consistency:** `gates` is always `{"state": str, "history": list}`; decision strings are `approve`/`request-changes`/`reject` (hyphenated) everywhere — the trigger command is `resolve-gate`, the decision keyword `request-changes`; check-run name is `f"{PID}/{cursor}"`; `open_gate` signature is `(dir_, pid, instance, proto_path, gate_id, sha, pr)` in lib, next.py, and join.py.
- **Regression invariant:** `phase_states` untouched; `pipeline_states` is renderer-only; `next_phase_id`/`join.py` changes are inert unless a `kind:"gate"` state is actually wired in.
