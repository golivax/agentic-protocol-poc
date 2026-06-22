# Deferred: workflow-YAML integration for nested sub-workflow branches

**Status:** the engine + pytest layer for nested sub-workflow branches (Plans 1–4)
is implemented and green on branch `feat/nested-subworkflow-branches`. The
**GitHub-Actions workflow wiring** that makes it run live is deliberately
**deferred** to a separate integration pass on `main`, for two reasons:

1. `CLAUDE.md` mandates that `agentic-orchestrator.yml`, `agentic-engine.yml`, and
   the compiled agent `*.lock.yml` live on the default branch (`main`) and are
   never committed onto a feature/demo branch (it pollutes the reviewed diff and
   those events run from `main`).
2. These steps cannot be validated without a live PR run; TDD does not cover them.

This file collects the exact wiring to apply during that integration pass. It is
the deferred portion of plan tasks **P2 T7**, **P3 T6**, and **P4 T6**.

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
