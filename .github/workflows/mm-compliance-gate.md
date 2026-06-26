---
'on':
  # Manual dispatch only — the automatic pull_request trigger is intentionally disabled.
  # Pass the target PR via the pr_number input when dispatching.
  workflow_dispatch:
    inputs:
      pr_number: { description: "PR number (manual run)", required: false }
permissions: { contents: read, pull-requests: read, issues: read }
engine:
  id: claude
  env:
    ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_AUTH_TOKEN }}
network:
  allowed:
    - defaults
    - bmc-bz1.tail22da2e.ts.net
safe-outputs:
  add-comment: { max: 1, hide-older-comments: true }
  noop:
tools:
  bash: [ "cat:*", "ls:*", "find:*", "echo:*", "python:*", "python3:*" ]
  edit:
timeout-minutes: 15
steps:
  - name: Checkout (repo workspace for the gh-aw agent + git)
    uses: actions/checkout@v4
    with: { persist-credentials: false }
  - name: Checkout the mental model
    uses: actions/checkout@v4
    with: { ref: _mental_model, path: _mm, persist-credentials: false }
  - name: Prefetch PR context
    env:
      GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
      PR: "${{ github.event.pull_request.number || github.event.inputs.pr_number }}"
      REPO: "${{ github.repository }}"
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,author,body,files,baseRefName,headRefName,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || {
        echo "::warning::pr diff unavailable in one shot; assembling per-file patches"
        gh api "repos/$REPO/pulls/$PR/files" --paginate \
          --jq '.[] | "diff --git a/\(.filename) b/\(.filename)\n--- a/\(.filename)\n+++ b/\(.filename)\n\(.patch // "(patch omitted: too large)")\n"' \
          > /tmp/gh-aw/agent/pr.diff
      }
---

# Mental-Model Compliance Gate

You judge whether a pull request COMPLIES with this repository's stored **mental model (MM)** — its
architectural decisions, conventions, and constraints. This is advisory; you never modify code.

## Inputs (already fetched for you)
- `/tmp/gh-aw/agent/pr.json` — PR metadata (title, author, body, changed files).
- `/tmp/gh-aw/agent/pr.diff` — the full unified diff.
- `_mm/` — the **entire** mental model, checked out from the `_mental_model` branch.

### How the mental model is organized
The MM was produced by **three independent approaches**, each in its own top-level directory (listed
in `_mm/METHODS.txt`). They describe the *same* repository from different angles — use them together:

1. **`_mm/socratic/` — the canonical decision corpus (this is what you judge against).**
   A Socratic theory-recovery pass (Naur-style) synthesized into **AsciiDoc (`.adoc`)**:
   - `_mm/socratic/docs/specs/adrs/*.adoc` — Architecture Decision Records (Nygard format, e.g.
     `yuanrong-datasystem-adr-001-master-worker-split.adoc`).
   - `_mm/socratic/docs/arc42/arc42-*.adoc` — the arc42 architecture overview.
   - `_mm/socratic/docs/specs/prd-*.adoc` — product requirements.
   - `_mm/socratic/docs/specs/use-cases-*.adoc` — use cases / flows.
   - `_mm/socratic/OPEN_QUESTIONS-*.adoc`, `_mm/socratic/QUESTION_TREE-*.adoc` — open questions and
     explicitly deferred items (treat as known gaps, not hard constraints).
   These are the explicit *decisions and constraints* a PR can uphold or diverge from.

2. **`_mm/legion-map/` — a generated codebase map (orientation + retrieval, not decisions).**
   - `_mm/legion-map/CODEBASE.md` — architecture overview, language distribution, module ownership,
     and risk areas.
   - `_mm/legion-map/codebase/index.jsonl` — one JSON chunk per retrievable unit (`path`, `summary`,
     `keywords`, `symbols`, `related_files`, `risk`); `_mm/legion-map/codebase/symbols.json` — coarse
     entry points, APIs, modules, tests, risk areas; `_mm/legion-map/config/directory-mappings.yaml`
     — directory → category mappings.
   Use it to orient which subsystem a changed file belongs to. It is a *map*, not a constraint set.

3. **`_mm/vibed-codeset/` — a codeset-style per-file knowledge base (evidence, not decisions).**
   A knowledge base mined from git history, static analysis (constructs + caller graph), test
   coverage, and co-change relationships. Per file it records past bugs and their root causes, an
   edit checklist (tests to run, constants to keep consistent), pitfalls with consequences, key
   constructs and their callers, and files that historically change together (hidden coupling).
   - Query it with the bundled renderer (this gate has `python3`): from the `_mm/vibed-codeset/`
     directory, `python3 .claude/docs/get_context.py <source/path>` prints one file's record,
     `python3 .claude/docs/get_context.py .` prints a repo overview, and
     `python3 .claude/docs/get_context.py --list` lists the files that have context. The script just
     renders `_mm/vibed-codeset/.claude/docs/knowledge.json` (large; 40 files), so prefer it over
     `cat`-ing the raw JSON. A pre-rendered repo overview also lives at `_mm/vibed-codeset/CLAUDE.md`
     (identical to `AGENTS.md`).
   Use this as evidence about a changed file's known pitfalls and required tests.

The MM is **holistic** — it is NOT tied to this PR, and there is no per-PR ADR. Judge compliance
against the **socratic decision corpus**; `legion-map/` and `vibed-codeset/` are supporting evidence
(orientation, coupling, known pitfalls), not independent constraints to comply with.

## Procedure
1. Read `/tmp/gh-aw/agent/pr.diff` and `/tmp/gh-aw/agent/pr.json`.
2. Enumerate and read the MM decision corpus: `find _mm/socratic -name '*.adoc' -not -path '*/.git/*'`,
   then `cat` each (these are AsciiDoc). For orientation, `cat _mm/legion-map/CODEBASE.md` (which
   subsystem a changed file belongs to). For a changed file's known pitfalls/required tests, query the
   codeset KB: `python3 _mm/vibed-codeset/.claude/docs/get_context.py <changed/source/path>` (or read
   `_mm/vibed-codeset/CLAUDE.md` for the overview).
3. If the diff has no substantive code/behavior change relevant to the MM (pure docs, comments,
   formatting, or test-only churn), call `noop` with a one-line reason and STOP.
4. Otherwise, for each MM decision/convention the diff touches, classify it as **upheld**,
   **diverges**, or **not applicable**.
5. Post EXACTLY ONE comment via `add-comment`.

If there are NO divergences:
~~~markdown
### ✅ Mental-Model Compliance — Compliant

This PR is consistent with the stored mental model.

<details><summary>What was checked</summary>

- {1–4 bullets naming the relevant MM decisions/conventions and how the diff upholds them, with file paths}

</details>
~~~

If there ARE divergences:
~~~markdown
### ⚠️ Mental-Model Compliance — {N} divergence(s)

This PR appears to diverge from the stored mental model. Either change the code to comply, or update
the mental model to reflect the new decision.

<details><summary>Divergences ({N})</summary>

- **{MM doc, e.g. socratic/docs/specs/adrs/yuanrong-datasystem-adr-002-…}: {decision title}** —
  {what the diff does that contradicts it}. Evidence: `{file path}` ({hunk/line summary}). Fix: {one line}.

</details>
~~~

## Rules
- Base every verdict on real evidence from `pr.diff`. Cite file paths. Never invent MM content that
  is not present in `_mm/`.
- Judge ONLY against the provided MM text.
- Always end by calling exactly one safe output (`add-comment` or `noop`).
