# Multi-AI Review → Issues → Triage → Committing Fix (Demo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a self-contained `code-review-demo` protocol (`review → triage → fix`) where the 5 domain reviewers open domain-labeled GitHub issues, triage consolidates them, and the fix phase commits remediations to the PR head and closes the issues it resolved — then deploy it to `SiRumCz/yuanrong-datasystem` and run it on PR #8.

**Architecture:** A new sibling protocol directory `.github/agent-factory/protocols/code-review-demo/` reuses the *behavior* of `code-review`'s review/triage/fix phases but: (a) drops preflight/overview/post-fix/mrp from `states[]`; (b) drops the `publish-review` PR-review channel; (c) carries its own copies of the checks, evidence schemas, and rubrics so it installs standalone; (d) replaces the suggest-only fix conclude hook with one that *applies + pushes + closes*. The 5 review legs run new `demo-review-<dim>-agent` workflows that add a gh-aw `create-issue` safe-output. `triage`/`fix` agents are reused unmodified. The engine (`.github/agent-factory/engine/`) and the shared `agentic-engine.yml` are **not** modified.

**Tech Stack:** Python 3 + PyYAML (runtime), gh-aw (codex/gpt-5.5 agents), GitHub Actions, `gh` CLI, pytest (dev-only).

## Global Constraints

- **Engine untouched:** do not edit anything under `.github/agent-factory/engine/` or `.github/workflows/agentic-engine.yml` / `agentic-orchestrator.yml`. (Verbatim spec non-goal.)
- **`code-review` protocol untouched:** do not edit `.github/agent-factory/protocols/code-review/protocol.json` or its checks/publish/schemas. All existing tests must stay green (`uv run pytest tests/ -q` → was 630 passed).
- **Check ABI:** `<check> <evidence.json> <diff.txt> <changed-files.txt>` → one JSON `{"check","pass","feedback"}` on stdout, **always exit 0**. Reads node config from `CHECK_PARAMS` env.
- **Conclude-hook ABI:** `<hook> <evidence.json> <instance-key>`; inherits env incl. `PUBLISH_TOKEN`, `GH_TOKEN`, `GITHUB_REPOSITORY`, `PR`, `PR_HEAD_SHA`, `ENGINE_LOCAL`, and (for states with `inputs`) `CONCLUDE_INPUTS_DIR`; must print `{"conclusion","summary","blocked"}` on stdout. Runs **once on the final passing iteration**.
- **`ENGINE_LOCAL=1` must short-circuit every network/git side effect** to a `*_OUT` file (mirrors existing hooks). Tests rely on this.
- **Security:** agent-derived strings (titles, findings, branch names) are passed to `subprocess` via argument lists / env, never interpolated into a shell string.
- **`gh aw compile` flips check exec bits:** every `gh aw compile` resets `*.py` (incl. our `code-review-demo/checks/*.py` and `publish/*.py`) to `100644`. After ANY compile step, restore exec bits and re-stage: `chmod 755 .github/agent-factory/protocols/code-review-demo/checks/*.py .github/agent-factory/protocols/code-review-demo/publish/conclude-*.py` and `git add` the mode change. Otherwise the engine reports checks as non-executable.
- **Trigger:** `code-review-demo` uses `/demo-review` (not `/review`) to avoid router overlap with `code-review`.
- **Engine version:** `code-review-demo/protocol.json` sets `"min_engine_version": "1.0.0"` (matching `code-review`).

---

## File Structure

**New protocol dir** `.github/agent-factory/protocols/code-review-demo/`:
- `protocol.json` — slimmed `review → join-review → triage → fix → done`.
- `checks/evidence-present.py`, `review-schema-valid.py`, `review-findings-anchored.py`, `_diff.py`, `triage-schema-valid.py`, `fix-schema-valid.py` — copies of code-review's (fix-schema-valid extended for `original_line`).
- `publish/conclude-triage.py` — copy + issue cross-link.
- `publish/conclude-fix.py` — copy + apply/push/close.
- `publish/_derive_gate.py` — copy.
- `publish/_apply_fixes.py` — **new** pure patch-applier helper.
- `review.evidence.schema.json`, `triage.evidence.schema.json`, `fix.evidence.schema.json` — copies (fix gains optional `original_line`).
- `rubrics/correctness.md`, `test.md`, `performance.md`, `security.md`, `maintainability.md` — copies.

**New agent workflows** `.github/workflows/`:
- `demo-review-correctness-agent.md` + `.lock.yml` (and `test`, `performance`, `security`, `maintainability`).

**Modified (shared, additive/back-compatible):**
- `.github/workflows/fix-agent.md` — one prompt bullet to emit optional `original_line`. (Recompile its lock.)

**New tests** `tests/`:
- `test_demo_protocol_shape.py` — protocol-lint + sequence assertions.
- `test_demo_apply_fixes.py` — `_apply_fixes` unit tests.
- `test_demo_conclude_fix_apply.py` — conclude-fix apply/push/close in ENGINE_LOCAL.
- `test_demo_conclude_triage_links.py` — conclude-triage issue-linking in ENGINE_LOCAL.

---

## Task 1: Scaffold the self-contained `code-review-demo` protocol

**Files:**
- Create: `.github/agent-factory/protocols/code-review-demo/protocol.json`
- Create (copies): `…/code-review-demo/checks/{evidence-present.py,review-schema-valid.py,review-findings-anchored.py,_diff.py,triage-schema-valid.py,fix-schema-valid.py}`
- Create (copies): `…/code-review-demo/publish/{conclude-triage.py,conclude-fix.py,_derive_gate.py}`
- Create (copies): `…/code-review-demo/{review.evidence.schema.json,triage.evidence.schema.json,fix.evidence.schema.json}`
- Create (copies): `…/code-review-demo/rubrics/{correctness,test,performance,security,maintainability}.md`
- Test: `tests/test_demo_protocol_shape.py`

**Interfaces:**
- Produces: a protocol whose `name` is `code-review-demo`, `states[0].id == "review"`, terminal phase `fix` with `next: "done"`; check `run` names resolve to `code-review-demo/checks/*`.

