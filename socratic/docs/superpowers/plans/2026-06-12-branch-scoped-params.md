# Branch-Scoped Check Params Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the protocol-global `categories` array with a generic, node-scoped `params` object that the engine resolves and forwards to each check via a `CHECK_PARAMS` env var — so config lives on the node that owns the checks (the state in v1, the branch in v2), and checks stop reaching into `protocol.json`.

**Architecture:** `run-checks.sh` already selects the check-owning node (state when `BRANCH` is unset, branch when set). We make it also read that node's `params` object and export it as `CHECK_PARAMS` for every check it runs. The two checks that need the rubric (`schema-valid`, `rubric-coverage`) read `categories` from `CHECK_PARAMS` instead of self-locating `protocol.json`. The engine stays protocol-agnostic — it forwards an opaque blob and never knows the word "categories." This also buys a deterministic precision win: the `security` branch gets `params.categories: ["security"]`, so `schema-valid` will now *reject* a security leg that emits a non-security verdict (previously only a prompt sentence forbade it).

**Tech Stack:** Bash + `jq` (engine + bash check), Python 3 (python check), standalone bash test suites under `tests/`.

**Key invariants (do not break):**
- The check ABI signature stays byte-stable: `<check> <evidence.json> <diff.txt> <changed-files.txt>`. `CHECK_PARAMS` is delivered as an **environment variable**, not a 4th positional arg.
- `CHECK_PARAMS` is **sole-source**: checks no longer fall back to reading `protocol.json`. A check that needs categories and receives no params emits a clear failing verdict (exit 0), never a crash.
- v1 (`protocols/grumpy/`) behavior stays identical: its `params` lives on the `review` state and carries the same five categories.
- Only two checks (`schema-valid`, `rubric-coverage`) read categories. The engine (`next.sh`, `advance.sh`, `lib.sh`), publish hooks, and the agent prompt do **not** — the agent prompt hardcodes its own category list (`.github/workflows/grumpy-agent.md:68`) and is unaffected.
- `traces-exist-in-diff` does **not** read categories and must remain untouched.

**Files touched:**
- `.github/engine/run-checks.sh` — resolve node `params`, export `CHECK_PARAMS` per check.
- `protocols/grumpy/checks/schema-valid.sh` and `protocols/multi-grumpy/checks/schema-valid.sh` — read categories from `CHECK_PARAMS` (two identical copies).
- `protocols/grumpy/checks/rubric-coverage.py` and `protocols/multi-grumpy/checks/rubric-coverage.py` — same (two identical copies).
- `protocols/grumpy/protocol.json` — `categories` → `params.categories` on the `review` state.
- `protocols/multi-grumpy/protocol.json` — `categories` → per-branch `params.categories` (`grumpy`: all five; `security`: `["security"]`).
- `protocols/multi-grumpy/security.evidence.schema.json` — mirror the precision win (enum → `["security"]`, fix stale title).
- `tests/test-runchecks.sh` — params-forwarding test + security precision-win assertion.
- `tests/test-checks.sh` — export `CHECK_PARAMS` for direct check calls + a missing-params guard test.
- Docs: `CLAUDE.md`, `docs/STATUS.md`, `docs/HOW-IT-WORKS.md`, `docs/BACKLOG.md`, `protocols/grumpy/README.md`.

**Run all suites at any checkpoint with:**
```bash
for t in tests/test-*.sh; do echo "== $t =="; bash "$t" || break; done
```

---

### Task 1: Engine forwards node-scoped `CHECK_PARAMS`

Additive and non-breaking: real checks still read `protocol.json` at this point and ignore the new env var. We prove the forwarding with a sandbox stub check.

**Files:**
- Modify: `.github/engine/run-checks.sh`
- Test: `tests/test-runchecks.sh`

- [ ] **Step 1: Write the failing test**

Append to `tests/test-runchecks.sh`, immediately before the final `echo "-----"` block (currently around line 72):

