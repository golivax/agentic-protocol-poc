# Protocol Distribution — Design

**Date:** 2026-06-22 (rewritten 2026-06-24 against the recursive-engine codebase)
**Status:** Approved (brainstorming)
**Topic:** A simple installer that distributes the agentic-protocol engine + one or more
chosen protocols into any target repo, reusing `gh aw` (unchanged) for the parts it
already does well (per-workflow engine selection, secret setup, compile), and gluing the
rest.

## Goal

Let someone install a protocol into a fresh repo the way `gh aw add-wizard` installs a
workflow: one command, guided setup, working end-to-end.

**Acceptance test:** install **two** protocols on `https://github.com/golivax/throw-away-repo`
and run both:

- `code-review` — `/review` → `preflight → review (fanout) → join → approval` (with its
  check-run + status comment).
- `recover-mental-model-stub` — `/recover` → fanout / sub-pipeline → `/answer`
  data-carrying gate → merge. This one exercises the **recursive** engine and the gate,
  stressing far more of the distributor than the flat code-review path.

## Context: the codebase this targets

As of 2026-06-24 the repo ships **three** protocols and a **recursive** engine:

- `protocols/code-review/`, `protocols/deep-review-stub/`,
  `protocols/recover-mental-model-stub/` — each a `protocol.json` + evidence schemas +
  `checks/` + `publish/`. The two `*-stub` protocols are recursive demos (nested
  fanouts / sub-pipelines, `max_depth`, `/answer` gates).
- Engine: `engine/{advance,join,lib,next,paths,run-checks}.py` — `paths.py` is the
  NODE_PATH coordinate added by the recursive unification.
