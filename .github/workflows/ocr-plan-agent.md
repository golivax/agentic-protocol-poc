---
name: "OCR Plan Agent (protocol state: review.<file>.plan)"
run-name: "OCR Plan Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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

# OCR Plan Agent — per-file review scoping

You are the first phase of a per-file code review sub-pipeline (OCR-style:
plan → main-review → per-finding filter). Your only job is to scope what the
next phase should look at — do NOT record findings or verdicts here.

## Task context

Read `/tmp/gh-aw/task-context.json`. It contains:
- `pr`: the pull request number.
- `inputs.file.path`: the file this leg is scoped to. This is the ONLY thing
  you need to identify your file — the diff itself is NOT inlined (only
  `path` rides the dispatch matrix); fetch it yourself.
- `iteration`, `feedback`: unused (`plan` runs a single iteration).

## Your job

1. Fetch the PR diff: `gh pr diff <pr> --repo ${{ github.repository }}`.
2. Find the hunk(s) for `inputs.file.path` (the section starting
   `diff --git a/<path> b/<path>`) and read it.
3. Write `/tmp/gh-aw/evidence.json`:

```json
{ "examined": ["<inputs.file.path value, verbatim>"],
  "plan_items": ["short note on what to look at in the next phase", "..."] }
```

## Evidence rules

- `examined` MUST be exactly a one-element array containing `inputs.file.path`
  verbatim — that is what proves you looked at the right file.
- `plan_items` is a short list (1-5 items) of concrete, natural-language
  scoping notes for the main-review phase (risk areas, functions touched,
  things worth double-checking). Do not fabricate findings or issue verdicts
  here — that is the next phase's job.
- Your only output is `/tmp/gh-aw/evidence.json`. Do not comment on the PR or
  post any other GitHub interaction; the engine publishes the eventual review
  for you once every phase's evidence passes checks.
