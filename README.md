# agentic-protocol-poc

PoC of the agentic protocol engine: gh-aw agent workflows structured as a
porch-style state machine with evidence schemas, deterministic transition
checks, and bounded iterate-with-feedback. Opening a PR (or commenting
`/review`) runs the protocol. Design spec lives in the parent project:
`docs/superpowers/specs/2026-06-10-agentic-protocol-engine-poc-design.md`.

The engine is generic; `agentic-orchestrator.yml` routes events to the shipped
**`code-review`** protocol: a multi-phase pipeline that runs
`preflight` (agent, pre-flight gate) → `review` (fans out to two parallel agents —
the general `grumpy` reviewer and a `security` stub — each with its own bounded
iterate loop and eager publish) → `join` (strict AND-barrier over both legs) →
`approval` (human gate: a write-access reviewer must `/approve` before the pipeline
check-run goes green). A correlation-id run resolver ensures concurrent PRs of the
same agent workflow never misattribute runs. Read `docs/HOW-IT-WORKS.md` (design)
and `docs/STATUS.md` (what is/isn't implemented) before extending.

State: branch `agentic-state`. Multi-phase agent phase →
`code-review/pr-<N>/preflight.yaml`; fan-out legs →
`code-review/pr-<N>/review.grumpy.yaml` and `code-review/pr-<N>/review.security.yaml`;
shared per-instance → `code-review/pr-<N>/_instance.yaml`. Advanced only by
fast-forward push (CAS). Never force-push that branch.

Test scaffolding: label `poc:sabotage` on a PR drives the failed-check → iterate
loop. `grumpy` sabotages **iteration 1 only** (omits two rubric categories →
fails `rubric-coverage`, then self-recovers); `security` sabotages **every
iteration** (fabricates a finding → fails `traces-exist-in-diff` → exhausts to
`failed`, leaving the merge gate red).

## Security posture

Each agent workflow (`grumpy-agent`, `security-agent`) deliberately runs with
`strict: false` and `sandbox.agent: false` because the LLM endpoint (base URL)
is itself a secret and cannot be carried in AWF's static egress allowlist — do
not copy this to production without restoring the firewall. The agent jobs are
read-only; publication happens only in the orchestrator after deterministic
checks pass.
