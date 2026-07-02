# Preflight Decomposition — Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold `mm-compliance` from a standalone blocking phase into the `preflight` fanout as a 4th leg, moving its block into the `preflight-gate` rollup — Phase B of the decomposition in `docs/superpowers/specs/2026-06-29-preflight-adherence-decomposition-design.md`.

**Architecture:** The existing `mm-compliance-gate` agent becomes the 4th preflight fanout leg (alongside the 3 chain legs). It no longer posts its own advisory comment or runs its own `conclude` — the `preflight-gate` agent renders its verdict as a 4th consolidated cell and `conclude-preflight` adds `mm.verdict == "diverges"` to the block-gaps rollup. The standalone `mm-compliance` phase and `conclude-mm-compliance.py` are deleted; the gate's `next` becomes `overview`, so one `/override` now clears mm-divergence together with the adherence chain.

**Tech Stack:** Python 3 (engine + checks; PyYAML only runtime dep), pytest + `uv run` (dev), gh-aw (`gh aw compile`) for the agent workflows.

## Global Constraints

- **Engine stays generic.** Do NOT touch `.github/agent-factory/engine/`. Phase B is protocol-only + agent-workflow edits; no engine change at all (unlike Phase A, which added `issues: read`).
- **Build on Phase A.** This branch (`feat/preflight-decomposition-phase-b`, base `cd6e34c`) already has the Phase-A fanout (3 chain legs) + `preflight-gate` + 3-leg `conclude-preflight`. Phase B extends that to 4 legs.
- **`mm-compliance` evidence has no `scope`.** Its schema is `{verdict: "compliant"|"diverges", divergences[], examined}`. The gate emits `scope: {}` for the mm cell; the rollup blocks on `verdict == "diverges"` only (no scope flags).
- **conclude ABI** unchanged: `conclude-preflight.py <evidence.json> <instance-key>`; env `BLOCKING`, `CONCLUDE_INPUTS_DIR`, `PUBLISH_TOKEN`, `PR`, `GITHUB_REPOSITORY`, `ENGINE_LOCAL`, `VERDICT_OUT`. Prints `{conclusion,summary,blocked,reasons,warnings}`.
- **gh-aw:** after editing any `*-agent.md`/`mm-compliance-gate.md`, run `gh aw compile` and commit BOTH the `.md` and the regenerated `.lock.yml`. If compile touches unrelated locks, do not commit those.
- **Tests** via `uv run pytest`; TDD order (failing test → red → implement → green → commit). The whole suite (`uv run pytest tests/ -q`, currently 595 passing) must be green at the end of Task 5.
- **Commit messages** end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## File Structure

```
.github/agent-factory/protocols/code-review/
  protocol.json                          # MODIFY: add mm-compliance as the 4th preflight leg; delete the standalone
                                         #   mm-compliance phase; gate inputs/params.legs += mm-compliance; gate.next -> overview
  publish/conclude-preflight.py          # MODIFY: add the mm leg to the rollup + comment + verdict.json (block on verdict=="diverges")
  publish/conclude-mm-compliance.py      # DELETE (absorbed into the gate rollup)
  mm-compliance.evidence.schema.json     # UNCHANGED (reused as the leg's evidence schema)
.github/workflows/
  mm-compliance-gate.md (+ .lock.yml)    # MODIFY: drop the add-comment safe-output + comment body (it's a leg now); recompile
  preflight-gate-agent.md (+ .lock.yml)  # MODIFY: read + render the 4th (mm-compliance) leg; recompile
tests/
  test_conclude_preflight.py             # MODIFY: add the mm leg builder + mm cases (compliant -> clear, diverges -> block)
  test_preflight_wiring.py               # MODIFY: fanout now has 4 legs; gate has 4 inputs
```

## Task order

1. Extend `conclude-preflight` + its harness for the mm leg (TDD; forward-compatible — safe before the wiring).
2. Drop the standalone comment from `mm-compliance-gate.md` (recompile).
3. Extend `preflight-gate-agent.md` to read + render the 4th leg (recompile).
4. Restructure `protocol.json` (fold the leg in, delete the phase, rewire the gate) + retire `conclude-mm-compliance.py`. *(Leaves `test_preflight_wiring` red until Task 5.)*
5. Update `test_preflight_wiring.py` for the 4-leg fanout + whole-suite green gate.