- Engine workflows: `agentic-orchestrator.yml`, `agentic-engine.yml`,
  `protocol-join.yml`. (`lint.yml` is **this repo's CI**, not engine runtime.)
- 11 agent `.md` (gh-aw sources) + their compiled `.lock.yml`. Each currently
  **hardcodes** an `engine:` block (`id: claude` + a literal `ANTHROPIC_BASE_URL` +
  `ANTHROPIC_AUTH_TOKEN`). This spec removes that hardcoding (see Engine selection).

Agent workflows are referenced by `"workflow":` keys nested at **any depth** in
`protocol.json` (a fanout branch, a sub-pipeline state, a fanout inside a fanout). So the
"which agents does this protocol need" question is answered by **recursively** walking
`protocol.json`.

## Why this isn't just `gh aw add-wizard`

gh-aw installs **workflows** (and, via `imports:`/`resources:`, companion markdown +
custom actions). It cannot carry the **Python engine**, a **protocol directory**, the
**engine-workflow YAMLs**, the **`agentic-state` branch**, or the **`POC_DISPATCH_TOKEN`**
secret. So a thin glue installer is unavoidable. We lean on `gh aw` for the agent
workflows (which benefit from the wizard's per-workflow engine + secret UX) and glue the
rest — **without modifying gh-aw**.

### What gh-aw gives us (confirmed)

- Selective fetch from a source repo via the GitHub REST API, spec `owner/repo/path@ref`
  — the model we mirror for our own fetches.
- `gh aw add` / `add-wizard`: prerequisite check → **per-workflow engine selection** →
  secret setup → compile.
- **What it does NOT do:** configure a custom endpoint (`ANTHROPIC_BASE_URL`). gh-aw has
  no repo-level place for arbitrary engine env — a base URL lives **only** in
  per-workflow `engine.env` frontmatter (it can reference `${{ secrets.X }}`). The
  wizard sets engine + secret, never a base URL. Our installer fills that gap with an
  explicit, opt-in step (see Custom endpoints).

## Chosen approach (Approach A)

A single `dist/install.sh` orchestrator that calls `gh aw` for the agent workflows and
glues the rest. One entry point, "very simple," data-driven so a new protocol costs no
script changes.

Rejected: **B** (gh-aw `aw.yml` package for agents + separate glue) — two entry points,
and `aw.yml` still can't carry the Python engine/protocol; **C** (a real `gh-agentic`
gh extension) — beyond "very simple" for a PoC. Both grafts on later.

## Source-repo additions and changes

New `dist/` directory:

- **`dist/install.sh`** — the installer (subcommands: `install`, `update`, `list`;
  `--dry-run`).
- **`dist/manifest.json`** — *data*: the **common** file set (engine `*.py` glob; the 3
  engine-workflow YAMLs), default `source` (`owner/repo`) + `ref`, the current
  **`engine_version`**, and min `gh-aw` version.
- **`dist/README.md`** — one-liner install instructions.

Changes to existing files:

- **The 11 agent `.md` and their committed locks are left UNTOUCHED.** They keep their
  working default engine (`claude` + the funnel `engine.env`), so this repo stays a
  consistent, running gh-aw deployment. Per-workflow engine selection for *distributed*
  copies is achieved at install time via `gh aw add --engine` override (see Engine
  selection) — not by mutating the source. (Earlier drafts proposed stripping the engine
  blocks; that was dropped once `gh aw add`/`compile` were confirmed to support `--engine`
  override, since stripping would delete the working locks and produce stock-endpoint
  locks this account can't use.)
- **`protocol.json` gains an optional `min_engine_version`** — a DSL addition (see
  Compatibility guard). *Flagged as a protocol-schema change; approved during
  brainstorming.* This is the ONLY change to existing files.

## Install UX

Mirrors gh-aw's "run inside your repo" model — the installer operates on the current
working directory (a clone of the target repo):

```bash
git clone https://github.com/golivax/throw-away-repo && cd throw-away-repo
curl -fsSL https://raw.githubusercontent.com/<this-repo>/main/dist/install.sh \
  | bash -s -- install code-review recover-mental-model-stub
```

- `install <protocol>...` — install one or more named protocols (shared engine +
  engine-workflows fetched once; each protocol's dir + agents looped).
- `list` — discover installable protocols (lists `protocols/` in `source@ref` via
  `gh api`).
- `update [<protocol>...]` — re-sync (see Updating).

Flags: `--ref <tag>` (default `main`), `--source <owner/repo>` (default from manifest),
`--base-url <url>` (default endpoint offered in the custom-endpoint step), `--dry-run`,
`--force` (overwrite locally-modified files on update).

Baked-in decisions: the installer **runs inside a clone of the target repo (cwd)**,
gh-aw-style; files land via **direct commit + push to the default branch** (so triggers
fire immediately). A `--create-pull-request` mode is a later nicety.

## Fetch model — "only what's needed"

Two tiers, both pulled from `source@ref` via `gh api` (REST contents/trees):

- **Common** (from `manifest.json`): `engine/*.py` (glob — robust to additions like
  `paths.py`); the 3 engine-workflow YAMLs. **`lint.yml` is excluded.**
- **Protocol-specific** (derived from each `protocol.json`): the entire
  `protocols/<name>/**` tree, **plus** the agent workflows it names — found by **walking
  `protocol.json` recursively** and collecting every `"workflow"` key at any depth,
  de-duplicated. Only the named protocols' agents are fetched.

`--dry-run` prints the resolved fetch set and exits. **The recursive derivation is the
one logic-heavy, side-effect-free seam — and the primary unit-test target.**

## Agent workflows + per-workflow engine selection

Engine is **per-workflow, chosen at install time** — not unified across a protocol.
(Reversed from an earlier "uniform per protocol" idea: each gh-aw workflow must be able to
pick its own engine, for flexibility.) The source `.md` carry a working *default* engine;
the installer **overrides it per workflow on the target** rather than mutating the source.

1. For each agent the protocol names, the installer prompts for an engine and runs
   `gh aw add <source>/workflows/<agent>.md@<ref> --engine <pick>` — the `--engine` flag
   (confirmed on both `gh aw add` and `gh aw compile`) overrides the source's default on
   the target. gh-aw sets the engine's secret and **dedupes already-set secrets** (a
   second Claude agent won't re-prompt); the prereq check is idempotent.
2. `gh aw compile` produces each `.lock.yml` on the target.

The **set of distinct engines** the user picks across a protocol's agents drives which
secrets get set — handled natively by gh-aw per engine. The source repo's own `.md` and
locks are never touched.

## Custom endpoints — explicit, opt-in, no magic

A custom Claude endpoint (your Claude Code account behind the funnel) is **not** something
gh-aw's wizard configures, and we will not fork gh-aw. So the installer adds a **separate,
announced, skippable step** that runs *after* the wizard:

- **Trigger:** the user opts in ("Configure a custom endpoint for the
  Claude/Codex/… workflows?"). Not automatic on a Claude pick.
- **Engine-agnostic:** maps each engine to its base-URL var (claude →
  `ANTHROPIC_BASE_URL`, codex → `OPENAI_BASE_URL`, …).
- **Transparent:** prompts for the base URL (default `--base-url`), then **shows a
  preview** of the exact `engine.env` it will add (e.g. for Claude:
  `ANTHROPIC_BASE_URL: <url>` + `ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}`,
  since the Claude Code account authenticates by bearer token, not API key) and **which
  files** it touches, asks for confirmation, then writes and runs `gh aw compile`.
- **gh-aw stays unchanged:** we only edit files gh-aw produced + call `compile`. The
  PoC keeps `strict:false`/`sandbox:false`; if the egress firewall is ever restored, this
  step also adds the host to `network.allowed`.

For the acceptance test, the operator opts into this step and points the Claude workflows
at the funnel. The mechanism (writing `engine.env`) is unavoidable given gh-aw's design;
the **visibility + consent** is what distinguishes it from background magic.

## Compatibility guard (engine ↔ protocol)

The recursive engine makes engine/protocol coupling real (a recursive protocol needs a
recursive engine). So:

- `dist/manifest.json` declares the shipped **`engine_version`**.
- Each `protocol.json` declares an optional **`min_engine_version`** (DSL addition).
- **Install** refuses a protocol whose `min_engine_version` > the engine being shipped,
  with a clear message.
- **Update** warns on a breaking engine-version bump.

## Bootstrap + finalize

- **State branch:** create orphan `agentic-state` with an empty initial commit, push,
  switch back. Skip if it already exists (idempotent).
- **`POC_DISPATCH_TOKEN`:** prompt + `gh secret set` (a PAT with repo + workflow scopes —
  user-supplied; cannot be auto-minted). Skip if already set.
- **Finalize:** one commit of engine + protocol(s) + engine-workflows + agent files (+ the
  install receipt) → push to the default branch.

## Updating

Install is **declarative**; update is a **re-sync** driven by an install receipt.

### The install receipt

`install.sh` writes `.github/agent-factory/.install.json` into the target repo, committed
with the files. It records: source repo + installed `ref`; the installed **protocol(s)**;
`engine_version` (from `manifest.json`) and each protocol `version` (from `protocol.json`)
— tracked **separately**; and the full list of installed files with **content hashes**.

### What `update` does

`install.sh update --ref <new>` (or re-running install with a new `--ref` — same code
path) re-fetches at the new ref and, using the receipt:

1. **Diffs file sets** — writes new/changed files; **deletes** files the new version
   removed (orphan cleanup, computed from the receipt's file list).
2. **Detects local drift** — current hash ≠ receipt hash ⇒ locally edited: **warn and
   skip** by default; `--force` overwrites.
3. **Regenerates agent locks** — re-run the custom-endpoint step (if previously applied)
   + `gh aw compile`. Owned by *our* installer, **not** `gh aw update`, which would
   re-fetch the upstream `.md` and lose the endpoint config.
4. **Never touches** the `agentic-state` branch or any state file — runtime data;
   in-flight reviews keep their state.
5. **Commits the delta** (receipt updated) to the default branch.
6. Reports per-unit version moves, e.g. `engine 0.1.0→0.2.0, code-review 0.1.0→0.1.1`,
   and **warns** if the new `engine_version` crosses a protocol's `min_engine_version` or
   is a breaking bump.

### Limitation (explicit)

State-format changes are **not** auto-migrated. If a new engine version changes the
on-disk YAML state schema, an in-flight review on the old format may break. The receipt's
version fields + `min_engine_version` let `update` **detect and warn**, not migrate.

## Error handling

Fail fast, fail **before** mutating:

- Preflight: `gh auth status`, write access, Actions enabled, `gh-aw` installed (else
  offer `gh extension install github/gh-aw`) and ≥ manifest's min version.
- Unknown protocol name → list the available ones (`list`) and exit.
- `min_engine_version` incompatibility → refuse with a clear message, before any write.
- Fetch failures abort before any commit — no partial installs.
- Existing `agentic-state` branch / secrets / files: skipped or `--force`d (never
  force-push the state branch).
- A rejected push is reported with the remote error.

**Security note (carried from the engine design):** the installer handles trusted source
files + user-supplied secrets; still, never `eval` fetched content, and pass values via
env, not string interpolation.

## Components (isolation boundaries)

- `dist/install.sh` — top-level orchestrator: arg parse, subcommands, preflight,
  sequencing.
- `dist/manifest.json` — *data*: common file set, source defaults, `engine_version`, min
  gh-aw.
- **protocol-resolver** (function): recursively read `protocol.json` → derive the
  protocol-specific file set + named agents (the testable seam; exercised by `--dry-run`).
- **fetcher** (function): `gh api` contents/trees → write file to target.
- **agent-installer** (function): `gh aw add`/wizard per agent + `gh aw compile`.
- **endpoint-config** (function): the opt-in custom-endpoint step (preview + consent +
  write + recompile).
- **bootstrap** (function): state branch + secrets.
- **receipt** (function): read/write/diff `.install.json` (hashing, orphan diff, drift,
  version compare).
- **finalize** (function): stage + commit + push.

## Testing

- **Unit:** pytest (or bats) over the **recursive** protocol-resolver / `--dry-run`
  derivation — feed a nested `protocol.json` (deep-review-stub shape), assert the resolved
  fetch set (common + protocol tree + all nested agents, de-duplicated). Also test receipt
  diffing (orphan + drift detection) and `min_engine_version` comparison with synthetic
  inputs.
- **Acceptance (the real bar):** run the installer against `golivax/throw-away-repo` for
  **code-review + recover-mental-model-stub**; confirm code-review runs
  `/review → … → approval` and recover runs `/recover → fanout/sub-pipeline → /answer
  gate → merge`. Then bump a protocol version in the source, run `update`, and confirm a
  clean re-sync (orphan cleanup + drift warning).

## Out of scope (PoC)

- **Non-agentic (plain `.yml`) workflows as part of a protocol.** Architecture
  accommodates it — a third fetch bucket copied **verbatim** into `.github/workflows/`
  (no `gh aw add`/compile), with the protocol declaring `aux_workflows` +
  `required_secrets`/`required_vars` so the installer prompts + `gh secret/variable set`
  generically. **Classification rule:** agentic `.md` (named in `protocol.json`'s
  `workflow:` keys) → gh-aw add + compile; plain `.yml` → verbatim copy. Not built in v1
  (no shipped protocol needs it). Tracked in `docs/BACKLOG.md` → "Non-agentic (plain
  `.yml`) workflows in a distributed protocol".
- Multi-engine validation beyond Claude (the wizard offers the menu; only Claude — via
  the custom endpoint — is validated end-to-end).
- Automatic state-schema migration across engine major versions.
- A standalone `gh` extension / `aw.yml` package distribution (Approaches B/C).
- `--create-pull-request` install mode (direct push only for the PoC).