- [ ] **Step 1: Copy the reusable files via a scripted copy**

```bash
cd .github/agent-factory/protocols
SRC=code-review DST=code-review-demo
mkdir -p $DST/checks $DST/publish $DST/rubrics
for f in evidence-present.py review-schema-valid.py review-findings-anchored.py _diff.py triage-schema-valid.py fix-schema-valid.py; do
  cp $SRC/checks/$f $DST/checks/$f
done
for f in conclude-triage.py conclude-fix.py _derive_gate.py; do
  cp $SRC/publish/$f $DST/publish/$f
done
for f in review.evidence.schema.json triage.evidence.schema.json fix.evidence.schema.json; do
  cp $SRC/$f $DST/$f
done
for d in correctness test performance security maintainability; do
  cp $SRC/rubrics/$d.md $DST/rubrics/$d.md
done
chmod 755 $DST/checks/*.py $DST/publish/conclude-*.py
ls -R $DST
```

Expected: the dir tree lists all copied files; check/conclude scripts are `755`.

- [ ] **Step 2: Write `code-review-demo/protocol.json`**

Create `.github/agent-factory/protocols/code-review-demo/protocol.json` with exactly this content (the 5 review branches, join, triage, fix, done — note `max_iterations: 1` on review legs, **no** `"publish"` key, `fix.next: "done"`, demo agent workflow names):

```json
{
  "name": "code-review-demo",
  "version": "0.1.0",
  "min_engine_version": "1.0.0",
  "_note": "Demo slice of code-review: review (5 dims, each opens domain-labeled issues via gh-aw create-issue) -> join -> triage (consolidate, link issues) -> fix (apply patches to the PR head, push, close resolved issues) -> done. Self-contained: its own checks/schemas/rubrics so dist installs it standalone. preflight/overview/post-fix/mrp are intentionally absent. Review legs run at max_iterations:1 so a retried agent run cannot double-open issues (create-issue fires in the agent job before checks). Reuses the stock triage-agent and fix-agent; the fix commit/close lives in this dir's conclude-fix.",
  "triggers": [
    { "on": "issue_comment", "comment_prefix": "/demo-review", "command": "start" }
  ],
  "states": [
    {
      "id": "review",
      "kind": "fanout",
      "label": "multi-dimension review (opens issues)",
      "params": { "status_note": { "verdict_field": "verdict", "flag_verdicts": ["REQUEST_CHANGES"], "severity_field": "severity", "flag_severities": ["critical", "high"], "label": "request-changes" } },
      "branches": [
        { "id": "correctness",     "workflow": "demo-review-correctness-agent",     "evidence": "review.evidence.schema.json", "max_iterations": 1, "params": { "dimension": "correctness",     "require": ["dimension", "verdict", "findings"], "non_empty": ["dimension", "verdict"] }, "checks": [ { "run": "evidence-present", "on_fail": "advisory" }, { "run": "review-schema-valid", "on_fail": "advisory" }, { "run": "review-findings-anchored", "on_fail": "advisory" } ] },
        { "id": "test",            "workflow": "demo-review-test-agent",            "evidence": "review.evidence.schema.json", "max_iterations": 1, "params": { "dimension": "test",            "require": ["dimension", "verdict", "findings"], "non_empty": ["dimension", "verdict"] }, "checks": [ { "run": "evidence-present", "on_fail": "advisory" }, { "run": "review-schema-valid", "on_fail": "advisory" }, { "run": "review-findings-anchored", "on_fail": "advisory" } ] },
        { "id": "performance",     "workflow": "demo-review-performance-agent",     "evidence": "review.evidence.schema.json", "max_iterations": 1, "params": { "dimension": "performance",     "require": ["dimension", "verdict", "findings"], "non_empty": ["dimension", "verdict"] }, "checks": [ { "run": "evidence-present", "on_fail": "advisory" }, { "run": "review-schema-valid", "on_fail": "advisory" }, { "run": "review-findings-anchored", "on_fail": "advisory" } ] },
        { "id": "security",        "workflow": "demo-review-security-agent",        "evidence": "review.evidence.schema.json", "max_iterations": 1, "params": { "dimension": "security",        "require": ["dimension", "verdict", "findings"], "non_empty": ["dimension", "verdict"] }, "checks": [ { "run": "evidence-present", "on_fail": "advisory" }, { "run": "review-schema-valid", "on_fail": "advisory" }, { "run": "review-findings-anchored", "on_fail": "advisory" } ] },
        { "id": "maintainability", "workflow": "demo-review-maintainability-agent", "evidence": "review.evidence.schema.json", "max_iterations": 1, "params": { "dimension": "maintainability", "require": ["dimension", "verdict", "findings"], "non_empty": ["dimension", "verdict"] }, "checks": [ { "run": "evidence-present", "on_fail": "advisory" }, { "run": "review-schema-valid", "on_fail": "advisory" }, { "run": "review-findings-anchored", "on_fail": "advisory" } ] }
      ],
      "next": "join-review"
    },
    { "id": "join-review", "kind": "join", "of": "review", "next": "triage" },
    {
      "id": "triage",
      "kind": "agent",
      "label": "review triage (cluster & rank)",
      "workflow": "triage-agent",
      "evidence": "triage.evidence.schema.json",
      "max_iterations": 2,
      "inputs": [
        { "from": "correctness",     "as": "correctness" },
        { "from": "test",            "as": "test" },
        { "from": "performance",     "as": "performance" },
        { "from": "security",        "as": "security" },
        { "from": "maintainability", "as": "maintainability" }
      ],
      "params": { "require": ["clusters", "summary"] },
      "checks": [
        { "run": "evidence-present", "on_fail": "iterate" },
        { "run": "triage-schema-valid", "on_fail": "iterate" }
      ],
      "conclude": "conclude-triage",
      "next": "fix"
    },
    {
      "id": "fix",
      "kind": "agent",
      "label": "apply remediations + commit to PR",
      "workflow": "fix-agent",
      "evidence": "fix.evidence.schema.json",
      "max_iterations": 2,
      "inputs": [ { "from": "triage", "as": "triage" } ],
      "params": { "require": ["fixes", "mode"] },
      "checks": [
        { "run": "evidence-present", "on_fail": "iterate" },
        { "run": "fix-schema-valid", "on_fail": "iterate" }
      ],
      "conclude": "conclude-fix",
      "next": "done"
    }
  ]
}
```

