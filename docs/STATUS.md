# PoC — Status & Deviations

This records what the PoC actually is, measured against the original design
spec (`agent-factory/docs/superpowers/specs/2026-06-10-...`) and plan. Read it
before extending the system so you know which "missing" pieces are deliberate.
The v1 sections come first; the **v2 — multi-grumpy fan-out/join** section at the
end records the multi-agent milestone (both are live-verified).

## Proven end-to-end (real GitHub Actions)

- Engine: `next.sh` (planner), `advance.sh` (sole state writer + publisher),
  `lib.sh` (compare-and-swap push to the state branch).
- Three deterministic checks: `schema-valid`, `rubric-coverage`,
  `traces-exist-in-diff`.
- Evidence-schema-as-contract (`protocols/grumpy/evidence.schema.json`).
- Four-trust-zone orchestrator (plan → dispatch → checks → advance).
- Bounded iterate-with-feedback (`max_iterations`).
- Durable state on the `agentic-state` branch, one file per PR, advanced by
  fast-forward (CAS) push.
- Two acceptance demos: PR #4/#9 (sabotage → iterate → pass), PR #7 (clean
  negative control); native line-anchored inline review live-verified on PR #21.
  86 local tests across four files: `tests/test-checks.sh` 34,
  `tests/test-engine.sh` 30, `tests/test-runchecks.sh` 14,
  `tests/test-publish.sh` 8.

## Simplifications declared up front (in the plan)

1. **Agent output is only `evidence.json`.** `advance.sh` derives the PR review
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
   a v2 hardening.
7. **Model pinned** to `claude-sonnet-4-6` (endpoint-specific).
8. **Sabotage label read via the PAT** — reading PR labels needs the
   `pull-requests` scope; the default `GITHUB_TOKEN` 403s.
9. **APPROVE→COMMENT fallback** in `advance.sh` — the repo's "Allow GitHub
   Actions to approve pull requests" setting is off, so a fully-clean result
   degrades to a COMMENT review instead of APPROVE.
10. **Accumulating status comment** — added after the demos, at user request:
    the single status comment is re-rendered from `state.history` each
    transition into a per-iteration checklist + a link to the state file.
