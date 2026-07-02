---
name: "MM Socratic Phase-2 Interactive Agent (protocol sub-state: recover/socratic/phase2)"
run-name: "MM Socratic Phase-2 Interactive Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
# INFRA PREREQUISITE: phase 2 of the socratic skill for the INTERACTIVE protocol.
# The OPEN leaves were answered by a human via the question issue (the engine gate),
# so the answers arrive in aw_context.inputs.answers ({questions, answers}); this
# agent restores phase 1's tree (mm-tree-socratic-phase1), writes those answers into
# OPEN_QUESTIONS, then synthesizes. Needs actions:read to download the phase-1
# artifact. See docs/STATUS.md.
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
        echo "[mm-socratic-2i] skill clone failed" >&2
      mkdir -p "$HOME/.claude/skills"
      src="$tmp/skill/socratic-code-theory-recovery"
      if [ -d "$src" ]; then
        rm -rf "$HOME/.claude/skills/socratic-code-theory-recovery"
        cp -r "$src" "$HOME/.claude/skills/socratic-code-theory-recovery"
      else
        echo "[mm-socratic-2i] skill dir not found in repo — phase 2 will be unavailable" >&2
      fi
  - name: Restore phase-1 tree, apply human answers, run phase 2
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
      # Restore phase 1's QUESTION_TREE/OPEN_QUESTIONS from its artifact (run_id in
      # the staged `tree` input). There is NO answering agent in the interactive
      # protocol — the answers came from the human via the question issue (gate).
      P1_RUN=$(python3 -c "import json; d=json.load(open('/tmp/gh-aw/task-context.json')); print((d.get('inputs',{}).get('tree') or {}).get('run_id',''))" 2>/dev/null || true)
      if [ -n "$P1_RUN" ]; then
        gh run download "$P1_RUN" --repo "$REPO" -n mm-tree-socratic-phase1 -D . || \
          echo "[mm-socratic-2i] could not download phase-1 tree (run $P1_RUN)" >&2
      fi
      # The human answers (gate output) are in task-context .inputs.answers as
      # {questions, answers: {<id>: <value>}}. Apply them into OPEN_QUESTIONS, then
      # synthesize.
      claude -p "we used socratic code-theory recovery. The human's answers to the OPEN leaves are in /tmp/gh-aw/task-context.json under .inputs.answers (an object {questions, answers:{<id>:<value>}}). Apply each answer to the matching leaf in OPEN_QUESTIONS*.adoc (mark any without an answer as (deferred)), then continue with phase 2: synthesize docs/specs (prd, use-cases, adrs) and docs/arc42 from the answered Question Tree. Do not ask anything." \
        --permission-mode bypassPermissions || \
        echo "[mm-socratic-2i] phase 2 exited non-zero (packaging whatever exists)" >&2
      # Stage the socratic leg's final tree: tree files + synthesized docs.
      cp -a QUESTION_TREE-*.adoc OPEN_QUESTIONS-*.adoc "$OUT"/ 2>/dev/null || true
      [ -d docs ] && cp -a docs "$OUT"/ || true
      ( cd "$OUT" && find . -type f | sort > MANIFEST.txt )
      python3 - "$OUT" > /tmp/gh-aw/evidence.json <<'PY'
      import json, os, sys
      root = sys.argv[1]
      files = []
      for dp, _, fns in os.walk(root):
          for fn in fns:
              ap = os.path.join(dp, fn)
              files.append({"path": os.path.relpath(ap, root),
                            "bytes": os.path.getsize(ap)})
      json.dump({"method": "socratic:phase2",
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
  - name: Upload mm-tree-socratic artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: mm-tree-socratic
      path: /tmp/gh-aw/out
      include-hidden-files: true
      if-no-files-found: warn
timeout-minutes: 45
---

# MM Socratic Phase-2 Interactive Agent — synthesize from human answers

Phase 2 ran in the setup steps: it restored phase 1's Question Tree, applied the
human's answers (from the question issue) into `OPEN_QUESTIONS-*.adoc`, synthesized
`docs/specs/*` + `docs/arc42/*`, and staged the leg's final tree (tree files + docs
+ `MANIFEST.txt`) into `/tmp/gh-aw/out`, plus seeded `/tmp/gh-aw/evidence.json`.

## Task context

Read `/tmp/gh-aw/task-context.json`:
- `pr`, `iteration`, `feedback`
- `inputs.tree`: phase-1 evidence `{run_id, questions, files}` — its `run_id` named
  the phase-1 artifact that was restored.
- `inputs.answers`: the gate's answers `{questions, answers:{<id>:<value>}}` — the
  human's responses from the question issue.

## Your job — ensure a complete, answer-grounded doc set

1. Verify `OPEN_QUESTIONS-*.adoc` in `/tmp/gh-aw/out` reflects every answer in
   `inputs.answers` (each answered leaf carries its answer; unanswered ones marked
   `(deferred)`). Fill any gaps from `inputs.answers`, then re-synthesize affected
   sections.
2. Confirm `/tmp/gh-aw/out` contains the full set the `socratic-docs-present` check
   requires: `docs/specs/prd-*.adoc`, `docs/specs/use-cases-*.adoc`, at least one
   `docs/specs/adrs/*.adoc`, and `docs/arc42/arc42-*.adoc`. Generate any missing,
   grounded in the tree + answers (cite `file:line` or mark `(team answer)`).
3. Refresh `MANIFEST.txt` and regenerate `/tmp/gh-aw/evidence.json` so `files[].path`
   matches `/tmp/gh-aw/out`, keeping `run_id` = `GITHUB_RUN_ID`.
4. Do NOT post comments or touch GitHub. The `combine` merge hook pulls the
   `mm-tree-socratic` artifact by `run_id` and pushes it under `socratic/`.