> **Note on `advisory` review checks:** the demo sets the review legs' checks to `advisory` (not `iterate`) so a single-pass (`max_iterations:1`) leg never blocks the join on a borderline schema/anchor nit — the issues are already opened and triage still runs. This is a deliberate demo-robustness choice; `triage`/`fix` keep `iterate`.

- [ ] **Step 3: Write the failing shape test**

Create `tests/test_demo_protocol_shape.py`:

```python
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTO = ROOT / ".github/agent-factory/protocols/code-review-demo/protocol.json"
LINT = ROOT / ".github/agent-factory/engine/protocol-lint.py"


def test_demo_protocol_name_and_sequence():
    p = json.load(open(PROTO))
    assert p["name"] == "code-review-demo"
    ids = [s["id"] for s in p["states"]]
    assert ids == ["review", "join-review", "triage", "fix"]
    fix = next(s for s in p["states"] if s["id"] == "fix")
    assert fix["next"] == "done"


def test_demo_review_legs_open_issues_no_publish():
    p = json.load(open(PROTO))
    review = next(s for s in p["states"] if s["id"] == "review")
    assert len(review["branches"]) == 5
    for b in review["branches"]:
        assert "publish" not in b, f"{b['id']} must not post a PR review (issues-only)"
        assert b["max_iterations"] == 1, f"{b['id']} must be single-pass (issue idempotency)"
        assert b["workflow"] == f"demo-review-{b['id']}-agent"


def test_demo_protocol_lint_clean():
    r = subprocess.run([sys.executable, str(LINT), str(PROTO)],
                       text=True, capture_output=True)
    assert r.returncode == 0, f"protocol-lint failed:\n{r.stdout}\n{r.stderr}"
```

- [ ] **Step 4: Run the test — expect FAIL then PASS**

Run: `uv run pytest tests/test_demo_protocol_shape.py -v`
Expected: PASS (all three). If `test_demo_protocol_lint_clean` fails, read the lint output and fix `protocol.json` (most likely a check name not present in `checks/`, or a branch workflow typo).

- [ ] **Step 5: Confirm the existing suite is still green**

Run: `uv run pytest tests/ -q`
Expected: `631 passed` (630 prior + the new file's tests count as collected; the prior 630 must all still pass — `code-review` is untouched).

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/protocols/code-review-demo tests/test_demo_protocol_shape.py
git commit -m "feat(demo): self-contained code-review-demo protocol (review→triage→fix)"
```

---

## Task 2: Demo review agents that open domain-labeled issues

**Files:**
- Create: `.github/workflows/demo-review-correctness-agent.md` (+ `test`, `performance`, `security`, `maintainability`)
- Create (compiled): the matching `*.lock.yml` for each (via `gh aw compile`)

**Interfaces:**
- Consumes: the engine dispatches each via `workflow_dispatch` with `aw_context` (Task-1 protocol `branches[].workflow` names must match these file basenames).
- Produces: an `evidence.json` artifact (unchanged shape) **and** one GitHub issue per kept finding, labeled `ai-review` + `review:<dimension>`.

- [ ] **Step 1: Generate the five demo review agent `.md` files from the stock ones**

Each demo agent is the stock `review-<dim>-agent.md` with three changes: (1) `name`/`run-name`, (2) the rubric staging path → `code-review-demo/rubrics/`, (3) frontmatter `safe-outputs` gains `create-issue` (with the domain label) and drops `staged: true`; `permissions.issues: write`. Apply with this script, then hand-verify one:

```bash
cd .github/workflows
for dim in correctness test performance security maintainability; do
  src="review-${dim}-agent.md"
  dst="demo-review-${dim}-agent.md"
  # 1) start from the stock agent
  cp "$src" "$dst"
  # 2) repoint the rubric path to the demo protocol dir
  sed -i 's#protocols/code-review/rubrics#protocols/code-review-demo/rubrics#g' "$dst"
  # 3) rename the workflow + run-name
  sed -i "s#^name: \"Review Agent: ${dim}.*#name: \"Demo Review Agent: ${dim} (protocol state: review.${dim})\"#" "$dst"
  sed -i 's#^run-name: "Review Agent#run-name: "Demo Review Agent#' "$dst"
done
ls demo-review-*-agent.md
```

- [ ] **Step 2: Hand-edit the `safe-outputs` + `permissions` block in each demo agent**

In each `demo-review-<dim>-agent.md`, replace the frontmatter block

```yaml
permissions:
  contents: read
  pull-requests: read
  issues: read
safe-outputs:
  staged: true
  noop: {}
```

with (substituting the dimension into the label — shown for `correctness`):

```yaml
permissions:
  contents: read
  pull-requests: read
  issues: write
safe-outputs:
  create-issue:
    title-prefix: "[ai-review][correctness] "
    labels: [ai-review, "review:correctness"]
    max: 5
  noop: {}
```

> The domain label lives in *config* (one agent = one dimension), so every issue this leg opens is correctly tagged without relying on per-call label support. `max: 5` caps a noisy leg.

- [ ] **Step 3: Add the issue-creation instruction to each demo agent's prompt body**

Append this section to each `demo-review-<dim>-agent.md` body (after the "Evidence output" section). It instructs the agent to also open one issue per kept finding via the `create-issue` safe-output (the same mechanism it already uses to call `noop`):

```markdown
## Open one issue per finding (required when findings ≠ [])

After writing `/tmp/gh-aw/evidence.json`, for EACH finding you kept (up to 5),
emit a `create-issue` safe-output so reviewers see your domain's problems as
distinct, labeled GitHub issues:

- **title**: the finding's `title` verbatim (the engine adds the
  `[ai-review][<dimension>]` prefix and the `review:<dimension>` label).
