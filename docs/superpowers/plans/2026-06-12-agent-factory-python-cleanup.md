# Agent-Factory Python Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Relocate the engine + protocols under a vendored `.github/agent-factory/` unit and rewrite the entire engine, checks, publish hooks, and test suite from bash to Python — with zero behavior change, proven by the existing tests at every step.

**Architecture:** Four sequential phases with a hard "all tests green" gate between each. The existing bash tests are black-box ABI tests (positional args in, JSON on stdout, state read via yq), so they validate a Python engine unchanged and serve as the regression anchor through phases 1–3. Only phase 4 replaces them with pytest. State stays YAML, read/written via PyYAML; `git`/`gh` via `subprocess`; all `jq` logic becomes stdlib `json`.

**Tech Stack:** Python 3 (stdlib + PyYAML), `git` + `gh` CLIs via subprocess, pytest (dev-only). GitHub Actions workflows stay YAML.

---

## Porting convention (read first — applies to every "port" task)

Phases 1–3 are a **behavior-preserving refactor**. For any task that says "port `X.sh` → `X.py`":

1. Read the existing bash file in full — it is the behavior specification.
2. Reproduce its behavior in Python, **preserving the ABI exactly**: same positional args, same env vars, same stdout (one JSON object), same exit-code contract.
3. Translate the tooling: `jq` → stdlib `json`; `yq` read → `yaml.safe_load`; `yq` write → the `dump_yaml` helper from Task 2.1; `git`/`gh` → `subprocess.run([...], check=...)`.
4. The **oracle** is the named existing bash test suite. The task is done when that suite passes unchanged (except for the `.sh`→`.py` path/extension edits the task specifies).
5. Do **not** add features, change output wording, or alter state shape. If you find a behavior that seems wrong, leave it and note it — parity first.

"Run the suite" always means from the repo root:
```bash
bash tests/<suite>.sh && echo ALL-GREEN
```
Expected output ends with `ALL-GREEN` (each suite exits non-zero on failure).

---

## File Structure

```
.github/agent-factory/
  VERSION                         # "0.1.0"
  README.md                       # vendored-unit note
  engine/
    lib.py                        # importable module + `python3 lib.py <subcmd>` CLI
    next.py advance.py run-checks.py join.py   # executables, import lib
  protocols/
    grumpy/checks/{schema-valid.py, rubric-coverage.py, traces-exist-in-diff.py}
    grumpy/publish/publish-review-from-evidence.py        # single self-contained hook
    multi-grumpy/checks/{schema-valid.py, rubric-coverage.py, traces-exist-in-diff.py}
    multi-grumpy/publish/{_review.py, publish-grumpy.py, publish-security.py}
tests/                            # phase 4: conftest.py + test_*.py (pytest), bash removed
```

---

# PHASE 1 — Relocate to `.github/agent-factory/` + VERSION

**Gate:** full bash suite green after the move (engine still bash, only paths changed).

### Task 1.1: Move engine + protocols, add VERSION/README, fix all paths

**Files:**
- Move: `.github/engine/` → `.github/agent-factory/engine/`
- Move: `protocols/` → `.github/agent-factory/protocols/`
- Create: `.github/agent-factory/VERSION`, `.github/agent-factory/README.md`
- Modify: `.github/workflows/orchestrator.yml`, `.github/workflows/protocol-join.yml`, all `tests/*.sh`, `CLAUDE.md`, `docs/STATUS.md`, `docs/HOW-IT-WORKS.md`, `.github/agent-factory/protocols/grumpy/README.md`

- [ ] **Step 1: Move the two trees with git mv (preserves history)**

```bash
mkdir -p .github/agent-factory
git mv .github/engine .github/agent-factory/engine
git mv protocols .github/agent-factory/protocols
```

- [ ] **Step 2: Add VERSION and README**

```bash
printf '0.1.0\n' > .github/agent-factory/VERSION
```

Create `.github/agent-factory/README.md`:
```markdown
# Agent-Factory

Vendored agentic protocol engine. Copy this whole directory into a repo's
`.github/`; workflows live in `.github/workflows/`.

- `engine/` — GENERIC state machine. Do not edit to add a protocol.
- `protocols/<name>/` — self-contained protocols you author/clone.
- `VERSION` — the vendored cut (semver). Bump on engine changes.

Runtime deps: Python 3 + PyYAML; `git` and `gh` on PATH.
```

- [ ] **Step 3: Update workflow path references**

In `.github/workflows/orchestrator.yml` and `.github/workflows/protocol-join.yml`, replace every occurrence:
- `.github/engine/` → `.github/agent-factory/engine/`
- `protocols/multi-grumpy/` → `.github/agent-factory/protocols/multi-grumpy/`

Verify none missed:
```bash
grep -rn "\.github/engine\|[^/]protocols/" .github/workflows/ ; echo "exit:$?"
```
Expected: no matches for the old paths (grep exit 1).

- [ ] **Step 4: Update test path constants**

In every `tests/*.sh`, replace `.github/engine/` → `.github/agent-factory/engine/` and `protocols/` → `.github/agent-factory/protocols/` (constants like `PROTO=`, `NEXT=`, `RC=`, `JOIN=`, `STUB_HOOK=`, `source` lines, and inline check/publish paths).

```bash
grep -rln "\.github/engine/\|[^/]protocols/" tests/   # files still needing edits
```
Edit until that prints nothing.

- [ ] **Step 5: Update docs prose**

