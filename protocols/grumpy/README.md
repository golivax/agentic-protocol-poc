# `grumpy-review` — the example protocol

Grumpy is a sarcastic senior code reviewer. It is **an example protocol, not the
engine** — it exists to exercise the protocol-agnostic machinery under
`.github/engine/`. To build a different protocol you write a new
`protocols/<name>/`; you do not touch the engine. See `docs/HOW-IT-WORKS.md` for
the engine and trust-zone model.

> **Status:** this README documents the protocol's design including the
> line-anchored inline-review feature being rolled out per the plan referenced at
> the bottom. The "Line anchoring" section below is the durable design rationale;
> the inline-publish mechanics (the `traces-exist-in-diff.py` check and the
> `comments[]` publish path) land with that plan.

## What's in this directory

| File | Role |
|------|------|
| `protocol.json` | states, checks, transitions, `max_iterations` (DATA) |
| `evidence.schema.json` | the rubric the agent must fill — the CONTRACT |
| `checks/schema-valid.sh` | FORM check: evidence parses and has the required shape (incl. the anchor) |
| `checks/rubric-coverage.py` | FORM check: every changed `.js` file × every category has exactly one verdict |
| `checks/traces-exist-in-diff.py` | FORM check: every claim is traceable to the independently-fetched diff (see below) |
| `publish/publish-review-from-evidence.sh` | zone-4 publish hook: turns verified evidence into a native PR review |

The agent (`.github/workflows/grumpy-agent.md`) fills `evidence.json`; the
deterministic checks verify **form**, never substance; the publish hook posts the
review only after the checks pass.

## Evidence discipline

The rubric is **categories × changed files**. For each cell the agent records one
verdict:

- `issues-found` — with ≥1 finding. Each finding quotes the offending code
  **verbatim** (`existing_code`), explains it (`comment`), and **anchors** it to
  the exact diff line(s) it critiques (`side` + `line`, optionally `start_line`).
- `none-found` — with ≥1 `examined` identifier (a function/variable name that
  appears in that file's diff), proving the cell was actually read.

`none-found` everywhere is a legal outcome. The point of the verbatim quotes and
examined identifiers is that a deterministic check can confirm the agent read the
real diff — the checks re-fetch the diff themselves and never trust agent-fetched
data.

## Line anchoring: how findings map to diff lines (design choice)

Grumpy posts **native inline review comments** — one threaded comment pinned to
the line(s) a finding critiques, like a human reviewer — rather than one markdown
blob in the review summary. That requires mapping each finding to a GitHub review
position. There are four ways to do this; grumpy uses option **C**, and the
reasons are worth recording because the obvious choices are traps.

**A. Model emits line numbers; we check the number is a real diff line.**
Rejected. LLMs are unreliable at the one thing this asks — counting lines from a
hunk header. The model says "line 8," means line 6, and 8 is *also* a valid diff
line, so a position-existence check passes and the comment lands on the wrong
code. A valid-but-wrong number is Goodhart-unsafe: there is no content
cross-check to catch it.

**B. Model emits an excerpt; we deterministically find the single match and
compute the line.** Tempting — the model does what it's good at (quote text) and
never counts — but it breaks on **non-uniqueness**. `}`, a `for (...)` header, or
two identical helper functions appear many times; a snippet with 2+ occurrences
has no single line, so you must reject and iterate. Multi-line blocks and
whitespace make the match fiddly. (And this only works for **verbatim** excerpts.
A *paraphrase* would force fuzzy/semantic matching — non-deterministic and
Goodhart-unsafe, the exact thing this protocol is built to avoid.)

**C. Model emits BOTH a verbatim snippet AND the line; the check verifies they
agree.** ← what grumpy does. The two halves cross-check each other's failure
modes:

- the **line disambiguates** — even if the snippet appears three times, the model
  told us which instance;
- the **snippet verifies** — `checks/traces-exist-in-diff.py` parses the diff
  into per-side line maps (`RIGHT` = new-file line numbers, `LEFT` = old-file line
  numbers) and confirms the verbatim `existing_code` sits *exactly* at the claimed
  line(s) on the claimed side. A miscounted line ⇒ content mismatch ⇒ the check
  rejects the evidence and the agent iterates with specific feedback.

Because every anchor that survives the check is a real, content-matched diff
position, the publish hook can post all comments in **one** review POST without
GitHub's all-or-nothing reviews API rejecting the batch (one bad position 422s
the whole review). The check *is* the guarantee; the publish hook trusts it.

**D. Snippet is the source of truth; the line is only a tiebreak.** The most
forgiving of the model's #1 error (miscounting): compute the line from a unique
snippet and ignore the model's number; use the number only to pick between
duplicate occurrences. Not chosen, because it breaks this engine's clean seam —
the check is a pure validator and the *same* evidence flows on to the publish
hook unchanged. D would have to recompute and **write back** the authoritative
line (in the check, or by re-deriving in the hook), and the "pick nearest
occurrence" logic gets awkward with multi-line ranges and `LEFT`/`RIGHT` sides,
which C verifies uniformly. D is the natural upgrade **if** live use shows
miscount-iterations are frequent; until then it is a backlog item, not paid-for
complexity.

### Why C's weakness is small here

C's only real cost is that a miscounted line burns an iteration. That is blunted
by **not making the agent eyeball line numbers**: it runs in a sandbox with shell
access, and a PR's `RIGHT` line number *is* the new-file line number, so the
agent is told to derive anchors mechanically — e.g. `grep -n` against the file at
the PR head (and the base file for `LEFT` side) — instead of counting hunk lines
by hand. Mechanical derivation + the snippet cross-check makes a wrong anchor
rare and, when it happens, self-correcting.

## The anchor, concretely

Each `issues-found` finding carries:

| field | meaning |
|-------|---------|
| `side` | `RIGHT` (added/unchanged line in the new file) or `LEFT` (removed line) |
| `line` | the anchor line number on that side; for a range, the **last** line |
| `start_line` | *(optional)* the **first** line of a multi-line range; same side, same hunk |
| `existing_code` | the verbatim snippet that must sit at those line(s) |
| `comment` | the inline review comment body |

`checks/traces-exist-in-diff.py` enforces: `side ∈ {RIGHT, LEFT}`; `line` (and
`start_line`, if present) are real positions on that side; `start_line < line`,
same hunk, contiguous; and `existing_code` matches the diff content at exactly
those lines. The publish hook then emits `{path, line, side[, start_line,
start_side], body}` per finding and pins the review to the reviewed head SHA.

## Status & references

- Design spec: `docs/superpowers/specs/2026-06-11-grumpy-inline-review-design.md`
  (in the parent project).
- Implementation plan:
  `docs/superpowers/plans/2026-06-11-grumpy-inline-review.md`.
- The engine relays whatever this protocol's publish hook returns — inline
  publishing required **zero** engine change, which is itself a test of the
  engine's protocol-agnosticism.
