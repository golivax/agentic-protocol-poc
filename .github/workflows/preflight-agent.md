---
name: "Preflight Agent (protocol state: preflight)"
run-name: "Preflight Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
# Custom Anthropic-compatible endpoint (public, Funnel-exposed). The endpoint
# accepts Bearer auth and needs no token-steering, so we bypass AWF's api-proxy
# (sandbox.agent: false) and let the claude CLI call it directly. engine.env is
# used (not top-level env) because gh-aw forwards engine.env to the CLI subprocess.
permissions:
  contents: read
  pull-requests: read
  issues: read
tools:
  cli-proxy: true
  edit: true
  bash:
    - "cat:*"
safe-outputs:
  staged: true
  threat-detection: false
pre-agent-steps:
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Prefetch PR + scope adherence checks (changed-files only)
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,body,files,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
      # Scope which adherence checks to judge: only those whose artifact FILE is in the PR diff.
      # Read the artifact text from the committed file so the agent can judge against it.
      python3 - "$REPO" <<'PY'
      import json, os, subprocess, sys, re
      repo = sys.argv[1]
      pr = json.load(open('/tmp/gh-aw/agent/pr.json'))
      head = pr.get('headRefOid') or ''
      files = [f['filename'] for f in pr.get('files', []) if f.get('status') != 'removed']
      SPEC = re.compile(r'(^|/)docs/(superpowers/)?specs/|(^|/)(SPEC|REQUIREMENTS)\.md$|^specs/', re.I)
      PLAN = re.compile(r'(^|/)docs/(superpowers/)?plans?/|(^|/)PLAN\.md$|^plans?/', re.I)
      def read(path):
          out = subprocess.run(['gh','api',f'repos/{repo}/contents/{path}?ref={head}','--jq','.content'],
                               capture_output=True, text=True)
          if out.returncode != 0 or not out.stdout.strip(): return ''
          import base64
          try: return base64.b64decode(out.stdout.strip()).decode('utf-8')[:12000]
          except Exception: return ''
      ai = []
      spec_hit = next((f for f in files if SPEC.search(f)), None)
      plan_hit = next((f for f in files if PLAN.search(f)), None)
      open('/tmp/gh-aw/agent/spec.txt','w').write(read(spec_hit) if spec_hit else '')
      open('/tmp/gh-aw/agent/plan.txt','w').write(read(plan_hit) if plan_hit else '')
      if spec_hit: ai.append('spec-adherence')
      if plan_hit: ai.append('plan-adherence')
      open('/tmp/gh-aw/agent/ai-checks.json','w').write(json.dumps(ai))
      PY
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

# Preflight Gate — adherence judgment only

You judge ONLY spec/plan adherence. Deterministic facts (spec/plan/docs/tests
presence) are computed by the engine's checks — do NOT recompute them.

1. Read `/tmp/gh-aw/agent/ai-checks.json` (the check ids to judge). If it is `[]`,
   write evidence with an empty `checks` list (see step 4) — there is no artifact
   to judge against — and stop.
2. Read `/tmp/gh-aw/agent/pr.diff`, `/tmp/gh-aw/agent/spec.txt`, `/tmp/gh-aw/agent/plan.txt`,
   and the engine's `task-context.json` (`pr`, `iteration`, `feedback` — fold prior
   feedback into this pass).
3. For each requested id, judge the diff against the located artifact text ONLY
   (never infer an artifact):
   - `spec-adherence`: does the diff achieve what `spec.txt` requires?
   - `plan-adherence`: does the diff follow `plan.txt`?
   status: pass = adheres, warn = partial, fail = does not. Base every verdict on
   real evidence from the diff.
4. Write `/tmp/gh-aw/evidence.json` (the engine evidence path) as ONE JSON object:
   `{"checks":[{"id":"<id>","status":"pass|fail|warn","summary":"…","evidence":[{"label":"…","detail":"…"}],"remediation":"…"}], "examined":["<files you read in the diff>"]}`
   Include one `checks` entry per requested id; `examined` lists the changed files
   you inspected. Write nothing else.