```bash
# --- params forwarding: CHECK_PARAMS reflects the check-owning node ---
# State-scoped params (BRANCH unset) and branch-scoped params (BRANCH set) are
# each forwarded verbatim. A stub check echoes CHECK_PARAMS back as its feedback.
SBX2=$(mktemp -d); mkdir -p "$SBX2/checks"
cat > "$SBX2/protocol.json" <<'JSON'
{"name":"p","states":[
  {"id":"s","params":{"categories":["a","b"]},"checks":[{"run":"echo-params"}],
   "branches":[{"id":"bx","params":{"categories":["only-b"]},"checks":[{"run":"echo-params"}]}]}
]}
JSON
cat > "$SBX2/checks/echo-params.sh" <<'SH'
#!/usr/bin/env bash
jq -nc --arg f "${CHECK_PARAMS:-MISSING}" '{check:"echo-params",pass:true,feedback:$f}'
SH
chmod +x "$SBX2/checks/echo-params.sh"

OUT=$("$RC" "$SBX2/protocol.json" s "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "params: state-scoped forwarded" 'jq -e ".results[0].feedback|fromjson|.categories==[\"a\",\"b\"]" <<<"$OUT" >/dev/null'
OUT=$(BRANCH=bx "$RC" "$SBX2/protocol.json" s "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "params: branch-scoped overrides state" 'jq -e ".results[0].feedback|fromjson|.categories==[\"only-b\"]" <<<"$OUT" >/dev/null'
rm -rf "$SBX2"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash tests/test-runchecks.sh`
Expected: the two new lines report `FAIL: params: state-scoped forwarded` and `FAIL: params: branch-scoped overrides state` — because `run-checks.sh` does not yet set `CHECK_PARAMS`, so the stub echoes `MISSING` and `fromjson` errors out. Final line shows a non-zero `failed` count and the script exits non-zero.

- [ ] **Step 3: Implement params resolution in `run-checks.sh`**

In `.github/engine/run-checks.sh`, after the line `PDIR="$(cd "$(dirname "$PROTO")" && pwd)"` (line 23), insert:

```bash

# Resolve the params object for the check-owning node (the branch when BRANCH is
# set, otherwise the state) and forward it to every check as CHECK_PARAMS. Checks
# read their scoped config (e.g. the rubric categories) from this blob instead of
# reaching into protocol.json — the runner never interprets its contents.
if [ -n "${BRANCH:-}" ]; then
  PARAMS=$(jq -c --arg s "$STATE" --arg b "$BRANCH" \
    '.states[] | select(.id==$s) | .branches[]? | select(.id==$b) | .params // {}' "$PROTO")
else
  PARAMS=$(jq -c --arg s "$STATE" '.states[] | select(.id==$s) | .params // {}' "$PROTO")
fi
```

Then change the check invocation line (currently line 47):

```bash
      out=$("$path" "$EV" "$DIFF" "$FILES" 2>/dev/null) && rc=0 || rc=$?
```

to prefix the env var:

```bash
      out=$(CHECK_PARAMS="$PARAMS" "$path" "$EV" "$DIFF" "$FILES" 2>/dev/null) && rc=0 || rc=$?
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash tests/test-runchecks.sh`
Expected: both new lines now `ok: params: ...`; final line `run-checks tests: N passed, 0 failed`; exit 0.

- [ ] **Step 5: Confirm no regressions across suites**

Run: `for t in tests/test-*.sh; do echo "== $t =="; bash "$t" || break; done`
Expected: every suite ends `0 failed`. (Real checks still read `protocol.json`; the new env var is harmless to them.)

- [ ] **Step 6: Commit**

```bash
git add .github/engine/run-checks.sh tests/test-runchecks.sh
git commit -m "engine: forward node-scoped params to checks via CHECK_PARAMS"
```

---

### Task 2: Add `params` to protocols and pre-position the test harness

