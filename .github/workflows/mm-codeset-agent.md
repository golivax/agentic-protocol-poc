---
name: "MM Codeset Agent (protocol leg: recover/codeset)"
run-name: "MM Codeset Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
# INFRA PREREQUISITE: this leg runs `python -m codeset` against the PR head.
# Python 3.10+, git, and the `claude` CLI (set up by the compiled lock; codeset
# shells out to it for synthesis) must be present, plus the ANTHROPIC_* secrets.
# codeset-vibing is installed from source in a setup step (it is not on PyPI).
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
  - name: Install codeset-vibing
    run: |
      # codeset-vibing is not on PyPI — install from source. It shells out to the
      # claude CLI (already installed by the compiled lock) for synthesis.
      python3 -m pip install --quiet "git+https://github.com/PGCodeLLM/codeset-vibing.git" || \
        echo "[mm-codeset] codeset-vibing install failed — python -m codeset will be unavailable" >&2
  - name: Run codeset and stage output
    env:
      ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
      ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
    run: |
      set -uo pipefail
      OUT=/tmp/gh-aw/out
      mkdir -p "$OUT"
      cd "$GITHUB_WORKSPACE/target"
      # The real method: codeset mines git history + AST + synthesizes per-file
      # knowledge, writing AGENTS.md/CLAUDE.md/.claude/docs/* + build.log.
      python3 -m codeset . --out "$OUT" 2>&1 | tee "$OUT/build.log" || \
        echo "[mm-codeset] codeset exited non-zero (packaging whatever exists)" >&2
      python3 - "$OUT" > /tmp/gh-aw/evidence.json <<'PY'
      import json, os, sys
      root = sys.argv[1]
      files = []
      for dp, _, fns in os.walk(root):
          for fn in fns:
              ap = os.path.join(dp, fn)
              files.append({"path": os.path.relpath(ap, root),
                            "bytes": os.path.getsize(ap)})
      json.dump({"method": "codeset-vibing",
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
  - name: Upload mm-tree-codeset artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: mm-tree-codeset
      path: /tmp/gh-aw/out
      if-no-files-found: warn
timeout-minutes: 45
---

# MM Codeset Agent — codeset-vibing mental model

The method (`python -m codeset .`) runs in the workflow setup steps and stages
`AGENTS.md`, `CLAUDE.md`, `.claude/docs/{knowledge.json,get_context.py}`, and
`build.log` into `/tmp/gh-aw/out`, plus writes `/tmp/gh-aw/evidence.json`.

## Task context

Read `/tmp/gh-aw/task-context.json` (`pr`, `iteration`, `feedback`).

## Your job (verify-and-repair only)

1. Confirm `/tmp/gh-aw/evidence.json` is valid JSON with a non-empty `run_id`
   and a `files` array.
2. Confirm the staged tree at `/tmp/gh-aw/out` contains `AGENTS.md`,
   `.claude/docs/knowledge.json`, and `.claude/docs/get_context.py` — the entries
   the `codeset-artifacts` check requires in the manifest.
3. If the manifest does not reflect the files actually on disk, regenerate
   `/tmp/gh-aw/evidence.json` from the real contents of `/tmp/gh-aw/out`
   (paths relative to that dir), keeping `run_id` = `GITHUB_RUN_ID`.
4. Do NOT post comments or touch GitHub. The engine publishes after checks pass;
   the `combine` merge hook pulls the `mm-tree-codeset` artifact by `run_id`.
