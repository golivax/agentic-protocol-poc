#!/usr/bin/env bash
# End-to-end engine lifecycle for a fan-out instance, all local (ENGINE_LOCAL).
set -euo pipefail
cd "$(dirname "$0")/.."
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "ok: $1"; }; bad(){ FAIL=$((FAIL+1)); echo "FAIL: $1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }
export ENGINE_LOCAL=1 GITHUB_REPOSITORY="golivax/agentic-protocol-poc" PR=80 PR_HEAD_SHA="e2esha"
WORK=$(mktemp -d); git init -q --bare "$WORK/origin.git"; export STATE_REMOTE="$WORK/origin.git"
source .github/agent-factory/engine/lib.sh
PROTO=.github/agent-factory/protocols/multi-grumpy/protocol.json
PASSV="$WORK/pass.json"; echo '{"results":[{"check":"x","pass":true,"feedback":""}]}' > "$PASSV"

# plan: fanout start seeds both branches
A=$(.github/agent-factory/engine/next.py "$WORK/p" pr-80 "$PROTO" start e2esha)
check "e2e: run-fanout" '[ "$(jq -r .action <<<"$A")" = run-fanout ]'

# advance each branch to done (grumpy publishes grumpy; security publishes security)
BRANCH=grumpy   .github/agent-factory/engine/advance.py "$WORK/ag" pr-80 "$PROTO" "$PASSV" tests/fixtures/evidence-complete.json >/dev/null 2>&1
BRANCH=security .github/agent-factory/engine/advance.py "$WORK/as" pr-80 "$PROTO" "$PASSV" tests/fixtures/evidence-security.json >/dev/null 2>&1
state_checkout "$WORK/v"
check "e2e: grumpy done"   '[ "$(yq -r .state "$WORK/v/multi-grumpy/pr-80/grumpy.yaml")" = done ]'
check "e2e: security done" '[ "$(yq -r .state "$WORK/v/multi-grumpy/pr-80/security.yaml")" = done ]'

# join: both done → aggregate success
OUT=$(.github/agent-factory/engine/join.py "$WORK/j" pr-80 "$PROTO" 2>&1)
check "e2e: join → success" 'grep -q "check-run multi-grumpy sha=e2esha status=completed conclusion=success" <<<"$OUT"'

echo "-----"; echo "fanout-e2e: $PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]
