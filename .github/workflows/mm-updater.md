---
'on':
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
    branches-ignore: ['_mental_model']
  workflow_dispatch:
    inputs:
      pr_number: { description: "PR number (manual run)", required: false }
permissions: { contents: read, pull-requests: read, issues: read }
strict: false
sandbox:
  agent: false
engine:
  id: claude
  env:
    ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
    ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
network:
  allowed:
    - defaults
    - bmc-bz1.tail22da2e.ts.net
safe-outputs:
  threat-detection: false
  create-pull-request:
    base-branch: _mental_model
    title-prefix: "[mm] "
    labels: [mental-model]
    draft: false
    if-no-changes: ignore
  add-comment: { max: 1, hide-older-comments: true }
  noop:
tools:
  bash: [ "cat:*", "ls:*", "find:*", "echo:*" ]
  edit:
timeout-minutes: 20
steps:
  - name: Checkout the mental model at root (agent edits + PR base)
    uses: actions/checkout@v4
    with: { ref: _mental_model, persist-credentials: false }
  - name: Prefetch PR context
    env:
      GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
      PR: "${{ github.event.pull_request.number || github.event.inputs.pr_number }}"
      REPO: "${{ github.repository }}"
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      echo "$PR" > /tmp/gh-aw/agent/pr-number.txt
      gh pr view "$PR" --repo "$REPO" --json number,title,author,body,files,baseRefName,headRefName > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || {
        echo "::warning::pr diff unavailable in one shot; assembling per-file patches"
        gh api "repos/$REPO/pulls/$PR/files" --paginate \
          --jq '.[] | "diff --git a/\(.filename) b/\(.filename)\n--- a/\(.filename)\n+++ b/\(.filename)\n\(.patch // "(patch omitted: too large)")\n"' \
          > /tmp/gh-aw/agent/pr.diff
      }
---

# Mental-Model Updater

You decide INDEPENDENTLY whether a pull request changes this repository's **mental model (MM)** and,
if so, propose the MM edits as a **separate** pull request against the `_mental_model` branch.

## Inputs
- `/tmp/gh-aw/agent/pr.json` — PR metadata. `/tmp/gh-aw/agent/pr.diff` — the full diff.
- `/tmp/gh-aw/agent/pr-number.txt` — the originating PR number (call it `N`).
- The **working tree is the `_mental_model` branch**. The canonical decisions live under
  `socratic/` as **AsciiDoc**: `socratic/docs/specs/adrs/*.adoc` (Nygard ADRs, named
  `yuanrong-datasystem-adr-NNN-kebab-title.adoc`), `socratic/docs/arc42/arc42-*.adoc`,
  `socratic/docs/specs/prd-*.adoc`, `socratic/docs/specs/use-cases-*.adoc`. Edits you make here
  become the proposed MM PR.

## Procedure
1. Read `pr.diff`, `pr.json`, and `pr-number.txt`. Read the current MM:
   `find socratic -name '*.adoc' -not -path '*/.git/*'`, then `cat` each (AsciiDoc).
2. Decide independently: does this PR introduce or alter an **architectural decision, convention, or
   anti-pattern** the MM should record, or does it **contradict** existing MM content that should be
   revised?
3. **If NO MM change is warranted:** make NO file edits; call `noop` with a one-line reason. STOP.
4. **If a change IS warranted:**
   a. Edit the MM in the working tree, minimally and grounded in the diff. Match the existing
      **AsciiDoc** style (code evidence cited inline as `[file:line]`):
      - New decision → add `socratic/docs/specs/adrs/yuanrong-datasystem-adr-NNN-kebab-title.adoc`
        in Nygard format (`== Status`, `== Context`, `== Decision`, `== Consequences`), using the
        next free 3-digit `NNN` (inspect existing `socratic/docs/specs/adrs/` files to pick it).
      - Changed architecture → edit the relevant section of `socratic/docs/arc42/arc42-yuanrong-datasystem.adoc`.
      - New or changed flow → edit `socratic/docs/specs/use-cases-yuanrong-datasystem.adoc`.
      Never add app or source files; only mental-model AsciiDoc.
   b. Emit `create-pull-request`. Title: `Capture MM change from PR #N: {short title}` (substitute the
      real `N`). The body MUST contain a line `Related to #N` (so GitHub cross-references the original
      PR) plus a short rationale citing evidence from `pr.diff`.
   c. Emit `add-comment` on the original PR:
      ~~~markdown
      ### 🧠 Mental-Model Updater

      This PR appears to change the mental model. I've opened a `[mm]` pull request against the
      `_mental_model` branch with the proposed update — see the cross-reference linked here.
      ~~~
5. Always end by calling the appropriate safe output(s).

## Rules
- The proposed PR must contain ONLY mental-model edits (you are on the `_mental_model` branch).
- Ground every proposed change in real evidence from `pr.diff`. Do not invent unrelated content.
- When unsure whether a change rises to an MM update, prefer `noop` to avoid noise.