In `CLAUDE.md`, `docs/STATUS.md`, `docs/HOW-IT-WORKS.md`, and `.github/agent-factory/protocols/grumpy/README.md`, update path mentions `.github/engine/` → `.github/agent-factory/engine/` and `protocols/` → `.github/agent-factory/protocols/`. (Leave the untracked `docs/superpowers/plans/2026-06-12-branch-scoped-params.md` historical file as-is.)

- [ ] **Step 6: Run the full suite**

Run:
```bash
for t in tests/test-*.sh; do echo "== $t =="; bash "$t" || echo "FAILED: $t"; done
```
Expected: every suite passes, no `FAILED:` lines.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: relocate engine+protocols to .github/agent-factory/ with VERSION"
```

---

# PHASE 2 — Engine bash → Python

**Gate:** the bash suite (with `.sh`→`.py` invocation edits) green against the Python engine.
All engine paths below are under `.github/agent-factory/engine/`.

### Task 2.1: Port `lib.py` (module + CLI), drive it from the two function-level suites

**Files:**
- Create: `.github/agent-factory/engine/lib.py`
- Keep (for now): `.github/agent-factory/engine/lib.sh`
- Modify: `tests/test-correlation.sh`, `tests/test-status-comment.sh`

Port every function from `lib.sh`: `protocol_id`, `state_file`, `instance_file`, `state_checkout`, `cas_push`, `resolve_executable`, `set_check_run`, `match_run_by_cid`, the status-comment upsert, `render_fanout_status_body`. Two surfaces: importable functions **and** a subcommand CLI for the helpers the orchestrator/tests call from the shell.

- [ ] **Step 1: Write `lib.py` scaffolding — YAML helpers, subprocess helpers, CLI dispatch**

```python
#!/usr/bin/env python3
"""Engine shared library. Importable by the engine scripts AND a thin CLI
(`python3 lib.py <subcommand> ...`) for helpers the orchestrator calls inline.
Ports .github/agent-factory/engine/lib.sh 1:1 — behavior must not change."""
import json
import os
import subprocess
import sys
import yaml

STATE_REMOTE = os.environ.get("STATE_REMOTE", "")
STATE_BRANCH = os.environ.get("STATE_BRANCH", "agentic-state")
GIT_ID = ["-c", "user.email=engine@agentic-protocol-poc",
          "-c", "user.name=protocol-engine"]

def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}

def dump_yaml(path, data):
    # sort_keys=False + block style keeps a stable, human-readable git trail.
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)

def git(dir_, *args, check=True, capture=False):
    return subprocess.run(["git", "-C", dir_, *args],
                          check=check, text=True,
                          capture_output=capture)

def protocol_id(proto_path):
    return load_yaml(proto_path)["name"]

def state_file(d, pid, instance, branch=None):
    return f"{d}/{pid}/{instance}/{branch}.yaml" if branch else f"{d}/{pid}/{instance}.yaml"

def instance_file(d, pid, instance):
    return f"{d}/{pid}/{instance}/_instance.yaml"
```

- [ ] **Step 2: Port the remaining functions into `lib.py`**

Port `state_checkout`, `cas_push` (preserve the **fast-forward-only, never-force-push** CAS semantics, including the single rebase-retry), `resolve_executable` (extension-agnostic glob with the same ambiguity/empty errors), `set_check_run`, `match_run_by_cid`, the status-comment upsert, and `render_fanout_status_body`. Use `git()` and `gh` via `subprocess`; all `jq` logic becomes `json`. Reproduce `lib.sh` behavior exactly — `lib.sh` is the spec.

- [ ] **Step 3: Add the CLI dispatch at the bottom of `lib.py`**

```python
def _cli(argv):
    cmd, args = argv[0], argv[1:]
    if cmd == "protocol-id":          print(protocol_id(args[0]))
    elif cmd == "state-file":         print(state_file(*args))
    elif cmd == "instance-file":      print(instance_file(*args))
    elif cmd == "set-check-run":      set_check_run(*args)
    elif cmd == "match-run-by-cid":   print(match_run_by_cid(*args))
    elif cmd == "render-fanout-status-body": print(render_fanout_status_body(*args))
    elif cmd == "upsert-status-comment":     upsert_status_comment(*args)
    elif cmd == "cas-push":           cas_push(*args)
    else:
        sys.stderr.write(f"lib.py: unknown subcommand {cmd}\n"); sys.exit(2)

if __name__ == "__main__":
    _cli(sys.argv[1:])
```
Make it executable: `chmod +x .github/agent-factory/engine/lib.py`.

- [ ] **Step 4: Repoint the two function-level suites at the CLI**

`tests/test-correlation.sh` and `tests/test-status-comment.sh` currently `source lib.sh` and call functions directly. A bash script can't source Python, so replace those calls with the `lib.py` CLI. Example — `match_run_by_cid "$cid" "$runs"` becomes:
```bash
OUT=$(python3 .github/agent-factory/engine/lib.py match-run-by-cid "$cid" "$runs")
```
Replace each sourced-function call in both suites with the equivalent `python3 .../lib.py <subcommand>` invocation. Keep the assertions identical.

- [ ] **Step 5: Run the oracle suites**

Run:
```bash
bash tests/test-correlation.sh && bash tests/test-status-comment.sh && echo ALL-GREEN
```
Expected: ends with `ALL-GREEN`.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py tests/test-correlation.sh tests/test-status-comment.sh
git commit -m "refactor(engine): port lib.sh -> lib.py (module + CLI); drive fn-level suites via CLI"
```

### Task 2.2: Port `next.py` (the planner)

**Files:**
- Create: `.github/agent-factory/engine/next.py`
- Modify: `tests/test-engine.sh`, `tests/test-fanout-e2e.sh`, `.github/workflows/orchestrator.yml` (the `next.sh` invocation only)

