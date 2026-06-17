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
- The resolver matches `cid:[<cid>]` via `match_run_by_cid` (`lib.py`, unit-tested
  in `tests/test_correlation.py`) and fails loudly on no match — no heuristic fallback.

**Status:** DONE — live-verified on concurrent PRs #48 + #49 (each `dispatch`
resolved its own cid'd run; zero cross-contamination; both aggregate checks
green). See `STATUS.md` §"Live verification".

---

## v4 — Human-in-the-loop (approval gate)

**Update (2026-06-17):** the *override escape-hatch* — a write-access human forcing
a **blocked** halt-gate past one phase via `/override` — shipped separately (see
`docs/superpowers/specs/2026-06-17-hitl-override-gate-design.md`). What remains in
THIS item is the broader **pause-and-require** `kind:"gate"` approval state (a human
sign-off as a *required* transition), which is still not started.

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
  wake-up), and `advance.py` remains the sole state writer.

**Status:** not started — the designated v4 milestone.

---

## HITL override escape-hatch — remaining follow-ups

The `/override` escape-hatch shipped and is live-verified (`docs/superpowers/specs/2026-06-17-hitl-override-gate-design.md`), but two items were deferred:

- **Live-test the DENIED path with a non-write identity.** The authorized and
  exhausted-refusal paths were exercised live; the *denied* path (a commenter
  lacking `write`/`admin` → permission check fails → denial comment, no advance)
  could not be driven because the only available identity is the repo owner
  (admin). It is covered by the unit tests + the auth-gate code; confirm live once
  a read-only collaborator / second account is available.
- **Clean up the accumulated live-test artifacts.** Disposable PRs and branches
  remain on `golivax/agentic-protocol-poc`: PRs #62 / #65 / #66 (+ branches
  `m3-live-clear` / `m3-live-blocked-absence` / `m3-live-blocked-adherence`) and
  the older #55 / `m2b-live2`. Close/delete when convenient. (Note: #65 was
  re-triggered and overridden during the HITL live test, so it now carries a
  completed review fan-out.)

**Status:** not started.

---

## Configurable feedback scope (last vs. cumulative)

**What:** Make the feedback injected into a retry's prompt configurable — either
only the immediately-preceding iteration's feedback (today's behavior) or the
cumulative feedback across all prior iterations.

**Why:** Today `next.py` injects `history[-1].feedback`, so iteration N only sees
iteration N-1's rejection reasons. For longer or stricter protocols it can help
the agent to see the full history ("you've now failed coverage twice for the
same cell"), at the cost of a longer prompt.

**Sketch:**
- Add a protocol option, e.g. top-level or on the agent state in
  `protocol.json`: `"feedback_scope": "last" | "cumulative"` (default `"last"`).
  Optionally a `"feedback_window": N` for "last N iterations".
- In `next.py`, when emitting feedback on resume:
  - `last` → `.history[-1].feedback` (current).
  - `cumulative` → join non-empty feedback across history, labelled by
    iteration, e.g. `"iter 1: …; iter 2: …"`.
- Keep the empty-history guard. Cumulative output can grow; consider a sane cap.
- Tests: extend `tests/test_engine.py` next.py cases to assert each mode.

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
guardrail against automation self-approving/merging). So `advance.py` falls back
APPROVE→COMMENT. `REQUEST_CHANGES` and `COMMENT` are not gated, so only the
all-clean path is affected.

**Options (pick per deployment):**
1. **Enable the repo setting** (`can_approve_pull_request_reviews=true`). Simplest;
   then `advance.py` submits `APPROVE` and the fallback never fires. It is a
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

**Why deferred:** The producer side is done — `advance.py`/`plan` emit the
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

## Check-authoring meta-protocol (AI-generated deterministic checks)

**What:** A protocol whose *output* is a candidate deterministic check script
(`checks/<name>.*` honoring the check ABI) generated by an agent from a
natural-language check spec, with **human review of the committed diff as the
transition gate**. The generated check then runs forever after as ordinary
deterministic code — no agent at runtime.

**Why:** Today every deterministic check is hand-written. But authoring a check
and executing a check happen at different times on opposite sides of the commit
boundary, so the trust model is preserved if we let AI do the *authoring* only:

- **Design-time (AI leverage):** an agent emits the check script from a spec.
- **Commit boundary (human control):** a person reviews the diff and merges.
- **Run-time (deterministic, credential-free):** the engine executes the
  committed script in zone 3 exactly like any other check.

This is the "demand evidence, check it deterministically" principle applied
recursively to the act of *building* checks — and it's the natural way to scale
the "easy-to-plug deterministic checks" seam (a new check is a `protocol.json`
entry + a file in `checks/`, no lock recompile) without every check being
artisanal. It also pairs with the DECIDE-phase / `on_fail`-severity work
(checks as first-class non-agentic verifiers): once a check carries a severity,
generating one is "write the verifier + declare its failure policy".

**Sketch:**
- Input: a check spec (the rubric/contract — what evidence or PR fact to verify,
  what `pass` means, what `feedback` to emit on fail).
- An `agent` state produces the candidate script as its evidence/artifact; form
  checks verify the *shape* (ABI conformance: 3 path args in, one
  `{check,pass,feedback}` JSON out, exits 0; runs against a fixture evidence +
  diff without crashing) — never the *substance* (whether the check's logic is
  correct; that's the human review).
- Terminal/publish step opens a PR adding `checks/<name>.*` + the
  `protocol.json` entry for human review. Merge = the gate.
- Reuse the existing trust zones unchanged: the agent is sandboxed and only
  emits a file; the engine still owns checks/state/verdict.

**Status:** not started. Idea captured 2026-06-16.

---

## Orchestrator B→A: collapse the trigger shim into a single self-routing workflow (DONE)

**What:** Evolve the generic orchestrator from **approach B** (an engine-owned
reusable `workflow_call` engine + a thin per-protocol *trigger shim* that
declares `on:` and calls it) to **approach A** (a single self-routing
`agentic-orchestrator.yml` with one fixed `on:` block covering all framework-
supported events, and a runtime router that scans every `protocols/*/protocol.json`
`triggers` block to decide which protocol(s) + command an event maps to). End
state: humans author **zero** workflow YAML — only the gh-aw agent markdown,
`protocol.json`, and checks.

**Why:** B already gets ~95% of the win (the human-authored surface is markdown +
protocol + checks; the shim is trivial boilerplate). A removes the last scrap of
per-protocol YAML and the duplicated `on:`/concurrency wiring across shims, at the
cost of a runtime router, multi-protocol-per-event fan-out, and job-level
concurrency keyed by `protocol·instance·branch`. Deferred because the routing +
concurrency edge cases are subtle and land in the jobs that hold the state PAT —
not worth blocking the first generic orchestrator on.

**Status:** DONE (2026-06-16). `agentic-orchestrator.yml` (router: read-only
`route` job calling `lib.route` to scan all protocols' `triggers` blocks at
runtime) + `agentic-engine.yml` (reusable `on: workflow_call` engine — the 4
trust zones) are live. The per-protocol trigger shim (`multi-grumpy-trigger.yml`)
is deleted. No per-protocol workflow YAML remains.

---

## (See also `STATUS.md` and the v1 deviation list for other candidate work: restore the agent egress firewall for the now-public endpoint. The correlation-id run resolver shipped as **v3** (DONE), above.)
