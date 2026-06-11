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

## Known engine couplings to generalise (not redesign)

The engine scripts are meant to be protocol-agnostic and mostly are
(`run-checks.sh` takes any `protocol.json`; `next.sh`/`advance.sh` take the
protocol path as an argument). Two grumpy-specific literals remain baked in and
should become parameters when a second protocol is added:

- `next.sh` / `advance.sh` write `protocol: "grumpy-review"` into new state.
- `lib.sh`'s `state_file` hardcodes the `grumpy/pr-<N>.yaml` path layout.

These are small (instance-key + protocol-id as inputs), not architectural.

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
