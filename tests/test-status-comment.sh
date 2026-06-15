#!/usr/bin/env bash
# Unit tests for lib.sh render_fanout_status_body — the combined fan-out PR
# progress-comment body. Pure renderer: reads branch state files, echoes a string.
set -euo pipefail
cd "$(dirname "$0")/.."
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "ok: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL: $1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

export ENGINE_LOCAL=1 GITHUB_REPOSITORY="golivax/agentic-protocol-poc"
source .github/agent-factory/engine/lib.sh
PROTO=.github/agent-factory/protocols/multi-grumpy/protocol.json
WORK=$(mktemp -d)

# seed_branch <dir> <instance> <branch> <state> <history-json>
# Writes a JSON state file (JSON is valid YAML; the renderer reads it with yq).
seed_branch() {
  local d="$1" inst="$2" b="$3" st="$4" hist="$5"
  mkdir -p "$d/multi-grumpy/$inst"
  jq -n --arg inst "$inst" --arg st "$st" --argjson h "$hist" \
    '{protocol:"multi-grumpy", instance:$inst, state:$st, iteration:1, gates:{}, history:$h}' \
    > "$d/multi-grumpy/$inst/$b.yaml"
}

# both branches present: grumpy passed iter 1, security failed iter 1 → in progress
seed_branch "$WORK/a" pr-80 grumpy   review '[{"iteration":1,"feedback":""}]'
seed_branch "$WORK/a" pr-80 security review '[{"iteration":1,"feedback":"sec: bad anchor"}]'
BODY=$(render_fanout_status_body "$WORK/a" multi-grumpy pr-80 "$PROTO")
check "render: grumpy section present"      'grep -q "\*\*grumpy\*\*" <<<"$BODY"'
check "render: security section present"     'grep -q "\*\*security\*\*" <<<"$BODY"'
check "render: passed checklist line"        'grep -q "iteration 1/3 — all checks passed" <<<"$BODY"'
check "render: failed checklist line w/ fb"  'grep -q "iteration 1/3 — sec: bad anchor" <<<"$BODY"'
check "render: tree/ link, not blob"         'grep -q "tree/agentic-state/multi-grumpy/pr-80" <<<"$BODY" && ! grep -q "blob" <<<"$BODY"'
check "render: link has no .yaml suffix"     '! grep -q "pr-80.yaml" <<<"$BODY"'
check "render: in-progress headline"         'grep -q "Review in progress" <<<"$BODY"'

# both done → complete headline
seed_branch "$WORK/b" pr-81 grumpy   done '[{"iteration":1,"feedback":""}]'
seed_branch "$WORK/b" pr-81 security done '[{"iteration":1,"feedback":""}]'
BODY=$(render_fanout_status_body "$WORK/b" multi-grumpy pr-81 "$PROTO")
check "render: complete headline" 'grep -q "Review complete" <<<"$BODY"'

# done + failed → incomplete headline
seed_branch "$WORK/c" pr-82 grumpy   done   '[{"iteration":1,"feedback":""}]'
seed_branch "$WORK/c" pr-82 security failed '[{"iteration":3,"feedback":"exhausted"}]'
BODY=$(render_fanout_status_body "$WORK/c" multi-grumpy pr-82 "$PROTO")
check "render: incomplete headline" 'grep -q "Review incomplete" <<<"$BODY"'

# only grumpy seeded (early/partial) → security renders _pending_, grumpy empty history note
seed_branch "$WORK/d" pr-83 grumpy review '[]'
BODY=$(render_fanout_status_body "$WORK/d" multi-grumpy pr-83 "$PROTO")
check "render: missing branch file → _pending_"     'grep -q "_pending_" <<<"$BODY"'
check "render: empty history → _no iterations yet_" 'grep -q "_no iterations yet_" <<<"$BODY"'

echo "-----"
echo "status-comment tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
