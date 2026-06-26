---
name: "MM Socratic Phase-1 Agent (protocol sub-state: recover/socratic/phase1)"
run-name: "MM Socratic Phase-1 Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
# INFRA PREREQUISITE: runs phase 1 of the socratic-code-theory-recovery skill
# (https://github.com/LLM-Coding/Semantic-Anchors) against the PR head. The runner
# must have the `claude` CLI with that skill installed + the ANTHROPIC_* secrets.
# See docs/STATUS.md.
permissions:
  contents: read
  pull-requests: read
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
      # Install the socratic-code-theory-recovery skill into ~/.claude/skills from
      # the Semantic-Anchors repo (the claude CLI is set up by the compiled lock).
      set -uo pipefail
      tmp=$(mktemp -d)
      git clone --depth 1 https://github.com/LLM-Coding/Semantic-Anchors "$tmp" || \
        echo "[mm-socratic-1] skill clone failed" >&2
      mkdir -p "$HOME/.claude/skills"
      # The repo's skill/ holds one subdir per skill; copy the specific one so its
      # SKILL.md lands at ~/.claude/skills/socratic-code-theory-recovery/SKILL.md.
      src="$tmp/skill/socratic-code-theory-recovery"
      if [ -d "$src" ]; then
        rm -rf "$HOME/.claude/skills/socratic-code-theory-recovery"
        cp -r "$src" "$HOME/.claude/skills/socratic-code-theory-recovery"
        ls "$HOME/.claude/skills/socratic-code-theory-recovery"
      else
        echo "[mm-socratic-1] skill dir not found in repo — phase 1 will be unavailable" >&2
      fi
  - name: Run socratic phase 1 and stage output
    env:
      ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
      ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
    run: |
      set -uo pipefail
      OUT=/tmp/gh-aw/out
      mkdir -p "$OUT"
      cd "$GITHUB_WORKSPACE/target"
      # Phase 1: build the Question Tree; surface OPEN leaves. The skill writes
      # QUESTION_TREE-*.adoc and OPEN_QUESTIONS-*.adoc into the repo root.
      claude -p "/socratic-code-theory-recovery work here and scope the entire repo. don't ask anything and give the output of phase 1" \
        --permission-mode bypassPermissions || \
        echo "[mm-socratic-1] phase 1 exited non-zero (packaging whatever exists)" >&2
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
      json.dump({"method": "socratic:phase1",
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
  - name: Upload mm-tree-socratic-phase1 artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: mm-tree-socratic-phase1
      path: /tmp/gh-aw/out
      if-no-files-found: warn
timeout-minutes: 30
---

# MM Socratic Phase-1 Agent — Question Tree & OPEN leaves

Phase 1 of the skill already ran in the setup steps and staged
`QUESTION_TREE-*.adoc` + `OPEN_QUESTIONS-*.adoc` into `/tmp/gh-aw/out`, and seeded
`/tmp/gh-aw/evidence.json` with a `run_id` + a `files` manifest. The OPEN leaves
are answered automatically by the downstream `answering` step (no human gate).

## Task context

Read `/tmp/gh-aw/task-context.json` (`pr`, `iteration`, `feedback`).

## Your job (verify-and-repair only)

1. Confirm `/tmp/gh-aw/evidence.json` is valid JSON with a non-empty `run_id`
   and a `files` array.
2. Confirm the staged tree at `/tmp/gh-aw/out` contains `QUESTION_TREE-*.adoc`
   and `OPEN_QUESTIONS-*.adoc` — what the `socratic-phase1-present` check requires.
3. If the manifest does not reflect the files actually on disk, regenerate
   `/tmp/gh-aw/evidence.json` from the real contents of `/tmp/gh-aw/out`
   (paths relative to that dir), keeping `run_id` = `GITHUB_RUN_ID`.
4. Do NOT post comments or touch GitHub. The engine dispatches the `answering`
   step next; it downloads your `mm-tree-socratic-phase1` artifact by `run_id`,
   answers the OPEN leaves automatically, and hands the answered tree to phase 2.
