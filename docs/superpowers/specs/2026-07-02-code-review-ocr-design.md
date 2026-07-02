# `code-review-ocr` protocol — the OCR-mimic (Milestone 2, Spec B) — design

**Date:** 2026-07-02
**Status:** design, approved for planning
**Milestone:** 2 (kickoff brief: `docs/superpowers/MILESTONE-2-BRIEF.md`, §B)
**Predecessor:** Milestone 2 Spec A (live dynamic-fanout wiring), design
`docs/superpowers/specs/2026-07-01-dynamic-fanout-live-wiring-design.md`, merged to
`main` and live-verified on PR #196. This is the second and final M2 spec: **Spec A**
made the dynamic-fanout construct run live; **Spec B (this doc)** builds the real
`open-code-review`-mimic protocol on top of it.

## 1. Summary

Alibaba's `open-code-review` (OCR) is, structurally, a *map over N changed files →
per-file `Plan → Main → Filter` → (nested) map over K findings → reduce → post one
review*, where N and K are discovered at runtime by parsing the diff. Milestone 1
added the missing engine primitive (dynamic fan-out), Spec A made it run live, and
this spec builds the **full nested protocol**: `code-review-ocr`.

The protocol fans out over a PR's changed files (dynamic fan-out via the `expand-files`
expander built in Spec A), runs a three-phase per-file sub-pipeline, fans out *again*
over each file's candidate findings for an LLM filter/relocate pass, reduces the
survivors per file, then reduces across all files (cross-file dedup) and posts **one**
GitHub review. It is fully automated — no human gate — mirroring OCR.

Two infra pieces Spec A explicitly deferred are built here because the nested shape
requires them:
- **Nested `from_fanout`** — a `merge` reducing over a fanout that is *not* the top
  fanout (the per-file `reduce` over its own `findings` fanout).
- **Matrix-size path-only delivery** — per-file agents receive only the file `path`
  and re-fetch their diff, instead of the full `{path, diff}` riding in the matrix
  (which caps at ~1 MB and does not scale to real PRs).

Everything else is reuse: `expand-files` (Spec A) verbatim, `code-review`'s
`traces-exist-in-diff` positioning check (OCR's `ResolveLineNumbers`/`ReLocateComment`
analog) and its `_review.py` single-review publication mechanism.

### Non-goals

- OCR's conditional/predicate transitions (e.g. "skip Plan when the diff is below a
  line threshold"). Per the M1 design's non-goals, that decision is pushed *inside*
  the agent; the engine has no predicate transitions. The `plan` phase always runs.
- Judging the *substance* of a finding (unchanged engine thesis: checks verify the
  *form* of evidence — the anchor resolves, the schema is filled — never whether the
  critique is correct).
- Changing the four-trust-zone model. (One deliberate, user-approved DSL/JSON-schema
  addition is in scope: the optional `expand.matrix_fields` key — see §4.2. It is
  additive and backward-compatible; no other schema change.)
- A human approval gate (§6) — OCR is fully automated.

## 2. The protocol tree

```
root (sequence)
└─ review          [fanout]  expand: expand-files (changed files), policy: any
   └─ «each file» (sequence)                       ← the per-file sub-pipeline
      ├─ plan          [agent]   ocr-plan-agent    — scope this file's review
      ├─ main-review   [agent]   ocr-main-agent    — emit K anchored candidate findings
      ├─ findings      [fanout]  expand: expand-findings (this file's findings), policy: any
      │  └─ «each finding» filter [agent] ocr-filter-agent — validate/relocate anchor, keep|drop
      ├─ join-findings [join]    of: findings, policy: any
      └─ reduce        [merge]   from_fanout: findings — collect surviving findings for this file
   (end per-file)
├─ join-review     [join]    of: review, policy: any
└─ merge           [merge]   from_fanout: review — cross-file dedup + post ONE GitHub review
```

- **Depth:** the deepest leaf (`filter`) sits at node-path `review . <fileleg> .
  findings . <findingleg>` = depth 4, within the default `max_depth` 5. Tight but
  legal; the validator enforces it.
- **Both fanouts are dynamic** (runtime-sized). The `findings` fanout inside the
  `review` fanout's `each` is a **nested dynamic fan-out** — M1 supports the
  structure (offline fixture `dyn-nested`); the *nested reduce* over it is the new
  infra (§4).