Additive: top-level `categories` stays in place (still the live source for the unmigrated checks), and we add `params` alongside it plus the `CHECK_PARAMS` export the direct-call test will need. Nothing reads `params` yet except the forwarding from Task 1, so all suites stay green.

**Files:**
- Modify: `protocols/grumpy/protocol.json`
- Modify: `protocols/multi-grumpy/protocol.json`
- Modify: `tests/test-checks.sh`

- [ ] **Step 1: Add `params` to the v1 grumpy `review` state**

In `protocols/grumpy/protocol.json`, add a `params` line to the `review` state, keeping the existing top-level `categories` for now. The state becomes:

```json
    {
      "id": "review",
      "kind": "agent",
      "workflow": "grumpy-agent",
      "evidence": "evidence.schema.json",
      "max_iterations": 3,
      "params": { "categories": ["naming", "error-handling", "performance", "duplication", "security"] },
      "checks": [
        { "run": "schema-valid", "on_fail": "iterate" },
        { "run": "rubric-coverage", "on_fail": "iterate" },
        { "run": "traces-exist-in-diff", "on_fail": "iterate" }
      ],
      "next": "publish"
    },
```

- [ ] **Step 2: Add `params` to each v2 multi-grumpy branch**

In `protocols/multi-grumpy/protocol.json`, add a `params` line to each branch, keeping top-level `categories` for now. The `grumpy` branch gets all five; the `security` branch gets only `security`:

```json
        {
          "id": "grumpy",
          "workflow": "grumpy-agent",
          "evidence": "grumpy.evidence.schema.json",
          "max_iterations": 3,
          "params": { "categories": ["naming", "error-handling", "performance", "duplication", "security"] },
          "checks": [
            { "run": "schema-valid",        "on_fail": "iterate" },
            { "run": "rubric-coverage",      "on_fail": "iterate" },
            { "run": "traces-exist-in-diff", "on_fail": "iterate" }
          ],
          "publish": "publish-grumpy"
        },
        {
          "id": "security",
          "workflow": "security-agent",
          "evidence": "security.evidence.schema.json",
          "max_iterations": 3,
          "params": { "categories": ["security"] },
          "checks": [
            { "run": "schema-valid",        "on_fail": "iterate" },
            { "run": "traces-exist-in-diff", "on_fail": "iterate" }
          ],
          "publish": "publish-security"
        }
```

- [ ] **Step 3: Verify both protocol files are valid JSON**

Run: `jq -e . protocols/grumpy/protocol.json >/dev/null && jq -e . protocols/multi-grumpy/protocol.json >/dev/null && echo OK`
Expected: `OK`

- [ ] **Step 4: Export `CHECK_PARAMS` in the direct-call test harness**

In `tests/test-checks.sh`, after the header lines (after `PASS=0; FAIL=0` on line 5), insert the export. `test-checks.sh` invokes the grumpy checks directly, so it must supply the params the engine would otherwise forward. All its direct cases use the full five-category vocabulary:

```bash

# These checks read their rubric from CHECK_PARAMS (the engine forwards it in
# production). Direct invocations here must supply it; the grumpy rubric is the
# full five-category set. Individual cases override this inline where needed.
export CHECK_PARAMS='{"categories":["naming","error-handling","performance","duplication","security"]}'
```

- [ ] **Step 5: Confirm all suites still green (additive, nothing reads `params` yet)**

Run: `for t in tests/test-*.sh; do echo "== $t =="; bash "$t" || break; done`
Expected: every suite ends `0 failed`. (Checks still read top-level `categories`; the exported `CHECK_PARAMS` is ignored by them until Tasks 3–4.)

- [ ] **Step 6: Commit**

```bash
git add protocols/grumpy/protocol.json protocols/multi-grumpy/protocol.json tests/test-checks.sh
git commit -m "protocols: add node-scoped params.categories (transitional, alongside top-level)"
```

---

