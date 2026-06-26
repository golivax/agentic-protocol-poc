# Authoring Protocols and Workflows

Everything you need to write a **new protocol** — the data declaration, the
evidence contract, the deterministic checks, the agent workflows, and the gates —
gathered in one place. You author a protocol as *data*; **you never edit the
engine**. The engine reads your `protocol.json` and drives it.

This page is a **map**, not a duplicate: each step links to the canonical doc. Read
it top-to-bottom the first time; come back to the [field reference](PROTOCOL-DSL.md)
and the [linter](#4-validate--visualize-your-protocol) every time after.

> **A protocol lives in one directory:**
> `.github/agent-factory/protocols/<name>/` — a `protocol.json`, one
> `*.evidence.schema.json` per agent step, a `checks/` dir, and (optionally) a
> `publish/` hook. Plus a gh-aw agent `.md` per agent step under
> `.github/workflows/`. To build a new protocol you create these files; you do not
> touch `.github/agent-factory/engine/`.

---

## 0. Before you start — the mental model

The whole design rests on one principle: **don't trust prose — demand evidence, and
check it deterministically.** Read these first so the DSL choices below make sense:

- [`HOW-IT-WORKS.md` §1–2](HOW-IT-WORKS.md#1-motivation) — motivation and key ideas
  (the engine drives, the agent is dispatched; evidence over prose).
- [`HOW-IT-WORKS.md` — execution model](HOW-IT-WORKS.md#execution-model-no-long-lived-driver)
  — why each run does **one transition and exits** (no long-lived driver).
- [`HOW-IT-WORKS.md` §3.2](HOW-IT-WORKS.md#32-the-four-trust-zones-per-iteration) —
  the four trust zones (the engine and the agent never share a job or a credential).
  This is the security model your checks and publish hooks live inside.

---

## 1. The tutorial — design a protocol end-to-end

The narrative walkthrough, in order. Work through it once against a real change:

| Step | What you decide | Read |
|------|-----------------|------|
| Anatomy of a `protocol.json` | The states, checks, and transitions — and the **enumerable rubric** that makes a judgment gateable | [`HOW-IT-WORKS.md` §4.1](HOW-IT-WORKS.md#41-anatomy-of-a-protocol-protocoljson) |
| The evidence schema | The **contract** the agent must fill — negative attestation with a trace | [`HOW-IT-WORKS.md` §4.2](HOW-IT-WORKS.md#42-the-evidence-schema-the-contract) |
| Writing a deterministic check | Verifying the *form* of the evidence (never the substance), with three worked examples | [`HOW-IT-WORKS.md` §4.3](HOW-IT-WORKS.md#43-writing-a-deterministic-check) |
| The agent workflows | The gh-aw `*-agent.md` source and its compiled lock | [`HOW-IT-WORKS.md` §4.4](HOW-IT-WORKS.md#4-developer-guide) |
| The orchestrator & command seam | Where trigger policy lives (not in the engine) | [`HOW-IT-WORKS.md` §4.5–4.6](HOW-IT-WORKS.md#4-developer-guide) |
| The publish hook | The trusted (zone 4) step that publishes the verdict | [`HOW-IT-WORKS.md` §4.7](HOW-IT-WORKS.md#47-the-publish-hook) |
| Fan-out / join | Parallel agent legs joined under an AND-barrier | [`HOW-IT-WORKS.md` §8](HOW-IT-WORKS.md#8-fan-out--join-multi-agent-review) |

---

## 2. The field reference — every `protocol.json` key

When you know the shape and just need the keys: **[`PROTOCOL-DSL.md`](PROTOCOL-DSL.md)**
documents every key by node kind (`agent`, `fanout`, `join`, `gate`, `merge`),
the trigger and check entries, and the validation gotchas.

Wire the **JSON Schema** into your editor for autocomplete + inline typo-catching —
it is intentionally stricter than the engine (`additionalProperties: false`):

```jsonc
{
  "$schema": "../../engine/protocol.schema.json",
  "name": "my-protocol",
  "states": [ /* … */ ]
}
```

The schema lives at
[`.github/agent-factory/engine/protocol.schema.json`](../.github/agent-factory/engine/protocol.schema.json)
and ships with the engine, so the `dist/` installer copies it into every target repo.

---

## 3. The contracts (ABIs) — keep these stable

Your checks and hooks plug into the engine through small, fixed interfaces. Don't
drift from them:

- **Check:** invoked as `<check> <evidence.json> <diff.txt> <changed-files.txt>`,
  prints one JSON object `{check, pass, feedback}` to stdout, and **always exits 0**
  (non-zero is a runner error). Reads its node-scoped config from the `CHECK_PARAMS`
  env var. Declare its severity with `on_fail` (`iterate` | `block` | `advisory`).
- **Publish hook:** invoked as `<hook> <evidence.json> <instance-key>`, prints
  `{conclusion, summary}`. Runs **trusted in zone 4** (not a sandboxed check).
- **Evidence:** negative attestation with a trace — "none-found" is legal but must
  carry the `examined` identifiers; findings carry verbatim `existing_code` + a
  `side`/`line` anchor.

Full detail and the security rationale: [`HOW-IT-WORKS.md` §4.3 / §4.7](HOW-IT-WORKS.md#43-writing-a-deterministic-check)
and the **Contracts (ABIs)** section of [`../CLAUDE.md`](../CLAUDE.md).

---

## 4. Validate & visualize your protocol

A protocol is data, so you can check it **before** wiring up any GitHub Actions.
The engine ships a linter that runs the same validation the engine does and draws
your protocol as a tree so you can eyeball its shape:

```bash
python3 .github/agent-factory/engine/protocol-lint.py \
  .github/agent-factory/protocols/<name>/protocol.json
```

It runs two layers and then renders the tree:

1. **Structural** — against `protocol.schema.json` (catches typos and wrong types).
   Uses the `jsonschema` library if installed (a dev-only dependency); when it's
   absent this layer is skipped with a note and only the semantic layer runs.
2. **Semantic** — the engine's own authoring rules (`join.of` in scope, every
   `agent`/flat branch has a `workflow`, `gate.questions_from` names a sibling) plus
   the `max_depth` cap — the *exact* checks `lib.validate_protocol` /
   `lib.check_depth` apply at runtime, so a green linter means the engine will
   accept your protocol.

Exit code `0` = valid, `1` = invalid (problems listed), `2` = unreadable/unparseable
input. A *schema-only* nit (e.g. an extra annotation key the engine would ignore)
still draws a best-effort diagram so you can see the shape; a *structural* error
(bad `join.of`, missing `workflow`) suppresses it.

It renders two complementary views — pick with `--view tree|block|both` (default
`tree`), or `--no-viz` to validate only:

- **`tree`** — a compact indented tree (every node, its checks, hooks, and inputs).
- **`block`** — a top-to-bottom BPMN-ish **flow diagram**: tasks as boxes, sequence
  flows as `│`/`▼` arrows, and each fan-out as a `fork ▸ … / join ▸ …` lane
  bracketing its parallel legs (a `∥` divider between them). Nested fan-outs nest
  the lane.

Example — the shipped `code-review` protocol, `tree` view:

```
code-review   (protocol)
   triggers: /review→start, /override→override, /approve→resolve-gate, …
   depth: 2 (max_depth=5)

├─ preflight   [agent] workflow=preflight-agent iters≤2  → review
│       checks: preflight-schema-valid, adherence-coverage, traces-exist-in-diff [iterate] · spec-present, plan-present [block] · docs-updated-with-code, tests-updated-with-code [advisory]
│       conclude=conclude-preflight on_blocked=halt publish=publish-verdict
├─ review   [fanout]  → join
│  ├─ grumpy   (leg) workflow=grumpy-agent iters≤3
│  │       checks: schema-valid, rubric-coverage, traces-exist-in-diff [iterate]
│  │       publish=publish-grumpy
│  └─ security   (leg) workflow=security-agent iters≤3
│          checks: schema-valid, traces-exist-in-diff [iterate]
│          publish=publish-security
├─ join   [join]  of=review  → approval
└─ approval   [gate·approval]  approve_excludes_author=true  → done

   terminals: done, failed (implicit)
```

…and the same protocol in the `block` (flow) view:

```
○ start
│
▼
┌─ preflight ──────────────────────────────────────────┐
│ agent · preflight-agent · iters≤2                    │
│ checks: 3×iterate, 2×block, 2×advisory               │
│ conclude conclude-preflight · publish publish-verdict│
└──────────────────────────────────────────────────────┘
│
▼
╔═ fork ▸ review ═══════════════════╗
║ ┌─ grumpy ──────────────────────┐
║ │ agent · grumpy-agent · iters≤3│
║ │ checks: 3×iterate             │
║ │ publish publish-grumpy        │
║ └───────────────────────────────┘
║ ┄┄┄┄ ∥ ┄┄┄┄
║ ┌─ security ──────────────────────┐
║ │ agent · security-agent · iters≤3│
║ │ checks: 2×iterate               │
║ │ publish publish-security        │
║ └─────────────────────────────────┘
╚═ join ▸ review ═══════════════════╝
│
▼
┌─ approval ───────────────────────┐
│ gate · approval (author excluded)│
└──────────────────────────────────┘
│
▼
◉ done
```

The linter understands the full DSL — nested sub-pipelines, data/approval gates, and
`merge` nodes all render at their true depth in both views (try `--view both` on
`deep-review-stub`, whose fan-outs nest four deep).

---

## 5. Authoring-error catalog

`lib.validate_protocol` rejects a malformed protocol **before any state is written**,
with an actionable message naming the offending node. The linter surfaces the same
messages. The high-value rules:

- **`join.of` must name a sibling fanout** — else *"join 'X' references unknown
  fanout of='Y' …"*
- **An `agent` node / flat branch must have a `workflow`** — else *"agent node 'X'
  missing 'workflow' …"*
- **`gate.questions_from` must name a sibling** — else *"gate 'X' has
  questions_from='Y' but no sibling state with id='Y' exists …"*
- **The tree may not exceed `max_depth`** (default 5) — else *"protocol depth N
  exceeds max_depth M"*.

The structural (schema) layer additionally catches typos like `wokflow`, wrong
types, and unknown keys. See [`PROTOCOL-DSL.md` — Validation gotchas](PROTOCOL-DSL.md#validation-gotchas)
for the reasoning and the two orthogonal axes (**process** vs. **verdict**).

---

## 6. Editing a gh-aw agent

`*-agent.md` is the **source**; `*-agent.lock.yml` is the **committed compiled
output** — the workflows run from the lock. After editing the `.md`, recompile and
commit the lock:

```bash
gh aw compile
```

Key facts (and the security rationale — the deliberately-disabled egress firewall,
the LLM endpoint under `engine.env`, the `cid:[…]` run-name marker) live in the
**"Editing a gh-aw agent"** section of [`../CLAUDE.md`](../CLAUDE.md) and in
[`STATUS.md`](STATUS.md) §6.

---

## See also

- [`PROTOCOL-DSL.md`](PROTOCOL-DSL.md) — the terse field-by-field reference.
- [`HOW-IT-WORKS.md`](HOW-IT-WORKS.md) — the full design rationale + tutorial.
- [`STATUS.md`](STATUS.md) — what is / isn't implemented, and why. **Read it before
  extending the engine** — many "missing" pieces are deliberate.
- The shipped protocols under `.github/agent-factory/protocols/` — `code-review`
  and `recover-mental-model` (production), `deep-review-stub` (a capability
  example to copy from).
- [`../dist/README.md`](../dist/README.md) — installing your protocol into another repo.
