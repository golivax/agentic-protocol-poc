# Agent-Factory House-Cleaning: Relocation + Full Python Port

**Date:** 2026-06-12
**Status:** Approved (design) — pending spec review before planning

## Context

This PoC is an agentic protocol engine: a generic bash state machine that drives
gh-aw agents through evidence-schema'd protocols with deterministic checks. It is
being prepared for adoption by a **client project** that will vendor it into a
product repo. Three pressures motivate this cleanup:

1. **Vendoring.** The client expects the whole apparatus (engine + protocols +
   workflows) to live under `.github/`, out of the way of their product code, and
   to be delivered as a copied/versioned unit — not scattered across the repo root.
2. **Readability as the engine grows.** The engine is 657 lines of bash across 5
   files, leaning heavily on `jq` (49 calls), `yq` (24), `git` (14), and `gh` (8),
   including a `yq → json → jq` dance forced by mikefarah yq's missing
   `if/then/else`. As it evolves (v2 fan-out/join added real complexity), bash is
   becoming the limiting factor for comprehension. Python reads better and scales.
3. **Duplication.** `protocols/grumpy/` and `protocols/multi-grumpy/` carry
   byte-identical copies of all three checks, and three near-identical publish
   hooks differ only in wording strings.

**Intended outcome:** the entire engine, checks, publish hooks, and test suite are
Python; everything ships as a self-contained vendored unit at
`.github/agent-factory/` with a `VERSION`; behavior is provably unchanged at every
step (the existing tests are the regression anchor); and each protocol remains an
independently copy-pasteable template for the client to clone.

## Decisions (locked during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Layout | `.github/agent-factory/{engine,protocols}` + `VERSION` | Client wants everything under `.github/`; one vendored unit |
| Delivery | Copied/vendored with `VERSION` (`0.1.0`) | No submodule; simple to copy and version |
| Engine language | Python (separate executables + `lib.py`) | Readability; preserve ABI for minimal blast radius |
| State format | **YAML, via PyYAML** | Preserve the human-auditable git trail (a stated design value); remove the yq→json→jq dance |
| Dedup model | **Self-contained protocols** | Vendorability: each protocol is a complete template; dedup only *within* a protocol |
| Publish→GitHub | `subprocess` to `gh` | Zero new deps; 1:1 behavior (422 surfacing, APPROVE→COMMENT fallback) |
| Checks | Python, stdlib-only (`json`/`os`/`sys`/`re`) | Match existing `.py` checks; no runtime deps |
| Tests | **pytest** (dev-only, top-level) | Best ergonomics for 774 lines; never enters the vendored unit or trust zones |
| Tests/docs location | Stay at repo root; paths updated | They are this repo's dev infra, not part of the vendored unit |
| Work structure | One phased program with a hard "all tests green" gate between phases | One pipeline; regression anchor preserved between engine and test rewrites |

## Architecture: the regression-safety invariant

The existing **bash tests are black-box ABI tests** — they invoke `next.sh`,
`advance.sh`, `run-checks.sh`, `join.sh` by positional args and parse the JSON on
stdout; they read state via `yq`. They are therefore *language-agnostic* and can
validate a Python engine unchanged (modulo `.sh`→`.py` in the invocation path).

This is the safety net for the whole program: **never rewrite an implementation
and its tests in the same step.** The bash suite rides along as the executable
spec through Phases 1–3, and is only translated to pytest in Phase 4 — after the
Python engine/checks/publish have already been proven green against it.

## Target layout

```
.github/
  workflows/                       # stays — GitHub mandates this path
    orchestrator.yml               #   only engine-call sites change
    protocol-join.yml
    *-agent.md / *-agent.lock.yml  #   unchanged (agents never reference these paths)
  agent-factory/
    VERSION                        # "0.1.0"
    README.md                      # engine/ generic-don't-edit; protocols/ yours; VERSION = vendored cut
    engine/
      lib.py  next.py  advance.py  run-checks.py  join.py
    protocols/
      grumpy/{protocol.json, *.evidence.schema.json, checks/, publish/, README.md}
      multi-grumpy/{...}
tests/                             # stays top-level → pytest
docs/                              # stays top-level → prose updated
```

`git mv` is used for both relocations so history follows. The durable state path
is derived from `protocol.json .name`, so the `agentic-state` branch layout
(`<protocol-id>/<instance-key>.yaml`) is **unchanged** by the move.

