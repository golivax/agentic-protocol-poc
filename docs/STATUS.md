# PoC — Status & Deviations

This records what the PoC actually is, measured against the original design
spec (`agent-factory/docs/superpowers/specs/2026-06-10-...`) and plan. Read it
before extending the system so you know which "missing" pieces are deliberate.

**Shipped protocol (as of 2026-06-20):** `code-review`
(`.github/agent-factory/protocols/code-review/`) — a multi-phase pipeline:
`preflight` (agent, pre-flight gate) → `review` (fanout: `grumpy` + `security` legs)
→ `join` (AND-barrier) → `approval` (human gate). This is the sole protocol in
`.github/agent-factory/protocols/`. The demo protocols `grumpy` (single-agent) and
`multi-grumpy` (single-phase fanout) were retired into `tests/fixtures/single-agent/`
and `tests/fixtures/fanout-mini/` on 2026-06-20 as engine regression fixtures.

**Custody-pipeline migration (2026-06-26):** the production `code-review` protocol
above was superseded by the full **custody-story pipeline** backported from
`golivax2/yuanrong-datasystem@main`. The original grumpy/security/approval example is
preserved verbatim as **`code-review-v1`** (start/override triggers renamed to
`/v1-review`/`/v1-override` so `code-review` keeps `/review`+`/override`; its gate
commands `/approve`·`/request-changes`·`/reject` are unchanged). The new `code-review`
is `preflight → overview → review` (5-leg fanout) `→ join → triage → fix → context →
mrp → done` on Codex agents, with Bun/Node/Z3 toolchains vendored under its `scripts/`
(outside the engine's Python-only contract — those phase checks degrade to advisory
when a toolchain is absent). Engine backport was **cherry-picked, not bulk-copied**
(a bulk copy would delete the engine-only `protocol-lint.py`/`protocol.schema.json`):
`next.py` iterate-state-preserve, `advance.py` conclude-`inputs[]`, a now-generic
`lib._evidence_status_note` (config-driven via a fanout's `params.status_note` — no
protocol vocabulary in the engine), and a new `prefetch-review.py` + the
`agentic-engine.yml` preflight prefetch block.

**Mental-model phases (2026-06-26, same PR):** the two mental-model agents are wired
INTO `code-review` as phases (not standalone): `preflight → mm-compliance → overview`
(a BLOCKING gate — `conclude-mm-compliance` halts on `verdict:diverges` until
`/override`), and `fix → fanout[context, mm-updater→mm-gate] → join → mrp` (context and
the MM-updater run in parallel; `mrp` waits the join). The empty-data-gate auto-skip
(`advance.py`) makes `mm-updater` engage the human gate only when it opened an `[mm]`
PR — a `mm-questions-present` check forces a question when `mm_changed`, and the
auto-skip fires only on an EXPLICIT empty list (fail-closed on missing/null/garbled).
The gate uses the protocol's `/mm-answer` trigger (`/answer` is owned by
`recover-mental-model-stub`; `open_gate` now derives the prefix from the protocol).
**Deliberate deviations:** (1) `mm-updater` carries a gh-aw `create-pull-request`
**write** safe-output on an engine leg — the agent stays read-only (zone 2) and
gh-aw's trusted post-job performs the write, so it emits intent rather than writing
directly; the normal zone-4 publish-hook path doesn't reach sub-pipeline leg agents.
(2) The production `code-review` has **no final human approval gate** — it drives to
`mrp → done` (the merge-readiness pack is the artifact); `/approve`·`/request-changes`·
`/reject` live on `code-review-v1`. (3) `context` is an advisory fanout leg, so
`conclude-context.py` does not run (a fanout leg has no conclude hook); it is retained
for the v1/standalone shape.

**Two-top-level-fanout join (2026-06-26 fix):** `code-review` is the first shipped
protocol with two top-level fanouts (`review`, `post-fix`). The top-level join uses an
instance-wide `_instance.yaml joined` flag; `next.py`'s fanout-entry now resets it when
a top fanout is entered, so the second fanout's barrier fires (else `mrp` was never
dispatched — a hard stall). Covered by `tests/fixtures/two-fanout` + a regression test.

The v1/v2/v3/v4 milestone sections below are a dated record of what was built
in sequence. Protocol names (`grumpy-review`, `multi-grumpy`) inside those
clearly-historical sections refer to the protocols as they existed at that
milestone; they are now retired.

## Nested sub-workflow branches + data-carrying gate (engine — in progress, 2026-06-22)

Design: `docs/superpowers/specs/2026-06-22-nested-subworkflow-branches-design.md`;
implementation Plans 1–4 under `docs/superpowers/plans/2026-06-22-plan-*.md`.
Unlocks the target protocol **`recover-mental-model-stub`** (one automated leg ∥
one human-gated leg → join → combine).

