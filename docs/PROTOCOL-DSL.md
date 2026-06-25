# Protocol DSL — field reference

A protocol is declared as data in
`.github/agent-factory/protocols/<name>/protocol.json`. The generic engine reads
that file to drive a recursive state machine; **you author the DSL, you never edit
the engine.** This page is the *reference* (every key, by node kind). For the
narrative "how to design a protocol" walkthrough — picking the rubric, writing the
evidence schema and checks — see
[`HOW-IT-WORKS.md` §4](HOW-IT-WORKS.md) (the tutorial).

There is a machine-readable JSON Schema at
[`.github/agent-factory/engine/protocol.schema.json`](../.github/agent-factory/engine/protocol.schema.json).
It is intentionally **stricter** than the engine — the engine ignores unknown keys
(`.get()`-based), but the schema sets `additionalProperties: false` so your editor
flags typos like `wokflow`. Wire it up for autocomplete + inline validation:

```jsonc
{
  "$schema": "../../engine/protocol.schema.json",
  "name": "my-protocol",
  "states": [ /* … */ ]
}
```

The shipped protocols already carry this pointer. (The schema ships inside the
engine directory, so the `dist/` installer copies it into every target repo.)

---

## The shape in one breath

A protocol is a **sequence** of **nodes**. The root `states[]` is a sequence; a
fan-out leg may carry its own `states[]` (a *sub-pipeline*), nesting to
`max_depth`. Every node has an `id` and a `kind`; most have a `next` pointing at
the sibling to enter when they finish. The two terminals, `done` and `failed`, are
implicit — you point `next` at them but never declare them.

| `kind` | What it is | Must declare |
|--------|-----------|--------------|
| `agent` | An LLM step, dispatched as a gh-aw workflow; produces evidence the checks verify | `workflow` |
| `fanout` | Splits into parallel `branches`; each is a flat agent leg **or** a nested sub-pipeline | `branches`, `next` (→ its `join`) |
| `join` | AND-barrier: waits for every branch of one fanout to reach a terminal state | `of` (the fanout id) |
| `gate` | A pause: **approval** (human `/approve`) or **data** (agent asks, human `/answer`s) | — |
| `merge` | Post-join reduce: combines the joined legs via a trusted hook | `hook`, `inputs` |

A **branch** (an item of `branches[]`) is special: it has **no `kind`**. It's
either *flat* (has `workflow`) or a *sub-pipeline* (has `states[]`) — never both.

---

## Top-level keys

| Key | Type | Req | Meaning |
|-----|------|-----|---------|
| `name` | string | ✔ | Protocol id. Becomes the state-path prefix `<name>/<instance-key>.yaml`. Unique per protocols dir. |
| `states` | node[] | ✔ | The root sequence. |
| `version` | string | | Informational; not enforced. |
| `min_engine_version` | string | | Lowest engine version required; the `dist` installer refuses an older engine. |
| `max_depth` | int | | Static nesting cap (default 5); the engine rejects a tree whose deepest leaf exceeds it. |
| `phase_labels` | object | | Override the PR labels of terminal/structural phases (`setup`/`done`/`failed`/`blocked`/…). |
| `triggers` | trigger[] | | Event → command entry points (see below). |
| `$schema` | string | | Editor pointer; ignored by the engine. |

## `triggers[]`

The router scans **every** protocol's triggers to pick which one an event belongs
to. Each maps a GitHub event to a protocol command.

| Key | Type | Req | Meaning |
|-----|------|-----|---------|
| `on` | string | ✔ | GitHub event, e.g. `issue_comment`, `pull_request`. |
| `command` | enum | ✔ | `start` · `reset` · `override` · `resolve-gate` · `answer`. |
| `comment_prefix` | string | | For `issue_comment`: match comments starting with this (e.g. `/review`). Also names a command's reply prefix (e.g. `/answer`). |
| `actions` | string[] | | For `pull_request`: only these actions (e.g. `["opened"]`). |

```jsonc
"triggers": [
  { "on": "issue_comment", "comment_prefix": "/review",  "command": "start" },
  { "on": "issue_comment", "comment_prefix": "/approve", "command": "resolve-gate" }
]
```

## `agent` node

| Key | Type | Req | Meaning |
|-----|------|-----|---------|
| `id`, `kind` | string | ✔ | `kind: "agent"`. |
| `workflow` | string | ✔ | The gh-aw agent workflow basename the engine dispatches. |
| `evidence` | string | | The `*.evidence.schema.json` filename (in the protocol dir) the agent must satisfy. |
| `max_iterations` | int | | Iterate-with-feedback rounds before the node is `failed` (default 3). |
| `params` | object | | Node-scoped config forwarded to checks via `CHECK_PARAMS` (e.g. the rubric `categories`). |
| `checks` | check[] | | Deterministic checks over the evidence (see below). |
| `inputs` | input[] | | Earlier nodes' outputs to materialize for this agent (see below). |
| `publish` | string | | Trusted (zone 4) hook that publishes the verdict after checks pass. |
| `conclude` | string | | Trusted (zone 4) post-checks hook that may return `blocked: true`. |
| `on_blocked` | `"halt"` | | With `conclude`: `halt` stops the pipeline (overridable via `/override`). |
| `next` | string | | Sibling to enter next. Omit if terminal. |

## `fanout` + `join`

```jsonc
{ "id": "review", "kind": "fanout", "next": "join",
  "branches": [
    { "id": "grumpy",   "workflow": "grumpy-agent",   "evidence": "grumpy.evidence.schema.json",
      "checks": [{ "run": "schema-valid", "on_fail": "iterate" }], "publish": "publish-grumpy" },
    { "id": "security", "workflow": "security-agent", "evidence": "security.evidence.schema.json",
      "checks": [{ "run": "schema-valid", "on_fail": "iterate" }], "publish": "publish-security" }
  ]
},
{ "id": "join", "kind": "join", "of": "review", "next": "approval" }
```

