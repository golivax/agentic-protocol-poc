# Orchestrator B→A: single self-routing workflow (design brief)

**Date:** 2026-06-16
**Status:** Design brief — READY FOR SPEC. Reprioritized to run BEFORE M3 (preflight port),
so the combined `code-review-pipeline` lands in a zero-hand-written-YAML world.
**Author context:** written as a context-clear handoff. A fresh session should be able
to go brainstorm/spec → writing-plans → subagent-driven-development from this file alone.

---

## Goal

Collapse the **N per-protocol trigger shims** (approach B) into **one engine-owned,
self-routing workflow** (approach A). End state: a human authoring a new protocol writes
ONLY (i) the gh-aw agent markdown, (ii) `protocol.json` (with its `triggers` block),
(iii) the checks — and **zero workflow YAML**. The router + engine + join are all
framework-owned and protocol-agnostic.

This is the `docs/BACKLOG.md` "Orchestrator B→A" item, promoted ahead of M3 at the
user's request (2026-06-16).

---

## Where we are now (B, live-proven on origin/main @ 9cb98ca)

- `.github/workflows/agentic-engine.yml` — generic reusable `on: workflow_call` engine.
  Input: `protocol` (path). Runs the 4-trust-zone graph (plan→dispatch→checks→advance)
  for both topologies + multi-phase. Its `plan.ctx` step derives the command from
  `github.event` via `lib.match_trigger` for ITS one protocol. **Largely unchanged by B→A.**
- `.github/workflows/multi-grumpy-trigger.yml` — the per-protocol SHIM (to be DELETED in B→A).
  Carries the static `on:`, `run-name`, `concurrency`, the union `permissions` ceiling, and
  `uses: ./agentic-engine.yml` with `protocol: <multi-grumpy path>` + `secrets: inherit`.
- `.github/workflows/protocol-join.yml` — already generic (reads `client_payload.protocol`).
  Owns the `protocol-join` repository_dispatch type for ALL protocols. **Keep as-is.**
- `lib.match_trigger(protocol, event_name, action="", comment_body="") -> command|""` — maps
  an ENTRY event to a command via the protocol's `triggers` block. Pure. (`lib.py match-trigger` CLI.)
- `lib.agent_workflow(protocol, phase="", branch="") -> name` — resolves a leg's agent
  workflow. (`lib.py agent-workflow` CLI.)
- `triggers` block already in `multi-grumpy/protocol.json` and `grumpy/protocol.json`.
- The engine derives `instance`/`command`/`branch`/`phase` from `github.event` itself.

**Key insight: the engine barely changes.** B→A is almost entirely a NEW router workflow that
decides *which protocol* to invoke the engine for; the engine still does per-protocol command
derivation given a `protocol` input.

---

## The constraint (recap — why static `on:` is unavoidable)

`on:` is parsed by GitHub's control plane at registration time, before any job/runner/checkout
exists — so it can't read `protocol.json`. `on:` also forbids `${{ }}` expressions, and the
expression language has no file I/O. You cannot compute `on:` from repo data. Every solution
just relocates the static bit. (Full reasoning in the conversation that produced this brief.)

## Options evaluated (web-researched 2026-06-16) and decision

1. **Approach A — one self-routing workflow (CHOSEN).** Maximal static `on:` + a runtime
   router that scans all `protocols/*/protocol.json` `triggers` and decides protocol+command.
   No new dependency: the router IS `match_trigger` applied across all protocols, and the
   literal-`uses:` limitation is moot because we have ONE engine.
2. Generated shims (keep B, auto-generate shims via GFlows/ytt/jsonnet + drift-check) — the
   FALLBACK if A's concurrency/routing feels fragile. Adds a codegen toolchain + committed
   generated files, but doesn't touch the working wiring.
3. External router (GitHub App webhook → repository_dispatch) — most flexible, zero repo YAML,
   but requires standing up + operating a service. Overkill for a single-repo PoC.
4. Native features — YAML anchors (Sept 2025) only dedupe within a file; no native dynamic-`on:`.

