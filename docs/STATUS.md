# PoC v1 — Status & Deviations

This records what the v1 PoC actually is, measured against the original design
spec (`agent-factory/docs/superpowers/specs/2026-06-10-...`) and plan. Read it
before extending the system so you know which "missing" pieces are deliberate.

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
  negative control). 36 local tests (`tests/test-checks.sh` 18,
  `tests/test-engine.sh` 18).

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

## Post-v1 enhancements (added after the demos)

- **Polyglot, data-driven checks.** `engine/run-checks.sh` reads the check list
  from `protocol.json` (`.states[].checks[]`) and resolves each to an executable
  in any language (`exec` path, or `checks/<name>.*` extension-agnostic). The
  orchestrator no longer hardcodes the check names. `rubric-coverage` is now
  Python (`rubric-coverage.py`); `schema-valid` and `traces-exist-in-diff` stay
  bash — same ABI. New test suite `tests/test-runchecks.sh` (11 tests) covers
  resolution and robustness (missing / non-executable / crashing / ambiguous).

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
