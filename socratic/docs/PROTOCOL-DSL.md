# Protocol DSL — field reference

A protocol is declared as data in
`.github/agent-factory/protocols/<name>/protocol.json`. The generic engine reads
that file to drive a recursive state machine; **you author the DSL, you never edit
the engine.** This page is the *reference* (every key, by node kind). For the
narrative "how to design a protocol" walkthrough — picking the rubric, writing the
evidence schema and checks — see
[`HOW-IT-WORKS.md` §4](HOW-IT-WORKS.md) (the tutorial).
[`AUTHORING.md`](AUTHORING.md) is the hub that ties the two together and points at
the `protocol-lint.py` validator/visualizer.

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
| `fanout` | Splits into parallel branches — a static list (`branches[]`) **or**, dynamically, a runtime-derived set (`expand`+`each`); each leg is a flat agent leg **or** a nested sub-pipeline | `branches` **xor** `expand`+`each`, `next` (→ its `join`) |
| `join` | AND-barrier: waits for every branch of one fanout to reach a terminal state, then applies a success `policy` | `of` (the fanout id) |
| `gate` | A pause: **approval** (human `/approve`) or **data** (agent asks, human `/answer`s) | — |
| `merge` | Post-join reduce: combines the joined legs via a trusted hook | `hook`, `inputs` |

