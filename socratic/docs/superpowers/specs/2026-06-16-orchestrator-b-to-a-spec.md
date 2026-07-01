# Orchestrator B→A: single self-routing workflow (SPEC)

**Date:** 2026-06-16
**Status:** SPEC — decisions locked, ready for `writing-plans`.
**Supersedes the open questions in** `2026-06-16-orchestrator-b-to-a-design.md` (the design
brief — keep it for the rationale, the options-evaluated table, and the RESOLVED design facts).
This spec records the locked decisions and the implementation contract.

---

## Goal

Collapse the **N per-protocol trigger shims** (approach B) into **one engine-owned,
self-routing router workflow** (approach A). End state: authoring a new protocol means writing
ONLY (i) the gh-aw agent markdown, (ii) `protocol.json` (with its `triggers` block), (iii) the
checks — and **zero workflow YAML**. The router, engine, and join are all framework-owned and
protocol-agnostic.

This is the `docs/BACKLOG.md` "Orchestrator B→A" item, promoted ahead of M3 so the combined
`code-review-pipeline` lands in a zero-hand-written-YAML world.

## Non-goals (this iteration)

- **Multi-protocol fan-out per event.** One protocol handles a given entry event (enforced — see
  Decision 1). Fan-out-to-all would need dispatch-per-protocol (no `strategy` on a reusable-call
  job); deferred.
- **Engine behavior changes.** The engine (`agentic-engine.yml`) stays byte-identical. If a change
  to it turns out to be required, that is a signal to stop and re-spec.
- **Touching `protocol-join.yml`.** It is already generic and live-proven; left unchanged.

---

## Locked decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Multi-protocol match policy | **Error on ambiguous.** 0 matches → skip; exactly 1 → route to it; **≥2 → fail the `route` job loudly** (no silent pick). |
| 2 | `protocol-join.yml` | **Keep separate.** Router owns `repository_dispatch` types `[protocol-continue, protocol-advance]`; join keeps owning `[protocol-join]`. Unchanged. |
| 3 | Concurrency group key | **Static prefix + instance + branch**, prefix genericized `multi-grumpy-` → `agentic-`. Protocol is unknown at workflow-concurrency-eval time, so it cannot be in the key. |
| 4 | Naming / cleanup | **NEW** `agentic-orchestrator.yml`; **DELETE** `multi-grumpy-trigger.yml`; **KEEP** `agentic-engine.yml` + `protocol-join.yml`. |
| 5 | Engine input surface | **`protocol` only.** Verified against `agentic-engine.yml` `plan.ctx` (the step derives `command`/`instance`/`branch`/`phase` from `github.event` itself). Engine stays byte-identical. |

---

## Where we are now (B, live-proven on origin/main @ 9cb98ca)

- `.github/workflows/agentic-engine.yml` — generic reusable `on: workflow_call` engine. Input:
  `protocol` (path). Its `plan.ctx` step derives `(instance, command, branch, phase)` from
  `github.event` via `lib.match_trigger` for ITS one protocol. **Unchanged by B→A.**
- `.github/workflows/multi-grumpy-trigger.yml` — the per-protocol SHIM to be **DELETED**. Carries
  the static `on:`, `run-name`, `concurrency`, the union `permissions` ceiling, and
  `uses: ./agentic-engine.yml` with `protocol: <multi-grumpy path>` + `secrets: inherit`.
- `.github/workflows/protocol-join.yml` — already generic (reads `client_payload.protocol`); owns
  the `protocol-join` repository_dispatch type for ALL protocols. **Keep as-is.**
- `lib.match_trigger(protocol, event_name, action="", comment_body="") -> command|""` — maps an
  ENTRY event to a command via the protocol's `triggers` block. Pure. (`lib.py match-trigger` CLI.)
  Note: `protocol` here is the loaded dict; the CLI loads it from the path.
- `triggers` blocks already present in both `multi-grumpy/protocol.json` and `grumpy/protocol.json`
  (identical: `/grumpy`→start, PR opened/reopened→start, PR synchronize→reset).

**Key insight (verified in code): the engine barely changes.** B→A is almost entirely a NEW router
that decides *which protocol* to invoke the engine for; the engine still does per-protocol command
derivation given a `protocol` input. The router never derives the command — it only selects the
protocol path and decides skip.

