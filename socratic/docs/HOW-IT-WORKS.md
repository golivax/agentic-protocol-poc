# The Agentic Protocol Engine — How It Works

A guide to the engine: why it exists, the ideas it's built on, its architecture,
and how a developer writes and runs a protocol.

---

## 1. Motivation

Two existing systems each solve half of "run a reliable, multi-step agentic
process on GitHub":

- **GitHub Agentic Workflows (gh-aw)** compile a markdown file into a sandboxed
  GitHub Actions workflow: the agent runs with read-only credentials, and
  everything it wants to change in the world goes through *safe-outputs* —
  schema-validated, count-limited, executed by a separate job that holds the
  write permissions. This is excellent **spatial** control (what one agent run
  may emit) but each run is a single, stateless agent invocation. A multi-step
  process exists only as natural-language instructions in the prompt that the
  agent may or may not follow.

- **porch** (from codev) is a deterministic protocol engine. Phases,
  transitions, checks, and human gates are declared as *data* (`protocol.json`);
  state lives in a git-committed YAML file; a pure `(state, protocol) → action`
  planner decides what happens next; transitions only occur when shell checks
  pass. This is excellent **temporal** control (when a process may advance) but
  it runs on a developer's laptop with the agent as the driver: the agent calls
  porch as a tool, so porch's determinism still depends on the agent choosing to
  consult it.

**The synthesis:** keep gh-aw's sandbox for each agent step, and put porch's
planner in charge of *when* the process advances — but invert porch's control
model so the **engine drives and the agent is dispatched**. The agent can't
skip the engine, because it only exists when the engine spawns it, can only
affect the world through artifacts the engine's checks inspect, and is gone
before the engine writes state.

The one principle that ties it together:

> **Don't trust prose — demand evidence, and check it deterministically.**
> Each step's contract is an *evidence schema* the agent must fill. Code can't
> verify "the agent did a good job", but it can verify the *structure* of the
> evidence: every rubric cell has a verdict, every claim cites something that
> exists in the diff. That converts the agent's cheapest failure (silently
> skipping work) into its most detectable one (an explicit, falsifiable claim).

---

## 2. Key ideas and assumptions

1. **A workflow run is one transition of a state machine whose state lives in
   git.** State is durable (a YAML file on a branch), so the compute can be
   ephemeral. This is the same shape as AWS Step Functions or Temporal:
   durable state, stateless workers.

2. **Protocol logic is data, not YAML.** `protocol.json` declares states,
   checks, and transitions. One generic engine interprets it. GitHub's workflow
   YAML only decides *when to wake the engine*, never the protocol logic.

3. **The evidence schema is the contract.** The agent must produce a structured
   evidence file covering an enumerable rubric. Prose in the workflow body is
   just guidance on how to satisfy the schema.

4. **Checks verify form; verification (a judge/human) verifies substance.**
   Deterministic code checks coverage, schema, and traceability against
   *independently-derived* ground truth. Whether the agent's *opinion* is
   correct is a separate concern (a second LLM judge, or a human gate) — not a
   check.

5. **State advances only by fast-forward push (compare-and-swap).** The state
   branch is the single source of truth; concurrent writers are resolved by
   git rejecting non-fast-forward pushes. Never force-push it.

6. **Events are wake-ups, not state carriers.** A trigger (slash command,
   re-dispatch, label) only tells the engine to look; everything load-bearing is
   re-derived from the state file. This survives GitHub's event coalescing and
   makes transitions safe to replay.

7. **Trust zones are separated by job and credential.** The engine (which holds
   state-write credentials) never runs agent-influenced code; the agent (which
   runs untrusted model output) never holds state-write credentials. See §3.