- [ ] **Step 1: Port `next.sh` → `next.py`**

`import lib` (same dir). Preserve the ABI: `next.py <state_workdir> <instance-key> <protocol.json> <command> [head_sha]`, env `BRANCH`, action JSON on stdout. Reproduce both paths: `BRANCH` empty → v1 single-agent planning; `BRANCH` set / fan-out → seed `_instance.yaml` + emit `run-fanout`/branch dispatch list. `yq -n '...'` state seeding becomes building a dict and `lib.dump_yaml`. `chmod +x`.

- [ ] **Step 2: Repoint invocations `.sh`→`.py`**

In `tests/test-engine.sh` (`NEXT=` constant), `tests/test-fanout-e2e.sh`, and the orchestrator `next.sh` call site: `next.sh` → `next.py` (invoke as `python3 .../next.py ...` or rely on the shebang + exec bit — match how the suite invokes it; the `NEXT=` constant pointing at the `.py` with exec bit works).

- [ ] **Step 3: Run the planner oracles**

Run:
```bash
bash tests/test-engine.sh && bash tests/test-fanout-e2e.sh && echo ALL-GREEN
```
Expected: ends with `ALL-GREEN`. (`test-engine.sh` also exercises `advance.sh`, still bash here — that's fine, both coexist.)

- [ ] **Step 4: Commit**

```bash
git add .github/agent-factory/engine/next.py tests/test-engine.sh tests/test-fanout-e2e.sh .github/workflows/orchestrator.yml
git commit -m "refactor(engine): port next.sh -> next.py"
```

### Task 2.3: Port `run-checks.py`

**Files:**
- Create: `.github/agent-factory/engine/run-checks.py`
- Modify: `tests/test-runchecks.sh`, `.github/workflows/orchestrator.yml` (the `run-checks.sh` call site)

- [ ] **Step 1: Port `run-checks.sh` → `run-checks.py`**

Preserve ABI: `run-checks.py <protocol.json> <state-id> <evidence.json> <diff.txt> <changed-files.txt>`, env `BRANCH`. Reproduce: resolve each check via `lib.resolve_executable`, resolve node-scoped `params` (branch's when `BRANCH` set, else state's), export it as `CHECK_PARAMS` in the child env, invoke the check, collect verdicts → JSON on stdout. The runner must **not** interpret check contents. `chmod +x`.

- [ ] **Step 2: Repoint `tests/test-runchecks.sh` (`RC=`) and the orchestrator call site to `run-checks.py`.**

- [ ] **Step 3: Run the oracle**

Run: `bash tests/test-runchecks.sh && echo ALL-GREEN`
Expected: ends with `ALL-GREEN`.

- [ ] **Step 4: Commit**

```bash
git add .github/agent-factory/engine/run-checks.py tests/test-runchecks.sh .github/workflows/orchestrator.yml
git commit -m "refactor(engine): port run-checks.sh -> run-checks.py"
```

### Task 2.4: Port `advance.py` (the writer)

**Files:**
- Create: `.github/agent-factory/engine/advance.py`
- Modify: `tests/test-engine.sh`, `tests/test-fanout-e2e.sh`, `.github/workflows/orchestrator.yml` (the `advance.sh` call site)

- [ ] **Step 1: Port `advance.sh` → `advance.py`**

Preserve ABI: `advance.py <state_workdir> <instance-key> <protocol.json> <verdicts.json> <evidence.json>`, env `BRANCH`, `PR`, `AGENT_RUN_ID`, `ENGINE_LOCAL`, `PUBLISH_TOKEN`, `GITHUB_REPOSITORY`. Reproduce exactly: initial-state seeding, read `iteration`, mutate history/state, the done/iterate/fail branching, `run_publish_hook` (resolve + run the protocol publish hook; same neutral-on-error fallbacks), `render_status_body` / `render_fanout_status_body`, then `cas_push`. All `yq -i` mutations become `load_yaml` → mutate dict → `dump_yaml`; the `yq -o=json '.history' | jq` rendering becomes pure Python over the loaded list. `chmod +x`.

- [ ] **Step 2: Repoint invocations**

`tests/test-engine.sh` (the `advance.sh` calls and the `STUB_HOOK`/`.test-stub-proto.json` setup), `tests/test-fanout-e2e.sh`, and the orchestrator `advance.sh` call site → `advance.py`. (The grumpy publish hook is still `.sh` in this phase — leave that path until Phase 3.)

- [ ] **Step 3: Run the oracles**

Run: `bash tests/test-engine.sh && bash tests/test-fanout-e2e.sh && echo ALL-GREEN`
Expected: ends with `ALL-GREEN`.

- [ ] **Step 4: Commit**

```bash
git add .github/agent-factory/engine/advance.py tests/test-engine.sh tests/test-fanout-e2e.sh .github/workflows/orchestrator.yml
git commit -m "refactor(engine): port advance.sh -> advance.py"
```

### Task 2.5: Port `join.py`

**Files:**
- Create: `.github/agent-factory/engine/join.py`
- Modify: `tests/test-join.sh`, `tests/test-fanout-e2e.sh`, `.github/workflows/protocol-join.yml` (the `join.sh` call site)

- [ ] **Step 1: Port `join.sh` → `join.py`**

Preserve ABI: `join.py <state_workdir> <instance-key> <protocol.json>`. Reproduce: idempotency guard (`joined` already true → exit 0), read each branch's `state`, evaluate the AND-barrier (process axis: all branches `done`), render the final status comment, set `joined = true` in `_instance.yaml`, CAS-push. `chmod +x`.

- [ ] **Step 2: Repoint `tests/test-join.sh` (`JOIN=` + its `source lib.sh` → `lib.py` CLI where it asserts functions), `tests/test-fanout-e2e.sh`, and the orchestrator-join call site to `join.py`.**

Note: `tests/test-join.sh` is not `chmod +x` — keep invoking it as `bash tests/test-join.sh`.

- [ ] **Step 3: Run the oracles**

Run: `bash tests/test-join.sh && bash tests/test-fanout-e2e.sh && echo ALL-GREEN`
Expected: ends with `ALL-GREEN`.

- [ ] **Step 4: Commit**

```bash
git add .github/agent-factory/engine/join.py tests/test-join.sh tests/test-fanout-e2e.sh .github/workflows/protocol-join.yml
git commit -m "refactor(engine): port join.sh -> join.py"
```

### Task 2.6: Convert orchestrator inline `source lib.sh` sites; delete `lib.sh`

**Files:**
- Modify: `.github/workflows/orchestrator.yml`, `.github/workflows/protocol-join.yml`
- Delete: `.github/agent-factory/engine/lib.sh`

- [ ] **Step 1: Replace inline function calls with `lib.py` CLI**

In `orchestrator.yml`, the three `source .github/agent-factory/engine/lib.sh` steps call shell functions inline. Replace each:
- `set_check_run "multi-grumpy" "$SHA" in_progress "" "..." "..."` → `python3 .github/agent-factory/engine/lib.py set-check-run "multi-grumpy" "$SHA" in_progress "" "..." "..."`
- The "Ensure shared status comment" block (`instance_file` → `yq` read id → `render_fanout_status_body` → `upsert_status_comment` → `cas_push`) → a single `python3 .github/agent-factory/engine/lib.py ensure-status-comment "$STATE_DIR" multi-grumpy "$INSTANCE" "$PROTO" "$PR"` subcommand. Add that `ensure-status-comment` subcommand to `lib.py`'s `_cli` (it encapsulates the create-once guard + render + upsert + cas_push). Remove the `source` lines.

Do the same for any inline `source ... lib.sh` in `protocol-join.yml`.

- [ ] **Step 2: Confirm nothing sources the bash lib anymore, then delete it**

```bash
grep -rn "lib.sh" .github/ tests/ ; echo "exit:$?"
```
Expected: no matches (exit 1). Then:
```bash
git rm .github/agent-factory/engine/lib.sh
```

- [ ] **Step 3: Run the full suite**

Run:
```bash
for t in tests/test-*.sh; do echo "== $t =="; bash "$t" || echo "FAILED: $t"; done
```
Expected: no `FAILED:` lines. The engine is now fully Python.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(engine): convert orchestrator inline lib calls to lib.py CLI; remove lib.sh"
```

---

# PHASE 3 — Checks + publish bash → Python

**Gate:** `test-checks.sh`, `test-publish.sh`, `test-runchecks.sh` green, plus the full suite.

### Task 3.1: Port grumpy `schema-valid.py`

**Files:**
- Create: `.github/agent-factory/protocols/grumpy/checks/schema-valid.py`
- Delete: `.github/agent-factory/protocols/grumpy/checks/schema-valid.sh`
- Modify: `tests/test-checks.sh`, `tests/test-runchecks.sh` (paths → `.py`)

- [ ] **Step 1: Write `schema-valid.py`**

Shebang `#!/usr/bin/env python3`, stdlib only. ABI: `schema-valid.py <evidence.json> <diff.txt> <changed-files.txt>`, env `CHECK_PARAMS`, prints `{"check":"schema-valid","pass":<bool>,"feedback":<str>}`, **always exit 0**. Reproduce the bash logic exactly:
- evidence missing/invalid JSON → `pass:false`, "evidence file is missing or not valid JSON".
- `.files` not an array → "top-level .files array is missing".
- any `.files[]` not an object with a `verdicts` array → the same message.
- `CHECK_PARAMS.categories` absent/empty/non-array → "schema-valid: no categories in CHECK_PARAMS ...".
- Per `file × verdict`, accumulate `; `-joined errors for: illegal category; illegal verdict (not issues-found/none-found); issues-found with no findings; finding with empty `existing_code` or `comment`; finding missing a valid line/side anchor (`side` ∈ {RIGHT,LEFT}, `line` int ≥ 1, `start_line` null or int ≥ 1); none-found with no `examined`.
- Non-empty errors → `pass:false` with the joined string, else `pass:true` with `""`.

```python
#!/usr/bin/env python3
import json, os, sys

def emit(ok, feedback):
    print(json.dumps({"check": "schema-valid", "pass": ok, "feedback": feedback}))
    sys.exit(0)

def main():
    ev_path = sys.argv[1]
    try:
        with open(ev_path) as f: ev = json.load(f)
    except Exception:
        emit(False, "evidence file is missing or not valid JSON")
    if not isinstance(ev.get("files"), list):
        emit(False, "top-level .files array is missing")
    for fe in ev["files"]:
        if not (isinstance(fe, dict) and isinstance(fe.get("verdicts"), list)):
            emit(False, "a .files entry is not an object with a verdicts array; "
                        "check that every file is an object and verdicts is an array")
    try:
        params = json.loads(os.environ.get("CHECK_PARAMS", "") or "{}")
    except Exception:
        params = {}
    cats = params.get("categories")
    if not (isinstance(cats, list) and len(cats) > 0):
        emit(False, "schema-valid: no categories in CHECK_PARAMS "
                    "(engine must pass params.categories for this check's node)")
    errs = []
    for fe in ev["files"]:
        p = fe.get("path")
        for v in fe.get("verdicts", []):
            c = v.get("category"); verdict = v.get("verdict")
            findings = v.get("findings") or []
            if c not in cats:
                errs.append(f"illegal category {c} in {p}")
            elif verdict not in ("issues-found", "none-found"):
                errs.append(f"illegal verdict {verdict} for {c} × {p}")
            elif verdict == "issues-found" and len(findings) == 0:
                errs.append(f"issues-found with no findings: {c} × {p}")
            elif verdict == "issues-found" and not all(
                    len(fd.get("existing_code") or "") > 0 and len(fd.get("comment") or "") > 0
                    for fd in findings):
                errs.append(f"finding with empty existing_code or comment: {c} × {p}")
            elif verdict == "issues-found" and not all(
                    fd.get("side") in ("RIGHT", "LEFT")
                    and isinstance(fd.get("line"), int) and not isinstance(fd.get("line"), bool) and fd.get("line") >= 1
                    and (fd.get("start_line") is None
                         or (isinstance(fd.get("start_line"), int) and not isinstance(fd.get("start_line"), bool) and fd.get("start_line") >= 1))
                    for fd in findings):
                errs.append(f"finding missing valid line/side anchor: {c} × {p}")
            elif verdict == "none-found" and len(v.get("examined") or []) == 0:
                errs.append(f"none-found with no examined identifiers: {c} × {p}")
    emit(len(errs) == 0, "; ".join(errs))

if __name__ == "__main__":
    main()
```
Note: the explicit `not isinstance(..., bool)` guards reproduce jq's numeric type check (in Python `True == 1`, which jq does not treat as a number). `chmod +x`.

- [ ] **Step 2: Delete the bash check and repoint tests**

```bash
git rm .github/agent-factory/protocols/grumpy/checks/schema-valid.sh
```
In `tests/test-checks.sh` and `tests/test-runchecks.sh`, change any direct `schema-valid.sh` path to `schema-valid.py` (resolution by `protocol.json` is extension-agnostic, so the `.json` itself is unchanged).

- [ ] **Step 3: Run the oracles**

Run: `bash tests/test-checks.sh && bash tests/test-runchecks.sh && echo ALL-GREEN`
Expected: ends with `ALL-GREEN`.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(grumpy): port schema-valid.sh -> schema-valid.py"
```

### Task 3.2: Port multi-grumpy `schema-valid.py` (identical copy)

**Files:**
- Create: `.github/agent-factory/protocols/multi-grumpy/checks/schema-valid.py`
- Delete: `.github/agent-factory/protocols/multi-grumpy/checks/schema-valid.sh`
- Modify: `tests/test-checks.sh`, `tests/test-runchecks.sh` if they reference the multi-grumpy copy

- [ ] **Step 1: Copy the grumpy `schema-valid.py` verbatim into the multi-grumpy checks dir** (self-contained protocols — identical content is intended).

```bash
cp .github/agent-factory/protocols/grumpy/checks/schema-valid.py \
   .github/agent-factory/protocols/multi-grumpy/checks/schema-valid.py
chmod +x .github/agent-factory/protocols/multi-grumpy/checks/schema-valid.py
git rm .github/agent-factory/protocols/multi-grumpy/checks/schema-valid.sh
```

- [ ] **Step 2: Confirm parity and repoint any multi-grumpy test paths to `.py`**

```bash
diff .github/agent-factory/protocols/grumpy/checks/schema-valid.py \
     .github/agent-factory/protocols/multi-grumpy/checks/schema-valid.py && echo IDENTICAL
```
Expected: `IDENTICAL`.

- [ ] **Step 3: Run the oracles**

Run: `bash tests/test-checks.sh && bash tests/test-runchecks.sh && echo ALL-GREEN`
Expected: ends with `ALL-GREEN`.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(multi-grumpy): port schema-valid.sh -> schema-valid.py"
```

### Task 3.3: Port grumpy publish hook → Python (self-contained)

**Files:**
- Create: `.github/agent-factory/protocols/grumpy/publish/publish-review-from-evidence.py`
- Delete: `.github/agent-factory/protocols/grumpy/publish/publish-review-from-evidence.sh`
- Modify: `tests/test-publish.sh`, `tests/test-engine.sh` (the `cp ...publish...sh` stub-copy line and `STUB_HOOK`/`HOOK` paths)

- [ ] **Step 1: Write the Python publish hook**

ABI: `<hook> <evidence.json> <instance-key>`; env `ENGINE_LOCAL`, `GITHUB_REPOSITORY`, `PUBLISH_TOKEN`, `PR`; prints `{"conclusion","summary"}`; **exit 0 on success, nonzero on hard failure** (load-bearing). Reproduce 1:1: derive `event` (REQUEST_CHANGES if any `issues-found`, else APPROVE), build `comments[]` (one per issues-found finding: `{path, side, line, body}`, plus `{start_line, start_side: side}` when ranged), the `body`/`conclusion`/`summary` wording, `ENGINE_LOCAL` dry-run to stderr, else fetch head SHA via `gh`, pin `commit_id`, POST one review, surface a failed POST's combined response, and the APPROVE→COMMENT fallback. Shell to `gh` via `subprocess`.

```python
#!/usr/bin/env python3
"""Grumpy publication (zone 4). Ports publish-review-from-evidence.sh 1:1."""
import json, os, subprocess, sys

def gh_api(path, method=None, input_json=None, token=None):
    cmd = ["gh", "api", path]
    if method: cmd += ["--method", method, "--input", "-"]
    env = dict(os.environ)
    if token: env["GH_TOKEN"] = token
    return subprocess.run(cmd, input=input_json, text=True,
                          capture_output=True, env=env)

def main():
    evid = sys.argv[1]
    with open(evid) as f: ev = json.load(f)
    issues = any(v.get("verdict") == "issues-found"
                 for fe in ev.get("files", []) for v in fe.get("verdicts", []))
    event = "REQUEST_CHANGES" if issues else "APPROVE"
    comments = []
    for fe in ev.get("files", []):
        for v in fe.get("verdicts", []):
            if v.get("verdict") != "issues-found": continue
            for fd in v.get("findings", []):
                c = {"path": fe["path"], "side": fd["side"], "line": fd["line"], "body": fd["comment"]}
                if fd.get("start_line"):
                    c["start_line"] = fd["start_line"]; c["start_side"] = fd["side"]
                comments.append(c)
    n = len(comments); nfiles = len({c["path"] for c in comments})
    if event == "REQUEST_CHANGES":
        body = (f"\U0001f624 Grumpy protocol review — {n} issue(s) across {nfiles} file(s), "
                "evidence verified by deterministic checks. Griping inline.")
        conclusion, summary = "failure", "Grumpy requested changes — resolve them before merging. See the inline comments."
    else:
        body = ("\U0001f624 Fine. I examined every file against every category and found nothing "
                "worth complaining about. Don't get used to it.")
        conclusion, summary = "success", "Grumpy examined every file × category and found nothing to fix."
    base = {"event": event, "body": body, "comments": comments}
    repo, pr, token = os.environ["GITHUB_REPOSITORY"], os.environ["PR"], os.environ.get("PUBLISH_TOKEN", "")
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] POST repos/{repo}/pulls/{pr}/reviews\n")
        sys.stderr.write(json.dumps(base, indent=2) + "\n")
    else:
        head = gh_api(f"repos/{repo}/pulls/{pr}", token=token)
        commit = head.stdout.strip() if head.returncode == 0 else ""
        commit = subprocess.run(["jq", "-r", ".head.sha"], input=head.stdout, text=True,
                                capture_output=True).stdout.strip() if head.stdout.startswith("{") else commit
        payload = {**base, "commit_id": commit}
        def post(p):
            r = gh_api(f"repos/{repo}/pulls/{pr}/reviews", method="POST",
                       input_json=json.dumps(p), token=token)
            if r.returncode != 0:
                sys.stderr.write(f"[publish] reviews POST failed: {r.stdout}{r.stderr}\n")
            return r.returncode == 0
        if not post(payload):
            if event == "APPROVE":
                sys.stderr.write("[publish] APPROVE rejected (repo setting?); falling back to COMMENT\n")
                payload["event"] = "COMMENT"
                if not post(payload):
                    sys.stderr.write("[publish] COMMENT fallback also failed\n"); sys.exit(1)
            else:
                sys.stderr.write(f"[publish] review submission failed for event={event}\n"); sys.exit(1)
    print(json.dumps({"conclusion": conclusion, "summary": summary}))

if __name__ == "__main__":
    main()
```
Prefer fetching the head SHA with `gh api ... --jq .head.sha` (pass `--jq` in `gh_api`) to avoid the extra `jq` shell-out; the engineer may simplify the `commit = ...` lines accordingly as long as `commit_id` ends up the head SHA. `chmod +x`.

- [ ] **Step 2: Delete the bash hook and repoint tests**

```bash
git rm .github/agent-factory/protocols/grumpy/publish/publish-review-from-evidence.sh
```
In `tests/test-publish.sh` (`HOOK=`) and `tests/test-engine.sh` (the `cp .../publish-review-from-evidence.sh .../publish-grumpy.sh` setup line and any `STUB_HOOK`), change `.sh` → `.py`. The stub-copy line becomes copying the `.py`.

- [ ] **Step 3: Run the oracles**

Run: `bash tests/test-publish.sh && bash tests/test-engine.sh && echo ALL-GREEN`
Expected: ends with `ALL-GREEN`.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(grumpy): port publish hook to python"
```

### Task 3.4: Port multi-grumpy publish hooks → shared `_review.py` + thin entrypoints

**Files:**
- Create: `.github/agent-factory/protocols/multi-grumpy/publish/_review.py`
- Create: `.github/agent-factory/protocols/multi-grumpy/publish/publish-grumpy.py`
- Create: `.github/agent-factory/protocols/multi-grumpy/publish/publish-security.py`
- Delete: `publish-grumpy.sh`, `publish-security.sh` (multi-grumpy)
- Modify: `tests/test-publish.sh`, `tests/test-engine.sh` (paths → `.py`)

- [ ] **Step 1: Create `_review.py` (importable mechanism)**

Move the entire publish mechanism from Task 3.3 into a `run(strings)` function in `_review.py`, parameterized by the four wording strings. Same ABI semantics; same `gh` subprocess; exit codes load-bearing.

```python
#!/usr/bin/env python3
"""Shared PR-review publication mechanism for multi-grumpy branches.
Imported by the thin per-branch entrypoints; not invoked directly."""
import json, os, subprocess, sys

def gh_api(path, method=None, input_json=None, token=None, jq=None):
    cmd = ["gh", "api", path]
    if jq: cmd += ["--jq", jq]
    if method: cmd += ["--method", method, "--input", "-"]
    env = dict(os.environ)
    if token: env["GH_TOKEN"] = token
    return subprocess.run(cmd, input=input_json, text=True, capture_output=True, env=env)

def run(req_body, req_summary, ok_body, ok_summary):
    """req_*: REQUEST_CHANGES wording (body may contain {n}/{nfiles}); ok_*: APPROVE wording."""
    evid = sys.argv[1]
    with open(evid) as f: ev = json.load(f)
    issues = any(v.get("verdict") == "issues-found"
                 for fe in ev.get("files", []) for v in fe.get("verdicts", []))
    event = "REQUEST_CHANGES" if issues else "APPROVE"
    comments = []
    for fe in ev.get("files", []):
        for v in fe.get("verdicts", []):
            if v.get("verdict") != "issues-found": continue
            for fd in v.get("findings", []):
                c = {"path": fe["path"], "side": fd["side"], "line": fd["line"], "body": fd["comment"]}
                if fd.get("start_line"):
                    c["start_line"] = fd["start_line"]; c["start_side"] = fd["side"]
                comments.append(c)
    n = len(comments); nfiles = len({c["path"] for c in comments})
    if event == "REQUEST_CHANGES":
        body, conclusion, summary = req_body.format(n=n, nfiles=nfiles), "failure", req_summary
    else:
        body, conclusion, summary = ok_body, "success", ok_summary
    base = {"event": event, "body": body, "comments": comments}
    repo, pr, token = os.environ["GITHUB_REPOSITORY"], os.environ["PR"], os.environ.get("PUBLISH_TOKEN", "")
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] POST repos/{repo}/pulls/{pr}/reviews\n")
        sys.stderr.write(json.dumps(base, indent=2) + "\n")
    else:
        commit = gh_api(f"repos/{repo}/pulls/{pr}", token=token, jq=".head.sha").stdout.strip()
        payload = {**base, "commit_id": commit}
        def post(p):
            r = gh_api(f"repos/{repo}/pulls/{pr}/reviews", method="POST",
                       input_json=json.dumps(p), token=token)
            if r.returncode != 0:
                sys.stderr.write(f"[publish] reviews POST failed: {r.stdout}{r.stderr}\n")
            return r.returncode == 0
        if not post(payload):
            if event == "APPROVE":
                sys.stderr.write("[publish] APPROVE rejected (repo setting?); falling back to COMMENT\n")
                payload["event"] = "COMMENT"
                if not post(payload):
                    sys.stderr.write("[publish] COMMENT fallback also failed\n"); sys.exit(1)
            else:
                sys.stderr.write(f"[publish] review submission failed for event={event}\n"); sys.exit(1)
    print(json.dumps({"conclusion": conclusion, "summary": summary}))
