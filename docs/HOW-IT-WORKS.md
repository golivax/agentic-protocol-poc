# The Agentic Protocol Engine — How It Works

A guide to the PoC: why it exists, the ideas it's built on, its architecture,
and how a developer writes and runs a protocol.

---

## 1. Motivation

Two existing systems each solve half of "run a reliable, multi-step agentic
process on GitHub":

- **GitHub Agentic Workflows (gh-aw)** compile a markdown file into a sandboxed
  GitHub Actions workflow: the agent runs with read-only credentials, and
  everything it wants to change in the world goes through *safe-outputs* —
  schema-validated, count-limited, executed by a separate job that holds the
  write permissions. This is excellent **spatial** control (what one agent run
  may emit) but each run is a single, stateless agent invocation. A multi-step
  process exists only as natural-language instructions in the prompt that the
  agent may or may not follow.

- **porch** (from codev) is a deterministic protocol engine. Phases,
  transitions, checks, and human gates are declared as *data* (`protocol.json`);
  state lives in a git-committed YAML file; a pure `(state, protocol) → action`
  planner decides what happens next; transitions only occur when shell checks
  pass. This is excellent **temporal** control (when a process may advance) but
  it runs on a developer's laptop with the agent as the driver: the agent calls
  porch as a tool, so porch's determinism still depends on the agent choosing to
  consult it.

**The synthesis:** keep gh-aw's sandbox for each agent step, and put porch's
planner in charge of *when* the process advances — but invert porch's control
model so the **engine drives and the agent is dispatched**. The agent can't
skip the engine, because it only exists when the engine spawns it, can only
affect the world through artifacts the engine's checks inspect, and is gone
before the engine writes state.

The one principle that ties it together:

> **Don't trust prose — demand evidence, and check it deterministically.**
> Each step's contract is an *evidence schema* the agent must fill. Code can't
> verify "the agent did a good job", but it can verify the *structure* of the
> evidence: every rubric cell has a verdict, every claim cites something that
> exists in the diff. That converts the agent's cheapest failure (silently
> skipping work) into its most detectable one (an explicit, falsifiable claim).

---

## 2. Key ideas and assumptions

1. **A workflow run is one transition of a state machine whose state lives in
   git.** State is durable (a YAML file on a branch), so the compute can be
   ephemeral. This is the same shape as AWS Step Functions or Temporal:
   durable state, stateless workers.

2. **Protocol logic is data, not YAML.** `protocol.json` declares states,
   checks, and transitions. One generic engine interprets it. GitHub's workflow
   YAML only decides *when to wake the engine*, never the protocol logic.

3. **The evidence schema is the contract.** The agent must produce a structured
   evidence file covering an enumerable rubric. Prose in the workflow body is
   just guidance on how to satisfy the schema.

4. **Checks verify form; verification (a judge/human) verifies substance.**
   Deterministic code checks coverage, schema, and traceability against
   *independently-derived* ground truth. Whether the agent's *opinion* is
   correct is a separate concern (a second LLM judge, or a human gate) — not a
   check.

5. **State advances only by fast-forward push (compare-and-swap).** The state
   branch is the single source of truth; concurrent writers are resolved by
   git rejecting non-fast-forward pushes. Never force-push it.

6. **Events are wake-ups, not state carriers.** A trigger (slash command,
   re-dispatch, label) only tells the engine to look; everything load-bearing is
   re-derived from the state file. This survives GitHub's event coalescing and
   makes transitions safe to replay.

7. **Trust zones are separated by job and credential.** The engine (which holds
   state-write credentials) never runs agent-influenced code; the agent (which
   runs untrusted model output) never holds state-write credentials. See §3.