---

### Task 1: Extend conclude-preflight with the mm-compliance leg

**Files:**
- Modify: `.github/agent-factory/protocols/code-review/publish/conclude-preflight.py`
- Test: `tests/test_conclude_preflight.py`

**Interfaces:**
- Consumes: `CONCLUDE_INPUTS_DIR/mm-compliance.json` = `{verdict: "compliant"|"diverges", divergences[], examined}` (no `scope`).
- Produces: `conclude-preflight` blocks when `mm.verdict == "diverges"`; the mm leg appears in the consolidated comment + `verdict.json`.

- [ ] **Step 1: Add the mm-leg builder + cases to the harness (failing test).** In `tests/test_conclude_preflight.py`, add this builder after `_code_leg` (after line ~26):

```python
def _mm_leg(verdict):
    # mm-compliance evidence has NO scope object (verdict compliant|diverges + divergences[] + examined).
    return {"verdict": verdict,
            "divergences": ([] if verdict == "compliant"
                            else [{"decision": "ADR-1", "detail": "contradicts X", "evidence": "f.py:1"}]),
            "examined": ["_mm/socratic/x.adoc", "f.py"]}
```

Add `mm-compliance` to the all-N/A baseline so every existing case keeps mm compliant (no spurious block). Replace `_all_na` (lines ~52-55) with:

```python
def _all_na():
    return {"spec-solves-issue": _spec_leg("n/a", issue_linked=False, spec_present=False),
            "plan-implements-spec": _plan_leg("n/a", code_changed=False, spec_present=False, plan_present=False),
            "code-implements-plan": _code_leg("n/a", code_changed=False, plan_present=False),
            "mm-compliance": _mm_leg("compliant")}
```

Append two cases to the `CASES` list (before its closing `]`):

```python
    ("mm-compliant-clear",
     lambda L: L | {"mm-compliance": _mm_leg("compliant")},
     False, None, None),
    ("mm-diverges-block",
     lambda L: L | {"mm-compliance": _mm_leg("diverges")},
     True, "mental model", None),
```

- [ ] **Step 2: Run the harness — expect the two new cases to FAIL.**

Run: `uv run pytest tests/test_conclude_preflight.py -q`
Expected: `mm-diverges-block` FAILS (current rollup ignores mm, so `blocked` is False / no "mental model" reason); `mm-compliant-clear` passes; the old cases still pass.

- [ ] **Step 3: Add the mm leg to the rollup.** In `conclude-preflight.py`, update `LEGS` (line 37) to include mm:

```python
LEGS = ("spec-solves-issue", "plan-implements-spec", "code-implements-plan", "mm-compliance")
```

Change the `rollup` signature + body (lines 69-98) to accept and judge `mm_leg` — add the mm block reason after the `underplan` reason, keeping warnings unchanged:

```python
def rollup(spec_leg, plan_leg, code_leg, mm_leg):
    """Return (reasons[], warnings[]) for the Phase-B preflight (chain + mm-compliance). Reasons => block."""
    reasons, warnings = [], []

    issue_linked = _flag(spec_leg, "issue_linked")
    spec_present = _flag(spec_leg, "spec_present") or _flag(plan_leg, "spec_present")
    plan_present = _flag(plan_leg, "plan_present") or _flag(code_leg, "plan_present")
    code_changed = _flag(plan_leg, "code_changed") or _flag(code_leg, "code_changed")

    spec_v, plan_v, code_v = _verdict(spec_leg), _verdict(plan_leg), _verdict(code_leg)

    if issue_linked and not spec_present:
        reasons.append("issue is linked but no spec is present")
    if spec_present and spec_v == "does-not-solve":
        reasons.append("the spec does not solve the linked issue")
    if code_changed and not spec_present:
        reasons.append("code changed but no spec is present")
    if code_changed and not plan_present:
        reasons.append("code changed but no plan is present")
    if plan_v == "underspec":
        reasons.append("plan does not implement the spec (underspec)")
    if code_v == "underplan":
        reasons.append("code does not implement the plan (underplan)")
    if _verdict(mm_leg) == "diverges":
        reasons.append("the PR diverges from the stored mental model")

    if plan_v == "overspec":
        warnings.append("plan adds items beyond the spec (overspec)")
    if code_v == "overplan":
        warnings.append("code adds changes beyond the plan (overplan)")

    return reasons, warnings
```