```

- [ ] **Step 2: Create the two thin entrypoints (verbatim wording from the existing `.sh`)**

`publish-grumpy.py`:
```python
#!/usr/bin/env python3
import _review
_review.run(
    req_body="\U0001f624 Grumpy protocol review — {n} issue(s) across {nfiles} file(s), "
             "evidence verified by deterministic checks. Griping inline.",
    req_summary="Grumpy requested changes — resolve them before merging. See the inline comments.",
    ok_body="\U0001f624 Fine. I examined every file against every category and found nothing "
            "worth complaining about. Don't get used to it.",
    ok_summary="Grumpy examined every file × category and found nothing to fix.")
```

`publish-security.py`:
```python
#!/usr/bin/env python3
import _review
_review.run(
    req_body="\U0001f512 Security review — {n} potential issue(s) across {nfiles} file(s), "
             "evidence verified by deterministic checks. Details inline.",
    req_summary="Security review flagged issues — resolve them before merging. See the inline comments.",
    ok_body="\U0001f512 Security review — examined the changed surface and found no vulnerabilities worth flagging.",
    ok_summary="Security review found nothing to fix.")
```
`chmod +x` both entrypoints. (`_review.py` need not be executable — it's imported, and same-dir `import _review` works because the entrypoint's dir is `sys.path[0]`.)

- [ ] **Step 3: Delete the bash hooks; repoint tests**

```bash
git rm .github/agent-factory/protocols/multi-grumpy/publish/publish-grumpy.sh \
       .github/agent-factory/protocols/multi-grumpy/publish/publish-security.sh
