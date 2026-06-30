---
name: "MM Socratic Answering Agent (protocol sub-state: recover/socratic/answering)"
run-name: "MM Socratic Answering Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
strict: false
sandbox:
  agent: false
features:
  dangerously-disable-sandbox-agent: "POC custom Anthropic endpoint cannot be expressed in AWF static egress allowlist; agent stays read-only and never holds the state PAT"
engine:
  id: claude
  model: claude-sonnet-4-6
  env:
    ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
    ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
# INFRA PREREQUISITE: runs the AUTOMATED answering step of socratic-code-theory-
# recovery — it researches (code + web) and fills the OPEN leaves of phase 1, with
# NO human input. Restores phase 1's tree from the mm-tree-socratic-phase1 artifact
# (needs actions:read), runs `claude` to answer, and uploads the answered tree.
# See docs/STATUS.md.
permissions:
  contents: read
  pull-requests: read
  actions: read
tools:
  cli-proxy: true
  edit: true
  bash: [":*"]
pre-agent-steps:
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
      cat /tmp/gh-aw/task-context.json
  - name: Checkout target ref
    uses: actions/checkout@v5
    with:
      ref: ${{ fromJSON(github.event.inputs.aw_context || '{}').ref }}
      path: target
      persist-credentials: false
      fetch-depth: 0
  - name: Install socratic skill
    run: |
      set -uo pipefail
      tmp=$(mktemp -d)
      git clone --depth 1 https://github.com/LLM-Coding/Semantic-Anchors "$tmp" || \
        echo "[mm-socratic-ans] skill clone failed" >&2
      mkdir -p "$HOME/.claude/skills"
      src="$tmp/skill/socratic-code-theory-recovery"
      if [ -d "$src" ]; then
        rm -rf "$HOME/.claude/skills/socratic-code-theory-recovery"
        cp -r "$src" "$HOME/.claude/skills/socratic-code-theory-recovery"
      else
        echo "[mm-socratic-ans] skill dir not found in repo — answering will be unavailable" >&2
      fi
  - name: Restore phase-1 tree and answer the OPEN leaves
    env:
      ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
      ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
      GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      REPO: ${{ github.repository }}
    run: |
      set -uo pipefail
      OUT=/tmp/gh-aw/out
      mkdir -p "$OUT"
      cd "$GITHUB_WORKSPACE/target"
      # Restore phase 1's QUESTION_TREE/OPEN_QUESTIONS from its artifact (run_id is
      # carried in the staged `tree` input the engine materialized for this leg).
      P1_RUN=$(python3 -c "import json; d=json.load(open('/tmp/gh-aw/task-context.json')); print((d.get('inputs',{}).get('tree') or {}).get('run_id',''))" 2>/dev/null || true)
      if [ -n "$P1_RUN" ]; then
        gh run download "$P1_RUN" --repo "$REPO" -n mm-tree-socratic-phase1 -D . || \
          echo "[mm-socratic-ans] could not download phase-1 tree (run $P1_RUN)" >&2
      fi
      # The AUTOMATED answering pass: research + fill the OPEN_QUESTIONS in place.
      claude -p "we used socratic code-theory recovery and it created open questions in OPEN_QUESTIONS*.adoc. Do the best you can, reading code and the web as needed, to answer the questions in place. If you can't answer one, defer it. Do not ask anything." \
        --permission-mode bypassPermissions || \
        echo "[mm-socratic-ans] answering exited non-zero (packaging whatever exists)" >&2
      # Stage the answered tree (QUESTION_TREE + the now-answered OPEN_QUESTIONS).
      cp -a QUESTION_TREE-*.adoc OPEN_QUESTIONS-*.adoc "$OUT"/ 2>/dev/null || true
      python3 - "$OUT" > /tmp/gh-aw/evidence.json <<'PY'
      import json, os, sys
      root = sys.argv[1]
      files = []
      for dp, _, fns in os.walk(root):
          for fn in fns:
              ap = os.path.join(dp, fn)
              files.append({"path": os.path.relpath(ap, root),
                            "bytes": os.path.getsize(ap)})
      json.dump({"method": "socratic:answering",
                 "run_id": os.environ.get("GITHUB_RUN_ID", ""),
                 "files": files}, sys.stdout)
      PY
      cat /tmp/gh-aw/evidence.json
post-steps:
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
  - name: Upload mm-tree-socratic-answering artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: mm-tree-socratic-answering
      path: /tmp/gh-aw/out
      include-hidden-files: true   # codeset writes .claude/ (dotfiles); v4 excludes them by default
      if-no-files-found: warn
timeout-minutes: 45
---

# MM Socratic Answering Agent — auto-answer the OPEN leaves

The automated answering pass already ran in the setup steps: it restored phase 1's
Question Tree, answered the OPEN leaves in `OPEN_QUESTIONS-*.adoc` (code + web
research, no human input), staged the answered tree into `/tmp/gh-aw/out`, and
seeded `/tmp/gh-aw/evidence.json`.

## Task context

Read `/tmp/gh-aw/task-context.json`:
- `pr`, `iteration`, `feedback`
- `inputs.tree`: phase-1 evidence `{run_id, files}` — its `run_id` named the
  artifact that was restored.

## Your job (verify-and-repair only)

1. Confirm `/tmp/gh-aw/evidence.json` is valid JSON with a non-empty `run_id`
   and a `files` array.
2. Read the staged `OPEN_QUESTIONS-*.adoc` in `/tmp/gh-aw/out`. Ensure every OPEN
   leaf now has an answer or an explicit `(deferred)` marker — never leave a
   `_(write here)_` blank. If any remain, answer them from the code/diff (cite
   `file:line`) or mark them deferred, and re-save the file.
3. If the manifest does not reflect the files on disk, regenerate
   `/tmp/gh-aw/evidence.json` from the real contents of `/tmp/gh-aw/out`,
   keeping `run_id` = `GITHUB_RUN_ID`.
4. Do NOT post comments or touch GitHub. Phase 2 downloads your
   `mm-tree-socratic-answering` artifact by `run_id` and synthesizes the docs.
