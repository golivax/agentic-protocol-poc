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

# --- next.sh: first call initializes state and emits run-agent iter 1
S3="$WORK/s3"
A=$(.github/engine/next.sh "$S3" 7 protocols/grumpy/protocol.json)
check "next: initial action is run-agent" '[ "$(jq -r .action <<<"$A")" = run-agent ]'
check "next: initial iteration is 1"      '[ "$(jq -r .iteration <<<"$A")" = 1 ]'
check "next: state file pushed"           "git clone -q --branch agentic-state '$STATE_REMOTE' '$WORK/verify3' && grep -q 'state: review' '$WORK/verify3/grumpy/pr-7.yaml'"

# --- next.sh: feedback from history is surfaced
FB="Missing: security × src/auth.js" yq -i \
  '.iteration = 2 | .history += [{"iteration": 1, "agent_run_id": "100", "feedback": strenv(FB)}]' \
  "$S3/grumpy/pr-7.yaml"
cas_push "$S3" "simulate failed iteration"
S4="$WORK/s4"
A=$(.github/engine/next.sh "$S4" 7 protocols/grumpy/protocol.json)
check "next: resumes at iteration 2"   '[ "$(jq -r .iteration <<<"$A")" = 2 ]'
check "next: carries feedback"         'jq -r .feedback <<<"$A" | grep -q "security × src/auth.js"'

# --- next.sh: terminal state halts
S5="$WORK/s5"; state_checkout "$S5"
yq -i '.state = "done"' "$S5/grumpy/pr-7.yaml" && cas_push "$S5" "simulate done"
S6="$WORK/s6"
A=$(.github/engine/next.sh "$S6" 7 protocols/grumpy/protocol.json)
check "next: terminal state halts" '[ "$(jq -r .action <<<"$A")" = halt ]'

# --- advance.sh: failed checks → iteration bump + feedback + re-dispatch intent
W7="$WORK/w7"; rm -rf "$W7"
FAILV='{"results":[{"check":"rubric-coverage","pass":false,"feedback":"Missing: duplication × src/report.js"},{"check":"schema-valid","pass":true,"feedback":""}]}'
echo "$FAILV" > "$WORK/verdicts-fail.json"
OUT=$(AGENT_RUN_ID=200 .github/engine/advance.sh "$W7" 8 protocols/grumpy/protocol.json "$WORK/verdicts-fail.json" tests/fixtures/evidence-lazy.json 2>&1) || bad "advance(fail) exited nonzero"
git clone -q --branch agentic-state "$STATE_REMOTE" "$WORK/verify7"
check "advance: iteration bumped"     '[ "$(yq -r .iteration "$WORK/verify7/grumpy/pr-8.yaml")" = 2 ]'
check "advance: feedback in history"  'yq -r ".history[-1].feedback" "$WORK/verify7/grumpy/pr-8.yaml" | grep -q "duplication × src/report.js"'
check "advance: re-dispatch intended" 'grep -q "grumpy-continue" <<<"$OUT"'

# --- advance.sh: all pass → publish + state done
W8="$WORK/w8"; rm -rf "$W8"
PASSV='{"results":[{"check":"schema-valid","pass":true,"feedback":""},{"check":"rubric-coverage","pass":true,"feedback":""},{"check":"traces-exist-in-diff","pass":true,"feedback":""}]}'
echo "$PASSV" > "$WORK/verdicts-pass.json"
OUT=$(AGENT_RUN_ID=201 .github/engine/advance.sh "$W8" 8 protocols/grumpy/protocol.json "$WORK/verdicts-pass.json" tests/fixtures/evidence-complete.json 2>&1) || bad "advance(pass) exited nonzero"
git clone -q --branch agentic-state "$STATE_REMOTE" "$WORK/verify8"
check "advance: state done"              '[ "$(yq -r .state "$WORK/verify8/grumpy/pr-8.yaml")" = done ]'
check "advance: publish intended"        'grep -q "pulls/8/reviews" <<<"$OUT"'
check "advance: verdict REQUEST_CHANGES" 'grep -q "REQUEST_CHANGES" <<<"$OUT"'

# --- advance.sh: exhaustion → state failed
W9="$WORK/w9"; rm -rf "$W9"
state_checkout "$W9"
yq -i '.iteration = 3 | .state = "review"' "$W9/grumpy/pr-8.yaml"
cas_push "$W9" "simulate iteration 3"
W10="$WORK/w10"; rm -rf "$W10"
OUT=$(AGENT_RUN_ID=202 .github/engine/advance.sh "$W10" 8 protocols/grumpy/protocol.json "$WORK/verdicts-fail.json" tests/fixtures/evidence-lazy.json 2>&1) || bad "advance(exhaust) exited nonzero"
git clone -q --branch agentic-state "$STATE_REMOTE" "$WORK/verify9"
check "advance: exhausted → failed" '[ "$(yq -r .state "$WORK/verify9/grumpy/pr-8.yaml")" = failed ]'

echo "-----"
echo "engine tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