### Task 3: Migrate `schema-valid` to `CHECK_PARAMS` and lock in the security precision win

There are **two identical copies** of `schema-valid.sh` (`protocols/grumpy/checks/` and `protocols/multi-grumpy/checks/`). Edit both identically. The new failing test asserts the precision win: the `security` branch must reject a non-security category.

**Files:**
- Modify: `protocols/grumpy/checks/schema-valid.sh`
- Modify: `protocols/multi-grumpy/checks/schema-valid.sh`
- Test: `tests/test-runchecks.sh`

- [ ] **Step 1: Write the failing test**

In `tests/test-runchecks.sh`, find the existing branch-aware block (the `BRANCH=security` case ending around line 70) and append this assertion right after it:

```bash
# security branch's params.categories is ["security"], so schema-valid must reject
# evidence that carries any other category (the deterministic precision win that
# replaces a prompt-only rule). evidence-complete.json contains naming/perf/etc.
OUT=$(BRANCH=security "$RC" "$MG" review "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "branch security → schema-valid rejects non-security category" \
  '[ "$(jq -r ".results[]|select(.check==\"schema-valid\")|.pass" <<<"$OUT")" = false ] && jq -r ".results[]|select(.check==\"schema-valid\")|.feedback" <<<"$OUT" | grep -q "illegal category"'
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash tests/test-runchecks.sh`
Expected: `FAIL: branch security → schema-valid rejects non-security category`. At this point `schema-valid` still reads top-level `categories` (all five), so `naming` is legal and the check passes — the opposite of what we assert.

- [ ] **Step 3: Edit `protocols/grumpy/checks/schema-valid.sh`**

Remove the `protocol.json` self-location and read categories from `CHECK_PARAMS`. Change line 6 from:

```bash
PROTO="$(cd "$(dirname "$0")/.." && pwd)/protocol.json"
```

to (delete the `PROTO=` line entirely — `EV="$1"` on line 5 stays). Then replace line 20:

```bash
CATS_JSON=$(jq -c '.categories' "$PROTO")
```

with:

```bash
CATS_JSON=$(printf '%s' "${CHECK_PARAMS:-}" | jq -c '.categories // empty' 2>/dev/null || true)
if [ -z "$CATS_JSON" ]; then
  emit false "schema-valid: no categories in CHECK_PARAMS (engine must pass params.categories for this check's node)"; exit 0
fi
```

- [ ] **Step 4: Apply the identical edit to `protocols/multi-grumpy/checks/schema-valid.sh`**

Make the exact same two changes (delete the `PROTO=` line; replace the `CATS_JSON=` line with the `CHECK_PARAMS` version above) in `protocols/multi-grumpy/checks/schema-valid.sh`.

- [ ] **Step 5: Verify the two copies are still identical**

Run: `diff protocols/grumpy/checks/schema-valid.sh protocols/multi-grumpy/checks/schema-valid.sh && echo IDENTICAL`
Expected: `IDENTICAL` (no diff output).

- [ ] **Step 6: Run the precision-win test to verify it passes**

Run: `bash tests/test-runchecks.sh`
Expected: `ok: branch security → schema-valid rejects non-security category`; final line `0 failed`; exit 0. (The `grumpy` branch and v1 still pass: the engine forwards their five-category `params`.)

- [ ] **Step 7: Confirm the direct-call suite still green**

Run: `bash tests/test-checks.sh`
Expected: `0 failed`. `schema-valid` now reads the `CHECK_PARAMS` exported in Task 2 (the five-category set), so the `illegal category` / `vibes` case and all others behave as before.

- [ ] **Step 8: Commit**

```bash
git add protocols/grumpy/checks/schema-valid.sh protocols/multi-grumpy/checks/schema-valid.sh tests/test-runchecks.sh
git commit -m "checks: schema-valid reads categories from CHECK_PARAMS; security leg now rejects non-security verdicts"
```

---

### Task 4: Migrate `rubric-coverage` to `CHECK_PARAMS`