- [ ] **Step 4: Wire the mm leg through `_render_comment` and `main`.** Change `_render_comment` (lines 101-119) to accept `mm_leg` and add its row:

```python
def _render_comment(status, reasons, warnings, spec_leg, plan_leg, code_leg, mm_leg):
    """Build the single consolidated comment body. Agent-supplied summaries are
    concatenated into this string; the whole body is passed to lib.post_pr_comment
    as ONE `gh api -f body=BODY` argument (an argument vector, never shell-interpolated)."""
    icon = "\U0001f6d1" if status == "blocked" else "✅"
    lines = [f"{icon} **Preflight {status}** — issue → spec → plan → code + mental-model", ""]
    rows = [("spec-solves-issue", spec_leg), ("plan-implements-spec", plan_leg),
            ("code-implements-plan", code_leg), ("mm-compliance", mm_leg)]
    lines.append("| leg | verdict |")
    lines.append("|---|---|")
    for name, leg in rows:
        lines.append(f"| {name} | `{_verdict(leg)}` |")
    if reasons:
        lines += ["", "**Blocking:**"] + [f"- {r}" for r in reasons]
    if warnings:
        lines += ["", "**Advisory:**"] + [f"- {w}" for w in warnings]
    if status == "blocked":
        lines += ["", "_Halted — a maintainer `/override` advances past the gate._"]
    return "\n".join(lines)
```

In `main` (lines 122-166), load the mm leg, pass it to `rollup` + `_render_comment`, and add it to the `verdict.json` records. Replace the body of `main` from the leg loads through the comment post:

```python
def main():
    blocking = os.environ.get("BLOCKING", "") == "1"
    spec_leg = _load_leg("spec-solves-issue")
    plan_leg = _load_leg("plan-implements-spec")
    code_leg = _load_leg("code-implements-plan")
    mm_leg = _load_leg("mm-compliance")

    reasons, warnings = rollup(spec_leg, plan_leg, code_leg, mm_leg)
    blocked = bool(blocking or reasons)
    if blocking:
        reasons = reasons + ["engine blocking signal"]
    status = "blocked" if blocked else "clear"

    # verdict.json — custody-shaped payload (folds in the retired publish-verdict role).
    records = []
    for name, leg in (("spec-solves-issue", spec_leg),
                      ("plan-implements-spec", plan_leg),
                      ("code-implements-plan", code_leg),
                      ("mm-compliance", mm_leg)):
        records.append({"type": "leg", "leg": name,
                        "verdict": _verdict(leg), "scope": _scope(leg)})
    records.append({"type": "verdict", "status": status, "blocked": blocked,
                    "blocking": bool(blocking), "reasons": reasons, "warnings": warnings})
    payload = {"records": records}
    inst = sys.argv[2] if len(sys.argv) > 2 else ""
    if inst.startswith("pr-") and inst[3:].isdigit():
        payload["meta"] = {"pr_number": int(inst[3:]),
                           "head_sha": os.environ.get("HEAD_SHA", "")}
    out_path = os.environ.get("VERDICT_OUT", "/tmp/gh-aw/verdict.json")
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError:
        pass

    pr = os.environ.get("PR", "")
    if pr:
        body = _render_comment(status, reasons, warnings, spec_leg, plan_leg, code_leg, mm_leg)
        lib.post_pr_comment(pr, body)

    summary = ("Preflight blocked: " + "; ".join(reasons)) if blocked else "Preflight clear."
    print(json.dumps({"conclusion": "blocked" if blocked else "clear",
                      "summary": summary, "blocked": blocked,
                      "reasons": reasons, "warnings": warnings}))
```

