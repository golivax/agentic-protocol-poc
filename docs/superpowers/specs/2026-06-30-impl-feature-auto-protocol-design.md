# `impl-feature-auto` — autonomous feature-from-issue protocol (design)

**Date:** 2026-06-30
**Status:** design approved (brainstorming), ready for planning
**Author:** brainstormed with the user

---

## 1. Summary

A new engine protocol, **`impl-feature-auto`**, that implements a feature described
in a GitHub issue **autonomously** — no human interaction mid-run — and opens a PR
the maintainer reviews. A maintainer comments **`/impl-feature-auto`** on an issue;
the engine runs a two-node pipeline:

1. **`design`** — produce a spec doc with an **Accountability Ledger**, then invoke
   the superpowers `writing-plans` skill to produce an implementation plan. This node
   is checked **rigorously** (a thorough deterministic ledger check + spec/plan
   presence gates). It writes no code and opens no PR.
2. **`implement`** — only if `design` passed: execute the plan with subagent-driven
   TDD, finish the branch, and open a single PR (spec + plan + code + tests). This
   node has **no checks** — the existing **`/review` (code-review)** pipeline, whose
   preflight already scrutinises spec/plan adherence, is the substantive gate on the
   resulting PR.

The agent runs the **superpowers** skill library (the same skills a human Claude Code
session uses), vendored into the gh-aw runtime at a pinned version.

This is the first of a planned **family of three** protocols (see §13).

## 2. Goals / Non-goals

**Goals**
- Turn an issue into a reviewable PR with **zero human input mid-run**.
- Make the run **accountable**: every gap the agent filled is recorded in a
  structured Accountability Ledger that is deterministically checked for
  completeness, internal consistency, and honest triage.
- **No spec/plan ⇒ no PR**, by construction (not by creating-then-discarding).
- Keep the agent **read-only / tokenless**: the PR is opened by gh-aw `safe-outputs`,
  the issue summary by the engine's trusted publish zone.
- Reuse the existing engine with **minimal, additive** changes (issue-keying).
- Be **engine-swappable** in principle (claude today; codex/others later) — only the
  superpowers staging path differs per engine.

**Non-goals**
- Judging whether the **code is correct / good** — that is the human's PR review and
  the `/review` pipeline's job. This protocol checks the *form* of the design
  artifacts, never the *substance* of the implementation.
- Mid-run human collaboration (that is the future `/impl-feature` protocol).
- Bug-fixing (that is the future `/fix-bug` protocol).
- A UI/API (`workflow_dispatch`) entry point — comment-driven only for now (the
  dispatch entry, like `recover-mental-model`, can be added later).

## 3. Background — why it fits (and bends) the engine model

The engine's thesis: *a workflow run is one transition of a state machine whose state
lives in git; the agent is read-only and affects the world only through an
`evidence.json` the engine's checks inspect deterministically; trusted zone-4 code
acts on the verdicts.*

This protocol is the first **write-heavy** one (it must produce code and a PR), which
bends the model in two controlled ways:

- **The deliverable write (the PR) is owned by gh-aw `safe-outputs`**, not by a
  zone-4 publish hook (in `code-review` the publish hook does the write — posts the
  review). The agent itself stays read-only/tokenless; gh-aw's vetted safe-outputs
  job performs the PR write. The engine's publish hook here is lighter: it
  **summarises and links** the PR after checks, and **closes** it on a blocked run.
- **"Correctness" is not deterministically checkable.** We lean into this: the
  `design` node is checked hard (its artifacts have a strict grammar); the
  `implement` node is unchecked and hands off to `/review`.

Everything load-bearing is still re-derived from durable state; events are wake-ups;
state advances by fast-forward CAS push. No engine invariant is weakened.

## 4. Architecture overview

```
impl-feature-auto  (sequence; trigger: /impl-feature-auto on an issue; instance key issue-<N>)
├─ design     (agent)   ← Phase 0 spec + ledger → writing-plans → plan;  rigorously checked
└─ implement  (agent)   ← execute plan (TDD) → finishing-branch → open PR;  no checks
```

Files created (no edits to `.github/agent-factory/engine/` except the additive
issue-keying in §10):

```
.github/agent-factory/protocols/impl-feature-auto/
  protocol.json
  design.evidence.schema.json
  implement.evidence.schema.json
  checks/
    ledger-wellformed              # ledger layer 1
    ledger-consistent              # ledger layer 2
    read-these-first-consistent    # ledger layer 3
    spec-present
    plan-present
  publish/
    post-summary                   # zone-4: summarise+link PR (or close on block)

.github/workflows/
  impl-feature-auto-design-agent.md       (+ .lock.yml via `gh aw compile`)
  impl-feature-auto-implement-agent.md     (+ .lock.yml)
```

