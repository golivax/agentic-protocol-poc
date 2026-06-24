# Stage 4c — Live Verification (deep-review-stub + re-verify code-review/recover)

- **Date:** 2026-06-24
- **Status:** Design approved; ready for implementation plan
- **Depends on:** Stage 4a (engine unification) + 4b (GHA NODE_PATH wiring), merged into local `main` (`d72aff5`, 414 tests). Continues on `main` (workflow-on-default-branch rule).
- **Parent spec:** `docs/superpowers/specs/2026-06-23-stage4-recursive-engine-unification-design.md` §6.

## 1. Motivation

The unified engine (4a) + GHA wiring (4b) are offline-proven (414 pytest tests +
structural contract tests + actionlint). The one thing pytest cannot prove is
that the wiring actually drives real GitHub Actions end-to-end. Stage 4c is the
live shakedown: a real depth-4 protocol (`deep-review-stub`) walked on a live PR
through real Actions, plus re-verification that the two existing live protocols
(`code-review`, `recover-mental-model-stub`) still work on the unified engine.

This stage is where live-only bugs surface (the recover-mm precedent caught two
the offline layers could not). It also performs the production deploy (push to
`origin/main`), which is **gated on explicit user confirmation**.

## 2. The protocol DSL is untouched (again)

`deep-review-stub` is authored in the existing protocol.json DSL — no new fields,
no schema changes (standing constraint: keep the DSL human-intuitive; flag any
change). It is a new *protocol*, not an engine change.

## 3. `deep-review-stub` protocol

Mirror the proven `deep-fanout` depth-4 topology (the keystone pytest walk already
exercises this exact engine shape, minimizing live-debug risk):

```
preflight (fanout)
├── quick (flat agent)
└── deep (sub-pipeline)
    ├── triage (agent)
    ├── analyze (fanout)
    │   ├── sec (flat agent)
    │   └── perf (flat agent)
    ├── join-analyze (join of: analyze, next: report)
    └── report (agent; inputs: [sec, perf])
join-preflight (join of: preflight, next: done)
```

- **Location:** `.github/agent-factory/protocols/deep-review-stub/` —
  `protocol.json` + `checks/*` + `*.evidence.schema.json` + `publish/*`, mirroring
  `recover-mental-model-stub/`'s layout.
- **Trigger:** `{ "on": "issue_comment", "comment_prefix": "/deep-review", "command": "start" }`.
- **Checks:** lightweight per leaf — a `schema-valid` check (evidence parses +
  matches the permissive leaf schema) and a presence check (the evidence has the
  one expected key). It is a STUB proving orchestration, not a real review, so
  checks stay minimal (`on_fail: iterate`). No `block`/gate severity.
- **Schemas:** permissive per-leaf evidence schemas (one expected string key,
  e.g. `{"finding": "..."}` / `{"summary": "..."}`).
- **Publish:** minimal publish hooks (a `noop`-style hook, or a one-line PR
  comment) per the recover layout. `report` may post a small combined comment.
- **`max_depth`:** 4 (matches the static tree; default cap is 5 so this is fine).

## 4. gh-aw stub agents

Five thin gh-aw agents in `.github/workflows/` (`quick-agent.md`,
`triage-agent.md`, `sec-agent.md`, `perf-agent.md`, `report-agent.md`), each
mirroring `rmm-summary-agent.md`:
- Frontmatter: `strict: false`, `sandbox.agent: false`, LLM endpoint under
  `engine.env` (`ANTHROPIC_BASE_URL` literal + `ANTHROPIC_AUTH_TOKEN` from
  secret), model `claude-sonnet-4-6`, `run-name` embeds `cid:[<cid>]`,
  read-only permissions, the `pre-agent-steps` PR-diff fetch + `aw_context`
  materialization, `post-steps` evidence upload.
