---
name: "MM Socratic Phase-2 Agent (protocol sub-state: recover/socratic/phase2)"
run-name: "MM Socratic Phase-2 Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
# INFRA PREREQUISITE: runs phase 2 of the socratic-code-theory-recovery skill.
# Unlike the local socratic-runs harness, phase 2 here CANNOT `--resume` phase 1's
# session (it is a separate GitHub run on a fresh machine). Instead it restores
# phase 1's tree from the mm-tree-socratic-phase1 artifact, writes the human
# answers into OPEN_QUESTIONS, and synthesizes from those files. Needs actions:read
# to download the phase-1 artifact. See docs/STATUS.md.
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
  - name: Checkout PR head
    uses: actions/checkout@v5
    with:
      ref: refs/pull/${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}/head
      path: target
      persist-credentials: false
      fetch-depth: 0
  - name: Install socratic skill
    run: |
      set -uo pipefail
      tmp=$(mktemp -d)
      git clone --depth 1 https://github.com/LLM-Coding/Semantic-Anchors "$tmp" || \
        echo "[mm-socratic-2] skill clone failed" >&2
      mkdir -p "$HOME/.claude/skills"
      # The repo's skill/ holds one subdir per skill; copy the specific one so its
      # SKILL.md lands at ~/.claude/skills/socratic-code-theory-recovery/SKILL.md.
      src="$tmp/skill/socratic-code-theory-recovery"
      if [ -d "$src" ]; then
        rm -rf "$HOME/.claude/skills/socratic-code-theory-recovery"
        cp -r "$src" "$HOME/.claude/skills/socratic-code-theory-recovery"
      else
        echo "[mm-socratic-2] skill dir not found in repo — phase 2 will be unavailable" >&2
      fi
  - name: Restore phase-1 tree, apply answers, run phase 2
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
      # Restore phase-1's QUESTION_TREE/OPEN_QUESTIONS from its artifact (run_id is
      # carried in the staged `tree` input the engine materialized for this leg).
      P1_RUN=$(python3 -c "import json,sys; d=json.load(open('/tmp/gh-aw/task-context.json')); print((d.get('inputs',{}).get('tree') or {}).get('run_id',''))" 2>/dev/null || true)
      if [ -n "$P1_RUN" ]; then
        gh run download "$P1_RUN" --repo "$REPO" -n mm-tree-socratic-phase1 -D . || \
          echo "[mm-socratic-2] could not download phase-1 tree (run $P1_RUN)" >&2
      fi
      # Write the human answers (inputs.answers) into OPEN_QUESTIONS so phase 2 can
      # read them, then synthesize. (The agent body refines this if needed.)
      claude -p "we used socratic code-theory recovery; the OPEN_QUESTIONS*.adoc answers were supplied in /tmp/gh-aw/task-context.json under .inputs.answers — apply them, then continue with phase 2 to synthesize docs/specs and docs/arc42." \
        --permission-mode bypassPermissions || \
        echo "[mm-socratic-2] phase 2 exited non-zero (packaging whatever exists)" >&2
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
      if-no-files-found: warn
timeout-minutes: 45
---

# MM Socratic Phase-2 Agent — synthesize the documentation set

Phase 2 ran in the setup steps: it restored phase 1's Question Tree, applied the
human answers, synthesized `docs/specs/*` + `docs/arc42/*`, and staged the leg's
final tree (tree files + docs + `MANIFEST.txt`) into `/tmp/gh-aw/out`, plus seeded
`/tmp/gh-aw/evidence.json`.

## Task context

Read `/tmp/gh-aw/task-context.json`:
- `pr`, `iteration`, `feedback`
- `inputs.tree`: phase-1 evidence `{run_id, questions, files}` — its `run_id`
  named the artifact that was restored.
- `inputs.answers`: `{questions:[...], answers:{<id>:<value>}}` — the human's
  answers to the OPEN leaves.

## Your job — ensure a complete, answer-grounded doc set

1. Verify the staged `OPEN_QUESTIONS-*.adoc` reflects `inputs.answers` (each
   answered leaf carries its answer, deferred ones marked). If any answer from
   `inputs.answers` is missing in the file, write it in and re-run the synthesis
   for the affected sections.
2. Confirm `/tmp/gh-aw/out` contains the full set the `socratic-docs-present`
   check requires: `docs/specs/prd-*.adoc`, `docs/specs/use-cases-*.adoc`, at
   least one `docs/specs/adrs/*.adoc`, and `docs/arc42/arc42-*.adoc`. Generate any
   that are missing, grounded in the tree + answers (cite `file:line` or mark
   `(team answer)`; never invent facts).
3. Refresh `MANIFEST.txt` (the sorted file list) and regenerate
   `/tmp/gh-aw/evidence.json` so `files[].path` matches `/tmp/gh-aw/out`,
   keeping `run_id` = `GITHUB_RUN_ID`.
4. Do NOT post comments or touch GitHub. The `combine` merge hook pulls the
   `mm-tree-socratic` artifact by `run_id` and pushes it under `socratic/`.