- **body**: a short markdown block with
  `` `<path>:<line>` `` · **severity** · the `impact` · a fenced "Suggested fix"
  showing `fix`, and a trailer line `Found by the <dimension> reviewer on PR #<pr>`
  (read `<pr>` from `/tmp/gh-aw/task-context.json` `.pr`).

If `findings` is empty (verdict `APPROVE`), open NO issue — just write the
evidence object and call `noop`. Never open an issue for a finding not present
in your `evidence.json`.
```

- [ ] **Step 4: Compile the locks**

Run: `gh aw compile`
Expected: regenerates `demo-review-*-agent.lock.yml` (and others) with no errors.

- [ ] **Step 5: Verify the compiled lock actually wires issue creation**

Run:
```bash
grep -nA6 "create.issue\|create_issue\|issues: write\|issue_write" .github/workflows/demo-review-correctness-agent.lock.yml | head -40
```
Expected: a `create_issue` job (or step) is present and `issues: write` appears in the relevant job's permissions, and `staged` is not forcing dry-run. **If the lock shows no active create-issue path**, the gh-aw version's safe-output key differs — consult `gh aw --help` / the gh-aw docs for the exact `create-issue` schema and adjust the frontmatter in Step 2, then recompile. Do not proceed until the lock wires a real issue-creating job.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/demo-review-*-agent.md .github/workflows/demo-review-*-agent.lock.yml
git commit -m "feat(demo): 5 review agents open domain-labeled issues (create-issue safe-output)"
```

---

## Task 3: `original_line` — verify-before-replace field for the fix applier

**Files:**
- Modify: `.github/agent-factory/protocols/code-review-demo/fix.evidence.schema.json`
- Modify: `.github/agent-factory/protocols/code-review-demo/checks/fix-schema-valid.py`
- Modify: `.github/workflows/fix-agent.md` (one additive prompt bullet) + recompile lock
- Test: `tests/test_demo_protocol_shape.py` (extend) — or assert via the apply tests in Task 5

**Interfaces:**
- Produces: a fix evidence object may carry, per fix, `original_line: <string>` (optional). Consumed by `_apply_fixes.apply_fix` in Task 4.

- [ ] **Step 1: Add the optional field to the demo fix schema**

In `code-review-demo/fix.evidence.schema.json`, inside the `fixes[].items.properties`, add (do not add to `required`):

```json
"original_line": { "type": "string", "minLength": 1 }
```

- [ ] **Step 2: Make the demo fix-schema-valid accept (and shallow-check) it**

In `code-review-demo/checks/fix-schema-valid.py`, where each `fixes[]` item is validated, add a tolerant check after the existing per-field validation (find the loop that validates `cluster_id/path/line/rationale/suggested_patch` and add):

```python
            ol = fix.get("original_line")
            if ol is not None and (not isinstance(ol, str) or ol == ""):
                problems.append(f"fixes[{i}].original_line must be a non-empty string when present")
```

(Use the same `problems.append(...)` / index variable names already in that file — read the loop first and match them.)

- [ ] **Step 3: Add the emit instruction to the shared fix-agent prompt**

In `.github/workflows/fix-agent.md`, in "Step 3 — craft fixes", append one bullet to the `fixes` entry description:

```markdown
- `original_line`: the exact current content of the line at `line` (verbatim,
  copied from `/tmp/gh-aw/agent/pr.diff`, without the leading `+`), so the engine
  can verify the target before applying your `suggested_patch`. Include it
  whenever you emit a single-line fix.
```

This is additive and optional, so `code-review`'s stricter consumers are unaffected.

- [ ] **Step 4: Recompile the fix-agent lock**

Run: `gh aw compile`
Expected: `fix-agent.lock.yml` regenerates cleanly.

- [ ] **Step 5: Sanity-check both fix schemas validate a sample with/without `original_line`**

Run:
```bash
cd .github/agent-factory/protocols/code-review-demo
printf '%s' '{"mode":"suggest","fixes":[{"cluster_id":"c1","path":"a.py","line":3,"rationale":"r","suggested_patch":"x = 1","original_line":"x = 0"}],"skipped":[]}' > /tmp/fix_ok.json
python3 checks/fix-schema-valid.py /tmp/fix_ok.json /dev/null /dev/null
printf '%s' '{"mode":"suggest","fixes":[{"cluster_id":"c1","path":"a.py","line":3,"rationale":"r","suggested_patch":"x = 1","original_line":""}],"fixes_extra":0}' > /tmp/fix_bad.json
python3 checks/fix-schema-valid.py /tmp/fix_bad.json /dev/null /dev/null
```
Expected: first prints `"pass": true`; second prints `"pass": false` (empty `original_line`).

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/protocols/code-review-demo/fix.evidence.schema.json \
        .github/agent-factory/protocols/code-review-demo/checks/fix-schema-valid.py \
        .github/workflows/fix-agent.md .github/workflows/fix-agent.lock.yml