## Phase 1 — Relocate to `.github/agent-factory/` + VERSION

**Goal:** pure relocation, zero behavior change.

- `git mv .github/engine .github/agent-factory/engine`
- `git mv protocols .github/agent-factory/protocols`
- Add `.github/agent-factory/VERSION` (`0.1.0`) and a short `README.md`.
- Update path references (engine scripts need **no** change — they `source`
  `lib.sh` via `$(dirname "$0")/lib.sh` and derive the protocol dir from the passed
  `protocol.json` arg):
  - `orchestrator.yml`: ~10 sites — `.github/engine/` → `.github/agent-factory/engine/`
    and `protocols/multi-grumpy/...` → `.github/agent-factory/protocols/multi-grumpy/...`.
  - `protocol-join.yml`: 2 sites.
  - `tests/*.sh`: path constants (`PROTO=`, `NEXT=`, `RC=`, `JOIN=`, `source` lines,
    direct check/publish paths).
  - Docs prose: `CLAUDE.md`, `docs/STATUS.md`, `docs/HOW-IT-WORKS.md`,
    `.github/agent-factory/protocols/grumpy/README.md`. (The untracked
    `docs/superpowers/plans/…` historical plan is left as-is.)

**Gate:** `for t in tests/test-*.sh; do bash "$t"; done` all green (still bash engine, moved paths).

## Phase 2 — Engine bash → Python

**Goal:** rewrite the 5 engine files in Python; ABI byte-stable; bash tests green.

### Files & responsibilities (preserved 1:1)
- `lib.py` — state checkout, `cas_push`, status-comment upsert, `resolve_executable`,
  `set_check_run`, `match_run_by_cid`, `render_fanout_status_body`, `state_file`,
  `instance_file`, `protocol_id`.
- `next.py` — pure planner: `(state, protocol, command) → action JSON`.
- `advance.py` — sole writer of non-initial state: verdicts → mutate → publish →
  CAS-push → re-dispatch.
- `run-checks.py` — resolve + run a state's checks → verdicts (forwards `CHECK_PARAMS`).
- `join.py` — fan-out AND-barrier.

### Design rules
- **Separate executables, ABI preserved.** Same positional args, same stdout JSON,
  same env vars (`BRANCH`, `PR`, `AGENT_RUN_ID`, `ENGINE_LOCAL`, `STATE_REMOTE`,
  `PUBLISH_TOKEN`, `CHECK_PARAMS`, …). Shebang `#!/usr/bin/env python3`, `chmod +x`.
  The orchestrator and bash tests change only `.sh`→`.py`.
- **`lib.py` is dual-surface:**
  - *Importable module* for the other engine scripts (`import lib`; same-dir).
  - *Subcommand CLI* (`python3 lib.py <subcommand> …`) for the helpers the
    orchestrator/join call inline after `source lib.sh`: `set-check-run`,
    `instance-file`, `render-fanout-status-body`, `upsert-status-comment`,
    `cas-push`, `match-run-by-cid`. Each inline `source lib.sh; func …` site becomes
    one Python call. Where a step is multi-line engine logic (e.g. orchestrator's
    "Ensure shared status comment": `yq`-read id → render → upsert → push), collapse
    it into a single `lib.py` subcommand to shrink embedded bash.
- **External tools via `subprocess`** (no heavy libraries): `git` (preserve
  compare-and-swap / fast-forward-only semantics exactly — **never force-push**
  `agentic-state`), `gh`. All `jq` logic → stdlib `json`; all `yq` I/O →
  `yaml.safe_load` / `yaml.safe_dump(sort_keys=False, default_flow_style=False)`
  for stable, readable diffs.
- **Cross-tool transition safety:** PyYAML-written state is valid YAML, so the
  still-bash tests' `yq` reads succeed during this phase — the engine port need not
  be atomic with the test port.
- **Orchestrator stays YAML.** Only its calls *into* engine logic become Python; we
  do not rewrite trust-zone glue. The security rule (agent-derived strings —
  `feedback`, `verdicts`, filenames — passed via `env:`, never interpolated into
  `run:`) is preserved.

**Gate:** the (path + `.sh`→`.py`-updated) bash suite all green against the Python engine.

