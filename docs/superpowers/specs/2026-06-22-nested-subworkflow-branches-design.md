# Nested sub-workflow branches + data-carrying human gate

**Date:** 2026-06-22
**Status:** Design approved; ready for implementation planning.
**Target protocol:** `recover-mental-model-stub` — the real two-workflow protocol
this engine capability unlocks (one automated leg ∥ one human-gated leg → join →
combine). It lives in `.github/agent-factory/protocols/recover-mental-model-stub/`
and is built as a follow-on once Plans 1-4 land; the engine work itself is
protocol-agnostic and exercised by the `subpipeline-mini` test fixture.

## Motivation

A protocol is needed where two workflows run in parallel and join at the end —
the existing fanout/join shape — but **one of the two legs is not fully
automated**. That leg must:

1. run an agent that produces output **and a list of questions for the user**,
2. **pause** for the user to answer those questions,
3. run a second agent that consumes the answers to produce its final output.

After both legs finish, a final stage **combines** the two outputs (for now, a
deterministic append; the engine should also allow an agent-based or
publish-only combine).

### Why this is not supported today

Grounded in the current engine (`.github/agent-factory/engine/`):

- **A fanout branch is flat.** `lib.resolve_agent_unit` (`lib.py:64`) resolves a
  branch to exactly one `agent_state` + one `max_iterations`. A leg is a single
  agent running an iterate-until-checks-pass loop, then publish + `fire_join`
  (`advance.py:398-407`). There is no way to nest `agent → gate → agent` inside a
  leg.
- **The gate carries no data.** `open_gate` (`lib.py:238`) renders static text
  (`/approve`, `/request-changes`, `/reject`); `do_resolve_gate` (`next.py:266`)
  records only a 3-way decision + free-text `reason`. Crucially, human input is
  **never** plumbed into a downstream agent — `seed_and_dispatch_phase` always
  dispatches the next phase with `feedback: ""`. The only thing that becomes
  agent feedback is *check verdicts* (the iterate loop). There is no human→agent
  and no agent→agent input channel.
- **Combine-after-join is explicitly unimplemented.** `join.py:129`: *"A
  multi-fan-out pipeline would instead advance from the JOIN state's `.next`;
  that is intentionally not supported yet."*

## Target shape

```
top-level pipeline:
  review   (fanout)
    ├─ A (single agent)                       ← unchanged: a 1-state leg
    └─ B (sub-pipeline):
         draft    (agent)  → emits questions[]
         clarify  (gate)   → human /answer, coverage-checked
         finalize (agent)  → inputs:[clarify.answers, draft.evidence]
  join     (AND-barrier; B terminal only when finalize done)
  combine  (merge hook | agent | publish)     ← inputs:[A.out, B.out]
  → done
```

## Decisions (from brainstorming)

1. **Nesting generality: linear sub-pipeline.** A branch becomes an *ordered
   sequence* of states (agent / gate). No nested fanout/join inside a branch.
   Reuses the existing multi-phase sequencer one scope down; bounded to depth 2.
2. **Answer model: structured + coverage-checked.** The agent emits questions as
   an evidence artifact (each with an id); the human replies with `id → answer`
   pairs; a deterministic check verifies every question is answered before the
   branch resumes. Matches the engine's "demand evidence, check it
   deterministically" philosophy.
3. **Combine stage: deterministic merge hook for this use case, but the engine
   supports all three modes** — (1) trusted merge hook, (2) agent combine,
   (3) publish-only.
4. **Implementation approach: unify the sequencer (Approach A).** "Top-level
   phase advance" and "a branch sub-pipeline" are the same loop at different
   **scopes**. Give a fanout branch its own cursor and run the existing
   seed→dispatch→advance machinery scoped to the branch. (Rejected: Approach B,
   branch references a separate sub-protocol — heavier, risks level drift;
   Approach C, inline special-case — not general.)

## Design

### 1. Scope model

The engine keys every operation off `PHASE` + `BRANCH`. Add a third coordinate:
**the branch's own sub-state cursor**, making a leg a sequencer identical to the
top-level phase sequencer, one scope deeper.

