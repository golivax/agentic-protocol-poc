# The Agentic Protocol Engine — How It Works

A guide to the PoC: why it exists, the ideas it's built on, its architecture,
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

---

## 3. Architecture

### 3.1 Components

```
protocols/grumpy/
  protocol.json          # states, checks, transitions, max_iterations (DATA)
  evidence.schema.json   # the rubric the agent must fill (the CONTRACT)
  checks/*.sh            # deterministic transition checks (FORM verification)
  publish/publish-review-from-evidence.sh  # protocol's publish hook (zone 4)

.github/engine/            # GENERIC — fully protocol-agnostic
  lib.sh                 # state checkout, CAS push, status-comment upsert,
                         #   resolve_executable, set_check_run
  next.sh                # planner: (state, protocol, command) -> action JSON
  advance.sh             # sole state writer: verdicts -> mutate, publish, push
  run-checks.sh          # resolve + run a state's checks (any language) -> verdicts

.github/workflows/
  orchestrator.yml       # the 4 trust zones; maps events → commands; calls the engine
  grumpy-agent.md        # gh-aw agent workflow for the example protocol
  grumpy-agent.lock.yml  # compiled output of grumpy-agent.md (committed)

agentic-state branch
  <protocol-id>/<instance-key>.yaml   # durable per-instance state (e.g. grumpy-review/pr-<N>.yaml)
```

> **`grumpy-review` is an example protocol, not the engine.** Everything under
> `.github/engine/` is protocol-agnostic: it reads the protocol id from
> `protocol.json` `.name`, resolves checks and publish hooks from the protocol
> directory, and derives the state path as `<protocol-id>/<instance-key>.yaml`.
> The grumpy reviewer — its `protocol.json`, evidence schema, checks, and
> publish hook — lives entirely in `protocols/grumpy/` + `grumpy-agent.md` and
> exists to exercise the engine. To build a different protocol (an
> incident-response runbook, a release checklist, a compliance review) you write
> a new `protocols/<name>/` and agent workflow; you do **not** touch the engine.

### 3.2 The four trust zones (per iteration)

Each iteration runs as jobs in `orchestrator.yml`, with strictly separated
credentials:

| Zone | Job | Holds | Runs agent code? |
|------|-----|-------|------------------|
| 1. Engine-pre | `plan` | state-branch PAT | no — deterministic `next.sh` |
| 2. Agent | `dispatch` → the gh-aw workflow | read-only repo token + LLM creds | yes — sandboxed |
| 3. Checks | `checks` | nothing (read-only default token) | no — bash/jq over evidence + diff |
| 4. Engine-post | `advance` | state PAT + publish token | no — reads check verdicts only |

The invariant: **the engine and the agent never share a job or a credential.**
The agent produces an `evidence.json` artifact; the checks job downloads it and
independently re-fetches the PR diff (it never trusts agent-fetched data); the
advance job reads only the check *verdicts* to decide, and only the evidence (to
*render* the already-decided review).

### 3.3 The transition lifecycle

```
event (pull_request open/push, /grumpy comment, or repository_dispatch "protocol-continue")
   │
   ▼  orchestrator maps event → command: opened/reopened → start,
      synchronize → reset, issue_comment /grumpy → start, dispatch → continue
   │
   ▼
[plan]      checkout agentic-state; next.sh <dir> <instance-key> <protocol.json>
            <command> [head_sha] reads/creates <protocol-id>/<instance-key>.yaml,
            emits {action: run-agent|halt, iteration, feedback}
   │ run-agent
   ▼
[dispatch]  workflow_dispatch the gh-aw agent with aw_context = {pr, iteration,
            feedback, sabotage}; poll until it finishes; output its run id
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

The loop terminates in at most `max_iterations` agent runs. `next.sh` independently
halts on a terminal state, so a stray re-dispatch can never resurrect a finished
instance.

### 3.4 State model

`<protocol-id>/<instance-key>.yaml` (e.g. `grumpy-review/pr-<N>.yaml`; porch-compatible field names):

```yaml
protocol: grumpy-review
instance: pr-9
state: done            # review | publish | done | failed
iteration: 2           # 1-based, bounded by max_iterations
gates: {}              # reserved for v2 human gates
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

Every transition is a commit to this file on the `agentic-state` branch, so
`git log agentic-state -- grumpy-review/pr-<N>.yaml` is a complete, auditable history.

---

## 4. Developer guide

### 4.1 Anatomy of a protocol (`protocol.json`)