11. **Agent run-id resolver** is "newest `workflow_dispatch` run since T0".
    Correct only one-PR-at-a-time: the gh-aw agent workflow uses a *global*
    concurrency group, so two PRs reviewed concurrently could misattribute
    runs. A production fix stamps a correlation id into the evidence artifact.
    (Still deferred — now the **v3** milestone; see `BACKLOG.md`. v2's fan-out
    *within* one PR is safe without it — see the v2 section's concurrency note.)

## Post-v1 enhancements (added after the demos)

- **Polyglot, data-driven checks.** `engine/run-checks.sh` reads the check list
  from `protocol.json` (`.states[].checks[]`) and resolves each to an executable
  in any language (`exec` path, or `checks/<name>.*` extension-agnostic). The
  orchestrator no longer hardcodes the check names. `rubric-coverage` is now
  Python (`rubric-coverage.py`); `rubric-coverage` AND `traces-exist-in-diff`
  are now Python, while `schema-valid` stays bash — same ABI. New test suite
  `tests/test-runchecks.sh` (11 tests) covers resolution and robustness
  (missing / non-executable / crashing / ambiguous).

- **Merge-gating via a check run.** `advance.sh`/`plan` emit a `grumpy-review`
  check run on the PR head SHA reflecting protocol state (in_progress while
  reviewing, `action_required` on changes-requested, `success` on clean,
  `failure` on exhausted). Emitting works on any repo; *blocking* the merge
  requires making `grumpy-review` a required status check in branch protection /
  rulesets (needs a public repo or a paid plan for private). `lib.sh`
  `set_check_run`; engine tests assert the three outcomes (50 local tests total).

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

The engine is now fully protocol-agnostic (`grep -rin grumpy .github/engine/`
is empty). The two previously noted couplings are gone:

- **Protocol id from data.** `next.sh` and `advance.sh` read the protocol id
  from `protocol.json` `.name` via `protocol_id()` in `lib.sh`. The check-run
  name, status-comment headline, and state-path prefix all derive from it.
- **State path from data.** `lib.sh`'s `state_file` returns
  `<dir>/<protocol-id>/<instance-key>.yaml` (e.g. `grumpy-review/pr-<N>.yaml`).

**Trigger policy lives in `orchestrator.yml`, not the engine.** `next.sh`
accepts a command (`start` / `reset` / `continue`); the orchestrator maps
GitHub events to commands:

| GitHub event | Command |
|---|---|
| `pull_request` opened / reopened | `start` |
| `pull_request` synchronize | `reset` |
| `issue_comment` `/grumpy` | `start` |
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

**Publication is a protocol publish hook.** `advance.sh` resolves and calls
`protocols/grumpy/publish/publish-review-from-evidence.sh` via
`resolve_executable` (the same mechanism as checks). The hook runs trusted in
zone 4 (engine-post) holding the publish token; it is not a sandboxed check.

## Not exercised by this PoC (honest gaps)

- **Checks that execute agent-authored code** (e.g. running the project's
  `npm test` as a gate). Grumpy's checks are pure data-inspection of the
  evidence file and the independently-fetched diff, so the design's
  "zone-3 runs agent code with zero credentials" hardening was never tested.
- gh-aw's egress firewall on the agent (disabled, see #6).
- Human gates, multi-phase protocols, fan-out/join (all v2).
- The external web-app projection of the state branch.
- `gh aw compile` changes to understand a `protocol:` block (the engine is
  vendored as repo scripts, not compiled into the lock file).

## Operational gotchas (also in project memory)

- `gh secret set NAME --body -` stores the literal `-`, not stdin. Use
  `--body "$VALUE"`.
- Custom endpoint must be configured via `engine.env` (forwarded to the CLI
  subprocess) with the proxy off; top-level `env:` is not forwarded.
- Workflows run from the **default branch** for `issue_comment` /
  `repository_dispatch` — keep `orchestrator.yml` and the agent lock on `main`,
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

- **The `BRANCH` engine seam.** `next.sh`, `run-checks.sh`, and
  `advance.sh` all read a `BRANCH` env var (`lib.sh` provides the branch-aware
  `state_file`/`instance_file` helpers they pass it to). **Empty/unset = the original v1
  single-agent grumpy path, byte-identical** (this is the regression guard —
  the whole v1 suite still passes unchanged). **Set = operate on one fan-out
  branch:** its agent unit, its check list, its publish hook, and its own
  per-branch state file. No new code path forks the engine; the same scripts
  read one extra variable.

- **New `fanout` + `join` protocol kinds (Approach C — data-driven).** Each
  branch reuses the v1 single-agent iterate loop *verbatim*; the only new logic
  is the fan-out planner and the join barrier. `protocols/multi-grumpy/protocol.json`
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
  gating check**. `plan` marks the aggregate `in_progress`; `join.sh` completes
  it (success iff all branches `done`, else failure).

- **`join.sh`** (new engine script). Reads every branch state file; once all are
  terminal and `_instance.yaml` is not yet joined, sets the aggregate check-run,
  renders the status comment, flips `joined`, and CAS-pushes. **Idempotent.** It
  runs in a dedicated **serialized** workflow `.github/workflows/protocol-join.yml`
  (concurrency `join-<instance>`, `cancel-in-progress: false`), fired by a
  `repository_dispatch: protocol-join` that `advance.sh` emits whenever a branch
  reaches a terminal state. `advance.sh` also now carries `client_payload[branch]`
  on its `protocol-continue` iterate dispatch.

- **Orchestrator = branch matrix.** `.github/workflows/orchestrator.yml` was
  rewritten. `plan` runs `next.sh` **unbranched** for `pull_request`/`issue_comment`
  (→ action `run-fanout`, `branches=[grumpy,security]`) and **branched**
  (`BRANCH=<payload.branch> next.sh … continue`) for `repository_dispatch:
  protocol-continue` (→ one branch). `dispatch`/`checks`/`advance` are a
  `strategy.matrix.branch` (`fail-fast: false`), gated `if: needs.plan.outputs.branches
  != '[]'`. **Per-branch data (agent run-id, verdicts) flows between the matrixed
  jobs via branch-named ARTIFACTS** (`runmeta-<branch>`, `verdicts-<branch>`),
  **not** job `outputs` — because GHA matrix legs share one outputs map and the
  last leg clobbers the others. The four v1 trust zones are preserved per leg
  (plan = engine-pre; dispatch = agent-trigger; checks = read-only ground truth,
  **no write tokens**; advance = sole state writer + publisher).

- **The security agent** (`.github/workflows/security-agent.md` → compiled
  `security-agent.lock.yml`) is a thin gh-aw clone of grumpy-agent that emits
  grumpy-shaped evidence with `category:"security"`. It has a **persistent
  sabotage knob:** while the `poc:sabotage` label is present it fabricates a
  finding **every iteration** (→ fails `traces-exist-in-diff` → exhausts to
  `failed`). This contrasts with grumpy's **iteration-1-only** knob (sabotage
  then self-recover). The orchestrator's sabotage step now reports the label
  regardless of iteration; each agent self-decides what to do with it.

- **New tests.** `tests/test-join.sh` (join aggregation + idempotency) and
  `tests/test-fanout-e2e.sh` (local end-to-end: fanout start → advance ×2 → join
  success). The full local suite is now **6 files / 122 assertions, all green**
  (the four v1 files — `test-checks.sh`, `test-engine.sh`, `test-runchecks.sh`,
  `test-publish.sh` — plus these two).

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

## Concurrency — still deferred (now v3)

The correlation-id run resolver (v1 deviation #11) is **still deferred — now the
v3 milestone** (see `BACKLOG.md`). v2 does **not** need it: fan-out **within one
PR** is safe because grumpy and security are **distinct workflow files**, so each
branch's "newest run since T0" resolver only ever sees its own workflow's runs —
they can't misattribute to each other. The unsolved case remains **concurrent
PRs of the *same* workflow**, which share a global concurrency group; that is
the correlation-id problem deferred to v3. v2 was live-verified one PR at a time.
