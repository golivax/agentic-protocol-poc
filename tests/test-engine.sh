#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "ok: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL: $1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

export ENGINE_LOCAL=1
export GITHUB_REPOSITORY="golivax/agentic-protocol-poc"

# fresh local "origin" per run
WORK=$(mktemp -d)
git init -q --bare "$WORK/origin.git"
export STATE_REMOTE="$WORK/origin.git"

source .github/engine/lib.sh

# --- lib: state checkout creates the branch on a bare origin
S1="$WORK/s1"
state_checkout "$S1"
check "state branch created on origin" \
  "git ls-remote --heads '$STATE_REMOTE' agentic-state | grep -q agentic-state"

# --- lib: cas push happy path
mkdir -p "$S1/grumpy" && echo "state: review" > "$S1/grumpy/pr-1.yaml"
cas_push "$S1" "init pr-1"
check "cas push lands" \
  "git clone -q --branch agentic-state '$STATE_REMOTE' '$WORK/verify1' && grep -q 'state: review' '$WORK/verify1/grumpy/pr-1.yaml'"

# --- lib: cas push retries via rebase when origin moved (disjoint files)
S2="$WORK/s2"; state_checkout "$S2"
mkdir -p "$S2/grumpy" && echo "state: review" > "$S2/grumpy/pr-2.yaml"
echo "state: publish" > "$S1/grumpy/pr-1.yaml" && cas_push "$S1" "advance pr-1"   # origin moves
cas_push "$S2" "init pr-2"                                                        # stale clone must rebase+push
check "cas push survives concurrent writer" \
  "git clone -q --branch agentic-state '$STATE_REMOTE' '$WORK/verify2' && \
   grep -q 'state: publish' '$WORK/verify2/grumpy/pr-1.yaml' && \
   grep -q 'state: review' '$WORK/verify2/grumpy/pr-2.yaml'"

echo "-----"
echo "engine tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