Also update the module docstring (lines 1-26): change "Phase A: the 3-leg ... chain" to "Phase B: the 3-leg chain + mm-compliance", and add `| mm.verdict=='diverges'` to the `block if:` list.

- [ ] **Step 5: Run the harness — expect PASS.**

Run: `uv run pytest tests/test_conclude_preflight.py -q`
Expected: all cases pass, including `mm-compliant-clear` and `mm-diverges-block`, plus the unchanged `test_engine_blocking_forces_block` / `test_verdict_json_shape` / `test_posts_one_comment_engine_local`.

- [ ] **Step 6: Commit.**

```bash
git add .github/agent-factory/protocols/code-review/publish/conclude-preflight.py tests/test_conclude_preflight.py
git commit -m "feat(conclude-preflight): add the mm-compliance leg to the rollup (block on diverges)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Drop the standalone advisory comment from mm-compliance-gate.md

**Files:**
- Modify: `.github/workflows/mm-compliance-gate.md` (+ regenerate `.lock.yml`)

**Interfaces:**
- Produces: the `mm-compliance-gate` agent writes `/tmp/gh-aw/evidence.json` (`{verdict, divergences[], examined}`) and calls `noop` — no PR comment (the preflight gate renders mm status).

- [ ] **Step 1: Remove the `add-comment` safe-output.** In the frontmatter, change:

```yaml
safe-outputs:
  add-comment: { max: 1, hide-older-comments: true }
  noop: {}
```

to:

```yaml
safe-outputs:
  noop: {}
```

- [ ] **Step 2: Remove the comment-posting from the body.** Read the file. In the `## Procedure` section, delete step 5 in its entirety — the line `5. Post EXACTLY ONE advisory comment via add-comment mirroring the verdict:` **and** both fenced markdown templates that follow it (the `### ✅ Mental-Model Compliance — Compliant` block and the `### ⚠️ Mental-Model Compliance — {N} divergence(s)` block, each delimited by `~~~markdown ... ~~~`). The procedure now ends at step 4 (write `/tmp/gh-aw/evidence.json`).

- [ ] **Step 3: Update the `## Rules` section** to drop the comment instructions. Replace the three rule bullets:

```markdown
- ALWAYS write `/tmp/gh-aw/evidence.json` first (even when compliant — `divergences: []`), then post the comment.
- Base every verdict on real evidence from `pr.diff`. Cite file paths. Never invent MM content not in `_mm/`.
- End by calling exactly one safe output (`add-comment`).
```

with:

```markdown
- ALWAYS write `/tmp/gh-aw/evidence.json` (even when compliant — `divergences: []`).
- Base every verdict on real evidence from `pr.diff`. Cite file paths. Never invent MM content not in `_mm/`.
- This is a preflight fanout LEG: write evidence and then call `noop`. Do NOT post a comment — the preflight gate renders the mental-model verdict in the consolidated preflight comment.
```

- [ ] **Step 4: Recompile and sanity-check.**

```bash
gh aw compile
grep -c "add-comment" .github/workflows/mm-compliance-gate.lock.yml
```
Expected: `gh aw compile` succeeds; the grep prints `0` (no `add-comment` survives in the lock). If `gh aw compile` reports a frontmatter error, fix the `.md` and recompile (never hand-edit the lock). If it modifies unrelated locks, do not stage those.

- [ ] **Step 5: Commit.**

```bash
git add .github/workflows/mm-compliance-gate.md .github/workflows/mm-compliance-gate.lock.yml
git commit -m "feat(code-review): mm-compliance becomes a preflight leg — drop its standalone comment

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Extend preflight-gate-agent.md to read + render the 4th (mm) leg

**Files:**
- Modify: `.github/workflows/preflight-gate-agent.md` (+ regenerate `.lock.yml`)

**Interfaces:**
- Consumes: `.inputs.mm-compliance` = `{verdict: "compliant"|"diverges", divergences[], examined}` (NO scope).
- Produces: a 4-cell consolidated evidence; the mm cell is `{leg: "mm-compliance", verdict, scope: {}, summary}`.

- [ ] **Step 1: Update the intro + inputs list.** Change the intro line (line 49) "You read the three preflight chain legs" to "You read the four preflight legs". In the `## Inputs` block (lines 55-60), change "the three leg evidences" to "the four leg evidences" and add a 4th bullet after the `code-implements-plan` line:

