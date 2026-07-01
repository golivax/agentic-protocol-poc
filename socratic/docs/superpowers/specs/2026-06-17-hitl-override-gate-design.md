# HITL Override Gate — Design

**Date:** 2026-06-17
**Status:** design approved; ready for implementation plan
**Milestone:** the override escape-hatch (a *narrowing* of the broader "v4 —
human-in-the-loop (approval gate)" backlog item). The full pause-and-require
`kind:"gate"` approval state remains a **separate, later** effort — see
`docs/BACKLOG.md` §"v4".

## Problem

The `code-review-pipeline` protocol runs `preflight → review fan-out → join →
done` with `on_blocked: halt`. When the preflight gate blocks, `advance.py`
writes the phase `state: failed`, sets the check-runs to `failure`, CAS-pushes
`"phase preflight blocked → pipeline halted"`, and **stops** — it does not
advance the cursor and fires no dispatch. The pipeline is frozen, and there is
**no path today for a human to force it forward**: a fresh `/review` comment
re-seeds the *first* phase (it can't skip over a gate), and the only event that
jumps to a named phase (`repository_dispatch: protocol-advance`) is emitted
solely by `advance.py` itself, behind the state PAT — no human affordance.

This design adds that affordance: a write-access human can force a **blocked**
gate to advance exactly one phase, accepting the risk, while the gate's "no"
remains permanently in the audit trail.

## Scope & locked decisions

These were settled during brainstorming and are binding for the plan:

1. **Override escape-hatch only.** This is *not* the backlog's `kind:"gate"`
   pause-and-require approval state. It is an escape hatch on a gate that
   already ran, blocked, and halted.
2. **Trigger: a `/override` PR comment.** Routes through the existing
   `issue_comment → match_trigger` seam, declared in `protocol.json` triggers.
   May carry an optional free-text reason: `/override <reason>`.
3. **Authorization: the authoritative GitHub permissions API.** Honor the
   override only if the commenter's repo permission ∈ `{write, admin}`, read via
   `GET /repos/{owner}/{repo}/collaborators/{login}/permission`. The commenter
   identity is read from the **trusted event context**
   (`github.event.comment.user.login`), never from the comment body.
4. **Scope: one gate at a time.** A single `/override` clears exactly the
   currently-blocked gate and advances the cursor **one** phase. A later block is
   a separate, explicit human decision requiring another `/override`. No
   "run-to-completion" mode.
5. **Overridable state: blocked only.** A gate that *exhausted* (could not
   produce schema-valid evidence within `max_iterations`) is **not** overridable;
   `/override` on it returns a clear error message explaining the distinction.
6. **The blocked gate's verdict is never rewritten.** The blocked phase keeps
   `state: failed` and its `failure` check-run. The override is recorded *beside*
   the failure, not over it.

## Architecture: engine primitive + opt-in trigger

The override is a **generic engine capability** (it operates on the generic
multi-phase cursor + halt mechanism). A protocol *opts in* by declaring an
`/override` trigger. A protocol with no halt-gate never reaches an overridable
state, so the capability is inert for it. **No protocol-specific override logic
is added to a protocol directory** — only the trigger declaration (data).

### The one new state primitive: the `halted` marker

Today both the block-halt path (`advance.py:324-332`) and the exhaustion path
(`advance.py:398-415`) write `state: failed`. Because override is **blocked
only**, the engine must durably distinguish them. The minimal change:

- In the **block-halt** branch only, after setting the phase `state: failed`,
  stamp the instance cursor file `_instance.yaml` with:

  ```yaml
  halted:
    phase: preflight        # the blocked phase id
    reason: blocked
    sha: <PR head sha at block time>
  ```

- The **exhaustion** path writes no `halted` key (it stays `failed`, unmarked).

This marker is the single source of truth for "is there a blocked gate to
override, and which phase." It rides the existing CAS push — no new state file,
no new trust seam. (The `sha` is recorded for the audit trail and to let a future
check confirm the override targeted the head it was blocked at; the override
action itself does not require the sha to match.)

### The override action: reuse the clear-gate path

Override is mechanically identical to a naturally-cleared gate. The handler:

1. computes `next_phase_id(halted.phase)`, then
2. calls the **existing** `seed_and_dispatch_phase(next, "override")`, which
   seeds the next phase, CAS-pushes, and dispatches it.

No new state-machine code — the advance reuses what `advance.py` already runs on
a clear gate.

## Components & data flow

```
/override comment
   │
   ▼
agentic-orchestrator.yml  route job (read-only)
   • lib.route scans triggers → code-review-pipeline
   │
   ▼
agentic-engine.yml  ctx step
   • match_trigger(issue_comment, "/override") → command="override"
   • AUTH GATE: login = github.event.comment.user.login   (trusted)
                GET .../collaborators/{login}/permission
                permission ∈ {write,admin} ?
        ├─ yes → command="override"
        └─ no  → command="override-denied"  (post denial comment, stop)
   │
   ▼ (authorized)
plan job  next.py  command="override"            ← zone 1, holds state PAT
   • load _instance.yaml, read `halted` marker
   • validate (see Guards); on refusal → signal a comment, no state change
   • on success:
       - append overrides[] audit record (actor, reason, phase)
       - delete `halted` key
       - seed_and_dispatch_phase(next_phase, "override")   ← cursor advances,
         CAS-push (commit msg names actor+phase), dispatch next phase
```

**Trust-zone fit.** The human comment is a *wake-up event*, not a state carrier.
The auth check is read-only. State mutation happens only in `next.py` (zone 1,
state PAT) via the existing seeder; `advance.py` and `next.py` remain the sole
state writers. The untrusted `/override <reason>` text is carried via `env:` and
used only as escaped display text in the audit comment / commit message — never
interpolated into a `run:` block (the standing CLAUDE.md injection rule).

### Audit trail (what stays truthful)

- **Phase state:** preflight `state: failed` is **never** touched.
- **`_instance.yaml`:** gains a durable record —
  ```yaml
  overrides:
    - phase: preflight
      actor: <login>        # trusted, from event context
      reason: <free text>   # untrusted display text, escaped; "" if none given
  ```
  and loses the `halted` key (the gate is no longer halted).
- **Status comment:** updated to
  `⚠️ preflight gate was blocked — overridden by @<login>; proceeding to review.`
  (plus the reason if given).
- **Check-runs:** the `preflight` gate check-run stays `failure` (truthful); the
  pipeline-level aggregate check-run summary is updated to note the override and
  who. No new check-run flips the gate green — that would launder the failure.
- **Git log:** the CAS commit message names actor + phase, so
  `git log agentic-state -- code-review-pipeline/pr-N/_instance.yaml` is a
  complete override audit.

## Authorization detail

- Commenter login: `github.event.comment.user.login` (trusted event context).
- Check: `GET /repos/{owner}/{repo}/collaborators/{login}/permission`; require
  `.permission ∈ {write, admin}`. This is exactly "can this person push", the
  real authority for waiving a gate, and matches branch-protection semantics.
- **Token risk (resolve in the plan):** reading a collaborator's permission level
  requires the calling token to have push access to the repo. The default
  `GITHUB_TOKEN` may be insufficient; the call likely needs `POC_DISPATCH_TOKEN`
  (the repo-scoped PAT already configured). The plan verifies which token works
  and uses the minimal one.
- The comment **body** is used only to (a) match the `/override` prefix and
  (b) extract the optional reason string. No identity/permission claim is ever
  read from the body.

## Guards & error messages

Every `/override` that does not result in an advance posts exactly one
explanatory PR comment and makes **no** state change. Cases, decided by
inspecting `_instance.yaml`:

| Situation | Detected by | Message |
|---|---|---|
| Not authorized | permissions API ≠ write/admin | `@<login> /override requires write access to this repository.` |
| No pipeline for this PR | no `_instance.yaml` | `Nothing to override — no code-review-pipeline run exists for this PR.` |
| Gate exhausted, not blocked | `state: failed` but no `halted` marker (or `reason != blocked`) | `The preflight gate is exhausted (it could not produce a valid result), not blocked. Override only applies to a gate that ran and returned a blocking verdict; re-run the pipeline instead.` |
| Pipeline not halted (running / already advanced / done) | cursor present, no `halted` marker | `Nothing to override — the pipeline is not currently halted at a blocked gate (current phase: <phase>).` |
| Blocked, but no next phase | `halted` present, `next_phase_id` empty | `The blocked gate is the final phase; there is nothing to advance to.` |

**Idempotency / races.** The override clears `halted` and advances the cursor
inside the CAS push, so a second `/override` (double-comment, or after the
override already fired) falls through to the "not halted" case — it cannot
double-advance. If the CAS push loses a race, the existing single-rebase-retry in
`cas_push` re-reads state and re-evaluates; a stale override against an
already-advanced cursor degrades to the harmless "nothing to override" comment.

**One denial path.** The `override-denied` sentinel (auth failure) and the
in-`next.py` guard refusals route to the same PR-comment-posting helper, so there
is one code path for "explain why nothing happened."

## Files touched (engine + protocol data only)

- `.github/agent-factory/engine/advance.py` — block-halt branch stamps the
  `halted` marker on `_instance.yaml`; exhaustion path leaves it unmarked.
- `.github/agent-factory/engine/next.py` — new `override` command: validate
  marker, append `overrides[]`, clear `halted`, call `seed_and_dispatch_phase`.
- `.github/agent-factory/engine/lib.py` — if needed, a small helper to post a
  plain PR comment for the guard/denial messages (reuse existing comment glue if
  present); `match_trigger` already handles the new trigger via protocol data.
- `.github/workflows/agentic-engine.yml` — `ctx` step gains the auth gate
  (permissions API on the trusted login) and the `override` / `override-denied`
  command emission + denial-comment step.
- `.github/agent-factory/protocols/code-review-pipeline/protocol.json` — add the
  `/override` trigger (data only).
- `docs/BACKLOG.md` / `docs/STATUS.md` — note the escape-hatch as shipped and
  re-scope the remaining v4 approval-gate item.

The engine/protocol separation holds: the only protocol-directory change is the
trigger declaration; all logic is in the generic engine.

## Testing

All pytest, matching the suite's style (`tmp_path` state dir, `ENGINE_LOCAL=1`,
`run_engine`/`read_state_yaml` helpers). Auth and comment-posting are GitHub I/O,
tested at the seam, not live.

**New `tests/test_override.py`:**
- **Marker write:** `advance.py` through a block-halt → `_instance.yaml` gains
  `halted: {phase, reason: blocked, sha}`; through an exhaustion → **no** `halted`
  key. (Guards the core primitive.)
- **Happy path:** blocked `_instance.yaml` + `next.py command=override` → cursor
  advanced one phase, `overrides[]` appended (actor + reason), `halted` cleared,
  preflight state still `failed`, a `run-agent`/`run-fanout` action emitted for
  the next phase.
- **Guard cases (one each):** exhausted-not-blocked, no-instance, not-halted,
  blocked-but-final-phase → no cursor change; refusal is signalled (the workflow
  turns it into a comment).
- **Idempotency:** second `override` after success → degrades to "not halted", no
  double-advance.
- **Reason is inert:** a reason with shell/markdown metacharacters lands verbatim
  in the `overrides[]` record and is treated as data, never executed.

**Regression guard:** the existing v1/v2 + pipeline suites stay green — the
feature is purely additive (new trigger, new command branch, new marker key). A
protocol that never declares the trigger and never block-halts produces
byte-identical state.

**Auth:** unit-tested in `next.py`/lib where pure; asserted by construction at the
YAML seam in the workflow. The permissions-API call is workflow glue, verified in
the live checkpoint, not in pytest.

**Live checkpoint (post-merge, manual)** on a PR whose preflight blocks:
1. non-write user `/override` → denial comment, no advance;
2. write user `/override` → review fan-out launches; status comment + git log
   show the override; preflight check-run stays red;
3. `/override` on an exhausted gate → the exhausted-not-blocked comment.

## Out of scope

- The `kind:"gate"` pause-and-require approval state (backlog v4) — separate
  milestone.
- A run-to-completion override that waives multiple gates at once (rejected:
  decision 4).
- Flipping the blocked gate's verdict green (rejected: decision 6).
- Overriding an exhausted gate (rejected: decision 5).