```
In `tests/test-publish.sh` change the `publish-security.sh` references → `publish-security.py`.

- [ ] **Step 4: Run the oracles**

Run: `bash tests/test-publish.sh && bash tests/test-engine.sh && echo ALL-GREEN`
Expected: ends with `ALL-GREEN`.

- [ ] **Step 5: Full suite + commit**

```bash
for t in tests/test-*.sh; do echo "== $t =="; bash "$t" || echo "FAILED: $t"; done
git add -A
git commit -m "refactor(multi-grumpy): port publish hooks to python with shared _review.py"
```

---

# PHASE 4 — Tests bash → pytest

**Gate:** `pytest` all green; each suite's parity confirmed before its bash file is deleted; bash suite removed.

### Task 4.1: Add pytest scaffolding and shared fixtures

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/requirements-dev.txt` (`pytest`, `PyYAML`)

- [ ] **Step 1: Write `conftest.py` with the fake-state fixture**

Encapsulate what each bash suite hand-rolls: a temp bare git repo as the `agentic-state` origin, env with `ENGINE_LOCAL=1` and `STATE_REMOTE` pointed at it, paths to the engine scripts and fixtures, and a helper to invoke an engine script capturing `(stdout, stderr, rc)`.

```python
import json, os, subprocess, pathlib, pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
PROTOCOLS = ROOT / ".github/agent-factory/protocols"
FIXTURES = ROOT / "tests/fixtures"

@pytest.fixture
def state_origin(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "agentic-state", str(origin)], check=True)
    return origin

@pytest.fixture
def engine_env(state_origin):
    env = dict(os.environ)
    env["ENGINE_LOCAL"] = "1"
    env["STATE_REMOTE"] = str(state_origin)
    return env

def run_engine(script, *args, env=None, branch=None):
    e = dict(env or os.environ)
    if branch is not None: e["BRANCH"] = branch
    r = subprocess.run(["python3", str(ENGINE / script), *map(str, args)],
                       text=True, capture_output=True, env=e)
    return r.stdout, r.stderr, r.returncode
```

