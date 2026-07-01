# Remove demo protocols (grumpy / multi-grumpy); rename pipeline to `code-review` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Leave the repo shipping exactly one example protocol — `code-review` (renamed from `code-review-pipeline`) — plus the generic engine, with grumpy/multi-grumpy gone as shipped protocols and no loss of engine regression coverage.

**Architecture:** The engine is already protocol-agnostic and the router selects protocols purely by scanning `protocol.json` `triggers`, so removing the two demo dirs from `.github/agent-factory/protocols/` de-routes them with zero workflow changes. The two demo protocols were also the system-under-test for the engine's single-phase regression guards (v1 single-agent, single-phase fan-out + join). To preserve that coverage we **move** them into `tests/fixtures/` (renamed `single-agent` / `fanout-mini`) — exactly the existing `pipeline-mini` fixture pattern — so they survive as generic engine-test fixtures, not as shipped protocols. Tests that exercise paths the real pipeline genuinely has (check-script behavior, publish hooks) are repointed at the shipped `code-review` protocol.

**Tech Stack:** Python 3 + PyYAML (runtime), pytest (dev), gh-aw workflows, JSON protocol definitions.

## Global Constraints

- **Engine stays generic** — never add protocol-specific logic to `.github/agent-factory/engine/`. Only comments there mention grumpy.
- **The agent workflows stay** — `grumpy-agent`, `security-agent`, `preflight-agent` (`.github/workflows/*-agent.md` + `.lock.yml`) are *reused* by `code-review` as its branch legs / preflight agent. Do NOT delete them. The `grumpy`/`security` strings that remain in the repo are branch-*leg* names and the agent persona, not the deleted protocols.
- **Keep the suite green at every commit.** Each task is ordered so no commit leaves a test pointing at a path that no longer exists.
- **State path note:** renaming the pipeline's `protocol.json` `.name` to `code-review` changes the derived state path to `code-review/pr-N/…`. Acceptable for the PoC (a fresh instance starts under the new path; old `agentic-state` history is untouched).
- **Living docs only.** Update `CLAUDE.md`, `README.md`, `docs/HOW-IT-WORKS.md`, `docs/STATUS.md`, `docs/BACKLOG.md`, `docs/EVALUATING-AGENT-OUTPUT.md`. Leave `docs/superpowers/plans/**`, `docs/superpowers/specs/**`, and `docs/demo*-transcript.txt` untouched as a historical archive.
- **Fixture naming:** new fixtures follow the `pipeline-mini` convention — dir name == `protocol.json` `.name`. Branch-leg names inside `fanout-mini` may stay `grumpy`/`security` (they mirror `code-review`'s own legs and the retained `grumpy-agent`), to avoid churning the structural assertions.
- Run the whole suite with `pytest tests/ -q`; a single module with `pytest tests/test_X.py -q`.

---

### Task 1: Rename `code-review-pipeline` → `code-review`

**Files:**
- Move: `.github/agent-factory/protocols/code-review-pipeline/` → `.github/agent-factory/protocols/code-review/`
- Modify: `.github/agent-factory/protocols/code-review/protocol.json` (`.name`)
- Modify: `.github/agent-factory/protocols/code-review/publish/_review.py` (docstring)
- Modify (rename pipeline path/PID constants — `code-review-pipeline` → `code-review`, and any state-path/PID assertion strings): `tests/test_pipeline_status.py`, `tests/test_conclude_preflight.py`, `tests/test_preflight_checks.py`, `tests/test_preflight_coverage.py`, `tests/test_pipeline_check_resolution.py`, `tests/test_gate.py`, `tests/test_override.py`, `tests/test_phase_labels.py` (the `CRP_PROTO` constant only)
- Modify (real-routing assertions expecting the pipeline): `tests/test_route.py`, `tests/test_triggers.py` — change `endswith("code-review-pipeline/protocol.json")` → `endswith("code-review/protocol.json")` and any `"code-review-pipeline"` literal.
- Modify: `tests/test_resolve_agent_unit.py` — only if it asserts the pipeline name literally.

**Interfaces:**
- Produces: the shipped protocol id `code-review`; state path prefix `code-review/`. All later tasks and docs use this name.

- [ ] **Step 1: Move the directory (preserves history)**

```bash
cd /home/gustavo/huawei/agent-factory/poc
git mv .github/agent-factory/protocols/code-review-pipeline .github/agent-factory/protocols/code-review
```

- [ ] **Step 2: Rename the protocol id**

In `.github/agent-factory/protocols/code-review/protocol.json` change the first field:
```json
"name": "code-review",
```
(was `"code-review-pipeline"`).

- [ ] **Step 3: Fix the shared-publish docstring**

In `.github/agent-factory/protocols/code-review/publish/_review.py` line 2, replace `Shared PR-review publication mechanism for multi-grumpy branches.` with `Shared PR-review publication mechanism for code-review review branches.`

- [ ] **Step 4: Repoint the pipeline tests**

In each listed test module, replace the path/PID constant. Example (`tests/test_pipeline_status.py`):
```python
PIPELINE = ROOT / ".github/agent-factory/protocols/code-review/protocol.json"
PID = "code-review"
```
Then grep the module for residual `code-review-pipeline` literals (tree-link assertions like `tree/agentic-state/code-review-pipeline/pr-65`, headline `**code-review-pipeline · pr-65**`) and update them to `code-review`. Leave the `MULTIGRUMPY` constant in `test_pipeline_status.py` for now (Task 3 removes it).

- [ ] **Step 5: Update real-routing assertions**

In `tests/test_route.py` and `tests/test_triggers.py`, update the assertions that resolve the *real* `protocols/` dir for `/review` and PR-opened/reopened to expect `code-review/protocol.json`. Do NOT yet touch assertions about `/grumpy` → `multi-grumpy` (grumpy/mg still live in `protocols/` until Tasks 2–3).

- [ ] **Step 6: Run the affected modules**

Run:
```bash
pytest tests/test_pipeline_status.py tests/test_conclude_preflight.py tests/test_preflight_checks.py tests/test_preflight_coverage.py tests/test_pipeline_check_resolution.py tests/test_gate.py tests/test_override.py tests/test_phase_labels.py tests/test_route.py tests/test_triggers.py -q
```
Expected: PASS. Then run the full suite `pytest tests/ -q` — still green (grumpy/mg untouched).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(protocol): rename code-review-pipeline -> code-review"
```

---

### Task 2: Move `grumpy` → `tests/fixtures/single-agent` (preserve v1 single-agent regression)

**Files:**
- Move: `.github/agent-factory/protocols/grumpy/` → `tests/fixtures/single-agent/`
- Delete: `tests/fixtures/single-agent/README.md` (grumpy persona doc — not needed by tests)
- Modify: `tests/fixtures/single-agent/protocol.json` (`.name`, drop the `/v1-grumpy` trigger)
- Modify (single-agent regression references): `tests/test_engine.py` (the `GRUMPY_PROTO` constant + single-agent state-path / `protocol:` assertion strings), `tests/test_runchecks.py` (the `GRUMPY_PDIR` portion), and the stray single-phase regression cases in `tests/test_phase_relay.py`, `tests/test_multiphase.py`, `tests/test_phase_labels.py` (its `GRUMPY_PROTO`), `tests/test_pipeline_status.py` (the `grumpy` `ensure_status_comment` case)
- Modify (routing): `tests/test_route.py`, `tests/test_triggers.py` — drop/repoint any assertion that `/v1-grumpy` routes to `grumpy-review` now that it leaves `protocols/`.

**Interfaces:**
- Consumes: nothing (additive move).
- Produces: fixture `tests/fixtures/single-agent/` with `protocol.json` `.name == "single-agent"`, a single `review` state of `kind:"agent"` (workflow `grumpy-agent`) → `publish` deterministic state. Used by `test_engine.py` for the BRANCH-unset single-phase path.

- [ ] **Step 1: Move the dir and drop the persona README**

```bash
git mv .github/agent-factory/protocols/grumpy tests/fixtures/single-agent
git rm tests/fixtures/single-agent/README.md
```

- [ ] **Step 2: Rename the fixture protocol and strip its trigger**

In `tests/fixtures/single-agent/protocol.json`: set `"name": "single-agent"` and replace the `triggers` array with `[]` (fixtures are driven directly by tests, not routed).

- [ ] **Step 3: Repoint the single-agent tests**

In `tests/test_engine.py`:
```python
GRUMPY_PROTO = ROOT / "tests/fixtures/single-agent/protocol.json"
```
Run the module, then fix any assertion strings that embedded the old name (state paths `grumpy/pr-…` → `single-agent/pr-…`; `protocol: grumpy-review` → `protocol: single-agent`). Apply the same constant/string fixes in `test_runchecks.py`, `test_phase_relay.py`, `test_multiphase.py`, `test_phase_labels.py`, and the grumpy `ensure_status_comment` case in `test_pipeline_status.py`.

- [ ] **Step 4: Update routing assertions for the departed trigger**

In `tests/test_route.py` / `tests/test_triggers.py`, remove or update any assertion that `/v1-grumpy` (or `grumpy-review`) is routable from the real `protocols/` dir — it no longer is.

- [ ] **Step 5: Run affected modules + full suite**

Run:
```bash
pytest tests/test_engine.py tests/test_runchecks.py tests/test_phase_relay.py tests/test_multiphase.py tests/test_phase_labels.py tests/test_pipeline_status.py tests/test_route.py tests/test_triggers.py -q && pytest tests/ -q
```
Expected: PASS. Confirm nothing references `protocols/grumpy`: `grep -rn "protocols/grumpy" tests/ .github` → no hits.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "test(fixtures): move grumpy demo -> tests/fixtures/single-agent; drop as shipped protocol"
```

---

### Task 3: Move `multi-grumpy` → `tests/fixtures/fanout-mini` (preserve single-phase fan-out + join regression)

**Files:**
- Move: `.github/agent-factory/protocols/multi-grumpy/` → `tests/fixtures/fanout-mini/`
- Delete: `tests/fixtures/fanout-mini/publish/__pycache__/` (stale bytecode)
- Modify: `tests/fixtures/fanout-mini/protocol.json` (`.name`, drop `/grumpy` trigger)
- Modify: `tests/fixtures/fanout-mini/publish/_review.py` (docstring `multi-grumpy` → `fanout-mini`)
- Modify (fan-out/join/status regression references): `tests/test_engine.py` (`MULTI_PROTO`), `tests/test_fanout_e2e.py` (`PROTO` + `multi-grumpy/pr-80` strings + the `check-run multi-grumpy …` expectation), `tests/test_join.py` (`PROTO` + `multi-grumpy` literals), `tests/test_status_comment.py` (`PROTO` + `multi-grumpy/pr-80` tree links + `protocol:"multi-grumpy"`), `tests/test_runchecks.py` (the `MULTI_GRUMPY_PROTO` portion), `tests/test_phase_labels.py` (`MG_PROTO`), `tests/test_pipeline_status.py` (remove the now-dead `MULTIGRUMPY` constant)
- Modify (routing): `tests/test_route.py`, `tests/test_triggers.py` — drop/repoint any `/grumpy` → `multi-grumpy` real-routing assertion.

**Interfaces:**
- Produces: fixture `tests/fixtures/fanout-mini/` with `.name == "fanout-mini"`, a `review` `kind:"fanout"` state (legs `grumpy`+`security`) → `join` `kind:"join"`. Exercises `has_fanout && not is_multiphase` (the "sole fan-out state, cursor absent" path in `join.py`) and `render_fanout_status_body` in `lib.py`.

- [ ] **Step 1: Move the dir and clean bytecode**

```bash
git mv .github/agent-factory/protocols/multi-grumpy tests/fixtures/fanout-mini
rm -rf tests/fixtures/fanout-mini/publish/__pycache__
```

- [ ] **Step 2: Rename + de-trigger the fixture, fix the docstring**

In `tests/fixtures/fanout-mini/protocol.json`: set `"name": "fanout-mini"` and `"triggers": []`. In `tests/fixtures/fanout-mini/publish/_review.py` change the docstring `multi-grumpy branches` → `fanout-mini branches`.

- [ ] **Step 3: Repoint the fan-out / join / status tests**

In `tests/test_engine.py`:
```python
MULTI_PROTO = ROOT / "tests/fixtures/fanout-mini/protocol.json"
```
In `test_fanout_e2e.py`, `test_join.py`, `test_status_comment.py`, `test_runchecks.py`, `test_phase_labels.py`: set the protocol constant to `tests/fixtures/fanout-mini/protocol.json` and replace every `multi-grumpy` literal (state-dir paths `multi-grumpy/pr-80`, tree links `tree/agentic-state/multi-grumpy/pr-80`, `protocol:"multi-grumpy"`, and the `check-run multi-grumpy …` check-name string) with `fanout-mini`. In `test_pipeline_status.py` delete the `MULTIGRUMPY` constant (now unused).

- [ ] **Step 4: Update routing assertions for the departed `/grumpy` trigger**

In `tests/test_route.py` / `tests/test_triggers.py`, remove/update any assertion that `/grumpy` routes to `multi-grumpy` from the real `protocols/` dir.

- [ ] **Step 5: Run affected modules + full suite**

Run:
```bash
pytest tests/test_engine.py tests/test_fanout_e2e.py tests/test_join.py tests/test_status_comment.py tests/test_runchecks.py tests/test_phase_labels.py tests/test_pipeline_status.py tests/test_route.py tests/test_triggers.py -q && pytest tests/ -q
```
Expected: PASS. Confirm `grep -rn "protocols/multi-grumpy" tests/ .github` → no hits, and `ls .github/agent-factory/protocols/` shows only `code-review`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "test(fixtures): move multi-grumpy demo -> tests/fixtures/fanout-mini; drop as shipped protocol"
```

---

### Task 4: Scrub residual grumpy/multi-grumpy names from synthetic test fixtures + workflow comment

**Files:**
- Modify: `tests/test_route.py`, `tests/test_triggers.py`, `tests/test_resolve_agent_unit.py` — rename in-test synthetic labels (e.g. the `_mk_protocols({"multi-grumpy": …})` dict keys and `GRUMPY_TRIGGERS` fixture name) to neutral names like `demo-fanout` / `DEMO_TRIGGERS`. These are tmp-dir fakes; the rename is cosmetic but completes the removal.
- Modify: `.github/workflows/agentic-engine.yml` — the `protocol_path` input `description` example `…/protocols/multi-grumpy/protocol.json` → `…/protocols/code-review/protocol.json`.

**Interfaces:** none (cosmetic + doc-string).

- [ ] **Step 1: Rename synthetic labels**

In `tests/test_route.py`, `tests/test_triggers.py`, `tests/test_resolve_agent_unit.py`, replace the `grumpy`/`multi-grumpy` synthetic-fixture identifiers with neutral names. Keep the `code-review` real-routing assertions from Tasks 1–3 intact.

- [ ] **Step 2: Fix the workflow input example**

Edit the `agentic-engine.yml` `protocol_path` description to reference `code-review`.

- [ ] **Step 3: Verify no stray references remain in living code/tests**

Run:
```bash
grep -rn "multi-grumpy\|grumpy-review\|code-review-pipeline" tests/ .github | grep -v "fixtures/fanout-mini\|fixtures/single-agent"
```
Expected: only legitimate branch-leg / agent-name hits (the `grumpy`/`security` legs in `code-review/protocol.json`, `grumpy-agent`/`security-agent` workflow files). No `multi-grumpy`, `grumpy-review`, or `code-review-pipeline` literals.

- [ ] **Step 4: Run full suite**

Run: `pytest tests/ -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: scrub residual demo-protocol names from synthetic fixtures + workflow comment"
```

---

### Task 5: Update living docs

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `docs/HOW-IT-WORKS.md`, `docs/STATUS.md`, `docs/BACKLOG.md`, `docs/EVALUATING-AGENT-OUTPUT.md`
- Do NOT touch: `docs/superpowers/plans/**`, `docs/superpowers/specs/**`, `docs/demo1-transcript.txt`, `docs/demo2-transcript.txt`.

**Interfaces:** none.

- [ ] **Step 1: CLAUDE.md** — In the "What this is" section, drop the two-bullet grumpy/multi-grumpy example list and present `code-review` (`.github/agent-factory/protocols/code-review/`) as the single example protocol exercising the engine. In the architecture diagram/table, replace `protocols/<name>/` grumpy/multi-grumpy references with `code-review`. Delete or fold the "v2 — fan-out / join (the `BRANCH` seam)" section's framing that calls multi-grumpy the live protocol — keep the BRANCH-seam mechanics description but attribute the fan-out/join phase to `code-review`'s `review` phase. Note that single-phase engine regression now lives in `tests/fixtures/{single-agent,fanout-mini}/`.

- [ ] **Step 2: README.md** — Rewrite the opening (lines ~3–22) so the pitch describes `code-review`: preflight gate → review fan-out (`grumpy` + `security` legs) → join → approval gate. Remove "regression-guard baseline"/"v2 live fan-out" language.

- [ ] **Step 3: docs/HOW-IT-WORKS.md** — Repoint the worked examples (Components 3.1, State model 3.4, Anatomy 4.1) from `grumpy-review/pr-<N>.yaml` to `code-review`. In the big section 8 ("v2 — Fan-out / join"), reframe the worked protocol as `code-review`'s review/join phases; keep the conceptual content. Where single-phase shapes are illustrated, reference the `tests/fixtures/single-agent` / `fanout-mini` fixtures.

- [ ] **Step 4: docs/STATUS.md** — Update the opening framing and the v4 section to name `code-review` as the shipped pipeline. Leave the historical v1/v2 milestone sections as a record, but add a one-line note that the grumpy/multi-grumpy demo protocols were retired into `tests/fixtures/` on 2026-06-20.

- [ ] **Step 5: docs/BACKLOG.md** — Update the live triggers line (`code-review-pipeline = /review …; multi-grumpy …`) to reflect `code-review` as the sole routed protocol; `/grumpy` and `/v1-grumpy` are retired.

- [ ] **Step 6: docs/EVALUATING-AGENT-OUTPUT.md** — Update the single grumpy mention (the "fall back to GitHub MCP tools" cleanup line) to reference the `grumpy`/`security` review agents of `code-review`.

- [ ] **Step 7: Verify docs are internally consistent**

Run:
```bash
grep -rn "multi-grumpy\|grumpy-review\|code-review-pipeline" CLAUDE.md README.md docs/HOW-IT-WORKS.md docs/STATUS.md docs/BACKLOG.md docs/EVALUATING-AGENT-OUTPUT.md
```
Expected: no hits (only intentional `grumpy`-as-leg/agent references may remain, if any).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "docs: retire grumpy/multi-grumpy from living docs; code-review is the sole example protocol"
```

---

## Final verification (run after all tasks)

- [ ] `pytest tests/ -q` — full suite green; no net loss of engine paths (single-agent + fan-out/join coverage now provided by `tests/fixtures/{single-agent,fanout-mini}`).
- [ ] `ls .github/agent-factory/protocols/` → only `code-review`.
- [ ] Router smoke — resolve a PR-opened event against the live `protocols/` dir and confirm it selects `code-review`:
  ```bash
  python3 .github/agent-factory/engine/lib.py route .github/agent-factory/protocols pull_request opened 2>/dev/null || \
  pytest tests/test_route.py -q   # the real-protocol routing tests assert code-review ownership
  ```
- [ ] `grep -rn "multi-grumpy\|grumpy-review\|code-review-pipeline" --exclude-dir=.git --exclude-dir=superpowers .` → hits only inside `docs/superpowers/**` and `docs/demo*-transcript.txt` (the intentional historical archive) and the moved `tests/fixtures/*` internals.
- [ ] Agent workflows intact: `ls .github/workflows/*-agent.md` still lists `grumpy-agent.md`, `security-agent.md`, `preflight-agent.md`.
