# `impl-feature-auto` Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new engine protocol `impl-feature-auto` that turns a GitHub issue into a reviewable PR autonomously (no human mid-run), via a two-node `design → implement` pipeline where `design` is rigorously checked (Accountability Ledger + spec/plan presence) and `implement` executes the plan and opens the PR.

**Architecture:** Reuses the existing recursive engine unchanged except for an **additive issue-keying** change (a new `target` trigger field + `issue-<N>` instance derivation). The protocol is pure data + deterministic checks + two gh-aw agents that run the **superpowers** skill library staged into the runtime. The design agent emits the ledger as structured `evidence.json` so checks inspect a precise object; spec/plan files travel between the two agent runs as **artifacts** (the same run-id + `gh run download` pattern `recover-mental-model` already uses). The deliverable PR write is owned by gh-aw `safe-outputs`; a zone-4 publish hook only summarises/links it.

**Tech Stack:** Python 3 + PyYAML (engine runtime), pytest (dev-only tests), gh-aw (`*.md` → compiled `*.lock.yml`), GitHub Actions YAML, JSON Schema (draft-07).

## Global Constraints

- **Runtime deps:** the vendored `.github/agent-factory/` unit needs only **Python 3 + PyYAML** — no third-party imports in checks/hooks. pytest is dev-only.
- **Check ABI:** every check is invoked `<check> <evidence.json> <diff.txt> <changed-files.txt>`, prints exactly one JSON object `{"check","pass","feedback"}` to stdout, and **always exits 0** (non-zero is reserved for a genuine runner error).
- **Publish-hook ABI:** invoked `<hook> <evidence.json> <instance-key>` with env `ENGINE_LOCAL`, `GITHUB_REPOSITORY`, `PUBLISH_TOKEN`, `PR`; prints `{"conclusion","summary"}`. Runs trusted in zone 4. In `ENGINE_LOCAL=1` mode it must do **no GitHub I/O**.
- **Security rule:** agent-derived strings (`feedback`, `verdicts`, `pr_branch`, comment bodies, filenames) are passed to shell steps via `env:`, **never** interpolated into `run:` blocks.
- **Engine is generic:** do **not** add protocol-specific logic to `.github/agent-factory/engine/`. The only engine edits in this plan are the additive issue-keying ones in Tasks 1–3 (already flagged to and approved by the user; the DSL change is the new optional `target` trigger field, default `"pr"`, backward-compatible).
- **superpowers staging:** pin to the **release tag `v6.0.3`** via the GitHub release tarball; copy the **whole `skills/` subtree** into `.claude/skills` (companion files are referenced by relative path — `SKILL.md` alone is insufficient). Engine-swappable via a single `DEST` var.
- **gh-aw locks:** after editing any `*-agent.md`, run `gh aw compile` and commit the regenerated `*.lock.yml`. Workflows run from the lock.
- **No state-PAT in agents:** agents are read-only; they never hold the state PAT. The PR write is gh-aw `safe-outputs` (implement only); the issue summary is the engine's zone-4 publish hook.
- **Run the suite** with `uv run pytest tests/ -q` (auto-syncs dev deps from `uv.lock`).

---

## File Structure

**New protocol (all protocol-specific logic):**
```
.github/agent-factory/protocols/impl-feature-auto/
  protocol.json                      # the two-node sequence + triggers (Task 4)
  design.evidence.schema.json        # design contract (Task 4)
  implement.evidence.schema.json     # implement contract (Task 4)
  checks/
    _common.py                       # shared helpers (NON_TRIVIAL, load_evidence, sibling_file) (Task 5)
    ledger-wellformed.py             # ledger layer 1: completeness + enums (Task 5)
    ledger-consistent.py             # ledger layer 2: rule-based contradictions (Task 6)
    read-these-first-consistent.py   # ledger layer 3: risk-sorted triage + spec cross-ref (Task 7)
    spec-present.py                  # spec doc exists with the 5 required sections (Task 8)
    plan-present.py                  # plan doc was produced (Task 8)
    implement-schema-valid.py        # implement evidence has summary + valid pr_branch (Task 9)
  publish/
    post-summary.py                  # zone-4: summarise+link the PR, comment on the issue (Task 10)
```

**New agent workflows:**
```
.github/workflows/
  impl-feature-auto-design-agent.md       (+ .lock.yml via `gh aw compile`)   (Task 11)
  impl-feature-auto-implement-agent.md     (+ .lock.yml)                        (Task 12)
```

**Engine/infra edits (additive issue-keying — Tasks 1–3):**
```
.github/agent-factory/engine/protocol.schema.json   # add optional trigger.target (Task 1)
.github/agent-factory/engine/lib.py                  # match_trigger/route target filter (Task 1); pr_from_instance helper (Task 2)
.github/agent-factory/engine/next.py                 # use lib.pr_from_instance (Task 2)
.github/workflows/agentic-orchestrator.yml           # route-job guard + instance run-name/concurrency (Task 3)
.github/workflows/agentic-engine.yml                 # ctx issue-keying + head-SHA for the issue case (Task 3)
```

**New tests:**
```
tests/test_impl_feature_auto_checks.py    # Tasks 5–9 (unit, over crafted evidence)
tests/test_impl_feature_auto_publish.py   # Task 10
tests/test_impl_feature_auto_e2e.py       # Task 13 (offline NODE_PATH walk on the real protocol)
tests/test_route.py                       # extended in Task 1
tests/test_protocol_schema.py             # extended in Task 1
tests/test_workflow_contract.py           # extended in Task 3
tests/fixtures/impl-feature-auto/         # crafted spec.md/plan.md + evidence samples (Tasks 7,8,13)
```

---

## Task 1: `target` trigger field — schema + router filtering

Add an optional `target: "pr" | "issue"` field to triggers (default `"pr"`). `match_trigger`/`route` use it to decide whether an `issue_comment` on a **plain issue** (vs a PR) may match. This is the approved DSL change; it is backward-compatible because every existing trigger omits `target` and therefore defaults to `"pr"`.

**Files:**
- Modify: `.github/agent-factory/engine/protocol.schema.json` (trigger definition, ~lines 50–74)
- Modify: `.github/agent-factory/engine/lib.py` (`match_trigger` ~lines 297–316; `route` ~lines 365–410)
- Test: `tests/test_route.py` (extend), `tests/test_protocol_schema.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `lib.match_trigger(protocol, event_name, action="", comment_body="", is_pr_comment=True) -> str` — **new keyword arg `is_pr_comment`** (default `True` keeps every existing caller's behavior). A trigger with `on=="issue_comment"` matches only when its effective `target` (`trigger.get("target","pr")`) equals `"pr"` if `is_pr_comment` else `"issue"`.
  - `lib.route(protocols_dir, event_name, action="", comment_body="", dispatch_protocol="", is_pr_comment=True)` — when `event_name=="issue_comment" and not is_pr_comment`, it **no longer unconditionally skips**: it scans protocols and matches only `target:"issue"` triggers (via `match_trigger(..., is_pr_comment=False)`).

- [ ] **Step 1: Write the failing schema test**

Add to `tests/test_protocol_schema.py`:

```python
def test_trigger_target_field_allowed():
    """The trigger schema must accept an optional `target` of pr|issue."""
    import json, pathlib, jsonschema
    root = pathlib.Path(__file__).resolve().parent.parent
    schema = json.load(open(root / ".github/agent-factory/engine/protocol.schema.json"))
    proto = {
        "name": "t",
        "triggers": [
            {"on": "issue_comment", "comment_prefix": "/x", "command": "start", "target": "issue"}
        ],
        "states": [{"id": "a", "kind": "agent", "workflow": "w"}],
    }
    jsonschema.validate(proto, schema)  # must not raise