- **Trigger:** `{on: issue_comment, comment_prefix: "/ocr-review", command: "start"}`
  (same shape as `code-review`'s `/review`). Instance key `pr-<N>`.

## 3. Expanders

- **`expand-files`** — reused **verbatim** from Spec A
  (`.github/agent-factory/protocols/code-review-ocr/expand/expand-files`, copied from
  the `dyn-fanout-stub` protocol): parse `gh pr diff` → one item per changed file with
  the OCR skip-binary/vendored/oversized pre-filters. `id_from: $.path`.
- **`expand-findings`** — new, small: reads the per-file `main-review` evidence (its
  `findings` array, surfaced as the fanout node's input) → one item per candidate
  finding `{finding_id, path, existing_code, side, line[/start_line], comment}`.
  `id_from: $.finding_id`. Runs in zone 1 (plan), trusted, fail-loud. Under
  `ENGINE_LOCAL` reads a fixture like Spec A's expanders.

## 4. Infra built here (the two Spec-A deferrals)

### 4.1 Nested `from_fanout` (`lib.run_merge_hook`)

Today `run_merge_hook` computes `fo_tree_path = [inp["from_fanout"]]` — the top-level
fanout id only — and M1 added a **fail-loud guard** so a nested reduce raises rather
than silently mis-reducing. This spec implements the real resolution:

- Thread the merge node's **node-path** into `run_merge_hook` (it already receives the
  proto + instance; add the current tree-path, as the recursive `advance`/`next`
  callers already hold `NODE_PATH`).
- For a `from_fanout` input, resolve the sibling fanout's tree-path **relative to the
  merge's node-path** (the fanout named by `from_fanout` that is in-scope at this
  depth), not `[id]`. `collect_fanout_evidence` already reads the manifest by
  arbitrary tree-path, so once the path is correct it collects the right legs.
- The top `merge` (`from_fanout: review`) resolves to `["review"]` (unchanged); the
  per-file `reduce` (`from_fanout: findings`) resolves to
  `["review", "<fileleg>", "findings"]`.
- `lib.validate_protocol` Rule 6 (`from_fanout` names an in-scope fanout) already
  exists for the top level; extend it to validate a nested `from_fanout` against the
  fanout in scope at the merge's depth.

### 4.2 Matrix-size: path-only delivery for file legs