**Assumptions:** a publicly-reachable LLM endpoint; one protocol instance per
PR, advanced one PR at a time; a PAT for cross-workflow triggering (the default
`GITHUB_TOKEN` deliberately can't trigger workflows).

---

## 3. Architecture

### 3.1 Components

```
protocols/grumpy/
  protocol.json          # states, checks, transitions, max_iterations (DATA)
  evidence.schema.json   # the rubric the agent must fill (the CONTRACT)
  checks/*.sh            # deterministic transition checks (FORM verification)

.github/engine/
  lib.sh                 # state checkout, CAS push, status-comment upsert
  next.sh                # planner: (state, protocol) -> action JSON
  advance.sh             # sole state writer: verdicts -> mutate, publish, push

.github/workflows/
  orchestrator.yml       # hand-written: the 4 trust zones, one run per iteration
  grumpy-agent.md        # gh-aw agent workflow (the one "agent" state)
  grumpy-agent.lock.yml  # compiled output of grumpy-agent.md (committed)

agentic-state branch
  grumpy/pr-<N>.yaml     # durable per-instance state (the source of truth)
```

### 3.2 The four trust zones (per iteration)

Each iteration runs as jobs in `orchestrator.yml`, with strictly separated
credentials:

| Zone | Job | Holds | Runs agent code? |
|------|-----|-------|------------------|
| 1. Engine-pre | `plan` | state-branch PAT | no — deterministic `next.sh` |
| 2. Agent | `dispatch` → the gh-aw workflow | read-only repo token + LLM creds | yes — sandboxed |
| 3. Checks | `checks` | nothing (read-only default token) | no — bash/jq over evidence + diff |
| 4. Engine-post | `advance` | state PAT + publish token | no — reads check verdicts only |

The invariant: **the engine and the agent never share a job or a credential.**
The agent produces an `evidence.json` artifact; the checks job downloads it and
independently re-fetches the PR diff (it never trusts agent-fetched data); the
advance job reads only the check *verdicts* to decide, and only the evidence (to
*render* the already-decided review).

### 3.3 The transition lifecycle

```
event (/grumpy comment, or repository_dispatch "grumpy-continue")
   │
   ▼
[plan]      checkout agentic-state; next.sh reads/creates grumpy/pr-<N>.yaml,
            emits {action: run-agent|halt, iteration, feedback}
   │ run-agent
   ▼
[dispatch]  workflow_dispatch the gh-aw agent with aw_context = {pr, iteration,
            feedback, sabotage}; poll until it finishes; output its run id
   │
   ▼
[checks]    download the agent's evidence artifact; re-fetch `gh pr diff`;
            run each protocol check; emit verdicts {results:[{check,pass,feedback}]}
   │
   ▼
[advance]   append an iteration record to state.history, then:
            • all checks pass → state=done, publish review, CAS-push
            • a check failed, iteration<max → bump iteration, CAS-push,
              repository_dispatch "grumpy-continue"  (→ next run)
            • iterations exhausted → state=failed, CAS-push
```

The loop terminates in at most `max_iterations` agent runs. `next.sh` independently
halts on a terminal state, so a stray re-dispatch can never resurrect a finished
instance.

### 3.4 State model

`grumpy/pr-<N>.yaml` (porch-compatible field names):

```yaml
protocol: grumpy-review
instance: pr-9
state: done            # review | publish | done | failed
iteration: 2           # 1-based, bounded by max_iterations
gates: {}              # reserved for v2 human gates
history:               # one record per iteration — the audit trail
  - iteration: 1
    agent_run_id: "…"
    checks: { schema-valid: pass, rubric-coverage: fail, traces-exist-in-diff: pass }
    feedback: "Missing or duplicated rubric cells: security × src/auth.js; …"
  - iteration: 2
    agent_run_id: "…"
    checks: { schema-valid: pass, rubric-coverage: pass, traces-exist-in-diff: pass }
    feedback: ""
status_comment_id: 4673907543   # the single PR comment the engine re-renders
```

Every transition is a commit to this file on the `agentic-state` branch, so
`git log agentic-state -- grumpy/pr-<N>.yaml` is a complete, auditable history.

---

## 4. Developer guide

### 4.1 Anatomy of a protocol (`protocol.json`)

```jsonc
{
  "name": "grumpy-review",
  "categories": ["naming", "error-handling", "performance", "duplication", "security"],
  "states": [
    { "id": "review",
      "kind": "agent",                 // an LLM step; dispatched as a gh-aw workflow
      "workflow": "grumpy-agent",      // which gh-aw workflow to dispatch
      "evidence": "evidence.schema.json",
      "max_iterations": 3,
      "checks": [                      // run in order between this state and `next`
        { "run": "schema-valid",        "on_fail": "iterate" },
        { "run": "rubric-coverage",     "on_fail": "iterate" },
        { "run": "traces-exist-in-diff","on_fail": "iterate" }
      ],
      "next": "publish" },
    { "id": "publish",
      "kind": "deterministic",         // no agent; the engine executes `action`
      "action": "publish-review-from-evidence",
      "next": null }                   // terminal
  ]
}
```

Designing a protocol state = **choosing the enumerable rubric** (here, 5
categories × changed files) and the checks that verify the evidence is complete
and traceable. The art is finding the decomposition of a judgment task that
makes it gateable.

### 4.2 The evidence schema (the contract)

`evidence.schema.json` is a JSON Schema describing what the agent must emit to
`/tmp/gh-aw/evidence.json`. The key idea is **negative attestation with a
trace**: "I found nothing" is a legal verdict, but it must carry the identifiers
the agent examined, so the check can confirm the agent actually read the code.

```json
{ "files": [
  { "path": "src/util.js", "verdicts": [
    { "category": "naming", "verdict": "none-found",
      "examined": ["clamp", "value", "min", "max"] },      // trace for a negative
    { "category": "error-handling", "verdict": "issues-found",
      "findings": [ { "existing_code": "if (min > max) {…}", // verbatim from the diff
                      "comment": "NaN slips through this guard…" } ] } ] } ] }
```

### 4.3 Writing a deterministic check

Contract: `check.sh <evidence.json> <diff.txt> <changed-files.txt>` →
one line of JSON `{"check": "<name>", "pass": bool, "feedback": "<string>"}`,
**always exit 0** (a non-zero exit means a runner error, not a failed check).
Read the rubric from `protocol.json` — don't hardcode it.

Two rules that make checks trustworthy:

1. **Derive ground truth independently.** `rubric-coverage` and
   `traces-exist-in-diff` re-run `gh pr diff` themselves; they never trust a
   diff the agent produced. A prompt-injected agent can't fake coverage by
   lying about what changed.
2. **Verify form, never substance.** Check that every cell has a verdict
   (coverage), that the schema holds, that every `existing_code`/`examined`
   value appears in the real diff (traceability). Do **not** try to check
   whether a finding is "correct" — that's a job for a second LLM judge or a
   human gate (a future state), not a deterministic check.

When a check fails, its `feedback` string is what gets injected into the next
iteration's prompt, so make it specific and actionable ("Missing: security ×
src/auth.js"), not "evidence invalid".

The three shipped checks:

- **`schema-valid`** — the evidence parses and matches the structural shape
  (legal category/verdict, `issues-found` has ≥1 finding with non-empty
  `existing_code`, `none-found` has ≥1 `examined`).
- **`rubric-coverage`** — every changed `.js` file × every category has
  exactly one verdict. Ground truth: `gh pr diff --name-only`.
- **`traces-exist-in-diff`** — every `existing_code` snippet (whitespace-
  normalized, with diff `+`/`-` markers stripped so multi-line snippets verify)
  and every `examined` identifier appears in that file's diff section.

### 4.4 The agent workflow (`grumpy-agent.md`)

A normal gh-aw markdown file with two protocol-specific responsibilities:

- **Frontmatter** declares the engine, read-only permissions, the LLM endpoint
  (`engine.env`), a `pre-agent-steps` step that materializes the dispatched
  `aw_context` JSON to `/tmp/gh-aw/task-context.json`, and a `post-steps` step
  that uploads `/tmp/gh-aw/evidence.json` as an artifact named `evidence`.
- **Body** is the prompt: the persona + the mission ("for every changed file ×
  every category, record exactly one verdict in evidence.json; copy
  `existing_code` verbatim; cite `examined` identifiers; do NOT fabricate
  findings; your only output is evidence.json — the engine publishes for you").
  The iteration's `feedback` is injected so the agent fixes exactly what the
  previous round's checks rejected.

Compile it with `gh aw compile` and commit the generated `grumpy-agent.lock.yml`
(workflows run from the committed lock).

> Custom LLM endpoint note: configure it under `engine.env`
> (`ANTHROPIC_BASE_URL` literal + `ANTHROPIC_AUTH_TOKEN` from a secret), which
> gh-aw forwards to the CLI subprocess. See `STATUS.md` for why the egress
> firewall is currently disabled for a custom endpoint.

### 4.5 The orchestrator (`orchestrator.yml`)

Mostly protocol-agnostic plumbing you won't edit per-protocol: the four jobs,
the trigger surface (`issue_comment` for `/grumpy`, `repository_dispatch` for
re-entry), the per-PR `concurrency` group, and the credential wiring. It calls
the engine scripts and the checks; the *protocol* decides everything else.

A safety detail worth knowing: agent-derived strings (`feedback`, `verdicts`)
are passed to shell steps via `env:`, never interpolated into `run:` blocks —
otherwise a crafted filename or finding could inject shell commands into the
job that holds the state PAT.

---

## 5. Using a protocol through GitHub (developer's-eye view)

1. **Open a PR** as usual.
2. **Comment `/grumpy`.** That `issue_comment` wakes the orchestrator.
3. **Watch it work.** On the happy path you get one workflow run and a review
   appears — same UX as plain gh-aw. The protocol machinery only becomes
   visible when it has something to say:
   - The engine maintains **one status comment**, re-rendered each transition
     into a checklist:
     ```
     🔍 grumpy-review · pr-9
     - ✗ iteration 1/3 — Missing: security × src/auth.js; duplication × src/report.js
     - ✅ iteration 2/3 — all checks passed
     ✅ done — review published.
     [Full state & audit trail](…/blob/agentic-state/grumpy/pr-9.yaml)
     ```
   - The final **review** (REQUEST_CHANGES / APPROVE) is the deliverable.
4. **If checks fail**, you don't see half-baked output — the agent silently
   iterates (a second run), and only checked output is ever published. After
   `max_iterations`, the engine posts a clear failure instead of going quiet.
5. **Inspect the record** any time: the status-comment link, the `agentic-state`
   branch (`git log agentic-state -- grumpy/pr-<N>.yaml`), or the Actions tab
   (one orchestrator run + one agent run per iteration).

The mental-model shift from plain gh-aw: **the PR/issue is the unit of
existence, and workflow runs are heartbeats that advance it** — not the other
way round. A protocol can sit waiting (a future human gate) for weeks at zero
cost, because "waiting" is just a line in a committed file.

---

## 6. Operational setup

Secrets on the repo:

- `ANTHROPIC_API_KEY` — the LLM auth token (set with `gh secret set NAME
  --body "$VALUE"`; **not** `--body -`, which stores the literal `-`).
- `ANTHROPIC_BASE_URL` — the endpoint (also a literal in the agent frontmatter).
- `POC_DISPATCH_TOKEN` — a PAT (repo + workflow scopes) used for the
  state-branch push, the `workflow_dispatch` of the agent, the
  `repository_dispatch` re-entry, and the PR-label read (PR labels need the
  `pull-requests` scope, which the default `GITHUB_TOKEN` lacks).

Publication of the PR review uses the default `GITHUB_TOKEN` (the bot), because
GitHub forbids a PR author from reviewing their own PR and the PAT is the author.
A fully-clean result falls back from APPROVE to COMMENT unless the repo's
"Allow GitHub Actions to approve pull requests" setting is enabled.

Keep `orchestrator.yml` and the agent lock on the **default branch** — that's
where workflows run from for `issue_comment` / `repository_dispatch` events.

---

## 7. Design principles to carry forward

- **Evidence over prose.** A state's contract is its evidence schema; the prose
  only explains how to satisfy it.
- **Omission → commission.** Force a verdict for every rubric cell so the
  agent's cheapest failure (skipping) becomes a detectable explicit claim.
- **Coverage, not yield.** Demand a verdict for every cell, where "nothing
  found" is legal — so the agent is never pressured to fabricate findings
  (Goodhart-safe).
- **Independent ground truth.** Checks re-derive what they verify against;
  never trust agent-produced data.
- **Form vs. substance.** Code checks form; judges/humans check substance.
- **Graduated failure rungs.** repair < drop < iterate < gate — reach for the
  cheapest remedy that fits (v1 implements only `iterate`).

See `STATUS.md` for what is and isn't implemented, and the spec/plan under
`agent-factory/docs/superpowers/` for the full design history.
