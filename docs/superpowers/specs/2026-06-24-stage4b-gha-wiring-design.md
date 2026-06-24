# Stage 4b — GitHub Actions `NODE_PATH` Wiring

- **Date:** 2026-06-24
- **Status:** Design approved; ready for implementation plan
- **Depends on:** Stage 4a (engine unification, branch `feat/stage4-recursive-engine-unification`, HEAD `b037bd2`, 401 tests). This plan continues on the same branch.
- **Parent spec:** `docs/superpowers/specs/2026-06-23-stage4-recursive-engine-unification-design.md` (§5 is the high-level version of this).

## 1. Motivation

Stage 4a unified the engine onto a single `NODE_PATH` coordinate but left the
GitHub Actions layer threading the retired `(BRANCH, PHASE, SUBSTATE)` triple and
the retired `protocol-advance` dispatch type. As a result the engine on the 4a
branch is **inoperative under real GHA** — `advance.py` now hard-requires
`NODE_PATH` (exits 1 without it) and `next.py continue` rejects a path-less
continue (exits 2). Stage 4b rewires the three workflow files to the `NODE_PATH`
axis so the unified engine runs in production, and adds the one small engine emit
change the GHA matrix needs.

**Critical sequencing:** 4a + 4b must land on `main` together (or 4b before any
4a→main merge). Merging 4a alone breaks every live `/review` and `/recover`. This
spec's deliverable is what makes 4a safe to ship.

## 2. The protocol DSL is untouched

Stage 4b changes **GitHub Actions YAML + one engine emit helper only**. It does
**not** change the protocol.json DSL/schema (a human-authored public contract —
see the `protocol-dsl-stability` standing constraint). No new protocol fields, no
renamed keys, no changed field semantics.

## 3. The one engine emit change — enrich `legs[]`

Today `next.py` `_fanout_action` emits `legs: [{"path": <branch path>}]` — only a
path, and for a **sub-pipeline branch** the path stops at the branch
(`recover.rationale`) rather than the first agent sub-state
(`recover.rationale.draft`). The authoritative per-leg data (`workflow`,
`substate`) lives on `branches[]`, which the GHA matrix is dropping.

**Change:** make each `legs[]` entry carry the **leaf agent path + workflow**, so
`legs[].path` is exactly the `NODE_PATH` to dispatch/check/advance and
`legs[].workflow` is the agent to run:

```python
# next.py _fanout_action — per branch b in `branches`:
#   flat branch:         path = fanout_path + [b.id]            (e.g. review.grumpy)
#   sub-pipeline branch: path = fanout_path + [b.id, b.substate] (e.g. recover.rationale.draft)
leg_path = path + [b["id"]] + ([b["substate"]] if b.get("substate") else [])
legs.append({"path": ".".join(leg_path), "workflow": b.get("workflow")})
```

`branches[]` (with `substate`) already carries everything needed; this is a pure
re-projection. The nested-fanout `continue` emit (which also goes through
`_fanout_action`) gets the same treatment for free. This is **pytest-coverable**:
a unit test asserts `legs` for code-review (`review.grumpy`/`review.security`,
each with its workflow), recover (`recover.summary` + `recover.rationale.draft`
with `rmm-draft-agent`), and deep-fanout. `branches[]` may be kept as-is or
dropped from the action once the workflows no longer read it (see §4); keeping it
is harmless and lower-risk, so the default is to keep it and simply stop reading
it in YAML.

## 4. `agentic-engine.yml`

- **Matrix axis** `leg: {branch, substate}` → **`leg: {path, workflow}`**, built
  in the `plan` job from `action.legs` (replace the `branches`-from-jq logic that
  produced `{branch, substate}` with passing `legs` through). For the single
  agent-phase / single-agent case the action is `run-agent`; the plan job emits a
  one-element `legs` list carrying that node's path + workflow (the path comes
  from the action — see §6 open item).
- **Thread `NODE_PATH=${{ matrix.leg.path }}`** into the dispatch, checks, and
  advance jobs. **Delete** the `BRANCH` / `PHASE` / `SUBSTATE` env wiring on those
  jobs.
- **`ctx` step (plan job):** on `repository_dispatch` `protocol-continue`, set
  `NODE_PATH = github.event.client_payload.path`. Delete the
  `branch`/`phase`/`substate` payload parsing and the `protocol-advance` →
  `advance-phase` case (both retired). Entry events (`issue_comment` →
  `start`/`override`/`resolve-gate`/`answer`) are unchanged except they no longer
  set branch/phase/substate.
