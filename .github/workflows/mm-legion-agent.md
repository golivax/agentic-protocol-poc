---
name: "MM Legion Agent (protocol leg: recover/legion)"
run-name: "MM Legion Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
# Custom Anthropic-compatible endpoint (public, Funnel-exposed). engine.env (not
# top-level env) is forwarded to the CLI subprocess by gh-aw.
#
# INFRA PREREQUISITE: this leg runs a real `/legion:map` against the PR head. The
# runner must have the `claude` CLI on PATH and the legion skill installed
# (https://github.com/9thLevelSoftware/legion), plus the ANTHROPIC_* secrets. The
# install step below is best-effort and environment-specific — adjust to your
# runner image. See docs/STATUS.md.
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
  - name: Run legion:map and stage output
    env:
      ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
      ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
    run: |
      set -uo pipefail
      OUT=/tmp/gh-aw/out
      mkdir -p "$OUT"
      cd "$GITHUB_WORKSPACE/target"
      # The real method: /legion:map writes .planning/ into the repo root.
      claude -p "/legion:map" --permission-mode bypassPermissions || \
        echo "[mm-legion] legion:map exited non-zero (continuing to package whatever exists)" >&2
      if [ -d .planning ]; then
        cp -a .planning/. "$OUT"/
      fi
      # Build the evidence manifest (run_id + files) the engine's check + combine
      # hook consume. Form-only — the check verifies presence, not substance.
      python3 - "$OUT" > /tmp/gh-aw/evidence.json <<'PY'
      import json, os, sys
      root = sys.argv[1]
      files = []
      for dp, _, fns in os.walk(root):
          for fn in fns:
              ap = os.path.join(dp, fn)
              files.append({"path": os.path.relpath(ap, root),
                            "bytes": os.path.getsize(ap)})
      json.dump({"method": "legion:map",
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
  - name: Upload mm-tree-legion artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: mm-tree-legion
      path: /tmp/gh-aw/out
      if-no-files-found: warn
timeout-minutes: 45
---

# MM Legion Agent — legion:map mental model

The heavy lifting (running `/legion:map` against the PR head and staging
`.planning/` into `/tmp/gh-aw/out`) happens in the workflow setup steps, which
also write `/tmp/gh-aw/evidence.json` (a `run_id` + file manifest).

## Task context

Read `/tmp/gh-aw/task-context.json` (`pr`, `iteration`, `feedback`).

## Your job (verify-and-repair only)

1. Confirm `/tmp/gh-aw/evidence.json` exists and is valid JSON with a non-empty
   `run_id` and a `files` array.
2. Confirm the staged tree at `/tmp/gh-aw/out` contains the core legion-map
   artifacts: `CODEBASE.md`, `codebase/index.jsonl`, `codebase/symbols.json`,
   and `config/directory-mappings.yaml`. These are what the `legion-artifacts`
   check looks for in the manifest.
3. If the manifest is missing those entries but the files exist on disk,
   regenerate `/tmp/gh-aw/evidence.json` so its `files[].path` list reflects the
   actual contents of `/tmp/gh-aw/out` (paths relative to that dir). Keep
   `run_id` set to the `GITHUB_RUN_ID`.
4. Do NOT post comments or touch GitHub. Your only output is the evidence file;
   the engine publishes after checks pass, and the `combine` merge hook pulls the
   `mm-tree-legion` artifact by `run_id`.
