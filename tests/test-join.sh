#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "ok: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL: $1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

export ENGINE_LOCAL=1
export GITHUB_REPOSITORY="golivax/agentic-protocol-poc"
WORK=$(mktemp -d)
git init -q --bare "$WORK/origin.git"
export STATE_REMOTE="$WORK/origin.git"
export PR_HEAD_SHA="joinsha"
source .github/agent-factory/engine/lib.sh
JOIN=.github/agent-factory/engine/join.sh
PROTO=.github/agent-factory/protocols/multi-grumpy/protocol.json

seed() {  # seed <dir> <pr> <grumpy-state> <security-state>
  local d="$1" pr="$2" g="$3" s="$4"
  state_checkout "$d"; mkdir -p "$d/multi-grumpy/$pr"
  yq -n ".protocol=\"multi-grumpy\"|.instance=\"$pr\"|.state=\"$g\"|.iteration=1|.gates={}|.history=[]" > "$d/multi-grumpy/$pr/grumpy.yaml"
  yq -n ".protocol=\"multi-grumpy\"|.instance=\"$pr\"|.state=\"$s\"|.iteration=1|.gates={}|.history=[]" > "$d/multi-grumpy/$pr/security.yaml"
  yq -n '.protocol="multi-grumpy"|.instance="'"$pr"'"|.head_sha="joinsha"|.joined=false' > "$d/multi-grumpy/$pr/_instance.yaml"
  cas_push "$d" "seed $pr g=$g s=$s"
}

# both done → aggregate success, joined=true
seed "$WORK/j1" pr-1 done done
OUT=$(PR=1 "$JOIN" "$WORK/j1b" pr-1 "$PROTO" 2>&1)
check "all done → check-run success" 'grep -q "check-run multi-grumpy sha=joinsha status=completed conclusion=success" <<<"$OUT"'
check "all done → comment shows complete headline" 'grep -q "Review complete — published" <<<"$OUT"'
check "all done → comment shows both sections"      'grep -q "\*\*grumpy\*\*" <<<"$OUT" && grep -q "\*\*security\*\*" <<<"$OUT"'
state_checkout "$WORK/j1v"
check "all done → joined=true" '[ "$(yq -r .joined "$WORK/j1v/multi-grumpy/pr-1/_instance.yaml")" = true ]'

# one failed → aggregate failure
seed "$WORK/j2" pr-2 done failed
OUT=$(PR=2 "$JOIN" "$WORK/j2b" pr-2 "$PROTO" 2>&1)
check "one failed → check-run failure" 'grep -q "check-run multi-grumpy sha=joinsha status=completed conclusion=failure" <<<"$OUT"'
check "one failed → comment shows incomplete headline" 'grep -q "Review incomplete — a branch could not complete" <<<"$OUT"'
check "one failed → comment shows both sections" 'grep -q "\*\*grumpy\*\*" <<<"$OUT" && grep -q "\*\*security\*\*" <<<"$OUT"'

# not all terminal → no aggregate yet, joined stays false
seed "$WORK/j3" pr-3 done review
OUT=$(PR=3 "$JOIN" "$WORK/j3b" pr-3 "$PROTO" 2>&1)
check "partial → no completed aggregate" '! grep -q "status=completed" <<<"$OUT"'
state_checkout "$WORK/j3v"
check "partial → joined stays false" '[ "$(yq -r .joined "$WORK/j3v/multi-grumpy/pr-3/_instance.yaml")" = false ]'

# idempotent: a second join after joined=true is a no-op
seed "$WORK/j4" pr-4 done done
PR=4 "$JOIN" "$WORK/j4b" pr-4 "$PROTO" >/dev/null 2>&1
OUT=$(PR=4 "$JOIN" "$WORK/j4c" pr-4 "$PROTO" 2>&1)
check "idempotent: second join is a no-op" 'grep -qi "already joined" <<<"$OUT"'

echo "-----"
echo "join tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