git commit -m "feat(demo): optional original_line on fix evidence (verify-before-apply)"
```

---

## Task 4: `_apply_fixes.py` — pure patch-applier helper

**Files:**
- Create: `.github/agent-factory/protocols/code-review-demo/publish/_apply_fixes.py`
- Test: `tests/test_demo_apply_fixes.py`

**Interfaces:**
- Produces: `apply_fix(workdir: str, fix: dict) -> dict` returning `{"cluster_id","path","status","detail"}` where `status ∈ {"applied","skipped"}`; and `apply_all(workdir: str, fixes: list) -> list[dict]` (one result per fix). Replaces the 1-based line `fix["line"]` in `workdir/fix["path"]` with `fix["suggested_patch"]` (which may be multiline). When `fix.get("original_line")` is present, it must equal the current line (after stripping a trailing newline) or the fix is `skipped` with `detail="drift"`. Out-of-range line / missing file → `skipped`.

- [ ] **Step 1: Write the failing unit test**

Create `tests/test_demo_apply_fixes.py`:

```python
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MOD = ROOT / ".github/agent-factory/protocols/code-review-demo/publish/_apply_fixes.py"
spec = importlib.util.spec_from_file_location("_apply_fixes", MOD)
af = importlib.util.module_from_spec(spec)
spec.loader.exec_module(af)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_apply_replaces_target_line(tmp_path):
    _write(tmp_path, "a.py", "x = 0\ny = 1\nz = 2\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 1,
           "suggested_patch": "x = 99", "original_line": "x = 0"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "applied"
    assert (tmp_path / "a.py").read_text() == "x = 99\ny = 1\nz = 2\n"


def test_apply_skips_on_drift(tmp_path):
    _write(tmp_path, "a.py", "x = 0\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 1,
           "suggested_patch": "x = 99", "original_line": "DOES NOT MATCH"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "skipped" and res["detail"] == "drift"
    assert (tmp_path / "a.py").read_text() == "x = 0\n"


def test_apply_skips_missing_file(tmp_path):
    res = af.apply_fix(str(tmp_path), {"cluster_id": "c1", "path": "nope.py",
                                       "line": 1, "suggested_patch": "x"})
    assert res["status"] == "skipped" and res["detail"] == "missing-file"


def test_apply_skips_out_of_range(tmp_path):
    _write(tmp_path, "a.py", "x = 0\n")
    res = af.apply_fix(str(tmp_path), {"cluster_id": "c1", "path": "a.py",
                                       "line": 99, "suggested_patch": "x"})
    assert res["status"] == "skipped" and res["detail"] == "line-out-of-range"


def test_apply_multiline_patch(tmp_path):
    _write(tmp_path, "a.py", "a\nb\nc\n")
    fix = {"cluster_id": "c1", "path": "a.py", "line": 2,
           "suggested_patch": "b1\nb2", "original_line": "b"}
    res = af.apply_fix(str(tmp_path), fix)
    assert res["status"] == "applied"
    assert (tmp_path / "a.py").read_text() == "a\nb1\nb2\nc\n"


def test_apply_all_returns_one_result_per_fix(tmp_path):
    _write(tmp_path, "a.py", "x = 0\n")
    fixes = [
        {"cluster_id": "c1", "path": "a.py", "line": 1, "suggested_patch": "x = 1", "original_line": "x = 0"},
        {"cluster_id": "c2", "path": "missing.py", "line": 1, "suggested_patch": "y"},
    ]
    out = af.apply_all(str(tmp_path), fixes)
    assert [r["status"] for r in out] == ["applied", "skipped"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_demo_apply_fixes.py -q`
Expected: FAIL — `No module named` / file not found for `_apply_fixes.py`.

- [ ] **Step 3: Write `_apply_fixes.py`**

Create `.github/agent-factory/protocols/code-review-demo/publish/_apply_fixes.py`:

```python
#!/usr/bin/env python3
"""Pure patch-applier for the demo fix phase. No git, no network — only file edits.

apply_fix replaces the 1-based `line` in `<workdir>/<path>` with `suggested_patch`
(possibly multiline). If `original_line` is present it must match the current line
(trailing newline ignored), else the fix is skipped as drift. apply_all maps over
a list of fixes and returns one result dict each.
"""
import os


def apply_fix(workdir, fix):
    cid = fix.get("cluster_id")
    rel = fix.get("path") or ""
    line = fix.get("line")
    patch = fix.get("suggested_patch")
    out = {"cluster_id": cid, "path": rel, "status": "skipped", "detail": ""}

    if not isinstance(rel, str) or not rel or not isinstance(line, int) or line < 1 \
            or not isinstance(patch, str):
        out["detail"] = "malformed-fix"
        return out

    target = os.path.join(workdir, rel)
    if not os.path.isfile(target):
        out["detail"] = "missing-file"
        return out

    with open(target) as fh:
        lines = fh.readlines()  # each retains its "\n"
    if line > len(lines):
        out["detail"] = "line-out-of-range"
        return out

    current = lines[line - 1].rstrip("\n")
    expected = fix.get("original_line")
    if expected is not None and current != expected.rstrip("\n"):
        out["detail"] = "drift"
        return out

    trailing_nl = lines[line - 1].endswith("\n")
    replacement = patch.split("\n")
    new_block = [seg + "\n" for seg in replacement]
    if not trailing_nl:
        new_block[-1] = new_block[-1].rstrip("\n")
    lines[line - 1:line] = new_block
    with open(target, "w") as fh:
        fh.writelines(lines)

    out["status"] = "applied"
    return out


def apply_all(workdir, fixes):
    results = []
    for fix in fixes or []:
        if isinstance(fix, dict):
            results.append(apply_fix(workdir, fix))
    return results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_demo_apply_fixes.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/protocols/code-review-demo/publish/_apply_fixes.py tests/test_demo_apply_fixes.py
git commit -m "feat(demo): _apply_fixes pure patch-applier with verify-before-replace"
```

---

## Task 5: `conclude-fix` applies patches, pushes to the PR, closes resolved issues

**Files:**
- Modify: `.github/agent-factory/protocols/code-review-demo/publish/conclude-fix.py`
- Test: `tests/test_demo_conclude_fix_apply.py`

**Interfaces:**
- Consumes: `_apply_fixes.apply_all` (Task 4); `CONCLUDE_INPUTS_DIR/triage.json` (clusters → member findings → dimension+title, for issue resolution); env `PR`, `GITHUB_REPOSITORY`, `GH_TOKEN` (PAT), `PR_HEAD_SHA`, `ENGINE_LOCAL`.
- Produces: in live mode, a commit pushed to the PR head branch + closed issues; in `ENGINE_LOCAL`, a JSON report at `APPLY_OUT`. Still prints the existing `{"conclusion","summary","blocked"}`.

- [ ] **Step 1: Write the failing ENGINE_LOCAL test**

Create `tests/test_demo_conclude_fix_apply.py`:

```python
import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/code-review-demo/publish/conclude-fix.py"


def _run(env, evidence_path, instance="pr-8"):
    r = subprocess.run(["python3", str(HOOK), str(evidence_path), instance],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip())


def test_apply_writes_files_and_records_closes(tmp_path):
    # workdir = a fake PR head checkout
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "a.py").write_text("x = 0\n")

    # triage input (cluster c1 → a correctness finding titled "Bad default")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "triage.json").write_text(json.dumps({
        "clusters": [{
            "cluster_id": "c1", "title": "Bad default", "dimension": ["correctness"],
            "severity": "high", "paths": ["a.py"], "rank": 1,
            "member_findings": [{"dimension": "correctness", "path": "a.py",
                                 "line": 1, "severity": "high", "title": "Bad default"}]
        }]
    }))

    evidence = tmp_path / "fix.json"
    evidence.write_text(json.dumps({
        "mode": "suggest", "skipped": [],
        "fixes": [{"cluster_id": "c1", "path": "a.py", "line": 1,
                   "rationale": "default should be 1", "suggested_patch": "x = 1",
                   "original_line": "x = 0"}]
    }))

    apply_out = tmp_path / "apply.json"
    env = dict(os.environ)
    env.update({
        "ENGINE_LOCAL": "1",
        "CONCLUDE_INPUTS_DIR": str(inputs),
        "APPLY_WORKDIR": str(workdir),
        "APPLY_OUT": str(apply_out),
        "GITHUB_REPOSITORY": "acme/repo", "PR": "8",
        "FIX_REVIEW_OUT": str(tmp_path / "review.json"),
        "FIX_OUT": str(tmp_path / "report.json"),
    })
    out = _run(env, evidence)

    # file actually edited
    assert (workdir / "a.py").read_text() == "x = 1\n"
    # report recorded the applied fix + the issue it would close
    rep = json.loads(apply_out.read_text())
    assert rep["applied"] == 1
    assert any(c["label"] == "review:correctness" and c["title"] == "Bad default"
               for c in rep["close"])
    assert out["conclusion"] == "neutral"


