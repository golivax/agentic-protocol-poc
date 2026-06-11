# Backlog — planned enhancements

Running list of things we've decided to do but haven't yet. For "what is / isn't
implemented today" see `STATUS.md`.

## Configurable feedback scope (last vs. cumulative)

**What:** Make the feedback injected into a retry's prompt configurable — either
only the immediately-preceding iteration's feedback (today's behavior) or the
cumulative feedback across all prior iterations.

**Why:** Today `next.sh` injects `history[-1].feedback`, so iteration N only sees
iteration N-1's rejection reasons. For longer or stricter protocols it can help
the agent to see the full history ("you've now failed coverage twice for the
same cell"), at the cost of a longer prompt.

**Sketch:**
- Add a protocol option, e.g. top-level or on the agent state in
  `protocol.json`: `"feedback_scope": "last" | "cumulative"` (default `"last"`).
  Optionally a `"feedback_window": N` for "last N iterations".
- In `next.sh`, when emitting feedback on resume:
  - `last` → `.history[-1].feedback` (current).
  - `cumulative` → join non-empty feedback across history, labelled by
    iteration, e.g. `"iter 1: …; iter 2: …"`.
- Keep the empty-history guard. Cumulative output can grow; consider a sane cap.
- Tests: extend `tests/test-engine.sh` next.sh cases to assert each mode.

**Status:** not started. Requested 2026-06-11.

---

## Real APPROVE on clean PRs (instead of the COMMENT fallback)

**What:** Let a fully-clean result publish a formal `APPROVE` review rather than
degrading to a `COMMENT`.

**Why:** Today publication uses the default `GITHUB_TOKEN` (the
`github-actions[bot]`), because the PAT is the PR author and GitHub forbids
authors from reviewing their own PR. But the bot can't submit `APPROVE` unless
the repo setting *Settings → Actions → General → "Allow GitHub Actions to create
and approve pull requests"* is enabled (off by default, off here — it's a
guardrail against automation self-approving/merging). So `advance.sh` falls back
APPROVE→COMMENT. `REQUEST_CHANGES` and `COMMENT` are not gated, so only the
all-clean path is affected.

**Options (pick per deployment):**
1. **Enable the repo setting** (`can_approve_pull_request_reviews=true`). Simplest;
   then `advance.sh` submits `APPROVE` and the fallback never fires. It is a
   repo-wide security-control change, so it must be a deliberate owner decision.
2. **Publish under a dedicated identity** — a GitHub App or a separate bot
   account that is *not* the PR author and is permitted to approve. More
   production-shaped: also removes the "PAT-is-the-author" coupling that forces
   us onto the bot token today.

Keep the COMMENT fallback regardless, as the safe default when neither is set up.

**Status:** not started. Requested 2026-06-11.

---

## Make `grumpy-review` a required status check (enforce the merge gate)

**What:** Configure branch protection / a ruleset so the `grumpy-review` check
run actually *blocks* merges, not just shows red.

**Why deferred:** The producer side is done — `advance.sh`/`plan` emit the
`grumpy-review` check on the PR head SHA (in_progress → failure on
changes-requested → success on clean), verified live on PR #15. What's left is a
one-time GitHub *config* step, deferred so it's a deliberate choice (turning it
on blocks merges on every PR that doesn't get a clean review).

**Prerequisites (met):** repo is public (branch protection/rulesets available);
the `grumpy-review` name has reported at least once, so it's selectable.

**How (per HOW-IT-WORKS §5.1):**
- Ruleset (recommended): *Settings → Rules → Rulesets → New branch ruleset* →
  target `main` → *Require status checks before merging* → add `grumpy-review`
  (source: GitHub Actions) → Active → Create.
- Optionally layer *Require approvals* for a human sign-off too (note: the bot
  can block via `failure` but can't `APPROVE` to unblock unless the
  "Allow GitHub Actions to approve pull requests" setting is on).
- Verify: open a PR with issues → merge button blocked until a clean review.

**Status:** ready to enable (config only). Deferred until you want enforcement on.

---

## (See also `STATUS.md` "Known engine couplings to generalise" and the v1 deviation list for other candidate work: restore the agent egress firewall for the now-public endpoint, correlation-id run resolver, parameterise the `grumpy-review` / `grumpy/pr-<N>.yaml` literals out of the engine.)