- **`dispatch` step:** resolve the agent workflow from `${{ matrix.leg.workflow }}`
  (delete the `lib.agent-workflow` call). CID leg token derived from the
  path (sanitize to a CID-safe token).
- **`checks` step:** derive the check `NODE` from `NODE_PATH` (the run-checks
  contract already accepts the path coordinate via 4a; confirm and pass it).
- **`advance` step:** invoke `advance.py` with `NODE_PATH` only.
- **Artifact names** `runmeta-…` / `verdicts-…` keyed on the **dot-path
  directly** (`runmeta-review.grumpy`) — dots are legal in GitHub artifact names,
  and node ids cannot contain `.` (it is the path separator), so the dot-path is
  unique with no lossy sanitization. A unit test on the (tiny) sanitizer/keying
  helper, if one is introduced, asserts distinctness over representative deep
  paths; if the raw dot-path is used as-is, no helper is needed.

## 5. `protocol-join.yml` and `agentic-orchestrator.yml`

- **`protocol-join.yml`:** add `NODE_PATH: ${{ github.event.client_payload.path }}`
  to the `join.py` step env (empty → top join, unchanged). Concurrency group →
  `join-<instance>-<client_payload.path>` (nested joins at different fanout paths
  don't serialize against each other or the top join).
- **`agentic-orchestrator.yml`:** concurrency group →
  `agentic-<instance>-<client_payload.path>` (replacing `…-<branch>`). Drop
  `protocol-advance` from the `on: repository_dispatch types` list. Optionally add
  the path to `run-name` for debuggability.

## 6. Open implementation detail (resolve in the plan)

The single-agent / agent-phase entry action is `run-agent` (not `run-fanout`), and
today it does **not** carry a `legs`/`path` on every emit (the recursive
`continue`-at-agent emits `path`, but the depth-1 agent-phase entry emits
`phase`). The plan must ensure **every dispatched action exposes the leg path +
workflow** the matrix needs — either by having the plan job derive the one-element
`legs` for a `run-agent` (from the action's `phase`/path + the resolved workflow),
or by extending the engine's `run-agent` emits to always include `path` +
`workflow` (mirroring §3). Preference: make the engine emit `path` + `workflow` on
**all** dispatchable actions (`run-fanout` legs and `run-agent`) so the GHA layer
reads one uniform shape — this is the cleanest seam and is pytest-coverable. The
plan's first task pins this down against code-review's `preflight` (agent phase)
and the single-agent capability fixture.

## 7. Security (CLAUDE.md invariant)

`client_payload.path` / `NODE_PATH` and all agent-derived strings stay strictly
`env:`-passed, never interpolated into a `run:` block — same posture as the
existing `branch`/feedback handling. The engine already validates path segments
against protocol nodes (`node_at_path` → None on an unknown segment); the GHA
layer never `eval`s or path-joins the payload into a filesystem path.

## 8. Verification (4b done-bar)

4b cannot be pytest-driven end-to-end (it is GHA YAML + live). Its gates are:
1. **Engine emit unit test** — `legs[]` (and `run-agent`) carry the correct leaf
   path + workflow for code-review, recover, deep-fanout, single-agent. Full
   pytest suite stays green.
2. **`actionlint`** clean on the three workflow files (add it to CI if not
   present).
3. **Structural no-legacy check** — a test/grep asserting the workflows no longer
   reference `BRANCH`/`PHASE`/`SUBSTATE` env wiring, `protocol-advance`, or
   `lib.agent-workflow` for leg resolution.

Full behavioral validation (a live PR walking code-review, recover, and the new
`deep-review-stub` through real Actions) is **Stage 4c**.

## 9. Out of scope (Stage 4c / later)

- The live `deep-review-stub` protocol + gh-aw agents + live PR verification (4c).
- Re-verifying code-review + recover on real Actions (4c).
- Dropping the residual `client_payload[branch]`/`[substate]` that `advance.py`'s
  iterate redispatch still emits alongside `[path]` (harmless relay remnant; clean
  up opportunistically when the matrix is fully path-only).

## 10. Risks

- **Live-only bugs** (the layer 4b can't unit-test): a job that now dispatches but
  lacks a needed token; a path that's fine in a unit test but wrong under a real
  protocol name/depth; artifact-name edge cases. These surface in 4c — budget a
  live-debug pass there (the recover-mm precedent caught 2 such bugs).
- **The §6 agent-action path/workflow shape** is the one real design decision; the
  plan resolves it first so the matrix has a uniform leg shape.