## Phase 3 — Checks + publish → Python

**Goal:** the only remaining bash check and all publish hooks become Python; dedup within each protocol.

- **Checks.** Only `schema-valid.sh` is bash (`rubric-coverage.py`,
  `traces-exist-in-diff.py` already Python). Port to `schema-valid.py` in **both**
  `grumpy/checks/` and `multi-grumpy/checks/` as self-contained copies. Preserve the
  check ABI: 3 path args (`<evidence.json> <diff.txt> <changed-files.txt>`), one
  `{check,pass,feedback}` JSON object on stdout, **always exit 0**, categories from
  `CHECK_PARAMS`. The jq validation (legal categories; verdict enum; issues-found
  has ≥1 finding with non-empty `existing_code`+`comment` and a valid line/side
  anchor; none-found has `examined`) maps directly to stdlib `json`.
- **Publish.** Rewrite all three hooks in Python, shelling to `gh` via `subprocess`.
  Preserve the publish ABI: args `<evidence.json> <instance-key>`; env `ENGINE_LOCAL`,
  `GITHUB_REPOSITORY`, `PUBLISH_TOKEN`, `PR`; `{conclusion,summary}` on stdout; **exit
  code load-bearing** (0 on success, nonzero on hard failure). Preserve 1:1 behavior:
  head-SHA pinning (`commit_id`), single review POST, 422-body surfacing,
  APPROVE→COMMENT fallback.
  - `multi-grumpy/publish/`: shared importable `_review.py` holds the mechanism
    (same-dir `import _review` works — script dir is `sys.path[0]`); `publish-grumpy.py`
    and `publish-security.py` are thin entry points supplying only their four wording
    strings. *This is the only genuine within-protocol duplication.*
  - `grumpy/publish/`: one hook → stays a single self-contained
    `publish-review-from-evidence.py` (no lib split; no duplication to remove).
- **Resolution.** Check/publish resolution is extension-agnostic, so `protocol.json`
  needs no name edits — but each `.sh` is **deleted as the `.py` is added**, to avoid
  `resolve_executable`'s ambiguity error (two files matching `<name>.*`).

**Gate:** `test-checks.sh`, `test-publish.sh`, `test-runchecks.sh` (path/`.py`-updated) green, plus the full suite.

## Phase 4 — Tests bash → pytest

**Goal:** translate all 8 suites to pytest; confirm parity; retire bash tests.

- One pytest module per current suite (`test_engine.py`, `test_checks.py`,
  `test_runchecks.py`, `test_publish.py`, `test_correlation.py`, `test_join.py`,
  `test_fanout_e2e.py`, `test_status_comment.py`).
- A shared fixture spins up the bare-git fake `agentic-state` origin and runs with
  `ENGINE_LOCAL=1` (replacing each script's hand-rolled setup). State assertions read
  via PyYAML.
- **Parity check:** each ported suite must reproduce the same pass/fail as its bash
  predecessor against the Python engine before the bash file is deleted.

**Gate:** `pytest` all green; bash suite removed; pytest is now the anchor.

## Dependencies

- **Runtime (vendored unit):** Python 3 + **PyYAML**; `git` + `gh` on PATH. No `jq`,
  no `yq`. PyYAML is preinstalled on GitHub ubuntu runners; noted in
  `agent-factory/README.md` for the client copy.
- **Dev only (never vendored, never in a trust zone):** pytest.

## Verification

- Phases 1–3, from repo root: `for t in tests/test-*.sh; do echo "== $t =="; bash "$t"; done` — all green is the gate to the next phase.
- Phase 4: `pytest` — all green, with per-suite parity confirmed before deletion.
- Whole-program acceptance: a clean `pytest` run plus a manual read-through of
  `orchestrator.yml`/`protocol-join.yml` confirming every engine-call site points at
  `.github/agent-factory/engine/*.py` and no `source *.sh` remains.

## Non-goals (YAGNI)

- No cross-protocol dedup (self-contained protocols chosen).
- No state-format change (YAML stays; only the tool reading it changes).
- No `gh aw` recompile (agents never reference engine/protocol paths).
- No git submodule (copied/vendored model with `VERSION`).
- Orchestrator not rewritten beyond its engine-call sites; it remains YAML.
- No new engine features or behavior changes of any kind — this is a pure
  language/location refactor.
