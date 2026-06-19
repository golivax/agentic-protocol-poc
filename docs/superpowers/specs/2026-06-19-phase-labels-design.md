# Phase Labels — Design

**Date:** 2026-06-19
**Status:** Approved (brainstorming) → ready for implementation plan

## Problem

A workflow run is one transition of a state machine whose cursor (the "head") lives
in `_instance.yaml` as `inst["phase"]`. The engine always knows the current phase,
and already mirrors that head to the PR via a status comment and check-runs — but
**not** via a PR label. A reviewer scanning the PR list can't see which phase a PR
is in (pre-flight gate? review? approval gate?) without opening the status comment.

We want the engine to keep a PR label in sync with the current phase, in a way that
is **as protocol-agnostic as possible** — mapping to whatever phases the active
protocol declares, plus an engine-level "setup" phase (before the first protocol
phase runs) and the engine's terminal outcomes.

## Goals

- One PR label always reflects the current head, swapped on every transition.
- Protocol-agnostic: the mechanism reads phase identity + display text from the
  protocol; it hardcodes nothing protocol-specific.
- Cover the pre-protocol **setup** window and **terminal** outcomes
  (`done`/`failed`/`blocked`), which are engine concepts, not protocol states.
- Best-effort: labeling must never break or block a state transition.
- Preserve the v1 `grumpy-review` regression baseline byte-for-byte.

## Non-goals

- Labeling the v1 single-agent `grumpy-review` protocol (no `_instance.yaml`).
- Per-phase label colors as a required feature (optional future extension).
- Surfacing per-branch fan-out leg state as labels (the head is the *phase*, not
  the leg).

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Label text source | Explicit optional `label` field per state in `protocol.json`; humanized-id fallback. |
| Terminal-label text source | Engine defaults, overridable via an optional top-level `phase_labels` map in `protocol.json`. |
| Namespace / mutual exclusion | Track the currently-applied label string in `_instance.yaml`; remove exactly that one before adding the next (no prefix convention). |
| Terminal outcomes labeled? | Yes — `setup`, `done`, `failed`, `blocked` all get labels. |
| Scope | Instance-based protocols only (`code-review-pipeline`, `multi-grumpy`). v1 untouched. |

## Approach

**An `ensure_phase_label` reconciler** (chosen over scattering add/remove calls, and
over doing it in workflow YAML). It is idempotent: *read the current head from
state, make the PR's labels match it, record what was applied.* It is co-located
with the existing status-comment refresh seams, which already fire on every cursor
move — so the label tracks the head exactly like the status comment does.

Rejected alternatives:
- **Scatter add/remove at each cursor write** — 5+ hand-rolled sites, not
  idempotent, easy to miss a terminal path.
- **Do it in workflow YAML (`gh pr edit`)** — terminal states happen in
  `advance.py`/`join.py` where no plan step runs, and it duplicates label-text
  resolution into bash.

## Components

### 1. `phase_label_text(protocol, key)` — pure resolver (in `lib.py`)

Maps a state id **or** a terminal/special key to a display string:

- **Live phase** (`key` matches a `states[]` id): `state["label"]` if present, else a
  humanized id (`preflight` → `Preflight`, `code-review` → `Code review`).
- **Terminal / special key** (`setup`, `done`, `failed`, `blocked`): protocol's
  optional top-level `phase_labels[key]` override if present, else the engine
  default.

Engine defaults (chosen to read well as bare PR labels):

| key | default text |
|---|---|
| `setup` | `⚙ setup` |
| `<state>` | `state.label` or humanized id |
| `done` | `✅ done` |
| `failed` | `❌ failed` |
| `blocked` | `⛔ blocked` |

`code-review-pipeline` will add `"label": "pre-flight gate"` to its `preflight`
state and `"approval gate"` to `approval`; the rest fall back automatically.

### 2. `ensure_phase_label(dir_, pid, instance, proto, pr, head_key)` — reconciler (in `lib.py`)

Best-effort and `ENGINE_LOCAL`-aware, mirroring `set_check_run` (failure logs to
stderr and returns; never raises):