def test_trigger_target_rejects_unknown_value():
    import json, pathlib, jsonschema, pytest
    root = pathlib.Path(__file__).resolve().parent.parent
    schema = json.load(open(root / ".github/agent-factory/engine/protocol.schema.json"))
    proto = {"name": "t",
             "triggers": [{"on": "issue_comment", "command": "start", "target": "wat"}],
             "states": [{"id": "a", "kind": "agent", "workflow": "w"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(proto, schema)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_protocol_schema.py::test_trigger_target_field_allowed tests/test_protocol_schema.py::test_trigger_target_rejects_unknown_value -v`
Expected: FAIL — `additionalProperties` rejects `target` (allowed test fails) / unknown-value test may pass-by-accident; both must end green only after Step 3.

- [ ] **Step 3: Add `target` to the trigger schema**

In `.github/agent-factory/engine/protocol.schema.json`, inside `definitions.trigger.properties` (after `command`), add:

```json
        "target": {
          "type": "string",
          "description": "For issue_comment: whether this trigger fires on a comment on a PR (default) or on a plain issue. Plain-issue protocols (e.g. impl-feature-auto, keyed issue-<N>) set \"issue\".",
          "enum": ["pr", "issue"]
        }
```

- [ ] **Step 4: Run the schema tests to verify they pass**

Run: `uv run pytest tests/test_protocol_schema.py -k target -v`
Expected: PASS (both).

- [ ] **Step 5: Write the failing router tests**

Add to `tests/test_route.py`:

```python
ISSUE_TRIGGERS = [
    {"on": "issue_comment", "comment_prefix": "/impl-feature-auto",
     "command": "start", "target": "issue"},
]

def test_issue_targeted_trigger_routes_on_plain_issue_comment():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"iface": ISSUE_TRIGGERS})
        r = lib.route(pdir, "issue_comment", "", "/impl-feature-auto go",
                      is_pr_comment=False)
        assert r["skip"] is False
        assert r["command"] == "start"
        assert r["protocol"].endswith("iface/protocol.json")

def test_pr_targeted_trigger_does_not_route_on_plain_issue_comment():
    # A default (pr) trigger must NOT fire on a plain-issue comment.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"fanout-demo": DEMO_TRIGGERS})
        r = lib.route(pdir, "issue_comment", "", "/grumpy", is_pr_comment=False)
        assert r["skip"] is True

def test_issue_targeted_trigger_does_not_route_on_pr_comment():
    # And the issue-targeted trigger must NOT fire on a PR comment.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pdir = _mk_protocols(Path(td), {"iface": ISSUE_TRIGGERS})
        r = lib.route(pdir, "issue_comment", "", "/impl-feature-auto go",
                      is_pr_comment=True)
        assert r["skip"] is True
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_route.py -k "issue_targeted or pr_targeted" -v`
Expected: FAIL — `route` currently skips every non-PR comment and ignores `target`.

- [ ] **Step 7: Thread `is_pr_comment` + `target` through `match_trigger` and `route`**

In `lib.py`, change `match_trigger`'s signature and the `issue_comment` branch:

```python
def match_trigger(protocol, event_name, action="", comment_body="", is_pr_comment=True):
    """Map an ENTRY GitHub event to an engine command via protocol["triggers"].
    For issue_comment, a trigger's `target` (default "pr") must match whether the
    comment is on a PR (is_pr_comment True) or a plain issue (False)."""
    for t in protocol.get("triggers", []):
        if t.get("on") != event_name:
            continue
        if event_name == "issue_comment":
            want = "pr" if is_pr_comment else "issue"
            if t.get("target", "pr") != want:
                continue
            prefix = t.get("comment_prefix", "")
            if not prefix or comment_body.startswith(prefix):
                return t.get("command", "")
        elif event_name == "pull_request":
            actions = t.get("actions", [])
            if not actions or action in actions:
                return t.get("command", "")
        else:
            return t.get("command", "")
    return ""
```

In `route`, **delete** the early `if event_name == "issue_comment" and not is_pr_comment: skip` block (lines ~383–384) and pass `is_pr_comment` into the scan:

```python
    matches = []
    for path in sorted(glob.glob(os.path.join(protocols_dir, "*", "protocol.json"))):
        with open(path) as f:
            proto = json.load(f)
        cmd = match_trigger(proto, event_name, action, comment_body,
                            is_pr_comment=is_pr_comment)
        if cmd:
            matches.append((path, cmd))
```

(The ambiguity / 0-match / 1-match logic below is unchanged.)

- [ ] **Step 8: Verify router + existing route tests still pass**

Run: `uv run pytest tests/test_route.py -v`
Expected: PASS — the new tests pass; pre-existing tests (which pass `is_pr_comment=True` or rely on the default) are unaffected. Note `test_non_pr_comment_skips_without_scanning` now skips because no protocol declares a `target:"issue"` trigger matching `/grumpy` (still green).

- [ ] **Step 9: Verify the CLI passthrough still works**

`lib.py route` CLI already forwards the 6th positional arg as `is_pr_comment` (string "true"/"false"). Confirm no CLI change is needed:

Run: `uv run pytest tests/test_route.py -k cli -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add .github/agent-factory/engine/protocol.schema.json .github/agent-factory/engine/lib.py tests/test_route.py tests/test_protocol_schema.py
git commit -m "feat(engine): add optional trigger.target for plain-issue routing"
```

---

## Task 2: `lib.pr_from_instance` helper + wire into `next.py`

The engine derives a `pr` value from the instance key with the inline idiom `INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE` (4 sites in `next.py`). For an `issue-<N>` instance this yields the non-numeric `"issue-<N>"`. Add a single helper that maps `pr-<N>` **and** `issue-<N>` → `<N>` (numeric), else returns the raw instance, and use it everywhere so a future issue-keyed gate/status resolves the right issue number.

**Files:**
- Modify: `.github/agent-factory/engine/lib.py` (add helper near `instance_file`, ~line 413)
- Modify: `.github/agent-factory/engine/next.py` (4 derivation sites: ~140, 234, 257, 324, 526, 745 — replace the inline idiom)
- Test: `tests/test_correlation.py` (pure helper test — append; this module already tests pure resolvers)

**Interfaces:**
- Produces: `lib.pr_from_instance(instance: str) -> str` — `"pr-5"→"5"`, `"issue-5"→"5"`, `"ref-foo"→"ref-foo"`, `"ui-x"→"ui-x"`.

- [ ] **Step 1: Write the failing helper test**

Append to `tests/test_correlation.py`:

```python
def test_pr_from_instance_handles_pr_issue_and_passthrough():
    import sys, pathlib
    eng = pathlib.Path(__file__).resolve().parent.parent / ".github/agent-factory/engine"
    sys.path.insert(0, str(eng))
    import lib
    assert lib.pr_from_instance("pr-5") == "5"
    assert lib.pr_from_instance("issue-42") == "42"
    assert lib.pr_from_instance("ref-feat-x") == "ref-feat-x"
    assert lib.pr_from_instance("ui-abc") == "ui-abc"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_correlation.py::test_pr_from_instance_handles_pr_issue_and_passthrough -v`
Expected: FAIL — `AttributeError: module 'lib' has no attribute 'pr_from_instance'`.

- [ ] **Step 3: Add the helper**

In `lib.py`, just above `def instance_file`:

```python
def pr_from_instance(instance):
    """Derive the PR/issue NUMBER from an instance key.
    pr-<N> and issue-<N> -> <N> (the GitHub thread number, numeric so the engine
    can comment/label on it). ref-*/ui-* and any other shape pass through verbatim
    (no numeric thread)."""
    for prefix in ("pr-", "issue-"):
        if instance.startswith(prefix):
            return instance[len(prefix):]
    return instance
```

- [ ] **Step 4: Replace the inline idiom in `next.py`**

At each site currently reading
`pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE`
replace with:
`pr = lib.pr_from_instance(INSTANCE)`

(Sites: the `gate` branch ~140, and the ~234/257/324/526/745 derivations. `grep -n 'startswith("pr-")' next.py` to find them all; replace every one.)

- [ ] **Step 5: Run the helper test + the full engine/next suites**

Run: `uv run pytest tests/test_correlation.py tests/test_engine.py tests/test_gate.py tests/test_gate_data.py tests/test_nested_gate_answer.py -q`
Expected: PASS — `pr-<N>` instances are byte-identical (`pr_from_instance("pr-1")=="1"`), so no behavior change for existing protocols.

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/engine/lib.py .github/agent-factory/engine/next.py tests/test_correlation.py
git commit -m "refactor(engine): pr_from_instance helper (handles pr- and issue- keys)"
```

---

## Task 3: Orchestrator + engine-workflow issue-keying

Wire the YAML so a `/impl-feature-auto` comment on a **plain issue** reaches the engine, derives instance `issue-<N>`, checks out the **default branch** (no PR head), and resolves the head SHA from it. All agent-derived strings stay in `env:`.

**Files:**
- Modify: `.github/workflows/agentic-orchestrator.yml` (route-job `if:` ~54–58; `run-name` line 7; `concurrency.group` line 47)
- Modify: `.github/workflows/agentic-engine.yml` (plan-job `if:` ~40; `ctx` step `issue_comment)` case ~111–113 + default-branch checkout; `head` step ~195–208 for the issue case)
- Test: `tests/test_workflow_contract.py` (extend — offline string assertions)

**Interfaces:**
- Consumes: `lib.route` (Task 1), `lib.pr_from_instance` (Task 2).
- Produces: for a plain-issue `/impl-feature-auto` comment, the engine sets `instance=issue-<N>`, `pr=<N>` (numeric — for commenting/labeling on issue #N), `checkout_ref=<default branch>`, `head_sha=<default-branch tip>`.

- [ ] **Step 1: Write the failing workflow-contract tests**

Append to `tests/test_workflow_contract.py`:

```python
def test_orchestrator_routes_plain_issue_comments():
    t = _load("agentic-orchestrator.yml")
    # The route job must accept ANY issue_comment (not only PR comments); lib.route
    # decides skip via the target field.
    assert "github.event.issue.pull_request != null" not in t.split("jobs:")[0] \
        or "issue_comment'" in t  # guard relaxed; see below assertion
    # instance derivation distinguishes pr- vs issue- keys for issue_comment events.
    assert "format('issue-{0}'" in t
    assert "format('pr-{0}'" in t

def test_engine_yml_derives_issue_instance_and_default_branch():
    t = _load("agentic-engine.yml")
    assert "issue-$" in t              # INSTANCE="issue-$N" path exists
    assert "default_branch" in t       # checkout the default branch for the issue case
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_workflow_contract.py -k "plain_issue or issue_instance" -v`
Expected: FAIL — strings absent.

- [ ] **Step 3: Relax the orchestrator route-job guard**

In `agentic-orchestrator.yml`, the `route` job `if:` (lines ~54–58) currently ends with
`(github.event_name == 'issue_comment' && github.event.issue.pull_request != null)`.
Change that final clause to allow any issue comment (route decides skip):

```yaml
    if: >
      github.event_name == 'repository_dispatch' ||
      github.event_name == 'workflow_dispatch' ||
      github.event_name == 'pull_request' ||
      github.event_name == 'issue_comment'
```

- [ ] **Step 4: Make `run-name` + `concurrency` distinguish issue from PR**

Replace the instance fragment in `run-name` (line 7) and `concurrency.group` (line 47). The fragment today is:
`github.event.inputs.instance || github.event.client_payload.instance || format('pr-{0}', github.event.issue.number || github.event.pull_request.number)`

Replace **both occurrences** with (a GHA `&&`/`||` ternary keyed on PR-ness of the issue):

```yaml
github.event.inputs.instance || github.event.client_payload.instance || (github.event.issue.pull_request != null && format('pr-{0}', github.event.issue.number) || format('issue-{0}', github.event.issue.number))
```

This also fixes a latent collision (PR #5 `/review` vs issue #5 `/impl-feature-auto` would otherwise share a concurrency group).

- [ ] **Step 5: Relax the engine plan-job guard**

In `agentic-engine.yml`, the plan job `if:` (~line 40) has the same
`github.event.issue.pull_request != null` clause. Change it the same way (allow any `issue_comment`; ctx derives the rest). If the guard is a single combined expression, mirror Step 3.

- [ ] **Step 6: Derive `issue-<N>` + default-branch checkout in the ctx step**

In the `ctx` step's `issue_comment)` case (~lines 111–114), today:

```bash
            issue_comment)
              PR="${{ github.event.issue.number }}"
              INSTANCE="pr-$PR"
              CMD=$(python3 .github/agent-factory/engine/lib.py match-trigger "$PROTO" issue_comment "" "$COMMENT_BODY")
```

Replace with (note `match-trigger` gains the `is_pr_comment` positional; the route job already guarantees only a `target`-matching protocol reaches here):

```bash
            issue_comment)
              N="${{ github.event.issue.number }}"
              IS_PR="${{ github.event.issue.pull_request != null }}"
              if [ "$IS_PR" = "true" ]; then
                PR="$N"; INSTANCE="pr-$N"
              else
                # Plain-issue protocol (impl-feature-auto): key issue-<N>, comment on
                # issue #N (PR=$N is numeric), analyze the DEFAULT branch (no PR head;
                # the feature branch is created later by the implement agent).
                PR="$N"; INSTANCE="issue-$N"
                CHECKOUT_REF="${{ github.event.repository.default_branch }}"
              fi
              CMD=$(python3 .github/agent-factory/engine/lib.py match-trigger "$PROTO" issue_comment "" "$COMMENT_BODY" "$IS_PR")
```

> The existing `match-trigger` CLI subcommand must accept the optional 5th positional `is_pr_comment`. Confirm/extend `lib.py`'s `match-trigger` CLI dispatch to forward it (it parses `sys.argv`); add it in this step if absent, defaulting to `"true"` when not given. Add a one-line `tests/test_triggers.py` assertion if you extend the CLI.

- [ ] **Step 7: Resolve the head SHA for the issue case**

In the `head` step (~lines 193–208), the logic branches PR-run vs ref-target vs continue/join. The issue case set `CHECKOUT_REF` to the default branch (Step 6) and no `PR`-as-pull-request. Ensure the SHA resolves from `CHECKOUT_REF` when there is no PR head. The existing ref-target branch already does `git ls-remote`/`gh api` on a ref; reuse it by treating "PR empty + CHECKOUT_REF set" the same as a ref-target start:

```bash
          if [ -n "$PR" ] && [ "$IS_PR_RUN" = "true" ]; then
            SHA=$(gh pr view "$PR" --repo "$REPO" --json headRefOid --jq .headRefOid)
          elif [ -n "$CHECKOUT_REF" ]; then
            SHA=$(gh api "repos/$REPO/commits/$CHECKOUT_REF" --jq .sha)
          else
            # continue/join: read the SHA pinned in instance state at start
            SHA=$(gh api "repos/$REPO/contents/$PID/$INSTANCE/_instance.yaml?ref=agentic-state" \
                   --jq '.content' | base64 -d | sed -n 's/^head_sha:[[:space:]]*//p' | head -1)
          fi
```

> `IS_PR_RUN` here means "the PR-head path", true only for `pr-<N>` PR runs — not the issue case. Derive it in ctx (e.g. `echo "is_pr_run=$IS_PR" >> $GITHUB_OUTPUT` for the PR-comment branch, empty otherwise) and read it as an env in the head step. Match the file's existing variable plumbing; the key behavior is: **issue case → SHA from the default-branch tip**, identical to a ref-target start.

- [ ] **Step 8: Run the contract tests + actionlint**

Run: `uv run pytest tests/test_workflow_contract.py -v`
Expected: PASS.
Run: `actionlint .github/workflows/agentic-orchestrator.yml .github/workflows/agentic-engine.yml`
Expected: no errors (the repo ships an `actionlint` binary at the root; `./actionlint ...`).

- [ ] **Step 9: Commit**

```bash
git add .github/workflows/agentic-orchestrator.yml .github/workflows/agentic-engine.yml tests/test_workflow_contract.py .github/agent-factory/engine/lib.py
git commit -m "feat(engine): issue-keying (issue-<N> instance, default-branch checkout) for plain-issue protocols"
```

---

## Task 4: Protocol data — `protocol.json` + evidence schemas

Create the two-node sequence and both evidence contracts. The schemas come verbatim from the spec Appendix A.

**Files:**
- Create: `.github/agent-factory/protocols/impl-feature-auto/protocol.json`
- Create: `.github/agent-factory/protocols/impl-feature-auto/design.evidence.schema.json`
- Create: `.github/agent-factory/protocols/impl-feature-auto/implement.evidence.schema.json`
- Test: `tests/test_protocol_lint.py` (extend)

**Interfaces:**
- Produces: protocol id `impl-feature-auto`; node ids `design`, `implement`; trigger `/impl-feature-auto` (`command: start`, `target: issue`); design checks `ledger-wellformed`, `ledger-consistent`, `read-these-first-consistent`, `spec-present`, `plan-present`; implement check `implement-schema-valid`; implement input `{from: design, as: design}`; implement publish `post-summary`.

- [ ] **Step 1: Write the failing lint test**

Append to `tests/test_protocol_lint.py`:

```python
def test_impl_feature_auto_protocol_lints_clean():
    import subprocess, pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    proto = root / ".github/agent-factory/protocols/impl-feature-auto/protocol.json"
    r = subprocess.run(
        ["python3", str(root / ".github/agent-factory/engine/protocol-lint.py"), str(proto)],
        text=True, capture_output=True)
    assert r.returncode == 0, f"lint failed:\n{r.stdout}\n{r.stderr}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_protocol_lint.py::test_impl_feature_auto_protocol_lints_clean -v`
Expected: FAIL — `protocol.json` does not exist yet.

- [ ] **Step 3: Write `protocol.json`**

`.github/agent-factory/protocols/impl-feature-auto/protocol.json`:

```json
{
  "$schema": "../../engine/protocol.schema.json",
  "name": "impl-feature-auto",
  "version": "0.1.0",
  "min_engine_version": "1.0.0",
  "triggers": [
    { "on": "issue_comment", "comment_prefix": "/impl-feature-auto", "command": "start", "target": "issue" }
  ],
  "states": [
    {
      "id": "design",
      "kind": "agent",
      "label": "design (spec + ledger + plan)",
      "workflow": "impl-feature-auto-design-agent",
      "evidence": "design.evidence.schema.json",
      "max_iterations": 3,
      "checks": [
        { "run": "ledger-wellformed",            "on_fail": "iterate" },
        { "run": "ledger-consistent",            "on_fail": "iterate" },
        { "run": "read-these-first-consistent",  "on_fail": "iterate" },
        { "run": "spec-present",                 "on_fail": "block" },
        { "run": "plan-present",                 "on_fail": "block" }
      ],
      "next": "implement"
    },
    {
      "id": "implement",
      "kind": "agent",
      "label": "implement (TDD + open PR)",
      "workflow": "impl-feature-auto-implement-agent",
      "evidence": "implement.evidence.schema.json",
      "max_iterations": 1,
      "inputs": [{ "from": "design", "as": "design" }],
      "checks": [
        { "run": "implement-schema-valid", "on_fail": "iterate" }
      ],
      "publish": "post-summary",
      "next": "done"
    }
  ]
}
```

> **Why `implement` has one check, not zero:** the engine's `decide([])` treats *zero verdicts* as a failed attempt (a checks-job-failure guard), so a literally check-less node never reaches `done`. `implement-schema-valid` (Task 9) supplies one passing verdict on good evidence and one `iterate`→`failed` (max_iterations 1) on missing `pr_branch`. The existing `/review` pipeline remains the substantive gate on the resulting PR.

- [ ] **Step 4: Write `design.evidence.schema.json`**

Copy verbatim from the spec Appendix A "design.evidence.schema.json" (the full draft-07 object with `spec_path`, `plan_path`, `summary`, `ledger[]` items carrying `id/category/what/why/what_i_did/confidence/blast_radius{level,why}/reversibility{level,why}/revisit_if/verified`, and `read_these_first[]`). Add one field the carrier needs:

```json
    "run_id": { "type": "string", "description": "design agent's GITHUB_RUN_ID; implement downloads the spec/plan artifacts by it" },
```
inside `properties` (not `required` — local/offline evidence omits it).

- [ ] **Step 5: Write `implement.evidence.schema.json`**

Copy verbatim from spec Appendix A "implement.evidence.schema.json" (`required: ["summary","pr_branch"]`, `additionalProperties:false`). Add an optional `run_id` string property too (harmless; mirrors design).

- [ ] **Step 6: Run the lint test**

Run: `uv run pytest tests/test_protocol_lint.py::test_impl_feature_auto_protocol_lints_clean -v`
Expected: PASS — `protocol-lint.py` resolves the structural schema (draft-07) + the engine's own semantic rules (agent nodes have `workflow`; checks resolve to files in `checks/` — these come in Tasks 5–9, so **if lint resolves check files**, run this step's verification again at the end of Task 9). If lint only checks structure/semantics that don't require the check files to exist yet, it passes now; otherwise mark this test xfail-until-Task-9 and re-run.

- [ ] **Step 7: Commit**

```bash
git add .github/agent-factory/protocols/impl-feature-auto/protocol.json .github/agent-factory/protocols/impl-feature-auto/*.evidence.schema.json tests/test_protocol_lint.py
git commit -m "feat(impl-feature-auto): protocol.json + evidence schemas"
```

---

## Task 5: `ledger-wellformed` check (layer 1) + shared helpers

Per-item completeness + valid enums + justified blast_radius/reversibility + ASSUMPTION ⇒ `verified: true`.

**Files:**
- Create: `.github/agent-factory/protocols/impl-feature-auto/checks/_common.py`
- Create: `.github/agent-factory/protocols/impl-feature-auto/checks/ledger-wellformed.py`
- Test: `tests/test_impl_feature_auto_checks.py` (create)

**Interfaces:**
- Produces:
  - `_common.load_evidence(path) -> dict` — parse evidence JSON; `{}` on error.
  - `_common.NON_TRIVIAL(s) -> bool` — truthy iff `s` is a non-empty string after strip and not in `{"todo","tbd","n/a","na","none","-",""}` (case-insensitive).
  - `_common.sibling(evidence_path, name) -> str|None` — absolute path of `name` in the evidence file's directory if it exists, else None (used by Tasks 7–8 to read bundled `spec.md`/`plan.md`).
  - `_common.RISK(item) -> int` — the 0..6 risk score (used by Task 7; defined here so it lives with the ledger model).
  - check output `{"check":"ledger-wellformed","pass":bool,"feedback":str}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_impl_feature_auto_checks.py`:

```python
import json, os, pathlib, subprocess
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHK = ROOT / ".github/agent-factory/protocols/impl-feature-auto/checks"

def run_check(name, evidence_obj, tmp_path, extra_files=None):
    """Write evidence (+ optional sibling files like spec.md) and run the check."""
    ev = tmp_path / "evidence.json"
    ev.write_text(json.dumps(evidence_obj))
    for fn, content in (extra_files or {}).items():
        (tmp_path / fn).write_text(content)
    empty = tmp_path / "empty.txt"; empty.write_text("")
    r = subprocess.run(
        ["python3", str(CHK / f"{name}.py"), str(ev), str(empty), str(empty)],
        text=True, capture_output=True)
    assert r.returncode == 0, f"check must exit 0; stderr={r.stderr}"
    return json.loads(r.stdout)

def good_item(**over):
    item = {
        "id": "L1", "category": "DECISION",
        "what": "Use issue-<N> instance keys",
        "why": "mirrors recover ref-keying",
        "what_i_did": "added target field",
        "confidence": "high",
        "blast_radius": {"level": "low", "why": "internal routing only"},
        "reversibility": {"level": "reversible", "why": "field is additive"},
        "revisit_if": "a third keying scheme appears",
    }
    item.update(over)
    return item

def good_evidence(**over):
    ev = {"spec_path": "docs/superpowers/specs/x-design.md",
          "plan_path": "docs/superpowers/plans/x.md",
          "ledger": [good_item()], "read_these_first": []}
    ev.update(over)
    return ev

# ---- ledger-wellformed ----
def test_wellformed_passes_on_good_ledger(tmp_path):
    out = run_check("ledger-wellformed", good_evidence(), tmp_path)
    assert out["pass"] is True, out

def test_wellformed_fails_missing_field(tmp_path):
    item = good_item(); del item["why"]
    out = run_check("ledger-wellformed", good_evidence(ledger=[item]), tmp_path)
    assert out["pass"] is False and "why" in out["feedback"]

def test_wellformed_fails_trivial_field(tmp_path):
    out = run_check("ledger-wellformed", good_evidence(ledger=[good_item(what_i_did="TODO")]), tmp_path)
    assert out["pass"] is False

def test_wellformed_fails_bad_enum(tmp_path):
    out = run_check("ledger-wellformed", good_evidence(ledger=[good_item(confidence="maybe")]), tmp_path)
    assert out["pass"] is False

def test_wellformed_fails_bad_blast_level(tmp_path):
    it = good_item(); it["blast_radius"] = {"level": "huge", "why": "x"}
    out = run_check("ledger-wellformed", good_evidence(ledger=[it]), tmp_path)
    assert out["pass"] is False

def test_wellformed_fails_blast_why_trivial(tmp_path):
    it = good_item(); it["blast_radius"] = {"level": "high", "why": "N/A"}
    out = run_check("ledger-wellformed", good_evidence(ledger=[it]), tmp_path)
    assert out["pass"] is False and "blast_radius" in out["feedback"]

def test_wellformed_assumption_requires_verified_true(tmp_path):
    it = good_item(category="ASSUMPTION")  # no `verified`
    out = run_check("ledger-wellformed", good_evidence(ledger=[it]), tmp_path)
    assert out["pass"] is False and "verified" in out["feedback"]
    it2 = good_item(category="ASSUMPTION", verified=True)
    assert run_check("ledger-wellformed", good_evidence(ledger=[it2]), tmp_path)["pass"] is True

def test_wellformed_fails_empty_ledger(tmp_path):
    out = run_check("ledger-wellformed", good_evidence(ledger=[]), tmp_path)
    assert out["pass"] is False
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_impl_feature_auto_checks.py -k wellformed -v`
Expected: FAIL — check files don't exist.

- [ ] **Step 3: Write `_common.py`**

```python
#!/usr/bin/env python3
"""Shared helpers for impl-feature-auto checks. Python 3 stdlib only."""
import json
import os

_TRIVIAL = {"", "todo", "tbd", "n/a", "na", "none", "-"}

CONF = {"low": 2, "med": 1, "high": 0}
BLAST = {"high": 2, "medium": 1, "low": 0}
REV = {"irreversible": 2, "costly": 1, "reversible": 0}


def load_evidence(path):
    try:
        with open(path) as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def NON_TRIVIAL(s):
    return isinstance(s, str) and s.strip().lower() not in _TRIVIAL


def sibling(evidence_path, name):
    p = os.path.join(os.path.dirname(os.path.abspath(evidence_path)), name)
    return p if os.path.isfile(p) else None


def RISK(item):
    """0..6 risk score over the three typed axes (low confidence x high/irreversible)."""
    c = CONF.get(item.get("confidence"), 0)
    b = BLAST.get((item.get("blast_radius") or {}).get("level"), 0)
    r = REV.get((item.get("reversibility") or {}).get("level"), 0)
    return c + b + r
```

- [ ] **Step 4: Write `ledger-wellformed.py`**

```python
#!/usr/bin/env python3
"""ledger-wellformed (layer 1) — per-item completeness + valid enums + justified
blast_radius/reversibility + ASSUMPTION ⇒ verified:true. Form only; never judges
whether a rating is calibrated. Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402

CATEGORIES = {"DECISION", "ASSUMPTION", "UNKNOWN", "DEFERRED", "DEVIATION"}
CONF = {"high", "med", "low"}
BLAST = {"low", "medium", "high"}
REV = {"reversible", "costly", "irreversible"}
SCALARS = ("what", "why", "what_i_did", "revisit_if")


def emit(ok, feedback):
    print(json.dumps({"check": "ledger-wellformed", "pass": ok, "feedback": feedback}))


def main():
    ev = _common.load_evidence(sys.argv[1] if len(sys.argv) > 1 else "")
    ledger = ev.get("ledger")
    if not isinstance(ledger, list) or not ledger:
        emit(False, "ledger missing or empty")
        return
    problems = []
    for i, it in enumerate(ledger):
        tag = it.get("id", f"[{i}]") if isinstance(it, dict) else f"[{i}]"
        if not isinstance(it, dict):
            problems.append(f"{tag}: not an object"); continue
        if it.get("category") not in CATEGORIES:
            problems.append(f"{tag}: category {it.get('category')!r} not in {sorted(CATEGORIES)}")
        for f in SCALARS:
            if not _common.NON_TRIVIAL(it.get(f)):
                problems.append(f"{tag}: field {f!r} missing/trivial")
        if it.get("confidence") not in CONF:
            problems.append(f"{tag}: confidence {it.get('confidence')!r} invalid")
        for axis, allowed in (("blast_radius", BLAST), ("reversibility", REV)):
            obj = it.get(axis)
            if not isinstance(obj, dict):
                problems.append(f"{tag}: {axis} missing/not-object"); continue
            if obj.get("level") not in allowed:
                problems.append(f"{tag}: {axis}.level {obj.get('level')!r} invalid")
            if not _common.NON_TRIVIAL(obj.get("why")):
                problems.append(f"{tag}: {axis}.why missing/trivial (must justify the level)")
        if it.get("category") == "ASSUMPTION" and it.get("verified") is not True:
            problems.append(f"{tag}: ASSUMPTION must carry verified:true (verify the code fact)")
    if problems:
        emit(False, "; ".join(problems[:8]))
    else:
        emit(True, "")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the wellformed tests to verify they pass**

Run: `uv run pytest tests/test_impl_feature_auto_checks.py -k wellformed -v`
Expected: PASS (all 8).

- [ ] **Step 6: Commit**

```bash
git add .github/agent-factory/protocols/impl-feature-auto/checks/_common.py .github/agent-factory/protocols/impl-feature-auto/checks/ledger-wellformed.py tests/test_impl_feature_auto_checks.py
git commit -m "feat(impl-feature-auto): ledger-wellformed check (layer 1)"
```

---

## Task 6: `ledger-consistent` check (layer 2)

Rule-based contradictions the grammar exposes — not judgments. Deterministic rules that pass layer 1 but fail here.

**Files:**
- Create: `.github/agent-factory/protocols/impl-feature-auto/checks/ledger-consistent.py`
- Test: `tests/test_impl_feature_auto_checks.py` (extend)

**Interfaces:**
- Consumes: `_common`.
- Produces: `{"check":"ledger-consistent","pass":bool,"feedback":str}`. Rules: **R1** `category=="UNKNOWN" ⇒ confidence=="low"` (UNKNOWN is low-confidence by definition); **R2** ledger `id`s are unique (no duplicates).

> The spec's other layer-2 examples ("DEVIATION with empty what-it-conflicted-with", "confidence:low with empty revisit_if") reduce to layer-1 non-triviality given this schema (there is no separate conflict field, and `revisit_if` is already required non-trivial for every item). R1 + R2 are the contradictions that survive past layer 1. The layer is intentionally extensible.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_impl_feature_auto_checks.py`:

```python
# ---- ledger-consistent ----
def test_consistent_passes_clean(tmp_path):
    out = run_check("ledger-consistent", good_evidence(), tmp_path)
    assert out["pass"] is True, out

def test_consistent_fails_unknown_high_confidence(tmp_path):
    it = good_item(id="L1", category="UNKNOWN", confidence="high")
    out = run_check("ledger-consistent", good_evidence(ledger=[it]), tmp_path)
    assert out["pass"] is False and "UNKNOWN" in out["feedback"]

def test_consistent_passes_unknown_low_confidence(tmp_path):
    it = good_item(id="L1", category="UNKNOWN", confidence="low")
    out = run_check("ledger-consistent", good_evidence(ledger=[it]), tmp_path)
    assert out["pass"] is True

def test_consistent_fails_duplicate_ids(tmp_path):
    led = [good_item(id="L1"), good_item(id="L1")]
    out = run_check("ledger-consistent", good_evidence(ledger=led), tmp_path)
    assert out["pass"] is False and "L1" in out["feedback"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_impl_feature_auto_checks.py -k consistent -v`
Expected: FAIL — check absent.

- [ ] **Step 3: Write `ledger-consistent.py`**

```python
#!/usr/bin/env python3
"""ledger-consistent (layer 2) — rule-based contradictions (deterministic, not
judgments). R1: UNKNOWN ⇒ confidence==low. R2: ledger ids unique.
Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402


def emit(ok, feedback):
    print(json.dumps({"check": "ledger-consistent", "pass": ok, "feedback": feedback}))


def main():
    ev = _common.load_evidence(sys.argv[1] if len(sys.argv) > 1 else "")
    ledger = ev.get("ledger")
    if not isinstance(ledger, list) or not ledger:
        emit(False, "ledger missing or empty")
        return
    problems = []
    seen = set()
    for it in ledger:
        if not isinstance(it, dict):
            continue
        tag = it.get("id", "?")
        if it.get("category") == "UNKNOWN" and it.get("confidence") != "low":
            problems.append(f"{tag}: UNKNOWN with confidence {it.get('confidence')!r} "
                            f"— UNKNOWN is low-confidence by definition")
        if tag in seen:
            problems.append(f"duplicate ledger id {tag!r}")
        seen.add(tag)
    if problems:
        emit(False, "; ".join(problems[:8]))
    else:
        emit(True, "")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the consistent tests to verify they pass**

Run: `uv run pytest tests/test_impl_feature_auto_checks.py -k consistent -v`
Expected: PASS (4).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/protocols/impl-feature-auto/checks/ledger-consistent.py tests/test_impl_feature_auto_checks.py
git commit -m "feat(impl-feature-auto): ledger-consistent check (layer 2)"
```

---

## Task 7: `read-these-first-consistent` check (layer 3)

Honest triage: every high-risk item appears in `read_these_first`; every entry references a real id; order is monotonic non-increasing by risk; and every ledger `id`/`what` also appears in the bundled `spec.md` (cross-reference so the JSON can't diverge from the prose).

**Files:**
- Create: `.github/agent-factory/protocols/impl-feature-auto/checks/read-these-first-consistent.py`
- Test: `tests/test_impl_feature_auto_checks.py` (extend)
- Fixture: `tests/fixtures/impl-feature-auto/spec-good.md` (created inline by the test via `extra_files`)

**Interfaces:**
- Consumes: `_common.RISK`, `_common.sibling`.
- Produces: `{"check":"read-these-first-consistent","pass":bool,"feedback":str}`. Reads `spec.md` from the evidence file's directory; if absent → fail with a clear message (the design agent must bundle it — Task 11).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_impl_feature_auto_checks.py`:

```python
# ---- read-these-first-consistent ----
def _spec_for(items):
    # A minimal spec that mentions every id + what (cross-ref must pass).
    lines = ["# Spec\n", "## Accountability Ledger\n"]
    for it in items:
        lines.append(f"- {it['id']}: {it['what']}\n")
    return "".join(lines)

def test_rtf_passes_when_highrisk_listed_and_ordered(tmp_path):
    hi = good_item(id="L1", confidence="low",
                   blast_radius={"level": "high", "why": "broad"},
                   reversibility={"level": "irreversible", "why": "published"})   # risk 6
    lo = good_item(id="L2")  # risk 0
    ev = good_evidence(ledger=[hi, lo], read_these_first=["L1"])
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": _spec_for([hi, lo])})
    assert out["pass"] is True, out

def test_rtf_fails_buried_highrisk(tmp_path):
    hi = good_item(id="L1", confidence="low",
                   blast_radius={"level": "high", "why": "broad"},
                   reversibility={"level": "irreversible", "why": "x"})
    ev = good_evidence(ledger=[hi], read_these_first=[])   # high-risk omitted
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": _spec_for([hi])})
    assert out["pass"] is False and "L1" in out["feedback"]

def test_rtf_fails_unknown_id(tmp_path):
    it = good_item(id="L1")
    ev = good_evidence(ledger=[it], read_these_first=["L9"])
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": _spec_for([it])})
    assert out["pass"] is False and "L9" in out["feedback"]

def test_rtf_fails_misordered(tmp_path):
    a = good_item(id="L1")  # risk 0
    b = good_item(id="L2", confidence="low",
                  blast_radius={"level": "high", "why": "x"},
                  reversibility={"level": "irreversible", "why": "x"})  # risk 6
    ev = good_evidence(ledger=[a, b], read_these_first=["L1", "L2"])  # ascending = wrong
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": _spec_for([a, b])})
    assert out["pass"] is False and "order" in out["feedback"].lower()

def test_rtf_fails_spec_divergence(tmp_path):
    it = good_item(id="L1", what="A decision the spec forgot")
    ev = good_evidence(ledger=[it], read_these_first=[])
    out = run_check("read-these-first-consistent", ev, tmp_path,
                    extra_files={"spec.md": "# Spec\n## Accountability Ledger\n(empty)\n"})
    assert out["pass"] is False and "spec" in out["feedback"].lower()

def test_rtf_fails_when_spec_missing(tmp_path):
    it = good_item(id="L1")
    ev = good_evidence(ledger=[it], read_these_first=[])
    out = run_check("read-these-first-consistent", ev, tmp_path)  # no spec.md bundled
    assert out["pass"] is False and "spec.md" in out["feedback"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_impl_feature_auto_checks.py -k rtf -v`
Expected: FAIL — check absent.

- [ ] **Step 3: Write `read-these-first-consistent.py`**

```python
#!/usr/bin/env python3
"""read-these-first-consistent (layer 3) — honest triage over the typed risk axes
+ spec cross-reference. Reads the bundled spec.md (sibling of evidence.json).
Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402

HIGH_RISK = 2  # risk >= 2 must be surfaced


def emit(ok, feedback):
    print(json.dumps({"check": "read-these-first-consistent", "pass": ok, "feedback": feedback}))


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    ev = _common.load_evidence(ev_path)
    ledger = ev.get("ledger")
    rtf = ev.get("read_these_first")
    if not isinstance(ledger, list) or not ledger:
        emit(False, "ledger missing or empty")
        return
    if not isinstance(rtf, list):
        emit(False, "read_these_first missing or not a list")
        return
    by_id = {it.get("id"): it for it in ledger if isinstance(it, dict)}
    problems = []

    # every rtf entry references a real id
    for rid in rtf:
        if rid not in by_id:
            problems.append(f"read_these_first id {rid!r} not in ledger")

    # every high-risk item must be surfaced
    for it in ledger:
        if _common.RISK(it) >= HIGH_RISK and it.get("id") not in rtf:
            problems.append(f"high-risk item {it.get('id')!r} (risk "
                            f"{_common.RISK(it)}) buried — must be in read_these_first")

    # order monotonic non-increasing by risk (ties any order); only over known ids
    known = [r for r in rtf if r in by_id]
    risks = [_common.RISK(by_id[r]) for r in known]
    if any(risks[i] < risks[i + 1] for i in range(len(risks) - 1)):
        problems.append(f"read_these_first order is not risk-descending: {list(zip(known, risks))}")

    # cross-reference: every ledger id + its `what` must appear in the spec prose
    spec_path = _common.sibling(ev_path, "spec.md")
    if not spec_path:
        problems.append("bundled spec.md not found beside evidence.json (cannot cross-reference)")
    else:
        spec = open(spec_path, encoding="utf-8", errors="replace").read()
        for it in ledger:
            i = it.get("id", "")
            w = (it.get("what") or "").strip()
            if i and i not in spec:
                problems.append(f"ledger id {i!r} absent from spec.md (JSON/prose divergence)")
            elif w and w not in spec:
                problems.append(f"ledger {i} `what` text absent from spec.md (divergence)")

    if problems:
        emit(False, "; ".join(problems[:8]))
    else:
        emit(True, "")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the rtf tests to verify they pass**

Run: `uv run pytest tests/test_impl_feature_auto_checks.py -k rtf -v`
Expected: PASS (6).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/protocols/impl-feature-auto/checks/read-these-first-consistent.py tests/test_impl_feature_auto_checks.py
git commit -m "feat(impl-feature-auto): read-these-first-consistent check (layer 3)"
```

---

## Task 8: `spec-present` + `plan-present` checks (block-severity)

Presence gates. `spec-present` reads the bundled `spec.md` and confirms the 5 required section headings. `plan-present` confirms `evidence.plan_path` is set and a bundled `plan.md` exists and is non-empty. Both `block` — a missing prerequisite ends the run `failed` with nothing created (the `implement` node never runs).

**Files:**
- Create: `.github/agent-factory/protocols/impl-feature-auto/checks/spec-present.py`
- Create: `.github/agent-factory/protocols/impl-feature-auto/checks/plan-present.py`
- Test: `tests/test_impl_feature_auto_checks.py` (extend)

**Interfaces:**
- Consumes: `_common.sibling`, `_common.NON_TRIVIAL`.
- Produces: `{"check":"spec-present"|"plan-present","pass":bool,"feedback":str}`. Required spec sections (case-insensitive substring of a markdown heading line): `summary`, `scope`, `behavior` (matches "Behavior/acceptance criteria"), `accountability ledger`, `read these first`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_impl_feature_auto_checks.py`:

```python
FULL_SPEC = """# Feature X — design
## Summary
...
## Scope
...
## Behavior / acceptance criteria
...
## Accountability Ledger
- L1: ...
## READ THESE FIRST
- L1
"""

# ---- spec-present ----
def test_spec_present_passes_with_all_sections(tmp_path):
    out = run_check("spec-present", good_evidence(), tmp_path,
                    extra_files={"spec.md": FULL_SPEC})
    assert out["pass"] is True, out

def test_spec_present_fails_missing_section(tmp_path):
    spec = FULL_SPEC.replace("## READ THESE FIRST", "## Other")
    out = run_check("spec-present", good_evidence(), tmp_path, extra_files={"spec.md": spec})
    assert out["pass"] is False and "read these first" in out["feedback"].lower()

def test_spec_present_fails_no_spec_file(tmp_path):
    out = run_check("spec-present", good_evidence(), tmp_path)
    assert out["pass"] is False and "spec.md" in out["feedback"]

# ---- plan-present ----
def test_plan_present_passes(tmp_path):
    out = run_check("plan-present", good_evidence(), tmp_path,
                    extra_files={"plan.md": "# Plan\n## Task 1\n..."})
    assert out["pass"] is True, out

def test_plan_present_fails_no_plan_file(tmp_path):
    out = run_check("plan-present", good_evidence(), tmp_path)
    assert out["pass"] is False

def test_plan_present_fails_empty_plan_path(tmp_path):
    out = run_check("plan-present", good_evidence(plan_path=""), tmp_path,
                    extra_files={"plan.md": "# Plan\n"})
    assert out["pass"] is False and "plan_path" in out["feedback"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_impl_feature_auto_checks.py -k "spec_present or plan_present" -v`
Expected: FAIL — checks absent.

- [ ] **Step 3: Write `spec-present.py`**

```python
#!/usr/bin/env python3
"""spec-present (block) — the bundled spec.md exists and carries the 5 required
sections. Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402

REQUIRED = ["summary", "scope", "behavior", "accountability ledger", "read these first"]


def emit(ok, feedback):
    print(json.dumps({"check": "spec-present", "pass": ok, "feedback": feedback}))


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    spec_path = _common.sibling(ev_path, "spec.md")
    if not spec_path:
        emit(False, "no spec.md bundled beside evidence.json (design must write + upload the spec)")
        return
    text = open(spec_path, encoding="utf-8", errors="replace").read().lower()
    # consider only markdown heading lines for section matching
    headings = "\n".join(ln for ln in text.splitlines() if ln.lstrip().startswith("#"))
    missing = [s for s in REQUIRED if s not in headings]
    if missing:
        emit(False, f"spec.md missing required section(s): {', '.join(missing)}")
    else:
        emit(True, "")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write `plan-present.py`**

```python
#!/usr/bin/env python3
"""plan-present (block) — evidence.plan_path is set and a bundled plan.md exists
and is non-empty. Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402


def emit(ok, feedback):
    print(json.dumps({"check": "plan-present", "pass": ok, "feedback": feedback}))


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    ev = _common.load_evidence(ev_path)
    if not _common.NON_TRIVIAL(ev.get("plan_path")):
        emit(False, "evidence.plan_path missing/trivial (writing-plans produced no plan)")
        return
    plan_path = _common.sibling(ev_path, "plan.md")
    if not plan_path or os.path.getsize(plan_path) == 0:
        emit(False, "no non-empty plan.md bundled beside evidence.json")
        return
    emit(True, "")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the presence tests to verify they pass**

Run: `uv run pytest tests/test_impl_feature_auto_checks.py -k "spec_present or plan_present" -v`
Expected: PASS (6).

- [ ] **Step 6: Re-run the lint test from Task 4**

Now all five `design` checks resolve to files.
Run: `uv run pytest tests/test_protocol_lint.py::test_impl_feature_auto_protocol_lints_clean -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add .github/agent-factory/protocols/impl-feature-auto/checks/spec-present.py .github/agent-factory/protocols/impl-feature-auto/checks/plan-present.py tests/test_impl_feature_auto_checks.py
git commit -m "feat(impl-feature-auto): spec-present + plan-present checks (block)"
```

---

## Task 9: `implement-schema-valid` check

The single check on the `implement` node: evidence carries a non-trivial `summary` and a `pr_branch` matching `impl-feature-auto/issue-<N>`. Supplies the one passing verdict the engine needs to reach `done` (recall `decide([])` would otherwise fail the node), and fails an `implement` run that produced no resolvable PR branch.

**Files:**
- Create: `.github/agent-factory/protocols/impl-feature-auto/checks/implement-schema-valid.py`
- Test: `tests/test_impl_feature_auto_checks.py` (extend)

**Interfaces:**
- Consumes: `_common`.
- Produces: `{"check":"implement-schema-valid","pass":bool,"feedback":str}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_impl_feature_auto_checks.py`:

```python
# ---- implement-schema-valid ----
def test_impl_valid_passes(tmp_path):
    ev = {"summary": "Implemented feature X", "pr_branch": "impl-feature-auto/issue-42"}
    out = run_check("implement-schema-valid", ev, tmp_path)
    assert out["pass"] is True, out

def test_impl_valid_fails_bad_branch(tmp_path):
    ev = {"summary": "x", "pr_branch": "feature/whatever"}
    out = run_check("implement-schema-valid", ev, tmp_path)
    assert out["pass"] is False and "pr_branch" in out["feedback"]

def test_impl_valid_fails_missing_summary(tmp_path):
    ev = {"pr_branch": "impl-feature-auto/issue-1"}
    out = run_check("implement-schema-valid", ev, tmp_path)
    assert out["pass"] is False and "summary" in out["feedback"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_impl_feature_auto_checks.py -k impl_valid -v`
Expected: FAIL — check absent.

- [ ] **Step 3: Write `implement-schema-valid.py`**

```python
#!/usr/bin/env python3
"""implement-schema-valid — the implement node's only check: evidence carries a
non-trivial summary and a pr_branch of the form impl-feature-auto/issue-<N> (so
post-summary can resolve the PR). Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402

BRANCH_RE = re.compile(r"^impl-feature-auto/issue-[0-9]+$")


def emit(ok, feedback):
    print(json.dumps({"check": "implement-schema-valid", "pass": ok, "feedback": feedback}))


def main():
    ev = _common.load_evidence(sys.argv[1] if len(sys.argv) > 1 else "")
    problems = []
    if not _common.NON_TRIVIAL(ev.get("summary")):
        problems.append("summary missing/trivial")
    pr_branch = ev.get("pr_branch", "")
    if not isinstance(pr_branch, str) or not BRANCH_RE.match(pr_branch):
        problems.append(f"pr_branch {pr_branch!r} not of form impl-feature-auto/issue-<N>")
    if problems:
        emit(False, "; ".join(problems))
    else:
        emit(True, "")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the impl_valid tests + the whole checks module**

Run: `uv run pytest tests/test_impl_feature_auto_checks.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/protocols/impl-feature-auto/checks/implement-schema-valid.py tests/test_impl_feature_auto_checks.py
git commit -m "feat(impl-feature-auto): implement-schema-valid check"
```

---

## Task 10: `post-summary` publish hook (zone 4)

After the `implement` checks pass, summarise + link the PR (resolved by `pr_branch`) and comment on the originating issue. On a `design` block the run ended `failed` with no PR, so the hook just records the failure. Defensive: if `implement` produced no resolvable PR, report that rather than claim success. No GitHub I/O in `ENGINE_LOCAL=1`.

**Files:**
- Create: `.github/agent-factory/protocols/impl-feature-auto/publish/post-summary.py`
- Test: `tests/test_impl_feature_auto_publish.py` (create)

**Interfaces:**
- ABI: `post-summary.py <evidence.json> <instance-key>`; env `ENGINE_LOCAL`, `GITHUB_REPOSITORY`, `PUBLISH_TOKEN`, `PR`.
- Produces: stdout `{"conclusion","summary"}`. Uses `lib.pr_from_instance` to get the issue number from `issue-<N>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_impl_feature_auto_publish.py`:

```python
import json, os, pathlib, subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = ROOT / ".github/agent-factory/protocols/impl-feature-auto/publish/post-summary.py"

def run_hook(evidence_obj, instance, tmp_path, env_extra=None):
    ev = tmp_path / "evidence.json"
    ev.write_text(json.dumps(evidence_obj))
    env = dict(os.environ); env["ENGINE_LOCAL"] = "1"
    env.update(env_extra or {})
    r = subprocess.run(["python3", str(HOOK), str(ev), instance],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)

def test_post_summary_local_success(tmp_path):
    out = run_hook({"summary": "did X", "pr_branch": "impl-feature-auto/issue-7"},
                   "issue-7", tmp_path)
    assert out["conclusion"] == "success"
    assert "issue-7" in out["summary"] or "7" in out["summary"]

def test_post_summary_defensive_no_branch(tmp_path):
    out = run_hook({"summary": "did X"}, "issue-7", tmp_path)
    assert out["conclusion"] in ("neutral", "failure")
    assert "pr" in out["summary"].lower()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_impl_feature_auto_publish.py -v`
Expected: FAIL — hook absent.

- [ ] **Step 3: Write `post-summary.py`**

```python
#!/usr/bin/env python3
"""post-summary (zone 4) — after implement's checks pass, resolve the PR by
pr_branch and comment on the originating issue with a link. ENGINE_LOCAL=1 does no
GitHub I/O. ABI: post-summary.py <evidence.json> <instance-key>; env ENGINE_LOCAL,
GITHUB_REPOSITORY, PUBLISH_TOKEN, PR. Prints {"conclusion","summary"}.

This hook only summarises/links — the PR WRITE is gh-aw safe-outputs (implement)."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "engine"))
import lib  # noqa: E402


def _local():
    return os.environ.get("ENGINE_LOCAL", "0") == "1"


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    instance = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        with open(ev_path) as fh:
            ev = json.load(fh)
    except (OSError, ValueError):
        ev = {}
    pr_branch = (ev.get("pr_branch") or "").strip()
    issue = lib.pr_from_instance(instance)

    if not pr_branch:
        print(json.dumps({"conclusion": "neutral",
                          "summary": "implement produced no pr_branch; no PR to link."}))
        return

    if _local():
        print(json.dumps({"conclusion": "success",
                          "summary": f"[local] would link PR on branch {pr_branch} to {instance}."}))
        return

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    env = dict(os.environ)
    env["GH_TOKEN"] = os.environ.get("PUBLISH_TOKEN", os.environ.get("GH_TOKEN", ""))
    r = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--head", pr_branch,
         "--state", "open", "--json", "number,url", "--limit", "1"],
        text=True, capture_output=True, env=env)
    prs = []
    if r.returncode == 0 and r.stdout.strip():
        try:
            prs = json.loads(r.stdout)
        except ValueError:
            prs = []
    if not prs:
        print(json.dumps({"conclusion": "failure",
                          "summary": f"No open PR found for branch {pr_branch}."}))
        return
    pr_num, pr_url = prs[0].get("number"), prs[0].get("url")
    if str(issue).isdigit():
        lib.post_pr_comment(issue,
                            f"🤖 **Feature implemented** — opened PR #{pr_num}: {pr_url}\n\n"
                            f"{ev.get('summary', '').strip()}")
    print(json.dumps({"conclusion": "success",
                      "summary": f"Linked PR #{pr_num} for issue {instance}."}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the publish tests to verify they pass**

Run: `uv run pytest tests/test_impl_feature_auto_publish.py -v`
Expected: PASS (2).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/protocols/impl-feature-auto/publish/post-summary.py tests/test_impl_feature_auto_publish.py
git commit -m "feat(impl-feature-auto): post-summary publish hook (zone 4)"
```

---

## Task 11: `design-agent` workflow + superpowers staging

The Phase-0 agent: stage superpowers, prefetch the issue, run Phase 0 (spec + structured ledger) and `writing-plans`, bundle `spec.md`/`plan.md` beside `evidence.json`, upload it. Read-only; no PR. Mirrors `preflight-agent.md`/`mm-legion-agent.md` frontmatter.

**Files:**
- Create: `.github/workflows/impl-feature-auto-design-agent.md`
- Create (compiled): `.github/workflows/impl-feature-auto-design-agent.lock.yml` (via `gh aw compile`)
- Test: `tests/test_workflow_contract.py` (extend — lock contract assertions)

**Interfaces:**
- Consumes: engine `aw_context` (`issue` number, `iteration`, `feedback`, `cid`, `ref`, `sha`).
- Produces: `/tmp/gh-aw/evidence/evidence.json` (+ `spec.md`, `plan.md` siblings) uploaded as the `evidence` artifact; evidence carries `run_id`, `spec_path`, `plan_path`, `ledger`, `read_these_first`.

- [ ] **Step 1: Write the failing lock-contract test**

Append to `tests/test_workflow_contract.py`:

```python
def test_design_agent_lock_is_readonly_and_bundles_spec():
    t = _load("impl-feature-auto-design-agent.lock.yml")
    assert "pull-requests: write" not in t  # read-only
    assert "create-pull-request" not in t   # design opens no PR
    assert "evidence" in t                  # uploads evidence artifact
    assert ".claude/skills" in t            # stages superpowers

def test_implement_agent_lock_opens_pr():
    t = _load("impl-feature-auto-implement-agent.lock.yml")
    assert "create-pull-request" in t       # implement opens the PR via safe-outputs
```

(The implement assertion is satisfied by Task 12; running this test now fails on both files.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_workflow_contract.py -k "design_agent_lock or implement_agent_lock" -v`
Expected: FAIL — lock files don't exist.

- [ ] **Step 3: Write the design agent frontmatter + prompt**

`.github/workflows/impl-feature-auto-design-agent.md` (frontmatter — mirror `preflight-agent.md`; key differences: `target` ref checkout, superpowers staging, issue prefetch, evidence bundle upload):

```markdown
---
name: "Impl-Feature-Auto Design Agent (protocol state: design)"
run-name: "Impl-Feature-Auto Design · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
strict: false
sandbox:
  agent: false
features:
  dangerously-disable-sandbox-agent: "POC custom Anthropic endpoint cannot be expressed in AWF static egress allowlist; agent stays read-only and never holds the state PAT"
engine:
  id: claude
  model: claude-sonnet-4-6
  env:
    ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
    ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
permissions:
  contents: read
  issues: read
  pull-requests: read
tools:
  cli-proxy: true
  edit: true
  bash:
    - "gh issue view *"
    - "git *"
    - "cat:*"
    - "ls:*"
    - "mkdir:*"
    - "cp:*"
pre-agent-steps:
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw/agent
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
      cat /tmp/gh-aw/task-context.json
  - name: Checkout target ref
    uses: actions/checkout@v5
    with:
      ref: ${{ fromJSON(github.event.inputs.aw_context || '{}').ref }}
      path: target
      persist-credentials: false
      fetch-depth: 0
  - name: Stage superpowers skills (pinned release tag)
    run: |
      set -euo pipefail
      SP_VERSION="v6.0.3"; DEST="$GITHUB_WORKSPACE/target/.claude/skills"
      mkdir -p "$DEST"
      curl -fsSL "https://github.com/obra/superpowers/archive/refs/tags/${SP_VERSION}.tar.gz" -o /tmp/sp.tgz
      tar -xzf /tmp/sp.tgz --strip-components=2 -C "$DEST" "superpowers-${SP_VERSION#v}/skills"
      ls "$DEST" | head
  - name: Prefetch the issue
    env:
      GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      ISSUE: ${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}
      REPO: ${{ github.repository }}
    run: |
      set -euo pipefail
      gh issue view "$ISSUE" --repo "$REPO" \
        --json number,title,body,labels,author,url > /tmp/gh-aw/agent/issue.json
      cat /tmp/gh-aw/agent/issue.json
post-steps:
  - name: Bundle + upload evidence (json + spec.md + plan.md)
    if: always()
    run: |
      set -uo pipefail
      OUT=/tmp/gh-aw/evidence
      mkdir -p "$OUT"
      # The agent wrote evidence.json to /tmp/gh-aw/evidence.json and recorded
      # spec_path/plan_path (repo-relative, under target/). Copy them in by fixed name.
      cp /tmp/gh-aw/evidence.json "$OUT/evidence.json" 2>/dev/null || echo '{}' > "$OUT/evidence.json"
      SPEC=$(python3 -c 'import json,sys;print(json.load(open("/tmp/gh-aw/evidence.json")).get("spec_path",""))' 2>/dev/null || true)
      PLAN=$(python3 -c 'import json,sys;print(json.load(open("/tmp/gh-aw/evidence.json")).get("plan_path",""))' 2>/dev/null || true)
      [ -n "$SPEC" ] && cp "$GITHUB_WORKSPACE/target/$SPEC" "$OUT/spec.md" 2>/dev/null || true
      [ -n "$PLAN" ] && cp "$GITHUB_WORKSPACE/target/$PLAN" "$OUT/plan.md" 2>/dev/null || true
      ls -la "$OUT"
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence
      if-no-files-found: warn
timeout-minutes: 30
---
```

Prompt body (after the frontmatter `---`):

```markdown
<!-- BOOTSTRAP: gh-aw runs `claude --print`, where the SessionStart hook may not
fire, so we inline the using-superpowers bootstrap to make the model reliably
reach for the staged skills. -->

You have superpowers. Skills live under `.claude/skills/` (staged as PROJECT
skills, so they are BARE-NAMED — `writing-plans`, not `superpowers:writing-plans`).
Before any creative work, check whether a skill applies and use it.

# Design Agent — Phase 0 only (spec + Accountability Ledger + plan). NO CODE, NO PR.

Working directory: `target/` (the analyzed codebase, checked out at the default branch).

## 1. Read the request
Read `/tmp/gh-aw/agent/issue.json` (the feature request: title + body). Read
`/tmp/gh-aw/task-context.json` (`pr` = issue number, `iteration`, `feedback`).
On iteration > 1, fold the `feedback` (failed ledger/spec/plan checks) into this pass.

## 2. Phase 0 — write the spec
Write a spec to `target/docs/superpowers/specs/<YYYY-MM-DD>-<topic>-design.md` with
EXACTLY these sections (the `spec-present` check requires all five headings):
`## Summary`, `## Scope`, `## Behavior / acceptance criteria`,
`## Accountability Ledger`, `## READ THESE FIRST`.

Fill gaps yourself (this is autonomous). For every gap you fill, add a ledger entry.
The **Accountability Ledger** records each gap: category (DECISION | ASSUMPTION |
UNKNOWN | DEFERRED | DEVIATION), what / why / what-I-did, confidence (high|med|low),
**blast radius** (level low|medium|high + WHY), **reversibility** (level
reversible|costly|irreversible + WHY), and a revisit-if condition. An ASSUMPTION
that asserts a fact about the code MUST be verified against the codebase and marked
verified. **READ THESE FIRST** lists the ledger ids risk-sorted (low-confidence ×
high/irreversible first).

## 3. Produce the plan
Use the `writing-plans` skill on the spec to write a plan to
`target/docs/superpowers/plans/<YYYY-MM-DD>-<topic>.md`.

## 4. Emit evidence — then STOP (do not implement)
Write `/tmp/gh-aw/evidence.json` as ONE JSON object matching design.evidence.schema.json:
`{"spec_path","plan_path","summary","run_id","ledger":[…],"read_these_first":[…]}`
- `spec_path`/`plan_path`: the repo-relative paths under `target/` you just wrote
  (e.g. `docs/superpowers/specs/…-design.md`).
- `run_id`: the value of the `GITHUB_RUN_ID` environment variable.
- `ledger`: the SAME ledger as structured data — one object per gap, fields exactly
  as in §2 (`id` like "L1", `category`, `what`, `why`, `what_i_did`, `confidence`,
  `blast_radius:{level,why}`, `reversibility:{level,why}`, `revisit_if`, and
  `verified:true` on any ASSUMPTION asserting a code fact). Every `id` and `what`
  MUST also appear verbatim in the spec's Ledger section (a check cross-references).
- `read_these_first`: ledger ids, risk-descending.
Write nothing else. Do NOT write code, do NOT open a PR, do NOT comment on GitHub.
```

- [ ] **Step 4: Compile the lock**

Run: `gh aw compile`
Expected: regenerates `impl-feature-auto-design-agent.lock.yml`. (If `gh aw` is unavailable in the dev env, note it in the commit body and compile on a machine that has it — the lock is required for the workflow to run, and the contract test in Step 5 reads the lock.)

- [ ] **Step 5: Run the design lock-contract test**

Run: `uv run pytest tests/test_workflow_contract.py -k design_agent_lock -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/impl-feature-auto-design-agent.md .github/workflows/impl-feature-auto-design-agent.lock.yml tests/test_workflow_contract.py
git commit -m "feat(impl-feature-auto): design agent (Phase 0 + writing-plans, read-only)"
```

---

## Task 12: `implement-agent` workflow

The implementation agent: download the design spec/plan by `run_id`, execute the plan under TDD via superpowers, finish the branch, open the PR via `safe-outputs: create-pull-request` on `impl-feature-auto/issue-<N>`. Emits minimal evidence (`summary`, `pr_branch`, `run_id`).

**Files:**
- Create: `.github/workflows/impl-feature-auto-implement-agent.md`
- Create (compiled): `.github/workflows/impl-feature-auto-implement-agent.lock.yml` (via `gh aw compile`)

**Interfaces:**
- Consumes: `aw_context.inputs.design` (design's evidence — incl. `run_id`, `spec_path`, `plan_path`), `aw_context.pr` (issue number), `ref`/`sha`.
- Produces: `/tmp/gh-aw/evidence.json` `{summary, pr_branch, run_id}`; a PR on branch `impl-feature-auto/issue-<N>` via safe-outputs.

- [ ] **Step 1: Write the implement agent frontmatter + prompt**

`.github/workflows/impl-feature-auto-implement-agent.md` frontmatter (mirror Task 11; add `safe-outputs: create-pull-request`, download the design artifacts, allow git branch ops):

```markdown
---
name: "Impl-Feature-Auto Implement Agent (protocol state: implement)"
run-name: "Impl-Feature-Auto Implement · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
strict: false
sandbox:
  agent: false
features:
  dangerously-disable-sandbox-agent: "POC custom Anthropic endpoint cannot be expressed in AWF static egress allowlist; agent stays read-only and never holds the state PAT"
engine:
  id: claude
  model: claude-sonnet-4-6
  env:
    ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
    ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
permissions:
  contents: read
  issues: read
  pull-requests: read
tools:
  cli-proxy: true
  edit: true
  bash:
    - "git *"
    - "gh run download *"
    - "gh issue view *"
    - "cat:*"
    - "ls:*"
    - "mkdir:*"
    - "cp:*"
    - "python3 *"
    - "pytest *"
    - "uv *"
safe-outputs:
  create-pull-request:
    draft: false
pre-agent-steps:
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw/agent
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
      cat /tmp/gh-aw/task-context.json
  - name: Checkout target ref
    uses: actions/checkout@v5
    with:
      ref: ${{ fromJSON(github.event.inputs.aw_context || '{}').ref }}
      path: target
      persist-credentials: false
      fetch-depth: 0
  - name: Stage superpowers skills (pinned release tag)
    run: |
      set -euo pipefail
      SP_VERSION="v6.0.3"; DEST="$GITHUB_WORKSPACE/target/.claude/skills"
      mkdir -p "$DEST"
      curl -fsSL "https://github.com/obra/superpowers/archive/refs/tags/${SP_VERSION}.tar.gz" -o /tmp/sp.tgz
      tar -xzf /tmp/sp.tgz --strip-components=2 -C "$DEST" "superpowers-${SP_VERSION#v}/skills"
  - name: Download design spec + plan (by design run_id)
    env:
      GH_TOKEN: ${{ secrets.POC_DISPATCH_TOKEN }}
      REPO: ${{ github.repository }}
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      set -uo pipefail
      mkdir -p /tmp/gh-aw/design
      # The engine materialized design's evidence into aw_context.inputs.design.
      RID=$(printf '%s' "$CTX" | python3 -c 'import json,sys;c=json.load(sys.stdin);print((c.get("inputs",{}).get("design") or {}).get("run_id",""))')
      if [ -n "$RID" ]; then
        gh run download "$RID" --repo "$REPO" -n evidence -D /tmp/gh-aw/design || echo "no design artifact"
      fi
      ls -la /tmp/gh-aw/design || true
post-steps:
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 60
---
```

Prompt body:

```markdown
You have superpowers. Skills live under `.claude/skills/` (bare-named). Use them.

# Implement Agent — execute the plan with TDD, then open ONE PR.

Working directory: `target/`. Issue number is `pr` in `/tmp/gh-aw/task-context.json`.

## 1. Recover the design artifacts
The design spec + plan were downloaded to `/tmp/gh-aw/design/` (`spec.md`, `plan.md`)
and their repo-relative paths are in `aw_context.inputs.design` (`spec_path`,
`plan_path`) in `/tmp/gh-aw/task-context.json`. Copy `spec.md`/`plan.md` to those
paths under `target/` if not already present, so the PR ships spec + plan.

## 2. Create the feature branch
In `target/`: `git checkout -b impl-feature-auto/issue-<N>` (N = the issue number).

## 3. Execute the plan (TDD)
Use `executing-plans` / `subagent-driven-development` to implement the plan
task-by-task under RED-GREEN-REFACTOR. Run the project's tests. Any mid-implementation
ledger appends go into the spec doc that ships in the PR.

## 4. Finish the branch + open the PR
Use `finishing-a-development-branch`. Commit spec + plan + code + tests on
`impl-feature-auto/issue-<N>`. Open ONE pull request via safe-outputs. The PR body
MUST carry the Accountability Ledger and the READ-THESE-FIRST list (from the design
spec) so the PR is self-describing, and reference the issue (`Closes #<N>`).

## 5. Emit evidence
Write `/tmp/gh-aw/evidence.json` as ONE JSON object:
`{"summary":"<one line>","pr_branch":"impl-feature-auto/issue-<N>","run_id":"<GITHUB_RUN_ID>"}`
Write nothing else.
```

> **safe-outputs branch naming (verify at compile/live):** gh-aw `create-pull-request`
> opens the PR from the branch the agent committed to. The engine resolves the PR by
> that branch in `post-summary`, so the branch MUST be exactly
> `impl-feature-auto/issue-<N>`. If the installed gh-aw version names the branch itself,
> set its branch option to that value. This is the §15 open item to confirm in the
> live step (out of this plan's offline scope).

- [ ] **Step 2: Compile the lock**

Run: `gh aw compile`
Expected: regenerates `impl-feature-auto-implement-agent.lock.yml`.

- [ ] **Step 3: Run the implement lock-contract test**

Run: `uv run pytest tests/test_workflow_contract.py -k implement_agent_lock -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/impl-feature-auto-implement-agent.md .github/workflows/impl-feature-auto-implement-agent.lock.yml
git commit -m "feat(impl-feature-auto): implement agent (TDD + safe-outputs PR)"
```

---

## Task 13: Offline NODE_PATH e2e walk

Walk the **real** `protocol.json` with crafted verdicts (no agent/check execution), proving `design → implement → done` and a `design` **block** → `failed` with `implement` never entered. Mirrors `tests/test_cap_single_agent.py` / `tests/test_unified_codereview_e2e.py`.

**Files:**
- Create: `tests/test_impl_feature_auto_e2e.py`

**Interfaces:**
- Consumes: `next.py`, `advance.py` via the conftest `run_engine`-style subprocess pattern; the real protocol at `.github/agent-factory/protocols/impl-feature-auto/protocol.json`.

- [ ] **Step 1: Write the walk test**

Create `tests/test_impl_feature_auto_e2e.py`:

```python
"""Offline NODE_PATH walk of the real impl-feature-auto protocol (crafted verdicts).
design → implement → done; and a design `block` → failed (implement never entered)."""
import json, subprocess, pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / ".github/agent-factory/protocols/impl-feature-auto/protocol.json"
NEXT, ADVANCE = ENG / "next.py", ENG / "advance.py"
PID = "impl-feature-auto"
INST = "issue-5"


def _yaml(p): return yaml.safe_load(open(p))


def _reclone(env, tmp_path, tag):
    d = tmp_path / f"rc-{tag}"
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", env["STATE_REMOTE"], str(d)], check=True)
    return d / PID / INST


def _run(script, *args, env, **extra):
    e = dict(env); e.update(extra)
    return subprocess.run(["python3", str(script), *map(str, args)],
                          text=True, capture_output=True, env=e)


def _verdicts(tmp_path, tag, results):
    v = tmp_path / f"v-{tag}.json"; v.write_text(json.dumps({"results": results}))
    ev = tmp_path / f"ev-{tag}.json"; ev.write_text("{}")
    return v, ev


def test_design_pass_then_implement_then_done(engine_env, tmp_path):
    base = dict(engine_env); base["PR_HEAD_SHA"] = "sha1"; base["AGENT_RUN_ID"] = "r1"
    # start → run-agent at design
    r = _run(NEXT, tmp_path / "s", INST, PROTO, "start", "sha1", env=base)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["action"] == "run-agent"

    # advance design with all checks passing → enter implement (re-dispatch)
    passing = [{"check": c, "pass": True, "feedback": "", "on_fail": of} for c, of in [
        ("ledger-wellformed", "iterate"), ("ledger-consistent", "iterate"),
        ("read-these-first-consistent", "iterate"), ("spec-present", "block"),
        ("plan-present", "block")]]
    v, ev = _verdicts(tmp_path, "design", passing)
    r2 = _run(ADVANCE, tmp_path / "a1", INST, PROTO, v, ev, env=base, NODE_PATH="design")
    assert r2.returncode == 0, r2.stderr
    fdir = _reclone(engine_env, tmp_path, "afterdesign")
    assert _yaml(fdir / "design.yaml")["state"] == "done"

    # advance implement passing → done
    v2, ev2 = _verdicts(tmp_path, "impl", [{"check": "implement-schema-valid", "pass": True, "feedback": "", "on_fail": "iterate"}])
    base2 = dict(base); base2["AGENT_RUN_ID"] = "r2"
    r3 = _run(ADVANCE, tmp_path / "a2", INST, PROTO, v2, ev2, env=base2, NODE_PATH="implement")
    assert r3.returncode == 0, r3.stderr
    fdir2 = _reclone(engine_env, tmp_path, "done")
    assert _yaml(fdir2 / "implement.yaml")["state"] == "done"


def test_design_block_fails_without_implement(engine_env, tmp_path):
    base = dict(engine_env); base["PR_HEAD_SHA"] = "sha1"; base["AGENT_RUN_ID"] = "r1"
    _run(NEXT, tmp_path / "s", INST, PROTO, "start", "sha1", env=base)
    # spec-present fails with on_fail=block → run ends failed, implement never entered
    results = [{"check": "spec-present", "pass": False, "feedback": "no spec", "on_fail": "block"}]
    v, ev = _verdicts(tmp_path, "block", results)
    r = _run(ADVANCE, tmp_path / "a", INST, PROTO, v, ev, env=base, NODE_PATH="design")
    assert r.returncode == 0, r.stderr
    fdir = _reclone(engine_env, tmp_path, "blocked")
    # design did not advance to done; implement was never seeded
    assert not (fdir / "implement.yaml").is_file(), "implement must NOT run after a design block"
    assert "event_type=protocol-continue" not in r.stderr  # no entry into implement
```

> Two specifics to confirm against the engine while writing, and adjust the assertions to the real layout (do not change the engine):
> - **State-file paths:** for a multi-node sequence the agent state files are `…/<instance>/design.yaml` and `…/<instance>/implement.yaml` (multi-phase layout), with the cursor in `_instance.yaml`. If `paths.py` places them elsewhere for this shape, read the actual path from the reclone dir and assert on that (mirror how `test_unified_codereview_e2e.py` locates files).
> - **Block semantics:** confirm whether an `on_fail:block` failure on `design` marks `design.yaml` `state: failed`/`blocked` and halts, vs. requiring an `on_blocked` field (this protocol sets none). The load-bearing assertion is **`implement` never runs**; assert that, plus whatever terminal state the engine writes for a block on a node without `on_blocked`. If a block on a node lacking `conclude`/`on_blocked` does NOT halt in this engine, switch the block test to drive `spec-present` failing while exhausting `max_iterations` is not applicable (block never iterates) — instead assert the run does not dispatch `implement`. Keep the test faithful to observed behavior.

- [ ] **Step 2: Run the e2e walk**

Run: `uv run pytest tests/test_impl_feature_auto_e2e.py -v`
Expected: PASS (2). If the block-path terminal state differs from the assertion, adjust per the note above (the engine is the source of truth; do not modify it).

- [ ] **Step 3: Run the whole suite**

Run: `uv run pytest tests/ -q`
Expected: PASS — all new tests green; no regressions in the existing ~459-test suite.

- [ ] **Step 4: Commit**

```bash
git add tests/test_impl_feature_auto_e2e.py
git commit -m "test(impl-feature-auto): offline NODE_PATH e2e walk (design→implement→done; design block)"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
|---|---|
| §4 architecture / file layout | Task 4 (protocol.json), 5–10 (checks+publish), 11–12 (agents) |
| §5 design node + its 5 checks | Task 4 (wiring), 5–8 (checks) |
| §6 implement node (inputs, max_iter 1, publish; check-less caveat) | Task 4 + Task 9 (the one minimal check resolving the `decide([])` finding) |
| §7 post-summary / discard semantics | Task 10 |
| §8.1/8.2/8.3 ledger layers | Task 5 / 6 / 7 |
| §8 "out of scope" substance boundary | Honored — no AI judge; checks are form-only (noted in Tasks 5–7) |
| §9 artifact carrier | Task 11 (bundle+upload) + Task 12 (download by run_id) |
| §10 trigger/route/issue-keying | Task 1 (target field + route), Task 2 (pr_from_instance), Task 3 (YAML) |
| §11 agent workflows + superpowers staging | Task 11, 12 (staging step pinned to v6.0.3, whole skills/ subtree) |
| §11 bootstrap injection | Task 11/12 prompt bodies (inlined using-superpowers) |
| §12 prompt split (Phase 0 vs implement; bare skill names; issue.json) | Task 11, 12 |
| Appendix A evidence schemas | Task 4 |
| §14 testing (fixture walk, per-check units, lint) | Tasks 5–9 (units), 4 (lint), 13 (walk) |
| §13 protocol family naming | N/A (context only) |

**2. Placeholder scan** — every code step contains real, runnable content. The only deferred-to-live items are explicitly flagged §15 open questions (safe-outputs branch naming; `gh aw` availability; the live PR run is out of scope per §14) and the two engine-behavior confirmations in Task 13's note — these are "confirm observed behavior, adjust the test," not "write code later."

**3. Type consistency** — check output object is `{"check","pass","feedback"}` everywhere; publish output `{"conclusion","summary"}`; `lib.pr_from_instance` used in Task 2 (next.py) and Task 10 (post-summary); `_common.RISK` defined in Task 5, consumed in Task 7; evidence field names (`spec_path`, `plan_path`, `ledger`, `read_these_first`, `run_id`, `pr_branch`, `summary`) match the Appendix-A schemas and the agent prompts; the `pr_branch` regex `^impl-feature-auto/issue-[0-9]+$` matches the instance key `issue-<N>` and the agent's `git checkout -b`.

---

## Notes for the implementer

- **Engine edits are confined to Tasks 1–3** and are the additive issue-keying surface the user pre-approved. Do not touch `next.py`/`advance.py`/`paths.py`/`join.py` beyond the `pr_from_instance` swap in Task 2. If a task seems to need a deeper engine change, **stop and surface it** — it likely means an assertion should adapt to the engine, not the reverse.
- **`decide([])` is a failed attempt, not a pass** — this is why `implement` carries one check (Task 9). Do not "simplify" by removing it.
- **Inputs carry evidence JSON, not files** — `implement` gets design's *evidence* (incl. `run_id`) via `aw_context.inputs.design`, and downloads the spec/plan *files* by that run_id (Task 12), mirroring `recover`'s leg→combine pattern.
- After Tasks 11/12, if `gh aw` is unavailable locally, the `.lock.yml` files must still be generated before the workflows can run; note this in the commit and compile where `gh aw` exists. The offline tests (Tasks 1–10, 13) do not need the locks.
