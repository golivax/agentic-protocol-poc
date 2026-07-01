# Dynamic fan-out — live GitHub-Actions wiring (Milestone 2, Spec A) — design

**Date:** 2026-07-01
**Status:** design, approved for planning
**Milestone:** 2 (kickoff brief: `docs/superpowers/MILESTONE-2-BRIEF.md`)
**Predecessor:** Milestone 1 (dynamic-fanout *engine construct*), design
`docs/superpowers/specs/2026-06-30-dynamic-fanout-design.md`, merged to `main`
(PR #193). This is the first of **two** M2 specs: **Spec A (this doc)** makes the
M1 construct run *live*; **Spec B** (later) builds the real `code-review-ocr`
protocol.

## 1. Summary

Milestone 1 proved the engine can *represent* a dynamic (data-driven) fan-out —
a `fanout` node whose leg set is a runtime `__manifest.yaml` produced by a trusted
`expand` hook instead of a static `branches[]` — but only **offline** (the
`ENGINE_LOCAL` pytest layer, with fixture-reading stub expanders). A dynamic-fanout
protocol is **not turn-key on live Actions**: the live matrix in
`agentic-engine.yml` is still fed the static planner leg list, nothing stages a
leg's runtime item for its agent, the expander inherits the plan job's full
write-capable PAT, and the human-facing status comment / lint tree render zero legs
for a dynamic fanout.

Spec A closes exactly the four gaps needed to run **one** dynamic fan-out end-to-end
on GitHub Actions, and proves it with a minimal live **stub** protocol driven by a
**real** diff-parsing expander. Concretely, the four brief backlog items in scope
(numbered as in the brief; item 4 is deferred to Spec B):

- **Item 1 — Runtime-matrix wiring** — the live `strategy.matrix` legs come from the
  runtime manifest (one leg per manifest entry), not the static planner list.
- **Item 2 — Stage `inputs/<as>.json` at dispatch** — the per-leg agent receives its
  runtime item (the changed file + its diff).
- **Item 3 — Expander credential-scoping (security)** — `run_expander` hands the
  expander subprocess only a read-only token, not the state/publish PAT.
- **Item 5 — Dynamic-leg-aware rendering** — the PR status comment and the
  `protocol-lint` tree read the manifest so dynamic legs render.

**Deferred to Spec B** (recorded here so scope is unambiguous): nested `from_fanout`
resolution (brief item 4), the real `code-review-ocr` protocol shape, per-finding
nested fan-out, and the production OCR agents.

### Non-goals

- The `code-review-ocr` protocol itself, per-finding nested fan-out, and nested
  `from_fanout` — **Spec B**. (M1's fail-loud guard means a nested reduce cannot
  silently mis-reduce in the meantime.)
- Any change to the **static** fan-out code path. The whole M1 regression story
  rests on the dynamic path firing *only* on `expand`/`policy`/`from_fanout`;
  Spec A preserves that byte-for-byte (see §7).
- Judging the *substance* of evidence (unchanged engine thesis: checks verify the
  *form* of evidence, never its correctness).
- Changing the four-trust-zone model — the expander stays inside the existing
  zone-1 (plan) boundary; §5 only *scopes its credentials*, it does not move it.

## 2. The verification vehicle — `dyn-fanout-stub` protocol

Spec A is "make it run live," so its definition of done is a **live run**, which
requires a live dynamic protocol + a real gh-aw agent (the M1 fixtures are offline
stubs that cannot dispatch). We add the minimal such protocol — the live analog of
the `tests/fixtures/dyn-fanout-flat` fixture.

`.github/agent-factory/protocols/dyn-fanout-stub/`:

- **`protocol.json`** — a single top-level dynamic `fanout` named `review`:
  - `expand`: the real `expand-files` hook (see §2.1), `id_from`/`key` per the M1
    DSL, `max_legs` well under the GHA 256 cap.
  - `each`: a one-state agent sub-shape whose `workflow` is the stub agent (§2.2),
    with one deterministic check.
  - `join`: `policy: all` (simplest happy path; `any`/`quorum` are already
    offline-covered by M1 — no need to re-prove them live).
  - Trigger block: `/dyn-stub` comment command.
- **`expand/expand-files`** — the real diff-parsing expander (§2.1).
- **`*.evidence.schema.json`** — a trivial rubric: the agent attests it `examined`
  the file (negative-attestation with a trace, per the engine's evidence contract).
- **`checks/<name>`** — one deterministic check: the evidence's `examined` names the
  leg's file. (Extension-agnostic per the check ABI; a `.py` needs no bash wrapper.)
- No publish hook needed for the stub (the join → done is the proof); if a terminal
  status comment is wanted, reuse `code-review`'s pattern as a template.

**Shape choice:** single top-level dynamic fanout (single-phase). Rationale: it is
the smallest thing that exercises the full live path (expand → matrix → per-leg
dispatch → checks → join → done). The *multi-phase* `state_path` naming that Spec B
will need (the "leading-id-drop" gotcha) is de-risked **offline** by a pytest walk
(§6), not by adding a live phase here — no live cost for a naming check.

### 2.1 `expand-files` — the real diff-parsing expander

Built in Spec A (not a throwaway) and reused verbatim by Spec B's OCR protocol.

- Input (ABI): invoked as `<hook> <state-dir> <instance-key>` with `PR` in env
  (per `run_expander`); re-fetches the diff itself via `gh pr diff` (never trusts
  agent-produced data — zone-1 trusted hook).
- Output: `{"items": [{"path": ..., "diff": ...}, ...]}` — one item per changed
  file.
- OCR-style pre-filters (from OCR `internal/agent/preview.go` + `filterLargeDiffs`):
  skip **binary**, **vendored/generated**, and **oversized-diff** files. Filter
  thresholds live in the protocol's node `params` (read via the hook's env / args),
  not hardcoded.
- Fail-loud on a `gh` error (raise → `run_expander` surfaces it), consistent with
  the M1 house style.

### 2.2 The stub gh-aw agent

One `*-agent.md` → compiled `*-agent.lock.yml` (`gh aw compile`), following the
`grumpy-agent`/`security-agent` pattern:

- Read-only repo token + LLM creds only; **never** holds the state PAT (zone 2).
- Reads its runtime item from `inputs/<as>.json` (the file surfaced by §4).
- Emits minimal evidence: `{examined: ["<path>"]}` (plus whatever the trivial rubric
  requires). No real review logic — this is a plumbing proof.
- `engine.env` custom Anthropic endpoint + pinned model + `run-name` `cid:[...]`,
  same caveats as the existing agents (`strict:false`/`sandbox.agent:false` — the
  documented endpoint-allowlist weakening; unchanged here).

## 3. Item 1 — runtime-matrix wiring

The live matrix (`agentic-engine.yml` `dispatch`/`checks` jobs) already consumes
`fromJSON(needs.plan.outputs.legs)` with axis `leg:{path,workflow}` and threads
`NODE_PATH` per leg (Stage 4b). The work is ensuring the planner emits, for a
**dynamic** fanout, **one leg per manifest entry**:

- `next.py`'s fan-out action (`_fanout_action`/`legs`) must build the leg list from
  the persisted `__manifest.yaml` (which for a dynamic fanout *is* the seeded
  branch list), each leg carrying:
  - `path`: `<fanout-tree-path>.<legid>` — and for a sub-pipeline `each`, the first
    substate appended (`<fanout>.<legid>.<first-substate>`). (Spec A's stub `each`
    is single-state, but the field must be correct for B.)
  - `workflow`: the `each` template's agent workflow.
  - the per-leg `NODE_PATH`.
- Confirm the field shape a runtime leg needs matches what the matrix + downstream
  jobs already read for a static leg (so no matrix-side change beyond consuming a
  runtime-sized list).
- **Cap alignment:** GHA `strategy.matrix` hard-caps at 256; M1's `max_legs` ≤ 256
  already enforces this at expand time (fail-loud over-cap). Assert the two agree.
- Touch `agentic-orchestrator.yml` (router) and `protocol-join.yml` for path-aware
  concurrency of runtime legs (same treatment Stage 4b gave static legs).

Open verification (resolve during planning): whether `_fanout_action` *already*
builds correct legs for a dynamic fanout (it builds from the seeded `branches` list,
which for dynamic is the manifest legs) — if so, item 1 is mostly assertion +
matrix-consumption verification rather than new leg-building code.

## 4. Item 2 — stage `inputs/<as>.json` at dispatch

M1's `lib.stage_item` persists each leg's item beside its state file
(`<...>.<as>.item.json`) **offline**. Live, the dispatch/checks job must surface
that item to the leg's agent as `inputs/<as>.json`, mirroring how declared inputs
are staged (`materialize_inputs`):

- In the leg's agent job (`agentic-engine.yml`), before dispatch, read the staged
  item for this `NODE_PATH` and write it to `inputs/<as>.json` in the agent's
  workspace.
- The agent (§2.2) reads `inputs/<as>.json` to get its `{path, diff}`.
- Keep this on the **dynamic path only** — a static leg has no `<as>` item, so the
  step is a no-op / skipped when the node has no `expand`.

## 5. Item 3 — expander credential-scoping (security)

**Decision:** in-process env **allowlist-scrub** in `lib.run_expander`, plus
threading a read-only token into the plan step. (Chosen over a separate
least-privilege job, which would move the expander out of zone 1 and diverge from
M1's "expander runs inline in `next.py`" design.)

Today `run_expander` does `env = dict(os.environ)` and forwards the plan step's full
environment — which carries `STATE_REMOTE` (authenticated with `POC_DISPATCH_TOKEN`),
`GH_TOKEN = POC_DISPATCH_TOKEN`, and `PUBLISH_TOKEN = POC_DISPATCH_TOKEN` — to the
expander subprocess. The docstring already *claims* the expander gets "only a read
token"; today that claim is **false** (recorded honestly in `docs/STATUS.md`). Spec A
makes it true.

Change:

- **`lib.run_expander`**: build the subprocess env from a strict **allowlist**, not
  a copy. Pass only: `PATH`, `PR`, the state-dir arg, `ENGINE_LOCAL` (for the
  offline stub), and exactly one token — `GH_TOKEN` set to a **read-scoped** token
  from a dedicated env var (e.g. `EXPANDER_TOKEN`), never `STATE_REMOTE` /
  `PUBLISH_TOKEN` / the broad PAT. Everything else is dropped by default (so a future
  added plan-job env var cannot leak by default — the whole point of allowlist over
  denylist).
- **`agentic-engine.yml` plan step**: provide the read-only token. Simplest source
  is the workflow's default `GITHUB_TOKEN` (with the workflow's `permissions:`
  granting `contents: read` + `pull-requests: read`, which `expand-files` needs for
  `gh pr diff`). Thread it into the plan step as `EXPANDER_TOKEN`.
- Update the `run_expander` docstring + `docs/STATUS.md` to record the claim is now
  **enforced** (removing the "known deviation").

Trust-zone note: the expander still runs in zone 1 (plan). This item does not change
*where* it runs, only *what credential it can see* — the plan job as a whole still
holds the PAT for CAS-push, but the expander subprocess no longer inherits it.

## 6. Item 5 — dynamic-leg-aware rendering (cosmetic)

Two renderers iterate only the static `state["branches"]`, so a dynamic fanout shows
zero legs:

- **`lib.render_fanout_status_body`** — when the node has `expand`, resolve legs from
  the `__manifest.yaml` (via the existing `resolve_leg_ids` / manifest read) and
  render a section per manifest leg (id + key + state), matching what a static fanout
  renders. Check-run gating and join logic are already manifest-correct; only the
  human comment degrades today.
- **`protocol-lint.py` tree renderer** — when a fanout has `expand` (no static
  `branches[]`), render the `each` template as the leg shape and annotate
  `inputs: legs ← <id_from>` rather than showing nothing.

Cosmetic-only (no gating behavior changes), but required for the live stub run to be
legible.

## 7. Backward-compatibility invariant

Every change is gated on the dynamic markers so **all** existing protocols and
fixtures stay byte-identical:

- Items 1–4 fire only when a node has `expand` (item 1 leg-building), stages an
  `<as>` item (item 2), calls `run_expander` (item 3), or renders a fanout with
  `expand` (item 4-rendering).
- The static `strategy.matrix` leg list, the static status-comment rendering, and
  the static path in `next.py`/`join.py` are untouched.
- Regression guard: the existing static fixtures (`cap-single-agent`,
  `simple-fanout`, `cap-mp-fanout-gate`, `deep-fanout`, …) and their pytest walks
  must remain green and byte-unchanged.

## 8. Testing strategy

**Offline (pytest, `ENGINE_LOCAL`) — the primary gate:**

- Extend `tests/test_dynamic_fanout.py`:
  - `expand-files` unit: parse a sample `gh pr diff` → items; the three pre-filters
    (binary / vendored / oversized) drop the right files; fail-loud on `gh` error.
  - `run_expander` env-scrub: the subprocess sees only the allowlisted env + the read
    token; `STATE_REMOTE`/`PUBLISH_TOKEN`/broad-PAT are absent. (Assert via a stub
    expander that dumps its env.)
  - `next.py` dynamic legs: `action.legs` has one entry per manifest leg with the
    right `path`/`workflow`/`NODE_PATH` (incl. the sub-pipeline-each first-substate
    field, exercised via a fixture even though the stub is single-state).
  - Renderers: `render_fanout_status_body` over a dynamic fanout emits one section per
    manifest leg; `protocol-lint` renders the `each` tree.
  - **Multi-phase `state_path` walk** (B de-risk): a fixture with `preflight → dynamic
    review` confirms leg state-file names under the multi-phase leading-id-keep rule.
- Static regression: all existing fixtures byte-identical, suite green.
- `protocol-lint.py` clean on `dyn-fanout-stub/protocol.json`.

**Live (gated, on a real PR) — the definition of done:**

- `/dyn-stub` on a PR touching ≥2 files → expand-files fans out over the real changed
  files → per-file stub agent runs → checks pass → join(all) → done. Verify the PR
  status comment renders one section per file (item 5).
- Edge: a PR whose files are *all* filtered (zero legs) and, if cheaply forced, an
  over-cap case → fail-loud, no silent empty run.
- Expect 1–3 live-only bugs (every prior milestone had them); live-debug pass.

## 9. Deploy / merge plan

Workflows and agent locks run from the **default branch** for
`issue_comment`/`repository_dispatch`, so all of `agentic-orchestrator.yml`,
`agentic-engine.yml`, `protocol-join.yml`, the `dyn-fanout-stub` protocol, and the
compiled agent lock **must land on `main`** before the live walk. Development on
`feat/dynamic-fanout-live-wiring`; merge to `main` (gated, explicit user OK) before
the gated live PR verification, exactly as prior milestones' live stages did.

## 10. Where things are (implementation map)

- Engine: `.github/agent-factory/engine/{next,join,lib,paths,advance,run-checks}.py`
  — items 1 (`next.py` legs), 3 (`lib.run_expander`), 5 (`lib.render_fanout_status_body`).
- Lint: `.github/agent-factory/engine/protocol-lint.py` — item 5 tree renderer.
- Protocol (new): `.github/agent-factory/protocols/dyn-fanout-stub/`
  (`protocol.json`, `expand/expand-files`, evidence schema, `checks/*`).
- Workflows: `.github/workflows/{agentic-orchestrator,agentic-engine,protocol-join}.yml`
  — items 1 (matrix), 2 (`inputs/<as>.json` staging), 3 (`EXPANDER_TOKEN`).
- Agent: new `*-agent.md` → `*-agent.lock.yml` (`gh aw compile`).
- Tests: `tests/test_dynamic_fanout.py`; fixtures under `tests/fixtures/dyn-*`;
  harness in `tests/conftest.py` (`run_engine`, `engine_env`, `read_state_yaml`).

## 11. Risks / open questions (resolve during planning)

- **R1 — `_fanout_action` may already emit dynamic legs.** It builds from the seeded
  `branches` list, which for a dynamic fanout *is* the manifest legs. If so, item 1
  is mostly assertion + matrix-consumption verification, not new code. Confirm the
  runtime leg's `path`/`workflow`/first-substate fields early.
- **R2 — read token permissions.** The default `GITHUB_TOKEN` must have
  `contents: read` + `pull-requests: read` for `gh pr diff` in the plan job; verify
  the workflow's `permissions:` block grants them, and that an `issue_comment`-
  triggered run on `main` still gets a usable token.
- **R3 — `inputs/<as>.json` timing.** The staged item is written to the state branch
  by the plan job; the agent job must read the *leg-scoped* item for its `NODE_PATH`.
  Confirm the state-branch checkout in the agent job exposes it.
- **R4 — live-only surprises.** Prior milestones each surfaced 1–3 bugs the offline
  layer could not (missing `GH_TOKEN` in a job, silent empty-verdicts). Budget a
  live-debug pass.
