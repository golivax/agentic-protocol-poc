# Design: multi-AI review → issues → triage → committing fix (demo)

**Date:** 2026-06-30
**Branch:** `feat/multi-ai-review-issue-demo` (cut from `feat/backport-protocol-from-yuanrong`)
**Status:** awaiting user review

## Goal

Showcase the 5-dimension AI review phase of the `code-review` protocol as a
standalone, end-to-end story on a real PR:

1. Five domain reviewers (correctness, test, performance, security,
   maintainability) each review the PR through **their own lens** and **open
   GitHub issues** for the problems they find — making "different agents find
   different problems" visible as distinct, domain-labeled issues.
2. The **triage** agent consolidates/ranks those findings.
3. The **fix** agent proposes remediations *and the engine commits them to the
   PR branch*, then closes the issues it resolved.

Only this `review → triage → fix` slice runs; the other phases (preflight,
preflight-gate, overview, post-fix, mrp) are disabled for the demo.

## Target & deployment (decided)

- **Engine host / review target:** install the slimmed protocol into
  `SiRumCz/yuanrong-datasystem` on its **default branch `main`** via
  `dist/install.sh` (we have WRITE; the engine must live on the target repo's
  default branch to be driven by `/review`).
- **Demo PR:** **#8 — "feat(client): switch worker on URMA data-plane failure"**
  (1656+/3−, 21 files). Failover logic naturally spans all five review domains.
  (#7 is a duplicate of #8; we use #8.)
- **Agent engine:** unchanged — **codex / gpt-5.5 via the Tailscale gateway**
  (`engine.env.OPENAI_BASE_URL`). The dist gateway-restore fix (`823dd6a`) is
  already on this branch, so install won't strip it.

## Non-goals

- Not changing the engine (`.github/agent-factory/engine/`).
- Not re-enabling preflight/overview/post-fix/mrp.
- Not building inline-PR-review output for the review legs (we replace that
  channel with issues — see Decision D1).
- Not a production hardening pass (the gateway firewall stays disabled as on the
  source branch).

## Current state (verified on the branch)

- `code-review/protocol.json` runs the full custody pipeline:
  `preflight(6-leg) → preflight-gate → overview → review(5-dim) → join-review →
  triage → fix → post-fix → mrp → done`.
- The engine starts at `states[0]` and walks the sequence **positionally**
  (`next.py: enter_root → paths.root_ids(proto)[0]`). `validate_protocol` does
  not require any particular phase. `review` declares **no `inputs` from
  `overview`** → the `review → triage → fix` slice is self-contained.
- The 5 review agents are **already differentiated** by hand-written,
  domain-scoped rubrics ("Own correctness … leave the rest to the siblings").
  They emit a read-only `evidence.json`; `staged: true` keeps their safe-outputs
  inert. A trusted hook `publish-review.py` (wired via `"publish":
  "publish-review"` on each review branch) posts a per-dimension PR review.
  *(The protocol `_note` claiming reviews aren't posted is stale.)*
- `triage` consumes the 5 review evidences → dedup/rank clusters;
  `conclude-triage.py` posts one gate comment.
- `fix` is **suggest-only** (`mode:"suggest"`, emits `suggested_patch`);
  `conclude-fix.py` posts inline `suggestion` blocks. **No commit.**
- gh-aw **`create-issue`** is supported by the installed gh-aw (present in
  compiled `*.lock.yml`), just not declared in any `.md` yet.

## The four units of work

### Unit 1 — Slim the protocol to `review → triage → fix`

Produce a sequence whose `states[]` is exactly:

```
review (5-dim fanout) → join-review → triage → fix → done
```

- `review` keeps its 5 branches; **`"publish": "publish-review"` is removed**
  from each branch (issues replace the PR-review channel — Decision D1).
- `join-review.next = "triage"`; `triage` keeps its 5-leg `inputs`; `fix` keeps
  its `triage` input; **`fix.next = "done"`**.
- preflight/preflight-gate/overview/post-fix/mrp nodes are dropped from
  `states[]` (their files stay on disk, unused).

**Decision D2 (implementation form) — REVISED after exploration:** build a
**self-contained sibling protocol** `code-review-demo/` (name `code-review-demo`,
trigger `/demo-review`) rather than editing `code-review/protocol.json` in place.
Reason: ~12 regression tests pin the full `code-review` shape
(`test_preflight_wiring`, `test_unified_codereview_e2e`, `test_route`,
`test_protocol_lint`, `test_resolve_agent_unit`, `test_mm_pipeline_wiring`, …); an
in-place slim would gut the suite. The sibling dir is fully self-contained
(its own copies of the checks, evidence schemas, rubrics, and the *modified*
conclude hooks), so `dist/install.sh install code-review-demo` ships a single,
standalone protocol and `code-review` stays byte-for-byte intact.

Consequences:
- Trigger is **`/demo-review`** (not `/review`) — avoids router trigger-overlap
  with the still-present `code-review` protocol on the dev repo and keeps
  `test_route` green. (On yuanrong it can be renamed to `/review` post-install if
  desired, since `code-review` isn't installed there.)
- The 5 review legs need **demo-specific agent workflows**
  (`demo-review-<dim>-agent`) because the stock review agents hard-code the rubric
  path `…/protocols/code-review/rubrics/…`; the demo agents stage from
  `…/code-review-demo/rubrics/…` and add the `create-issue` safe-output. `triage`
  and `fix` agents are **reused unmodified** (their demo behavior changes live in
  the demo's *conclude* hooks, not the agents).
- **No change to `.github/agent-factory/engine/` or `agentic-engine.yml`:** the
  fix applier runs in the existing `advance` job and pushes/closes using the
  PAT already exported there as `GH_TOKEN` (`POC_DISPATCH_TOKEN`, repo scope),
  so no new job permission is required.
- `fix` evidence gains an **optional** `original_line` field (verbatim current
  content of the target line) so the applier can verify-before-replace (mitigates
  R2). The shared `fix-agent.md` prompt gets one additive bullet to emit it;
  back-compatible (optional), so `code-review`'s fix check stays valid.

**Verification:** `protocol-lint.py` passes on the slimmed protocol; the existing
fanout/triage/fix pytest regressions still pass.

### Unit 2 — Reviewers open domain-labeled issues

For each of the 5 review agent `.md` files:

- **Frontmatter:** remove `staged: true`; add a `create-issue` safe-output
  carrying that agent's **domain label**, and add `issues: write`:

  ```yaml
  permissions:
    contents: read
    pull-requests: read
    issues: write
  safe-outputs:
    create-issue:
      title-prefix: "[ai-review] "
      labels: [ai-review, "review:correctness"]   # per-agent domain label
      max: 10
    noop: {}
  ```

- **Prompt:** after writing `evidence.json` as today, instruct the agent to emit
  **one issue per finding** — title = `[<dimension>] <finding.title>`, body =
  `path:line`, severity, impact, proposed fix, and a backref to the PR. Title is
  a **stable key** (`<dimension>` + finding title) so downstream phases can
  resolve the issue without knowing its number.

- Recompile each lock via `gh aw compile`.

**Idempotency under the iterate loop (Decision D3).** gh-aw safe-outputs execute
in the agent job *before* the engine's checks run, so a failed-then-retried
iteration would double-post issues. The agent's restricted tools (`bash:
cat,echo`) can't easily list existing issues to dedup. For the demo we set the
**review legs to `max_iterations: 1`**: each leg runs once; if its evidence fails
schema/anchor checks the leg is marked failed but its issues (if any) are still
useful. A proper dedup guard (query open `review:*` issues by title-key, skip
existing) is recorded as a follow-up.

**Why this satisfies the "showcase":** five agents → five `review:<domain>`
labels → a reader sees, at a glance, which reviewer raised which class of
problem, even when they overlap on the same lines.

### Unit 3 — Triage cross-links the issues (light touch)

`triage` is unchanged in substance (consumes the 5 evidences → clusters). The
only addition: `conclude-triage.py` resolves each cluster's matching
`review:<domain>` issues **by label + title-key** and includes the issue numbers
in its gate comment, so the consolidated triage comment links the open issues.
No new agent behavior; the resolution is deterministic in the trusted hook.

### Unit 4 — Fix commits to the PR + closes resolved issues (trusted zone-4)

The `fix` **agent stays read-only** (`mode:"suggest"`, emits `suggested_patch`).
A **trusted patch-applier** runs in the engine's `advance` job (zone 4), as an
extension of `conclude-fix.py` (or a sibling `apply-fixes.py` it calls):

1. Check out PR #8's **head branch**, apply each `suggested_patch` at its
   `path:line` (exact line replacement), `git commit` (`fix: address AI review
   findings (#<pr>)`), `git push` to the head branch.
2. For each applied fix, resolve the matching `review:<domain>` issue(s) by
   label + title-key and **close** them with a comment linking the pushed SHA
   (deterministic stand-in for `Fixes #N`, which we can't embed pre-push because
   issue numbers aren't in evidence — Decision D3 consequence).
3. Emit the usual conclude JSON (`conclusion`, `summary`).

**Tokens / permissions:** the `advance` job already holds a write token and
posts PR comments. Add `contents: write` to its `permissions` so the push
succeeds (same-repo PR head; commits by the job token deliberately don't
re-trigger workflows). **`ENGINE_LOCAL=1`** short-circuits the push/close to file
output so pytest exercises the applier without touching GitHub.

PR #8's head branch lives in `yuanrong-datasystem` itself (you authored it, WRITE
access), so a same-repo push works — no fork-PR cross-repo complication.

## Data flow (demo run)

```
/review on PR #8
  └─ engine starts at `review` (states[0])
     ├─ correctness  → evidence.json + create-issue(s) [label review:correctness]
     ├─ test         → evidence.json + create-issue(s) [label review:test]
     ├─ performance  → evidence.json + create-issue(s) [label review:performance]
     ├─ security     → evidence.json + create-issue(s) [label review:security]
     └─ maintainability → evidence.json + create-issue(s) [label review:maintainability]
  └─ join-review (AND-barrier)
  └─ triage  → clusters + gate comment (links the issues by title-key)
  └─ fix     → suggested_patch evidence
              → [zone-4 applier] commit+push to PR #8 head; close resolved issues
  └─ done
```

## Risks / open items

- **R1 — Organic coverage.** #8 may not yield a finding in *every* domain;
  some `review:*` buckets could be empty. Acceptable for a realistic demo;
  fallback is a planted-defect PR (deferred per user's choice of #8).
- **R2 — Patch application fragility.** `suggested_patch` is "exact replacement
  line(s)" anchored to `path:line`; if the head moved since review, application
  can miss. Applier must verify the target line matches the finding's expected
  content and skip (record) on mismatch rather than corrupt the file.
- **R3 — Issue/number linkage** is by label+title-key, not by stored number
  (consequence of agent-side create-issue). Title-key must be stable across
  phases; collisions (two findings, same title) close-link ambiguously — applier
  closes all label+title matches and comments the SHA.
- **R4 — Secrets on yuanrong:** install must set the state PAT and the codex
  gateway token; verify the gateway URL is reachable from GitHub-hosted runners.

## Defaults taken (flip if wrong)

- **D1:** review legs output **issues only** (drop `publish-review`), not issues
  *and* inline PR reviews.
- Agent engine stays **codex/gpt-5.5** via the gateway.

## Test strategy

- **Local:** extend pytest for the zone-4 applier in `ENGINE_LOCAL` mode
  (apply-to-tempdir, file-out for push/close); `protocol-lint.py` on the slimmed
  protocol; existing fanout/triage/fix regressions stay green; `gh aw compile`
  clean.
- **Live:** `dist/install.sh install code-review` → yuanrong `main`; `/review`
  on PR #8; observe 5 labeled issues → triage comment → fix commit on the PR
  branch → issues closed.
