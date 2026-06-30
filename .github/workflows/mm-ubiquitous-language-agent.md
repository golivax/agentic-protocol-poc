---
name: "MM Ubiquitous-Language Agent (protocol leg: recover/ubiquitous-language)"
run-name: "MM Ubiquitous-Language Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
# INFRA PREREQUISITE: this leg runs the domain-modeling skill against the PR head.
# The runner must have the `claude` CLI (set up by the compiled lock) + the
# ANTHROPIC_* secrets. The domain-modeling skill is installed from
# mattpocock/skills in a setup step. See docs/STATUS.md.
permissions:
  contents: read
  pull-requests: read
safe-outputs:
  # Suppress gh-aw's default auto create-issue (these agents output FILES via
  # artifacts, not GitHub issues). gh-aw has no "declare nothing" option — a
  # non-system output must be declared to drop the auto create-issue, so use the
  # most inert one (add-labels: it targets the triggering issue/PR, of which a
  # workflow_dispatch run has none, so it can never act; the prompt also forbids
  # GitHub side-effects). Disable the other issue-creating sinks too.
  # threat-detection:false because declaring safe-outputs would otherwise require
  # the agent sandbox (disabled here).
  add-labels:
  noop: false
  missing-tool: false
  missing-data: false
  threat-detection: false
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
  - name: Install domain-modeling skill
    run: |
      # Install the domain-modeling skill into ~/.claude/skills from mattpocock/skills
      # (the claude CLI is set up by the compiled lock). The repo nests skills under
      # skills/<category>/<name>; copy the specific one so SKILL.md lands at
      # ~/.claude/skills/domain-modeling/SKILL.md.
      set -uo pipefail
      tmp=$(mktemp -d)
      git clone --depth 1 https://github.com/mattpocock/skills "$tmp" || \
        echo "[mm-ubiq] skill clone failed" >&2
      mkdir -p "$HOME/.claude/skills"
      src="$tmp/skills/engineering/domain-modeling"
      if [ -d "$src" ]; then
        rm -rf "$HOME/.claude/skills/domain-modeling"
        cp -r "$src" "$HOME/.claude/skills/domain-modeling"
      else
        echo "[mm-ubiq] skill dir not found in repo — /domain-modeling will be unavailable" >&2
      fi
  - name: Run domain-modeling and stage output
    env:
      ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
      ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
    run: |
      set -uo pipefail
      OUT=/tmp/gh-aw/out
      mkdir -p "$OUT"
      cd "$GITHUB_WORKSPACE/target"
      # The real method: derive the domain glossary (CONTEXT.md) from code only.
      claude -p "/domain-modeling — Derive the domain glossary for this repo from the code only. Don't ask me any clarifying questions; when a term is ambiguous, pick the most defensible canonical name based on the code and note alternatives under _Avoid_. Generate CONTEXT.md only — do not create or offer any ADRs, and don't write a CONTEXT-MAP.md (treat this as a single context). If you hit a genuine ambiguity you can't resolve from code, make a reasonable choice and move on rather than stopping." \
        --permission-mode bypassPermissions || \
        echo "[mm-ubiq] domain-modeling exited non-zero (packaging whatever exists)" >&2
      cp -a CONTEXT.md "$OUT"/ 2>/dev/null || true
      python3 - "$OUT" > /tmp/gh-aw/evidence.json <<'PY'
      import json, os, sys
      root = sys.argv[1]
      files = []
      for dp, _, fns in os.walk(root):
          for fn in fns:
              ap = os.path.join(dp, fn)
              files.append({"path": os.path.relpath(ap, root),
                            "bytes": os.path.getsize(ap)})
      json.dump({"method": "domain-modeling",
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
  - name: Upload mm-tree-ubiquitous-language artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: mm-tree-ubiquitous-language
      path: /tmp/gh-aw/out
      if-no-files-found: warn
timeout-minutes: 30
---

# MM Ubiquitous-Language Agent — domain glossary (CONTEXT.md)

The method (`/domain-modeling`) runs in the workflow setup steps and stages
`CONTEXT.md` (the code-derived domain glossary) into `/tmp/gh-aw/out`, plus writes
`/tmp/gh-aw/evidence.json` (a `run_id` + file manifest).

## Task context

Read `/tmp/gh-aw/task-context.json` (`pr`, `iteration`, `feedback`).

## Your job (verify-and-repair only)

1. Confirm `/tmp/gh-aw/evidence.json` is valid JSON with a non-empty `run_id`
   and a `files` array.
2. Confirm the staged tree at `/tmp/gh-aw/out` contains `CONTEXT.md` — what the
   `ubiquitous-language-present` check requires. If it is missing but a
   `CONTEXT.md` exists in the checked-out repo, copy it into `/tmp/gh-aw/out`.
3. Confirm `CONTEXT.md` is a glossary only (terms + definitions + `_Avoid_`
   alternatives) with no ADRs and no `CONTEXT-MAP.md`. If `/domain-modeling`
   produced extra files, remove them from `/tmp/gh-aw/out` so only `CONTEXT.md`
   ships.
4. If the manifest does not reflect the files on disk, regenerate
   `/tmp/gh-aw/evidence.json` from the real contents of `/tmp/gh-aw/out`,
   keeping `run_id` = `GITHUB_RUN_ID`.
5. Do NOT post comments or touch GitHub. The engine publishes after checks pass;
   the `combine` merge hook pulls the `mm-tree-ubiquitous-language` artifact by
   `run_id` and pushes it under `ubiquitous-language/`.
