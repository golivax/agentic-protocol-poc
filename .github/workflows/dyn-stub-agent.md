---
name: "Dyn Stub Agent (protocol state: review)"
concurrency:
  group: "dyn-stub-${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}"
  cancel-in-progress: false
run-name: "Dyn Stub Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
safe-outputs:
  # The stub agent's only output is the evidence.json artifact (post-steps),
  # NOT a gh-aw safe-output — so gh-aw's default conclusion job files a spurious
  # "No Safe Outputs Generated" issue per leg. noop.report-as-issue:false suppresses it.
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

# Dyn Stub Agent — plumbing proof

This is a STUB agent proving the dynamic-fanout live wiring. Do no real review.

## Task context

Read `/tmp/gh-aw/task-context.json`. Its `.inputs.file.path` is the changed file this
leg was fanned out for. (`.pr`, `.iteration`, `.feedback` are also present.)

## Your job

Write `/tmp/gh-aw/evidence.json` containing exactly:

    { "examined": ["<the .inputs.file.path value>"] }

Use the file path from the task context verbatim. Do not add other keys.