Engine capability (Python + pytest layer) being built on branch
`feat/nested-subworkflow-branches`:

- **Sub-pipeline branches** — a fanout `branch` may be a linear sub-pipeline
  (`states: [...]`) the engine sequences with its own cursor (`<branch>.yaml`
  carrying `sub_state`) + per-step files (`<branch>.<substate>.yaml`). Same
  seed/advance loop as top-level phases, one scope deeper (`SUBSTATE` env var).
- **Inputs channel + output persistence** — states persist their `evidence.json`
  beside their state file; a state may declare `inputs:[{from,as}]` resolved by
  `lib.resolve_inputs` and staged as `inputs/<as>.json`.
- **Data-carrying gate** — a gate with `questions_from` renders agent-emitted
  questions; `/answer qID: value` accumulates answers, an `answers-coverage`
  check gates resumption, and the answers artifact feeds the next sub-state.
- **Combine/merge state** — `join` advances to its `.next`; `kind:"merge"` runs a
  trusted reduce hook, or an agent combine, or publish-only.

Exercised by the `tests/fixtures/subpipeline-mini/` fixture (single-phase fanout:
A flat ∥ B `draft → clarify → finalize` → join → combine). **Deferred:** the
GitHub-Actions workflow wiring (input artifact staging, `/answer` routing, merge
job env) — see `docs/superpowers/notes-deferred-workflow-integration.md`; it lands
on `main` per the workflow-on-default-branch rule.

### Recursive sub-pipelines (arbitrary depth) — engine done

A sub-state may itself be a fanout or sub-pipeline, to arbitrary bounded depth.
The engine carries one variable-length **node-path** (`NODE_PATH` env) instead of
the fixed `(phase, branch, substate)` triple; `next.py`/`advance.py`/`join.py`
enter, advance, and bubble joins recursively (path-keyed `<fanout>.__join.yaml`
markers for nested fanouts; the top fanout keeps `_instance.yaml`). A
configurable `max_depth` (default **5**) bounds the static tree. The `/answer`
handler is recursive too: `_find_open_gate` follows live cursors to a gate at any
depth, and `do_answer` advances the enclosing sub-pipeline cursor / fires the
enclosing (nested) join. Exercised by `tests/fixtures/deep-fanout/` (depth-4 walk)
and `tests/fixtures/gate-deep/` (depth-5 nested gates).

### Stage 4a — unified recursive engine (engine + pytest, done on branch `feat/stage4-recursive-engine-unification`)

The engine is now a **single recursive code path** for all protocol shapes. The
root of every protocol is treated as a `sequence` node; `start`/`reset` commands
enter via `enter_root`; every phase transition, the top-level join, the approval
gate, and merge/combine steps are driven by the recursive sequencer on the
**`NODE_PATH`** coordinate alone. `NODE_PATH` is now **required** by
`advance.py` and `join.py` — the old bespoke `(BRANCH, PHASE, SUBSTATE)` triple
and the machinery that used it are deleted.

**`protocol-advance` repository_dispatch type retired.** Phase-to-phase transition
is now "continue at the next sibling path" — a `NODE_PATH` update, not a named
dispatch type. Nothing fires a `protocol-advance` event; nothing in the engine
listens for one.

**Legacy multi-phase machinery deleted.** The `start_fanout`, `seed_and_dispatch_phase`,
and the bespoke single-agent/phase-transition code paths that lived alongside the
recursive engine are gone. All protocol shapes (single-agent, simple fanout,
multi-phase, sub-pipeline, deep nested) go through the same `enter_root` → recursive
enter/advance/join stack.

**Cursor layout.** The root cursor lives in `_instance.yaml` under the `phase` key;
nested cursors live in `<seq>.yaml` (one per sequence node, keyed by path segment).

**Authoring-error validation.** `lib.validate_protocol` checks common protocol
authoring errors — a `join` whose `of` names no in-scope fanout, an `agent`
node (or flat fanout branch) missing its `workflow`, and a `gate` whose
`questions_from` names a non-existent sibling — and emits **actionable error
messages** naming the offending node id plus a fix hint, failing fast (exit 2)
before any state is written. This is a release-bar requirement: protocol authors
outside this PoC must get clear feedback on malformed protocols.

**Deploy requirement.** There is **no in-flight state migration.** Any PR that was
mid-run when this branch deploys will have state in the old `(BRANCH, PHASE, SUBSTATE)`
layout, which the new engine cannot resume. A fresh `/review`, `/recover`, or
equivalent trigger is required after deploy to start a clean run.

