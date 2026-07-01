# Dynamic (data-driven) fan-out — engine construct (design)

**Date:** 2026-06-30
**Status:** design approved (brainstorming), ready for planning
**Author:** brainstormed with the user

---

## 1. Summary

A new **generic engine construct**: a **dynamic (data-driven) fan-out** whose
branch set is a *matrix produced at runtime* from a trusted expander hook, plus a
**runtime-cardinality join** and a **reduce over the dynamic set**. Today the
engine's `fanout` node fans out over a **static** `branches[]` array declared in
`protocol.json`; this milestone lets a `fanout` instead fan out over **N items only
known at runtime** (e.g. the changed files in a diff), with N bounded and
engine-controlled.

The construct is **protocol-agnostic**. It is realized by **extending the existing
`fanout` kind** (not a new kind): a `fanout` has *either* today's static
`branches[]` *xor* a new `expand` + `each` pair. The recursive engine's
enter/advance/join machinery is reused verbatim — the only genuine novelty is
**where the leg list comes from** (a persisted manifest vs. `protocol.json`).

This milestone ships and tests the **engine capability offline** (the
`ENGINE_LOCAL` pytest layer) with **OCR-inspired fixtures**. A **second milestone**
(out of scope here) builds a real `open-code-review`-mimic protocol and exercises
the construct live on GitHub Actions.

### Motivation — the OCR gap

An analysis of Alibaba's `open-code-review` (OCR) found its entire pipeline is a
*map over N files → (nested) map over K comments → reduce*, where N and K are
discovered at runtime by parsing the diff. Every other OCR concept maps cleanly onto
the existing engine (evidence-as-contract, deterministic anchor checks equivalent to
`traces-exist-in-diff`, bounded iterate-with-feedback, per-file `Plan → Main →
Filter` sub-pipeline, and OCR's `ReLocateComment` retry ≈ the engine's iterate
loop). OCR has **no human gates** and **no durable resumable state** — areas where
the engine is *stronger*. The **one** missing primitive is **dynamic fan-out with a
runtime-sized join/reduce**. This design adds exactly that primitive.

## 2. Goals / Non-goals

**Goals**
- Add a **generic** dynamic fan-out construct usable by any protocol — OCR is merely
  the first intended consumer (milestone 2).
- Keep the change **additive and backward-compatible**: every existing protocol
  (`code-review`, `recover-mental-model`, `deep-review-stub`, all fixtures) is
  **byte-unchanged**. The static `branches[]` path and `join` default behavior are
  untouched.
- Keep cardinality **engine-controlled and DoS-safe**: a trusted expander decides the
  item list; the engine bounds it (`max_legs`) and **fails loud** on over-cap.
- Reuse the recursive engine (**one code path for every shape**) — no parallel
  execution path, honoring the Stage 4a unification.
- Prove the construct in the **offline `ENGINE_LOCAL` pytest layer** with
  OCR-inspired fixtures.

**Non-goals**
- A real OCR-mimic protocol, a real diff-parsing expander, and live GitHub-Actions
  matrix wiring for runtime legs — **milestone 2**.
- Judging the *substance* of any finding (unchanged engine thesis: checks verify the
  *form* of evidence, never its correctness).
