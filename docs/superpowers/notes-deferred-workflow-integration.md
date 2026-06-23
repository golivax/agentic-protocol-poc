# Workflow-YAML integration for nested sub-workflow branches

**Status:** ✅ **IMPLEMENTED** on branch `feat/recover-mental-model-stub` (the
real `recover-mental-model-stub` protocol + all the workflow wiring described
below). The engine + pytest layer (Plans 1–4) is on `main`. The wiring lands on
`main` per the rule that `agentic-orchestrator.yml`, `agentic-engine.yml`, and the
compiled agent `*.lock.yml` run from the default branch. **It is verified
statically (YAML parse, expression review, a reproduced+fixed CLI bug) but has NOT
yet had a live PR run** — see the live-verification checklist below.

What shipped on this branch:
- `recover-mental-model-stub` protocol (`protocol.json` + schemas + checks +
  publish-summary + append-rationale merge hook) — engine behavior pytest-covered.
- 3 gh-aw agents (`rmm-summary-agent`, `rmm-draft-agent`, `rmm-finalize-agent`)
  `.md` + compiled `.lock.yml` (`gh aw compile`, 0 errors).
- Engine gaps closed for live: `lib.agent_workflow` + `run-checks.py` are now
  substate-aware (and the `agent-workflow` CLI forwards the substate arg).
- `agentic-engine.yml`: `SUBSTATE` threaded through the `plan→dispatch→checks→
  advance` matrix (matrix axis is now `leg = {branch, substate}`); a new `/answer`
  command (write-gated, body via heredoc env, never interpolated); resolved
  `inputs` inlined into `aw_context.inputs` so `rmm-finalize-agent` reads
  `.inputs.answers` / `.inputs.draft`; `NODE` resolution generalized off the
  hardcoded `"review"` fallback to the protocol's first fanout/agent state.

## Live-verification checklist (the part only a real PR run can confirm)

Prereq: this branch must be **merged to `main`** first (issue_comment/PR events
run workflows from the default branch). Secrets already configured per CLAUDE.md
(`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `POC_DISPATCH_TOKEN`).

1. **Trigger:** on any open PR, comment `/recover`. Expect the orchestrator to
   route to `recover-mental-model-stub` and the engine `plan` job to emit a
   `run-fanout` with two legs: `summary` (flat) and `rationale` (substate `draft`).
2. **Fanout dispatch:** expect two `dispatch` matrix legs —
   `rmm-summary-agent` and `rmm-draft-agent` — both resolving a workflow (this is
   exactly what the CLI-substate fix unblocks; if `rationale` dies with
   "no agent workflow resolved … substate='draft'", the fix didn't land).
3. **Summary leg:** `rmm-summary-agent` posts a change summary; its `summary`
   leg reaches `done`.
4. **Gate opens:** after `draft` passes `questions-present`, the engine advances
   the `rationale` cursor to `clarify` and posts the agent's questions with the
   `/answer` syntax. The leg is NOT terminal; the join waits.
5. **Answer:** comment `/answer q1: … q2: …` (write access required). Expect the
   engine to accept it, run `answers-coverage`, and — once every question is
   answered — advance to `finalize` and dispatch `rmm-finalize-agent`.
6. **Inputs reach finalize:** confirm `rmm-finalize-agent`'s rationale reflects
   your answers (proves `aw_context.inputs.answers` was staged + read).
7. **Join + combine:** once both legs are `done`, the join advances to `combine`
   (`kind:"merge"`), `append-rationale` posts the combined summary + rationale,
   and the aggregate check-run goes green.

Known watch-points if a live run fails:
- Matrix object access (`matrix.leg.branch/.substate`) — only confirmable live;
  if GHA rejects the matrix, the `branches` output shape is wrong.
- Artifact name match across dispatch-upload / checks+advance-download
  (`runmeta-<branch>-<substate>`).
- Latent (not exercised by this single-phase protocol): a sub-pipeline branch
  nested under a *multi-phase* protocol — `next.py`'s multi-phase fanout emit
  omits per-branch `substate` (`next.py:183-184`); fine for `recover` (single
  fanout phase), but would need fixing before a multi-phase sub-pipeline.

---

## Original wiring spec (kept as the record; now implemented above)

This file collected the exact wiring applied during the integration pass — the
deferred portion of plan tasks **P2 T7**, **P3 T6**, and **P4 T6**.

## P2 T7 — stage resolved `inputs` into the agent job (`agentic-engine.yml`)

The dispatch action JSON emitted by `next.py`/`advance.py` now may carry an
`inputs` array: `[{as, path, kind}, …]` (resolved by `lib.resolve_inputs`, paths
are on the `agentic-state` branch).

Wiring:
- In the **plan/dispatch job** (zone 1, already holds the state branch checkout):
  after capturing the action JSON, add a step that `jq`-extracts `.inputs`, copies
  each `path` from the state checkout into a staging dir, and
  `actions/upload-artifact`s that dir.
- In the **agent job** (zone 2, read-only): add an `actions/download-artifact`
  step that lands the files under `inputs/<as>.json` in the agent workspace before
  the agent runs.
- Security: pass the action JSON via `env:`, never interpolate `.inputs[].path`
  or contents into a `run:` string. The agent stays read-only and never checks out
  `agentic-state`.
- Agent prompt docs (`*-agent.md`) reference `inputs/<name>.json`.

## P3 T6 — route `/answer` comments to the engine (`agentic-orchestrator.yml`)

`lib.match_trigger` already maps a protocol trigger
`{on: issue_comment, comment_prefix: "/answer", command: "answer"}` → command
`answer`; no engine change needed. Wiring:
- In the orchestrator's comment-routing step, recognise `/answer …` alongside
  `/review`/`/approve`/`/override`.
- Forward the raw comment body + actor to the engine as
  `env: ANSWER_BODY: ${{ github.event.comment.body }}`,
  `ANSWER_ACTOR: ${{ github.event.comment.user.login }}` — **never** inside a
  `run:` string (untrusted human input; the standing security rule).
- The real protocol (`recover-mental-model-stub`) must declare the `/answer`
  trigger in its `protocol.json`.

## P4 T6 — merge job env for the reduce hook (`protocol-join.yml`)

`join.py` runs `lib.run_merge_hook` inline in the join-evaluator job. Confirm that
job exports `PUBLISH_TOKEN`, `GITHUB_REPOSITORY`, `PR`, `PR_HEAD_SHA` so the
trusted merge hook (zone 4) can read inputs and publish. Add any missing var.

## Validation (during the integration pass)

- `gh aw compile` after any `*-agent.md` change; commit the `*.lock.yml`.
- `actionlint` on each edited workflow.
- A live smoke run on a throwaway PR exercising the `recover-mental-model-stub`
  protocol end-to-end: fanout(A ∥ B:[draft→clarify→finalize]) → join →
  combine → done, including a real `/answer` round-trip.
