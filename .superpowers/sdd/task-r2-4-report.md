# Task R2-4 Report: `security-gather` (Cedar+Guardians lift)

## Files Created

| File | Description |
|------|-------------|
| `.github/workflows/security-gather-agent.md` | New agent source |
| `.github/workflows/security-gather-agent.lock.yml` | Compiled lock (via `gh aw compile --approve`) |
| `.github/agent-factory/protocols/code-review/security-gather.evidence.schema.json` | Evidence schema |
| `.github/agent-factory/protocols/code-review/checks/security-gather-coverage.py` | Zone-3 form-check (`100755`) |
| `tests/test_security_gather_coverage.py` | 14 tests (TDD — written first) |
| `.superpowers/sdd/task-r2-4-report.md` | This report |

## Lifted Block

From `review-security-agent.md`, copied VERBATIM into `security-gather-agent.md`:
- `actions/setup-python@v5` step (Python 3.11 for Guardians)
- "Run Cedar + Guardians security engines" shell block (runs `run-cedar.js` / `plan-extract.js` / `verify_driver.py` / `emit-engine-report.js` → writes `/tmp/gh-aw/agent/engine-report.json`)
- "Inject engine findings" post-step (`anchor-engine-findings.js`)

`scripts/security/**` paths are unchanged — they already travel with the protocol.

## Verdict Rule (in check and agent prompt)

```
LOCKED_VIOLATION  iff engine_report.violations has any entry with locked:true
n/a               if violations field absent or engines produced only stubs (fail-open)
PASS              otherwise (violations present, none locked:true)
```

## Pytest Summary

```
14 passed in 0.35s
```

Tests cover: locked detected, PASS clean, PASS empty violations, n/a engines-absent,
3 verdict-mismatch cases, 3 missing sub-object cases, engine_report not-object,
invalid verdict enum, evidence not-object, evidence unreadable.

## Check Mode

```
100755 9bb3dd92b7834fcd50fde4c62602cdfc4999f370 0  .../security-gather-coverage.py
```

## Lock Drift

`gh aw compile` touched `cluster-coverage.py` mode (100755→100644) as a side-effect.
Restored with `git checkout --`. Final `git status --short` shows only the 5 new/intended
files (security-gather-agent.md, .lock.yml, schema, check, test). No other lock changed.

## Commit Hash

(see below — committed after this report is written)

---

## FINAL-REVIEW C1 Fix: drop `anchor-engine-findings.js` post-step

### What was removed

From `.github/workflows/security-gather-agent.md`:
- The entire `post-steps[0]` entry named "Anchor engine findings and assemble security-gather evidence"
  (the `node "$SEC/anchor-engine-findings.js" ...` call that rewrote `/tmp/gh-aw/evidence.json`
  in-place, overwriting `verdict` to `REQUEST_CHANGES` on a LOCKED violation — not in the
  `PASS|LOCKED_VIOLATION|n/a` enum).
- Stale mention of `anchor-engine-findings.js` in the agent prompt body.
- Stale "fold violations into anchored findings" comment in the Cedar+Guardians step.

Retained: all engine-running steps (`run-cedar.js` / `plan-extract.js` / `verify_driver.py` /
`emit-engine-report.js`). `engine_report` is still produced and available; `anchor-engine-findings.js`
is left on disk unused.

### Other callers of `anchor-engine-findings.js`

`grep -rn "anchor-engine-findings" --include="*.md" --include="*.yml"` finds:
- `security-gather-agent.lock.yml` — compiled from `.md`; updated by `gh aw compile` to no longer call it.
- `review-security-agent.md` / `.lock.yml` — NOT present (R2-6 already removed it from there).
- Only docs/plans/specs (read-only). No live workflow still invokes the script.

### Tests added (to `tests/test_security_gather_coverage.py`)

Two new regression tests (total now 16):
1. `test_security_gather_agent_no_anchor_engine_findings` — reads `security-gather-agent.md`, asserts
   the string `anchor-engine-findings` is absent. Pins the clobber path as removed.
2. `test_locked_violation_with_locked_true_passes_check` — a `LOCKED_VIOLATION` verdict + `locked:true`
   violation in `engine_report` must PASS `security-gather-coverage`, proving no downstream step forces
   the verdict to `REQUEST_CHANGES`.

### Pytest summaries

Module: `16 passed in 1.54s`
Full suite: `696 passed, 1 warning in 71.15s`

### Check exec bits (`git ls-files -s`)

All three checks still 100755 after `gh aw compile` — no mode flip to restore.

### Commit

`79a0aac` — `fix(security-gather): drop anchor-engine-findings post-step (clobbered the LOCKED_VIOLATION verdict)`