---

## The constraint (recap — why a static `on:` is unavoidable)

`on:` is parsed by GitHub's control plane at registration time, before any job/runner/checkout
exists — so it can't read `protocol.json`. `on:` also forbids `${{ }}` expressions, and the
expression language has no file I/O. You cannot compute `on:` from repo data. Every solution just
relocates the static bit. Approach A relocates it to one router whose static `on:` is the union of
all protocols' entry events; a runtime `route` job then narrows to the matching protocol.

## RESOLVED design facts (do NOT re-research — from the brief, still binding)

### A job that calls a reusable workflow may use ONLY: `name, uses, with, secrets, needs, if, permissions`
(NOT `concurrency`, `strategy`, `env`, `environment`.) Implications:
- **Concurrency goes at the ROUTER WORKFLOW level**, using `github.event.*` (live-proven on the
  shim). This serializes per instance+branch.
- **No `strategy` on the engine-calling job** ⇒ the router handles **ONE protocol per event** (the
  PoC case). The engine's INTERNAL branch fan-out matrix is unaffected — it lives inside
  `agentic-engine.yml`'s dispatch/checks/advance jobs, not on the calling job.
- `with:` to a reusable workflow CAN be an expression (e.g. `protocol: ${{ needs.route.outputs.protocol }}`).
- The engine-calling job CAN set `permissions:` and `needs:`/`if:`.

### Reusable-workflow gotchas (live run caught these; pytest + actionlint did NOT)
- **Permission CEILING:** a called workflow can't request more `GITHUB_TOKEN` permission than the
  CALLING job grants → else `startup_failure` before any job runs. The router must grant the UNION
  the engine needs: `contents: read, issues: write, pull-requests: write, checks: write, actions: read`.
- **`inputs` in workflow-level `run-name`/`concurrency` of a `workflow_call` workflow** →
  `startup_failure`. Keep run-name/concurrency on the ROUTER (caller), using `github.event.*`.
- **actionlint does NOT catch the permission ceiling** (even project-mode). The LIVE RUN is the
  binding proof for any orchestrator YAML change.

---

## Design

### Component shape (after B→A)

| File | Role | Change |
|------|------|--------|
| `agentic-orchestrator.yml` | **NEW** router: static `on:`, union `permissions` ceiling, workflow-level `concurrency`, a read-only `route` job → conditional `engine` job | created |
| `multi-grumpy-trigger.yml` | per-protocol shim | **deleted** |
| `agentic-engine.yml` | reusable `workflow_call` engine | unchanged (byte-identical) |
| `protocol-join.yml` | serialized AND-barrier, owns `protocol-join` dispatch | unchanged |

### The router workflow — `agentic-orchestrator.yml`