1. Resolve `new = phase_label_text(proto, head_key)`.
2. Read `_instance.yaml`; `prev = inst.get("phase_label", "")`. If there is no
   instance file, **no-op** (this is what excludes v1).
3. If `prev == new`, return (idempotent).
4. **Remove** the set `{prev} ∪ {setup_constant}` from the PR. Removing the setup
   constant unconditionally covers the pre-instance setup label, which is added
   without being recorded (see Edge cases). Ignore "label not found" errors.
5. **Ensure-create** `new` (`gh label create … --force`, idempotent — `--add-label`
   errors on a nonexistent label), then **add** `new` to the PR.
6. Set `inst["phase_label"] = new`. The **caller persists** this via the
   `dump_yaml` + `cas_push` it already performs at that seam — no extra push.

`gh` calls use the existing `PUBLISH_TOKEN`/`GH_TOKEN` env already present in the
`plan`/`advance` jobs. Under `ENGINE_LOCAL=1`, the function only logs its intent
(same pattern as `set_check_run`) and still records `phase_label` so tests can
assert on state.

## Call sites (data flow)

All on instance-based seams that already write the cursor and refresh the status
comment:

| Moment | File / location | `head_key` |
|---|---|---|
| Engine picks up work, pre-seed | `next.py` start/reset entry | `setup` |
| Enter first / fan-out phase | `next.py` `seed_and_dispatch_phase`, `start_fanout` | phase id |
| Advance to next phase | `advance.py` GATE-CLEAR branch (`inst["phase"]=nxt`) | `nxt` |
| Pipeline complete | `advance.py` no-further-phase branch | `done` |
| Gate blocked / halt | `advance.py` `halted` branch | `blocked` |
| Fan-out exhausts → failed | `join.py` failed-terminal | `failed` |

The v1 single-agent path (`write_fresh_state` + `run-agent`) gets **no** call, and
`ensure_phase_label` no-ops without an `_instance.yaml` anyway → v1 byte-identical.

## Edge cases

### Setup label (pre-instance)

At `start`/`reset` entry, `_instance.yaml` does not exist yet (and on restart the
old one is about to be wiped). So the `setup` label is added **without** being
recorded in state. To guarantee it is later removed, the reconciler's removal step
(step 4) **always** removes the `setup` constant in addition to the recorded
`prev`. Setup is the only label ever added un-recorded, so this stays precise
without a prefix scan — consistent with the "track in state file" decision.

Implementation note: the setup label is applied by a small dedicated call (not the
full reconciler, which needs an instance file). It adds the resolved `setup` text
and ensure-creates it; it does not record state.

### Restart (`reset_instance=True`)

`next.py`'s reset path already loads the old instance to render the superseded
banner. Read `old_inst.get("phase_label")` there and remove that label before
wiping `_instance.yaml`, so restarting from e.g. `approval gate` does not orphan
that label on the PR.

## Security

- Phase ids and label texts come from trusted `protocol.json`, never from
  agent-produced evidence/feedback. They are passed to any shell via `env:`, never
  interpolated into `run:` blocks (engine security rule).
- No new token surface: reuses the `PUBLISH_TOKEN` already held by the engine-post
  zone.

## Testing (pytest)

- `phase_label_text`: explicit `label`, humanize fallback, terminal defaults,
  per-protocol `phase_labels` override.
- `ensure_phase_label` (`ENGINE_LOCAL`): records `phase_label` in `_instance.yaml`;
  idempotent no-op when unchanged; removes `prev ∪ {setup}`; no-ops without an
  instance file.
- Transition integration: `start` → setup then preflight label; advance →
  review/approval; terminal → done / blocked / failed.
- Regression: v1 `grumpy-review` emits no label calls and its state bytes are
  unchanged.

## Protocol.json changes (data only)

`code-review-pipeline/protocol.json`:
- Add `"label"` to states that want a custom display name (e.g. `preflight` →
  `"pre-flight gate"`, `approval` → `"approval gate"`).
- Optionally add a top-level `"phase_labels"` override map (only if the engine
  defaults aren't wanted).

No changes to `.github/agent-factory/engine/` are required by *protocol authors* —
the label fields are read generically by the engine.