- [ ] **Step 2: Verify pytest collects and the fixture works**

Run:
```bash
python3 -m pip install -r tests/requirements-dev.txt >/dev/null
pytest tests/ --collect-only -q
```
Expected: collection succeeds (0 tests so far, no errors).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py tests/requirements-dev.txt
git commit -m "test: add pytest scaffolding (conftest fixtures, dev deps)"
```

### Tasks 4.2–4.9: Translate each suite, confirm parity, delete the bash file

For each bash suite below, perform the same five steps. **Translate assertion-for-assertion** — read the bash suite, port each `bad`/assertion into a pytest function with the identical inputs and expected outputs. The parity oracle is: the bash suite passed at the end of Phase 3, so the pytest port must make the same assertions and pass.

| Task | Bash suite | pytest module |
|---|---|---|
| 4.2 | `tests/test-correlation.sh` | `tests/test_correlation.py` |
| 4.3 | `tests/test-checks.sh` | `tests/test_checks.py` |
| 4.4 | `tests/test-runchecks.sh` | `tests/test_runchecks.py` |
| 4.5 | `tests/test-publish.sh` | `tests/test_publish.py` |
| 4.6 | `tests/test-status-comment.sh` | `tests/test_status_comment.py` |
| 4.7 | `tests/test-join.sh` | `tests/test_join.py` |
| 4.8 | `tests/test-engine.sh` | `tests/test_engine.py` |
| 4.9 | `tests/test-fanout-e2e.sh` | `tests/test_fanout_e2e.py` |

Do them in the order listed (simplest → most complex; `test-engine.sh` and `test-fanout-e2e.sh` are the largest and exercise the most engine surface).

- [ ] **Step 1: Write `tests/test_<name>.py`** — one pytest function per logical assertion in the bash suite, using `conftest` fixtures/`run_engine`. Parse engine stdout with `json.loads`; read state with `yaml.safe_load`.
- [ ] **Step 2: Run it** — `pytest tests/test_<name>.py -v` — Expected: all PASS.
- [ ] **Step 3: Confirm parity** — `bash tests/test-<name>.sh && echo BASH-GREEN` still passes (both green = parity).
- [ ] **Step 4: Delete the bash suite** — `git rm tests/test-<name>.sh`.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "test: port test-<name>.sh to pytest"`.

