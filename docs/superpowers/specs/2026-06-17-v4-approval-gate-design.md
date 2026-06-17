# v4 — Pause-and-Require Approval Gate — Design

**Date:** 2026-06-17
**Status:** design approved; ready for implementation plan
**Milestone:** v4 — human-in-the-loop (approval gate). This is the broader
**pause-and-require** `kind:"gate"` state — a human sign-off as a *required*
transition. It is distinct from the already-shipped `/override` escape-hatch
(`docs/superpowers/specs/2026-06-17-hitl-override-gate-design.md`), which only
rescues an *already-blocked* gate. This is the first real use of the reserved
`gates:` field in the state model.

## Problem

v1/v2 gate purely on deterministic checks (form) and an agent's verdict. There is
no point in any protocol where a **human's explicit sign-off is a required
transition**. A human gate makes "waiting for a person" a first-class, zero-cost
protocol state — a line in the committed state file — consistent with the "PR is
the unit of existence; runs are heartbeats" model. A protocol can sit gated for
weeks at no compute cost, then advance the instant a human comments.

The `/override` escape-hatch is the *inverse*: it forces a gate that already ran,
blocked, and halted to move forward. v4 is the *proactive* primitive — a state
that deliberately pauses and will not advance until a human decides.

## Scope & locked decisions

Settled during brainstorming; binding for the plan:

1. **Generic engine primitive.** A new phase `kind: "gate"`, placeable anywhere
   in any protocol. No protocol-specific gate logic lives in a protocol
   directory — only the `gate` phase declaration + trigger declarations (data).
   A protocol with no `gate` phase is completely unaffected (byte-identical
   state).
2. **Demo placement: final sign-off gate.** In `code-review-pipeline`:
   `preflight → review fan-out → join → approval (gate) → done`. A human must
   approve the completed review before the pipeline is marked done.
3. **Trigger: slash-command comments.** `/approve`, `/request-changes`,
   `/reject` PR comments, routed through the existing `issue_comment →
   match_trigger` seam. Each may carry optional free-text after the command.
   - **Known limitation (documented, not a bug):** GitHub's *native* "Approve"
     button (a `pull_request_review` event) does **not** resolve the gate — only
     a `/approve` *comment* does. Accepting native reviews too is an easy
     additive follow-up (the "Both" option) and is **out of scope** here.
4. **Three-way outcome.** `approve` / `request-changes` / `reject` (see
   "Outcome semantics").
5. **Authorization: GitHub permissions API.** Honor a decision only if the
   commenter's repo permission ∈ `{write, admin}`, read via
   `GET /repos/{owner}/{repo}/collaborators/{login}/permission`. Commenter
   identity comes from the **trusted event context**
   (`github.event.comment.user.login`), never the comment body. Reuses the
   `/override` auth gate.
6. **No self-approval.** `/approve` from the PR author is refused (data-driven by
   a protocol flag `approve_excludes_author`, default behavior in the demo:
   forbidden), mirroring branch-protection's "no self-approval" norm.
   `/request-changes` and `/reject` by anyone with write (including the author)
   are allowed.
7. **`/override` does NOT apply to human gate decisions.** Neither `reject` nor
   `request-changes` writes the `halted:{reason:blocked}` marker that `/override`
   understands. Clean separation: `/override` is for agent/deterministic blocks
   only; a human "no" is overturned only by another human decision or a new
   commit.
8. **State home: per-phase file.** The gate gets its own state file
   `<instance>/<gate-id>.yaml` carrying the reserved `gates:` field; this is its
   first real use. `_instance.yaml` keeps only the cursor + existing cross-phase
   keys.

## The new state primitive

A phase `kind: "gate"`. Unlike `agent`/`fanout`, it **dispatches no agent and
runs no checks** — a pure human-decision phase, zero LLM cost. When the cursor
lands on it, the engine seeds the gate, emits an "awaiting approval" check-run,
and the run **ends**. The gate sits open in committed state until a human comment
wakes the engine.

### protocol.json (the demo)

```jsonc
{ "id": "join",     "kind": "join", "of": "review", "next": "approval" },
{ "id": "approval", "kind": "gate", "next": "done",
  "approve_excludes_author": true }
```

Triggers gain three comment prefixes, all routed to one command:

