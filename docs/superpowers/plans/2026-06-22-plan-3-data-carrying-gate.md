# Plan 3 — Data-Carrying Human Gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A gate sub-state that surfaces agent-generated questions, collects `/answer` replies (accumulated), verifies coverage with a deterministic check, persists the answers, and resumes the branch into the next sub-state that consumes them.

**Architecture:** When the branch cursor advances to a sub-state of `kind:"gate"`, `advance.py` opens the gate (scoped to the branch) instead of dispatching an agent — rendering the questions read from the upstream sub-state's persisted evidence (`questions_from`). A new `/answer` command in `next.py` parses `qID: value` pairs, merges them into the gate's `answers.json` (Plan 2's `output_artifact_path(kind="answers")`), runs the `answers-coverage` check; on full coverage it advances the branch cursor to the next sub-state and dispatches it with the answers resolved as an input.

**Tech Stack:** Python 3 + PyYAML (runtime), pytest (dev).

**Depends on:** Plan 1 (sub-pipeline branches), Plan 2 (output persistence, `resolve_inputs`, `output_artifact_path`).

## Global Constraints

- Runtime deps: **Python 3 + PyYAML** (+ stdlib).
- The existing approve/request-changes/reject top-level gate is **untouched**; data-carrying is an additive mode keyed by `questions_from`.
- `/answer` text is human-supplied/untrusted: parsed in zone 1, carried via `env:`/files, **never** interpolated into a `run:` block. Coverage is a deterministic check over a structured doc.
- Every new field optional; existing suite stays green. CAS-push invariants unchanged.

---

### Task 1: `open_gate` gains branch scope + `questions_from` rendering

**Files:**
- Modify: `.github/agent-factory/engine/lib.py:238-254` (`open_gate`)
- Test: `tests/test_gate_data.py` (create)

**Interfaces:**
- Produces: `lib.open_gate(dir_, pid, instance, proto_path, gate_id, sha, pr, branch=None, questions=None)`. With `branch`, the gate state file is `state_file(..., branch=branch, substate=gate_id)` and the cursor stays the branch cursor; the gate file stores `gates:{state:open, history:[], questions:<questions>}`. The PR-comment body lists the questions (numbered, with `/answer <id>: …` syntax) when `questions` is non-empty; otherwise the legacy static approve/reject text. Existing top-level callers (no `branch`/`questions`) behave byte-identically.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate_data.py
import importlib, json, os, subprocess, sys
from conftest import ENGINE, FIXTURES, run_engine, read_state_yaml
sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")


def _clone(tmp_path, engine_env):
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
    return work


def test_open_gate_branch_scoped_with_questions(tmp_path, engine_env):
    # Arrange a checked-out state dir with an instance file present.
    dir_ = tmp_path / "dir"
    e = dict(engine_env)
    for k, v in e.items():
        os.environ[k] = v  # open_gate uses module-level git env via lib
    lib.state_checkout(str(dir_))
    inst = lib.instance_file(str(dir_), "rev", "pr-1")
    os.makedirs(os.path.dirname(inst), exist_ok=True)
    lib.dump_yaml(inst, {"protocol": "rev", "instance": "pr-1", "joined": False})

    qs = [{"id": "q1", "text": "Which DB?"}, {"id": "q2", "text": "Sync or async?"}]
    lib.open_gate(str(dir_), "rev", "pr-1", str(FIXTURES / "subpipeline-mini/protocol.json"),
                  "clarify", "abc123", "1", branch="B", questions=qs)

    gf = read_state_yaml(lib.state_file(str(dir_), "rev", "pr-1", branch="B", substate="clarify"))
    assert gf["gates"]["state"] == "open"
    assert gf["gates"]["questions"] == qs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate_data.py -k open_gate_branch_scoped -v`
Expected: FAIL — `open_gate() got an unexpected keyword argument 'branch'`.

- [ ] **Step 3: Implement**

Replace `open_gate` in `lib.py`:

```python
def open_gate(dir_, pid, instance, proto_path, gate_id, sha, pr, branch=None, questions=None):
    """Seed a gate state file (gates.state=open), emit the awaiting check-run, and
    refresh the status comment. `branch` scopes the gate to a sub-pipeline leg.
    `questions` (a list of {id,text}) turns this into a data-carrying gate whose
    comment lists them with the /answer syntax. Caller owns the cursor + cas_push."""
    sf = state_file(dir_, pid, instance, branch=branch,
                    substate=(gate_id if branch else None),
                    phase=(None if branch else None))
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    gates = {"state": "open", "history": []}
    if questions:
        gates["questions"] = questions
    dump_yaml(sf, {
        "protocol": pid, "instance": instance, "state": gate_id,
        "head_sha": sha, "gates": gates,
    })
    cr_name = f"{pid}/{branch}/{gate_id}" if branch else f"{pid}/{gate_id}"
    if questions:
        listed = "\n".join(f"{i+1}. `{q['id']}` — {q['text']}" for i, q in enumerate(questions))
        summary = ("Answer with `/answer <id>: <value>` (one or more per comment), e.g. "
                   f"`/answer {questions[0]['id']}: …`.")
        set_check_run(cr_name, sha, "in_progress", "", "Awaiting answers", summary)
        post_pr_comment(pr, f"❓ **{gate_id}** needs input:\n\n{listed}\n\n{summary}")
    else:
        set_check_run(cr_name, sha, "in_progress", "", "Awaiting human approval",
                      "Comment `/approve`, `/request-changes`, or `/reject` on this PR.")
    inf = instance_file(dir_, pid, instance)
    if os.path.isfile(inf):
        body = render_pipeline_status_body(dir_, pid, instance, proto_path)
        upsert_status_comment(inf, pr, body)
```

> The existing top-level callers pass no `branch`/`questions`, so `sf` resolves to the legacy `state_file(..., phase=gate_id)`? **No** — legacy top-level gates pass the gate id via the *phase* path. Preserve that: when `branch` is None, keep the original `state_file(dir_, pid, instance, phase=gate_id)`. Adjust the first lines:

```python
    if branch:
        sf = state_file(dir_, pid, instance, branch=branch, substate=gate_id)
    else:
        sf = state_file(dir_, pid, instance, phase=gate_id)
```

(Replace the combined `sf = …` above with this branchful form.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gate_data.py -k open_gate_branch_scoped -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: green — `test_join.py` opens top-level gates with the legacy signature.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test_gate_data.py
git commit -m "feat(engine): open_gate supports branch scope + questions rendering"
```

---

### Task 2: Advancing into a gate sub-state opens it (no dispatch, no join)

**Files:**
- Modify: `.github/agent-factory/engine/advance.py` (the Plan-1 sub-pipeline `done` block)
- Test: `tests/test_gate_data.py`

**Interfaces:**
- Consumes: `lib.branch_substates`, `lib.open_gate`, `lib.output_artifact_path`.
- Produces: when the next sub-state after a finished sub-agent is `kind:"gate"`, `advance.py` advances the branch cursor `sub_state` to it and **opens** the gate (reading `questions_from`'s persisted evidence for the questions) rather than seeding+dispatching an agent. The run ends; no `fire_join`. A gate with no `questions_from` opens as a plain approval gate.

- [ ] **Step 1: Write the failing test**

Extend the fixture first (Task 5 finalises it; for this test add the gate). Edit `tests/fixtures/subpipeline-mini/protocol.json` branch B `states` to `draft → clarify → finalize`:

```json
"states": [
  { "id": "draft", "kind": "agent", "workflow": "draft-agent",
    "evidence": "draft.evidence.schema.json", "max_iterations": 2,
    "checks": [{ "run": "always-pass", "on_fail": "iterate" }] },
  { "id": "clarify", "kind": "gate", "questions_from": "draft",
    "checks": [{ "run": "answers-coverage", "on_fail": "iterate" }] },
  { "id": "finalize", "kind": "agent", "workflow": "finalize-agent",
    "evidence": "finalize.evidence.schema.json", "max_iterations": 2,
    "inputs": [{ "from": "clarify", "as": "answers" },
               { "from": "draft", "as": "draft" }],
    "checks": [{ "run": "always-pass", "on_fail": "iterate" }] }
]
```

Then the test:

```python
def test_advance_into_gate_opens_it(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    # draft → done, emitting questions in evidence.
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "draft.json"
    ev.write_text(json.dumps({"questions": [{"id": "q1", "text": "Which DB?"}]}))
    e = dict(engine_env); e.update(BRANCH="B", SUBSTATE="draft",
                                   PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir", "pr-1", proto, v, ev, env=e)

    work = _clone(tmp_path, engine_env)
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "clarify"
    assert cursor["state"] == "review"      # leg NOT terminal; not joined
    gate = read_state_yaml(work / "subpipeline-mini/pr-1/B.clarify.yaml")
    assert gate["gates"]["state"] == "open"
    assert gate["gates"]["questions"][0]["id"] == "q1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate_data.py -k advance_into_gate -v`
Expected: FAIL — advance.py treats `clarify` like an agent sub-state (seeds an agent state file, re-dispatches), no gate opened.

- [ ] **Step 3: Implement gate-aware advance**

In `advance.py`, inside the Plan-1 sub-pipeline block, where `nxt_sub` is handled, branch on the next sub-state's kind. Replace the `if nxt_sub:` body:

```python
            if nxt_sub:
                cur["sub_state"] = nxt_sub
                cur["state"] = life_state
                lib.dump_yaml(cursor_sf, cur)
                nxt_state = lib.state_by_id(
                    {"states": lib.branch_substates(proto, branch)}, nxt_sub)
                if nxt_state and nxt_state.get("kind") == "gate":
                    # Open the gate (scoped to this branch); read questions from
                    # the source sub-state's persisted evidence.
                    questions = []
                    qfrom = nxt_state.get("questions_from")
                    if qfrom:
                        qpath = lib.output_artifact_path(dir_, pid, instance,
                                                         branch=branch, phase=(phase or None),
                                                         substate=qfrom, kind="evidence")
                        if os.path.isfile(qpath):
                            try:
                                questions = json.load(open(qpath)).get("questions", []) or []
                            except (json.JSONDecodeError, ValueError):
                                questions = []
                    lib.open_gate(dir_, pid, instance, proto_path, nxt_sub, sha, pr,
                                  branch=branch, questions=questions)
                    lib.cas_push(dir_, f"{instance}: branch {branch} {substate} done → gate {nxt_sub} open")
                    return
                # Otherwise: an agent sub-state → seed + dispatch (Plan 1 behaviour).
                nsf = lib.state_file(dir_, pid, instance, branch=branch,
                                     phase=(phase if phase else None), substate=nxt_sub)
                lib.dump_yaml(nsf, {
                    "protocol": pid, "instance": instance, "state": life_state,
                    "iteration": 1, "gates": {}, "head_sha": sha, "history": [],
                })
                lib.cas_push(dir_, f"{instance}: branch {branch} {substate} done → {nxt_sub}")
                redispatch = [
                    f"repos/{github_repository}/dispatches",
                    "-f", "event_type=protocol-continue",
                    "-F", f"client_payload[protocol]={pid}",
                    "-F", f"client_payload[instance]={instance}",
                    "-F", f"client_payload[branch]={branch}",
                    "-F", f"client_payload[substate]={nxt_sub}",
                ]
                if phase:
                    redispatch += ["-F", f"client_payload[phase]={phase}"]
                gh_api(*redispatch)
            else:
```

(The `else:` keeps the Plan-1 "last sub-state → leg done → fire_join" body unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gate_data.py -k advance_into_gate -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/advance.py tests/fixtures/subpipeline-mini/protocol.json tests/test_gate_data.py
git commit -m "feat(engine): advancing into a gate sub-state opens it"
```

---

### Task 3: The `answers-coverage` check

**Files:**
- Create: `tests/fixtures/subpipeline-mini/checks/answers-coverage.py`
- Test: `tests/test_gate_data.py`

**Interfaces:**
- Produces: a check executable following the standard ABI
  (`<check> <evidence.json> <diff.txt> <changed-files.txt>` → one JSON line, exit 0).
  Its `evidence.json` arg is a synthesized doc `{questions:[{id,…}], answers:{id:val}}`.
  Passes iff every question id has a non-empty answer; on failure `feedback` lists the missing ids.

- [ ] **Step 1: Write the failing test**

```python
from conftest import run_check, FIXTURES


def _cov(tmp_path, questions, answers):
    doc = tmp_path / "doc.json"
    doc.write_text(json.dumps({"questions": questions, "answers": answers}))
    empty = tmp_path / "e.txt"; empty.write_text("")
    return run_check(FIXTURES / "subpipeline-mini/checks/answers-coverage.py", doc, empty, empty)


def test_answers_coverage_pass(tmp_path):
    r = _cov(tmp_path, [{"id": "q1"}, {"id": "q2"}], {"q1": "pg", "q2": "async"})
    assert r["pass"] is True


def test_answers_coverage_missing(tmp_path):
    r = _cov(tmp_path, [{"id": "q1"}, {"id": "q2"}], {"q1": "pg"})
    assert r["pass"] is False
    assert "q2" in r["feedback"]


def test_answers_coverage_empty_value(tmp_path):
    r = _cov(tmp_path, [{"id": "q1"}], {"q1": "   "})
    assert r["pass"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate_data.py -k answers_coverage -v`
Expected: FAIL — check file missing (`FileNotFoundError`/json error).

- [ ] **Step 3: Implement the check**

```python
# tests/fixtures/subpipeline-mini/checks/answers-coverage.py
#!/usr/bin/env python3
import json, sys

doc = json.load(open(sys.argv[1]))
questions = doc.get("questions", []) or []
answers = doc.get("answers", {}) or {}
missing = [q["id"] for q in questions
           if not str(answers.get(q["id"], "")).strip()]
if missing:
    print(json.dumps({"check": "answers-coverage", "pass": False,
                      "feedback": "unanswered: " + ", ".join(missing)}))
else:
    print(json.dumps({"check": "answers-coverage", "pass": True, "feedback": ""}))
```

- [ ] **Step 4: Make it executable**

Run: `chmod +x tests/fixtures/subpipeline-mini/checks/answers-coverage.py`
Expected: no output.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_gate_data.py -k answers_coverage -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/subpipeline-mini/checks/answers-coverage.py tests/test_gate_data.py
git commit -m "feat(checks): answers-coverage check (every question answered)"
```

---

### Task 4: The `/answer` command — parse, accumulate, cover, advance

**Files:**
- Modify: `.github/agent-factory/engine/next.py` (add `do_answer`, wire the `answer` command)
- Test: `tests/test_gate_data.py`

**Interfaces:**
- Consumes: env `ANSWER_BODY` (the raw comment), `ANSWER_ACTOR`; `lib.resolve_executable`, `lib.output_artifact_path`, `lib.branch_substates`, `lib.state_file`.
- Produces: a `do_answer()` that:
  1. finds the branch whose cursor `sub_state` is a gate in state `open` (scans fanout branches; `/answer <branch> …` overrides when ambiguous);
  2. parses `qID: value` pairs from `ANSWER_BODY` (one or many lines);
  3. merges them into the gate's `answers.json` (load-existing → update → persist);
  4. runs the gate's `answers-coverage` check over `{questions, answers}`;
  5. on pass → gate `state: answered`, advance branch cursor `sub_state` to the next sub-state, seed+dispatch it (with answers resolvable as an input); on fail → keep gate open, post the missing-ids feedback.
  Emits a `noop` action on success/partial; never dispatches an agent itself except the next sub-state via `repository_dispatch`.

- [ ] **Step 1: Write the failing test**

```python
def _seed_open_gate(tmp_path, engine_env, proto):
    """Drive start → draft done so the clarify gate is open with one question."""
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "draft.json"
    ev.write_text(json.dumps({"questions": [{"id": "q1", "text": "Which DB?"}]}))
    e = dict(engine_env); e.update(BRANCH="B", SUBSTATE="draft",
                                   PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir", "pr-1", proto, v, ev, env=e)


def test_answer_completes_gate_and_advances(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    _seed_open_gate(tmp_path, engine_env, proto)
    e = dict(engine_env)
    e["ANSWER_BODY"] = "/answer q1: postgres"
    e["ANSWER_ACTOR"] = "alice"
    e["PR_HEAD_SHA"] = "abc123"
    out, err, rc = run_engine("next.py", tmp_path / "dir", "pr-1", proto, "answer", env=e)
    assert rc == 0, err

    work = _clone(tmp_path, engine_env)
    gate = read_state_yaml(work / "subpipeline-mini/pr-1/B.clarify.yaml")
    assert gate["gates"]["state"] == "answered"
    answers = json.loads((work / "subpipeline-mini/pr-1/B.clarify.answers.json").read_text())
    assert answers["answers"]["q1"] == "postgres"
    cursor = read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")
    assert cursor["sub_state"] == "finalize"


def test_answer_partial_keeps_gate_open(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    # Two questions; answer only one.
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "start", "abc123", env=engine_env)
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "draft.json"
    ev.write_text(json.dumps({"questions": [{"id": "q1", "text": "A?"}, {"id": "q2", "text": "B?"}]}))
    e = dict(engine_env); e.update(BRANCH="B", SUBSTATE="draft",
                                   PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir", "pr-1", proto, v, ev, env=e)

    e2 = dict(engine_env); e2["ANSWER_BODY"] = "/answer q1: x"; e2["ANSWER_ACTOR"] = "al"; e2["PR_HEAD_SHA"] = "abc123"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "answer", env=e2)
    work = _clone(tmp_path, engine_env)
    gate = read_state_yaml(work / "subpipeline-mini/pr-1/B.clarify.yaml")
    assert gate["gates"]["state"] == "open"   # still waiting on q2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate_data.py -k "answer_completes or answer_partial" -v`
Expected: FAIL — `next.py` has no `answer` command (`unknown command: answer`).

- [ ] **Step 3: Implement `do_answer` + wire the command**

Add to `next.py` (near `do_resolve_gate`):

```python
import re


def _find_open_gate_branch(proto, want_branch=""):
    """Return (branch, gate_substate) for the open data-gate, or (None, None)."""
    fo = lib.state_by_id(proto, "") if False else None
    for s in proto.get("states", []):
        if s.get("kind") == "fanout":
            fo = s
            break
    if not fo:
        return None, None
    for b in fo.get("branches", []):
        if want_branch and b["id"] != want_branch:
            continue
        cf = lib.state_file(DIR, PID, INSTANCE, branch=b["id"])
        if not os.path.isfile(cf):
            continue
        cur = lib.load_yaml(cf)
        sub = cur.get("sub_state", "")
        for s in b.get("states", []):
            if s["id"] == sub and s.get("kind") == "gate":
                gsf = lib.state_file(DIR, PID, INSTANCE, branch=b["id"], substate=sub)
                if os.path.isfile(gsf) and lib.load_yaml(gsf).get("gates", {}).get("state") == "open":
                    return b["id"], sub
    return None, None


def _parse_answers(body):
    """Parse `/answer qID: value` pairs (one or many lines). Returns {id: value}."""
    out = {}
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("/answer"):
            line = line[len("/answer"):].strip()
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*[:=]\s*(.+)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def do_answer():
    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    body = os.environ.get("ANSWER_BODY", "")
    actor = os.environ.get("ANSWER_ACTOR", "")
    # Optional explicit branch: `/answer <branch> qID: val` — first bare token.
    want = ""
    head = body[len("/answer"):].strip() if body.startswith("/answer") else body
    first = head.split()[0] if head.split() else ""
    if first and ":" not in first and "=" not in first:
        want = first

    branch, gate = _find_open_gate_branch(proto_data, want)
    if not branch:
        lib.post_pr_comment(pr, "No open question gate to answer right now.")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "", "reason": "answer: no open gate"}))
        return

    gsf = lib.state_file(DIR, PID, INSTANCE, branch=branch, substate=gate)
    gdata = lib.load_yaml(gsf)
    questions = gdata.get("gates", {}).get("questions", []) or []

    # Merge new answers into the persisted answers artifact.
    apath = lib.output_artifact_path(DIR, PID, INSTANCE, branch=branch,
                                     substate=gate, kind="answers")
    existing = {}
    if os.path.isfile(apath):
        try:
            existing = json.load(open(apath)).get("answers", {}) or {}
        except (json.JSONDecodeError, ValueError):
            existing = {}
    existing.update(_parse_answers(body))
    doc = {"questions": questions, "answers": existing}
    os.makedirs(os.path.dirname(apath), exist_ok=True)
    with open(apath, "w") as fh:
        json.dump(doc, fh)

    # Run the gate's answers-coverage check over the synthesized doc.
    gate_cfg = next(s for s in lib.branch_substates(proto_data, branch) if s["id"] == gate)
    check_run = (gate_cfg.get("checks", [{}])[0]).get("run", "answers-coverage")
    pdir = os.path.dirname(os.path.abspath(PROTO))
    res = lib.resolve_executable(f"{pdir}/checks", check_run, pdir, "")
    kind, path = res.split("\t", 1)
    empty = os.path.join(DIR, "_empty.txt")
    open(empty, "w").close()
    import subprocess as _sp
    cov = _sp.run([path, apath, empty, empty], text=True, capture_output=True)
    verdict = json.loads(cov.stdout) if cov.stdout.strip() else {"pass": False, "feedback": "no verdict"}

    gdata["gates"].setdefault("history", []).append({"actor": actor, "answers": list(_parse_answers(body))})
    if not verdict.get("pass"):
        lib.dump_yaml(gsf, gdata)
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} gate {gate} partial answers")
        lib.post_pr_comment(pr, f"📝 Recorded. Still needed — {verdict.get('feedback','')}.")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "", "reason": "answer: partial"}))
        return

    # Full coverage → close the gate, advance the branch cursor to the next sub-state.
    gdata["gates"]["state"] = "answered"
    lib.dump_yaml(gsf, gdata)
    nxt_sub = lib.next_substate_id(proto_data, branch, gate)
    cf = lib.state_file(DIR, PID, INSTANCE, branch=branch)
    cur = lib.load_yaml(cf)
    sha = gdata.get("head_sha", "") or HEAD_SHA
    if nxt_sub:
        cur["sub_state"] = nxt_sub
        cur["state"] = "review"
        lib.dump_yaml(cf, cur)
        nsf = lib.state_file(DIR, PID, INSTANCE, branch=branch, substate=nxt_sub)
        lib.dump_yaml(nsf, {"protocol": PID, "instance": INSTANCE, "state": "review",
                            "iteration": 1, "gates": {}, "head_sha": sha, "history": []})
        lib.set_check_run(f"{PID}/{branch}/{gate}", sha, "completed", "success", "Answered", f"Answered by @{actor}.")
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} gate {gate} answered → {nxt_sub}")
        lib.post_pr_comment(pr, f"✅ {gate} answered by @{actor}; continuing to {nxt_sub}.")
        gh_api = None  # next.py has no gh_api; dispatch via lib helper below
        lib.dispatch_continue(PID, INSTANCE, branch, nxt_sub)
    else:
        cur["state"] = "done"
        lib.dump_yaml(cf, cur)
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} gate {gate} answered → leg done")
        lib.fire_join_dispatch(PID, INSTANCE)  # see note
    print(json.dumps({"action": "noop", "iteration": 0, "feedback": "", "reason": "answer: complete"}))
```

> **Dispatch helpers:** `next.py` currently does not send repository_dispatch (only `advance.py` does, via `gh_api`). Add two thin helpers to `lib.py` so both writers share them:
>
> ```python
> def _gh_dispatch(event_type, fields):
>     if os.environ.get("ENGINE_LOCAL", "0") == "1":
>         sys.stderr.write(f"[ENGINE_LOCAL] dispatch {event_type} {fields}\n")
>         return
>     args = ["gh", "api", f"repos/{os.environ.get('GITHUB_REPOSITORY','')}/dispatches",
>             "-f", f"event_type={event_type}"]
>     for k, v in fields.items():
>         args += ["-F", f"client_payload[{k}]={v}"]
>     subprocess.run(args, text=True, capture_output=True)
>
> def dispatch_continue(pid, instance, branch, substate, phase=""):
>     f = {"protocol": pid, "instance": instance, "branch": branch, "substate": substate}
>     if phase:
>         f["phase"] = phase
>     _gh_dispatch("protocol-continue", f)
>
> def fire_join_dispatch(pid, instance):
>     _gh_dispatch("protocol-join", {"protocol": pid, "instance": instance})
> ```
>
> Remove the stray `gh_api = None` line in `do_answer` (it was a placeholder); call `lib.dispatch_continue` / `lib.fire_join_dispatch` directly. Under `ENGINE_LOCAL=1` (tests) these no-op, so the test asserts on state only.

Wire the command near the other command guards (`next.py:388-394`):

```python
if COMMAND == "answer":
    do_answer()
    sys.exit(0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gate_data.py -k "answer_completes or answer_partial" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/next.py .github/agent-factory/engine/lib.py tests/test_gate_data.py
git commit -m "feat(engine): /answer command — accumulate, cover, advance the branch"
```

---

### Task 5: End-to-end — gated leg runs `draft → clarify → finalize → join`

**Files:**
- Test: `tests/test_gate_data.py`

**Interfaces:**
- Consumes: Tasks 1-4 + Plan 2 (finalize consumes the answers input).
- Produces: a full walk — start → draft done → gate open → `/answer` → finalize dispatched with `inputs` containing `answers` → finalize done → leg done → join joins (with flat branch A also done).

- [ ] **Step 1: Write the end-to-end test**

```python
def test_gated_leg_full_walk(tmp_path, engine_env):
    proto = FIXTURES / "subpipeline-mini/protocol.json"
    _seed_open_gate(tmp_path, engine_env, proto)            # start + draft done + gate open
    # Answer the gate.
    e = dict(engine_env); e["ANSWER_BODY"] = "/answer q1: postgres"; e["ANSWER_ACTOR"] = "al"; e["PR_HEAD_SHA"] = "abc123"
    run_engine("next.py", tmp_path / "dir", "pr-1", proto, "answer", env=e)

    # finalize resume → action carries the answers input.
    out, err, rc = run_engine("next.py", tmp_path / "dir", "pr-1", proto, "continue",
                              env=engine_env, branch="B", substate="finalize")
    action = json.loads(out)
    names = {i["as"] for i in action.get("inputs", [])}
    assert "answers" in names and "draft" in names

    # finalize → done → leg done.
    v = tmp_path / "v.json"; v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / "fin.json"; ev.write_text("{}")
    ef = dict(engine_env); ef.update(BRANCH="B", SUBSTATE="finalize",
                                     PR_HEAD_SHA="abc123", AGENT_RUN_ID="r")
    run_engine("advance.py", tmp_path / "dir", "pr-1", proto, v, ev, env=ef)
    work = _clone(tmp_path, engine_env)
    assert read_state_yaml(work / "subpipeline-mini/pr-1/B.yaml")["state"] == "done"
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_gate_data.py -k gated_leg_full_walk -v`
Expected: PASS.

- [ ] **Step 3: Run the full suite**

Run: `pytest tests/ -q`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_gate_data.py
git commit -m "test(engine): e2e gated leg draft->clarify->finalize->join"
```

---

### Task 6: `/answer` trigger + orchestrator command mapping (integration)

**Files:**
- Modify: `.github/agent-factory/protocols/code-review/protocol.json` (only when the real protocol adopts a gate; for the engine, add the trigger pattern to the fixture is enough — but document the wiring)
- Modify: `.github/workflows/agentic-orchestrator.yml` (map a `/answer` comment to the `answer` command, forwarding `ANSWER_BODY`/`ANSWER_ACTOR` via `env:`)
- Modify: `docs/STATUS.md`

**Interfaces:**
- Consumes: `lib.match_trigger` already maps `comment_prefix:"/answer"` → `command:"answer"` once a protocol declares it.
- Produces: the orchestrator recognises `/answer …` PR comments, routes to the engine with `COMMAND=answer`, and passes the raw comment body + actor as env (never interpolated into `run:`).

- [ ] **Step 1: Confirm `match_trigger` needs no change**

Run: `pytest tests/test_correlation.py tests/test_engine.py -q` (routing/trigger coverage)
Expected: green; `match_trigger` already handles arbitrary `comment_prefix`.

- [ ] **Step 2: Add the orchestrator mapping**

In `agentic-orchestrator.yml`, in the comment-routing step, add `/answer` alongside `/review`/`/approve`. Forward the comment body via `env: ANSWER_BODY: ${{ github.event.comment.body }}` and `ANSWER_ACTOR: ${{ github.event.comment.user.login }}` to the engine call — never inside a `run:` string.

- [ ] **Step 3: Document in STATUS.md**

Add a "## Data-carrying gate" note: questions evidence → `/answer` accumulation → `answers-coverage` → answers artifact consumed by the next sub-state; the security rule for the untrusted comment body.

- [ ] **Step 4: Lint**

Run: `actionlint .github/workflows/agentic-orchestrator.yml` (if available).
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/agentic-orchestrator.yml docs/STATUS.md
git commit -m "feat(workflow): route /answer comments to the engine answer command"
```

---

## Self-Review (Plan 3)

- **Spec coverage:** §4 data-carrying gate → Tasks 1 (render), 2 (open on advance), 3 (coverage check), 4 (`/answer` accumulate+advance), 5 (e2e), 6 (trigger wiring). Trust-zone rule (untrusted `/answer` body via env) → Tasks 4, 6.
- **Placeholder scan:** the `gh_api = None` placeholder in Task 4's first draft is explicitly called out and removed in the same step (replaced by `lib.dispatch_continue`/`lib.fire_join_dispatch`). No other placeholders.
- **Type consistency:** `open_gate(..., branch=, questions=)`, `output_artifact_path(kind="answers")`, `next_substate_id`, `branch_substates`, `dispatch_continue`/`fire_join_dispatch` consistent with Plans 1-2. Gate states: `open → answered` (data gate) vs legacy `open → approved/changes_requested/rejected` (untouched).

## Plan 3 → Plan 4 handoff

Plan 4 (merge) is independent of the gate; it consumes Plan 1 (leg terminal) + Plan 2 (`resolve_inputs` with branch-id refs). After Plan 3, branch B's leg output is `finalize`'s evidence — exactly what `branch_output_substate(proto, "B")` returns for the combine.