```yaml
name: Agentic Orchestrator
# Engine-owned, protocol-agnostic router. Declares the UNION of all protocols'
# entry triggers (a reusable workflow_call workflow cannot declare `on:` itself),
# runs a read-only `route` job to pick the matching protocol, then calls the
# generic engine. run-name + concurrency live HERE (the caller), github.event-based,
# never `inputs` (which would startup_failure a workflow_call workflow).
run-name: "agentic · ${{ github.event.client_payload.instance || format('pr-{0}', github.event.issue.number || github.event.pull_request.number) }}"

on:
  pull_request:
    types: [opened, synchronize, reopened]
  issue_comment:
    types: [created]
  repository_dispatch:
    types: [protocol-continue, protocol-advance]   # protocol-join owned by protocol-join.yml

# The calling job's permissions are the CEILING for the reusable engine. Grant the
# UNION the engine's jobs need; the engine's per-job permissions scope down within it.
# (State writes use POC_DISPATCH_TOKEN, not GITHUB_TOKEN, so contents stays read.)
permissions:
  contents: read
  issues: write
  pull-requests: write
  checks: write
  actions: read

concurrency:
  # Static prefix + instance + branch. Protocol is unknown at concurrency-eval time,
  # so all protocols share this namespace (fine for one-pipeline-per-repo). branch is
  # empty for entry events / agent phases; set for fan-out branch continues.
  group: agentic-${{ github.event.client_payload.instance || format('pr-{0}', github.event.issue.number || github.event.pull_request.number) }}-${{ github.event.client_payload.branch }}
  cancel-in-progress: false

jobs:
  route:
    # Read-only. Picks the protocol path (entry events) or passes through the
    # dispatch protocol; decides skip. Holds NO state PAT.
    runs-on: ubuntu-latest
    outputs:
      protocol: ${{ steps.r.outputs.protocol }}
      skip:     ${{ steps.r.outputs.skip }}
    steps:
      - uses: actions/checkout@v4
      - id: r
        env:
          EVENT_NAME:        ${{ github.event_name }}
          PR_EVENT_ACTION:   ${{ github.event.action }}
          COMMENT_BODY:      ${{ github.event.comment.body }}
          IS_PR_COMMENT:     ${{ github.event.issue.pull_request != null }}
          DISPATCH_PROTOCOL: ${{ github.event.client_payload.protocol }}
        run: |
          # Agent-derived strings (COMMENT_BODY, DISPATCH_PROTOCOL) are read via env,
          # NEVER interpolated into this run: block. lib.route prints {protocol,command,skip}
          # to stdout and exits non-zero only on a genuine error (incl. ambiguous match).
          # An issue_comment on a non-PR issue → skip (engine ignores it anyway).
          python3 .github/agent-factory/engine/lib.py route \
            .github/agent-factory/protocols "$EVENT_NAME" "$PR_EVENT_ACTION" \
            >> "$GITHUB_OUTPUT"
          # (exact CLI arg wiring — comment body, dispatch protocol, is-pr-comment —
          #  finalized during implementation; all agent strings flow via env.)
  engine:
    needs: route
    if: ${{ needs.route.outputs.skip != 'true' }}
    uses: ./.github/workflows/agentic-engine.yml
    with:
      protocol: ${{ needs.route.outputs.protocol }}
    secrets: inherit
    permissions:
      contents: read
      issues: write
      pull-requests: write
      checks: write
      actions: read
```

> The exact mechanics of passing `COMMENT_BODY` / `DISPATCH_PROTOCOL` / the is-PR-comment guard
> into the `lib.py route` CLI are an implementation detail finalized in the plan. The binding
> contracts are: (a) all agent-derived strings flow via `env:`, never `run:` interpolation;
> (b) the CLI prints `protocol=`/`skip=` lines consumable by `$GITHUB_OUTPUT`; (c) the engine
> job is gated on `skip != 'true'`.

### `lib.route` — the testable core (TDD'able)

```
lib.route(protocols_dir, event_name, action="", comment_body="",
          dispatch_protocol="", is_pr_comment=True) -> {"protocol": str, "command": str, "skip": bool}
```

Behavior:

1. **repository_dispatch** (`dispatch_protocol` non-empty): return
   `{"protocol": dispatch_protocol, "command": "", "skip": False}`. The engine re-derives the
   command from the dispatch type; `route` does not.
2. **issue_comment on a non-PR issue** (`is_pr_comment` false): return `{"skip": True}` — the
   engine ignores these anyway; short-circuit so we never scan.
3. **entry event** (pull_request, or issue_comment on a PR): glob `protocols/*/protocol.json` in
   **sorted** order, load each, run `match_trigger(proto, event_name, action, comment_body)`.
   Collect every protocol whose result is a non-empty command.
   - **0 matches** → `{"skip": True}`
   - **exactly 1** → `{"protocol": <path>, "command": <cmd>, "skip": False}`
   - **≥2 matches** → **raise `ValueError`** naming the conflicting protocol paths. The CLI catches
     it, prints the message to stderr, and exits non-zero → the `route` job fails loudly.

CLI: a new `lib.py route` subcommand (sibling to `match-trigger`/`agent-workflow`). It loads each
`protocol.json` from disk, calls `lib.route`, and on success prints `$GITHUB_OUTPUT`-style lines
(`protocol=...`, `skip=...`); on `ValueError` it exits non-zero. `command` is computed for
parity/testing but the engine does not consume it from `route`.

**`lib.match_trigger` and `lib.agent_workflow` are unchanged** — `route` composes `match_trigger`.

