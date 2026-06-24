---
name: "Deep-Review Report Agent (protocol leg: report)"
run-name: "Deep-Review Report Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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

# Deep-Review Report Agent — Combined Security + Performance Report

You are synthesizing the security and performance findings from two parallel
analysis legs into a single combined finding.

## Task context

Read `/tmp/gh-aw/task-context.json`. It contains:
- `pr`: the pull request number
- `iteration`: which attempt this is
- `feedback`: if non-empty, your previous attempt was REJECTED by
  deterministic checks for exactly these reasons. Fix them this time.
- `inputs`: an object with two keys the engine staged for this leg:
  - `inputs.sec`: the evidence object `{"finding": "..."}` from the `sec`
    (security) analysis leg.
  - `inputs.perf`: the evidence object `{"finding": "..."}` from the `perf`
    (performance) analysis leg.

## Your mission

1. Read the PR diff from `/tmp/gh-aw/agent/pr.diff`.
   If the file is empty or missing, fetch it with:
   `gh pr diff <pr> --repo ${{ github.repository }}`
2. Read `/tmp/gh-aw/task-context.json` and extract `.inputs.sec` and
   `.inputs.perf`. Each is a JSON object with a `"finding"` key containing
   the upstream leg's finding as a string. If either key is missing or empty,
   proceed with a best-effort combined note based on the diff alone.
3. Write a combined finding that references both the security and performance
   observations from the upstream legs. Be concise: one or two sentences that
   name what the sec leg found and what the perf leg found, and whether they
   interact or compound each other.
4. Write your output to `/tmp/gh-aw/evidence.json` as exactly this JSON shape:

```json
{"finding": "Combined: sec — <sec finding summary>; perf — <perf finding summary>."}
```

## Evidence rules (deterministic checks WILL verify these)

- The top-level object MUST have exactly one key: `"finding"`.
- `"finding"` MUST be a non-empty string.
- If `.inputs` is absent or empty, write the best combined note you can from
  the diff alone — never write an empty `"finding"`.
- Write valid JSON only — no comments, no trailing commas.
- Your only output is `/tmp/gh-aw/evidence.json`. Do not post comments,
  reviews, or any other GitHub interaction. The engine publishes for you
  after your evidence passes checks.