Two identical copies again. Behavior under the five-category vocabulary is unchanged; we add a guard test for the missing-params case to drive the migration.

**Files:**
- Modify: `protocols/grumpy/checks/rubric-coverage.py`
- Modify: `protocols/multi-grumpy/checks/rubric-coverage.py`
- Test: `tests/test-checks.sh`

- [ ] **Step 1: Write the failing test**

In `tests/test-checks.sh`, after the existing `rubric-coverage.py` assertions (after the no-trailing-newline case around line 59), add an inline guard test (it overrides the exported `CHECK_PARAMS` for this one call):

```bash
# rubric-coverage with empty CHECK_PARAMS → clean failing verdict, not a crash:
out=$(CHECK_PARAMS='' protocols/grumpy/checks/rubric-coverage.py "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt"); rc=$?
if [ "$rc" = "0" ] && [ "$(jq -r .pass <<<"$out")" = "false" ] && [[ "$(jq -r .feedback <<<"$out")" == *"no categories"* ]]; then
  PASS=$((PASS+1)); echo "ok: rubric-coverage handles missing CHECK_PARAMS"
else
  FAIL=$((FAIL+1)); echo "FAIL: rubric-coverage missing-params rc=$rc out=$out"
fi
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash tests/test-checks.sh`
Expected: `FAIL: rubric-coverage missing-params ...`. The current code ignores the env and reads `protocol.json`, so with complete evidence it returns `pass=true` — not the `pass=false` + `no categories` we assert.

- [ ] **Step 3: Edit `protocols/grumpy/checks/rubric-coverage.py`**

Replace the `protocol.json` lookup (lines 19–23):

```python
    proto = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "protocol.json"
    )
    with open(proto) as fh:
        categories = json.load(fh)["categories"]
```

with reading from `CHECK_PARAMS`:

```python
    try:
        categories = json.loads(os.environ.get("CHECK_PARAMS", "")).get("categories")
    except (ValueError, AttributeError):
        categories = None
    if not categories:
        print(json.dumps({
            "check": "rubric-coverage",
            "pass": False,
            "feedback": "rubric-coverage: no categories in CHECK_PARAMS "
                        "(engine must pass params.categories for this check's node)",
        }))
        return
```

Also update the docstring sentence (lines 9–10) from `Categories come from the sibling\nprotocol.json, never hardcoded.` to:

```python
.js; the diff (arg 2) is unused here. Categories come from CHECK_PARAMS
(engine-resolved, scoped to this check's node), never hardcoded.
```

(Keep `import os` — it is still used for `os.environ`.)

- [ ] **Step 4: Apply the identical edit to `protocols/multi-grumpy/checks/rubric-coverage.py`**

Make the same two changes in `protocols/multi-grumpy/checks/rubric-coverage.py`.

- [ ] **Step 5: Verify the two copies are still identical**

Run: `diff protocols/grumpy/checks/rubric-coverage.py protocols/multi-grumpy/checks/rubric-coverage.py && echo IDENTICAL`
Expected: `IDENTICAL`.

- [ ] **Step 6: Run the test to verify it passes**

Run: `bash tests/test-checks.sh`
Expected: `ok: rubric-coverage handles missing CHECK_PARAMS`; all prior `rubric-coverage.py` cases still `ok` (they run under the exported five-category `CHECK_PARAMS`); final line `0 failed`.

- [ ] **Step 7: Confirm the runner suite still green**

Run: `bash tests/test-runchecks.sh`
Expected: `0 failed`. The `grumpy` branch's `rubric-coverage` reads the forwarded five-category `params`.

- [ ] **Step 8: Commit**

```bash
git add protocols/grumpy/checks/rubric-coverage.py protocols/multi-grumpy/checks/rubric-coverage.py tests/test-checks.sh
git commit -m "checks: rubric-coverage reads categories from CHECK_PARAMS"
```

---