- Conditional / predicate transitions (e.g. OCR's "skip Plan when below a line
  threshold"). That is a separate potential feature and is **not** in scope; where an
  OCR protocol needs it, the decision is pushed inside the agent.
- Changing the four-trust-zone model (the expander lands inside the **existing**
  zone-1 boundary).

## 3. Background — how it fits the engine model

The engine thesis: *a workflow run is one transition of a state machine whose state
lives in git; the agent is read-only and affects the world only through an
`evidence.json` the engine's checks inspect deterministically; trusted zone-4 code
acts on verdicts.* Two facts make this construct a natural extension rather than a
bolt-on:

1. **The recursive engine already sequences N legs of arbitrary length** by
   `NODE_PATH`; enter/advance/join bubble recursively. Making the leg *set* dynamic is
   a localized change at the enumeration point, not a new engine.
2. **Cardinality is decided by trusted, deterministic code** (the expander re-derives
   items from the independently-fetched diff — it never trusts agent data), which is
   exactly the "demand evidence, check it deterministically" posture the engine takes
   everywhere else.

The one place it *bends* the model: a new **trusted hook type** (the expander) runs
in the plan job (zone 1). It is engine-side, deterministic, holds no new credential,
and never runs agent code — so the invariant "the engine and the agent never share a
job or a credential" is preserved.

## 4. The realization — Approach B (extend `fanout`)

Chosen over (A) a dedicated `dynamic-fanout` kind and (C) expanding the matrix in the
GHA layer. Rationale:
- **vs. A:** a separate kind would duplicate (or force sharing of) the leg-sequencing
  and join-bubbling the recursive engine already has, and adds a second fan-out
  concept to the DSL, validator, and visualizer — cutting against "one code path for
  every shape."
- **vs. C:** moving materialization into the workflow YAML splits the logic across
  engine + GHA, weakens "the engine drives," and makes the manifest→legs step
  **untestable** in the `ENGINE_LOCAL` pytest layer where this milestone's whole test
  strategy lives.

**The core seam.** Today `next.py` reads a fanout's legs from
`node.get("branches", [])`. We introduce one function:

```
resolve_legs(node, state) →  node["branches"]            if static
                             manifest legs (from state)  if dynamic (expand present)
```

Everything downstream — recursive enter/advance, per-leg iterate loops, join bubbling
on `NODE_PATH` — is **unchanged**, because it already operates on a resolved leg list.

## 5. DSL surface

A `fanout` node gains two **mutually-exclusive** modes: static `branches[]` **xor**
dynamic `expand` + `each`. Presence of `expand` ⇒ dynamic.

```jsonc
{
  "id": "review",
  "kind": "fanout",
  "expand": {                      // NEW — presence flips the node to dynamic
    "hook": "expand-files",        //   trusted expander, resolved from expand/<hook> or an exec path
    "as": "file",                  //   each item is staged for its leg as inputs/file.json
    "id_from": "$.path",           //   JSONPath → each leg's stable id (→ state-file key)
    "max_legs": 256                //   REQUIRED, 1..256; over-cap ⇒ hard block, fail loud
  },
  "each": {                        // NEW — the per-item branch TEMPLATE (replaces static branches[])
    "workflow": "review-file-agent",              // flat leg …
    "evidence": "review-file.evidence.schema.json",
    "max_iterations": 3,
    "checks": [ { "run": "schema-valid",         "on_fail": "iterate" },
                { "run": "traces-exist-in-diff", "on_fail": "iterate" } ],
    "publish": "publish-file-review"
    // …  OR  "states": [ /* a sub-pipeline template, e.g. Plan → Main → Filter */ ]
  },
  "next": "join"
},
{ "id": "join", "kind": "join", "of": "review",
  "policy": "any",                 // NEW — all (default) | any | quorum:<int|pct%>
  "next": "reduce" },
{ "id": "reduce", "kind": "merge",
  "hook": "dedup-comments",
  "inputs": [ { "from_fanout": "review", "as": "legs" } ],  // NEW — collect ALL legs' evidence
  "next": "done" }
```

**New keys:**

| Key | On | Req | Meaning |
|---|---|---|---|
| `expand.hook` | fanout | ✔ | trusted expander executable → emits the item list |
| `expand.as` | fanout | ✔ | basename each item is injected as (`inputs/<as>.json`) |
| `expand.id_from` | fanout | ✔ | JSONPath → each leg's stable, filesystem-safe id |
| `expand.max_legs` | fanout | ✔ | hard ceiling (1..256); over-cap = fail loud |
| `each` | fanout | ✔ | per-item branch template — flat (`workflow`) xor sub-pipeline (`states[]`) |
| `policy` | join | | `all` (default, = today's strict AND) `\| any \| quorum:N` |
| `inputs[].from_fanout` | merge | | collect every leg's evidence into one array input |

**`each` is exactly a branch template** — the same object a static `branches[]` entry
is (flat leg keys: `workflow`/`evidence`/`max_iterations`/`params`/`checks`/`publish`/
`inputs`; or a sub-pipeline via `states[]`). This is why the OCR per-file
`Plan → Main → Filter` pipeline is expressible with no new machinery.

## 6. Execution model

When the cursor enters a **dynamic** fanout (plan job, zone 1):

1. **Expander runs** as a distinct trusted step *before* `next.py` (so `next.py`
   stays a pure planner). It re-fetches the diff itself — never trusts agent data —
   and prints `{"items":[…]}`.
2. **Engine bounds + keys it.** If `len(items) > max_legs` → **hard block, fail
   loud** (fanout `state: failed`, exact message, **no legs**). Else assign each item
   a stable leg id from `id_from` (sanitized + hashed); **collision ⇒ fail loud**.
3. **Persist the manifest** (`<fanout>.__manifest.yaml`: item list + count + leg ids),
   CAS-pushed to `agentic-state`. This is the durable dynamic analog of static
   `branches[]` and the single source of truth for cardinality.
4. `next.py` reads the manifest via `resolve_legs` and emits the `run-fanout` matrix
   (one leg per manifest entry).
5. Each leg dispatches, iterates, publishes **independently** (existing machinery).
   Its item is staged as `inputs/<as>.json`.
6. **Join** reads `__manifest.yaml` for the expected leg set + count, waits for all
   legs to reach a terminal state, then applies `policy`.

**`next.py` stays pure:** the expander is I/O run as its own plan-job step; its output
(the manifest) is fed to `next.py` alongside state — mirroring how checks run outside
`next.py` and feed verdicts to `advance.py`.

## 7. State layout (`paths.py`)

Path-keyed files, mirroring today's fan-out layout:

```
code-review/pr-N/review.__manifest.yaml      ← NEW: item list + count + leg ids
code-review/pr-N/review.<legid>.yaml         ← one per leg (legid = hashed id_from)
code-review/pr-N/review.<legid>.<substate>…  ← nested, if `each` is a sub-pipeline
code-review/pr-N/review.__join.yaml          ← existing join marker; now reads manifest count
```

Each leg writes **only its own file** → no CAS write contention (the static-fanout
invariant carries over).

## 8. Trust zones

The expander fits the **existing** four zones with no new credential:

| Zone | Job | Piece |
|---|---|---|
| 1 Engine-pre | `plan` | **expander hook** — trusted, re-fetches diff, holds only the state PAT `plan` already has; writes the manifest |
| 2 Agent | `dispatch` | each leg's agent, sandboxed (unchanged) |
| 3 Checks | `checks` | per-leg checks over evidence + independently re-fetched diff (unchanged) |
| 4 Engine-post | `advance` | per-leg advance/publish; the `merge`/reduce hook (unchanged) |

**Expander ABI** (mirrors the publish/merge hook contract):

```
expand-<name> <state-context.json>       # env: PR, GITHUB_REPOSITORY, ENGINE_LOCAL, GH_TOKEN (diff-read only)
  → stdout: {"items": [ {...}, {...} ]}    # one object per leg; always exit 0 (nonzero = runner error)
```

The expander runs in zone 1 and needs only a **read** token (`contents`/`pull-requests`
read) to re-fetch the diff — **never** the publish token or the state PAT beyond what
`plan` already holds. Each `items[]` entry is an **opaque, author-defined object**; the
engine touches only `id_from` to key it. The engine — not the hook — writes the manifest.

## 9. Data flow

**Manifest** (`<fanout>.__manifest.yaml`, engine-written):

```yaml
fanout: review
count: 3
legs:
  - id:  "a1b2c3"          # sanitized(hash(id_from)); collision ⇒ fail loud
    key: "src/a.go"        # raw id_from value (humans / status comment)
    item: { path: "src/a.go", diff: "@@ …", lang: "go" }   # the full opaque item
  - id:  "d4e5f6"
    key: "src/b.go"
    item: { … }
```

**Item injection.** On dispatch, the engine auto-stages a leg's `item` as
`inputs/<expand.as>.json` (e.g. `inputs/file.json`) — a well-known input every leg's
agent reads. The `each` template may *also* declare its own `inputs[]` (e.g. an
upstream node's evidence), resolved exactly as branch inputs are today.

**Reduce over the dynamic set.** A `merge` with `inputs:[{from_fanout:"review",
as:"legs"}]` collects **every leg's persisted `evidence.json`** into one array staged
as `inputs/legs.json`:

```json
[ { "leg_id":"a1b2c3", "key":"src/a.go", "state":"done",   "evidence": { … } },
  { "leg_id":"d4e5f6", "key":"src/b.go", "state":"failed", "evidence": null } ]
```

The trusted merge hook (zone 4) reduces this — e.g. OCR's cross-file dedup + final
render — and can reduce over **only the survivors** (consistent with an `any`/`quorum`
join that let partial failure through). `from_fanout` reads the same
`__manifest.yaml`, so merge and join never disagree on cardinality, and it collects
from **persisted per-leg evidence on the state branch**, not job outputs — resilient
to the GHA matrix-outputs-clobber problem the same way per-leg artifacts already are.

## 10. Join policy

`join.policy` decides the barrier over the runtime leg set. Default `all` keeps every
existing join byte-identical.

| `policy` | Join succeeds iff |
|---|---|
| `all` (default) | **every** leg reached `done` (today's strict AND-barrier) |
| `any` | **≥1** leg reached `done` (fail only if all failed — OCR's actual policy) |
| `quorum:N` | **≥N** legs reached `done`; `N` is an integer count (`quorum:3`) or a percentage of `count` (`quorum:80%`) |

The **process axis** (`done`/`failed`) is what the join reads — orthogonal to the
review **verdict** (APPROVE/CHANGES_REQUESTED), exactly as today. A leg that produced
a valid review *with comments* is a process **success**.

## 11. Failure & edge handling

| Situation | Behavior |
|---|---|
| **Expander fails** (nonzero / crash / non-JSON / missing `items`) | Fanout → `state: failed`, run **halts**, distinct message (`expander 'expand-files' failed: <detail>`); **no legs, no manifest**. It is trusted infra, not an iterate condition. |
| **Over-cap** (`len(items) > max_legs`) | **Hard block, fail loud** (`expander emitted 412 items > max_legs 256`); no legs written. Never silently truncate. |
| **Zero items** | Vacuous no-op: manifest `count: 0`; engine advances straight to the join's `.next`; status comment notes "nothing to fan out." `all` = vacuously satisfied; `any`/`quorum` = **not** satisfied (they sink an empty set). |
| **Duplicate leg id** (`id_from` collision) | **Fail loud** at manifest-build (`two items map to leg id 'a1b2c3'`). |
| **Per-leg failure** (leg exhausts checks → `failed`) | Recorded on that leg only; join applies `policy`. |
| **Leg dispatch error** (infra; agent never ran) | Counts as a `failed` leg for policy; leg file records `error`. |

**Idempotency / CAS (unchanged invariants):**
- Manifest written **once** under CAS; re-entering the fanout reads it — **the
  expander is not re-run**, cardinality is **frozen for the run**. A new commit →
  `synchronize→reset` → fresh run → fresh expander (the diff may have changed).
- Each leg writes only its own file → no CAS write contention.
- The join is idempotent (reads manifest count + terminal leg states; re-firing is a
  no-op) — same as the existing `__join.yaml` mechanism.
- `advance` only ever fast-forward-pushes `agentic-state`; never force-push.

**Observability:** the accumulating status comment renders dynamic legs from the
manifest (`review · 3 files · 2 done / 1 failed → join(any): ✅`); over-cap /
expander-failure / zero-items each post a distinct, honest line — no silent gaps.

## 12. Validation (`lib.validate_protocol`)

Fail fast (exit 2) with an **actionable message** before any state is written:

- A `fanout` has **exactly one** of `{branches[]}` xor `{expand + each}` — never both,
  never neither.
- `expand` requires `hook`, `as`, `id_from`, and `max_legs ∈ [1,256]`.
- `each` is a well-formed branch template (flat needs `workflow`; sub-pipeline needs
  `states[]`; not both).
- `join.policy` ∈ `{all, any, quorum:N}`; any `quorum:N` parses to a positive count or
  percentage.
- `inputs[].from_fanout` names a **sibling fanout**.
- `max_depth` still bounds the static template tree; the `each` template counts toward
  depth exactly like a branch.

The bundled `protocol-lint.py` applies these plus the JSON Schema
(`protocol.schema.json` gains the new keys with `additionalProperties:false`).

## 13. Test strategy (this milestone — offline, `ENGINE_LOCAL`)

All offline in the existing pytest layer (`tests/`, `conftest.py`, `ENGINE_LOCAL=1`).
A **stub expander** reads a fixture file and emits a fixed `items` list —
deterministic, no diff/network.

**New fixtures** (`tests/fixtures/`), each an OCR-shaped protocol:

| Fixture | Shape | OCR analog |
|---|---|---|
| `dyn-fanout-flat` | expander → N flat legs → `join(any)` → `merge` | per-file review, flat |
| `dyn-fanout-subpipeline` | `each` = `Plan → Main → Filter` sub-pipeline | OCR's per-file pipeline |
| `dyn-nested` | per-file leg contains a *second* dynamic fanout (per-comment) | OCR's nested map (file→comments) |
| `dyn-fanout-badcap` | expander emits > `max_legs` | over-cap guard |

**Test matrix** (module style mirroring `test_engine.py` / `test_join.py`):

- **Manifest & keying:** expander → manifest persisted (count, leg ids); `id_from`
  sanitize+hash; **collision → fail loud**; CAS commit shape.
- **Expansion → dispatch:** `resolve_legs` yields N legs; item staged as
  `inputs/<as>.json`; **regression guard:** static fanout still resolves from
  `branches[]` and `code-review`'s planner output is byte-identical.
- **Over-cap:** `len > max_legs` → fanout `failed`, no legs, exact message.
- **Expander failure:** nonzero / non-JSON / missing `items` → halt, distinct message.
- **Zero items:** `all` → advances to join.next; `any`/`quorum` → sinks.
- **Join policies:** parametrized over mixed done/failed leg sets → assert `all` /
  `any` / `quorum:N` (both count and `%`) verdicts.
- **Reduce:** `from_fanout` collects per-leg evidence with `state` tags; merge sees
  survivors only.
- **Resume/idempotency:** re-enter fanout → expander **not** re-run (manifest frozen);
  re-fire join → no-op.
- **Recursion:** `dyn-nested` walks depth (per-file → per-comment); joins bubble;
  `max_depth` counts the `each` template like a branch.
- **Validator:** rejects `branches[]`+`expand` together, missing/oversized `max_legs`,
  bad `policy`, unparseable `quorum`, `from_fanout` naming a non-fanout — each with an
  actionable message.

## 14. Deferred to milestone 2 (the OCR-mimic protocol)

- A real `open-code-review`-mimic protocol (`code-review-ocr` or similar).
- A real diff-parsing expander (changed files → items).
- Live GitHub-Actions matrix wiring for **runtime-cardinality** legs (the current
  matrix is fed from static `action.legs`; it must accept the manifest-expanded list —
  the artifact-per-leg mechanism already carries over).
- End-to-end agent runs; the nested per-comment fan-out live.
- (Possible, separate) conditional/predicate transitions for OCR's Plan-skip.

## 15. Engine touch-point summary (for planning)

Additive, localized to the recursive engine (no protocol-specific logic):

- **`next.py`** — `resolve_legs` seam; on entering a dynamic fanout, consume the
  expander manifest, bound-check (`max_legs`), key legs, persist `__manifest.yaml`,
  emit the runtime leg matrix. Stays pure (expander I/O is a separate plan-job step).
- **`paths.py`** — runtime leg keys (`<fanout>.<legid>…`) and the `__manifest.yaml`
  path.
- **`join.py`** — read `__manifest.yaml` for cardinality; apply `policy`
  (`all`/`any`/`quorum`).
- **`advance.py`** — per-leg advance unchanged; `merge` gains `from_fanout` collection.
- **`lib.py`** — `validate_protocol` rules (§12); resolve the expander executable
  (same mechanism as checks/publish); a stable leg-id sanitizer/hasher.
- **`protocol.schema.json`** + **`docs/PROTOCOL-DSL.md`** — document the new keys.
- **GHA workflows** — offline-testable parts only this milestone; runtime-matrix
  wiring is milestone 2.
