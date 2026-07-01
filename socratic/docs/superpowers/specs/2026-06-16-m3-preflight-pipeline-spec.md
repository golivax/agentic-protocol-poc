# M3 — Preflight Gate Port + Combined `code-review-pipeline` (SPEC)

**Date:** 2026-06-16
**Status:** SPEC — decisions locked, ready for `writing-plans`.
**Parent design (approved):** `docs/superpowers/specs/2026-06-16-code-review-pipeline-design.md`
(the master spec; M3 is its Milestone 3). This file records the M3-specific decisions
that the master spec left open or that **B→A** changed, plus the concrete file map and ABIs.
Read the master spec's "Milestone 3" section (lines 264–318) and its Decisions table first —
this spec assumes them and only states deltas + the implementation contract.

---

## Goal

Port the custody **preflight gate** onto this engine and chain it in front of the existing
`multi-grumpy` review as one protocol: **preflight → (clear) → review fan-out → join → done**;
a **blocked** preflight halts the pipeline before any review runs (`on_blocked: halt`).
This is the master spec's "sophisticated code review pipeline" and **the first multi-phase
live test** of the engine.

The port is the proof that a structurally-new protocol drops in by authoring only
**(i)** the gh-aw agent markdown, **(ii)** `protocol.json`, **(iii)** the checks — plus its
conclude/publish hooks. **No orchestrator YAML** is written (B→A's router auto-discovers the
protocol from its `triggers` block).

## Port source

The custody preflight lives at
`/home/gustavo/huawei/new-custody/custody/app/backend/component/preflight/`:
- `workflow/checks.js` — the four deterministic checks (`spec-present`, `plan-present`,
  `docs-updated-with-code`, `tests-updated-with-code`) + path classifiers
  (`isDocFile`/`isTestFile`/`isCodeFile`).
- `workflow/scripts/locate.js` — spec/plan artifact location (PR body + changed files + repo probe).
- `workflow/registry.js` — `CHECKS` (with `severity`) + `computeVerdict` (`blocked` iff any
  `severity:'blocker'` check is `fail`/`error`).
- `workflow/scripts/merge-verdict.js` — merges deterministic + AI items → `{records[], meta}`.
- `workflow/scripts/deterministic-checks.js` — prefetch/gather + `runDeterministic`.
- `workflow/preflight-gate.md` — the agent (codex/gpt-5.5; we reuse Claude sonnet instead).

These are **JS ports → Python** honoring this engine's ABIs. Fidelity is deliberately
**not** 1:1 — see the locked deviations below.

## Engine substrate (already implemented — do NOT rebuild)

Verified present on `main` @ `3ad2899`:
- **conclude hook** — `advance.py:run_conclude_hook` runs the state's optional `conclude`,
  returns `{conclusion, summary, blocked}`; `blocking` (from zone-3 `block` checks) is passed in.
- **`on_blocked: halt`** — `advance.py:324` terminates the pipeline when
  `conclude.blocked && state.on_blocked == "halt"` (aggregate + sub check-run → `failure`).
- **multi-phase cursor** — `next.py:seed_and_dispatch_phase`; `advance.py` advances the
  `_instance.yaml` `phase` cursor on clear and fires `repository_dispatch protocol-advance`.
- **DECIDE + `on_fail` severities** — `lib.decide`; `run-checks.py` stamps `on_fail`
  (`iterate`/`advisory`/`block`) per check entry.

**M3 is therefore authoring**, with ONE small generic-engine addition (PR-body env infra, § Decision 3).

---

## Locked decisions (M3-specific)

### Decision 1 — `spec-present`/`plan-present` detect from CHANGED FILES only (+ build PR-body env infra)