- Prompt: read the PR diff + `task-context.json`, produce minimal valid
  `evidence.json` for that leaf (a STUB — a short generated finding, not a real
  deep review). `report` reads its `inputs` (`sec`/`perf` from `aw_context.inputs`)
  and emits a combined finding.
- Compile: `gh aw compile` → commit the `.lock.yml`s alongside the `.md`s.
- All committed on `main`.

## 5. Live verification (the deliverable)

1. **Production push (gated):** push `main` (with 4a+4b+deep-review-stub+agents) to
   `origin/main` (`golivax/agentic-protocol-poc`). **Requires explicit user
   confirmation immediately before the push** — it is the production deploy.
2. **deep walk:** open (or reuse) a test PR; comment `/deep-review`; watch Actions
   walk: preflight fanout (`quick` ∥ `deep`) → `deep` sub-pipeline (`triage` →
   `analyze` fanout `sec`∥`perf` → `join-analyze` bubbles → `report`) →
   `join-preflight` → instance `done`. Confirm the recursive NODE_PATH dispatch,
   per-leg agent runs, path-keyed artifacts, and bubbling joins all work live.
3. **Re-verify existing protocols:** `/review` (code-review: preflight gate →
   review fanout → join → approval gate → `/approve`) and `/recover`
   (recover: fanout + rationale sub-pipeline → `/answer` → finalize → join →
   combine) both complete on the unified engine.
4. **Live-debug pass:** expect 1–3 live-only bugs (missing token on a job that now
   dispatches; a coordinate fine in tests but wrong under a real protocol
   name/depth; artifact-name edge case). Fix on `main`, re-verify.
5. **Opportunistic hardening:** while verifying join live, env-pass the pre-existing
   `protocol-join.yml` `client_payload.protocol`/`instance` `run:`-interpolation
   (4b-review-flagged security seam).
6. Leave the test PR open (prior-milestone convention).

## 6. Execution phases

- **4c-build (offline, subagent-driven):** author `deep-review-stub` protocol +
  checks + schemas + publish hooks + a pytest walk test (drive the new protocol
  through next.py/advance.py/join.py via NODE_PATH like `test_deep_fanout_e2e`, so
  the protocol shape is offline-verified before going live); author the 5 gh-aw
  agents; `gh aw compile`; commit. Gate: pytest green + actionlint + the new
  protocol walks offline.
- **4c-live (interactive):** the gated production push + the live PR walks +
  live-debug. Not subagent-automatable (needs real Actions runs + watching).

## 7. Security / constraints

- Production push gated on explicit confirmation; never force-push `main`.
- Agent frontmatter keeps the documented posture (`strict:false` +
  `sandbox.agent:false` egress firewall off for the custom endpoint — the biggest
  documented weakening; agents stay read-only + never hold the state PAT).
- Secrets already configured on the repo: `ANTHROPIC_API_KEY`,
  `ANTHROPIC_BASE_URL`, `POC_DISPATCH_TOKEN`.
- DSL untouched. CLAUDE.md security rule (agent-derived strings env-only) holds.

## 8. Done-bar

- `deep-review-stub` protocol + 5 agents + locks committed on `main`; the offline
  pytest walk for it is green; full suite + actionlint green.
- Live: `/deep-review` walks to `done` on a real PR; `/review` and `/recover`
  complete; live-only bugs fixed; pushed to `origin/main`.

## 9. Out of scope

- Nested-gate `/answer` LIVE (engine-proven by `gate-deep` pytest; top-level
  `/answer` already live-proven by recover). `deep-review-stub` has no gate.
- The deferred minor cleanups (dead `agent-workflow` CLI; vestigial
  `client_payload[branch]/[substate]` relay in advance.py) — clean up
  opportunistically.

## 10. Risks

- **Live-only bugs** (the whole point of this stage) — budgeted debug pass.
- **Production push** — gated on confirmation; the branch is a safety ref.
- **gh-aw compile drift** — always recompile + commit locks after editing `.md`.