## 5. The `design` node

**Does:** the user's *Phase 0* — produce a spec doc at
`docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` containing Summary, Scope,
Behavior/acceptance criteria, the **Accountability Ledger**, and **READ THESE
FIRST** — then invoke superpowers `writing-plans` on that spec to produce a plan at
`docs/superpowers/plans/…`. **Writes no code, opens no PR.**

**Emits `evidence.json`** with the ledger as **structured data** (the enabler for a
thorough check; the spec markdown remains the human render — see §8) plus
`spec_path`, `plan_path`, `read_these_first`. **Uploads the spec + plan files as
artifacts** (the carrier — §9).

**Checks** (`max_iterations: 3`):

| Check | `on_fail` | Verifies |
|---|---|---|
| `ledger-wellformed` | `iterate` | layer 1 — per-item completeness + valid enums (§8.1) |
| `ledger-consistent` | `iterate` | layer 2 — rule-based contradictions (§8.2) |
| `read-these-first-consistent` | `iterate` | layer 3 — triage coverage, ordering, spec cross-ref (§8.3) |
| `spec-present` | `block` | a spec doc exists with the 5 required sections |
| `plan-present` | `block` | a plan doc was produced |

- **Ledger checks `iterate`** because re-running Phase 0 is **cheap** (no code, no
  PR) — the agent fixes a malformed/dishonest-triage ledger in a fast follow-up. Up
  to 3 iterations.
- **`spec-present` / `plan-present` `block`**: a missing prerequisite ends the run
  `failed` with **nothing created** — no branch, no PR. "No spec/plan ⇒ no merge
  potential" is satisfied *by construction*, because the `implement` node never runs.

## 6. The `implement` node

**Runs only if `design` reached `done`.**

**Does:** downloads the spec + plan artifacts into its checkout, executes the plan
with superpowers `executing-plans` / `subagent-driven-development` under TDD
(RED-GREEN-REFACTOR), runs `finishing-a-development-branch`, and opens **one PR** via
`safe-outputs: create-pull-request` containing **spec + plan + code + tests**. The PR
body carries the Accountability Ledger and the READ-THESE-FIRST triage list so the PR
is self-describing. Mid-implementation ledger appends (allowed by the prompt) are
written into the spec doc that ships in the PR.

- `inputs: [{ from: design, as: design }]` — threads the design evidence (spec/plan
  paths) so the node needn't guess.
- `max_iterations: 1`.
- **`checks: none`** — the existing `/review` (code-review) pipeline's preflight is
  the substantive gate on this PR; we do not duplicate it. (A check-less agent node
  passes once it produces evidence.)
- **`publish: post-summary`** — see §7.

`implement` evidence is minimal: a one-line `summary` and `pr_branch` (so
`post-summary` can resolve the PR).

## 7. Publish / discard semantics (`post-summary`, zone 4)

The **issue summary is the engine's job, not a safe-output**, because it must run
*after* the deterministic checks and be conditioned on their verdicts — a safe-output
is agent-authored content emitted *before* any check exists. `post-summary` runs in
zone 4 (the `advance` job), after `run-checks.py` folds the verdicts:

- **On `implement` done:** resolve the PR by `pr_branch`
  (`impl-feature-auto/issue-<N>`), comment on **issue #N** linking the PR + the
  READ-THESE-FIRST list; the engine's status comment + phase label track the head to
  ✅ done.
- **On a `block` at `design`:** the run ends `failed`; nothing was created, so the
  hook just records the failure on the issue (no PR to close).
- **Defensive:** if an `implement` run somehow produced no PR, the hook reports that
  rather than claiming success.

(The PR *write* itself is gh-aw `safe-outputs`; `post-summary` only summarises/links —
this is the one place the protocol's deliverable write lives outside zone 4, by
design, per §3.)

## 8. The Accountability Ledger and its checks

The ledger is the protocol's accountability surface and is checked thoroughly. The
**enabler** is emitting it as **structured data in `evidence.json`** (not only as
spec prose), so checks inspect a precise object instead of brittle markdown:

