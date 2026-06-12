# Backlog — planned enhancements

Running list of things we've decided to do but haven't yet. For "what is / isn't
implemented today" see `STATUS.md`.

**v3 = correlation-id run resolver** is DONE (below); the next milestone is
**v4 = human-in-the-loop (approval gate)**. Both are written up first below.
The remaining entries are smaller, unsequenced candidates.

## v3 — Correlation-id run resolver (DONE)

**What:** Stamp a correlation id into each agent dispatch and the launched run's
name (so it appears in the run's displayTitle), so the orchestrator resolves
*the exact run it launched* instead of guessing "newest `workflow_dispatch` run
of this workflow since T0".

**Why (the blocker it removes):** Today the agent run-id resolver is correct only
**one PR at a time per workflow** (v1 deviation #11). The gh-aw agent workflow
uses a *global* concurrency group, so two PRs reviewed concurrently by the **same**
workflow could misattribute runs. v2's fan-out *within* one PR is already safe —
grumpy and security are **distinct** workflow files, so each branch's resolver
only sees its own workflow's runs (see `STATUS.md` §"v2 … Concurrency"). What v3
fixes is the remaining case: **concurrent PRs of the same workflow.** Until then,
the PoC is live-verified one PR at a time.

**As built (Approach A — run-name):**
- `dispatch` mints `cid = <orchestrator_run_id>-<run_attempt>-<branch>` and adds
  it to the `aw_context` JSON.
- Each agent `.md` sets `run-name: "<Agent> · cid:[${{ fromJSON(... aw_context).cid }}]"`;
  `gh aw compile` bakes it into the lock, so the cid lands in the run's displayTitle.
- The resolver matches `cid:[<cid>]` via `match_run_by_cid` (`lib.sh`, unit-tested
  in `tests/test-correlation.sh`) and fails loudly on no match — no heuristic fallback.

**Status:** DONE — live-verified on concurrent PRs #48 + #49 (each `dispatch`
resolved its own cid'd run; zero cross-contamination; both aggregate checks
green). See `STATUS.md` §"Live verification".

---

## v4 — Human-in-the-loop (approval gate)

**What:** A protocol state kind that **pauses** for a human decision (approve /
request-changes / reject) before the engine advances past it — the first use of
the `gates: {}` field already reserved in the state model.

**Why:** v1/v2 gate purely on deterministic checks (form) and an agent's verdict;
there is no point where a human's explicit sign-off is a *required transition*.
A human gate makes "waiting for a person" a first-class, zero-cost protocol state
(a line in the committed state file), consistent with the "PR is the unit of
existence; runs are heartbeats" model — a protocol can sit gated for weeks.

**Sketch:**
- A `kind:"gate"` state in `protocol.json`; the engine emits a pending check-run
  and records the gate as open in `gates`.
- A trigger (a `/approve` comment, a review submission, or a label) maps — in the
  orchestrator, per the command seam — to a `resolve-gate` command that records
  the decision and outcome in the state file and advances (or halts) accordingly.
- Reuse the existing trust-zone split: the human's input is an *event* (a
  wake-up), and `advance.sh` remains the sole state writer.

**Status:** not started — the designated v4 milestone.

---

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

## (See also `STATUS.md` and the v1 deviation list for other candidate work: restore the agent egress firewall for the now-public endpoint. The correlation-id run resolver shipped as **v3** (DONE), above.)