A **branch** (an item of `branches[]`) is special: it has **no `kind`**. It's
either *flat* (has `workflow`) or a *sub-pipeline* (has `states[]`) — never both.
A dynamic fanout's `each` is the same template, minus `id` — see
[Dynamic fan-out](#dynamic-fan-out-data-driven) below.

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
| `_note` | string | | Free-text annotation (JSON has no comments). Ignored by the engine; allowed on **any** object — the protocol, a node, a branch, a trigger, a check, an input. |

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
  Optional `policy` (`all` default · `any` · `quorum:<N|P%>`) picks the
  success/fail verdict once every leg is terminal — see
  [Dynamic fan-out](#dynamic-fan-out-data-driven) below (it applies to a static
  fanout's join too).

### Dynamic fan-out (data-driven)

A `fanout` may instead derive its legs **at runtime** from a trusted expander
hook, rather than listing them as static `branches[]`. A fanout has **exactly
one** of the two modes — `branches[]` **xor** `expand`+`each` — never both,
never neither; the validator rejects anything else (see
[Validation gotchas](#validation-gotchas)). Full rationale and execution model:
[the dynamic fan-out design spec](superpowers/specs/2026-06-30-dynamic-fanout-design.md).

> **Offline scope:** this milestone ships and tests the engine **capability** —
> expansion, keying, the manifest, `join.policy`, `merge` `from_fanout` — entirely
> in the offline `ENGINE_LOCAL` pytest layer. Live GitHub-Actions runtime-matrix
> dispatch (staging `inputs/<as>.json` per runtime leg) and a real diff-parsing
> expander are **milestone 2** (design spec §14) — a dynamic-fanout protocol isn't
> turn-key on live Actions yet.

```jsonc
{ "id": "review", "kind": "fanout", "next": "join",
  "expand": { "hook": "expand-files", "as": "file", "id_from": "$.path", "max_legs": 256 },
  "each": {
    "workflow": "review-file-agent", "evidence": "review-file.evidence.schema.json",
    "checks": [ { "run": "schema-valid", "on_fail": "iterate" } ],
    "publish": "publish-file-review"
    // …  OR  "states": [ /* a sub-pipeline template, e.g. Plan → Main → Filter */ ]
  }
},
{ "id": "join", "kind": "join", "of": "review", "policy": "any", "next": "reduce" },
{ "id": "reduce", "kind": "merge", "hook": "dedup-comments",
  "inputs": [ { "from_fanout": "review", "as": "legs" } ], "next": "done" }
```

**`expand` keys:**

| Key | Type | Req | Meaning |
|-----|------|-----|---------|
| `hook` | string | ✔ | Trusted expander id. Resolved from `expand/<hook>` or `expand/<hook>.*` in the protocol dir (extension-agnostic) — mirrors check/publish resolution. |
| `exec` | string | | Explicit path (relative to the protocol dir), overriding the `expand/<hook>` lookup. |
| `as` | string | ✔ | Basename each expanded item is staged as for its leg: `inputs/<as>.json`. |
| `id_from` | string | ✔ | A simple `$.field` / `$.a.b` JSONPath into each item, selecting the value that becomes the leg's stable id/key (dotted-`$.`-rooted only — no wildcards/filters). |
| `max_legs` | int | ✔ | Hard cap on expanded legs, `1..256`. The expander emitting more is a **hard block, fail loud** — no legs are written, never a silent truncation. |

**`each`** is the per-item **branch template** — the same shape a `branches[]`
entry has, minus `id` (the runtime id comes from `id_from`): either a **flat**
leg (`workflow` + the usual `evidence`/`max_iterations`/`params`/`checks`/
`publish`/`inputs`) **xor** a **sub-pipeline** (`states[]`, a full nested
sequence — agents, gates, even a nested dynamic fanout; validated recursively
and counted toward `max_depth` exactly like a static branch). Every dynamic leg
is seeded from this one template, keyed by its runtime id. The engine persists
each leg's item beside its state file so it can be staged as
`inputs/<expand.as>.json` for the leg's agent (the live-dispatch materialize
step that does this on GitHub Actions is milestone 2 — see the scope note
above); `each`'s own `inputs[]` (if any) resolve exactly as a branch's do today.

**The expander ABI:**

```
<hook> <state_dir> <instance>           # e.g. expand-files code-review pr-42
  → stdout: {"items": [ {...}, {...} ]}    # one opaque, author-defined object per leg
  → always exit 0 (nonzero = a runner error, not "no items")
```

It runs **trusted in zone 1** — as a subprocess `lib.run_expander` spawns from
*inside* `next.py`'s sequencer, during the `plan` job, not as a separate step
that runs before `next.py`. In the dynamic-fanout case `next.py` is therefore
not a pure planner: this one I/O call is a deliberate, narrow exception. It
re-derives the item list itself (e.g. re-fetching the diff) and **never
trusts agent data** — the same posture as a check re-fetching `gh pr diff`.

> **Known gap, not yet a guarantee:** `run_expander` forwards the `plan`
> job's **full** environment to the expander subprocess (`env =
> dict(os.environ)`, no scrubbing) — on live GitHub Actions that job env
> carries the dispatch/publish PAT (`GH_TOKEN`/`PUBLISH_TOKEN`, both
> `POC_DISPATCH_TOKEN`) and the authenticated `STATE_REMOTE`. The expander is
> **not** credential-scoped to a read-only token today. Restricting it to a
> minimal read-only token is a **milestone-2 hardening** (design spec §14) —
> it only bites once live wiring puts real tokens in the job env; offline /
> `ENGINE_LOCAL` runs have no such secrets to leak.

The engine — not the hook — reads `id_from` out of each item to key it and
writes the manifest. It is **fail-loud**: an unresolved/non-executable hook, a
nonzero exit, non-JSON stdout, or a missing/non-array `items` halts the run
(fanout → `failed`, no legs, no manifest written) — treated as a trusted-infra
failure, not an iterate condition.

**The manifest.** The engine (never the hook) persists
`<tree-path>.__manifest.yaml` — `{count, legs: [{id, key, item}, …]}` — the
durable, runtime-computed analog of static `branches[]`, CAS-pushed to
`agentic-state` like every other state file. `id` is a short stable hash of the
`id_from` value (a collision is a fail-loud manifest-build error); `key` is the
raw `id_from` value (for humans / the status comment); `item` is the full
opaque item. **Cardinality is frozen for the run**: re-entering the fanout reads
the manifest — the expander is **not** re-run. A new commit triggers
`synchronize` → `reset` → a fresh run → a fresh expander pass.

**`join.policy`:**

| `policy` | Join succeeds iff |
|---|---|
| `all` (default) | Every leg reached `done` — today's strict AND-barrier; the behavior of every existing static join, byte-identical. |
| `any` | At least one leg reached `done` (fails only if every leg failed). |
| `quorum:N` | At least `N` legs reached `done` — `N` an integer count (`quorum:3`) or a percentage of the leg count (`quorum:80%`, `quorum:33.3%`). |

Whatever the policy, the join **still waits for every leg to reach a terminal
state** (`done` or `failed`) before evaluating — `policy` only changes the
success/fail verdict once everyone's finished, it never lets the barrier fire
early. **Zero legs** (a vacuous fanout — the expander returned `[]`): `all` is
vacuously satisfied and the run advances to `join.next` — and so is a
**percentage** `quorum:P%` (`ceil(0 * P / 100) == 0`, and `0 >= 0`), the same
vacuous pass as `all`. `any` and an **integer-count** `quorum:N` (`N >= 1`) are
**not** satisfied on zero legs — they sink an empty set (`0 >= 1` is false for
`any`; `0 >= N` is false for `quorum:N`). `policy` is optional and applies to
**any** join, static or dynamic — a static fanout's join can set `policy: any`
too.

**`merge` `inputs[].from_fanout`.** A merge input is normally `{from, as}` —
one sibling node's evidence. A fanout (dynamic or static) can instead be
reduced with `{from_fanout: <fanout-id>, as: <name>}`, which collects **every
leg's persisted evidence**, tagged with its terminal state, into one array
staged as `inputs/<name>.json`:

```json
[ { "leg_id": "a1b2c3", "key": "src/a.go", "state": "done",   "evidence": { "…": "…" } },
  { "leg_id": "d4e5f6", "key": "src/b.go", "state": "failed", "evidence": null } ]
```

It reads the same `__manifest.yaml` the join reads, so merge and join can never
disagree on cardinality, and it collects from **persisted per-leg state**, not
job outputs — resilient to the GitHub Actions matrix-outputs-clobber problem the
same way per-leg artifacts already are. A merge input is exactly one of `from`
**xor** `from_fanout` (schema-enforced) — never both.

> **Offline scope:** `from_fanout` currently resolves the **top-level** fanout
> only (the reduce reads the fixed path `[<fanout-id>]`); reducing over a
> **nested** fanout (one inside an `each` sub-pipeline) is milestone 2.

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
| `inputs` | input[] | ✔ | The leg outputs to combine — a `from` per sibling, or a single `from_fanout` to reduce over an entire (dynamic or static) fanout's legs at once; see [Dynamic fan-out](#dynamic-fan-out-data-driven). |
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
for this node's agent. Exactly one of `from` / `from_fanout` (schema-enforced).

| Key | Type | Req | Meaning |
|-----|------|-----|---------|
| `from` | string | ✔* | Source node id (a sibling sub-state, a fanout branch, or a phase). |
| `from_fanout` | string | ✔* | `merge` only — id of a sibling fanout; collects **every** leg's `{leg_id, key, state, evidence}` into one array. See [Dynamic fan-out](#dynamic-fan-out-data-driven). |
| `as` | string | ✔ | Basename under `inputs/` the source is written as. |

---

## A nested sub-pipeline, in miniature

A fan-out leg with its own `states[]` is a full sequence — agents, gates, even
deeper fan-outs (from `recover-mental-model`):

```jsonc
{ "id": "recover", "kind": "fanout", "next": "join",
  "branches": [
    { "id": "legion",  "workflow": "mm-legion-agent",  "checks": [ /* … */ ] },   // flat leg
    { "id": "codeset", "workflow": "mm-codeset-agent", "checks": [ /* … */ ] },   // flat leg
    { "id": "ubiquitous-language", "workflow": "mm-ubiquitous-language-agent", "checks": [ /* … */ ] },  // flat leg
    { "id": "socratic", "states": [                                              // sub-pipeline leg
        { "id": "phase1",    "kind": "agent", "workflow": "mm-socratic-phase1-agent", "checks": [ /* … */ ] },
        { "id": "answering", "kind": "agent", "workflow": "mm-socratic-answering-agent",
          "inputs": [ { "from": "phase1", "as": "tree" } ], "checks": [ /* … */ ] },
        { "id": "phase2",    "kind": "agent", "workflow": "mm-socratic-phase2-agent",
          "inputs": [ { "from": "phase1", "as": "tree" }, { "from": "answering", "as": "answers" } ] }
      ] }
  ]
}
```

(A sub-pipeline step can also be a `gate` — see the `subpipeline-gate` test fixture
for the `draft → clarify (gate) → finalize` shape.)

---

## A worked example: dynamic fan-out

`tests/fixtures/dyn-fanout-flat/protocol.json` — the smallest complete dynamic
fan-out: an `expand`ed `review` fanout of flat legs → a lenient `join(policy:
any)` → a `merge` that reduces over every leg via `from_fanout`:

```jsonc
{
  "name": "dyn-fanout-flat",
  "states": [
    { "id": "review", "kind": "fanout", "next": "join",
      "expand": { "hook": "expand-items", "as": "file", "id_from": "$.path", "max_legs": 8 },
      "each": { "workflow": "review-file-agent", "evidence": "leg.evidence.schema.json",
                "checks": [ { "run": "schema-valid", "on_fail": "iterate" } ],
                "publish": "reduce" } },
    { "id": "join", "kind": "join", "of": "review", "policy": "any", "next": "reduce" },
    { "id": "reduce", "kind": "merge", "hook": "reduce",
      "inputs": [ { "from_fanout": "review", "as": "legs" } ], "next": "done" }
  ]
}
```

- `review.expand.hook` resolves to `expand/expand-items.py` in the fixture dir;
  under `ENGINE_LOCAL` the stub expander reads a sibling fixture file
  (`expand/items.json`) instead of a live diff and prints its `items`.
- Each item becomes a `review-file-agent` leg, keyed by `$.path`, with the item
  staged as `inputs/file.json`.
- `join(policy: any)` lets the run proceed to `reduce` as long as at least one
  leg's checks pass — appropriate for an independent per-file review where one
  bad file shouldn't sink the whole pass.
- Both a leg's `publish` and a `merge`'s `hook` resolve from the same
  protocol-dir `publish/` folder, so the fixture points both at
  `publish/reduce.py`; `reduce.py` reads `inputs/legs.json` only when invoked as
  the merge hook, and receives every leg's `{leg_id, key, state, evidence}` row
  there.

Validate the shape (schema + the semantic rules above) with the bundled linter:

```bash
python3 .github/agent-factory/engine/protocol-lint.py \
  tests/fixtures/dyn-fanout-flat/protocol.json
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
- **A `fanout` needs exactly one of `branches[]` or `expand`+`each`.** Otherwise:
  *"fanout 'X' must have exactly one of branches[] (static) or expand+each
  (dynamic) — not both, not neither."*
- **`expand` needs `hook`/`as`/`id_from`/`max_legs`, and `max_legs` must be an
  int in `[1,256]`.** Otherwise: *"fanout 'X' expand missing 'Y' — expand needs
  hook, as, id_from, and max_legs"* / *"fanout 'X' expand.max_legs must be an
  int in [1,256], got …"*
- **`each` must be a flat leg (`workflow`) xor a sub-pipeline (`states`).**
  Otherwise: *"fanout 'X' each must be a flat leg (workflow) XOR a sub-pipeline
  (states) — not both, not neither."*
- **`join.policy` must parse** as `all` / `any` / `quorum:<N|P%>`. Otherwise:
  *"join 'X' has invalid policy='Y' — use 'all', 'any', or 'quorum:<N|P%>'."*
- **A `merge` input's `from_fanout` must name a sibling fanout** (mirrors
  `join.of`). Otherwise: *"merge 'X' input from_fanout='Y' names no fanout in
  scope — make sure a fanout with id='Y' exists as a sibling of 'X'."*

Also: the tree may not exceed `max_depth` (default 5); `min_engine_version` must be
≤ the installed engine; and a fan-out's `next` should point at its `join`. Two
orthogonal axes the engine tracks — **process** (`done`/`failed`: did checks pass
within `max_iterations`) vs. **verdict** (`APPROVE`/`CHANGES_REQUESTED`) — are kept
separate; a `join` cares only about the process axis.

Run all of this **before** wiring up any Actions with the bundled linter, which
applies the schema *and* these semantic rules and prints the message for any it
trips:

```bash
python3 .github/agent-factory/engine/protocol-lint.py \
  .github/agent-factory/protocols/<name>/protocol.json
```

The dev-only test `tests/test_protocol_schema.py` validates every shipped protocol
and fixture against the JSON Schema, so the schema and this page stay in lockstep
with the DSL.

---

## See also

- [`AUTHORING.md`](AUTHORING.md) — the authoring hub: the journey from mental model
  → tutorial → this reference → validating + visualizing your protocol.
- [`HOW-IT-WORKS.md` §4](HOW-IT-WORKS.md) — the authoring tutorial (rubric design,
  evidence schemas, writing a check, publish hooks).
- [`HOW-IT-WORKS.md` — execution model](HOW-IT-WORKS.md#execution-model-no-long-lived-driver)
  — why each run does one transition and exits.
- The shipped protocols under `.github/agent-factory/protocols/` — `code-review`
  and `recover-mental-model` (production), `deep-review-stub` (a capability
  example).
- [The dynamic fan-out design spec](superpowers/specs/2026-06-30-dynamic-fanout-design.md)
  — full rationale, execution model, and the milestone-2 (live-Actions) scope for
  `expand`/`each`/`policy`/`from_fanout`; `tests/fixtures/dyn-fanout-flat/` for the
  worked example above and `dyn-fanout-subpipeline`/`dyn-nested`/`dyn-fanout-badcap`
  for the sub-pipeline, nested, and over-cap shapes.