```jsonc
{
  "name": "grumpy-review",
  "categories": ["naming", "error-handling", "performance", "duplication", "security"],
  "states": [
    { "id": "review",
      "kind": "agent",                 // an LLM step; dispatched as a gh-aw workflow
      "workflow": "grumpy-agent",      // which gh-aw workflow to dispatch
      "evidence": "evidence.schema.json",
      "max_iterations": 3,
      "checks": [                      // run in order between this state and `next`
        { "run": "schema-valid",        "on_fail": "iterate" },
        { "run": "rubric-coverage",     "on_fail": "iterate" },
        { "run": "traces-exist-in-diff","on_fail": "iterate" }
      ],
      "next": "publish" },
    { "id": "publish",
      "kind": "deterministic",         // no agent; the engine executes `action`
      "action": "publish-review-from-evidence",
      "next": null }                   // terminal
  ]
}
```

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
The engine's `run-checks.sh` reads the check *list* from `protocol.json`
(`.states[].checks[]`) and resolves each entry to an executable:

- if the entry sets `"exec": "<path>"`, it runs `<protocol-dir>/<path>`;
- otherwise it finds `<protocol-dir>/checks/<run>` or `checks/<run>.*`
  (extension-agnostic — so `checks/rubric-coverage.py` and
  `checks/schema-valid.sh` are both first-class).

So a Python check is just `checks/<name>.py` with `#!/usr/bin/env python3` and
`chmod +x` — **no bash wrapper.** A wrapper is only worth writing when you're
*adapting an existing tool* whose output isn't the verdict JSON (e.g. a
`checks/tests-adapter.py` that runs `pytest` and translates its exit code into
`{check,pass,feedback}`) — that adapter does real work, and it's exactly the
"a check that runs agent-authored code in the credential-free zone-3 job" case.
`run-checks.sh` turns a missing, non-executable, crashing, or malformed check
into a *failing verdict* rather than aborting the run.

In this example protocol, `rubric-coverage` is implemented in **Python** and the
other two in **bash**, to demonstrate the mix; all three obey the same ABI.

Whatever the language: **always exit 0** even when the check fails (a non-zero
exit is reserved for a genuine runner error), and read the rubric from
`protocol.json` rather than hardcoding it.

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

The three shipped checks, in full. They are deliberately small (~30–40 lines of
`bash` + `jq`) and share the contract above.

#### `schema-valid.sh` — does the evidence have the right shape?

It guards three layers (parseable JSON → `.files` is an array → every entry is
an object with a `verdicts` array) so a malformed file produces a verdict
instead of crashing the main pass. Then one `jq` program collects *all* shape
violations (not just the first) and joins them into the feedback. The legal
categories come from `protocol.json`, never hardcoded.

```bash
#!/usr/bin/env bash
# Check: evidence file parses and matches the structural shape.
# Usage: schema-valid.sh <evidence.json> <diff.txt> <changed-files.txt>
set -euo pipefail
EV="$1"
PROTO="$(cd "$(dirname "$0")/.." && pwd)/protocol.json"

emit() { jq -n --argjson p "$1" --arg f "$2" '{check:"schema-valid", pass:$p, feedback:$f}'; }

if ! jq -e . "$EV" >/dev/null 2>&1; then
  emit false "evidence file is missing or not valid JSON"; exit 0
fi
if ! jq -e '.files | type == "array"' "$EV" >/dev/null 2>&1; then
  emit false "top-level .files array is missing"; exit 0
fi
if ! jq -e '[.files[] | type == "object" and (.verdicts | type == "array")] | all' "$EV" >/dev/null 2>&1; then
  emit false "a .files entry is not an object with a verdicts array; check that every file is an object and verdicts is an array"; exit 0
fi

CATS_JSON=$(jq -c '.categories' "$PROTO")
ERR=$(jq -r --argjson valid "$CATS_JSON" '
  [ .files[] | .path as $p | .verdicts[]? |
    if (.category as $c | $valid | index($c) | not)
      then "illegal category \(.category) in \($p)"
    elif (.verdict != "issues-found" and .verdict != "none-found")
      then "illegal verdict \(.verdict) for \(.category) × \($p)"
    elif .verdict == "issues-found" and ((.findings // []) | length) == 0
      then "issues-found with no findings: \(.category) × \($p)"
    elif .verdict == "issues-found" and ([(.findings // [])[] | ((.existing_code // "") | length) > 0 and ((.comment // "") | length) > 0] | all | not)
      then "finding with empty existing_code or comment: \(.category) × \($p)"
    elif .verdict == "none-found" and ((.examined // []) | length) == 0
      then "none-found with no examined identifiers: \(.category) × \($p)"
    else empty end
  ] | join("; ")' "$EV")

if [ -n "$ERR" ]; then emit false "$ERR"; else emit true ""; fi
```