- **branch** keys (no `kind`): `id` (✔), and either `workflow` (flat leg, plus the
  agent keys `evidence`/`max_iterations`/`params`/`checks`/`publish`/`inputs`) **or**
  `states[]` (a sub-pipeline — a nested sequence of full nodes).
- **join**: `of` names the sibling fanout it barriers; `next` is where to go once
  all legs are terminal. Each leg advances independently and fires the join; the
  join is its own evaluator run — see the execution model in
  [`HOW-IT-WORKS.md`](HOW-IT-WORKS.md#execution-model-no-long-lived-driver).

## `gate` node

Two flavors, distinguished by `questions_from`:

| Key | Type | Meaning |
|-----|------|---------|
| `questions_from` | string | **Data gate** — names a sibling node whose evidence carries the `questions`. The engine posts them; a human replies with the command's `/answer` prefix; the answers feed downstream via `inputs`. Omit for an **approval gate**. |
| `approve_excludes_author` | bool | Approval gate: if `true`, the PR author can't approve their own gate. |
| `checks` | check[] | Usually `answers-coverage` on a data gate. |
| `inputs` | input[] | Earlier outputs to surface. |

A gate **opens and the run ends** — nothing is held waiting; the eventual
`/approve` or `/answer` comment is a fresh wake-up.

## `merge` node

| Key | Type | Req | Meaning |
|-----|------|-----|---------|
| `hook` | string | ✔ | Trusted (zone 4) reduce hook that combines the joined legs. |
| `inputs` | input[] | ✔ | The leg outputs to combine. |
| `next` | string | | Sibling to enter next. |

## `checks[]` entry

A check is an executable resolved from `checks/<run>` or `checks/<run>.*` in the
protocol dir (extension-agnostic) — or an explicit `exec` path. ABI:
`<check> <evidence.json> <diff.txt> <changed-files.txt>` → one JSON object
`{check, pass, feedback}`, **always exit 0**.

| Key | Type | Req | Meaning |
|-----|------|-----|---------|
| `run` | string | ✔ | Check id → resolves the executable. |
| `exec` | string | | Explicit path (relative to the protocol dir), overriding the lookup. |
| `on_fail` | enum | | `iterate` (default — drives the retry loop) · `block` (stops the conclusion without iterating) · `advisory` (recorded only). |

## `inputs[]` entry

Materializes an earlier node's evidence (or a gate's answers) as `inputs/<as>.json`
for this node's agent.

| Key | Type | Req | Meaning |
|-----|------|-----|---------|
| `from` | string | ✔ | Source node id (a sibling sub-state, a fanout branch, or a phase). |
| `as` | string | ✔ | Basename under `inputs/` the source is written as. |

---

## A nested sub-pipeline, in miniature

A fan-out leg with its own `states[]` is a full sequence — agents, gates, even
deeper fan-outs (from `recover-mental-model-stub`):

```jsonc
{ "id": "recover", "kind": "fanout", "next": "join",
  "branches": [
    { "id": "summary", "workflow": "rmm-summary-agent", "checks": [ /* … */ ] },   // flat leg
    { "id": "rationale", "states": [                                              // sub-pipeline leg
        { "id": "draft",    "kind": "agent", "workflow": "rmm-draft-agent", "checks": [ /* … */ ] },
        { "id": "clarify",  "kind": "gate",  "questions_from": "draft",
          "checks": [{ "run": "answers-coverage", "on_fail": "iterate" }] },
        { "id": "finalize", "kind": "agent", "workflow": "rmm-finalize-agent",
          "inputs": [ { "from": "clarify", "as": "answers" }, { "from": "draft", "as": "draft" } ] }
      ] }
  ]
}
```

---

## Validation gotchas

`lib.validate_protocol` rejects a malformed protocol **before any state is
written**, with an actionable message. The high-value rules:

- **`join.of` must name a sibling fanout.** Otherwise:
  *"join 'X' references unknown fanout of='Y' — make sure a fanout with id='Y'
  exists as a sibling of 'X'."*
- **An `agent` node / flat branch must have a `workflow`.** Otherwise:
  *"agent node 'X' missing 'workflow' — add a `"workflow": "<name>"` key…"*
- **`gate.questions_from` must name a sibling.** Otherwise:
  *"gate 'X' has questions_from='Y' but no sibling state with id='Y' exists…"*

Also: the tree may not exceed `max_depth` (default 5); `min_engine_version` must be
≤ the installed engine; and a fan-out's `next` should point at its `join`. Two
orthogonal axes the engine tracks — **process** (`done`/`failed`: did checks pass
within `max_iterations`) vs. **verdict** (`APPROVE`/`CHANGES_REQUESTED`) — are kept
separate; a `join` cares only about the process axis.

The dev-only test `tests/test_protocol_schema.py` validates every shipped protocol
and fixture against the JSON Schema, so the schema and this page stay in lockstep
with the DSL.

---

## See also

- [`HOW-IT-WORKS.md` §4](HOW-IT-WORKS.md) — the authoring tutorial (rubric design,
  evidence schemas, writing a check, publish hooks).
- [`HOW-IT-WORKS.md` — execution model](HOW-IT-WORKS.md#execution-model-no-long-lived-driver)
  — why each run does one transition and exits.
- The shipped protocols under `.github/agent-factory/protocols/` — `code-review`
  (production), `recover-mental-model-stub` and `deep-review-stub` (capability
  examples).