```jsonc
"ledger": [
  {
    "id": "L1",
    "category": "ASSUMPTION",            // exactly one of DECISION|ASSUMPTION|UNKNOWN|DEFERRED|DEVIATION
    "what": "…", "why": "…", "what_i_did": "…",
    "confidence": "low",                  // high | med | low
    "blast_radius": {
      "level": "high",                    // low | medium | high  — magnitude/reach of impact
      "why": "changes the response envelope for every /api/runs consumer"
    },
    "reversibility": {
      "level": "irreversible",            // reversible | costly | irreversible
      "why": "published CLI flag external scripts depend on; removing it breaks them"
    },
    "revisit_if": "…",
    "verified": true                      // required when an ASSUMPTION asserts a code fact
  }
],
"read_these_first": ["L1", "L4"]          // ids into the ledger, risk-sorted
```

The three deterministic layers (all `iterate` on the `design` node):

### 8.1 `ledger-wellformed` (completeness + enums)
Per item: `category` is exactly one of the five; every field
(`what`/`why`/`what_i_did`/`confidence`/`blast_radius`/`reversibility`/`revisit_if`)
present and non-trivial (non-empty, not "TODO"/"N/A"); `confidence ∈ {high,med,low}`;
`blast_radius.level ∈ {low,medium,high}`; `reversibility.level ∈
{reversible,costly,irreversible}`; **and** each of `blast_radius.why` /
`reversibility.why` present and non-trivial (the model must **justify** the level it
picked, not just assert it); an `ASSUMPTION` that asserts a code fact carries
`verified: true` (the prompt requires verifying those against the codebase).

> The prompt's single "Blast radius (… reversible or not)" field is split in evidence
> into two same-shaped axes: `blast_radius` (`level` + `why` — *what* breaks and how
> far) and `reversibility` (`level` + `why` — *how hard to undo* if wrong). They're
> independent: an item can be high-blast-radius but reversible, or low-blast-radius
> but irreversible.

### 8.2 `ledger-consistent` (rule-based contradictions — still deterministic)
Catches self-contradiction the grammar exposes, e.g.: an `UNKNOWN` tagged
`confidence: high` (UNKNOWN is low-confidence by definition); a `DEVIATION` with an
empty "what it conflicted with"; a `confidence: low` item with an empty `revisit_if`
(a low-confidence call with no flip-condition). Rules, not judgments.

### 8.3 `read-these-first-consistent` (honest triage)
The prompt defines READ-THESE-FIRST as the ledger sorted by *(low confidence ×
high/irreversible blast radius)*. We model that as a **risk score** over the three
typed axes:

```
risk = confidence{low:2, med:1, high:0}
     + blast_radius.level{high:2, medium:1, low:0}
     + reversibility.level{irreversible:2, costly:1, reversible:0}      # 0..6
```

Deterministically:
- every **high-risk** item (`risk >= 2`) **must appear** in `read_these_first` (no
  burying a scary item); items with `risk < 2` may be omitted;
- every `read_these_first` entry references a real ledger `id`;
- the order is **monotonic non-increasing** by `risk` (ties may be in any order);
- **cross-reference:** every ledger `id`/`what` also appears in the spec doc's Ledger
  section, so the structured JSON can't diverge from the prose the human reads.

### Out of scope (the substance boundary)
Whether a confidence rating is *calibrated*, a "reversible" claim is *true*, or a
chosen default is *wise* is **not** deterministically checkable. We deliberately do
**not** add an AI judge here (considered and declined); that judgment is the human's
PR review (and, downstream, `/review`).

## 9. Inter-node carrier — artifacts

