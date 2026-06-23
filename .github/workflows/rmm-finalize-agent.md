---
name: "RMM Finalize Agent (protocol sub-state: rationale/finalize)"
run-name: "RMM Finalize Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
# Custom Anthropic-compatible endpoint (public, Funnel-exposed). The endpoint
# accepts Bearer auth and needs no token-steering, so we bypass AWF's api-proxy
# (sandbox.agent: false) and let the claude CLI call it directly. engine.env is
# used (not top-level env) because gh-aw forwards engine.env to the CLI subprocess.
permissions:
  contents: read
  pull-requests: read
tools:
  cli-proxy: true
  edit: true
  bash:
    - "gh pr diff *"
pre-agent-steps:
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Fetch PR diff
    env:
      GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
      PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}"
      REPO: "${{ github.repository }}"
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
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

# RMM Finalize Agent — Rationale & Risk Note

You are synthesizing the PR author's answers to clarifying questions into a
final rationale and risk note for the change.

## Task context

Read `/tmp/gh-aw/task-context.json`. It contains:
- `pr`: the pull request number
- `iteration`: which attempt this is
- `feedback`: if non-empty, your previous attempt was REJECTED by
  deterministic checks for exactly these reasons. Fix them this time.
- `inputs`: an object with two keys the engine staged for this leg:
  - `inputs.draft`: the draft evidence object `{"questions":[...]}` from the
    previous draft step — the questions that were asked.
  - `inputs.answers`: an object `{"questions":[...],"answers":{"q1":"...","q2":"..."}}`
    — the human's answers to those questions.

## Your mission

1. Read the PR diff from `/tmp/gh-aw/agent/pr.diff`.
   If the file is empty or missing, fetch it with:
   `gh pr diff <pr> --repo ${{ github.repository }}`
2. Read `/tmp/gh-aw/task-context.json` and extract `.inputs.draft` and
   `.inputs.answers`. If either key is missing or empty, write a best-effort
   rationale based solely on the diff — do not fail.
3. Synthesize a rationale and risk note that:
   - Explains the INTENT of the change (what problem it solves and why this
     approach was chosen), grounded in the author's answers where available.
   - Calls out the RISK and MITIGATION (rollback plan, migration concerns,
     unexpected coupling), grounded in the author's answers where available.
   - Remains grounded in the actual diff — do not invent facts not in the diff
     or the answers.
4. Write your output to `/tmp/gh-aw/evidence.json` as exactly this JSON shape:

```json
{"rationale": "This change solves X by doing Y because the author confirmed Z. The main risk is W, mitigated by V."}
```

## Evidence rules (deterministic checks WILL verify these)

- The top-level object MUST have exactly one key: `"rationale"`.
- `"rationale"` MUST be a non-empty string.
- If `.inputs` is absent or empty, write the best rationale you can from the
  diff alone — never write an empty `"rationale"`.
- Write valid JSON only — no comments, no trailing commas.
- Your only output is `/tmp/gh-aw/evidence.json`. Do not post comments,
  reviews, or any other GitHub interaction. The engine publishes for you
  after your evidence passes checks.
