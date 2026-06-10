# agentic-protocol-poc

PoC v1 of the agentic protocol engine: gh-aw agent workflows structured as a
porch-style state machine with evidence schemas, deterministic transition
checks, and bounded iterate-with-feedback. Comment `/grumpy` on a PR to run
the protocol. Design spec lives in the parent project:
`docs/superpowers/specs/2026-06-10-agentic-protocol-engine-poc-design.md`.

State: branch `agentic-state`, file `grumpy/pr-<N>.yaml`, advanced only by
fast-forward push (CAS). Never force-push that branch.

Test scaffolding: label `poc:sabotage` on a PR makes iteration 1 deliberately
skip two rubric categories, to demo the failed-check → iterate loop.

## Security posture

The agent workflow deliberately runs with `strict: false` and
`sandbox.agent: false` because the LLM endpoint (base URL) is itself a secret
and cannot be carried in AWF's static egress allowlist — do not copy this to
production without restoring the firewall. The agent job is read-only;
publication happens only in the orchestrator after deterministic checks pass.