#### `rubric-coverage.py` — is every cell of the rubric filled exactly once?

*(This one is **Python**, to show the ABI is language-agnostic — it's resolved
and run by `run-checks.sh` exactly like the bash checks.)* Ground truth is the
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
    proto = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "protocol.json"
    )
    with open(proto) as fh:
        categories = json.load(fh)["categories"]

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

#### `traces-exist-in-diff.sh` — does every claim point at something real?

This is what makes "I found nothing" cost more than a sentence. A `jq` pass
flattens the evidence into a stream of claims — each `existing_code` snippet and
each `examined` identifier, tagged with its file — and the shell verifies each
against the diff *the check itself was handed*:

- **snippets** are matched as a whitespace-normalized substring against the
  whole diff, after stripping the leading `+`/`-` so a multi-line snippet copied
  verbatim from the source still matches;
- **identifiers** must appear within the claimed file's own diff section, where
  the section is delimited by the `diff --git … b/<path>` header (matched as a
  literal suffix, not a regex, so a path like `src/a.js` can't match
  `src/aXjs`).

```bash
#!/usr/bin/env bash
# Check: every positive claim (existing_code) and negative-attestation trace
# (examined identifier) verifiably exists in the independently fetched diff.
# Usage: traces-exist-in-diff.sh <evidence.json> <diff.txt> <changed-files.txt>
set -euo pipefail
EV="$1"; DIFF="$2"

norm() { tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//'; }
# Strip the +/- prefix from diff content lines so multi-line snippets quoted
# verbatim from the source match after normalization.
DIFF_NORM=$(sed -E 's/^[+-]//' "$DIFF" | norm)

# file_section <path> — the diff lines belonging to one file
file_section() {
  awk -v p="$1" '
    /^diff --git /{ tail = " b/" p; on = (substr($0, length($0)-length(tail)+1) == tail) } on
  ' "$DIFF"
}

BAD=()
while IFS= read -r row; do
  path=$(jq -r '.path' <<<"$row")
  cat=$(jq -r '.category' <<<"$row")
  kind=$(jq -r '.kind' <<<"$row")
  val=$(jq -r '.value' <<<"$row")
  if [ "$kind" = "snippet" ]; then
    v=$(norm <<<"$val")
    case "$DIFF_NORM" in *"$v"*) ;; *) BAD+=("existing_code not in diff ($cat × $path): \"$val\"") ;; esac
  else
    if ! file_section "$path" | grep -qF -- "$val"; then
      BAD+=("examined identifier not in $path's diff ($cat): \"$val\"")
    fi
  fi
done < <(jq -c '
  .files[]? | .path as $p | .verdicts[]? | .category as $c |
  ( (.findings // [])[] | {path:$p, category:$c, kind:"snippet", value:.existing_code} ),
  ( (.examined // [])[] | {path:$p, category:$c, kind:"identifier", value:.} )' "$EV")

if [ "${#BAD[@]}" -gt 0 ]; then
  FB="Unverifiable claims: $(IFS='; '; echo "${BAD[*]}")"
  jq -n --arg f "$FB" '{check:"traces-exist-in-diff", pass:false, feedback:$f}'
else
  jq -n '{check:"traces-exist-in-diff", pass:true, feedback:""}'
fi
```

Together these three implement the principle from §2.7: `schema-valid` and
`rubric-coverage` enforce that the agent *addressed every cell*, and
`traces-exist-in-diff` enforces that each cell's claim — positive or negative —
is *anchored in code that actually changed*. None of them judge whether a
finding is correct; that's substance, reserved for a future judge/gate.

### 4.4 The agent workflow (`grumpy-agent.md`)

A normal gh-aw markdown file with two protocol-specific responsibilities:

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

Compile it with `gh aw compile` and commit the generated `grumpy-agent.lock.yml`
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

The engine (`next.sh`) receives a **command** and never sniffs GitHub events.
The orchestrator translates events to commands:

| GitHub event | Condition | Command |
|---|---|---|
| `pull_request` opened / reopened | — | `start` |
| `pull_request` synchronize | new commit pushed | `reset` |
| `issue_comment` created | body starts with `/grumpy` | `start` |
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

`head_sha` (the fifth argument to `next.sh`) is **recorded as metadata** only
(stored in the state file for check-run binding). The engine never compares it
to decide policy — that comparison was removed. The orchestrator is where the
policy lives: `synchronize` → `reset` is the mechanism that ensures a new push
invalidates the old review.