def test_no_fixes_is_noop(tmp_path):
    evidence = tmp_path / "fix.json"
    evidence.write_text(json.dumps({"mode": "suggest", "fixes": [], "skipped": []}))
    apply_out = tmp_path / "apply.json"
    env = dict(os.environ)
    env.update({"ENGINE_LOCAL": "1", "APPLY_OUT": str(apply_out),
                "GITHUB_REPOSITORY": "acme/repo", "PR": "8",
                "FIX_REVIEW_OUT": str(tmp_path / "r.json"), "FIX_OUT": str(tmp_path / "rep.json")})
    out = _run(env, evidence)
    assert out["conclusion"] == "neutral"
    rep = json.loads(apply_out.read_text())
    assert rep["applied"] == 0 and rep["close"] == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_demo_conclude_fix_apply.py -q`
Expected: FAIL (the hook doesn't yet apply files / write `APPLY_OUT`).

- [ ] **Step 3: Extend `conclude-fix.py` with the applier**

In `code-review-demo/publish/conclude-fix.py`, add imports and helpers, then call the applier from `main()` after `_post_review(payload)`. Add at the top (after the existing imports):

```python
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _apply_fixes  # noqa: E402


def _triage_clusters():
    return _triage_input().get("clusters") or []


def _issue_targets(applied_cluster_ids):
    """Map applied cluster_ids -> issue close-targets {label,title} via triage members."""
    targets = []
    seen = set()
    for cluster in _triage_clusters():
        if not isinstance(cluster, dict) or cluster.get("cluster_id") not in applied_cluster_ids:
            continue
        for m in cluster.get("member_findings") or []:
            if not isinstance(m, dict):
                continue
            dim, title = m.get("dimension"), m.get("title")
            if not dim or not title:
                continue
            key = (f"review:{dim}", title)
            if key not in seen:
                seen.add(key)
                targets.append({"label": key[0], "title": title})
    return targets


def _git(args, cwd, token=None):
    env = dict(os.environ)
    if token:
        env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", *args], cwd=cwd, env=env,
                          text=True, capture_output=True)


def _apply_commit_close(evidence):
    """Apply fixes to the PR head, push a commit, close resolved issues.
    Returns a report dict. ENGINE_LOCAL short-circuits to APPLY_WORKDIR/APPLY_OUT."""
    fixes = evidence.get("fixes") if isinstance(evidence.get("fixes"), list) else []
    report = {"applied": 0, "skipped": [], "pushed": False, "close": []}
    if not fixes:
        _write_apply(report)
        return report

    local = os.environ.get("ENGINE_LOCAL", "0") == "1"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr = os.environ.get("PR", "")
    token = os.environ.get("GH_TOKEN") or os.environ.get("PUBLISH_TOKEN")

    if local:
        workdir = os.environ.get("APPLY_WORKDIR")
        results = _apply_fixes.apply_all(workdir, fixes) if workdir else []
    else:
        if not repo or not pr or not token:
            _write_apply(report)
            return report
        head = _pr_head_ref(repo, pr, token)
        if not head:
            _write_apply(report)
            return report
        workdir = tempfile.mkdtemp(prefix="fix-apply-")
        url = f"https://x-access-token:{token}@github.com/{repo}.git"
        if _git(["clone", "--depth", "1", "--branch", head, url, workdir]).returncode != 0:
            shutil.rmtree(workdir, ignore_errors=True)
            _write_apply(report)
            return report
        results = _apply_fixes.apply_all(workdir, fixes)

    applied = [r for r in results if r["status"] == "applied"]
    report["applied"] = len(applied)
    report["skipped"] = [r for r in results if r["status"] != "applied"]
    report["close"] = _issue_targets({r["cluster_id"] for r in applied})

    if applied and not local:
        _commit_push(workdir, repo, pr, token)
        report["pushed"] = True
        _close_issues(repo, report["close"], token)
        shutil.rmtree(workdir, ignore_errors=True)

    _write_apply(report)
    return report


def _pr_head_ref(repo, pr, token):
    env = dict(os.environ); env["GH_TOKEN"] = token
    r = subprocess.run(["gh", "pr", "view", pr, "--repo", repo, "--json", "headRefName",
                        "--jq", ".headRefName"], text=True, capture_output=True, env=env)
    return r.stdout.strip() if r.returncode == 0 else ""