**Decision: implement A. Fallback to #2 only if a blocking issue appears.**

---

## RESOLVED design facts (do NOT re-research these)

### A job that calls a reusable workflow may use ONLY: `name, uses, with, secrets, needs, if, permissions`
(NOT `concurrency`, `strategy`, `env`, `environment`.) Source: GitHub docs "Supported keywords
for jobs that call a reusable workflow." Implications:
- **Concurrency goes at the ROUTER WORKFLOW level**, using `github.event.*` — exactly the
  expression the current shim already uses and which is live-proven:
  `group: agentic-${{ github.event.client_payload.instance || format('pr-{0}', github.event.issue.number || github.event.pull_request.number) }}-${{ github.event.client_payload.branch }}`.
  This serializes per instance+branch (the router IS the triggered workflow, like the shim was).
- **No `strategy` on the engine-calling job** ⇒ the router handles **ONE protocol per event**
  (the common/PoC case). Multi-protocol fan-out per event would need dispatch-per-protocol
  (deferred). NOTE: the engine's INTERNAL branch fan-out matrix is unaffected — that lives
  inside agentic-engine.yml's dispatch/checks/advance jobs, not on the calling job.
- `with:` to a reusable workflow CAN be an expression (e.g. `protocol: ${{ needs.route.outputs.protocol }}`).
- The engine-calling job CAN set `permissions:` (allowed keyword) and `needs:`/`if:`.

### Reusable-workflow gotchas already learned the hard way in M2b (the live run caught these; pytest + actionlint did NOT):
- **Permission CEILING:** a called workflow can't request more `GITHUB_TOKEN` permission than
  the CALLING job grants → else `startup_failure` before any job runs. The router must grant the
  UNION the engine needs: `contents: read, issues: write, pull-requests: write, checks: write, actions: read`.
- **`inputs` in workflow-level `run-name`/`concurrency` of a `workflow_call` workflow** → `startup_failure`.
  Keep run-name/concurrency on the ROUTER (caller), using `github.event.*`, never `inputs`.
- **actionlint does NOT catch the permission ceiling** (even project-mode). The LIVE RUN is the
  binding proof for any orchestrator YAML change.

---

## Proposed shape (A)

New file `.github/workflows/agentic-orchestrator.yml` (the router), replacing the per-protocol
shim(s). Sketch:

```yaml
name: Agentic Orchestrator
run-name: "${{ github.event.client_payload.instance || format('pr-{0}', github.event.issue.number || github.event.pull_request.number) }}"
on:
  pull_request: { types: [opened, synchronize, reopened] }
  issue_comment: { types: [created] }
  repository_dispatch: { types: [protocol-continue, protocol-advance] }   # join stays in protocol-join.yml
permissions:                 # UNION ceiling for the engine (see gotcha above)
  contents: read
  issues: write
  pull-requests: write
  checks: write
  actions: read
concurrency:                 # workflow-level, github.event-based (allowed; like the shim)
  group: agentic-${{ github.event.client_payload.instance || format('pr-{0}', github.event.issue.number || github.event.pull_request.number) }}-${{ github.event.client_payload.branch }}
  cancel-in-progress: false
jobs:
  route:                     # NORMAL job (read-only): pick the protocol + decide skip
    runs-on: ubuntu-latest
    outputs:
      protocol: ${{ steps.r.outputs.protocol }}
      skip:     ${{ steps.r.outputs.skip }}
    steps:
      - uses: actions/checkout@v4
      - id: r
        env: { COMMENT_BODY: ${{ github.event.comment.body }}, DISPATCH_PROTOCOL: ${{ github.event.client_payload.protocol }}, ... }
        run: |
          # repository_dispatch (continue/advance): protocol comes from client_payload.protocol.
          # entry events (pull_request/issue_comment): scan protocols/*/protocol.json,
          #   run lib.match_trigger on each; first match wins → its path. No match → skip=true.
          # (new helper: lib.route / lib.py route, OR a bash loop over lib.py match-trigger.)
  engine:
    needs: route
    if: ${{ needs.route.outputs.skip != 'true' }}
    uses: ./.github/workflows/agentic-engine.yml
    with: { protocol: ${{ needs.route.outputs.protocol }} }
    secrets: inherit
    permissions: { contents: read, issues: write, pull-requests: write, checks: write, actions: read }
```