Spec A threads each dynamic leg's full item (`{path, diff}`) into `matrix.leg.inputs`
→ the plan job's `legs` output (a `$GITHUB_OUTPUT`, ~1 MB cap) and `strategy.matrix`.
For a real PR (many files × large diffs) this overflows. The two fanouts have opposite
needs: a file's big field (`diff`) is **re-fetchable** (`gh pr diff -- <path>`) so only
`path` need ride the matrix; a finding is small but **not** re-fetchable (it came from
`main-review`'s evidence, not the diff) so it must be inlined in full. A single rigid
rule cannot serve both, so the item's matrix projection is made **declarative** — a
**user-approved DSL addition**:

- **New optional schema key `expand.matrix_fields: [<item keys>]`** — the subset of an
  item's keys to inline into `matrix.leg.inputs`. **Default (unset) = the full item**
  (today's Spec A behavior — backward-compatible; every existing dynamic protocol is
  byte-unchanged). `expand-files` sets `matrix_fields: ["path"]`; the `findings` fanout
  omits it (findings are small and must ship whole).
- **Projection point:** `_fanout_action`/`enter_node`, when building `legs[].inputs`
  for a dynamic leg, projects the staged item down to `matrix_fields` (when set). The
  **full item always stays durable on the state branch** (the manifest/staged item) —
  `matrix_fields` only trims what rides the matrix, never what is persisted.
- **Fail-loud size guard:** after projection, if a fanout's serialized `legs` output
  would still exceed a safe cap, the engine **raises** (same discipline as `max_legs`
  over-cap) — a protocol author who forgot to trim gets a clear error, never a silent
  truncation.
- The per-file agents (`ocr-plan-agent`, `ocr-main-agent`) receive `inputs.file.path`
  and **re-fetch** their diff: `gh pr diff -- <path>` (they run in a repo checkout with
  a read token) — the OCR agent model (each subtask fetches its own file context).
- The **per-finding** `findings` legs carry the small finding object inline (no
  `matrix_fields`) — unchanged Spec A mechanism.

Schema/validator: add `matrix_fields` (optional array of non-empty strings) to the
`expand` object in `protocol.schema.json`; `lib.validate_protocol` checks it is an
array of strings when present. (No `id_from`-subset constraint: the leg id/key is
already derived and stored in the manifest at expand time, so the matrix projection is
purely about what the agent receives.) This is the ONLY DSL change in Spec B.

## 5. Evidence schemas + deterministic checks

Reuse the `code-review` finding shape wherever possible (so `traces-exist-in-diff`
applies verbatim).

- **`plan.evidence.schema.json`** — `{examined: [path], plan_items: [string]}`
  (negative-attestation with a trace: the agent read the file). Check: `schema-valid`.
- **`main-review.evidence.schema.json`** — `{files: [{path, findings: [{finding_id,
  existing_code, side, line[/start_line], comment}]}]}` — the `grumpy` shape plus a
  `finding_id` (stable id the `findings` expander keys on). Checks: `schema-valid`,
  `traces-exist-in-diff` (every finding's anchor resolves in the independently-fetched
  diff — **reused verbatim**).
- **`filter.evidence.schema.json`** — `{finding_id, keep: bool, anchor: {side,
  line[/start_line]}, reason}`. Checks: `schema-valid`, `filter-verdict-valid` (new,
  small: `finding_id` matches the leg, `keep` is boolean, a kept finding carries an
  anchor). If `keep` and the anchor changed (relocation), `traces-exist-in-diff`
  re-validates it.

All checks obey the exit-0 ABI (guard non-dict evidence — the Spec A lesson) and read
node-scoped config from `CHECK_PARAMS`.

## 6. Reduce / publish, automation, policy

- **Per-file `reduce`** (`merge`, `from_fanout: findings`): collects the `filter` legs
  that returned `keep: true`, emitting the file's surviving findings. Trusted zone 4.
- **Top `merge`** (`from_fanout: review`): collects every file's surviving findings,
  **dedups** cross-file (same anchor/snippet reported twice), and posts **one** GitHub
  review via the reused `_review.py` mechanism. Because every posted anchor already
  passed `traces-exist-in-diff`, the single review call carries only valid positions
  and will not 422.
- **No human gate.** The top `merge` posts directly; there is no `approval` gate. This
  mirrors OCR (fully automated) and keeps the protocol a pure map→reduce.
- **Join policy `any`** at both joins (OCR's actual policy): the barrier's *process*
  verdict fails only if **every** leg failed; partial failure still reduces + posts the
  survivors. Orthogonal to finding verdicts, as always.
- **Publish token:** the top merge needs a write-capable token to post the review
  (`PUBLISH_TOKEN`, as `code-review` uses). Per-file `reduce` is state-only (no GitHub
  write).

## 7. Agents (gh-aw)

Three read-only agents, each following the Spec A `dyn-stub-agent` /
`code-review` pattern (custom Anthropic endpoint via `engine.env`, `strict:false`,
`sandbox.agent:false`, `run-name` cid, `permissions: {contents:read,
pull-requests:read}`, evidence artifact upload, **`safe-outputs.noop.report-as-issue:
false`** to suppress the gh-aw noise issue Spec A hit), compiled to `.lock.yml` via
`gh aw compile` (revert the unrelated lock drift; commit `.md`+`.lock.yml` together):

- **`ocr-plan-agent`** — reads `inputs.file.path`, re-fetches `gh pr diff -- <path>`,
  emits `plan` evidence (scope/what to review).
- **`ocr-main-agent`** — reads `inputs.file.path` (+ the plan, threaded as an input),
  re-fetches the diff, emits `main-review` findings with anchors + stable `finding_id`s.
- **`ocr-filter-agent`** — reads `inputs.finding` (the candidate finding), re-fetches
  the relevant diff hunk, decides `keep`/`drop` and validates/relocates the anchor
  (OCR's `ReviewFilter`/`ReLocateComment`), emits `filter` evidence.

## 8. Backward-compatibility invariant

Static-path byte-identity holds (the M2 regression story): every change fires only on
dynamic markers. Item 4.1 (`run_merge_hook` node-path) only alters behavior for a
`from_fanout` merge; a static protocol has none. Item 4.2 (matrix projection) only
affects a dynamic file leg. All existing protocols/fixtures stay green and unchanged.

## 9. Testing strategy

**Offline (pytest, `ENGINE_LOCAL`) — primary gate:**
- Nested `from_fanout`: a fixture with a per-file `reduce(from_fanout: findings)`
  inside the `review` fanout's `each`, plus the top `merge(from_fanout: review)`;
  assert each reduce collects the *correct* nested legs (not the top fanout), and the
  fail-loud guard is replaced by real resolution.
- Matrix projection: the file-leg matrix input carries only `path` (not `diff`); the
  finding legs carry the finding; static legs unchanged.
- `expand-findings` unit: main-review evidence → one item per finding, keyed by
  `finding_id`; fail-loud on malformed.
- Full OCR-shaped offline walk over `code-review-ocr` with stub expanders + a fixture
  main-review evidence: files → per-file plan/main/findings-fanout/join/reduce → top
  join/merge; assert the reduce/merge outputs.
- `traces-exist-in-diff` / `schema-valid` reused; `filter-verdict-valid` new tests
  (incl. garbage-evidence exit-0).
- `protocol-lint` clean on `code-review-ocr` (dynamic-leg-aware renderer from Spec A
  shows the `each` templates).
- Static regression: all existing fixtures byte-identical, suite green.

**Live (gated, on a real PR) — definition of done:**
- `/ocr-review` on a multi-file PR → per-file fan-out → per-file plan/main → nested
  per-finding filter fan-out → per-file reduce → cross-file dedup → **one** posted
  GitHub review with valid inline anchors.
- Edges: a file with zero findings (findings fanout is vacuous, reduce empty); an
  all-findings-dropped file; over-cap files/findings (fail-loud). Expect 1–3 live-only
  bugs; live-debug pass.

## 10. Deploy / merge + staged plan

Workflows + agent locks run from `main` (issue_comment), so the protocol dir, three
agent locks, and the engine/workflow changes land on `main` before the live walk
(gated, explicit user OK — as Spec A). Development on `feat/code-review-ocr`.

Although this is one spec, the implementation **plan is staged** for reviewable
increments:
1. **Infra:** nested `from_fanout` (4.1) + matrix path-only projection (4.2), offline.
2. **Protocol + checks:** `code-review-ocr/` (protocol.json, evidence schemas,
   `expand-findings`, `filter-verdict-valid`, reused checks/publish) + offline OCR walk.
3. **Agents:** the three gh-aw agents + compiled locks.
4. **Live:** gated merge to `main` + `/ocr-review` verification + live-debug.

## 11. Where things are (implementation map)

- Engine: `.github/agent-factory/engine/{lib.py (run_merge_hook, validate_protocol),
  next.py (matrix projection), paths.py, protocol.schema.json (`matrix_fields`)}`.
- Protocol (new): `.github/agent-factory/protocols/code-review-ocr/` (protocol.json,
  `expand/expand-files` [copied from Spec A], `expand/expand-findings`,
  `*.evidence.schema.json`, `checks/*` [reuse `traces-exist-in-diff`, `schema-valid`;
  new `filter-verdict-valid`], `publish/*` [reuse `_review.py` + a thin OCR entrypoint;
  per-file `reduce` hook]).
- Workflows: `.github/workflows/{agentic-engine.yml, agentic-orchestrator.yml,
  protocol-join.yml}` (matrix projection + path concurrency); three new
  `ocr-*-agent.md` → `.lock.yml`.
- Tests: `tests/test_dynamic_fanout.py`, `tests/fixtures/` (new OCR-shaped +
  nested-from_fanout fixtures).

## 12. Risks / open questions (resolve during planning)

- **R1 — matrix projection mechanism (§4.2). RESOLVED (user-approved):** add the
  optional `expand.matrix_fields` schema key (default = full item, backward-compatible)
  + a fail-loud size guard. `expand-files` sets `["path"]`; the findings fanout ships
  the whole item. Chosen over a rigid `id_from`-only rule (breaks findings) and over
  silent size-based auto-trim (implicit, against the fail-loud house style).
- **R2 — depth budget.** The `filter` leaf is at depth 4; if any wrapping (e.g. a
  per-file preflight) is added later it risks `max_depth` 5. Keep the tree flat as
  designed; the validator will catch violations.
- **R3 — nested `from_fanout` node-path plumbing.** `run_merge_hook` must learn the
  merge's node-path; confirm the recursive `advance` caller passes it (it holds
  `NODE_PATH`). The M1 fail-loud guard proves the current call sites.
- **R4 — live cost/scale.** A PR with N files × K findings spawns N plan + N main +
  ΣK filter agents. Verify against a *small* multi-file PR first; the over-cap guards
  (`max_legs`) bound blast radius.
- **R5 — plan/main input threading.** `main-review` needs the `plan` output; confirm
  the sub-pipeline input-passing (Plan-2 `resolve_inputs`, already used by the
  recover-mental-model sub-pipeline) carries a prior phase's evidence to a later phase.