### 4.7 The publish hook

When all checks pass, `advance.sh` calls the protocol's **publish hook** —
a protocol-provided executable resolved via the same `resolve_executable`
function used for checks (see §4.3), from the publish state's `.action` field:

```jsonc
{ "id": "publish",
  "kind": "deterministic",
  "action": "publish-review-from-evidence"  // resolved in protocols/<name>/publish/
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

The publish hook runs **trusted in zone 4** alongside `advance.sh`, holding
the publish token. It has the authority to post PR reviews and check runs.
A check (zone 3) has no credentials and is explicitly designed to run code
that touches agent-influenced data without being able to affect external
systems. Do not conflate them: "resolved via `resolve_executable`" describes
the *mechanical* resolution; the trust boundary is entirely different.

For grumpy-review, the publish hook is
`protocols/grumpy/publish/publish-review-from-evidence.sh`. It reads the
evidence, posts a REQUEST_CHANGES or APPROVE review (COMMENT fallback if the
repo setting is off), and returns `{"conclusion":"failure"|"success","summary":"…"}`.

---

## 5. Using a protocol through GitHub (developer's-eye view)

1. **Open a PR** as usual. The orchestrator triggers automatically on
   `pull_request` `opened`/`synchronize`/`reopened`, so the review runs on open
   **and re-runs on every push** (a new commit resets the instance to a fresh
   review of the new head — see §5.1). `/grumpy` remains a manual re-trigger.
2. *(optional)* **Comment `/grumpy`** to re-run on demand.
3. **Watch it work.** On the happy path you get one workflow run and a review
   appears — same UX as plain gh-aw. The protocol machinery only becomes
   visible when it has something to say:
   - The engine maintains **one status comment**, re-rendered each transition
     into a checklist:
     ```
     🔍 grumpy-review · pr-9
     - ✗ iteration 1/3 — Missing: security × src/auth.js; duplication × src/report.js
     - ✅ iteration 2/3 — all checks passed
     ✅ done — review published.
     [Full state & audit trail](…/blob/agentic-state/grumpy-review/pr-9.yaml)
     ```
   - The final **review** (REQUEST_CHANGES / APPROVE) is the deliverable.
4. **If checks fail**, you don't see half-baked output — the agent silently
   iterates (a second run), and only checked output is ever published. After
   `max_iterations`, the engine posts a clear failure instead of going quiet.
5. **Inspect the record** any time: the status-comment link, the `agentic-state`
   branch (`git log agentic-state -- grumpy-review/pr-<N>.yaml`), or the Actions tab
   (one orchestrator run + one agent run per iteration).

The mental-model shift from plain gh-aw: **the PR/issue is the unit of
existence, and workflow runs are heartbeats that advance it** — not the other
way round. A protocol can sit waiting (a future human gate) for weeks at zero
cost, because "waiting" is just a line in a committed file.

### 5.1 Blocking the merge on the review

By default a review verdict is *advisory* — GitHub won't stop a merge just
because a review requested changes. To make the protocol a real merge gate, it
publishes a **check run** named after the protocol id (for grumpy-review: `grumpy-review`)
on the PR's head commit, reflecting protocol state:

| protocol state | check run | merge box |
|---|---|---|
| reviewing / iterating | `in_progress` | pending — blocks |
| changes requested (issues found) | `completed` / `failure` | ❌ blocks |
| clean | `completed` / `success` | ✅ |
| failed after max iterations | `completed` / `failure` | ❌ blocks |

The check run binds to the head SHA. A push mid-review invalidates the old
verdict: the orchestrator maps `pull_request synchronize` → command `reset`,
which tells `next.sh` to **unconditionally start a fresh review** of the new
commit (the prior review stays in the state branch's git history). The engine
does not compare head SHAs itself — trigger policy lives in the orchestrator
(see §4.6). So the gate can never go green on un-reviewed code. The check is
emitted with the Actions `GITHUB_TOKEN` (the Checks API is App/Actions-token
only — a PAT can't create check runs), via `set_check_run` in `lib.sh`, from the
`plan` job (initial `in_progress`) and `advance.sh` (terminal/iterate states).
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
once (so GitHub knows the `grumpy-review` name):

- **Ruleset** (recommended): *Settings → Rules → Rulesets → New branch ruleset*,
  target the default branch, enable *Require status checks before merging*, add
  `grumpy-review` (source: GitHub Actions).
- **Classic**: *Settings → Branches → Add rule*, pattern `main`, *Require status
  checks to pass before merging*, search and select `grumpy-review`.

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