### Unit tests (new `tests/test_route.py`, in the spirit of `test_triggers.py`)

- `repository_dispatch` passthrough → returns `dispatch_protocol`, `skip=False`.
- entry event matching exactly one protocol → that path + command.
- entry event matching no protocol → `skip=True`.
- **two protocols matching the same event → raises `ValueError`** (the locked Decision 1).
- issue_comment on a non-PR issue → `skip=True` (no scan).
- sorted/deterministic globbing (fixture with ≥2 protocol dirs).
- CLI smoke: `lib.py route` prints the expected `$GITHUB_OUTPUT` lines; exits non-zero on ambiguous.

Use a `tmp_path` fixture that lays down a small `protocols/` tree with 2–3 minimal `protocol.json`
files (only the `name` + `triggers` blocks are needed) — mirrors `tests/fixtures/pipeline-mini/`.

---

## Verification plan

B→A is YAML — same regime as M2b (pytest + actionlint are necessary but NOT sufficient; the live
run is binding).

- **pytest:** new `tests/test_route.py` passes + the full regression suite stays green
  (currently **223 tests**). Engine/join/checks tests must be untouched (proves the engine stayed
  byte-identical).
- **actionlint** (ephemeral; reinstall per session):
  `GOBIN=/tmp/gobin go install github.com/rhysd/actionlint/cmd/actionlint@latest`, then
  `/tmp/gobin/actionlint` in PROJECT mode (no args) to cross-validate the reusable call.
  **REMEMBER: actionlint will NOT catch the permission-ceiling startup_failure.**
- **LIVE RUN — binding proof.** After merge to `main`, trigger BOTH paths against a PR:
  1. `pull_request: opened` (or reopened) — router must pick `multi-grumpy`, engine runs the full
     fan-out (grumpy + security legs), aggregate + sub check-runs, single shared status comment,
     join completes.
  2. `/grumpy` `issue_comment` — same outcome.
  Result must be **identical to the M2b live result** (PR #55, run 27654871019). To read a
  `startup_failure`'s real cause, use the GitHub web UI run page (the API hides it).
  Reuse PR #55 / branch `m2b-live2` (the throwaway `examples/clamp.py` diff) as the live PR, or open
  a fresh one.

## Risks

- **Repo-wide firing:** the router fires on EVERY pull_request/issue_comment, then no-ops fast when
  no protocol matches. CI noise — acceptable; the `route` job `log`s the skip reason.
- **Shared concurrency namespace:** all protocols share `agentic-<instance>-<branch>` (protocol
  can't be in the key). With one pipeline per repo this is correct; two different protocols on the
  same PR would serialize against each other. Acceptable for the PoC; revisit if multi-pipeline.
- **Ambiguous-match failure is intentional** (Decision 1): a second protocol that matches the same
  trigger turns a config slip into a red `route` job rather than a silent wrong-protocol run.
- **Permission ceiling is the classic trap:** the router's `engine` job AND workflow-level
  `permissions` must both grant the union. actionlint won't warn — the live run is the check.

## Cleanup checklist

- Delete `.github/workflows/multi-grumpy-trigger.yml`.
- Confirm no other workflow `uses:` the deleted shim and nothing references its `name:`.
- Update `CLAUDE.md` (the architecture map currently lists `orchestrator.yml`/`multi-grumpy-trigger.yml`)
  and `docs/STATUS.md` / `docs/BACKLOG.md` to describe the router. (Docs task — optional but
  recommended, keep it in the plan.)

## Suggested execution path (superpowers)

`writing-plans` → `subagent-driven-development`. Branch: `feat/orchestrator-b-to-a`.
Tasks roughly:
- **T1** — `lib.route` + `lib.py route` CLI + `tests/test_route.py` (TDD; the testable core).
- **T2** — `agentic-orchestrator.yml` router (honoring the two permission/concurrency gotchas).
- **T3** — delete `multi-grumpy-trigger.yml`; sweep for references; docs update.
- **T4** — actionlint (project mode) + LIVE equivalence run (PR-open AND `/grumpy`), confirm
  identical-to-M2b outcome.

Keep the engine byte-identical throughout (a diff to `agentic-engine.yml` is a red flag to stop).