- **Branch cursor** = the existing `review.<branch>.yaml` file (the one
  `join.py` already reads). It gains `sub_state: <id>`; its `state` field stays
  the leg-life (`in-flight | done | failed`). **Join is therefore unchanged**: a
  branch is `done` only when its last sub-state finishes.
- **Per-sub-state files** = `review.<branch>.<substate>.yaml` — each an ordinary
  agent/gate state file (iteration, history, gates).
- **Detection**: a branch with a `states: [...]` array is a sub-pipeline; a
  branch with a flat `workflow` is today's single-agent leg (branch **A** stays
  byte-identical, no path change).

Example state layout for branch B:

```
code-review/pr-N/review.B.yaml            {sub_state: clarify, state: in-flight}
code-review/pr-N/review.B.draft.yaml      (agent state)
code-review/pr-N/review.B.clarify.yaml    (gate state: questions + answers)
code-review/pr-N/review.B.finalize.yaml   (agent state)
```

### 2. Branch advance loop (reuse, not duplication)

Generalize `seed_and_dispatch_phase` (next.py) and the agent-phase advance block
(`advance.py:364-397`) to take a **(scope, cursor-file)** parameter. On a
sub-agent's `done`:

- **next sub-state exists** → advance the *branch cursor* `sub_state`, seed +
  dispatch it (agent → dispatch agent; gate → `open_gate` scoped to the branch);
- **last sub-state** → set branch cursor `state: done`, `fire_join` (today's
  leg-done path, unchanged);
- **gate blocked / rejected** → branch cursor `state: failed`, `fire_join` → the
  AND-barrier fails.

Same decision fold, same dispatch verbs — only the cursor location and the
re-dispatch payload (carry `branch` + a new `substate`) differ. The single-agent
leg (no `states`) takes the existing `done → fire_join` path verbatim.

### 3. Inputs channel + output persistence

Two additive pieces:

1. **Persist outputs.** When an agent/gate state completes, write its artifact
   beside its state file:
   - agent → `…<substate>.evidence.json`
   - gate → `…<substate>.answers.json`

   Today evidence is ephemeral (passed to publish then discarded); persisting it
   makes outputs durable and addressable for downstream `inputs`.
2. **Resolve inputs.** A state may declare:

   ```json
   "inputs": [{ "from": "<state-id>", "as": "<name>" }]
   ```

   Before dispatch, the **plan job** (zone 1, which holds the state branch) reads
   the referenced persisted artifacts and hands them to the consumer:
   - to an **agent** as a downloaded workflow artifact under `inputs/<name>.json`
     (the agent stays read-only and never touches the state branch);
   - to a **merge hook** as file-path args.

   Exact transport (workflow-artifact vs read-only state checkout) is a
   plan-time detail; **workflow-artifact is the recommended default** because it
   preserves the agent's read-only, no-state-PAT posture.

### 4. Data-carrying human gate (the core new capability)

1. `draft` agent emits evidence containing `questions: [{id, text}]` (its
   evidence schema requires the array; a schema-valid check guards form).
2. On `draft` done → evidence persisted; branch cursor advances to `clarify`
   (gate). `open_gate` is extended: a gate declaring `questions_from: draft`
   renders those questions, numbered, into the PR comment together with the
   `/answer` syntax — replacing the static approve/reject text for that gate.
3. New command **`/answer`**. The human comments `/answer q1: … q2: …`.
   `do_resolve_gate` gains an `answer` branch that parses `id → value` pairs and
   **accumulates** them into the gate's `answers.json` (multiple comments
   allowed; later answers for the same id overwrite).
4. A deterministic **`answers-coverage`** check (sibling of `rubric-coverage`)
   verifies every question id from the upstream questions has a non-empty
   answer. Missing ids → gate stays open, posts which ids are outstanding.
5. Coverage passes → gate `state: answered`, answers persisted, branch cursor
   advances to `finalize`.
6. `finalize` declares
   `inputs: [{from: clarify, as: answers}, {from: draft, as: draft}]`; the engine
   materializes them; the agent consumes.

