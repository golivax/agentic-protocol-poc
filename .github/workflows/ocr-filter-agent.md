---
name: "OCR Filter Agent (protocol state: review.<file>.findings.<finding>)"
run-name: "OCR Filter Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
strict: false
sandbox:
  agent: false
engine:
  id: claude
  model: claude-sonnet-4-6
  env:
    ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
    ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
permissions:
  contents: read
  pull-requests: read
tools:
  cli-proxy: true
  edit: true
  bash:
    - "gh pr diff *"
safe-outputs:
  # The agent's only output is the evidence.json artifact (post-steps), NOT a
  # gh-aw safe-output — so gh-aw's default conclusion job files a spurious
  # "No Safe Outputs Generated" issue per leg. noop.report-as-issue:false
  # suppresses it.
  noop:
    report-as-issue: false
  threat-detection: false
pre-agent-steps:
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
      cat /tmp/gh-aw/task-context.json
post-steps:
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 10
---

# OCR Filter Agent — keep/drop one finding

You are the last-mile filter (OCR ReviewFilter) for ONE candidate finding
raised by the main-review phase. Decide whether it survives into the posted
review: drop it if it is hallucinated, a duplicate of something already
obviously covered, or too low-value to be worth a human's time; otherwise
keep it and make sure its anchor is still accurate.

## Task context

Read `/tmp/gh-aw/task-context.json`. `inputs.finding` is the candidate:
`{ "finding_id", "path", "existing_code", "side", "line"[, "start_line"],
"comment" }`.

## Your job

1. Fetch the PR diff: `gh pr diff <pr> --repo ${{ github.repository }}` and
   locate the hunk(s) for `inputs.finding.path`.
2. Confirm `inputs.finding.existing_code` still sits at `side`/`line`
   (/`start_line`) in that hunk. If the surrounding diff shifted, relocate the
   anchor to wherever that exact snippet now sits (same side, same hunk); if
   it no longer appears at all, treat the finding as hallucinated and drop
   it.
3. Decide `keep`:
   - drop (`keep: false`) if: the snippet cannot be located in the diff
     (hallucinated), the finding restates something a competent reviewer
     would not bother commenting on, or it's a near-duplicate of a more
     specific concern about the same code.
   - keep (`keep: true`) otherwise.
4. Write `/tmp/gh-aw/evidence.json`:

```json
{ "finding_id": "<inputs.finding.finding_id, verbatim>",
  "keep": true,
  "anchor": { "side": "RIGHT", "line": 42 },
  "reason": "short justification",
  "path": "<inputs.finding.path>",
  "existing_code": "<inputs.finding.existing_code (or the relocated snippet)>",
  "comment": "<inputs.finding.comment>" }
```

## Evidence rules (deterministic checks WILL verify these)

- `finding_id` MUST echo `inputs.finding.finding_id` verbatim.
- `keep` is a boolean. When `keep` is `true`, `anchor` is REQUIRED:
  `{ "side": "RIGHT"|"LEFT", "line": <int>[, "start_line": <int>] }`, and it
  must resolve to the real `existing_code` snippet in the independently
  -fetched diff (same rules as the main-review phase: `+`/`-`/context lines
  advance RIGHT/LEFT/both from each hunk's `@@ -OLD +NEW @@` header). When
  `keep` is `false`, `anchor` may be omitted.
- Also echo `path`, `existing_code`, and `comment` back — verbatim, or the
  relocated `existing_code` if you moved the anchor — even though the schema
  does not require them: the downstream reduce/post-review step needs them to
  render the review comment.
- `reason` is a short (one sentence) justification for the decision.
- Your only output is `/tmp/gh-aw/evidence.json`. Do not comment on the PR or
  post any other GitHub interaction; the engine publishes the eventual review
  for you.