Each node is a separate run with a fresh checkout, so the spec + plan docs are carried
from `design` to `implement` via **path-keyed artifacts** (the same pattern
`recover-mental-model`'s legs already use): `design` uploads `spec`/`plan` artifacts
in its post-steps; `implement`'s pre-step downloads them into its checkout. **Nothing
touches the repo until `implement` opens the PR**, so a failed `design` leaves zero
residue. (Rejected alternative: `design` does a `push-to-branch` — simpler data flow
but leaves a dangling branch on failure.)

## 10. Trigger, routing, and the additive issue-keying change

- **Trigger:** a `triggers[]` entry mirroring `/review`:
  `{ "on": "issue_comment", "comment_prefix": "/impl-feature-auto", "command": "start" }`.
- **Instance key:** `issue-<N>` (the issue number from the comment event). The engine
  is otherwise PR-keyed (`pr-<N>`); `recover-mental-model` already proved non-PR keys
  (`ref-`) are an **additive** change. `issue-<N>` is the same shape:
  - `lib.route` currently special-cases issue_comment **on a PR**
    (`github.event.issue.pull_request != null`); it must also accept an
    issue_comment on a **plain issue** for this protocol.
  - the engine's instance-key / checkout-ref derivation must handle the issue case
    (no PR head; the working ref is the default branch, and the feature branch is
    `impl-feature-auto/issue-<N>`).
  - **Per the DSL-stability rule, any change to the engine or the `protocol.json`
    schema is flagged to the user before it is made.** The expected surface is
    `lib.route` + the orchestrator's comment guard + instance-key derivation —
    additive, mirroring the `recover` ref-keying precedent. Confirm exact surface in
    planning.

## 11. Agent workflows + superpowers staging

Two gh-aw agents (`on: workflow_dispatch:`, dispatched by the engine), mirroring the
existing agents' frontmatter (`strict: false`, `sandbox.agent: false`, `engine.id:
claude` with the `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` `engine.env`,
`run-name` with `cid:[…]`, the "Materialize task context" step, evidence upload).

Additions:
- `permissions`: `contents: read, issues: read, pull-requests: read` (read-only).
- `tools`: `cli-proxy: true`, `edit: true`, `bash` allowlist (`gh issue view *`,
  `git *`, etc.).
- `implement` only: `safe-outputs: create-pull-request`.
- `pre-agent-steps`:
  1. **Stage superpowers** (engine-swappable `DEST`), pinned to a **stable release
     tag** via the release tarball — copy the **whole `skills/` subtree** (37
     companion files are referenced by relative path; `SKILL.md` alone is
     insufficient):
     ```bash
     SP_VERSION="v6.0.3"; DEST=".claude/skills"; mkdir -p "$DEST"
     curl -fsSL "https://github.com/obra/superpowers/archive/refs/tags/${SP_VERSION}.tar.gz" -o sp.tgz
     # optional: sha256 lock the tarball
     tar -xzf sp.tgz --strip-components=2 -C "$DEST" "superpowers-${SP_VERSION#v}/skills"
     ```
     ⚠️ Pin to the **tag** (`v6.0.3` → commit `896224c…`), **not** the `6fd4507`
     pre-release snapshot the locally-installed copy sits on.
     For codex/others, only `DEST` changes (`$CODEX_HOME/skills` or
     `~/.agents/skills`).
  2. **Prefetch the issue** deterministically (like `preflight-agent.md`'s PR
     prefetch) — the engine passes the issue **number** via `aw_context`; the agent
     reads the file, never fetches the issue itself:
     ```bash
     gh issue view "$ISSUE" --repo "$REPO" \
       --json number,title,body,labels,author,url > /tmp/gh-aw/agent/issue.json
     ```
  3. (`implement` only) **download the spec/plan artifacts** from `design`.
- **Bootstrap injection:** because gh-aw runs `claude --print` (non-interactive,
  where the SessionStart hook may not fire), the agent prompt **prepends the
  `using-superpowers` bootstrap** so the model reliably reaches for the skills. This
  is engine-agnostic (it's just prompt text).

After editing each `.md`, run `gh aw compile` and commit the `.lock.yml`.

## 12. Prompt changes (from the user's original single prompt)

The user's one prompt becomes **two**, split at its own Phase boundary:
- **`design-agent`** = Phase 0 (spec + structured ledger) + invoke `writing-plans`,
  then **stop and emit evidence** (no implementation). It must also emit the ledger
  as the structured `evidence.json` array in §8 (in addition to the spec prose).
- **`implement-agent`** = download spec/plan → `executing-plans` /
  `subagent-driven-development` with TDD → `finishing-a-development-branch` → open PR.

Other edits to both:
- the `<<< [issue title/body] >>>` placeholder → "read the feature request from
  `/tmp/gh-aw/agent/issue.json`";
- superpowers skill references lose the `superpowers:` prefix (they are staged as
  **project** skills under `.claude/skills/`, so they are bare-named: `writing-plans`,
  `finishing-a-development-branch`, …);
- prepend the `using-superpowers` bootstrap (§11).

## 13. The protocol family (naming, for context)

| Protocol | Command | id | Status |
|---|---|---|---|
| Autonomous feature from issue | `/impl-feature-auto` | `impl-feature-auto` | **this spec** |
| Interactive feature (human ↔ agent, full superpowers default pipeline incl. brainstorming + gates) | `/impl-feature` | `impl-feature` | later |
| Bug fix via `systematic-debugging` | `/fix-bug` | `fix-bug` | later |

Command-minus-slash == protocol id. The naming scheme distinguishes along the two
axes that actually differ: **task** (feature/bug) and **autonomy** (auto vs
interactive).

## 14. Testing (spec scope)

Per the repo's pytest conventions (dev-only; runtime needs only Python 3 + PyYAML):
- a `tests/fixtures/impl-feature-auto/` minimal walk (offline NODE_PATH e2e), exercising
  `design → implement → done` and a `design` **block** (no implementation, no PR);
- unit tests for each check over crafted `evidence.json` — especially the three
  ledger layers (well-formed pass; each failure mode: missing field, bad enum,
  `UNKNOWN`+`high`, buried high-risk item, mis-ordered triage, spec/JSON divergence);
- a `protocol-lint.py` structural + semantic pass on the new `protocol.json`.

Live Actions verification (a real `/impl-feature-auto` on an issue → PR, then
`/review` on that PR) is a **separate** step after build, mirroring the Stage-4c
pattern — not in this spec's scope.

## 15. Risks / open items for planning

- **Issue-keying surface** (§10) — additive but touches the engine/router; flag to
  the user before editing, confirm exact functions.
- **`safe-outputs: create-pull-request` ↔ `sandbox.agent: false`** — confirm the
  interplay (the egress firewall is already disabled in this repo's agents).
- **Artifact carrier** retention/size — spec+plan are small markdown; well within
  artifact limits; confirm the download path in `implement`'s pre-step.
- **Check-less `implement` node** — confirm the engine treats an agent node with an
  empty `checks[]` as passing on evidence production (checks are optional in the DSL).
- **Branch naming** — `impl-feature-auto/issue-<N>` must be deterministic so
  `post-summary` can resolve the PR; confirm `safe-outputs` lets the agent set it.
- **`gh aw` availability / version** in the dev environment for compiling the locks.

## Appendix A — Evidence schemas

The authoritative contracts the agents must fill. Schema enforces *shape*; the
semantic rules (conditional `verified`, the `read_these_first` risk-ordering) live in
the `ledger-*` checks (§8), which is why those checks exist.

### `design.evidence.schema.json`

```jsonc
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "impl-feature-auto · design evidence",
  "type": "object",
  "additionalProperties": false,
  "required": ["spec_path", "plan_path", "ledger", "read_these_first"],
  "properties": {
    "spec_path": { "type": "string", "description": "path to the spec doc the agent wrote" },
    "plan_path": { "type": "string", "description": "path to the plan writing-plans produced" },
    "summary":   { "type": "string" },

    "ledger": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id","category","what","why","what_i_did",
                     "confidence","blast_radius","reversibility","revisit_if"],
        "properties": {
          "id":         { "type": "string", "pattern": "^L[0-9]+$" },
          "category":   { "enum": ["DECISION","ASSUMPTION","UNKNOWN","DEFERRED","DEVIATION"] },
          "what":       { "type": "string", "minLength": 1 },
          "why":        { "type": "string", "minLength": 1 },
          "what_i_did": { "type": "string", "minLength": 1 },
          "confidence": { "enum": ["high","med","low"] },
          "blast_radius": {
            "type": "object", "additionalProperties": false,
            "required": ["level","why"],
            "properties": {
              "level": { "enum": ["low","medium","high"] },
              "why":   { "type": "string", "minLength": 1 }
            }
          },
          "reversibility": {
            "type": "object", "additionalProperties": false,
            "required": ["level","why"],
            "properties": {
              "level": { "enum": ["reversible","costly","irreversible"] },
              "why":   { "type": "string", "minLength": 1 }
            }
          },
          "revisit_if": { "type": "string", "minLength": 1 },
          "verified":   { "type": "boolean",
                          "description": "required (by ledger-wellformed) when an ASSUMPTION asserts a code fact" }
        }
      }
    },

    "read_these_first": {
      "type": "array",
      "items": { "type": "string", "pattern": "^L[0-9]+$" },
      "description": "ledger ids, risk-sorted; checked by read-these-first-consistent"
    }
  }
}
```

### `implement.evidence.schema.json`

```jsonc
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "impl-feature-auto · implement evidence",
  "type": "object",
  "additionalProperties": false,
  "required": ["summary", "pr_branch"],
  "properties": {
    "summary":   { "type": "string" },
    "pr_branch": { "type": "string", "description": "so post-summary can resolve the PR (impl-feature-auto/issue-<N>)" }
  }
}
```