**Engine changes:** likely NONE beyond confirming its `plan.ctx` still derives command from
`github.event` given the `protocol` input (it does today). Double-check the engine doesn't
rely on anything the shim used to provide.

**New engine helper (TDD-able, the testable core of B→A):**
`lib.route(protocols_dir, event_name, action="", comment_body="", dispatch_protocol="") -> {protocol, command, skip}`
that, for entry events, globs `protocols/*/protocol.json` and returns the first whose
`match_trigger` yields a command; for repository_dispatch, returns `dispatch_protocol`
directly. Plus a `lib.py route` CLI. Unit-test it like `test_triggers.py` (multiple protocols,
no-match → skip, dispatch path).

---

## OPEN QUESTIONS to lock in the spec (most are pre-decided; confirm)
1. **Multi-protocol match policy:** first-match-wins (PoC default) vs error-on-ambiguous vs
   fan-out. Recommend: first-match-wins + a `log`/warning if >1 matches. (Fan-out needs
   dispatch-per-protocol — defer.)
2. **protocol-join.yml:** keep separate (recommended — already generic) vs fold into router.
   Keeping separate means the router's `repository_dispatch.types` = [protocol-continue, protocol-advance].
3. **Concurrency group key:** instance+branch (current, sufficient for one-pipeline-per-repo)
   vs add a protocol token. Protocol isn't known at workflow-concurrency-eval time, so a static
   prefix + instance + branch is the practical choice. Confirm acceptable.
4. **Naming/cleanup:** new `agentic-orchestrator.yml`; DELETE `multi-grumpy-trigger.yml`.
   Keep `agentic-engine.yml` + `protocol-join.yml`.
5. **Engine input surface:** confirm `protocol` is the only input the engine needs (it derives
   the rest from github.event). If the router must pass command/instance/branch/phase explicitly,
   expand engine inputs — but lean toward NOT (minimal change).

---

## Verification plan (B→A is YAML — same regime as M2b)
- pytest for the new `lib.route` helper + regression suite stays green (currently **223 tests**).
- `actionlint` (install: `GOBIN=/tmp/gobin go install github.com/rhysd/actionlint/cmd/actionlint@latest`
  — curl-to-bash is blocked by the sandbox; /tmp is ephemeral so reinstall per session).
  Run `/tmp/gobin/actionlint` in PROJECT mode (no args) to cross-validate the reusable call.
  REMEMBER: actionlint will NOT catch permission-ceiling startup_failures.
- **LIVE RUN is the binding proof.** After merge to main: trigger via a PR (pull_request:opened)
  AND a `/grumpy` comment (issue_comment); confirm the router picks `multi-grumpy`, the engine
  runs the full fan-out, aggregate+sub check-runs, single shared status comment, join completes —
  i.e. identical to the M2b live result (PR #55, run 27654871019). To read a startup_failure's
  real cause, the GitHub web UI run page shows it (the API hides it).

## Risks
- Repo-wide firing: the router fires on EVERY pull_request/issue_comment, then no-ops fast when
  no protocol matches (CI noise — acceptable; log the skip).
- The single shared concurrency/routing point is where A's complexity concentrates — give it a
  deliberate design pass (mostly resolved above).
- If multi-protocol-per-repo ever matters, revisit fan-out (dispatch-per-protocol).

## Suggested execution path (superpowers)
brainstorm (confirm open questions 1-5) → writing-plans → subagent-driven-development.
Branch: `feat/orchestrator-b-to-a`. Tasks roughly: (T1) `lib.route` + CLI + tests;
(T2) `agentic-orchestrator.yml` router; (T3) delete shim + any wiring; (T4) actionlint + live
equivalence run; (optional) docs. Keep the engine byte-identical where possible.