### Task 5: Remove the now-dead top-level `categories`

Both checks now read `CHECK_PARAMS`; nothing reads top-level `categories`. Remove it from both protocols.

**Files:**
- Modify: `protocols/grumpy/protocol.json`
- Modify: `protocols/multi-grumpy/protocol.json`

- [ ] **Step 1: Prove nothing reads top-level `.categories` anymore**

Run:
```bash
grep -rn '\.categories\b' .github protocols tests | grep -v 'params' | grep -v '\.evidence\.schema\.json'
```
Expected: no lines referencing a top-level `.categories` read (the only matches, if any, are inside `params` objects or the agent/test fixtures). If a real reader appears, stop and migrate it before deleting.

- [ ] **Step 2: Delete the top-level `categories` line from `protocols/grumpy/protocol.json`**

Remove line 4: `"categories": ["naming", "error-handling", "performance", "duplication", "security"],`. The file now goes straight from `"version": "0.1.0",` to `"states": [`.

- [ ] **Step 3: Delete the top-level `categories` line from `protocols/multi-grumpy/protocol.json`**

Remove the identical top-level `"categories": [...]` line (line 4).

- [ ] **Step 4: Verify both files are valid JSON**

Run: `jq -e . protocols/grumpy/protocol.json >/dev/null && jq -e . protocols/multi-grumpy/protocol.json >/dev/null && echo OK`
Expected: `OK`

- [ ] **Step 5: Run all suites**

Run: `for t in tests/test-*.sh; do echo "== $t =="; bash "$t" || break; done`
Expected: every suite ends `0 failed`.

- [ ] **Step 6: Commit**

```bash
git add protocols/grumpy/protocol.json protocols/multi-grumpy/protocol.json
git commit -m "protocols: drop dead top-level categories (now sourced from node params)"
```

---

### Task 6: Mirror the precision win in the security evidence contract

The `.evidence.schema.json` files are the agent-facing contract (handed to the agent), not runtime-validated — so this is a documentation/contract change, with no test. Narrow the `security` evidence schema's category enum and fix its stale title.

**Files:**
- Modify: `protocols/multi-grumpy/security.evidence.schema.json`

- [ ] **Step 1: Narrow the category enum**

In `protocols/multi-grumpy/security.evidence.schema.json`, change line 20 from:

```json
                "category": { "enum": ["naming", "error-handling", "performance", "duplication", "security"] },
```

to:

```json
                "category": { "enum": ["security"] },
```

- [ ] **Step 2: Fix the stale title**

Change line 3 from `"title": "Grumpy review evidence",` to `"title": "Security review evidence",`.

- [ ] **Step 3: Verify valid JSON**

Run: `jq -e . protocols/multi-grumpy/security.evidence.schema.json >/dev/null && echo OK`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add protocols/multi-grumpy/security.evidence.schema.json
git commit -m "multi-grumpy: narrow security evidence enum to [security], fix stale title"
```

---

### Task 7: Update the docs

The check ABI documentation currently says checks read the rubric from `protocol.json`. That is no longer true — they read scoped config from `CHECK_PARAMS`.

**Files:**
- Modify: `CLAUDE.md`
- Modify (as needed): `docs/STATUS.md`, `docs/HOW-IT-WORKS.md`, `docs/BACKLOG.md`, `protocols/grumpy/README.md`

- [ ] **Step 1: Update the check ABI line in `CLAUDE.md`**

In `CLAUDE.md`, change the sentence at line 102 from:

```
bash wrapper). Read the rubric from `protocol.json`, never hardcode it.
```

to:

```
bash wrapper). A check reads its node-scoped config (e.g. the rubric
`categories`) from the `CHECK_PARAMS` env var the runner forwards — the value of
the check-owning node's `params` object (the branch's when `BRANCH` is set, else
the state's). Never hardcode the rubric and never reach into `protocol.json`.
```

- [ ] **Step 2: Surface the remaining doc references to categories/`protocol.json` rubric**

Run:
```bash
grep -rni 'categories\|rubric from\|reads the rubric\|from .protocol.json' CLAUDE.md docs/STATUS.md docs/HOW-IT-WORKS.md docs/BACKLOG.md protocols/grumpy/README.md
```
For each hit that describes *where checks get categories* or *protocol-global `categories`*, update it to describe the node-scoped `params` / `CHECK_PARAMS` model. Leave hits that merely name the review dimensions (e.g. listing "naming, security, …" as example categories) unchanged. If `docs/STATUS.md` documents engine/protocol seams, add a one-line note that config reaches checks via `params` → `CHECK_PARAMS`, scoped per node.

- [ ] **Step 3: Verify no stale "rubric from protocol.json" phrasing remains**

Run:
```bash
grep -rni 'rubric from .protocol.json\|categories.*from.*protocol.json' CLAUDE.md docs/ protocols/grumpy/README.md || echo "clean"
```
Expected: `clean`.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/ protocols/grumpy/README.md
git commit -m "docs: check rubric now sourced from node-scoped params via CHECK_PARAMS"
```

