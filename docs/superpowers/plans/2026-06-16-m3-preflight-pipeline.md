# M3 — Preflight Gate Port + Combined `code-review-pipeline` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the custody preflight gate onto this engine and chain it before the existing review fan-out as one `code-review-pipeline` protocol (preflight → review → join → done, `on_blocked: halt`), live-tested both paths.

**Architecture:** Author a new protocol under `.github/agent-factory/protocols/code-review-pipeline/` — deterministic/advisory checks (Python ports of custody's `checks.js`/`locate.js`), an `adherence-coverage` form-check, a gh-aw preflight agent, and `conclude`/`publish` hooks — plus one small generic-engine addition (PR body/title in the checks-job env). The engine's multi-phase machinery (conclude hook, `on_blocked: halt`, phase relay) already exists and is NOT modified.

**Tech Stack:** Python 3 + PyYAML (engine/checks runtime), pytest (dev tests), gh-aw (agent compile), GitHub Actions reusable workflows, actionlint.

**Spec:** `docs/superpowers/specs/2026-06-16-m3-preflight-pipeline-spec.md` (and its parent `2026-06-16-code-review-pipeline-design.md`).
**Port source (read-only reference):** `/home/gustavo/huawei/new-custody/custody/app/backend/component/preflight/` — `workflow/checks.js`, `workflow/scripts/locate.js`, `workflow/registry.js`, `workflow/scripts/merge-verdict.js`, `workflow/preflight-gate.md`.

## Global Constraints

- **Check ABI:** `<check> <evidence.json> <diff.txt> <changed-files.txt>` → prints ONE JSON object `{"check","pass","feedback"}` to stdout, **always exits 0** (non-zero is reserved for runner errors). Node-scoped config via `CHECK_PARAMS` env. `on_fail` is protocol data stamped by `run-checks.py`, never check stdout.
- **Engine is NOT modified except `run-checks.py`/`agentic-engine.yml` for PR-body env (Task 1).** Do NOT touch `next.py`, `advance.py`, `lib.py` logic, `join.py`, `protocol-join.yml`. The conclude/`on_blocked`/multiphase substrate already exists.
- **`spec-present`/`plan-present` detect from changed-files ONLY** — a committed spec/plan FILE in the PR diff. Do NOT port custody's PR-body detection (`detectSpecInBody`/`detectPlanInBody`) for presence. `on_fail: block`.
- **`spec-present`/`plan-present` severity is `block`** (the deliberate divergence from custody's advisory warn). `docs-updated`/`tests-updated` are `advisory`.
- **LLM endpoint = Claude sonnet** (`ANTHROPIC_*` secrets, model `claude-sonnet-4-6`), copied from `grumpy-agent.md` — NOT custody's codex/gpt-5.5.
- **Triggers:** `code-review-pipeline` = `/review` + PR opened/reopened/synchronize; `multi-grumpy` drops PR triggers → `/grumpy` comment-only. No two protocols may match one event (B→A errors on ambiguity).
- **Conclude hook ABI:** `<hook> <evidence.json> <instance-key>`, reads `BLOCKING` (`"1"` blocking / `"0"` not — that's exactly what `advance.py:run_conclude_hook` sends) via env, prints `{"conclusion","summary","blocked"}` to stdout. Resolved from `<protocol-dir>/publish/`. Trusted (zone 4).
- **Publish hook ABI:** `<hook> <evidence.json> <instance-key>` with env `ENGINE_LOCAL`, `GITHUB_REPOSITORY`, `PUBLISH_TOKEN`, `PR`; prints `{"conclusion","summary"}`. Trusted (zone 4).
- Runtime deps: Python 3 + PyYAML only. Tests are pytest under `tests/test_*.py`; full suite currently **237** and must stay green.
- Checks share helpers via a sibling module imported with `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` (the check resolves its own dir; run-checks.py invokes it by absolute path).

---

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `.github/workflows/agentic-engine.yml` | checks job fetches PR body/title into env | T1 |
| `tests/test_pr_body_env.py` | proves a check receives `PR_BODY`/`PR_TITLE` via run-checks.py | T1 |
| `protocols/code-review-pipeline/checks/_paths.py` | shared path classifiers + spec/plan matchers | T2 |
| `protocols/code-review-pipeline/checks/spec-present.py` | `block`; spec file in diff | T2 |
| `protocols/code-review-pipeline/checks/plan-present.py` | `block`; plan file in diff | T2 |
| `protocols/code-review-pipeline/checks/docs-updated-with-code.py` | `advisory` | T2 |
| `protocols/code-review-pipeline/checks/tests-updated-with-code.py` | `advisory` | T2 |
| `tests/test_preflight_checks.py` | unit tests for the four above | T2 |
| `protocols/code-review-pipeline/checks/adherence-coverage.py` | `iterate`; expected set from changed-files | T3 |
| `protocols/code-review-pipeline/checks/schema-valid.py` | `iterate`; preflight evidence shape | T3 |
| `protocols/code-review-pipeline/checks/traces-exist-in-diff.py` | `iterate`; verbatim copy | T3 |
| `tests/test_preflight_coverage.py` | unit tests for adherence-coverage + schema-valid | T3 |
| `protocols/code-review-pipeline/preflight.evidence.schema.json` | rubric schema | T4 |
| `.github/workflows/preflight-agent.md` (+ `.lock.yml`) | gh-aw agent: prefetch+scope→evidence | T4 |
| `protocols/code-review-pipeline/publish/conclude-preflight.py` | conclude roll-up + verdict.json payload | T5 |
| `protocols/code-review-pipeline/publish/publish-verdict.py` | write verdict.json artifact + sub check-run | T5 |
| `tests/test_conclude_preflight.py` | unit tests for conclude + verdict shape | T5 |
| `protocols/code-review-pipeline/protocol.json` | 3-phase protocol + triggers | T6 |
| `protocols/multi-grumpy/protocol.json` | drop PR triggers | T6 |
| `tests/test_route.py` | extend real-protocols route assertions | T6 |

Task order: **T1** (engine env) → **T2** (det/advisory checks) → **T3** (form checks) → **T4** (agent+schema) → **T5** (conclude/publish) → **T6** (protocol assembly + triggers) → **T7** (actionlint + live).

---

### Task 1: PR body/title in the checks-job env

**Files:**
- Modify: `.github/workflows/agentic-engine.yml` (the checks job's run-checks step — fetch body/title, export before calling `run-checks.py`)
- Modify: `.github/agent-factory/engine/run-checks.py` (document + ensure PR_BODY/PR_TITLE pass through; they already do via `dict(os.environ)` — add an explicit comment and keep behavior)
- Test: `tests/test_pr_body_env.py` (create)

**Interfaces:**
- Consumes: existing `run-checks.py` `child_env = dict(os.environ)` (forwards all env to checks).
- Produces: contract that `PR_BODY` and `PR_TITLE` env vars, when set in the checks job, reach every check unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pr_body_env.py`:

```python
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"


def _probe_check(tmp_path):
    """A check that echoes PR_BODY/PR_TITLE from its env into feedback."""
    c = tmp_path / "echo-pr.py"
    c.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "print(json.dumps({'check':'echo-pr','pass':True,"
        "'feedback':'body=' + os.environ.get('PR_BODY','') + '|title=' + os.environ.get('PR_TITLE','')}))\n"
    )
    c.chmod(0o755)
    return c


def _protocol(tmp_path, check_path):
    p = tmp_path / "protocol.json"
    p.write_text(json.dumps({
        "name": "probe",
        "states": [{"id": "s", "kind": "agent",
                    "checks": [{"run": "echo-pr", "exec": str(check_path)}]}],
    }))
    return p


def test_checks_receive_pr_body_and_title(tmp_path):
    check = _probe_check(tmp_path)
    proto = _protocol(tmp_path, check)
    ev = tmp_path / "evidence.json"; ev.write_text("{}")
    diff = tmp_path / "diff.txt"; diff.write_text("")
    files = tmp_path / "files.txt"; files.write_text("")
    env = dict(os.environ)
    env["PR_BODY"] = "## Requirements\nDo the thing"
    env["PR_TITLE"] = "My PR"
    r = subprocess.run(
        ["python3", str(ENGINE / "run-checks.py"), str(proto), "s", str(ev), str(diff), str(files)],
        text=True, capture_output=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    verdict = out["results"][0]
    assert "body=## Requirements" in verdict["feedback"]
    assert "title=My PR" in verdict["feedback"]
```

- [ ] **Step 2: Run the test to verify it passes or fails**

Run: `pytest tests/test_pr_body_env.py -v`
Expected: PASS already if `run-checks.py` forwards `dict(os.environ)` to checks (it does today). If it FAILS, `run-checks.py` is filtering env — fix Step 3. (This test pins the contract regardless.)

- [ ] **Step 3: Make the env passthrough explicit in `run-checks.py`**

In `.github/agent-factory/engine/run-checks.py`, find the line `child_env = dict(os.environ)` (≈ line 99) and add a comment directly above it (no behavior change — `dict(os.environ)` already forwards `PR_BODY`/`PR_TITLE` the checks job exports):

```python
        # child_env inherits the full job environment, so PR_BODY / PR_TITLE
        # (exported by the checks job for checks that parse the PR description/
        # title) reach every check alongside CHECK_PARAMS. Keep this passthrough.
        child_env = dict(os.environ)
```

- [ ] **Step 4: Export PR body/title in the checks job**

In `.github/workflows/agentic-engine.yml`, find the checks job step that fetches the diff and runs `run-checks.py` (the step around lines 271–290 with `gh pr diff ... > /tmp/diff.txt`). Immediately AFTER the two `gh pr diff` lines and BEFORE the `run-checks.py` invocation, add:

```bash
          # Make the PR description + title available to checks (env, never
          # interpolated into this run: block). Fetched from GitHub by this
          # trusted job — not from agent output. Multiline body stays in a shell
          # var; run-checks.py forwards the job env to each check.
          PR_BODY="$(gh pr view "${{ needs.plan.outputs.pr }}" --repo "${{ github.repository }}" --json body --jq '.body' 2>/dev/null || true)"
          PR_TITLE="$(gh pr view "${{ needs.plan.outputs.pr }}" --repo "${{ github.repository }}" --json title --jq '.title' 2>/dev/null || true)"
          export PR_BODY PR_TITLE
```

(`${{ needs.plan.outputs.pr }}` is a numeric PR number, not agent-derived — safe to interpolate. The body/title themselves flow only through the exported shell vars, never into the `run:` text.)

- [ ] **Step 5: Validate YAML + run the suite**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/agentic-engine.yml'))" && echo OK`
Run: `pytest tests/test_pr_body_env.py tests/ -q`
Expected: `OK`; full suite green (238 = 237 + 1 new).

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/agentic-engine.yml .github/agent-factory/engine/run-checks.py tests/test_pr_body_env.py
git commit -m "feat(engine): expose PR_BODY/PR_TITLE to checks via job env

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Deterministic + advisory checks (spec/plan/docs/tests present)

**Files:**
- Create: `.github/agent-factory/protocols/code-review-pipeline/checks/_paths.py`
- Create: `.github/agent-factory/protocols/code-review-pipeline/checks/spec-present.py`
- Create: `.github/agent-factory/protocols/code-review-pipeline/checks/plan-present.py`
- Create: `.github/agent-factory/protocols/code-review-pipeline/checks/docs-updated-with-code.py`
- Create: `.github/agent-factory/protocols/code-review-pipeline/checks/tests-updated-with-code.py`
- Test: `tests/test_preflight_checks.py`

**Interfaces:**
- Consumes: check ABI `<check> <evidence.json> <diff.txt> <changed-files.txt>` → `{check,pass,feedback}`, exit 0.
- Produces: `_paths.py` exporting `is_doc(p)`, `is_test(p)`, `is_code(p)`, `is_spec_path(p)`, `is_plan_path(p)`, `read_changed_files(path) -> list[str]`. Used by these checks AND Task 3's `adherence-coverage`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_preflight_checks.py`:

```python
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / ".github/agent-factory/protocols/code-review-pipeline/checks"
sys.path.insert(0, str(CHECKS))


def _run(check_name, changed_files, tmp_path):
    """Invoke a check with an empty evidence + diff and the given changed-files."""
    from conftest import run_check  # provided by tests/conftest.py
    ev = tmp_path / "ev.json"; ev.write_text("{}")
    diff = tmp_path / "diff.txt"; diff.write_text("")
    files = tmp_path / "files.txt"; files.write_text("\n".join(changed_files) + "\n")
    return run_check(CHECKS / check_name, ev, diff, files)


# _paths classifiers -----------------------------------------------------------

def test_paths_classifiers():
    import _paths as P
    assert P.is_spec_path("docs/specs/foo.md")
    assert P.is_spec_path("docs/superpowers/specs/x.md")
    assert P.is_spec_path("REQUIREMENTS.md")
    assert not P.is_spec_path("src/app.py")
    assert P.is_plan_path("docs/superpowers/plans/p.md")
    assert P.is_plan_path("PLAN.md")
    assert not P.is_plan_path("docs/specs/foo.md")
    assert P.is_doc("README.md") and P.is_doc("docs/x.md")
    assert P.is_test("tests/test_x.py") and P.is_test("foo.test.js")
    assert P.is_code("src/app.py")
    assert not P.is_code("README.md") and not P.is_code("tests/test_x.py")


# spec-present / plan-present (block) ------------------------------------------

def test_spec_present_passes_when_spec_file_in_diff(tmp_path):
    v = _run("spec-present.py", ["docs/specs/feature.md", "src/app.py"], tmp_path)
    assert v["check"] == "spec-present" and v["pass"] is True


def test_spec_present_fails_when_absent(tmp_path):
    v = _run("spec-present.py", ["src/app.py", "README.md"], tmp_path)
    assert v["pass"] is False and "spec" in v["feedback"].lower()


def test_plan_present_passes_with_plan_file(tmp_path):
    v = _run("plan-present.py", ["docs/superpowers/plans/p.md"], tmp_path)
    assert v["pass"] is True


def test_plan_present_fails_when_absent(tmp_path):
    v = _run("plan-present.py", ["src/app.py"], tmp_path)
    assert v["pass"] is False


# docs/tests-updated (advisory) ------------------------------------------------

def test_docs_updated_pass_when_docs_changed(tmp_path):
    v = _run("docs-updated-with-code.py", ["src/app.py", "docs/guide.md"], tmp_path)
    assert v["pass"] is True


def test_docs_updated_warn_when_code_only(tmp_path):
    v = _run("docs-updated-with-code.py", ["src/app.py"], tmp_path)
    assert v["pass"] is False and "doc" in v["feedback"].lower()


def test_docs_updated_pass_when_no_code(tmp_path):
    v = _run("docs-updated-with-code.py", ["README.md"], tmp_path)
    assert v["pass"] is True  # no code changed → not applicable → pass


def test_tests_updated_pass_when_tests_changed(tmp_path):
    v = _run("tests-updated-with-code.py", ["src/app.py", "tests/test_app.py"], tmp_path)
    assert v["pass"] is True


def test_tests_updated_warn_when_code_only(tmp_path):
    v = _run("tests-updated-with-code.py", ["src/app.py"], tmp_path)
    assert v["pass"] is False and "test" in v["feedback"].lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_preflight_checks.py -q`
Expected: FAIL (`_paths` import error / checks missing).

- [ ] **Step 3: Create `_paths.py`**

Create `.github/agent-factory/protocols/code-review-pipeline/checks/_paths.py` (Python port of custody `checks.js` classifiers + `locate.js` `classifyArtifactPaths`; **path-only, no PR-body**):

```python
"""Shared path classifiers for the preflight checks. Ports custody's
checks.js (isDocFile/isTestFile/isCodeFile) + locate.js (spec/plan path arms).
Imported by spec/plan/docs/tests-present and adherence-coverage."""
import re

_DOC = re.compile(r"\.(md|mdx|rst|adoc|txt)$", re.I)
_DOC_DIR = re.compile(r"(^|/)docs?/", re.I)
_TEST = re.compile(r"(\.|_)(test|spec)\.[a-z0-9]+$", re.I)
_TEST_DIR = re.compile(r"(^|/)(tests?|__tests__|spec)/", re.I)
_EXT = re.compile(r"\.[a-z0-9]+$", re.I)

# Spec/plan artifact paths. Precise to avoid the bare spec/ test-dir collision:
# docs/specs, docs/superpowers/specs, top-level specs/, and SPEC/REQUIREMENTS.md.
_SPEC = re.compile(r"(^|/)docs/(superpowers/)?specs/|(^|/)(SPEC|REQUIREMENTS)\.md$|^specs/", re.I)
_PLAN = re.compile(r"(^|/)docs/(superpowers/)?plans?/|(^|/)PLAN\.md$|^plans?/", re.I)


def is_doc(p):  return bool(_DOC.search(p) or _DOC_DIR.search(p))
def is_test(p): return bool(_TEST.search(p) or _TEST_DIR.search(p))
def is_code(p): return not is_doc(p) and not is_test(p) and bool(_EXT.search(p))
def is_spec_path(p): return bool(_SPEC.search(p))
def is_plan_path(p): return bool(_PLAN.search(p))


def read_changed_files(path):
    """Read the changed-files list (one path per line); blanks dropped."""
    try:
        with open(path) as fh:
            return [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return []
```

- [ ] **Step 4: Create `spec-present.py` and `plan-present.py`**

Create `.github/agent-factory/protocols/code-review-pipeline/checks/spec-present.py`:

```python
#!/usr/bin/env python3
"""Check: a spec/requirements FILE is present in the PR diff (changed-files).
Changed-files-only (no PR body). on_fail: block — absence blocks the pipeline.
Usage: spec-present.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402

SEARCHED = "docs/specs/, docs/superpowers/specs/, specs/, SPEC.md, REQUIREMENTS.md"


def main():
    files = _paths.read_changed_files(sys.argv[3])
    hits = [f for f in files if _paths.is_spec_path(f)]
    if hits:
        print(json.dumps({"check": "spec-present", "pass": True,
                          "feedback": f"Spec artifact in diff: {hits[0]}"}))
    else:
        print(json.dumps({"check": "spec-present", "pass": False,
                          "feedback": f"No spec/requirements file in the PR diff (searched: {SEARCHED})."}))


if __name__ == "__main__":
    main()
```

Create `plan-present.py` (identical structure, plan arm):

```python
#!/usr/bin/env python3
"""Check: an implementation-plan FILE is present in the PR diff (changed-files).
Changed-files-only (no PR body). on_fail: block.
Usage: plan-present.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402

SEARCHED = "docs/plans/, docs/superpowers/plans/, plans/, PLAN.md"


def main():
    files = _paths.read_changed_files(sys.argv[3])
    hits = [f for f in files if _paths.is_plan_path(f)]
    if hits:
        print(json.dumps({"check": "plan-present", "pass": True,
                          "feedback": f"Plan artifact in diff: {hits[0]}"}))
    else:
        print(json.dumps({"check": "plan-present", "pass": False,
                          "feedback": f"No implementation-plan file in the PR diff (searched: {SEARCHED})."}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Create `docs-updated-with-code.py` and `tests-updated-with-code.py`**

Create `docs-updated-with-code.py`:

```python
#!/usr/bin/env python3
"""Check (advisory): if code files changed, docs should change too.
Usage: docs-updated-with-code.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402


def main():
    files = _paths.read_changed_files(sys.argv[3])
    code = [f for f in files if _paths.is_code(f)]
    docs = [f for f in files if _paths.is_doc(f)]
    if not code:
        print(json.dumps({"check": "docs-updated-with-code", "pass": True,
                          "feedback": "No code files changed; doc-coherence not applicable."}))
    elif docs:
        print(json.dumps({"check": "docs-updated-with-code", "pass": True,
                          "feedback": f"Docs updated alongside code ({len(docs)} doc file(s))."}))
    else:
        print(json.dumps({"check": "docs-updated-with-code", "pass": False,
                          "feedback": f"Code changed ({len(code)} file(s)) but no documentation was updated."}))


if __name__ == "__main__":
    main()
```

Create `tests-updated-with-code.py` (identical structure, swap docs→tests via `_paths.is_test`):

```python
#!/usr/bin/env python3
"""Check (advisory): if code files changed, tests should change too.
Usage: tests-updated-with-code.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402


def main():
    files = _paths.read_changed_files(sys.argv[3])
    code = [f for f in files if _paths.is_code(f)]
    tests = [f for f in files if _paths.is_test(f)]
    if not code:
        print(json.dumps({"check": "tests-updated-with-code", "pass": True,
                          "feedback": "No code files changed; test-coherence not applicable."}))
    elif tests:
        print(json.dumps({"check": "tests-updated-with-code", "pass": True,
                          "feedback": f"Tests updated alongside code ({len(tests)} test file(s))."}))
    else:
        print(json.dumps({"check": "tests-updated-with-code", "pass": False,
                          "feedback": f"Code changed ({len(code)} file(s)) but no tests were added or updated."}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: chmod + run tests**

Run: `chmod +x .github/agent-factory/protocols/code-review-pipeline/checks/*.py`
Run: `pytest tests/test_preflight_checks.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add .github/agent-factory/protocols/code-review-pipeline/checks/ tests/test_preflight_checks.py
git commit -m "feat(preflight): port spec/plan/docs/tests-present checks (changed-files only)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Form checks — `adherence-coverage`, `schema-valid`, `traces-exist-in-diff`

**Files:**
- Create: `.github/agent-factory/protocols/code-review-pipeline/checks/adherence-coverage.py`
- Create: `.github/agent-factory/protocols/code-review-pipeline/checks/schema-valid.py`
- Copy: `.github/agent-factory/protocols/code-review-pipeline/checks/traces-exist-in-diff.py` (verbatim from multi-grumpy)
- Test: `tests/test_preflight_coverage.py`

**Interfaces:**
- Consumes: `_paths.is_spec_path`/`is_plan_path`/`read_changed_files` (Task 2); `CHECK_PARAMS` env (`{"ai_checks":["spec-adherence","plan-adherence"]}`).
- Produces: evidence shape contract — preflight evidence is `{"checks":[{"id":<ai_check_id>,"status":"pass|fail|warn",...}], "examined":[...]}` (Task 4 writes it; `adherence-coverage`/`schema-valid` validate it).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_preflight_coverage.py`:

```python
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / ".github/agent-factory/protocols/code-review-pipeline/checks"


def _run(check_name, evidence_obj, changed_files, tmp_path, params=None):
    from conftest import run_check
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps(evidence_obj))
    diff = tmp_path / "diff.txt"; diff.write_text("")
    files = tmp_path / "files.txt"; files.write_text("\n".join(changed_files) + "\n")
    return run_check(CHECKS / check_name, ev, diff, files, check_params=params)


AI_PARAMS = {"ai_checks": ["spec-adherence", "plan-adherence"]}


def _evidence(ids):
    return {"checks": [{"id": i, "status": "pass", "summary": "ok", "evidence": []} for i in ids],
            "examined": ["src/app.py"]}


# adherence-coverage: expected set derived from changed-files -------------------

def test_coverage_ok_both_artifacts_present(tmp_path):
    v = _run("adherence-coverage.py", _evidence(["spec-adherence", "plan-adherence"]),
             ["docs/specs/s.md", "docs/superpowers/plans/p.md", "src/app.py"], tmp_path, AI_PARAMS)
    assert v["check"] == "adherence-coverage" and v["pass"] is True


def test_coverage_spec_only_expects_only_spec_adherence(tmp_path):
    # plan file absent → plan-adherence must NOT appear; spec-adherence must.
    v = _run("adherence-coverage.py", _evidence(["spec-adherence"]),
             ["docs/specs/s.md", "src/app.py"], tmp_path, AI_PARAMS)
    assert v["pass"] is True


def test_coverage_missing_expected_verdict_fails(tmp_path):
    # spec file present but spec-adherence not judged → fail.
    v = _run("adherence-coverage.py", _evidence([]),
             ["docs/specs/s.md", "src/app.py"], tmp_path, AI_PARAMS)
    assert v["pass"] is False and "spec-adherence" in v["feedback"]


def test_coverage_unexpected_verdict_fails(tmp_path):
    # no spec/plan file → no adherence expected, but agent judged spec-adherence → fail.
    v = _run("adherence-coverage.py", _evidence(["spec-adherence"]),
             ["src/app.py"], tmp_path, AI_PARAMS)
    assert v["pass"] is False


def test_coverage_neither_artifact_empty_evidence_passes(tmp_path):
    v = _run("adherence-coverage.py", _evidence([]), ["src/app.py"], tmp_path, AI_PARAMS)
    assert v["pass"] is True


# schema-valid: preflight evidence shape ---------------------------------------

def test_schema_valid_ok(tmp_path):
    v = _run("schema-valid.py", _evidence(["spec-adherence"]), ["docs/specs/s.md"], tmp_path, AI_PARAMS)
    assert v["check"] == "schema-valid" and v["pass"] is True


def test_schema_valid_rejects_bad_status(tmp_path):
    bad = {"checks": [{"id": "spec-adherence", "status": "MAYBE"}], "examined": ["x"]}
    v = _run("schema-valid.py", bad, ["docs/specs/s.md"], tmp_path, AI_PARAMS)
    assert v["pass"] is False


def test_schema_valid_rejects_missing_checks_key(tmp_path):
    v = _run("schema-valid.py", {"examined": ["x"]}, ["docs/specs/s.md"], tmp_path, AI_PARAMS)
    assert v["pass"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_preflight_coverage.py -q`
Expected: FAIL (checks missing).

- [ ] **Step 3: Create `adherence-coverage.py`**

```python
#!/usr/bin/env python3
"""Check: the agent judged exactly the adherence checks that the PR's committed
artifacts call for — spec file in diff ⇒ spec-adherence judged once; plan file ⇒
plan-adherence; absent ⇒ that check must NOT appear (it was correctly scoped out).
Expected set is derived from changed-files (NOT from agent output), so zone 3 stays
independent. Usage: adherence-coverage.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402

# Which ai_check id maps to which artifact presence.
ARTIFACT_OF = {"spec-adherence": _paths.is_spec_path, "plan-adherence": _paths.is_plan_path}


def main():
    try:
        ai_checks = json.loads(os.environ.get("CHECK_PARAMS", "")).get("ai_checks")
    except (ValueError, AttributeError):
        ai_checks = None
    if not isinstance(ai_checks, list) or not ai_checks:
        print(json.dumps({"check": "adherence-coverage", "pass": False,
                          "feedback": "no ai_checks in CHECK_PARAMS (engine must pass params.ai_checks)"}))
        return

    files = _paths.read_changed_files(sys.argv[3])
    expected = set()
    for cid in ai_checks:
        matcher = ARTIFACT_OF.get(cid)
        if matcher and any(matcher(f) for f in files):
            expected.add(cid)

    try:
        with open(sys.argv[1]) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    judged = []
    if isinstance(evidence, dict):
        for c in evidence.get("checks", []) or []:
            if isinstance(c, dict) and c.get("id"):
                judged.append(c["id"])
    judged_set = set(judged)

    missing = expected - judged_set
    unexpected = (judged_set & set(ai_checks)) - expected
    dups = sorted({c for c in judged if judged.count(c) > 1})
    problems = []
    if missing:    problems.append(f"missing verdict(s): {sorted(missing)}")
    if unexpected: problems.append(f"unexpected verdict(s) (no artifact in diff): {sorted(unexpected)}")
    if dups:       problems.append(f"duplicate verdict(s): {dups}")
    if problems:
        print(json.dumps({"check": "adherence-coverage", "pass": False,
                          "feedback": "adherence coverage off: " + "; ".join(problems)}))
    else:
        print(json.dumps({"check": "adherence-coverage", "pass": True,
                          "feedback": f"adherence coverage complete (expected {sorted(expected)})."}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create `schema-valid.py`**

```python
#!/usr/bin/env python3
"""Check: the preflight evidence has the required shape — a `checks` list whose
entries each carry an `id` and a `status` in {pass,fail,warn}, plus an `examined`
list. Reports the shape only (coverage/anchors are other checks).
Usage: schema-valid.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import sys

OK_STATUS = {"pass", "fail", "warn"}


def main():
    try:
        with open(sys.argv[1]) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError) as exc:
        print(json.dumps({"check": "schema-valid", "pass": False,
                          "feedback": f"evidence unreadable/not JSON: {exc}"}))
        return
    problems = []
    if not isinstance(evidence, dict):
        problems.append("evidence is not a JSON object")
    else:
        checks = evidence.get("checks")
        if not isinstance(checks, list):
            problems.append("missing or non-list `checks`")
        else:
            for i, c in enumerate(checks):
                if not isinstance(c, dict) or not c.get("id"):
                    problems.append(f"checks[{i}] missing `id`")
                elif c.get("status") not in OK_STATUS:
                    problems.append(f"checks[{i}] status {c.get('status')!r} not in {sorted(OK_STATUS)}")
        if not isinstance(evidence.get("examined"), list):
            problems.append("missing or non-list `examined`")
    if problems:
        print(json.dumps({"check": "schema-valid", "pass": False,
                          "feedback": "schema invalid: " + "; ".join(problems[:6])}))
    else:
        print(json.dumps({"check": "schema-valid", "pass": True, "feedback": ""}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Copy `traces-exist-in-diff.py` verbatim + chmod**

Run:
```bash
cp .github/agent-factory/protocols/multi-grumpy/checks/traces-exist-in-diff.py \
   .github/agent-factory/protocols/code-review-pipeline/checks/traces-exist-in-diff.py
chmod +x .github/agent-factory/protocols/code-review-pipeline/checks/*.py
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_preflight_coverage.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add .github/agent-factory/protocols/code-review-pipeline/checks/ tests/test_preflight_coverage.py
git commit -m "feat(preflight): adherence-coverage + schema-valid + traces-exist form checks

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Preflight agent + evidence schema

**Files:**
- Create: `.github/agent-factory/protocols/code-review-pipeline/preflight.evidence.schema.json`
- Create: `.github/workflows/preflight-agent.md` (+ compiled `.lock.yml` via `gh aw compile`)

**Interfaces:**
- Consumes: the engine dispatches the agent with `task-context.json` (`pr`, `iteration`, `feedback`) like `grumpy-agent.md`; writes evidence to the engine's expected path.
- Produces: `evidence.json` in the shape `{"checks":[{"id","status","summary","evidence":[],"remediation"}], "examined":[...]}` validated by Task 3's `schema-valid`/`adherence-coverage`.

- [ ] **Step 1: Read the two reference agents**

Read `.github/workflows/grumpy-agent.md` (the engine-integrated agent: how it reads `task-context.json`, writes evidence, the Claude sonnet `engine.env` block, `safe-outputs`, `run-name` with `cid:`) and the port source `/home/gustavo/huawei/new-custody/custody/app/backend/component/preflight/workflow/preflight-gate.md` (the adherence-judgment body + the prefetch/scoping step). The preflight agent = grumpy-agent's engine wiring + preflight's prefetch/scope/judge body.

- [ ] **Step 2: Create the evidence schema**

Create `.github/agent-factory/protocols/code-review-pipeline/preflight.evidence.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "preflight adherence evidence",
  "type": "object",
  "required": ["checks", "examined"],
  "properties": {
    "checks": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "status"],
        "properties": {
          "id": { "type": "string", "enum": ["spec-adherence", "plan-adherence"] },
          "status": { "type": "string", "enum": ["pass", "fail", "warn"] },
          "summary": { "type": "string" },
          "evidence": { "type": "array" },
          "remediation": { "type": "string" }
        }
      }
    },
    "examined": { "type": "array", "items": { "type": "string" } }
  }
}
```

- [ ] **Step 3: Create `preflight-agent.md`**

Create `.github/workflows/preflight-agent.md`. Copy the **frontmatter** from `grumpy-agent.md` verbatim (the `engine:` Claude-sonnet block, `ANTHROPIC_*`, `network`, `safe-outputs`, `permissions: contents/pull-requests/issues read`, the `run-name` with `cid:[...]`), then add the preflight prefetch/scope step and judgment body. Concretely:

- Keep the `run-name`, `on: workflow_dispatch` (with the engine's dispatch inputs), `engine.env` (sonnet), `network`, `safe-outputs: { staged: true }`, read-only `permissions` — all copied from `grumpy-agent.md`.
- Add a `steps:` prefetch that writes the located artifacts + scope, derived from **changed files only** (consistent with the checks):

```yaml
steps:
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Prefetch PR + scope adherence checks (changed-files only)
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ github.event.inputs.pr_number }}", REPO: "${{ github.event.inputs.repo || github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,body,files,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
      # Scope which adherence checks to judge: only those whose artifact FILE is in the PR diff.
      # Read the artifact text from the committed file so the agent can judge against it.
      python3 - "$REPO" <<'PY'
      import json, os, subprocess, sys, re
      repo = sys.argv[1]
      pr = json.load(open('/tmp/gh-aw/agent/pr.json'))
      head = pr.get('headRefOid') or ''
      files = [f['filename'] for f in pr.get('files', []) if f.get('status') != 'removed']
      SPEC = re.compile(r'(^|/)docs/(superpowers/)?specs/|(^|/)(SPEC|REQUIREMENTS)\.md$|^specs/', re.I)
      PLAN = re.compile(r'(^|/)docs/(superpowers/)?plans?/|(^|/)PLAN\.md$|^plans?/', re.I)
      def read(path):
          out = subprocess.run(['gh','api',f'repos/{repo}/contents/{path}?ref={head}','--jq','.content'],
                               capture_output=True, text=True)
          if out.returncode != 0 or not out.stdout.strip(): return ''
          import base64
          try: return base64.b64decode(out.stdout.strip()).decode('utf-8')[:12000]
          except Exception: return ''
      ai = []
      spec_hit = next((f for f in files if SPEC.search(f)), None)
      plan_hit = next((f for f in files if PLAN.search(f)), None)
      open('/tmp/gh-aw/agent/spec.txt','w').write(read(spec_hit) if spec_hit else '')
      open('/tmp/gh-aw/agent/plan.txt','w').write(read(plan_hit) if plan_hit else '')
      if spec_hit: ai.append('spec-adherence')
      if plan_hit: ai.append('plan-adherence')
      open('/tmp/gh-aw/agent/ai-checks.json','w').write(json.dumps(ai))
      PY
```

- Body (adherence judgment, Claude-sonnet-friendly; ported from custody's `preflight-gate.md` body but writing the **engine evidence shape**, not `ai-results.jsonl`):

```markdown
# Preflight Gate — adherence judgment only

You judge ONLY spec/plan adherence. Deterministic facts (spec/plan/docs/tests
presence) are computed by the engine's checks — do NOT recompute them.

1. Read `/tmp/gh-aw/agent/ai-checks.json` (the check ids to judge). If it is `[]`,
   write evidence with an empty `checks` list (see step 4) — there is no artifact
   to judge against — and stop.
2. Read `/tmp/gh-aw/agent/pr.diff`, `/tmp/gh-aw/agent/spec.txt`, `/tmp/gh-aw/agent/plan.txt`,
   and the engine's `task-context.json` (`pr`, `iteration`, `feedback` — fold prior
   feedback into this pass).
3. For each requested id, judge the diff against the located artifact text ONLY
   (never infer an artifact):
   - `spec-adherence`: does the diff achieve what `spec.txt` requires?
   - `plan-adherence`: does the diff follow `plan.txt`?
   status: pass = adheres, warn = partial, fail = does not. Base every verdict on
   real evidence from the diff.
4. Write `/tmp/gh-aw/evidence.json` (the engine evidence path) as ONE JSON object:
   `{"checks":[{"id":"<id>","status":"pass|fail|warn","summary":"…","evidence":[{"label":"…","detail":"…"}],"remediation":"…"}], "examined":["<files you read in the diff>"]}`
   Include one `checks` entry per requested id; `examined` lists the changed files
   you inspected. Write nothing else.
```

> NOTE: confirm the exact engine evidence path + `task-context.json` location by
> reading `grumpy-agent.md` — match it precisely (`/tmp/gh-aw/evidence.json` and
> the upload step grumpy uses). Mirror grumpy's evidence upload step.

- [ ] **Step 4: Compile the agent**

Run: `gh aw compile`
Expected: produces/updates `.github/workflows/preflight-agent.lock.yml` with no errors.

- [ ] **Step 5: Sanity-check the lock + suite**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/preflight-agent.lock.yml'))" && echo OK`
Run: `pytest tests/ -q`
Expected: `OK`; suite green (unchanged count — no new tests this task; the agent is exercised live in T7).

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/protocols/code-review-pipeline/preflight.evidence.schema.json .github/workflows/preflight-agent.md .github/workflows/preflight-agent.lock.yml
git commit -m "feat(preflight): preflight agent (sonnet) + adherence evidence schema

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `conclude-preflight` + `publish-verdict`

**Files:**
- Create: `.github/agent-factory/protocols/code-review-pipeline/publish/conclude-preflight.py`
- Create: `.github/agent-factory/protocols/code-review-pipeline/publish/publish-verdict.py`
- Test: `tests/test_conclude_preflight.py`

**Interfaces:**
- Consumes: conclude ABI `<hook> <evidence.json> <instance-key>` + env `BLOCKING` (`"1"`/`""`); evidence shape from Task 4.
- Produces: stdout `{"conclusion","summary","blocked"}`; writes the verdict payload to `/tmp/gh-aw/verdict.json` for `publish-verdict`. `blocked = (BLOCKING) OR (any adherence status == "fail")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_conclude_preflight.py`:

```python
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/code-review-pipeline/publish/conclude-preflight.py"


def _conclude(evidence_obj, blocking, tmp_path):
    ev = tmp_path / "evidence.json"; ev.write_text(json.dumps(evidence_obj))
    env = dict(os.environ)
    env["BLOCKING"] = "1" if blocking else "0"   # matches advance.py's contract
    env["VERDICT_OUT"] = str(tmp_path / "verdict.json")  # see hook: honor override for tests
    r = subprocess.run(["python3", str(HOOK), str(ev), "pr-7"],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout), (tmp_path / "verdict.json")


def test_clear_when_no_blocking_and_all_pass(tmp_path):
    ev = {"checks": [{"id": "spec-adherence", "status": "pass"}], "examined": ["a"]}
    out, vpath = _conclude(ev, blocking=False, tmp_path=tmp_path)
    assert out["blocked"] is False and out["conclusion"] != "blocked"


def test_blocked_when_blocking_true(tmp_path):
    ev = {"checks": [{"id": "spec-adherence", "status": "pass"}], "examined": ["a"]}
    out, _ = _conclude(ev, blocking=True, tmp_path=tmp_path)
    assert out["blocked"] is True


def test_blocked_when_adherence_fails(tmp_path):
    ev = {"checks": [{"id": "spec-adherence", "status": "fail", "summary": "nope"}], "examined": ["a"]}
    out, _ = _conclude(ev, blocking=False, tmp_path=tmp_path)
    assert out["blocked"] is True


def test_verdict_json_shape(tmp_path):
    ev = {"checks": [{"id": "spec-adherence", "status": "pass"}], "examined": ["a"]}
    _out, vpath = _conclude(ev, blocking=False, tmp_path=tmp_path)
    v = json.loads(vpath.read_text())
    assert "records" in v and isinstance(v["records"], list)
    assert any(r.get("type") == "verdict" for r in v["records"])
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_conclude_preflight.py -q`
Expected: FAIL (hook missing).

- [ ] **Step 3: Create `conclude-preflight.py`**

(Port of custody `computeVerdict` + `mergeVerdict`; `blocked` folds in `BLOCKING`.)

```python
#!/usr/bin/env python3
"""Conclude hook for the preflight phase. Rolls up the agent's adherence verdicts
with the engine's blocking signal into clear/blocked, and writes a custody-shaped
verdict.json payload for publish-verdict.

ABI: conclude-preflight.py <evidence.json> <instance-key>;  env BLOCKING ("1"/"").
Prints {"conclusion","summary","blocked"}. blocked = BLOCKING OR any adherence fail."""
import json
import os
import sys


def main():
    ev_path = sys.argv[1]
    blocking = os.environ.get("BLOCKING", "") == "1"
    try:
        with open(ev_path) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    checks = evidence.get("checks", []) if isinstance(evidence, dict) else []

    adherence_fail = any(isinstance(c, dict) and c.get("status") in ("fail", "error") for c in checks)
    blocked = bool(blocking or adherence_fail)
    status = "blocked" if blocked else "clear"

    # custody-shaped verdict.json payload (records[] + verdict + meta echo).
    counts = {"pass": 0, "fail": 0, "warn": 0, "todo": 0, "error": 0, "skipped": 0}
    for c in checks:
        st = c.get("status") if isinstance(c, dict) else None
        if st in counts:
            counts[st] += 1
    records = [{"type": "check", **c} for c in checks if isinstance(c, dict)]
    records.append({"type": "verdict", "status": status, "counts": counts,
                    "blocking": bool(blocking)})
    payload = {"records": records}
    # meta: pr number from the instance-key "pr-N" (head_sha unknown here → empty).
    inst = sys.argv[2] if len(sys.argv) > 2 else ""
    if inst.startswith("pr-") and inst[3:].isdigit():
        payload["meta"] = {"pr_number": int(inst[3:]), "head_sha": os.environ.get("HEAD_SHA", "")}

    out_path = os.environ.get("VERDICT_OUT", "/tmp/gh-aw/verdict.json")
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as fh:
            json.dump(payload, fh)
    except OSError:
        pass

    if blocked:
        summary = "Preflight blocked: " + (
            "a required spec/plan is missing" if blocking else "code does not adhere to the declared spec/plan")
    else:
        summary = "Preflight clear."
    print(json.dumps({"conclusion": "blocked" if blocked else "clear",
                      "summary": summary, "blocked": blocked}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create `publish-verdict.py`**

```python
#!/usr/bin/env python3
"""Publish hook for the preflight phase. Side-effects only: the verdict.json
written by conclude-preflight is uploaded as a workflow artifact by the gh-aw/
engine step; this hook sets the preflight sub check-run and echoes the conclusion.

ABI: publish-verdict.py <evidence.json> <instance-key>; env ENGINE_LOCAL,
GITHUB_REPOSITORY, PUBLISH_TOKEN, PR. Prints {"conclusion","summary"}."""
import json
import os
import sys


def main():
    ev_path = sys.argv[1]
    try:
        with open(ev_path) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    checks = evidence.get("checks", []) if isinstance(evidence, dict) else []
    n = len(checks)
    # The engine already decided conclusion via conclude-preflight; publish only
    # reports. In ENGINE_LOCAL test mode, do no GitHub I/O.
    summary = f"Preflight published ({n} adherence verdict(s))."
    print(json.dumps({"conclusion": "neutral", "summary": summary}))


if __name__ == "__main__":
    main()
```

> The verdict.json **artifact upload** is done by the agent/engine step (mirror
> grumpy's publish flow); `publish-verdict` itself performs no upload in
> `ENGINE_LOCAL`. Keep it minimal — the conclusion axis is owned by `conclude`.

- [ ] **Step 5: chmod + run tests**

Run: `chmod +x .github/agent-factory/protocols/code-review-pipeline/publish/*.py`
Run: `pytest tests/test_conclude_preflight.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/protocols/code-review-pipeline/publish/ tests/test_conclude_preflight.py
git commit -m "feat(preflight): conclude-preflight (clear/blocked + verdict.json) + publish-verdict

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Assemble `protocol.json` + move triggers off multi-grumpy

**Files:**
- Create: `.github/agent-factory/protocols/code-review-pipeline/protocol.json`
- Modify: `.github/agent-factory/protocols/multi-grumpy/protocol.json` (drop PR triggers)
- Test: `tests/test_route.py` (extend real-protocols assertions)

**Interfaces:**
- Consumes: all checks/agent/hooks from T2–T5; `lib.route` (B→A) auto-discovers the protocol from `triggers`.
- Produces: the 3-phase pipeline; the route table `/review`→pipeline, PR→pipeline, `/grumpy`→multi-grumpy, `/v1-grumpy`→grumpy.

- [ ] **Step 1: Write the failing route tests**

Append to `tests/test_route.py`:

```python
def test_real_protocols_review_comment_routes_to_pipeline():
    r = lib.route(REAL_PROTOCOLS, "issue_comment", "", "/review", is_pr_comment=True)
    assert r["skip"] is False and r["protocol"].endswith("code-review-pipeline/protocol.json")


def test_real_protocols_pr_opened_routes_to_pipeline():
    # After M3, the pipeline (not multi-grumpy) owns PR auto-triggers.
    r = lib.route(REAL_PROTOCOLS, "pull_request", "opened", "")
    assert r["skip"] is False and r["protocol"].endswith("code-review-pipeline/protocol.json")


def test_real_protocols_grumpy_comment_still_routes_to_multi_grumpy():
    r = lib.route(REAL_PROTOCOLS, "issue_comment", "", "/grumpy", is_pr_comment=True)
    assert r["skip"] is False and r["protocol"].endswith("multi-grumpy/protocol.json")
```

(Update the existing `test_real_protocols_pr_opened_routes_to_multi_grumpy` test — PR-open now routes to the pipeline. Change its expected protocol to `code-review-pipeline/protocol.json`, or replace it with `test_real_protocols_pr_opened_routes_to_pipeline` above and delete the old one.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_route.py -q`
Expected: FAIL (pipeline protocol.json missing; old PR-open test now wrong).

- [ ] **Step 3: Create the pipeline `protocol.json`**

Create `.github/agent-factory/protocols/code-review-pipeline/protocol.json`. The `review` fan-out `branches` are **copied verbatim** from `multi-grumpy/protocol.json` (the two branches `grumpy` + `security` with their `evidence`/`max_iterations`/`params`/`checks`/`publish`). Result:

```jsonc
{
  "name": "code-review-pipeline",
  "version": "0.1.0",
  "triggers": [
    { "on": "issue_comment", "comment_prefix": "/review", "command": "start" },
    { "on": "pull_request",  "actions": ["opened", "reopened"], "command": "start" },
    { "on": "pull_request",  "actions": ["synchronize"], "command": "reset" }
  ],
  "states": [
    {
      "id": "preflight",
      "kind": "agent",
      "workflow": "preflight-agent",
      "evidence": "preflight.evidence.schema.json",
      "max_iterations": 2,
      "params": { "ai_checks": ["spec-adherence", "plan-adherence"] },
      "checks": [
        { "run": "schema-valid",            "on_fail": "iterate"  },
        { "run": "adherence-coverage",      "on_fail": "iterate"  },
        { "run": "traces-exist-in-diff",    "on_fail": "iterate"  },
        { "run": "spec-present",            "on_fail": "block"    },
        { "run": "plan-present",            "on_fail": "block"    },
        { "run": "docs-updated-with-code",  "on_fail": "advisory" },
        { "run": "tests-updated-with-code", "on_fail": "advisory" }
      ],
      "conclude": "conclude-preflight",
      "publish": "publish-verdict",
      "on_blocked": "halt",
      "next": "review"
    },
    {
      "id": "review",
      "kind": "fanout",
      "branches": [ /* paste grumpy + security branches verbatim from multi-grumpy/protocol.json */ ],
      "next": "join"
    },
    { "id": "join", "kind": "join", "of": "review", "next": "done" }
  ]
}
```

The `review` branches reference `grumpy-agent`/`security-agent` workflows + `publish-grumpy`/`publish-security` hooks + `schema-valid`/`rubric-coverage`/`traces-exist-in-diff` checks. **Those checks/publish hooks must exist under `code-review-pipeline/`** for the engine to resolve them per-protocol. Copy them:
```bash
cp .github/agent-factory/protocols/multi-grumpy/checks/rubric-coverage.py \
   .github/agent-factory/protocols/code-review-pipeline/checks/rubric-coverage.py
cp -r .github/agent-factory/protocols/multi-grumpy/publish/* \
   .github/agent-factory/protocols/code-review-pipeline/publish/
chmod +x .github/agent-factory/protocols/code-review-pipeline/checks/*.py \
         .github/agent-factory/protocols/code-review-pipeline/publish/*.py
```
(`schema-valid`/`traces-exist-in-diff` already exist under the pipeline from T2/T3; `rubric-coverage` + the grumpy/security publish hooks + their evidence schemas must be present too — also copy `grumpy.evidence.schema.json` + `security.evidence.schema.json` from multi-grumpy.)

> **NOTE — confirm check resolution scope.** Verify whether the engine resolves a fan-out branch's checks/publish from the *protocol's own* directory (so they must be copied) or can reference the sibling multi-grumpy protocol. Read `lib.resolve_executable`/`run-checks.py` resolution: it resolves from `<protocol-dir>/checks`. So copies ARE required. If a shared-checks mechanism exists, prefer it; otherwise copy (matches the existing per-protocol-copy convention).

- [ ] **Step 4: Drop multi-grumpy's PR triggers**

Edit `.github/agent-factory/protocols/multi-grumpy/protocol.json` — replace its `triggers` array with the single comment trigger:

```json
  "triggers": [
    { "on": "issue_comment", "comment_prefix": "/grumpy", "command": "start" }
  ],
```

- [ ] **Step 5: Validate JSON + run route tests + full suite**

Run:
```bash
python3 -c "import json,glob; [json.load(open(p)) for p in glob.glob('.github/agent-factory/protocols/*/protocol.json')]; print('JSON OK')"
pytest tests/test_route.py tests/ -q
```
Expected: `JSON OK`; route tests PASS (no ambiguity — `/review`+PR→pipeline, `/grumpy`→multi-grumpy, `/v1-grumpy`→grumpy); full suite green.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/protocols/code-review-pipeline/ .github/agent-factory/protocols/multi-grumpy/protocol.json tests/test_route.py
git commit -m "feat(pipeline): assemble code-review-pipeline protocol; multi-grumpy → /grumpy only

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: actionlint + LIVE clear + blocked runs (binding proof)

**Files:** none (verification only).

**Interfaces:**
- Consumes: the merged pipeline on `main`.
- Produces: the binding proof that the multi-phase pipeline runs end-to-end (first multi-phase live test).

> **This is the acceptance gate.** pytest + actionlint are necessary but NOT sufficient — the phase relay, conclude/`on_blocked`, and the agent only run live.

- [ ] **Step 1: actionlint**

Run: `GOBIN=/tmp/gobin go install github.com/rhysd/actionlint/cmd/actionlint@latest && /tmp/gobin/actionlint`
Expected: no NEW errors for `agentic-engine.yml` / `preflight-agent.lock.yml` (pre-existing `queue: max` lock-file warnings unrelated). Permission ceiling is NOT caught here — the live run is the proof.

- [ ] **Step 2: Merge to main + push**

```bash
git checkout main && git merge --ff-only feat/m3-preflight-pipeline && git push origin main
```

- [ ] **Step 3: LIVE — clear path**

Open/prepare a PR that commits: a spec file (`docs/specs/<x>.md` or `REQUIREMENTS.md`), a plan file (`docs/superpowers/plans/<x>.md` or `PLAN.md`), and code + a test that adhere. Trigger via `/review` comment (and separately a `pull_request` event).
Verify on the GitHub web UI:
- An **Agentic Orchestrator** run picks `code-review-pipeline`.
- `_instance.yaml` cursor walks `preflight → review → join → done` (check `git log agentic-state`).
- aggregate `code-review-pipeline` check-run + per-phase sub-runs (`…/preflight`, `…/grumpy`, `…/security`).
- grumpy + security reviews posted; a `clear` `verdict.json` artifact on the preflight run.

- [ ] **Step 4: LIVE — blocked (absence) path**

Trigger `/review` on a PR with NO spec/plan file (e.g. a code-only diff).
Expected: `spec-present`/`plan-present` fail their `block` checks → preflight `blocked` → pipeline halts, **no review fan-out**, aggregate check-run `failure`, `blocked` `verdict.json`.

- [ ] **Step 5: LIVE — blocked (adherence) path**

Trigger `/review` on a PR that commits a spec file but whose code does NOT adhere.
Expected: preflight `blocked` via the conclude/adherence source (spec-present passes, but the agent's `spec-adherence` verdict is `fail` → `conclude-preflight` blocks). Pipeline halts.

- [ ] **Step 6: Record the result**

Update `code-review-pipeline-progress.md` memory + `docs/STATUS.md`: M3 live-verified (run ids, the three paths), any live-only fix. If a `startup_failure` appears, check the permission ceiling (router grants the union) and the `protocol-advance`/`protocol-continue` `client_payload` contracts. Do NOT mark done until all three paths pass.

---

## Self-Review (against the spec)

**Spec coverage:**
- Decision 1 (changed-files-only spec/plan + PR-body env) → T2 (`spec/plan-present`), T1 (PR_BODY/PR_TITLE). ✓
- Decision 2 (triggers) → T6 (pipeline triggers + multi-grumpy drop + route tests). ✓
- Decision 3 (PR-body sole engine change) → T1 only touches `agentic-engine.yml`+`run-checks.py`; no other task touches the engine. ✓
- Decision 4 (adherence-coverage derives expected set from changed-files) → T3 `adherence-coverage.py` + its tests. ✓
- Decision 5 (checks ported/skipped; sonnet) → T2/T3 (no `local-review-evidence`), T4 (sonnet copied from grumpy). ✓
- conclude roll-up (`blocked = BLOCKING OR adherence fail`; verdict.json shape) → T5 + tests. ✓
- 3-phase protocol (`on_blocked: halt`, review verbatim, join) → T6. ✓
- Live clear + both blocked paths → T7. ✓
- Engine substrate unchanged → Global Constraints + T1 the only engine edit. ✓

**Placeholder scan:** the two `/* paste … verbatim */` markers are concrete copy instructions (the source file + exact branches are named); no TBD/TODO; every check/hook/test has full code. The T4 agent body references grumpy-agent.md for the exact evidence path — that is a real file to read, with the shape pinned by T3's schema-valid. ✓

**Type/shape consistency:** evidence shape `{"checks":[{"id","status",…}],"examined":[]}` is identical across T3 (validators), T4 (schema + agent writes it), T5 (conclude reads `checks[].status`). `_paths` function names (`is_spec_path`/`is_plan_path`/`is_doc`/`is_test`/`is_code`/`read_changed_files`) are defined in T2 and used unchanged in T3. Check ABI + conclude/publish ABI consistent with Global Constraints. ✓