```markdown
- `.inputs.mm-compliance` — `{verdict, divergences[], examined}` (mental-model compliance; **no `scope`**). MAY be absent.
```

- [ ] **Step 2: Add the 4th cell to the produced JSON.** In the `## Produce` block, replace the `legs` array (lines 70-74) with the 4-cell version:

```json
  "legs": [
    { "leg": "spec-solves-issue",   "verdict": "<copied from the leg>", "scope": <copied leg scope object>, "summary": "<1-2 sentence render>" },
    { "leg": "plan-implements-spec", "verdict": "<copied>",             "scope": <copied>,                  "summary": "<...>" },
    { "leg": "code-implements-plan", "verdict": "<copied>",             "scope": <copied>,                  "summary": "<...>" },
    { "leg": "mm-compliance",        "verdict": "<copied: compliant|diverges>", "scope": {}, "summary": "<1-2 sentence render of compliance + divergence count>" }
  ],
```

- [ ] **Step 3: Update the Rules for the 4th cell.** In the `Rules:` list (lines 78-86), change "Emit **exactly three** cells" to "Emit **exactly four** cells", and add a bullet:

```markdown
- `mm-compliance` evidence has **no `scope`** — emit `scope: {}` for its cell (copy `verdict` only). Render its `summary` from the verdict + the number of `divergences`.
```

- [ ] **Step 4: Recompile and sanity-check.**

```bash
gh aw compile
grep -c "mm-compliance" .github/workflows/preflight-gate-agent.lock.yml
```
Expected: `gh aw compile` succeeds; the grep prints `>=1` (the mm leg is referenced in the compiled prompt). If unrelated locks change, don't stage them.

- [ ] **Step 5: Commit.**

```bash
git add .github/workflows/preflight-gate-agent.md .github/workflows/preflight-gate-agent.lock.yml
git commit -m "feat(code-review): preflight-gate renders the mm-compliance leg (4th cell)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Restructure protocol.json — mm-compliance becomes a leg; retire conclude-mm-compliance.py

**Files:**
- Modify: `.github/agent-factory/protocols/code-review/protocol.json`
- Delete: `.github/agent-factory/protocols/code-review/publish/conclude-mm-compliance.py`

**Interfaces:**
- Produces: `preflight` fanout has 4 branches incl. `mm-compliance`; `preflight-gate` reads 4 inputs + `params.legs` of 4 + `next: overview`; the standalone `mm-compliance` phase is gone.

**Note:** after this task `tests/test_preflight_wiring.py` will fail (it pins 3 legs) — Task 5 fixes it. The whole suite is intentionally red between Task 4 and Task 5.

- [ ] **Step 1: Add `mm-compliance` as the 4th preflight fanout branch.** In `protocol.json`, in the `preflight` node's `branches` array, after the `code-implements-plan` branch object (it ends at the `}` before the `]` on line 51), add a comma and this branch:

```json
        ,{
          "id": "mm-compliance",
          "workflow": "mm-compliance-gate",
          "evidence": "mm-compliance.evidence.schema.json",
          "max_iterations": 2,
          "params": { "require": ["verdict"] },
          "checks": [
            { "run": "evidence-present", "on_fail": "iterate" }
          ]
        }
```

- [ ] **Step 2: Update the `preflight-gate` node** (lines 55-77): add the mm input, add it to `params.legs`, and change `next` to `overview`. Replace the `inputs`, `params`, and `next` of that node so they read:

```json
      "inputs": [
        { "from": "spec-solves-issue",    "as": "spec-solves-issue" },
        { "from": "plan-implements-spec", "as": "plan-implements-spec" },
        { "from": "code-implements-plan", "as": "code-implements-plan" },
        { "from": "mm-compliance",        "as": "mm-compliance" }
      ],
      "params": { "legs": ["spec-solves-issue", "plan-implements-spec", "code-implements-plan", "mm-compliance"] },