---

### Task 8: Final full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run every suite end to end**

Run:
```bash
for t in tests/test-*.sh; do echo "== $t =="; bash "$t" || { echo "SUITE FAILED: $t"; break; }; done
```
Expected: each suite prints `0 failed`; no `SUITE FAILED` line. Note `tests/test-join.sh` is not executable — `bash tests/test-join.sh` (the loop already invokes via `bash`) handles it.

- [ ] **Step 2: Final sanity grep — no surviving top-level categories reader**

Run:
```bash
grep -rn '"categories"' protocols/*/protocol.json
```
Expected: every match is inside a `params` object (i.e. on the same line as `"params"`), none at top level.

---

## Self-Review

**Spec coverage:**
- Decision "env, not 4th arg" → Task 1 (Step 3 uses `CHECK_PARAMS="$PARAMS"` prefix; ABI signature unchanged). ✓
- Decision "sole-source, no protocol.json fallback" → Tasks 3 & 4 delete the self-location and add a clear missing-params failing verdict; Task 5 removes the old source. ✓
- Decision "take the precision win" → `security` gets `params.categories: ["security"]` (Task 2), `schema-valid` enforces it (Task 3 test), evidence enum mirrors it (Task 6). ✓
- "branch-intrinsic → lives on the node that owns the checks" → v1 params on the `review` state, v2 params per branch; engine selects the same node it already selects for checks (Task 1). ✓
- Generic `params` object (not a hardcoded `categories` field) → the engine forwards an opaque blob; `categories` is just one key (Task 1 comment + sandbox test uses arbitrary keys). ✓

**Placeholder scan:** every code step shows the exact text to insert/replace and the file/line anchor; every run step states the expected output. Task 7 Step 2 is intentionally discovery-driven (doc prose varies) but bounded by an exact grep and an explicit rule for what to change vs. leave. No TBD/TODO/"handle edge cases" placeholders.

**Type/identifier consistency:** the env var is `CHECK_PARAMS` everywhere; the JSON key is `params` with sub-key `categories` everywhere; the bash check reads `${CHECK_PARAMS:-}` and the python check reads `os.environ.get("CHECK_PARAMS", "")` — both guard the empty/absent case with the same "no categories in CHECK_PARAMS" feedback substring asserted by the tests. The two copies of each check are kept identical and verified with `diff` (Tasks 3.5, 4.5).

**Ordering safety (green at every commit):** Task 1 is additive (real checks ignore the env). Task 2 is additive (top-level `categories` still live; `params` and the test export are inert). Tasks 3–4 flip each check's source while both `params` (Task 2) and forwarding (Task 1) are already in place, and the harness export (Task 2) keeps direct calls green. Task 5 removes the dead source only after a grep proves no readers. Tasks 6–8 are contract/docs/verification.