def _commit_push(workdir, repo, pr, token):
    _git(["config", "user.name", "agentic-fix-bot"], workdir)
    _git(["config", "user.email", "agentic-fix-bot@users.noreply.github.com"], workdir)
    _git(["add", "-A"], workdir)
    msg = f"fix: apply AI review remediations (PR #{pr})"
    _git(["commit", "-m", msg], workdir)
    _git(["push", "origin", "HEAD"], workdir, token=token)


def _close_issues(repo, targets, token):
    env = dict(os.environ); env["GH_TOKEN"] = token
    for t in targets:
        listing = subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--label", t["label"],
             "--state", "open", "--json", "number,title"],
            text=True, capture_output=True, env=env)
        try:
            items = json.loads(listing.stdout or "[]")
        except ValueError:
            items = []
        for it in items:
            if t["title"].strip().lower() in (it.get("title") or "").strip().lower():
                subprocess.run(["gh", "issue", "close", str(it["number"]), "--repo", repo,
                                "--comment", "Resolved by the AI fix phase (committed to the PR)."],
                               text=True, capture_output=True, env=env)


def _write_apply(report):
    out = os.environ.get("APPLY_OUT")
    if not out:
        return
    try:
        with open(out, "w") as fh:
            json.dump(report, fh)
    except OSError:
        pass
```

Then, in `main()`, after the line `_post_review(payload)` and before the final `print(json.dumps(...))`, insert:

```python
    apply_report = _apply_commit_close(evidence)
```

And extend the printed `summary` to mention the apply outcome — change the summary f-string to append:

```python
                    f" applied={apply_report['applied']}, pushed={apply_report['pushed']}."
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_demo_conclude_fix_apply.py -q`
Expected: PASS (2 tests). The file `a.py` is rewritten to `x = 1`, and `apply.json` records `applied:1` + a `review:correctness`/`Bad default` close-target.

- [ ] **Step 5: Confirm no regression in the existing suite**

Run: `uv run pytest tests/ -q`
Expected: all prior tests pass + the new ones.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/protocols/code-review-demo/publish/conclude-fix.py tests/test_demo_conclude_fix_apply.py
git commit -m "feat(demo): conclude-fix applies patches to PR head, pushes, closes resolved issues"
```

---

## Task 6: `conclude-triage` links the open issues in its gate comment

**Files:**
- Modify: `.github/agent-factory/protocols/code-review-demo/publish/conclude-triage.py`
- Test: `tests/test_demo_conclude_triage_links.py`

**Interfaces:**
- Consumes: the same triage evidence (clusters); env `GITHUB_REPOSITORY`, `GH_TOKEN`, `ENGINE_LOCAL`, optional `TRIAGE_COMMENT_OUT`.
- Produces: the existing gate comment, with a trailing "Linked issues:" block listing the `review:<dim>` issues matched per cluster (resolved by label+title). In `ENGINE_LOCAL`, no API calls; the matched labels/titles are derived from clusters only (issue numbers omitted) and written to `TRIAGE_COMMENT_OUT`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_demo_conclude_triage_links.py`:

```python
import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/code-review-demo/publish/conclude-triage.py"