### Task 4.10: Final sweep — no bash tests remain, full pytest green

**Files:**
- Modify: `CLAUDE.md` (the "Running tests" section)

- [ ] **Step 1: Confirm no bash test scaffolding remains**

```bash
ls tests/*.sh 2>/dev/null; echo "exit:$?"
```
Expected: no files (exit non-zero from `ls`).

- [ ] **Step 2: Run the whole pytest suite**

Run: `pytest tests/ -q`
Expected: all tests pass, 0 failures.

- [ ] **Step 3: Update the "Running tests" docs**

In `CLAUDE.md`, replace the bash-suite instructions with pytest usage (`pip install -r tests/requirements-dev.txt`, `pytest tests/`, `pytest tests/test_engine.py -v`). Note PyYAML as the engine runtime dep and pytest as dev-only.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: complete pytest migration; remove bash suites; update docs"
```

---

## Final verification (whole program)

- [ ] `pytest tests/ -q` — all green.
- [ ] `grep -rn "lib.sh\|\.github/engine/\|[^/]protocols/" .github/ tests/ docs/STATUS.md docs/HOW-IT-WORKS.md CLAUDE.md` — no stale paths or bash-lib references (exit 1).
- [ ] `ls .github/agent-factory/{VERSION,README.md,engine,protocols}` — present.
- [ ] No `.sh` under `.github/agent-factory/` (`find .github/agent-factory -name '*.sh'` empty).
- [ ] Manual read of `orchestrator.yml` + `protocol-join.yml`: every engine-call site invokes `.github/agent-factory/engine/*.py` (or `lib.py <subcommand>`); no `source *.sh` remains; agent-derived strings (`feedback`, `verdicts`, filenames) still passed via `env:`, never interpolated into `run:`.

## Notes & gotchas (from the codebase)

- **Never force-push `agentic-state`.** `cas_push` is fast-forward-only with one rebase retry — preserve this exactly in `lib.py`.
- **mikefarah yq numeric checks:** jq's `type == "number"` rejects booleans; Python's `isinstance(x, int)` accepts `True`. The `not isinstance(..., bool)` guards in `schema-valid.py` reproduce jq's behavior — keep them.
- **`tests/test-join.sh` lacks the exec bit** — always invoked as `bash tests/test-join.sh`. (Irrelevant after Phase 4.)
- **Extension-agnostic resolution** (`resolve_executable`) errors on ambiguity, so always `git rm` the `.sh` in the same task that adds the `.py` — never leave both.
- **`gh aw compile` not needed** — agents (`*-agent.md`/`.lock.yml`) never reference engine/protocol paths.
- **PyYAML output stability:** always dump with `sort_keys=False, default_flow_style=False` so the state git-trail stays readable and diffs stay minimal.