**Test count.** 417 tests across all modules, all green (401 after Stage 4a; +16
from Stage 4b's emit + workflow-contract + run-checks-NODE_PATH tests). The
capability suite covers: single-agent, simple fanout, multi-phase, sub-pipeline,
depth-4/5 deep trees, data-carrying and approval gates, `/override`, restart/reset,
inputs channel, merge/combine, `max_depth` guard, authoring-error validation, and
security (agent-derived string injection paths).

### Stage 4b — GitHub Actions NODE_PATH wiring (DONE, merged to `main`)

The three workflows now drive the engine on the single `NODE_PATH` coordinate:
`agentic-engine.yml` matrix axis is `leg:{path,workflow}` fed from `action.legs`
(each leg carries its leaf agent path + workflow); `NODE_PATH` is threaded into
dispatch/checks/advance; the `ctx` step parses `client_payload.path`; artifacts are
path-keyed; the `(BRANCH, PHASE, SUBSTATE)` env wiring and `lib.agent-workflow`
leg-resolution are gone. `protocol-join.yml` threads `NODE_PATH` + path-aware
concurrency; `agentic-orchestrator.yml` is path-concurrency-keyed and dropped
`protocol-advance` from `on:`. `run-checks.py` gained a `NODE_PATH` mode (fail-loud
on an unresolvable path). A `lint.yml` runs `actionlint` (shellcheck capped at
error severity) on the hand-written workflows. Spec/plan:
`docs/superpowers/{specs,plans}/2026-06-24-stage4b-gha-wiring*`.

### Stage 4c — live verification (DONE, on `main`, pushed to origin)

A live depth-4 `deep-review-stub` protocol (mirrors `deep-fanout`) + 5 gh-aw stub
agents were added and **walked end-to-end on real GitHub Actions** (PR #88):
preflight fanout → `deep` sub-pipeline → nested `analyze` fanout → bubbling joins →
`report` → done, with path-named check-runs. `code-review` (PR #62) and
`recover-mental-model-stub` (PR #82) were **re-verified live** on the unified
engine (code-review → approval gate + self-approve guard; recover → fanout +
sub-pipeline `/answer` data-gate + merge → done, including recovery from a
transient agent failure via the iterate loop). Spec/plan:
`docs/superpowers/{specs,plans}/2026-06-24-stage4c-*`.

**Four live-only bugs found + fixed during 4c** (the offline layers could not
catch these — each is a job-context/interaction the ENGINE_LOCAL stubs hide): (1)
the `lint.yml` actionlint gate was red on pre-existing shellcheck style nits →
capped at error severity; (2) `protocol-join.yml` lacked `GH_TOKEN` — the unified
`join.py` now *dispatches* `protocol-continue`/`-join` (pre-4a it ran inline),
which the default token cannot do → added the dispatch PAT; (3) `do_answer`'s
top-level data-gate arm pre-seeded the next sub-state's file, which the continue
then re-seeded → empty-commit `cas_push` failure → it now advances the cursor only
and lets the continue seed; (4) the `merge`/combine hook reads `PR` from the env
to post its combined comment, but the unified merge runs from `next.py` in the
plan job (no `PR`; pre-4a it ran in `protocol-join.yml`, which set it) → the
combined comment silently dropped → `run_merge_hook` now derives `PR` from the
instance for the hook. **Backlog (cosmetic/minor):** a depth-1 agent-phase
check-run is named `<pid>/` (trailing slash) because `cr_name = pid + "/" +
"/".join(tree_path[1:])` is empty at depth 1; re-running a *terminal* merge
(manual re-fire) fails the final `cas_push` with an empty commit.

## Proven end-to-end (real GitHub Actions)

- Engine: `next.py` (planner), `advance.py` (sole state writer + publisher),
  `lib.py` (compare-and-swap push to the state branch).
- Three deterministic checks: `schema-valid`, `rubric-coverage`,
  `traces-exist-in-diff`.
- Evidence-schema-as-contract (`.github/agent-factory/protocols/code-review/*.evidence.schema.json`).
- Four-trust-zone orchestrator (plan → dispatch → checks → advance).
- Bounded iterate-with-feedback (`max_iterations`).
- Durable state on the `agentic-state` branch, one file per PR, advanced by
  fast-forward (CAS) push.
- Two acceptance demos: PR #4/#9 (sabotage → iterate → pass), PR #7 (clean
  negative control); native line-anchored inline review live-verified on PR #21.
  315 tests across eleven pytest modules: `test_checks.py` 39,
  `test_engine.py` 52, `test_runchecks.py` 19, `test_publish.py` 13,
  `test_correlation.py` 6, `test_status_comment.py` 11, `test_join.py` 10,
  `test_fanout_e2e.py` 4, plus the v3/v4 additions `test_multiphase.py`,
  `test_override.py`, `test_pipeline_status.py`, and `test_gate.py`. Run all:
  `pytest tests/ -q` (shared fixtures live in `tests/conftest.py`).

## Simplifications declared up front (in the plan)

1. **Agent output is only `evidence.json`.** `advance.py` derives the PR review
   deterministically from checked evidence (any `issues-found` →
   REQUEST_CHANGES, else APPROVE). The spec's alternative — the agent emits
   staged safe-outputs that advance executes — was dropped. Same guarantee
   ("nothing reaches the PR until checks pass"), one fewer format, and the
   published review provably matches the evidence.
2. **Publication uses `GITHUB_TOKEN`** (github-actions bot), not the PAT —
   GitHub forbids a PR author from formally reviewing their own PR, and the PAT
   belongs to the author.
3. **The orchestrator polls the agent run** (`gh run watch`) rather than ending
   and being re-woken by a completion callback. One orchestrator run therefore
   spans plan→dispatch→wait→checks→advance for a *single* iteration; only the
   iteration boundary crosses a run (via `repository_dispatch`). The
   "every transition is its own run" ideal is partially compromised to avoid
   `workflow_run` callback plumbing.
4. **All check failures route to `iterate`.** The `repair` and `drop` failure
   rungs from the design are not implemented.
5. **Findings quote `existing_code`, not line anchors.** No snippet→line
   position resolution (the OCR-style "positioning" module is out of scope).
   **(SUPERSEDED 2026-06-11:** findings now carry a verified `side`/`line`[/`start_line`]
   anchor and grumpy posts native inline review comments; the anchor is verified
   by `traces-exist-in-diff.py`. Live-verified on PR #21.**)**

## Deviations discovered during live integration (not in the plan)

6. **The agent's egress firewall is disabled** (`sandbox.agent: false` in
   `grumpy-agent.md`). gh-aw's firewall api-proxy is built for public
   Anthropic/Copilot endpoints and could not authenticate to the custom
   Anthropic-compatible endpoint; the agent now calls the endpoint directly via
   `engine.env`. **This is the biggest weakening.** The *credential* separation
   across the four zones is intact (the agent holds only read-only
   `contents`/`pull-requests` tokens and the LLM creds, never the state-branch
   PAT), but the agent's *network egress* is no longer restricted by AWF.
   Mitigations in place: read-only job token, read-only GitHub MCP, private
   repo. Restoring the firewall for a publicly-reachable, standard endpoint is
   a future hardening (still open after v2/v3).
7. **Model pinned** to `claude-sonnet-4-6` (endpoint-specific).
8. **Sabotage label read via the PAT** — reading PR labels needs the
   `pull-requests` scope; the default `GITHUB_TOKEN` 403s.
9. **APPROVE→COMMENT fallback** in `advance.py` — the repo's "Allow GitHub
   Actions to approve pull requests" setting is off, so a fully-clean result
   degrades to a COMMENT review instead of APPROVE.
10. **Accumulating status comment** — added after the demos, at user request:
    the single status comment is re-rendered from `state.history` each
    transition into a per-iteration checklist + a link to the state file.
11. **Agent run-id resolver** was "newest `workflow_dispatch` run since T0".
    Correct only one-PR-at-a-time: the gh-aw agent workflow uses a *global*
    concurrency group, so two PRs reviewed concurrently could misattribute
    runs. **(RESOLVED in v3:** the resolver now matches a per-dispatch
    correlation id stamped into the run's displayTitle and fails loudly on no
    match — see the §"Concurrency — correlation-id resolver" section below and
    `BACKLOG.md`. v2's fan-out *within* one PR was already safe without it — see
    the v2 section's concurrency note.**)**

## Post-v1 enhancements (added after the demos)

- **Polyglot, data-driven checks.** `engine/run-checks.py` reads the check list
  from `protocol.json` (`.states[].checks[]`) and resolves each to an executable
  in any language (`exec` path, or `checks/<name>.*` extension-agnostic). The
  orchestrator no longer hardcodes the check names. The ABI is language-agnostic;
  here all three checks (`schema-valid.py`, `rubric-coverage.py`,
  `traces-exist-in-diff.py`) happen to be Python — same ABI. New test module
  `tests/test_runchecks.py` (19 tests) covers resolution and robustness
  (missing / non-executable / crashing / ambiguous).

- **Merge-gating via a check run.** `advance.py`/`plan` emit a `code-review`
  check run on the PR head SHA reflecting protocol state (in_progress while
  reviewing, `action_required` on changes-requested, `success` on clean,
  `failure` on exhausted). Emitting works on any repo; *blocking* the merge
  requires making `code-review` a required status check in branch protection /
  rulesets (needs a public repo or a paid plan for private). `lib.py`
  `set_check_run`; engine tests assert the three outcomes.

- **Auto-review on open/push (`pull_request` trigger).** The orchestrator now
  triggers on `pull_request` `opened`/`synchronize`/`reopened`, so every PR is
  reviewed on open and re-reviewed on each push (not only on `/grumpy`). A
  `synchronize` event maps to the `reset` command, which unconditionally starts
  a fresh review (the prior review stays in the state branch's git history).
  The engine does not compare head SHAs — that policy is entirely in the
  orchestrator's event→command mapping. Required for the check-run merge gate
  to be coherent (otherwise un-`/grumpy`'d PRs would block forever).
  **Same-repo PRs only** — fork PRs get no secrets and need
  `pull_request_target` + sandboxing, deliberately out of scope.
- **Check-run conclusion is `failure` (not `action_required`) for
  changes-requested.** `action_required` made GitHub render a phantom "workflow
  awaiting approval" prompt on the PR with a broken "Approve workflows to run"
  button; `failure` blocks the merge identically without the confusion.

## Engine couplings — resolved

The engine carries no protocol-specific logic. The only `grumpy` token
remaining under `.github/agent-factory/engine/` is the legitimate leg-file example
`review.grumpy.yaml` in a comment in `next.py` — the state-file name for
`code-review`'s `grumpy` review leg, not a reference to any deleted protocol.
The two previously noted couplings are gone:

- **Protocol id from data.** `next.py` and `advance.py` read the protocol id
  from `protocol.json` `.name` via `protocol_id()` in `lib.py`. The check-run
  name, status-comment headline, and state-path prefix all derive from it.
- **State path from data.** `lib.py`'s `state_file` returns
  `<dir>/<protocol-id>/<instance-key>/<phase>.yaml` (e.g. `code-review/pr-<N>/preflight.yaml`)
  for the multi-phase form used by `code-review`; the single-agent fixture form is
  `<protocol-id>/<instance-key>.yaml` (e.g. `single-agent/pr-<N>.yaml`).

**Trigger policy lives in `agentic-orchestrator.yml` (router) and `agentic-engine.yml` (engine), not the engine scripts.** `next.py`
accepts a command (`start` / `reset` / `continue`); the orchestrator maps
GitHub events to commands:

| GitHub event | Command |
|---|---|
| `pull_request` opened / reopened | `start` |
| `pull_request` synchronize | `reset` |
| `issue_comment` `/review` | `start` |
| `repository_dispatch` `protocol-continue` | `continue` |

The `start`/`reset`/`continue` action matrix (Absent / Active / Terminal):

| Command | Absent | Active | Terminal |
|---|---|---|---|
| `start` | fresh review | halt | fresh re-review |
| `reset` | fresh review | fresh review | fresh review |
| `continue` | fresh review | resume current iteration | halt |

Two intentional v1 behavior divergences (documented, not defects):
- `start` on Terminal → fresh re-review (prior design: halt).
- `start` on Active → halt (prior design: resume).

**Publication is a protocol publish hook.** `advance.py` resolves and calls the
per-branch publish hook from `.github/agent-factory/protocols/code-review/publish/` via
`resolve_executable` (the same mechanism as checks). The hook runs trusted in
zone 4 (engine-post) holding the publish token; it is not a sandboxed check.

## v4 — /override escape-hatch (implemented)

The `/override` comment trigger is implemented and live-verifiable (see spec:
`docs/superpowers/specs/2026-06-17-hitl-override-gate-design.md`).

**What it does:** a write-access human comments `/override` on a PR whose pipeline
is **blocked** at a halt-gate; the engine advances the cursor exactly **one phase**
and dispatches it. An optional free-text reason may follow: `/override <reason>`.

**Blocked-only scope.** A gate that *exhausted* (could not produce schema-valid
evidence within `max_iterations`) is **not** overridable; `/override` on it returns
a distinct refusal message explaining the difference and makes no state change.

**Authorization.** The commenter's login is read from the trusted event context
(`github.event.comment.user.login` — never from the comment body). Permission is
verified via the GitHub collaborators API (`GET /repos/{owner}/{repo}/collaborators/{login}/permission`);
the override proceeds only if permission ∈ `{write, admin}`. Unauthorized attempts
receive an explanatory denial comment; no state changes.

**Audit trail — verdict never rewritten.** The blocked gate's `state: failed` and
its `failure` check-run are **never touched** — they remain truthful. The override
is recorded *beside* the failure:
- `_instance.yaml` gains an `overrides[]` entry (`{phase, actor, reason}`) and
  loses the `halted` marker.
- The CAS commit message names actor + phase, so
  `git log agentic-state -- <protocol>/<instance>/_instance.yaml` is a complete
  override audit.
- A dedicated status comment posts `⚠️ … gate was blocked — overridden by @<login>`.

**Not shipped in this milestone:** the broader `kind:"gate"` pause-and-require
approval state (a human sign-off as a *required* transition) — that remains the
`BACKLOG.md` v4 item, not started.

## v4 — kind:"gate" approval state (implemented)

The pause-and-require gate is implemented and wired into `code-review`
(see spec: `docs/superpowers/specs/2026-06-17-v4-approval-gate-design.md`).

**What it is.** A `kind:"gate"` phase in `protocol.json` dispatches **no agent
and runs no checks** — zero LLM cost. When the cursor lands on a gate, the engine
seeds the per-phase state file, emits an `in_progress` check-run ("⏳ Awaiting
human approval — comment `/approve`, `/request-changes`, or `/reject`"), and the
run **ends**. State is durable; the gate can sit open for days at no compute cost,
and the pending check-run keeps the merge blocked the entire time.

**Per-phase gate file.** Each `kind:"gate"` phase gets its own state file
(`<instance>/<gate-id>.yaml`) carrying the reserved `gates:` field — its first
real use. `gates.state ∈ {open, changes_requested, approved, rejected}`;
`gates.history` is an append-only list of every decision (`{decision, actor,
reason}`), never overwritten. `_instance.yaml` keeps only the cursor and the
existing cross-phase keys.

**Opening the gate.** Two paths: `seed_and_dispatch_phase` in `next.py` opens a
gate when the cursor advances into a `kind:"gate"` phase; `join.py` opens a
following gate (its `.next` points to one) instead of finalizing, so the join
barrier and the gate compose cleanly. Both paths write `gates.state: open` and
emit the `in_progress` check-run.

**Resolving the gate: the `resolve-gate` command.** `/approve`, `/request-changes`,
and `/reject` PR comments route through the existing `issue_comment → match_trigger`
seam to the `resolve-gate` command. Authorization mirrors the `/override` gate:
the commenter must have `write` or `admin` repo permission (verified via the GitHub
collaborators API; identity from the trusted event context, never the comment body).
Self-approval is forbidden when `approve_excludes_author: true` is set on the phase.

| decision | effect |
|---|---|
| `approve` | `gates.state: approved`; check-run → `success`; cursor advances (or `done` if last) |
| `request-changes` | `gates.state: changes_requested`; check-run → `failure`; **no cursor move, no `halted` marker** |
| `reject` | `gates.state: rejected`; phase `state: failed`; check-run → `failure`; **terminal, no `halted` marker** |

A `changes_requested` gate is resolvable: a later `/approve` flips it to
`approved` and advances the cursor. `reject` is terminal: a later `/approve` is
refused. Both non-terminal and terminal "no"s are revived by a new commit
(existing `synchronize → reset` reruns the whole pipeline).

**`/override` deliberately does NOT apply to gate decisions.** Neither
`request-changes` nor `reject` writes the `halted:{reason:blocked}` marker that
`/override` understands. A human "no" is overturned only by another human or a
new commit — not by an operator override. `/override` is for agent/check blocks
only; gate decisions are a separate authority channel.

**Demo layout.** The `code-review` protocol is
`preflight → review fan-out → join → approval (gate) → done`. The `join.py`
AND-barrier opens the approval gate once all branches reach `done`; a human must
explicitly approve before the aggregate pipeline check-run goes green.

**Tests.** `tests/test_gate.py` (new module): open / approve / request-changes /
reject / guards (unauthorized, self-approve, no live gate, rejected terminal) /
idempotency. Full suite: 315 tests, all green.

## Not exercised by this PoC (honest gaps)

- **Checks that execute agent-authored code** (e.g. running the project's
  `npm test` as a gate). Grumpy's checks are pure data-inspection of the
  evidence file and the independently-fetched diff, so the design's
  "zone-3 runs agent code with zero credentials" hardening was never tested.
- gh-aw's egress firewall on the agent (disabled, see #6).
- Fan-out/join **shipped in v2** (see the v2 section below); the `kind:"gate"`
  human approval state **shipped in v4** (see §above). Sequential multi-phase
  protocols (beyond the current `preflight → review → join → gate → done` shape)
  are not independently tested beyond the existing pipeline.
- The external web-app projection of the state branch.
- `gh aw compile` changes to understand a `protocol:` block (the engine is
  vendored as repo scripts, not compiled into the lock file).

## Operational gotchas (also in project memory)

- `gh secret set NAME --body -` stores the literal `-`, not stdin. Use
  `--body "$VALUE"`.
- Custom endpoint must be configured via `engine.env` (forwarded to the CLI
  subprocess) with the proxy off; top-level `env:` is not forwarded.
- Workflows run from the **default branch** for `issue_comment` /
  `repository_dispatch` — keep `agentic-orchestrator.yml`, `agentic-engine.yml`, and the agent locks on `main`,
  and never commit them onto a demo PR branch (pollutes the reviewed diff).

---

# v2 — multi-grumpy fan-out/join

v2 adds **multi-agent review**: a new `multi-grumpy` protocol whose single
`review` phase **fans out** to two independent gh-aw workflows — `grumpy` (the
v1 general reviewer, reused verbatim) and a thin `security` stub — each with its
own bounded iterate loop and eager publish, then **joins** them under a strict
AND-barrier that gates the merge. It is live-verified (PRs #25 and #28, below)
and built so the v1 single-agent path stays byte-identical (the regression guard).

## What shipped

- **The `BRANCH` engine seam.** `next.py`, `run-checks.py`, and
  `advance.py` all read a `BRANCH` env var (`lib.py` provides the branch-aware
  `state_file`/`instance_file` helpers they pass it to). **Empty/unset = the original v1
  single-agent grumpy path, byte-identical** (this is the regression guard —
  the whole v1 suite still passes unchanged). **Set = operate on one fan-out
  branch:** its agent unit, its check list, its publish hook, and its own
  per-branch state file. No new code path forks the engine; the same scripts
  read one extra variable.

- **New `fanout` + `join` protocol kinds (Approach C — data-driven).** Each
  branch reuses the v1 single-agent iterate loop *verbatim*; the only new logic
  is the fan-out planner and the join barrier. `.github/agent-factory/protocols/multi-grumpy/protocol.json`
  has a `review` state `kind:"fanout"` with a `branches[]` array (each branch:
  `id`, `workflow`, `evidence`, `max_iterations`, `checks`, `publish`) and a
  `join` state `kind:"join"`. The **security branch drops the `rubric-coverage`
  check** — it runs only `schema-valid` + `traces-exist-in-diff` (it has no
  fixed file×category rubric to cover).

- **Per-branch state layout.** `multi-grumpy/pr-N/<branch>.yaml` — one file per
  branch, each byte-shaped like v1's single-agent state — plus a shared
  `multi-grumpy/pr-N/_instance.yaml` (`head_sha`, `joined` flag). A branch is
  "active" when its state file's `.state == review` (the fan-out state id);
  terminal is `done`/`failed`. **No write contention:** each branch writes only
  its own file, so the v1 `cas_push` rebase-once invariant still holds under the
  matrix.

- **Eager publish ≠ gate (the hybrid).** Each branch publishes its review the
  moment it reaches `done` (grumpy 😤, security 🔒), independently of the other.
  The phase **gate** is a separate strict AND-join: the aggregate `multi-grumpy`
  check-run goes green **only when every branch reaches `done`**. A branch that
  exhausts to `failed` publishes nothing and leaves the aggregate **red**
  ("Review incomplete") → merge loudly blocked. There is no silent gap: a missing
  review always shows as a red gate, never as an absent-but-green one.

- **Axis separation (process outcome vs. review verdict).** `done`/`failed` is
  the **process** axis — did the agent produce evidence that passed its checks
  within `max_iterations`. The review **verdict** (issues-found → CHANGES_REQUESTED
  vs. none-found → APPROVE) is orthogonal. A valid review *with comments* is a
  process **success** and is published normally; its per-branch check-run
  conclusion `failure` then means "changes requested," **not** process failure.
  The strict join gate cares only about the process axis.

- **Three check-runs.** Two informational per-branch runs `multi-grumpy/grumpy`
  and `multi-grumpy/security`, plus the aggregate `multi-grumpy` — the **required
  gating check**. `plan` marks the aggregate `in_progress`; `join.py` completes
  it (success iff all branches `done`, else failure).

- **`join.py`** (new engine script). Reads every branch state file; once all are
  terminal and `_instance.yaml` is not yet joined, sets the aggregate check-run,
  renders the status comment, flips `joined`, and CAS-pushes. **Idempotent.** It
  runs in a dedicated **serialized** workflow `.github/workflows/protocol-join.yml`
  (concurrency `join-<instance>`, `cancel-in-progress: false`), fired by a
  `repository_dispatch: protocol-join` that `advance.py` emits whenever a branch
  reaches a terminal state. `advance.py` also now carries `client_payload[branch]`
  on its `protocol-continue` iterate dispatch.

- **Orchestrator = router + reusable engine.** The `multi-grumpy` protocol is
  selected at runtime by `agentic-orchestrator.yml`: a read-only `route` job calls
  `lib.route` (scans all protocols' `triggers` blocks, matches `github.event`,
  errors loudly if ≥2 match), then calls the reusable `agentic-engine.yml`
  (`on: workflow_call`) with the selected protocol path. Inside the engine,
  `plan` runs `next.py` **unbranched** for `pull_request`/`issue_comment`
  (→ action `run-fanout`, `branches=[grumpy,security]`) and **branched**
  (`BRANCH=<payload.branch> next.py … continue`) for `repository_dispatch:
  protocol-continue` (→ one branch). `dispatch`/`checks`/`advance` are a
  `strategy.matrix.branch` (`fail-fast: false`), gated `if: needs.plan.outputs.branches
  != '[]'`. **Per-branch data (agent run-id, verdicts) flows between the matrixed
  jobs via branch-named ARTIFACTS** (`runmeta-<branch>`, `verdicts-<branch>`),
  **not** job `outputs` — because GHA matrix legs share one outputs map and the
  last leg clobbers the others. The four v1 trust zones are preserved per leg
  (plan = engine-pre; dispatch = agent-trigger; checks = read-only ground truth,
  **no write tokens**; advance = sole state writer + publisher).
  The per-protocol trigger shim (`multi-grumpy-trigger.yml`) is deleted; no
  per-protocol workflow YAML remains.

- **The security agent** (`.github/workflows/security-agent.md` → compiled
  `security-agent.lock.yml`) is a thin gh-aw clone of grumpy-agent that emits
  grumpy-shaped evidence with `category:"security"`. It has a **persistent
  sabotage knob:** while the `poc:sabotage` label is present it fabricates a
  finding **every iteration** (→ fails `traces-exist-in-diff` → exhausts to
  `failed`). This contrasts with grumpy's **iteration-1-only** knob (sabotage
  then self-recover). The orchestrator's sabotage step now reports the label
  regardless of iteration; each agent self-decides what to do with it.

- **New tests.** `tests/test_join.py` (join aggregation + idempotency) and
  `tests/test_fanout_e2e.py` (local end-to-end: fanout start → advance ×2 → join
  success). The full local suite is now **8 modules / 154 tests, all green**
  (the four v1 modules — `test_checks.py`, `test_engine.py`, `test_runchecks.py`,
  `test_publish.py` — plus `test_correlation.py`, `test_status_comment.py`, and
  these two).

## Live verification

- **PR #25 — happy path.** Both branches reached `done` on iteration 1; the
  grumpy 😤 and security 🔒 reviews were **both posted eagerly** with inline
  line-anchored comments; two serialized Protocol Join runs fired (idempotent,
  `joined:true`); the aggregate `multi-grumpy` check went **success** (green
  gate). Both per-branch check-runs were `failure` = changes-requested (axis
  separation in action).
  <https://github.com/golivax/agentic-protocol-poc/pull/25>

- **PR #28 — sabotage (`poc:sabotage` label).** grumpy sabotaged on iteration 1,
  self-recovered on iteration 2 → `done` → **published** its 😤 review (5 issues)
  = a partial publish; security emitted the fabricated finding on all 3
  iterations → exhausted to `state:failed` (iteration 3) → posted **no** review;
  the aggregate `multi-grumpy` went **failure** ("Review incomplete") = red gate,
  merge loudly blocked. This is the hybrid policy's money-shot: one branch can
  succeed and publish while the gate still correctly blocks on the other's
  failure, with no silent gap.
  <https://github.com/golivax/agentic-protocol-poc/pull/28>

- **PRs #48 + #49 — v3 correlation-id under concurrency.** Two PRs opened
  seconds apart, both fanning out the same `grumpy` and `security` agent
  workflows. The agents serialize in gh-aw's shared concurrency group, so the
  two grumpy runs coexisted in the resolver's listing window (#49's run was the
  *newer* one) — the exact collision the old "newest since T0" heuristic got
  wrong. Each orchestrator's `dispatch` resolved its **own** run by cid: #48
  (orchestrator `27393102307`) → grumpy `27393110562` / security `27393111753`;
  #49 (orchestrator `27393109816`) → grumpy `27393117099` / security
  `27393117043` — four distinct runs, each matched to `cid:[<orchestrator>-1-<branch>]`.
  Every #48 review comment anchored on `concurrent_a.js`, every #49 comment on
  `concurrent_b.js` — **zero cross-contamination**. Both aggregate `multi-grumpy`
  checks went **success**.
  <https://github.com/golivax/agentic-protocol-poc/pull/48>,
  <https://github.com/golivax/agentic-protocol-poc/pull/49>

## Concurrency — correlation-id resolver (v3, implemented)

The agent-run resolver no longer guesses "newest `workflow_dispatch` run since
T0". The `dispatch` job mints a unique correlation id per dispatch
(`<orchestrator_run_id>-<run_attempt>-<branch>`), threads it to the agent via
`aw_context`, and the agent stamps it into its `run-name` (so it appears in the
run's `displayTitle`). The resolver selects the run whose displayTitle carries
the delimited token `cid:[<cid>]` (`match_run_by_cid` in `lib.py`), and **fails
loudly** if no run matches — never falling back to a recency heuristic. This makes
**concurrent PRs of the same workflow** safe: each PR resolves only its own run.

Known limitation (throughput, not correctness): the agent workflow's concurrency
group is `gh-aw-${{ github.workflow }}`, so two PRs running the same agent
*serialize* rather than run in parallel. Correctness is unaffected.