def test_comment_lists_linked_issue_keys(tmp_path):
    evidence = tmp_path / "triage.json"
    evidence.write_text(json.dumps({
        "clusters": [{"cluster_id": "c1", "title": "Bad default",
                      "dimension": ["correctness"], "severity": "high",
                      "paths": ["a.py"], "rank": 1,
                      "member_findings": [{"dimension": "correctness", "path": "a.py",
                                           "line": 1, "severity": "high", "title": "Bad default"}]}],
        "summary": {"present": ["correctness"], "missing": ["test", "performance", "security", "maintainability"],
                    "clusters": 1, "total_findings": 1, "by_severity": {"high": 1},
                    "by_dimension": {"correctness": 1}}
    }))
    comment_out = tmp_path / "comment.txt"
    env = dict(os.environ)
    env.update({"ENGINE_LOCAL": "1", "TRIAGE_COMMENT_OUT": str(comment_out),
                "GITHUB_REPOSITORY": "acme/repo", "PR": "8"})
    r = subprocess.run(["python3", str(HOOK), str(evidence), "pr-8"],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    body = comment_out.read_text()
    assert "review:correctness" in body and "Bad default" in body
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_demo_conclude_triage_links.py -q`
Expected: FAIL (no "Linked issues" content yet).

- [ ] **Step 3: Add issue-linking to the demo `conclude-triage.py`**

In `code-review-demo/publish/conclude-triage.py`, add a helper and append its output inside `_comment(triage)`:

```python
def _linked_issue_lines(clusters):
    seen, lines = set(), []
    for c in sorted(clusters, key=lambda x: x.get("rank") or 999):
        for m in c.get("member_findings") or []:
            if not isinstance(m, dict):
                continue
            dim, title = m.get("dimension"), m.get("title")
            if not dim or not title:
                continue
            key = (f"review:{dim}", title)
            if key not in seen:
                seen.add(key)
                lines.append(f"- `{key[0]}` — {title}")
    return lines
```

Then in `_comment(...)`, before `return "\n".join(lines)`, add:

```python
    linked = _linked_issue_lines(clusters)
    if linked:
        lines.append("")
        lines.append("Linked issues:")
        lines.extend(linked)
```

(Live issue-number resolution via `gh issue list` is optional polish; the label+title key is enough for the demo and keeps `ENGINE_LOCAL` API-free.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_demo_conclude_triage_links.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/protocols/code-review-demo/publish/conclude-triage.py tests/test_demo_conclude_triage_links.py
git commit -m "feat(demo): conclude-triage links domain issues in the gate comment"
```

---

## Task 7: Regression gate — lint, full suite, compile, protocol catalog

**Files:**
- Modify: `tests/test_dist_min_engine_version.py` (add `code-review-demo` to the iterated list)

**Interfaces:** none (verification task).

- [ ] **Step 1: Add the demo protocol to the min-engine-version coverage test**

In `tests/test_dist_min_engine_version.py`, add `"code-review-demo"` to the list of protocol names it iterates (find the `["code-review", "code-review-v1", …]` literal and append `"code-review-demo"`).

- [ ] **Step 2: Lint the demo protocol explicitly**

Run: `python3 .github/agent-factory/engine/protocol-lint.py .github/agent-factory/protocols/code-review-demo/protocol.json`
Expected: exits 0; prints the `review → triage → fix` tree.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: all pass (630 prior + the new demo tests; 0 failures).

- [ ] **Step 4: Compile all gh-aw agents, then restore check exec bits**

Run:
```bash
gh aw compile
chmod 755 .github/agent-factory/protocols/code-review-demo/checks/*.py \
          .github/agent-factory/protocols/code-review-demo/publish/conclude-*.py
git diff --stat
```
Expected: no compile errors; `git status` shows the expected `.lock.yml` changes (5 demo review agents + fix-agent) and the chmod restores any flipped `100644` check bits back to `100755`.

- [ ] **Step 5: Verify the demo check scripts are executable in git**

Run: `git ls-files -s .github/agent-factory/protocols/code-review-demo/checks .github/agent-factory/protocols/code-review-demo/publish | grep -v '100755' | grep -E '\.py$' || echo "all .py are 100755"`
Expected: prints `all .py are 100755` (every check/conclude script keeps its exec bit).

- [ ] **Step 6: Commit**

```bash
git add tests/test_dist_min_engine_version.py .github/agent-factory/protocols/code-review-demo
git commit -m "test(demo): cover code-review-demo in min-engine-version + lint gate"
```

---

## Task 8: Deploy to yuanrong-datasystem and run on PR #8

> **This task performs outward-facing actions on a public repo. Do NOT execute it until the user explicitly approves running the live demo.** It is documented here so the runbook is complete.

**Files:** none in this repo (operates on `SiRumCz/yuanrong-datasystem`).

- [ ] **Step 1: Push the demo branch so `dist/install.sh` can fetch from it**

```bash
git push -u origin feat/multi-ai-review-issue-demo
```
(install.sh fetches blobs from `--source <owner/repo> --ref <branch>`.)

- [ ] **Step 2: Install the demo protocol into yuanrong on its default branch**

From a clone of `SiRumCz/yuanrong-datasystem` (default branch `main`):
```bash
curl -fsSL https://raw.githubusercontent.com/golivax/agentic-protocol-poc/feat/multi-ai-review-issue-demo/dist/install.sh \
  | bash -s -- install code-review-demo \
      --source golivax/agentic-protocol-poc --ref feat/multi-ai-review-issue-demo
```
Expected: it fetches the engine, the `code-review-demo` protocol dir, and the referenced agent workflows (`demo-review-*-agent`, `triage-agent`, `fix-agent`), then commits `chore: install agentic protocol(s): code-review-demo` on `main`.

- [ ] **Step 3: Set the required secrets on yuanrong**

The installer prompts for / sets `POC_DISPATCH_TOKEN` (PAT, repo+workflow scopes — also used by the fix applier to push), and the codex gateway secret (`OPENAI_API_KEY`) the agents reference. Verify:
```bash
gh secret list --repo SiRumCz/yuanrong-datasystem
```
Expected: `POC_DISPATCH_TOKEN` and `OPENAI_API_KEY` present. (The gateway URL is literal in the agent frontmatter; the `823dd6a` dist fix keeps `engine.env.OPENAI_BASE_URL` after `gh aw add`.)

- [ ] **Step 4: Confirm the agent locks landed and are on the default branch**

```bash
gh api repos/SiRumCz/yuanrong-datasystem/contents/.github/workflows?ref=main \
  --jq '.[].name' | grep -E 'demo-review-|triage-agent|fix-agent|agentic-'
```
Expected: the 5 `demo-review-*-agent.lock.yml`, `triage-agent.lock.yml`, `fix-agent.lock.yml`, `agentic-orchestrator.yml`, `agentic-engine.yml`.

- [ ] **Step 5: Trigger the demo on PR #8**

```bash
gh pr comment 8 --repo SiRumCz/yuanrong-datasystem --body "/demo-review"
```

- [ ] **Step 6: Observe the run end-to-end**

```bash
# the 5 review legs + join + triage + fix runs
gh run list --repo SiRumCz/yuanrong-datasystem --limit 15
# issues the reviewers opened
gh issue list --repo SiRumCz/yuanrong-datasystem --label ai-review --state all
# the fix commit on the PR head
gh pr view 8 --repo SiRumCz/yuanrong-datasystem --json commits --jq '.commits[-1].messageHeadline'
```
Expected: up to 5 domain-labeled issues, a triage gate comment linking them, a `fix: apply AI review remediations (PR #8)` commit on the PR branch, and the resolved issues closed.

> **Live caveats:** R1 — a domain may yield no findings (its label bucket stays empty); R2 — the applier skips any fix whose `original_line` drifted (recorded, not forced); R3 — issue close is by label+title match, so two findings with an identical title close together. If pushing fails (e.g. fork PR), the applier records and the engine still completes.

---

## Self-Review (completed during authoring)

- **Spec coverage:** D1 (issues-only) → Task 1 drops `publish`; reviewers→issues → Task 2; triage links → Task 6; fix commits+closes → Tasks 4–5; slim sequence → Task 1; deploy to yuanrong PR #8 → Task 8. D2 revision (sibling protocol) → Task 1. `original_line`/R2 → Tasks 3–4. Idempotency/R3 (max_iterations:1) → Task 1 protocol + test. No-engine-change constraint → applier uses the advance job's existing PAT (Task 5).
- **Placeholder scan:** none — every code step shows full content.
- **Type consistency:** `apply_fix`/`apply_all` signatures and the `{cluster_id,path,status,detail}` result shape are identical in Tasks 4 and 5; `_issue_targets` consumes `{"label","title"}` matching the test's `rep["close"]` assertions; conclude-fix env names (`APPLY_WORKDIR`, `APPLY_OUT`, `CONCLUDE_INPUTS_DIR`, `GH_TOKEN`) match the engine's `advance` env and the tests.