```
and change the gate's last line from `"next": "mm-compliance"` to `"next": "overview"`. Leave its `checks` (`preflight-gate-coverage` + the 3 advisory) and `conclude`/`on_blocked` unchanged.

- [ ] **Step 3: Delete the standalone `mm-compliance` phase node** (the object `{ "id": "mm-compliance", "kind": "agent", ... "next": "overview" }`, lines 78-92, between `preflight-gate` and `overview`). Remove it entirely (including its trailing comma) so `preflight-gate` is immediately followed by `overview` in the `states` array.

- [ ] **Step 4: Lint the protocol.**

```bash
python3 .github/agent-factory/engine/protocol-lint.py .github/agent-factory/protocols/code-review/protocol.json
```
Expected: `OK: code-review is a valid protocol.` + a tree where `preflight [fanout]` has 4 legs (incl. `mm-compliance`), `preflight-gate [agent] → overview` with 4 `inputs`, and no standalone `mm-compliance` phase. Exit 0. (Semantic-only is fine if `jsonschema` is absent.)

- [ ] **Step 5: Retire `conclude-mm-compliance.py`** (its only consumer — the standalone phase's `conclude` — is gone; grep confirms no other live reference):

```bash
grep -rn "conclude-mm-compliance" .github/agent-factory .github/workflows tests/ | grep -v "\.lock\.yml" || echo "no live references"
git rm .github/agent-factory/protocols/code-review/publish/conclude-mm-compliance.py
```
Expected: `no live references`, then the file is removed.

- [ ] **Step 6: Commit.**

```bash
git add .github/agent-factory/protocols/code-review/protocol.json
git commit -m "feat(code-review): fold mm-compliance into the preflight fanout; retire its phase + conclude

mm-compliance is now the 4th preflight leg; its block moves into conclude-preflight
(verdict==diverges). preflight-gate reads 4 inputs/legs and advances to overview.
conclude-mm-compliance.py retired (absorbed into the gate rollup).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Update test_preflight_wiring.py for the 4-leg fanout + whole-suite green gate

**Files:**
- Modify: `tests/test_preflight_wiring.py`

**Interfaces:**
- Consumes: the post-Task-4 protocol.json.
- Produces: the wiring regression guard reflects 4 preflight legs + 4 gate inputs.

- [ ] **Step 1: Update the fanout-legs assertion.** In `test_preflight_is_a_fanout`, change the expected legs list to include `mm-compliance`:

```python
    assert legs == ["spec-solves-issue", "plan-implements-spec", "code-implements-plan", "mm-compliance"]
```

- [ ] **Step 2: Update the gate-inputs resolution loop.** In `test_preflight_gate_inputs_resolve_to_each_leg_evidence`, change the loop's leg tuple to include `mm-compliance` so all four gate inputs are checked:

```python
    for leg in ("spec-solves-issue", "plan-implements-spec", "code-implements-plan", "mm-compliance"):
```
(The body — `lib.output_artifact_path(... path=lib.state_path(proto, ["preflight", leg]) ...)` and the `endswith(f"/preflight.{leg}.evidence.json")` assertion — already generalizes to 4 legs; no other change.)

- [ ] **Step 3: Run the wiring test — expect PASS.**

Run: `uv run pytest tests/test_preflight_wiring.py -v`
Expected: `3 passed` (now over the 4-leg shape). The `test_mrp_preflight_input_resolves_to_the_gate` assertion is unchanged and still passes (mrp still reads `preflight-gate`).

- [ ] **Step 4: Whole-suite green gate.**

Run: `uv run pytest tests/ -q`
Expected: green (595, possibly +2 from the new conclude cases → 597). If any other module references the old standalone `mm-compliance` phase or `conclude-mm-compliance`, it surfaces here — fix forward. (Pre-flight grep found only a docstring mention in `test_mm_pipeline_wiring.py`, which has no assertion on it.)

- [ ] **Step 5: Commit.**

```bash
git add tests/test_preflight_wiring.py
git commit -m "test(code-review): pin the 4-leg preflight fanout (mm-compliance folded in)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