**Assumptions:** a publicly-reachable LLM endpoint; one protocol instance per
PR, advanced one PR at a time; a PAT for cross-workflow triggering (the default
`GITHUB_TOKEN` deliberately can't trigger workflows).

> §1–§7 describe the v1 single-agent engine. **§8 adds v2 — fan-out/join:** one
> protocol phase fanning out to several agents in parallel, then a strict
> AND-barrier joining them. The same trust zones, evidence-as-contract, and
> CAS-pushed durable state carry over; the engine grows one env seam (`BRANCH`)
> rather than a second code path.

---

## Execution model: no long-lived driver

There is no long-lived "driver" run that babysits a protocol to completion. Each
workflow run performs **one transition of the state machine and then exits**. The
durable state lives in git (the `agentic-state` branch); every run re-derives what
to do from that state, acts, writes the new state via a fast-forward CAS push, and
terminates. (This is key idea #1 and #6 made concrete.)

Concretely, the way the pieces hand off:

- A trigger — a `/review` comment, a `protocol-continue` / `protocol-join`
  `repository_dispatch` — is just a **wake-up**. The orchestrator → engine
  plan (`next.py`) / dispatch / checks / advance (`advance.py`) runs for *that one
  step*, then finishes.
- When a step needs to cause the next step, it **fires a new `repository_dispatch`
  event** (e.g. `advance.py` dispatches `protocol-continue` carrying
  `client_payload[path]`; `join.py` dispatches the bubbled continue/join). That
  spawns a *fresh* run later — it does not block waiting.
- A fan-out doesn't keep a parent run alive holding the legs together: each leg
  advances independently and fires the join barrier; the join is its own evaluator
  run that exits whether or not all legs are terminal yet ("not all terminal →
  wait" just means *this* run exits and a later leg's fire-join re-wakes it).
- Human gates make this especially clear: at an approval gate or a data `/answer`
  gate, the engine **opens the gate and the run ends**. Nothing is held open for
  minutes/hours/days waiting for the human — the eventual `/approve` or `/answer`
  comment is a new wake-up that starts a new run.

The one bounded wait that *does* exist is **inside a single agent-dispatch step**:
the `dispatch` job runs the gh-aw agent and `gh run watch`es that one agent run to
collect its `evidence.json` (it resolves the run by correlation id — §3.5 — then
waits for it). But that is scoped to one leg's one agent invocation within one
transition — not the whole protocol. The overall pipeline is event-driven and
stateless-between-steps, which is precisely why arbitrary depth works: each
fan-out level is its own engine invocation rather than a nested, long-held parent.

This is the "events are wake-ups, not state carriers; ephemeral compute, durable
state in git" model. The §3.3 lifecycle below shows it for a single iteration.

---

## 3. Architecture

### 3.1 Components

```
.github/agent-factory/protocols/code-review/
  protocol.json          # states, checks, transitions, max_iterations (DATA)
  *.evidence.schema.json # the rubric each agent phase must fill (the CONTRACT)
  checks/*.py            # deterministic transition checks (FORM verification)
  publish/               # per-branch publish hooks (zone 4)

.github/agent-factory/engine/   # GENERIC — fully protocol-agnostic
  lib.py                 # state checkout, CAS push, status-comment upsert,
                         #   resolve_executable, set_check_run (importable module
                         #   + a `python3 lib.py <subcommand>` CLI)
  next.py                # planner: (state, protocol, command) -> action JSON
  advance.py             # sole state writer: verdicts -> mutate, publish, push
  run-checks.py          # resolve + run a state's checks (any language) -> verdicts

.github/workflows/
  agentic-orchestrator.yml  # router: union on:, runtime route job, calls reusable engine
  agentic-engine.yml        # reusable on:workflow_call engine — the 4 trust zones
  preflight-agent.md        # gh-aw agent (preflight phase) -> preflight-agent.lock.yml
  grumpy-agent.md           # gh-aw agent (review/grumpy leg) -> grumpy-agent.lock.yml
  security-agent.md         # gh-aw agent (review/security leg) -> security-agent.lock.yml

agentic-state branch
  <protocol-id>/pr-<N>/<phase>.yaml   # durable per-phase state
                                      # e.g. code-review/pr-<N>/preflight.yaml,
                                      #      code-review/pr-<N>/review.grumpy.yaml
  <protocol-id>/pr-<N>/_instance.yaml # shared per-instance state (cursor, joined flag)
```

> **`code-review` is an example protocol, not the engine.** Everything under
> `.github/agent-factory/engine/` is protocol-agnostic: it reads the protocol id from
> `protocol.json` `.name`, resolves checks and publish hooks from the protocol
> directory, and derives the state path from the protocol id and instance key.
> The `code-review` pipeline — its `protocol.json`, evidence schemas, checks, and
> publish hooks — lives entirely in `.github/agent-factory/protocols/code-review/` + the agent
> `.md` files and exists to exercise the engine. To build a different protocol (an
> incident-response runbook, a release checklist, a compliance review) you write
> a new `.github/agent-factory/protocols/<name>/` and agent workflow; you do **not** touch the engine.
>
> Pure single-phase engine shapes (single-agent iterate loop; single-phase fanout
> without the multi-phase wrapper) are exercised as regression fixtures in
> `tests/fixtures/single-agent/` and `tests/fixtures/fanout-mini/`.

### 3.2 The four trust zones (per iteration)

Each iteration runs as jobs in `orchestrator.yml`, with strictly separated
credentials:

| Zone | Job | Holds | Runs agent code? |
|------|-----|-------|------------------|
| 1. Engine-pre | `plan` | state-branch PAT | no — deterministic `next.py` |
| 2. Agent | `dispatch` → the gh-aw workflow | read-only repo token + LLM creds | yes — sandboxed |
| 3. Checks | `checks` | nothing (read-only default token) | no — Python over evidence + diff |
| 4. Engine-post | `advance` | state PAT + publish token | no — reads check verdicts only |

The invariant: **the engine and the agent never share a job or a credential.**
The agent produces an `evidence.json` artifact; the checks job downloads it and
independently re-fetches the PR diff (it never trusts agent-fetched data); the
advance job reads only the check *verdicts* to decide, and only the evidence (to
*render* the already-decided review).

### 3.3 The transition lifecycle

```
event (pull_request open/push, /review comment, or repository_dispatch "protocol-continue")
   │
   ▼  orchestrator maps event → command: opened/reopened → start,
      synchronize → reset, issue_comment /review → start, dispatch → continue
   │
   ▼
[plan]      checkout agentic-state; next.py <dir> <instance-key> <protocol.json>
            <command> [head_sha] reads/creates <protocol-id>/<instance-key>.yaml,
            emits {action: run-agent|halt, iteration, feedback}
   │ run-agent
   ▼
[dispatch]  mint a correlation id (cid); workflow_dispatch the gh-aw agent with
            aw_context = {pr, iteration, feedback, sabotage, cid}; poll until it
            finishes; resolve its run by cid in the run's displayTitle (§3.5)
   │
   ▼
[checks]    download the agent's evidence artifact; re-fetch `gh pr diff`;
            run each protocol check; emit verdicts {results:[{check,pass,feedback}]}
   │
   ▼
[advance]   append an iteration record to state.history, then:
            • all checks pass → state=done, run protocol publish hook, CAS-push
            • a check failed, iteration<max → bump iteration, CAS-push,
              repository_dispatch "protocol-continue"  (→ next run)
            • iterations exhausted → state=failed, CAS-push
```

The loop terminates in at most `max_iterations` agent runs. `next.py` independently
halts on a terminal state, so a stray re-dispatch can never resurrect a finished
instance.

### 3.4 State model

Each phase gets its own state file under `<protocol-id>/pr-<N>/`. For a
single-agent phase (like `preflight` in `code-review`) the shape is
(porch-compatible field names):

```yaml
protocol: code-review
instance: pr-9
state: done            # <phase-id> | done | failed
iteration: 2           # 1-based, bounded by max_iterations
gates: {}              # used by kind:"gate" phases for human decision history
history:               # one record per iteration — the audit trail
  - iteration: 1
    agent_run_id: "…"
    checks: { schema-valid: pass, rubric-coverage: fail, traces-exist-in-diff: pass }
    feedback: "Missing or duplicated rubric cells: security × src/auth.js; …"
  - iteration: 2
    agent_run_id: "…"
    checks: { schema-valid: pass, rubric-coverage: pass, traces-exist-in-diff: pass }
    feedback: ""
status_comment_id: 4673907543   # the single PR comment the engine re-renders
```

Every transition is a commit to the file on the `agentic-state` branch, so
`git log agentic-state -- code-review/pr-<N>/preflight.yaml` is a complete,
auditable history.

> **Single-phase shape for the engine regression fixture:** `tests/fixtures/single-agent/`
> exercises the pure single-agent iterate loop with a minimal `protocol.json`;
> its state lives at `single-agent/pr-<N>.yaml` (no sub-directory, matching the
> `<protocol-id>/<instance-key>.yaml` formula when there is no multi-phase wrapping).

### 3.5 Resolving the agent's run — the correlation id

When the `dispatch` job launches the gh-aw agent it must later find *the exact
run it started* to read that run's evidence artifact. The naive resolver —
"newest `workflow_dispatch` run of this workflow since T0" — is correct only one
PR at a time: the gh-aw agent workflow uses a *global* concurrency group, so two
PRs reviewed concurrently by the same workflow could misattribute each other's
runs.

The fix is a **correlation id (cid)**:

- `dispatch` mints a unique `cid = <orchestrator_run_id>-<run_attempt>-<branch>`
  and threads it to the agent in the `aw_context` JSON.
- Each agent `.md` sets its `run-name` to embed the delimited token
  `cid:[<cid>]`; `gh aw compile` bakes this into the lock, so the cid lands in
  the run's **displayTitle**.
- The resolver (`match_run_by_cid` in `lib.py`) selects the run whose
  displayTitle carries that exact `cid:[<cid>]` token, and **fails loudly** if no
  run matches — it never falls back to a recency heuristic. So **concurrent PRs
  of the same workflow** each resolve only their own run.

The bracket delimiters stop a prefix cid (e.g. `42-1-grumpy`) from matching a
longer one. Throughput caveat (not correctness): the agent workflow's
concurrency group serializes two PRs running the same agent rather than running
them in parallel.

---

## 4. Developer guide

> This section is the **tutorial** — how to design a protocol. For a terse
> field-by-field reference of every `protocol.json` key by node kind (plus the
> machine-readable JSON Schema you can wire into your editor), see
> [`PROTOCOL-DSL.md`](PROTOCOL-DSL.md). [`AUTHORING.md`](AUTHORING.md) is the hub
> that gathers this tutorial, the reference, and the `protocol-lint.py`
> validate-and-visualize tool in one place.

### 4.1 Anatomy of a protocol (`protocol.json`)

Below is the shape of a **single-agent** protocol state — the simplest form,
used by the `tests/fixtures/single-agent/` engine regression fixture:

```jsonc
{
  "name": "single-agent",   // fixture — not a shipped protocol
  "states": [
    { "id": "review",
      "kind": "agent",                 // an LLM step; dispatched as a gh-aw workflow
      "workflow": "grumpy-agent",      // which gh-aw workflow to dispatch
      "evidence": "evidence.schema.json",
      "max_iterations": 3,
      "params": { "categories": ["naming", "error-handling", "performance", "duplication", "security"] },
                                       // node-scoped config; forwarded to checks as CHECK_PARAMS
      "checks": [                      // run in order between this state and `next`
        { "run": "schema-valid",        "on_fail": "iterate" },
        { "run": "rubric-coverage",     "on_fail": "iterate" },
        { "run": "traces-exist-in-diff","on_fail": "iterate" }
      ],
      "next": null }                   // terminal (publish handled by separate hook)
  ]
}
```

The shipped `code-review` protocol is multi-phase and uses `kind:"fanout"` and
`kind:"gate"` in addition to `kind:"agent"` — see
`.github/agent-factory/protocols/code-review/protocol.json` for the full declaration.

Designing a protocol state = **choosing the enumerable rubric** (here, 5
categories × changed files) and the checks that verify the evidence is complete
and traceable. The art is finding the decomposition of a judgment task that
makes it gateable.

### 4.2 The evidence schema (the contract)

`evidence.schema.json` is a JSON Schema describing what the agent must emit to
`/tmp/gh-aw/evidence.json`. The key idea is **negative attestation with a
trace**: "I found nothing" is a legal verdict, but it must carry the identifiers
the agent examined, so the check can confirm the agent actually read the code.

```json
{ "files": [
  { "path": "src/util.js", "verdicts": [
    { "category": "naming", "verdict": "none-found",
      "examined": ["clamp", "value", "min", "max"] },      // trace for a negative
    { "category": "error-handling", "verdict": "issues-found",
      "findings": [ { "existing_code": "if (min > max) {…}", // verbatim from the diff
                      "comment": "NaN slips through this guard…" } ] } ] } ] }
```

### 4.3 Writing a deterministic check

**The check contract is a language-agnostic CLI ABI**, not a bash convention:

> An executable invoked as `<check> <evidence.json> <diff.txt> <changed-files.txt>`
> that prints one JSON object `{"check","pass","feedback"}` to stdout and exits 0.

Any language that can read three file paths and print a line of JSON qualifies.
The engine's `run-checks.py` reads the check *list* from `protocol.json`
(`.states[].checks[]`) and resolves each entry to an executable:

- if the entry sets `"exec": "<path>"`, it runs `<protocol-dir>/<path>`;
- otherwise it finds `<protocol-dir>/checks/<run>` or `checks/<run>.*`
  (extension-agnostic — so `checks/rubric-coverage.py` and a `checks/foo.sh`
  bash check would both be first-class).

So a Python check is just `checks/<name>.py` with `#!/usr/bin/env python3` and
`chmod +x` — **no bash wrapper.** A wrapper is only worth writing when you're
*adapting an existing tool* whose output isn't the verdict JSON (e.g. a
`checks/tests-adapter.py` that runs `pytest` and translates its exit code into
`{check,pass,feedback}`) — that adapter does real work, and it's exactly the
"a check that runs agent-authored code in the credential-free zone-3 job" case.
`run-checks.py` turns a missing, non-executable, crashing, or malformed check
into a *failing verdict* rather than aborting the run.

In this example protocol all three checks (`schema-valid`, `rubric-coverage`,
`traces-exist-in-diff`) are implemented in **Python** — but that's incidental:
the ABI is language-agnostic, so a check can be written in any language that
honours the contract above, and all three obey it identically.

Whatever the language: **always exit 0** even when the check fails (a non-zero
exit is reserved for a genuine runner error), and read node-scoped config (e.g.
the rubric `categories`) from the `CHECK_PARAMS` env var rather than
hardcoding it or reaching into `protocol.json`.

Two rules that make checks trustworthy:

1. **Derive ground truth independently.** `rubric-coverage` and
   `traces-exist-in-diff` re-run `gh pr diff` themselves; they never trust a
   diff the agent produced. A prompt-injected agent can't fake coverage by
   lying about what changed.
2. **Verify form, never substance.** Check that every cell has a verdict
   (coverage), that the schema holds, that every `existing_code`/`examined`
   value appears in the real diff (traceability). Do **not** try to check
   whether a finding is "correct" — that's a job for a second LLM judge or a
   human gate (a future state), not a deterministic check.

When a check fails, its `feedback` string is what gets injected into the next
iteration's prompt, so make it specific and actionable ("Missing: security ×
src/auth.js"), not "evidence invalid".

The three shipped checks, in full. They are deliberately small (~30–60 lines of
Python each) and share the contract above.

#### `schema-valid.py` — does the evidence have the right shape?

It guards three layers (parseable JSON → `.files` is a list → every entry is
an object with a `verdicts` list) so a malformed file produces a verdict
instead of crashing the main pass. It then walks every verdict and collects
*all* shape violations (not just the first) before joining them into the
feedback. The legal categories come from `CHECK_PARAMS` (the check-owning node's
`params` object, forwarded by `run-checks.py`), never hardcoded — and because
`CHECK_PARAMS` may arrive as the literal string `"null"` (JSON null), the check
treats that as "no categories" and still exits 0 rather than throwing.

```python
#!/usr/bin/env python3
import json, os, sys

def emit(ok, feedback):
    print(json.dumps({"check": "schema-valid", "pass": ok, "feedback": feedback}))
    sys.exit(0)

def main():
    ev_path = sys.argv[1]
    try:
        with open(ev_path) as f: ev = json.load(f)
    except Exception:
        emit(False, "evidence file is missing or not valid JSON")
    if not isinstance(ev.get("files"), list):
        emit(False, "top-level .files array is missing")
    for fe in ev["files"]:
        if not (isinstance(fe, dict) and isinstance(fe.get("verdicts"), list)):
            emit(False, "a .files entry is not an object with a verdicts array; "
                        "check that every file is an object and verdicts is an array")
    try:
        params = json.loads(os.environ.get("CHECK_PARAMS", "") or "{}")
    except Exception:
        params = {}
    cats = (params or {}).get("categories")  # CHECK_PARAMS="null" -> json None; stay exit-0
    if not (isinstance(cats, list) and len(cats) > 0):
        emit(False, "schema-valid: no categories in CHECK_PARAMS "
                    "(engine must pass params.categories for this check's node)")
    errs = []
    for fe in ev["files"]:
        p = fe.get("path")
        for v in fe.get("verdicts", []):
            c = v.get("category"); verdict = v.get("verdict")
            findings = v.get("findings") or []
            if c not in cats:
                errs.append(f"illegal category {c} in {p}")
            elif verdict not in ("issues-found", "none-found"):
                errs.append(f"illegal verdict {verdict} for {c} × {p}")
            elif verdict == "issues-found" and len(findings) == 0:
                errs.append(f"issues-found with no findings: {c} × {p}")
            elif verdict == "issues-found" and not all(
                    len(fd.get("existing_code") or "") > 0 and len(fd.get("comment") or "") > 0
                    for fd in findings):
                errs.append(f"finding with empty existing_code or comment: {c} × {p}")
            elif verdict == "issues-found" and not all(
                    fd.get("side") in ("RIGHT", "LEFT")
                    and isinstance(fd.get("line"), int) and not isinstance(fd.get("line"), bool) and fd.get("line") >= 1
                    and (fd.get("start_line") is None
                         or (isinstance(fd.get("start_line"), int) and not isinstance(fd.get("start_line"), bool) and fd.get("start_line") >= 1))
                    for fd in findings):
                errs.append(f"finding missing valid line/side anchor: {c} × {p}")
            elif verdict == "none-found" and len(v.get("examined") or []) == 0:
                errs.append(f"none-found with no examined identifiers: {c} × {p}")
    emit(len(errs) == 0, "; ".join(errs))

if __name__ == "__main__":
    main()
```

#### `rubric-coverage.py` — is every cell of the rubric filled exactly once?

*(Like the others, this one is **Python** — the ABI is language-agnostic, and
`run-checks.py` resolves and runs it exactly the same way regardless of
language.)* Ground truth is the
changed-files list (the orchestrator fetches it with `gh pr diff --name-only`),
filtered to `.js`. For every file × every category it counts verdicts and
demands **exactly one** — so `0` (the agent skipped it, e.g. under sabotage)
*and* `2+` (padding/duplication) both fail. Malformed evidence is treated as
"no verdicts" so this check reports missing cells rather than crashing
(`schema-valid` reports the bad shape).

```python
#!/usr/bin/env python3
# Check: every reviewable changed file × every category has exactly one verdict.
# Usage: rubric-coverage.py <evidence.json> <diff.txt> <changed-files.txt>
import json, os, sys

def main() -> None:
    ev_path, _diff, files_path = sys.argv[1], sys.argv[2], sys.argv[3]
    # Categories come from CHECK_PARAMS (node-scoped params forwarded by run-checks.py).
    try:
        categories = json.loads(os.environ.get("CHECK_PARAMS", "")).get("categories")
    except (ValueError, AttributeError):
        categories = None
    if not isinstance(categories, list) or not categories:
        print(json.dumps({
            "check": "rubric-coverage",
            "pass": False,
            "feedback": "rubric-coverage: no categories in CHECK_PARAMS "
                        "(engine must pass params.categories for this check's node)",
        }))
        return

    try:
        with open(ev_path) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    files = evidence.get("files", []) if isinstance(evidence, dict) else []

    counts: dict[tuple, int] = {}
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        verdicts = entry.get("verdicts") or []
        if not isinstance(verdicts, list):
            continue
        for verdict in verdicts:
            if isinstance(verdict, dict):
                key = (path, verdict.get("category"))
                counts[key] = counts.get(key, 0) + 1

    with open(files_path) as fh:
        changed = [line.rstrip("\r\n") for line in fh]

    bad = []
    for path in changed:
        if not path.endswith(".js"):
            continue
        for category in categories:
            n = counts.get((path, category), 0)
            if n != 1:
                bad.append(f"{category} × {path} (verdicts: {n})")

    if bad:
        out = {"check": "rubric-coverage", "pass": False,
               "feedback": "Missing or duplicated rubric cells: " + "; ".join(bad)}
    else:
        out = {"check": "rubric-coverage", "pass": True, "feedback": ""}
    print(json.dumps(out, ensure_ascii=False))

if __name__ == "__main__":
    main()
```

#### `traces-exist-in-diff.py` — does every claim point at the exact line it names?

This is what makes "I found nothing" cost more than a sentence, and what makes
inline review comments reliable. The check parses the diff into per-side line
maps — `RIGHT` for new-file line numbers, `LEFT` for old-file line numbers —
and verifies each claim:

- **findings** must carry a `side` (`RIGHT`/`LEFT`) and a `line`; an optional
  `start_line` marks a multi-line range, which must be contiguous within one
  hunk and satisfy `start_line < line`. The check confirms the verbatim
  `existing_code` matches the diff content at exactly those lines after
  whitespace-normalisation (carried over from the old check).
- **`none-found` identifiers** must appear in the concatenated diff content for
  that file (whitespace-normalised matching is not applied here; simple
  substring suffices).

Because every anchor that passes is a real, content-matched diff position, the
publish hook can post all inline comments in one all-or-nothing review POST
without a 422 from the GitHub reviews API. The check *is* the guarantee; the
publish hook trusts it.

For the full design rationale and the tradeoffs among the four possible
anchoring strategies, see the `traces-exist-in-diff` check itself
(`.github/agent-factory/protocols/code-review/checks/traces-exist-in-diff.py`) — it
is the executable specification of the anchoring contract.

```python
#!/usr/bin/env python3
"""Check: every finding's anchor (line[/start_line] on a side) resolves to the
claimed snippet in the independently-fetched diff, and every `examined`
identifier appears in that file's diff hunks.

Usage: traces-exist-in-diff.py <evidence.json> <diff.txt> <changed-files.txt>

This replaces the former "snippet appears somewhere in the diff" check: a finding
must now name the exact line(s) it critiques (RIGHT = new-file line numbers,
LEFT = old-file line numbers), and we verify the snippet sits there. Anchors that
pass here are valid GitHub review positions, so the publish hook can post them in
a single review without the all-or-nothing reviews API 422-ing.
"""
import json
import re
import sys

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def norm(s: str) -> str:
    """Collapse all runs of whitespace to single spaces (matches the old check)."""
    return " ".join(s.split())


def parse_diff(path):
    """Return {file: {"RIGHT": {lineno: (content, hunk_id)}, "LEFT": {...}}}.

    Context lines populate both sides; '+' only RIGHT; '-' only LEFT. Each mapped
    line records the id of the hunk it belongs to (for same-hunk range checks).
    """
    maps = {}
    cur = None
    minus_path = None
    in_hunk = False
    right_no = left_no = 0
    hunk_id = -1
    with open(path) as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if line.startswith("diff --git"):
                cur, in_hunk = None, False
                minus_path = None
                continue
            if line.startswith("--- "):
                minus = line[4:]
                if minus == "/dev/null":
                    minus_path = None
                elif minus.startswith("a/"):
                    minus_path = minus[2:]
                else:
                    minus_path = minus
                in_hunk = False
                continue
            if line.startswith("+++ "):
                plus = line[4:]
                if plus == "/dev/null":
                    cur = minus_path  # deleted file: key it under its old path
                elif plus.startswith("b/"):
                    cur = plus[2:]
                else:
                    cur = plus
                if cur is not None:
                    maps.setdefault(cur, {"RIGHT": {}, "LEFT": {}})
                in_hunk = False
                continue
            m = HUNK_RE.match(line)
            if m:
                left_no, right_no = int(m.group(1)), int(m.group(2))
                hunk_id += 1
                in_hunk = True
                continue
            if not in_hunk or cur is None or line == "":
                continue
            tag, content = line[0], line[1:]
            if tag == " ":
                maps[cur]["LEFT"][left_no] = (content, hunk_id)
                maps[cur]["RIGHT"][right_no] = (content, hunk_id)
                left_no += 1
                right_no += 1
            elif tag == "+":
                maps[cur]["RIGHT"][right_no] = (content, hunk_id)
                right_no += 1
            elif tag == "-":
                maps[cur]["LEFT"][left_no] = (content, hunk_id)
                left_no += 1
            # "\ No newline at end of file" and any other marker: ignore
    return maps


def verify_finding(f, fmap, path, cat):
    """Return an error string if the finding's anchor is invalid, else None."""
    if not isinstance(f, dict):
        return f"malformed finding ({cat} × {path})"
    side = f.get("side")
    if side not in ("RIGHT", "LEFT"):
        return f"finding side must be RIGHT or LEFT ({cat} × {path}): {side!r}"
    smap = fmap.get(side, {})
    line = f.get("line")
    start = f.get("start_line")
    if not isinstance(line, int) or line not in smap:
        return f"line {line} not on {side} side of {path}'s diff ({cat})"
    if start is not None:
        if not isinstance(start, int) or start not in smap:
            return f"start_line {start} not on {side} side of {path}'s diff ({cat})"
        if start >= line:
            return f"start_line {start} must be < line {line} ({cat} × {path})"
        hunk = smap[line][1]
        for n in range(start, line + 1):
            if n not in smap or smap[n][1] != hunk:
                return (f"lines {start}-{line} are not one contiguous hunk on "
                        f"{side} ({cat} × {path})")
        lines = [smap[n][0] for n in range(start, line + 1)]
    else:
        lines = [smap[line][0]]
    got = norm("\n".join(lines))
    want = norm(f.get("existing_code") or "")
    if got != want:
        anchor = f"{start}-{line}" if start is not None else f"{line}"
        return (f"existing_code does not match {side} line(s) {anchor} of "
                f"{path} ({cat})")
    return None


def main():
    if len(sys.argv) < 4:
        print(json.dumps({
            "check": "traces-exist-in-diff",
            "pass": False,
            "feedback": "usage: traces-exist-in-diff.py <evidence.json> <diff.txt> <changed-files.txt>",
        }))
        sys.exit(0)
    # _files (changed-files.txt) is unused: the diff is the source of truth here.
    ev_path, diff_path, _files = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        with open(ev_path) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    maps = parse_diff(diff_path)

    bad = []
    files = evidence.get("files", []) if isinstance(evidence, dict) else []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        fmap = maps.get(path, {"RIGHT": {}, "LEFT": {}})
        blob = "\n".join(
            c for (c, _h) in list(fmap["RIGHT"].values()) + list(fmap["LEFT"].values())
        )
        for verdict in (entry.get("verdicts") or []):
            if not isinstance(verdict, dict):
                continue
            cat = verdict.get("category")
            for f in (verdict.get("findings") or []):
                err = verify_finding(f, fmap, path, cat)
                if err:
                    bad.append(err)
            for ident in (verdict.get("examined") or []):
                if ident not in blob:
                    bad.append(
                        f"examined identifier not in {path}'s diff ({cat}): {ident!r}"
                    )

    if bad:
        out = {
            "check": "traces-exist-in-diff",
            "pass": False,
            "feedback": "Unverifiable claims: " + "; ".join(bad),
        }
    else:
        out = {"check": "traces-exist-in-diff", "pass": True, "feedback": ""}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

Together these three implement the principle from §2: `schema-valid` and
`rubric-coverage` enforce that the agent *addressed every cell*, and
`traces-exist-in-diff` enforces that each cell's claim — positive or negative —
is *anchored in code that actually changed*, at the specific line(s) the agent
named. None of them judge whether a finding is correct; that's substance,
reserved for a future judge/gate.

### 4.4 The agent workflows (`*-agent.md`)

The `code-review` protocol uses three agent workflows:

- **`preflight-agent.md`** — reviews the PR for spec/plan adherence in the
  `preflight` phase.
- **`grumpy-agent.md`** — the general code reviewer; dispatched as the `grumpy`
  leg of the `review` fanout phase.
- **`security-agent.md`** — the security reviewer; dispatched as the `security`
  leg of the `review` fanout phase.

Each is a normal gh-aw markdown file with two protocol-specific responsibilities:

- **Frontmatter** declares the engine, read-only permissions, the LLM endpoint
  (`engine.env`), a `pre-agent-steps` step that materializes the dispatched
  `aw_context` JSON to `/tmp/gh-aw/task-context.json`, and a `post-steps` step
  that uploads `/tmp/gh-aw/evidence.json` as an artifact named `evidence`.
- **Body** is the prompt: the persona + the mission ("for every changed file ×
  every category, record exactly one verdict in evidence.json; copy
  `existing_code` verbatim; cite `examined` identifiers; do NOT fabricate
  findings; your only output is evidence.json — the engine publishes for you").
  The iteration's `feedback` is injected so the agent fixes exactly what the
  previous round's checks rejected.

Compile with `gh aw compile` and commit the generated `*.lock.yml`
(workflows run from the committed lock).

> Custom LLM endpoint note: configure it under `engine.env`
> (`ANTHROPIC_BASE_URL` literal + `ANTHROPIC_AUTH_TOKEN` from a secret), which
> gh-aw forwards to the CLI subprocess. See `STATUS.md` for why the egress
> firewall is currently disabled for a custom endpoint.

### 4.5 The orchestrator (`orchestrator.yml`)

Mostly protocol-agnostic plumbing you won't edit per-protocol: the four jobs,
the per-PR `concurrency` group, and the credential wiring. It calls the engine
scripts and the checks; the *protocol* decides everything else.

A safety detail worth knowing: agent-derived strings (`feedback`, `verdicts`)
are passed to shell steps via `env:`, never interpolated into `run:` blocks —
otherwise a crafted filename or finding could inject shell commands into the
job that holds the state PAT.

### 4.6 The command seam — trigger policy lives in the orchestrator, not the engine

The engine (`next.py`) receives a **command** and never sniffs GitHub events.
The orchestrator translates events to commands:

| GitHub event | Condition | Command |
|---|---|---|
| `pull_request` opened / reopened | — | `start` |
| `pull_request` synchronize | new commit pushed | `reset` |
| `issue_comment` created | body starts with `/review` | `start` |
| `repository_dispatch` `protocol-continue` | iterate re-entry | `continue` |

The command determines the engine's behaviour based on the instance lifecycle
state (Absent / Active / Terminal):

| Command | Absent | Active | Terminal |
|---|---|---|---|
| `start` | fresh review | **halt** (review in flight) | fresh re-review |
| `reset` | fresh review | fresh review | fresh review |
| `continue` | fresh review | resume current iteration | halt |

Two intentional v1 divergences from the original design:
- **`start` on Terminal → fresh re-review** (the prior design halted; now
  posting `/grumpy` on a finished PR restarts the review, e.g. after a
  rewrite).
- **`start` on Active → halt** (the prior design resumed; now a duplicate
  trigger on an in-flight review is silently ignored to avoid double-advancing).

`head_sha` (the fifth argument to `next.py`) is **recorded as metadata** only
(stored in the state file for check-run binding). The engine never compares it
to decide policy — that comparison was removed. The orchestrator is where the
policy lives: `synchronize` → `reset` is the mechanism that ensures a new push
invalidates the old review.

### 4.7 The publish hook

When all checks pass, `advance.py` calls the protocol's **publish hook** —
a protocol-provided executable resolved via the same `resolve_executable`
function used for checks (see §4.3), from the publish state's `.action` field:

```jsonc
{ "id": "review",        // an agent state with a publish hook
  "kind": "fanout",
  "branches": [ ... ],
  // each branch carries its own "publish": "<hook-name>" resolved from the protocol's publish/ dir
}
```

**ABI:** the hook is invoked as `<hook> <evidence.json> <instance-key>` with
env vars `ENGINE_LOCAL`, `GITHUB_REPOSITORY`, `PUBLISH_TOKEN`, and `PR`. It
prints one JSON object `{"conclusion","summary"}` on stdout; the engine relays
those to the check run.

**Trust zone distinction — this is NOT a sandboxed check:**

| | Zone | Credential | Sandboxed? |
|---|---|---|---|
| Checks | 3 | nothing (read-only default token) | yes — zero credential |
| Publish hook | 4 (engine-post) | publish token (PR reviews + check runs) | no — trusted, repo-authored |

The publish hook runs **trusted in zone 4** alongside `advance.py`, holding
the publish token. It has the authority to post PR reviews and check runs.
A check (zone 3) has no credentials and is explicitly designed to run code
that touches agent-influenced data without being able to affect external
systems. Do not conflate them: "resolved via `resolve_executable`" describes
the *mechanical* resolution; the trust boundary is entirely different.

For `code-review`, publish hooks live in
`.github/agent-factory/protocols/code-review/publish/` (one per branch: `publish-grumpy`,
`publish-security`). Each reads the evidence, posts a REQUEST_CHANGES or APPROVE
review (COMMENT fallback if the repo setting is off), and returns
`{"conclusion":"failure"|"success","summary":"…"}`.

---

## 5. Using a protocol through GitHub (developer's-eye view)

1. **Open a PR** as usual. The orchestrator triggers automatically on
   `pull_request` `opened`/`synchronize`/`reopened`, so the review runs on open
   **and re-runs on every push** (a new commit resets the instance to a fresh
   review of the new head — see §5.1). `/review` remains a manual re-trigger.
2. *(optional)* **Comment `/review`** to re-run on demand.
3. **Watch it work.** On the happy path the pipeline advances through preflight →
   review fan-out → join → approval gate — same UX as plain gh-aw once you
   approve. The protocol machinery only becomes visible when it has something to say:
   - The engine maintains **one status comment**, re-rendered each transition
     into a checklist:
     ```
     🔍 code-review · pr-9
     - ✗ iteration 1/3 — Missing: security × src/auth.js; duplication × src/report.js
     - ✅ iteration 2/3 — all checks passed
     ✅ done — review published.
     [Full state & audit trail](…/blob/agentic-state/code-review/pr-9/preflight.yaml)
     ```
   - The final **review** (REQUEST_CHANGES / APPROVE) from each leg and the
     human approval gate are the deliverables.
4. **If checks fail**, you don't see half-baked output — the agent silently
   iterates (a second run), and only checked output is ever published. After
   `max_iterations`, the engine posts a clear failure instead of going quiet.
5. **Inspect the record** any time: the status-comment link, the `agentic-state`
   branch (`git log agentic-state -- code-review/pr-<N>/`), or the Actions tab
   (one orchestrator run + one agent run per iteration).

The mental-model shift from plain gh-aw: **the PR/issue is the unit of
existence, and workflow runs are heartbeats that advance it** — not the other
way round. A protocol can sit waiting (a future human gate) for weeks at zero
cost, because "waiting" is just a line in a committed file.

### 5.1 Blocking the merge on the review

By default a review verdict is *advisory* — GitHub won't stop a merge just
because a review requested changes. To make the protocol a real merge gate, it
publishes a **check run** named after the protocol id (for `code-review`: `code-review`)
on the PR's head commit, reflecting protocol state:

| protocol state | check run | merge box |
|---|---|---|
| reviewing / iterating | `in_progress` | pending — blocks |
| changes requested (issues found) | `completed` / `failure` | ❌ blocks |
| clean | `completed` / `success` | ✅ |
| failed after max iterations | `completed` / `failure` | ❌ blocks |

The check run binds to the head SHA. A push mid-review invalidates the old
verdict: the orchestrator maps `pull_request synchronize` → command `reset`,
which tells `next.py` to **unconditionally start a fresh review** of the new
commit (the prior review stays in the state branch's git history). The engine
does not compare head SHAs itself — trigger policy lives in the orchestrator
(see §4.6). So the gate can never go green on un-reviewed code. The check is
emitted with the Actions `GITHUB_TOKEN` (the Checks API is App/Actions-token
only — a PAT can't create check runs), via `set_check_run` in `lib.py`, from the
`plan` job (initial `in_progress`) and `advance.py` (terminal/iterate states).
The relevant jobs carry `checks: write`.

> **Fork PRs are out of scope.** `pull_request` runs from forks get no secrets
> and a read-only token, so the orchestrator (which needs the state-branch PAT)
> can't run — and GitHub gates them behind first-time-contributor approval
> anyway. This PoC targets same-repo PRs. Supporting forks safely would need
> `pull_request_target` with careful sandboxing (the classic "pwn-request"
> surface), which is deliberately not attempted here.

**Emitting the check is not the same as enforcing it.** The check appears in the
merge box on any repo, but it only *blocks* merge once you make it a **required
status check** in branch protection / rulesets — which needs a public repo or a
paid plan for private repos. Configure it once the check has reported at least
once (so GitHub knows the `code-review` name):

- **Ruleset** (recommended): *Settings → Rules → Rulesets → New branch ruleset*,
  target the default branch, enable *Require status checks before merging*, add
  `code-review` (source: GitHub Actions).
- **Classic**: *Settings → Branches → Add rule*, pattern `main`, *Require status
  checks to pass before merging*, search and select `code-review`.

Optionally layer *Require approvals* on top for a human sign-off in addition to
the automated gate. (Caveat: the bot can post `action_required`/REQUEST_CHANGES
to block, but can't `APPROVE` to unblock unless the repo's "Allow GitHub Actions
to approve pull requests" setting is on — see `STATUS.md`.)

---

## 6. Operational setup

Secrets on the repo:

- `ANTHROPIC_API_KEY` — the LLM auth token (set with `gh secret set NAME
  --body "$VALUE"`; **not** `--body -`, which stores the literal `-`).
- `ANTHROPIC_BASE_URL` — the endpoint (also a literal in the agent frontmatter).
- `POC_DISPATCH_TOKEN` — a PAT (repo + workflow scopes) used for the
  state-branch push, the `workflow_dispatch` of the agent, the
  `repository_dispatch` re-entry, and the PR-label read (PR labels need the
  `pull-requests` scope, which the default `GITHUB_TOKEN` lacks).

Publication of the PR review uses the default `GITHUB_TOKEN` (the bot), because
GitHub forbids a PR author from reviewing their own PR and the PAT is the author.
A fully-clean result falls back from APPROVE to COMMENT unless the repo's
"Allow GitHub Actions to approve pull requests" setting is enabled.

Keep `orchestrator.yml` and the agent lock on the **default branch** — that's
where workflows run from for `issue_comment` / `repository_dispatch` events.

---

## 7. Design principles to carry forward

- **Evidence over prose.** A state's contract is its evidence schema; the prose
  only explains how to satisfy it.
- **Omission → commission.** Force a verdict for every rubric cell so the
  agent's cheapest failure (skipping) becomes a detectable explicit claim.
- **Coverage, not yield.** Demand a verdict for every cell, where "nothing
  found" is legal — so the agent is never pressured to fabricate findings
  (Goodhart-safe).
- **Independent ground truth.** Checks re-derive what they verify against;
  never trust agent-produced data.
- **Form vs. substance.** Code checks form; judges/humans check substance.
- **Graduated failure rungs.** repair < drop < iterate < gate — reach for the
  cheapest remedy that fits (v1 implements only `iterate`).

See `STATUS.md` for what is and isn't implemented, and the spec/plan under
`agent-factory/docs/superpowers/` for the full design history.

---

## 8. Fan-out / join (multi-agent review)

The `code-review` protocol's `review` phase fans out to **several** agents
running in parallel, each with its own iterate loop, then **joins** them under a
strict barrier before the process may advance. Specifically, the `review` phase
fans out to two agents — `grumpy` (the general reviewer) and a thin `security`
stub — and joins on "both finished," then hands off to the `approval` gate.

The design goal is that **the single-agent path stays byte-identical**: the
engine grows one environment variable, not a second code path.

### 8.1 The `BRANCH` env seam

`next.py`, `run-checks.py`, and `advance.py` all read one new env var,
`BRANCH` (and `lib.py` provides the branch-aware `state_file`/`instance_file`
helpers they pass it to):

- **`BRANCH` empty/unset** → the single-agent path (exercised by the
  `tests/fixtures/single-agent/` engine regression fixture — passes unchanged).
- **`BRANCH=<id>` set** → the same scripts operate on **one fan-out branch**:
  its agent unit, its check list, its publish hook, and its own state file.

A "branch" here is a *parallel agent leg* of a fan-out phase (the `grumpy` leg,
the `security` leg) — unrelated to a git branch. There is no fork in the engine;
each script simply reads one extra variable and selects the per-branch unit.

### 8.2 The `fanout` and `join` state kinds

The `code-review` protocol (`.github/agent-factory/protocols/code-review/protocol.json`)
introduces two state kinds alongside `agent` and `gate`. The chosen design
(**Approach C — data-driven**) is that each branch **reuses the single-agent
iterate loop verbatim**; the only genuinely new logic is the fan-out planner and
the join barrier.

```jsonc
// excerpt from .github/agent-factory/protocols/code-review/protocol.json
{
  "name": "code-review",
  "states": [
    // ... preflight (kind:"agent") omitted for brevity ...
    { "id": "review",
      "kind": "fanout",                 // fan out to N parallel agent branches
      "branches": [
        { "id": "grumpy",   "workflow": "grumpy-agent",
          "evidence": "grumpy.evidence.schema.json",   "max_iterations": 3,
          "params": { "categories": ["naming","error-handling","performance","duplication","security"] },
          "checks": [ {"run":"schema-valid"}, {"run":"rubric-coverage"},
                      {"run":"traces-exist-in-diff"} ],
          "publish": "publish-grumpy" },
        { "id": "security", "workflow": "security-agent",
          "evidence": "security.evidence.schema.json", "max_iterations": 3,
          "params": { "categories": ["security"] },    // scoped: schema-valid rejects any other category
          "checks": [ {"run":"schema-valid"},
                      {"run":"traces-exist-in-diff"} ], // no rubric-coverage
          "publish": "publish-security" }
      ],
      "next": "join" },
    { "id": "join",
      "kind": "join",                   // strict AND-barrier over review's branches
      "of": "review",
      "next": "approval" },             // advance only when every branch is `done`
    { "id": "approval",
      "kind": "gate",                   // human sign-off required
      "next": "done" }
  ]
}
```

Each branch carries its **own** `workflow`, `evidence` schema, `max_iterations`,
`params`, `checks`, and `publish` hook — so branches can differ. Here the **security branch
drops `rubric-coverage`** (it has no fixed file×category rubric); it runs only
`schema-valid` + `traces-exist-in-diff`. Its `params.categories: ["security"]` also
means `schema-valid` deterministically rejects a verdict in any other category.

### 8.3 Per-branch state + the instance file

Each branch gets its own state file, byte-shaped exactly like a v1 single-agent
state, plus one shared instance file per PR:

```
code-review/pr-<N>/review.grumpy.yaml    # the grumpy leg (looks like a single-agent state)
code-review/pr-<N>/review.security.yaml  # the security leg
code-review/pr-<N>/_instance.yaml        # shared: { head_sha, joined: false, cursor, ... }
```

(The `tests/fixtures/fanout-mini/` regression fixture exercises the same per-branch
state layout at `fanout-mini/pr-<N>/<branch>.yaml` without the multi-phase wrapping.)

A branch is **active** when its state file's `.state == review` (the fan-out
state id); **terminal** is `done`/`failed`. Crucially, **each branch writes only
its own file**, so the v1 compare-and-swap invariant (§2.5) holds with no write
contention even though the legs run concurrently — there is never a two-writer
race on a single file. The `_instance.yaml` `joined` flag makes the join step
idempotent (§8.5).

### 8.4 Eager publish vs. the strict join gate (the hybrid), and the two axes

Two things that v1 collapsed into one are deliberately separated in v2:

- **Eager publish** — each branch publishes its review the **moment it reaches
  `done`** (grumpy 😤, security 🔒), independently of the other. You see results
  as they land, not all-or-nothing at the end.
- **The gate is the join, not the publish.** The aggregate `code-review`
  check-run goes green **only when every branch reaches `done`**. A branch that
  exhausts to `failed` publishes nothing and leaves the aggregate **red**
  ("Review incomplete") → merge loudly blocked. A missing review is *always* a
  red gate, never an absent-but-green one — there is no silent gap.

This rests on keeping two axes orthogonal:

| | meaning | example |
|---|---|---|
| **Process** (`done` / `failed`) | did the agent produce evidence that passed *its* checks within `max_iterations`? | exhausted security branch → `failed` |
| **Verdict** (APPROVE / CHANGES_REQUESTED) | what did the (successful) review *conclude*? | grumpy found 5 issues → CHANGES_REQUESTED |

A valid review **with comments** is a process **success**, published normally;
its per-branch check-run conclusion `failure` then means *"changes requested,"*
**not** process failure. **The strict join gate is about the process axis only.**

### 8.5 `join.py` and the serialized join workflow

`advance.py`, when a branch reaches a terminal state, emits a
`repository_dispatch: protocol-join` (and now also carries `client_payload[branch]`
on its `protocol-continue` iterate dispatch). That dispatch fires a dedicated
workflow:

- **`.github/workflows/protocol-join.yml`** — runs **serialized** (concurrency
  group `join-<instance>`, `cancel-in-progress: false`) so two near-simultaneous
  branch completions can't double-evaluate the barrier.
- **`engine/join.py`** (new) — reads **every** branch state file; once all are
  terminal **and** `_instance.yaml.joined` is still false, it sets the aggregate
  check-run (`success` iff every branch is `done`, else `failure`), re-renders
  the status comment, flips `joined: true`, and CAS-pushes. It is **idempotent**:
  a second join run sees `joined: true` and no-ops.

### 8.6 Three check-runs

| check-run | kind | role |
|---|---|---|
| `code-review/grumpy` | per-branch | informational — the grumpy leg's outcome |
| `code-review/security` | per-branch | informational — the security leg's outcome |
| `code-review` | aggregate | **the required gating check** |

`plan` marks the aggregate `in_progress`; `join.py` completes it. Make the
aggregate `code-review` the required status check (same mechanism as §5.1).

### 8.7 The orchestrator as a branch matrix — and why artifacts, not job outputs

`orchestrator.yml` is rewritten so the agent-bearing jobs run as a matrix over
branches:

- **`plan`** runs `next.py` **unbranched** for `pull_request`/`issue_comment`
  (→ action `run-fanout`, `branches: [grumpy, security]`), and **branched**
  (`BRANCH=<payload.branch> next.py … continue`) for `repository_dispatch:
  protocol-continue` (→ exactly one branch).
- **`dispatch` → `checks` → `advance`** become a `strategy.matrix.branch`
  (`fail-fast: false`), gated `if: needs.plan.outputs.branches != '[]'`. Each leg
  carries `BRANCH=<branch>` end-to-end and preserves the four v1 trust zones
  (plan = engine-pre; dispatch = agent-trigger; checks = read-only ground truth,
  **no write tokens**; advance = sole state writer + publisher).

> **Why per-branch data flows through branch-named ARTIFACTS, not job `outputs`.**
> A GHA matrix's legs **share a single `outputs` map** for the job — the last leg
> to finish clobbers the others. So the agent run-id and the verdicts can't be
> passed leg→leg as job outputs. Instead each leg uploads **branch-named
> artifacts** (`runmeta-<branch>`, `verdicts-<branch>`) and the downstream leg
> downloads its own. This is the central plumbing decision of the v2 orchestrator.

### 8.8 The security agent and its sabotage knob

`.github/workflows/security-agent.md` (compiled to `security-agent.lock.yml`) is
a thin gh-aw clone of grumpy-agent that emits grumpy-shaped evidence tagged
`category:"security"`. Its sabotage knob differs from grumpy's on purpose, to
demonstrate the red gate:

- **grumpy:** iteration-1-only sabotage → **omits** two rubric categories
  (`security`, `duplication`) on iteration 1 so it fails `rubric-coverage`, then
  **self-recovers** on the next iteration and reaches `done`.
- **security:** **persistent** sabotage → while the `poc:sabotage` label is
  present it fabricates a finding **every** iteration → fails
  `traces-exist-in-diff` → exhausts to `failed`.

The orchestrator's sabotage step now reports the label regardless of iteration;
each agent self-decides what to do with it.

### 8.9 The fan-out lifecycle

```
event (pull_request open/push, /review comment)
   │  plan (UNBRANCHED): next.py → run-fanout, branches=[grumpy, security]
   ▼
review  ── fan out ──┬─────────────────────────┬──────────────────────────
                     │ branch: grumpy           │ branch: security
                     ▼  (iterate loop)          ▼  (iterate loop, no rubric-coverage)
            dispatch→checks→advance     dispatch→checks→advance
            (BRANCH=grumpy)             (BRANCH=security)
                     │ on terminal:             │ on terminal:
                     │  eager-publish if `done`  │  eager-publish if `done`
                     │  + repository_dispatch    │  + repository_dispatch
                     │    protocol-join          │    protocol-join
                     └────────────┬─────────────┘
                                  ▼  protocol-join.yml (SERIALIZED, concurrency join-<instance>)
                              [join] join.py: all branches terminal & not joined?
                                  • all `done`  → aggregate `code-review` = success
                                                  → open approval gate
                                  • any `failed`→ aggregate `code-review` = failure (red gate)
                                  flip _instance.yaml joined:true, CAS-push (idempotent)
                                  ▼
                              [approval] kind:"gate" — awaits human /approve
                                  ▼
                                done
```

Each branch's loop is the §3.3 v1 lifecycle verbatim, just with `BRANCH` set;
the only new states are the `fanout` entry (one `plan`, N legs) and the `join`
barrier.

### 8.10 Concurrency caveat

Fan-out **within one PR** was always safe even though both agents run
concurrently, because `grumpy` and `security` are **distinct workflow files** —
each branch's run resolver only ever sees its own workflow's runs, so the two
legs can't misattribute each other's runs. The originally-unsolved case was
**concurrent PRs of the *same* workflow**, which share a global concurrency
group. That is **fixed** by the correlation-id resolver (§3.5): each dispatch
stamps a `cid:[<cid>]` token into the run's displayTitle and
`match_run_by_cid` resolves on it, failing loudly on no match (see `STATUS.md`).

### 8.11 Tests

`tests/test_join.py` (join aggregation + idempotency) and
`tests/test_fanout_e2e.py` (local end-to-end: fanout start → advance ×2 → join
success) cover the fan-out/join mechanics. The full suite (338+ tests across
multiple modules) is run with `pytest tests/ -q`; see `STATUS.md` for the
current count.