**Deviation from custody AND from a naive port.** Custody's `locate.js` treats a `## Requirements`
section **in the PR description body** as a valid spec source (`detectSpecInBody`). M3 does **not**:
a spec/plan counts as present **iff a spec/plan FILE is in the PR diff** (the committed-artifact
signal is the more adequate presence check). Detection is purely over `changed-files.txt`, by path:
- **spec:** `docs/specs/…`, `docs/superpowers/specs/…`, `specs/…`, `SPEC.md`, `REQUIREMENTS.md`
  (port `classifyArtifactPaths`'s spec arm).
- **plan:** `docs/superpowers/plans/…`, `docs/plans/…`, `plans/…`, `PLAN.md` (plan arm).

A check is `pass:true` iff ≥1 matching file is in the diff; else `pass:false` with `feedback`
naming what was searched. `on_fail: block` makes absence a blocking verdict. The PR body is **not**
consulted for presence; `detectSpecInBody`/`detectPlanInBody` are **not** ported for the checks.

**Separately, build PR-body/title env infrastructure** (also requested): the generic checks job
fetches `gh pr view --json body,title` and `run-checks.py` forwards `PR_BODY` and `PR_TITLE` to
**every** check via env (alongside `CHECK_PARAMS`). M3's spec/plan checks do **not** read it; it is
general infrastructure for future checks that legitimately need to parse the PR description/title.

### Decision 2 — Trigger coexistence: pipeline owns PR + `/review`; multi-grumpy → `/grumpy` only

Because B→A's `lib.route` errors on ambiguous matches, two protocols cannot both match one event.
Resolution:
- **`code-review-pipeline` triggers:** `/review` comment + `pull_request` `opened`/`reopened`
  (`command: start`) + `synchronize` (`command: reset`). It becomes the **default PR reviewer**.
- **`multi-grumpy`:** **drop its PR triggers**, keep `/grumpy` comment-only (mirrors
  `grumpy → /v1-grumpy`). Still independently triggerable for the standalone fan-out.
- **`grumpy`:** unchanged (`/v1-grumpy`).

Resulting route table (all unambiguous): `/review` → pipeline; PR opened/reopened/synchronize →
pipeline; `/grumpy` → multi-grumpy; `/v1-grumpy` → grumpy. A `test_route.py` real-protocols
regression assertion is extended to cover this.

### Decision 3 — PR-body env is the ONLY engine change

`agentic-engine.yml` (checks job) + `run-checks.py` gain the `PR_BODY`/`PR_TITLE` env forwarding
(Decision 1). Trust zones unchanged (the body is fetched by the trusted checks job from GitHub, not
the agent). Everything else in M3 is authoring under `protocols/code-review-pipeline/`.

### Decision 4 — `adherence-coverage` derives its expected set from changed-files

`adherence-coverage` (zone-3 form-check, `on_fail: iterate`) is the preflight analogue of grumpy's
`rubric-coverage`. Because presence is deterministic (Decision 1), the check **re-derives the
expected adherence set from `changed-files`**: a spec file present ⇒ expect `spec-adherence` judged
exactly once in evidence; a plan file present ⇒ expect `plan-adherence`; absent ⇒ that adherence
check must **not** appear (it was correctly scoped out). This keeps zone-3 independent of the
agent-produced scoping and stays consistent with `spec-present`/`plan-present`. The agent's pre-step
scopes `ai-checks.json` using the **same** changed-files logic, so the agent and the check agree.

### Decision 5 — Checks ported / skipped (from master spec Decision 8, confirmed)

Port all *implemented* custody checks; **skip** `local-review-evidence` (state `todo`).
`spec-present`/`plan-present` are `block` (the deliberate divergence — custody warns).
`docs-updated`/`tests-updated` are `advisory`. `spec-adherence`/`plan-adherence` are AI checks
(judged by the agent; rolled up in `conclude`). LLM endpoint: **Claude sonnet** (master Decision 10),
not custody's codex/gpt-5.5.

---

## Components and file map

```
.github/agent-factory/protocols/code-review-pipeline/
  protocol.json                       # 3 phases; triggers (/review + PR); conclude/publish on preflight
  preflight.evidence.schema.json      # rubric: one verdict per scoped adherence check (+ traces shape)
  checks/
    spec-present.py                    # block;   changed-files only (Decision 1)
    plan-present.py                    # block;   changed-files only
    docs-updated-with-code.py          # advisory; path classification (port checks.js)
    tests-updated-with-code.py         # advisory; path classification
    adherence-coverage.py              # iterate; derives expected set from changed-files (Decision 4)
    schema-valid.py                    # iterate; reuse/adapt grumpy's
    traces-exist-in-diff.py            # iterate; reuse verbatim from grumpy
  publish/
    conclude-preflight.py              # conclude: merge adherence verdicts + blocking → clear/blocked
                                       #   + emit custody-shaped verdict.json payload (records[]+meta)
    publish-verdict.py                 # publish: write verdict.json artifact + set preflight sub check-run

.github/workflows/
  preflight-agent.md (+ .lock.yml)     # gh-aw agent: prefetch + scope (ai-checks.json) → evidence.json
                                       #   Claude sonnet; read-only; safe-outputs staged
  agentic-engine.yml                   # +PR_BODY/PR_TITLE fetch in checks job (Decision 3)

.github/agent-factory/engine/
  run-checks.py                        # +forward PR_BODY/PR_TITLE env to each check (Decision 3)

.github/agent-factory/protocols/multi-grumpy/protocol.json   # drop PR triggers (Decision 2)
```

Shared check helpers (path classifiers, spec/plan path matchers) live in the
`code-review-pipeline/checks/` directory; duplication with grumpy's reused checks
(`traces-exist-in-diff`, `schema-valid`) follows the existing per-protocol-copy convention
(see `docs/superpowers/plans/2026-06-12-branch-scoped-params.md` — checks are copied per protocol).

## ABIs (unchanged except the additive env)

- **Check ABI:** `<check> <evidence.json> <diff.txt> <changed-files.txt>` → `{check,pass,feedback}`,
  exit 0. **Additive:** `PR_BODY`/`PR_TITLE` available in env (alongside `CHECK_PARAMS`); existing
  checks ignore them. `on_fail` is protocol data stamped by `run-checks.py`, not check stdout.
- **conclude hook:** `<hook> <evidence.json> <instance-key>`, reads `BLOCKING` via env, prints
  `{conclusion, summary, blocked}` (and `conclude-preflight` also writes the `verdict.json` payload
  for `publish-verdict`). Trusted (zone 4). Already supported by `advance.py:run_conclude_hook`.
- **publish hook:** `<hook> <evidence.json> <instance-key>` with env `ENGINE_LOCAL`,
  `GITHUB_REPOSITORY`, `PUBLISH_TOKEN`, `PR`; prints `{conclusion, summary}`; performs side-effects
  (artifact + sub check-run). Trusted (zone 4).
- **Evidence:** rubric shape (one verdict per scoped adherence check; `issues-found` findings carry
  verbatim `existing_code` + `side`/`line` so `traces-exist-in-diff` works) + negative attestation
  with `examined` ids.

## `protocol.json` (shape)

```jsonc
{
  "name": "code-review-pipeline",
  "triggers": [
    { "on": "issue_comment", "comment_prefix": "/review", "command": "start" },
    { "on": "pull_request",  "actions": ["opened","reopened"], "command": "start" },
    { "on": "pull_request",  "actions": ["synchronize"],       "command": "reset" }
  ],
  "states": [
    { "id": "preflight", "kind": "agent", "workflow": "preflight-agent",
      "evidence": "preflight.evidence.schema.json", "max_iterations": 2,
      "params": { "ai_checks": ["spec-adherence","plan-adherence"] },
      "checks": [
        { "run": "schema-valid",            "on_fail": "iterate"  },
        { "run": "adherence-coverage",      "on_fail": "iterate"  },
        { "run": "traces-exist-in-diff",    "on_fail": "iterate"  },
        { "run": "spec-present",            "on_fail": "block"    },
        { "run": "plan-present",            "on_fail": "block"    },
        { "run": "docs-updated-with-code",  "on_fail": "advisory" },
        { "run": "tests-updated-with-code", "on_fail": "advisory" }
      ],
      "conclude": "conclude-preflight", "publish": "publish-verdict",
      "on_blocked": "halt", "next": "review" },

    { "id": "review", "kind": "fanout", "next": "join",
      "branches": [ /* grumpy + security branches, verbatim from multi-grumpy */ ] },

    { "id": "join", "kind": "join", "of": "review", "next": "done" }
  ]
}
```

The `review` phase's branches + per-branch publish hooks are copied **verbatim** from
`multi-grumpy` (the review behavior is unchanged; only its trigger ownership moves to the pipeline).

## `conclude-preflight` roll-up

`blocked = BLOCKING (any zone-3 `block` check failed: spec/plan absent) OR (any adherence verdict
in evidence failed)`, else `clear`. Port of `computeVerdict` (blocker `fail`/`error` ⇒ blocked) +
`mergeVerdict`. Emits the custody-shaped `verdict.json` payload: `{ records: [checklist, ...results,
verdict], meta: { pr_number, head_sha } }`. The `meta.head_sha` echo uses the real head SHA (fix
custody's `headRefName`-as-sha slip; use `headRefOid`).

## Live test (both paths, this repo)

- **Clear:** a PR that commits a spec file (`docs/specs/…` or `REQUIREMENTS.md`) + a plan file
  (`docs/**/plans/…` or `PLAN.md`) + code that adheres. Trigger via `/review` (and a `pull_request`
  event). Assert: aggregate `code-review-pipeline` check-run; `_instance.yaml` cursor walks
  `preflight → review → join → done`; per-phase sub-runs; grumpy + security reviews posted; a
  `clear` `verdict.json` artifact.
- **Blocked (absence):** a PR with **no** spec/plan file → `spec-present`/`plan-present` fail their
  `block` checks → `blocked` → pipeline halts, **no review runs**, aggregate check-run `failure`,
  `blocked` `verdict.json`.
- **Blocked (adherence):** a PR with a spec file but code that does **not** adhere → preflight
  `blocked` via the conclude/adherence source (exercises the AI path, not just absence).

The live run is the binding proof (multi-phase phase relay + conclude/`on_blocked` exercised
end-to-end for the first time). Reuse a throwaway PR; watch the permission ceiling (the router
already grants the union) and the `protocol-advance`/`protocol-continue` cross-workflow contracts.

## Testing strategy

- **Unit (pytest):** each ported check — `spec-present`/`plan-present` (file present / absent /
  multiple), `docs-updated`/`tests-updated` (code-only / with-docs / with-tests / no-code),
  `adherence-coverage` (expected-set derivation: spec-only / plan-only / both / neither; missing /
  duplicate / extra verdict), `schema-valid`, `traces-exist-in-diff`; `conclude-preflight`
  (clear / blocked-by-absence / blocked-by-adherence; `verdict.json` shape + `meta`).
- **`run-checks.py`:** `PR_BODY`/`PR_TITLE` forwarded to checks (a probe check echoes them).
- **`lib.route`:** real-protocols assertions updated — `/review` → pipeline, PR-open → pipeline,
  `/grumpy` → multi-grumpy, no ambiguity.
- **Multi-phase engine:** the existing `test_multiphase.py`/`test_phase_relay.py` fixtures already
  cover cursor progression + `on_blocked`; add a `code-review-pipeline`-shaped fixture assertion if
  a gap is found (the engine itself is unchanged).
- **Regression:** full suite stays green (currently 237); `grumpy`/`multi-grumpy` engine/checks
  behavior unchanged (only multi-grumpy's *triggers* change).
- **Live:** the three paths above.

## Verification plan

- pytest green (237 + new M3 unit tests).
- `actionlint` (project mode) — cross-validates `agentic-engine.yml` + the new agent lock.
  Reinstall per session: `GOBIN=/tmp/gobin go install github.com/rhysd/actionlint/cmd/actionlint@latest`.
- `gh aw compile` after editing `preflight-agent.md`; commit the `.lock.yml`.
- **LIVE run is binding** — clear + both blocked paths. To read a `startup_failure`'s cause use the
  GitHub web UI (the API hides it).

## Risks

- **First multi-phase live test.** The phase relay (`protocol-advance` → router re-enters `plan` at
  the new phase → `protocol-continue` within a phase) runs end-to-end for the first time. Watch the
  permission ceiling and the cross-workflow `client_payload` contracts.
- **PR-body env infra is YAGNI-adjacent** — it is built per explicit request for future checks; keep
  it minimal and separable, and `log`/document that M3's own checks do not use it.
- **Port fidelity drift** — the changed-files-only presence (Decision 1) and the
  `block`-on-absence severity (Decision 5) are deliberate divergences from custody. Whoever ports the
  checks must NOT faithfully reproduce custody's body-detection or advisory-warn behavior.
- **Agent/check scoping must agree** — the agent's `ai-checks.json` scoping and `adherence-coverage`'s
  expected-set both derive from changed-files; if they diverge, the form-check fails spuriously.
  Share the path-matching logic.

## Non-goals (from master spec)

Custody-app consumption of `verdict.json` (only emitted for forward-compat); the codex/gpt-5.5
endpoint; the check-authoring meta-protocol; the human-in-the-loop gate (v4).

## Suggested execution path

`writing-plans` → `subagent-driven-development`. Branch: `feat/m3-preflight-pipeline`. Tasks roughly:
(T1) PR-body env infra (engine: `agentic-engine.yml` + `run-checks.py` + test); (T2) the four
deterministic/advisory checks + shared path helpers + unit tests; (T3) `adherence-coverage` +
`schema-valid` + reuse `traces-exist-in-diff` + unit tests; (T4) `preflight-agent.md` + evidence
schema + scoping pre-step + `gh aw compile`; (T5) `conclude-preflight` + `publish-verdict` +
`verdict.json` + unit tests; (T6) assemble `protocol.json` + multi-grumpy trigger change + route
tests; (T7) actionlint + LIVE clear + both blocked paths.