**The existing approve/request-changes/reject gate is untouched** — data-carrying
is an additive gate *mode*, keyed by the presence of `questions_from`. A plain
gate stays a pure decision.

**Gate resolution scoping.** The gate now lives at branch scope, so `/answer`
resolves the target by scanning branches for a sub-state of kind `gate` in state
`open`. Question ids are branch-namespaced; if two gates are ever open at once,
`/answer <branch> …` disambiguates. (This protocol has only one gated branch →
always unambiguous.)

### 5. Combine / merge state (all three modes)

Extend `join.py` past its current `:129` limitation. When all branches are
`done`, inspect the join's `.next`:

- `kind: "merge"` → run a trusted reduce **hook** (zone 4, like publish) with
  both branch outputs materialized as file args → e.g. `append-outputs.py`.
  **(mode 1 — this protocol)**
- `kind: "agent"` + `inputs` → dispatch a normal agent (own evidence schema +
  checks) that synthesizes the merged result. **(mode 2)**
- `.next` is `done` or a gate → today's behavior (gate-after-join already exists
  at `join.py:86-108`); the branches each already published, nothing is merged.
  **(mode 3)**

So `join.py`'s all-done path becomes: `.next` is a gate → open it (today);
elif `.next` is `merge`/`agent` → seed + dispatch it via the shared advance
machinery; else finalize (today).

### 6. Trust zones (invariant preserved, one scope deeper)

The invariant holds: the engine and the agent never share a job or credential.

- Inputs into an **agent** (zone 2, read-only, no state PAT): answers (human) and
  prior evidence (agent-produced) are untrusted but harmless — the agent is
  read-only. Materialized as files, never shell.
- Inputs into a **merge hook** (zone 4, trusted, holds PUBLISH_TOKEN): passed as
  file-path args; the hook reads JSON as *data* (parse/append), never evals —
  the same discipline existing publish hooks already follow.
- `/answer` text is human-supplied (untrusted): parsed in zone 1 (state PAT), and
  per the standing security rule it is carried via `env:`/files and **never**
  interpolated into a `run:` block. Structural validation (coverage) happens in
  the credential-less checks job (zone 3).
- No new credential crosses a zone boundary.

### 7. protocol.json schema changes (additive, backward-compatible)

- a fanout `branch` may carry `states: [...]` **instead of** the flat
  `workflow/evidence/checks/publish` (a branch with `workflow` and no `states`
  is the existing single-agent leg);
- any `state` may carry `inputs: [{from, as}]`;
- a gate may carry `questions_from: <state-id>` + an answers-coverage check;
- new top-level `kind: "merge"` (`hook` + `inputs`);
- new trigger `{ "on": "issue_comment", "comment_prefix": "/answer",
  "command": "answer" }`.

Every new field is optional ⇒ the single-agent path and the current `code-review`
pipeline are untouched; the existing test suite stays green.

### 8. Testing

- New fixture `tests/fixtures/subpipeline-mini/`: A (single agent) ∥ B
  (draft → clarify → finalize) → join → merge.
- Unit (pytest, the house pattern):
  - branch sub-pipeline sequencing (seed → advance → advance → done);
  - inputs resolution + materialization;
  - data-carrying gate: questions render, `/answer` accumulation, coverage
    pass/fail, advance to `finalize`;
  - merge state in all three modes;
  - join: branch terminal only after the sub-pipeline completes; combine-after-
    join advance from the join's `.next`.
- Regression: single-agent + existing fanout fixtures byte-identical; the current
  `code-review` protocol unchanged.

## Out of scope / deferred (YAGNI)

- Nested fanout/join inside a branch (depth > 2). Linear sub-pipelines only.
- Generalized recursion via a referenced sub-protocol document (Approach B).
- A typed/validated answer schema beyond non-empty coverage (free-text answers
  per id for now).
- Smarter merge reducers (dedupe/reconcile) — the hook is pluggable, so this is a
  future protocol's concern, not an engine change.

## Plan-time details (not design forks)

- **Input transport:** workflow-artifact (recommended) vs read-only state
  checkout by the agent job.
- **`/answer` parse grammar:** recommended `qID: value`, one or many pairs per
  comment, accumulated across comments.