```jsonc
{ "on": "issue_comment", "comment_prefix": "/approve",         "command": "resolve-gate" },
{ "on": "issue_comment", "comment_prefix": "/request-changes", "command": "resolve-gate" },
{ "on": "issue_comment", "comment_prefix": "/reject",          "command": "resolve-gate" }
```

### Per-phase gate state file

`code-review-pipeline/pr-N/approval.yaml` — first real use of `gates:`:

```yaml
protocol: code-review-pipeline
instance: pr-N
state: approval            # cursor == this phase ⇒ gate is live
head_sha: <sha at gate open>
gates:
  state: open              # open | approved | changes_requested | rejected
  history:                 # append-only audit, every decision
    - {decision: changes_requested, actor: alice, reason: "fix null check"}
    - {decision: approved,          actor: bob,   reason: "lgtm"}
```

`_instance.yaml` is unchanged in shape — it still holds only the cursor
(`phase: approval`) and the existing cross-phase keys (`halted` / `overrides` /
`joined`).

## Lifecycle & the `resolve-gate` command

### Opening the gate

`seed_and_dispatch_phase(phase, cmd)` in `next.py` grows a third branch alongside
`agent` / `fanout`: when `kind == "gate"` it

- writes the cursor to `_instance.yaml` (`phase: approval`),
- seeds `approval.yaml` with `gates: {state: open, history: []}`,
- emits the check-run `code-review-pipeline/approval` as `in_progress`
  ("⏳ Awaiting human approval — comment `/approve`, `/request-changes`, or
  `/reject`"),
- updates the status comment,
- emits **no dispatch action**: `{"action": "noop", "reason": "gate-open:approval"}`.

The run ends; state is durable. The engine waits naturally — there is nothing to
dispatch.

### Resolving the gate

A new `resolve-gate` command in `next.py` (zone 1, state PAT). It reads
`GATE_DECISION` / `GATE_ACTOR` / `GATE_REASON` from env (set by the workflow
`ctx` step, mirroring the shipped `OVERRIDE_*`), loads `approval.yaml`, and
dispatches on decision.

**A gate is "live"** when the cursor is on it **and**
`gates.state ∈ {open, changes_requested}`. All three decisions are valid while
live (so a reviewer may re-request changes, or escalate `changes_requested → reject`,
or flip `changes_requested → approve`). The two non-live states are `approved`
(cursor has already advanced off the gate) and `rejected` (terminal).

| decision | guard | effect |
|---|---|---|
| `approve` | gate live; actor ≠ PR author (if `approve_excludes_author`) | append history; `gates.state: approved`; check-run → `success`; call existing `seed_and_dispatch_phase(next)` → advance one phase (or `done` if last) |
| `request-changes` | gate live | append history; `gates.state: changes_requested`; check-run → `failure`; status note; **no cursor move** (non-terminal halt); **no** `halted` marker |
| `reject` | gate live | append history; `gates.state: rejected`; phase `state: failed`; check-run → `failure` (terminal); **no** `halted` marker |

### Resuming after the two "no"s (reuses today's behavior — no new code)

- A `changes_requested` or `rejected` gate is revived by **a new commit** → the
  existing `synchronize → reset` re-seeds the *first* phase and the whole
  pipeline reruns, reopening the gate on the fresh review.
- A `changes_requested` gate also accepts a later **`/approve`** (reviewer
  changed their mind) — hence `approve`'s guard treats `changes_requested` as
  resolvable, not just `open`.
- `reject` is terminal: a later `/approve` is refused ("this gate was rejected;
  push a new commit or `/review` to restart").

### Guards & refusals

Every `resolve-gate` that does not result in a state change posts exactly one
explanatory PR comment and makes **no** state change (the one-path pattern from
`/override`):

| Situation | Detected by | Message (sketch) |
|---|---|---|
| Not authorized | permission ∉ {write,admin} | `@<login> resolving this gate requires write access to this repository.` |
| Author self-approve | decision==approve, actor==PR author, `approve_excludes_author` | `@<login> the PR author cannot approve their own gate.` |
| No live gate / already approved | cursor not on the gate (e.g. already approved and advanced) | `Nothing to resolve — no approval gate is currently open for this PR (current phase: <phase>).` |
| Any decision on a rejected gate | cursor on gate, `gates.state == rejected` | `This gate was rejected; push a new commit or comment /review to restart the pipeline.` |
| No pipeline for this PR | no `_instance.yaml` | `Nothing to resolve — no code-review-pipeline run exists for this PR.` |

### Idempotency / races

The decision flips `gates.state` away from `open` inside the CAS push, so a
double-comment falls through to "no gate open" — it cannot double-advance. A
racing CAS loss re-reads state via the existing single-rebase retry in
`cas_push` and re-evaluates against fresh state.

## Workflow seam & authorization

**Orchestrator** (`agentic-orchestrator.yml`): no `on:` change — `/approve` etc.
arrive on `issue_comment`, already in the union trigger block. `lib.route`
already scans `triggers`, so the three new prefixes route to
`code-review-pipeline` with zero router edits.

**Engine `ctx` step** (`agentic-engine.yml`) gains a `resolve-gate` branch,
structurally identical to the shipped `override` branch:

```
match_trigger(issue_comment, body) → command="resolve-gate"
   │
   ├─ GATE_DECISION = which prefix matched   (/approve | /request-changes | /reject)
   ├─ GATE_ACTOR    = github.event.comment.user.login        ← trusted context, never body
   ├─ GATE_REASON   = body with the prefix stripped          ← untrusted display text
   │
   └─ AUTH GATE:  perm = gh api .../collaborators/$GATE_ACTOR/permission
        ├─ perm ∉ {write,admin}                  → command="resolve-gate-denied" (post denial, stop)
        ├─ decision==approve AND GATE_ACTOR==PR author AND approve_excludes_author
        │                                         → command="resolve-gate-denied" (self-approval msg)
        └─ otherwise                              → command="resolve-gate"
```

Two derivations to pin in the plan:

- **Which prefix matched.** `match_trigger` returns only the command string. The
  `ctx` step re-derives `GATE_DECISION` by prefix-testing the body against
  `/approve` / `/request-changes` / `/reject`, **longest-prefix first** so
  `/request-changes` isn't shadowed by a `/request` substring or similar. We keep
  `match_trigger` a pure command-resolver (unchanged ABI) and derive the decision
  keyword in `ctx` — the smaller change.
- **PR author identity** for the self-approval guard comes from the trusted event
  payload (the PR author login), not the comment body.

**Security invariants held:**

- `GATE_REASON` (untrusted) flows only via `env:`, never interpolated into a
  `run:` block — used solely as escaped display text in the audit comment /
  commit message (standing CLAUDE.md injection rule).
- Auth is read-only; all state mutation stays in `next.py` (zone 1).
- The human comment is a wake-up event, not a state carrier — every load-bearing
  value is re-derived from `approval.yaml`.
- One denial path: `resolve-gate-denied` (auth/self-approval) and the in-`next.py`
  guard refusals route to the same PR-comment helper.

**Token note (resolve in plan):** the collaborator-permission API needs push
access on the calling token — same finding as `/override`; reuse
`POC_DISPATCH_TOKEN`, verified live.

## Check-runs, status comment, audit

### Check-run `code-review-pipeline/approval`

| moment | status / conclusion | title |
|---|---|---|
| gate opens | `in_progress`, no conclusion | ⏳ Awaiting human approval |
| `/approve` | `completed` / `success` | ✅ Approved by @bob |
| `/request-changes` | `completed` / `failure` | 🔁 Changes requested by @alice |
| `/reject` | `completed` / `failure` | ⛔ Rejected by @carol |

GitHub renders an `in_progress` check-run as a pending dot indefinitely — exactly
"a protocol sitting gated for weeks" made visible, and it makes the gate eligible
as a required status check (the merge stays blocked until someone approves). No
new `pending` status is invented; the engine already only emits
`in_progress` / `completed`.

### Status comment

`render_pipeline_status_body` gains a gate row. While open:
`approval — ⏳ awaiting human sign-off (/approve · /request-changes · /reject)`.
After: `approval — ✅ approved by @bob` / `🔁 changes requested by @alice` /
`⛔ rejected by @carol`, with the reason appended (escaped). Mirrors how
agent/fanout phases already render.

### Audit (what stays truthful)

- `gates.history` in `approval.yaml` is append-only — every decision (including a
  `changes_requested` later flipped to `approved`) is preserved, never
  overwritten.
- The CAS commit message names actor + decision, so
  `git log agentic-state -- code-review-pipeline/pr-N/approval.yaml` is a complete
  gate audit.
- A `request-changes` / `reject` followed by a new commit triggers `reset`, which
  (per the shipped restart-reset behavior) abandons the old status comment with a
  superseded banner and starts fresh — the prior round's gate history remains in
  git history.

## Files touched

**Engine (generic — the gate is an engine capability):**

- `.github/agent-factory/engine/next.py` — `seed_and_dispatch_phase` gains the
  `kind == "gate"` branch (seed + check-run + `noop`); new `resolve-gate` command
  (decision dispatch, guards, audit append, advance/halt).
- `.github/agent-factory/engine/advance.py` — **untouched for the open/resolve
  path** (gate resolution lives entirely in `next.py`/zone 1, like `override`).
  Touched only if the shared status-renderer helper needs the gate row.
- `.github/agent-factory/engine/lib.py` — gate row in
  `render_pipeline_status_body`; reuse the existing PR-comment helper for
  guard/denial messages; confirm `kind == "gate"` is correctly **excluded** from
  agent/fanout-only paths (`phase_states()`, fanout detection) while
  `next_phase_id` (kind-agnostic cursor math) already handles it.
- `.github/workflows/agentic-engine.yml` — `ctx` gains the `resolve-gate` branch
  + auth gate + denial-comment step.

**Protocol (data only):**

- `.github/agent-factory/protocols/code-review-pipeline/protocol.json` — the
  `gate` phase + three triggers + `approve_excludes_author`.

**Docs:**

- `docs/BACKLOG.md` (v4 → shipped), `docs/STATUS.md` (document the gate
  primitive).

The engine/protocol separation holds: the only protocol-directory change is the
phase + trigger declarations (data); all logic is in the generic engine.

## Testing

New `tests/test_gate.py`, pytest style (`tmp_path` state dir, `ENGINE_LOCAL=1`,
`run_engine` / `read_state_yaml` helpers). Auth and comment-posting are GitHub
I/O, tested at the seam, not live.

- **Open:** cursor → gate ⇒ `approval.yaml` seeded `gates.state: open`, `noop`
  action emitted, no agent dispatch.
- **approve:** open gate + `resolve-gate`/approve ⇒ `gates.state: approved`,
  history appended (actor + reason), cursor advances one phase
  (run-agent/run-fanout, or `done` if the gate is last).
- **request-changes:** ⇒ `changes_requested`, no cursor move, no `halted` marker;
  then a later `/approve` resolves it (changes_requested treated as approvable).
- **reject:** ⇒ phase `state: failed`, `gates.state: rejected`, no `halted`
  marker; a later `/approve` refuses with the terminal message.
- **Guards (one each):** unauthorized; author self-approve (with and without
  `approve_excludes_author`); no-gate-open / already-decided; wrong phase ⇒ no
  state change, refusal signalled (the workflow turns it into a comment).
- **Reason is inert:** a reason with shell/markdown metacharacters lands verbatim
  in `gates.history` and is treated as data, never executed.
- **Idempotency:** second identical comment after a resolve ⇒ "no gate open", no
  double-advance.
- **Regression guard:** the v1/v2 + pipeline + override suites stay green — a
  protocol with no `gate` phase produces byte-identical state. The feature is
  purely additive (new kind, new command, new triggers, new per-phase file).

**Auth:** unit-tested in `next.py`/lib where pure; asserted by construction at
the YAML seam in the workflow. The permissions-API call is workflow glue,
verified in the live checkpoint, not in pytest.

**Live checkpoint (post-merge, manual)** on a PR taken through to the gate:

1. check-run shows pending ("awaiting approval");
2. non-write user `/approve` → denial comment, no advance;
3. PR author `/approve` → self-approval denial, no advance;
4. reviewer `/request-changes` → halt + status note; a new commit reruns the
   pipeline and reopens the gate;
5. reviewer `/approve` → cursor advances to `done`; status comment + git log show
   the approval; check-run goes green.

## Out of scope

- Accepting a **native** `pull_request_review` (the "Approve" button) to resolve
  the gate — easy additive follow-up; capture in backlog.
- `/override` applying to a human gate decision (rejected: decision 7).
- A label-based trigger (rejected in favor of comments).
- Any multi-approver / quorum gate (single write-access decision suffices here).
