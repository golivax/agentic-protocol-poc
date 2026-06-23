---
name: "RMM Draft Agent (protocol sub-state: rationale/draft)"
run-name: "RMM Draft Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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

# RMM Draft Agent — Clarifying Questions

You are generating 2–3 clarifying questions a reviewer would ask the PR
author to understand the INTENT and RISK of the change.

## Task context

Read `/tmp/gh-aw/task-context.json`. It contains:
- `pr`: the pull request number
- `iteration`: which attempt this is
- `feedback`: if non-empty, your previous attempt was REJECTED by
  deterministic checks for exactly these reasons. Fix them this time.

## Your mission

1. Read the PR diff from `/tmp/gh-aw/agent/pr.diff`.
   If the file is empty or missing, fetch it with:
   `gh pr diff <pr> --repo ${{ github.repository }}`
2. Identify 2 to 3 questions a reviewer would genuinely need answered to
   understand the INTENT and RISK of the change. Good question types:
   - "Why this approach over X?" (design rationale)
   - "What's the migration / rollback risk?" (operational risk)
   - "How does this interact with Y?" (unexpected coupling)
   Each question must have a short stable id (`q1`, `q2`, `q3`) and a `text`.
3. Write your output to `/tmp/gh-aw/evidence.json` as exactly this JSON shape:

```json
{"questions": [
  {"id": "q1", "text": "Why was X chosen over Y for ...?"},
  {"id": "q2", "text": "What is the rollback plan if ...?"}
]}
```

## Evidence rules (deterministic checks WILL verify these)

- The top-level object MUST have exactly one key: `"questions"`.
- `"questions"` MUST be a non-empty JSON array.
- Each element MUST have a non-empty `"id"` string and a non-empty `"text"` string.
- Use stable ids `q1`, `q2`, `q3` — no spaces or special characters in ids.
- Include at least 2 questions and no more than 3.
- Write valid JSON only — no comments, no trailing commas.
- Your only output is `/tmp/gh-aw/evidence.json`. Do not post comments,
  reviews, or any other GitHub interaction. The engine publishes for you
  after your evidence passes checks.
