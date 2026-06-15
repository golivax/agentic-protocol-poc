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

# Shell wrappers: delegate lib functions to lib.py CLI.
LIB_PY=.github/agent-factory/engine/lib.py
state_checkout()  { python3 "$LIB_PY" state-checkout  "$@"; }
cas_push()        { python3 "$LIB_PY" cas-push         "$@"; }
state_file()      { python3 "$LIB_PY" state-file       "$@"; }
instance_file()   { python3 "$LIB_PY" instance-file    "$@"; }

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

PROTO=.github/agent-factory/protocols/grumpy/protocol.json
NEXT=.github/agent-factory/engine/next.py

# start on ABSENT → fresh review, run-agent iter 1, state pushed
A=$("$NEXT" "$WORK/n1" pr-7 "$PROTO" start)
check "start/absent: run-agent"        '[ "$(jq -r .action <<<"$A")" = run-agent ]'
check "start/absent: iteration 1"      '[ "$(jq -r .iteration <<<"$A")" = 1 ]'
check "start/absent: state pushed"     "git clone -q --branch agentic-state '$STATE_REMOTE' '$WORK/vn1' && grep -q 'state: review' '$WORK/vn1/grumpy-review/pr-7.yaml'"

# continue on ABSENT → fresh review iter 1 (defensive corner: engine loop before any start)
A=$("$NEXT" "$WORK/nc0" pr-700 "$PROTO" continue)
check "continue/absent: fresh iter 1" '[ "$(jq -r .action <<<"$A")" = run-agent ] && [ "$(jq -r .iteration <<<"$A")" = 1 ]'

# continue on ACTIVE (iter 2 + feedback) → resume iter 2 with feedback
# (reuses the pr-7 state pushed by start/absent above — pr-7 tests share state)
state_checkout "$WORK/n2"
FB="Missing: security × src/auth.js" yq -i \
  '.iteration = 2 | .history += [{"iteration":1,"agent_run_id":"100","feedback":strenv(FB)}]' \
  "$WORK/n2/grumpy-review/pr-7.yaml"
cas_push "$WORK/n2" "simulate failed iteration"
A=$("$NEXT" "$WORK/n3" pr-7 "$PROTO" continue)
check "continue/active: resumes iter 2" '[ "$(jq -r .iteration <<<"$A")" = 2 ]'
check "continue/active: carries feedback" 'jq -r .feedback <<<"$A" | grep -q "security × src/auth.js"'

# continue on TERMINAL → halt
state_checkout "$WORK/n4"
yq -i '.state = "done"' "$WORK/n4/grumpy-review/pr-7.yaml" && cas_push "$WORK/n4" "simulate done"
A=$("$NEXT" "$WORK/n5" pr-7 "$PROTO" continue)
check "continue/terminal: halts" '[ "$(jq -r .action <<<"$A")" = halt ]'

# start on TERMINAL → fresh re-review (intentional v1 divergence)
A=$("$NEXT" "$WORK/n6" pr-7 "$PROTO" start)
check "start/terminal: re-reviews fresh" '[ "$(jq -r .action <<<"$A")" = run-agent ] && [ "$(jq -r .iteration <<<"$A")" = 1 ]'
state_checkout "$WORK/n6b"
check "start/terminal: state reset to review" '[ "$(yq -r .state "$WORK/n6b/grumpy-review/pr-7.yaml")" = review ]'

# start on ACTIVE → halt (intentional v1 divergence; do not disturb in-flight)
state_checkout "$WORK/n7"; mkdir -p "$WORK/n7/grumpy-review"
yq -n '.protocol="grumpy-review"|.instance="pr-88"|.state="review"|.iteration=2|.gates={}|.head_sha="aaa"|.history=[]' > "$WORK/n7/grumpy-review/pr-88.yaml"
cas_push "$WORK/n7" "seed pr-88 active"
A=$("$NEXT" "$WORK/n8" pr-88 "$PROTO" start)
check "start/active: halts" '[ "$(jq -r .action <<<"$A")" = halt ]'

# reset always → fresh review iter 1 and records the new head
state_checkout "$WORK/n9"; mkdir -p "$WORK/n9/grumpy-review"
yq -n '.protocol="grumpy-review"|.instance="pr-9"|.state="done"|.iteration=3|.gates={}|.head_sha="old111"|.history=[{"iteration":1,"feedback":"old"}]' > "$WORK/n9/grumpy-review/pr-9.yaml"
cas_push "$WORK/n9" "seed pr-9 done@old111"
A=$("$NEXT" "$WORK/n10" pr-9 "$PROTO" reset new222)
check "reset: run-agent iter 1" '[ "$(jq -r .action <<<"$A")" = run-agent ] && [ "$(jq -r .iteration <<<"$A")" = 1 ]'
state_checkout "$WORK/n11"
check "reset: new head recorded + state review" '[ "$(yq -r .head_sha "$WORK/n11/grumpy-review/pr-9.yaml")" = new222 ] && [ "$(yq -r .state "$WORK/n11/grumpy-review/pr-9.yaml")" = review ]'

# reset on ACTIVE → also fresh iter 1 (unconditional, regardless of in-flight state)
state_checkout "$WORK/n12"; mkdir -p "$WORK/n12/grumpy-review"
yq -n '.protocol="grumpy-review"|.instance="pr-99"|.state="review"|.iteration=2|.gates={}|.head_sha="x"|.history=[{"iteration":1,"feedback":"prev"}]' > "$WORK/n12/grumpy-review/pr-99.yaml"
cas_push "$WORK/n12" "seed pr-99 active"
A=$("$NEXT" "$WORK/n13" pr-99 "$PROTO" reset z999)
check "reset/active: run-agent iter 1" '[ "$(jq -r .action <<<"$A")" = run-agent ] && [ "$(jq -r .iteration <<<"$A")" = 1 ]'

# --- advance.sh: failed checks → iteration bump + feedback + re-dispatch intent
W7="$WORK/w7"; rm -rf "$W7"
FAILV='{"results":[{"check":"rubric-coverage","pass":false,"feedback":"Missing: duplication × src/report.js"},{"check":"schema-valid","pass":true,"feedback":""}]}'
echo "$FAILV" > "$WORK/verdicts-fail.json"
OUT=$(PR=8 AGENT_RUN_ID=200 .github/agent-factory/engine/advance.py "$W7" pr-8 .github/agent-factory/protocols/grumpy/protocol.json "$WORK/verdicts-fail.json" tests/fixtures/evidence-lazy.json 2>&1) || bad "advance(fail) exited nonzero"
git clone -q --branch agentic-state "$STATE_REMOTE" "$WORK/verify7"
check "advance: iteration bumped"     '[ "$(yq -r .iteration "$WORK/verify7/grumpy-review/pr-8.yaml")" = 2 ]'
check "advance: feedback in history"  'yq -r ".history[-1].feedback" "$WORK/verify7/grumpy-review/pr-8.yaml" | grep -q "duplication × src/report.js"'
check "advance: re-dispatch intended" 'grep -q "protocol-continue" <<<"$OUT"'

# --- advance.sh: all pass → publish + state done
W8="$WORK/w8"; rm -rf "$W8"
PASSV='{"results":[{"check":"schema-valid","pass":true,"feedback":""},{"check":"rubric-coverage","pass":true,"feedback":""},{"check":"traces-exist-in-diff","pass":true,"feedback":""}]}'
echo "$PASSV" > "$WORK/verdicts-pass.json"
OUT=$(PR=8 AGENT_RUN_ID=201 .github/agent-factory/engine/advance.py "$W8" pr-8 .github/agent-factory/protocols/grumpy/protocol.json "$WORK/verdicts-pass.json" tests/fixtures/evidence-complete.json 2>&1) || bad "advance(pass) exited nonzero"
check "single-agent comment keeps per-file blob link" 'grep -q "blob/agentic-state/grumpy-review/pr-8.yaml" <<<"$OUT"'
git clone -q --branch agentic-state "$STATE_REMOTE" "$WORK/verify8"
check "advance: state done"              '[ "$(yq -r .state "$WORK/verify8/grumpy-review/pr-8.yaml")" = done ]'
check "advance: publish intended"        'grep -q "pulls/8/reviews" <<<"$OUT"'
check "advance: verdict REQUEST_CHANGES" 'grep -q "REQUEST_CHANGES" <<<"$OUT"'

# --- advance.sh: exhaustion → state failed
W9="$WORK/w9"; rm -rf "$W9"
state_checkout "$W9"
yq -i '.iteration = 3 | .state = "review"' "$W9/grumpy-review/pr-8.yaml"
cas_push "$W9" "simulate iteration 3"
W10="$WORK/w10"; rm -rf "$W10"
OUT=$(PR=8 AGENT_RUN_ID=202 .github/agent-factory/engine/advance.py "$W10" pr-8 .github/agent-factory/protocols/grumpy/protocol.json "$WORK/verdicts-fail.json" tests/fixtures/evidence-lazy.json 2>&1) || bad "advance(exhaust) exited nonzero"
git clone -q --branch agentic-state "$STATE_REMOTE" "$WORK/verify9"
check "advance: exhausted → failed" '[ "$(yq -r .state "$WORK/verify9/grumpy-review/pr-8.yaml")" = failed ]'

# --- advance.sh: empty verdicts (checks produced nothing) must NOT publish
W11="$WORK/w11"; rm -rf "$W11"
echo '{"results":[]}' > "$WORK/verdicts-empty.json"
OUT=$(PR=9 AGENT_RUN_ID=203 .github/agent-factory/engine/advance.py "$W11" pr-9 .github/agent-factory/protocols/grumpy/protocol.json "$WORK/verdicts-empty.json" tests/fixtures/evidence-lazy.json 2>&1) || bad "advance(empty) exited nonzero"
git clone -q --branch agentic-state "$STATE_REMOTE" "$WORK/verify11"
check "advance: empty verdicts → not done"   '[ "$(yq -r .state "$WORK/verify11/grumpy-review/pr-9.yaml")" != done ]'
check "advance: empty verdicts → no publish" '! grep -q "pulls/9/reviews" <<<"$OUT"'

# --- advance.sh emits a grumpy-review check run reflecting the outcome ---
export PR_HEAD_SHA="testsha123"
# iterate (fail, iter<max) → in_progress
OUT=$(PR=20 AGENT_RUN_ID=300 .github/agent-factory/engine/advance.py "$WORK/c1" pr-20 .github/agent-factory/protocols/grumpy/protocol.json "$WORK/verdicts-fail.json" tests/fixtures/evidence-lazy.json 2>&1)
check "check-run: iterate → in_progress" 'grep -q "check-run grumpy-review sha=testsha123 status=in_progress" <<<"$OUT"'
# pass with issues-found (evidence-complete has issues) → action_required
OUT=$(PR=20 AGENT_RUN_ID=301 .github/agent-factory/engine/advance.py "$WORK/c2" pr-20 .github/agent-factory/protocols/grumpy/protocol.json "$WORK/verdicts-pass.json" tests/fixtures/evidence-complete.json 2>&1)
check "check-run: changes requested → failure" 'grep -q "status=completed conclusion=failure" <<<"$OUT"'
# exhausted (fail at iter==max) → failure
W12="$WORK/c3"; state_checkout "$W12"; yq -i '.iteration = 3 | .state = "review"' "$W12/grumpy-review/pr-21.yaml" 2>/dev/null || { mkdir -p "$W12/grumpy-review"; yq -n '.protocol="grumpy-review"|.instance="pr-21"|.state="review"|.iteration=3|.gates={}|.history=[]' > "$W12/grumpy-review/pr-21.yaml"; }
cas_push "$W12" "seed pr-21 iter3"
OUT=$(PR=21 AGENT_RUN_ID=302 .github/agent-factory/engine/advance.py "$WORK/c4" pr-21 .github/agent-factory/protocols/grumpy/protocol.json "$WORK/verdicts-fail.json" tests/fixtures/evidence-lazy.json 2>&1)
check "check-run: exhausted → failure" 'grep -q "status=completed conclusion=failure" <<<"$OUT"'

# --- advance relays the publish hook's {conclusion,summary} (engine reads no schema)
STUB_HOOK=".github/agent-factory/protocols/grumpy/publish/stub-publish.sh"
STUB_PROTO=".github/agent-factory/protocols/grumpy/.test-stub-proto.json"
printf '#!/usr/bin/env bash\necho '"'"'{"conclusion":"success","summary":"STUB-RELAYED-OK"}'"'"'\n' > "$STUB_HOOK"
chmod +x "$STUB_HOOK"
jq '.states |= map(if .kind=="deterministic" then .action="stub-publish" else . end)' \
  .github/agent-factory/protocols/grumpy/protocol.json > "$STUB_PROTO"
echo '{"results":[{"check":"x","pass":true,"feedback":""}]}' > "$WORK/verdicts-stub.json"
# Clean up the stub files on EXIT too: if advance.sh ever exits nonzero, set -e
# would abort before the rm and leave stub-publish.sh in .github/agent-factory/protocols/grumpy/publish/,
# which then makes resolve_executable see two matches (ambiguous) for every later run.
trap 'rm -f "$STUB_HOOK" "$STUB_PROTO"' EXIT
OUT=$(PR=8 AGENT_RUN_ID=400 .github/agent-factory/engine/advance.py "$WORK/relay" pr-8 "$STUB_PROTO" "$WORK/verdicts-stub.json" tests/fixtures/evidence-complete.json 2>&1) || bad "advance(relay) exited nonzero"
rm -f "$STUB_HOOK" "$STUB_PROTO"; trap - EXIT
check "advance: relays hook conclusion" 'grep -q "conclusion=success" <<<"$OUT"'
check "advance: relays hook summary"    'grep -q "STUB-RELAYED-OK" <<<"$OUT"'

# --- lib: branch-aware state/instance paths (single-agent form unchanged) ---
check "state_file single-agent form unchanged" \
  '[ "$(state_file /s grumpy-review pr-5)" = "/s/grumpy-review/pr-5.yaml" ]'
check "state_file branch form nests under instance dir" \
  '[ "$(state_file /s multi-grumpy pr-5 grumpy)" = "/s/multi-grumpy/pr-5/grumpy.yaml" ]'
check "instance_file points at _instance.yaml" \
  '[ "$(instance_file /s multi-grumpy pr-5)" = "/s/multi-grumpy/pr-5/_instance.yaml" ]'

# --- next.sh branch-scoped continue resumes a single branch's state file ---
state_checkout "$WORK/nb1"; mkdir -p "$WORK/nb1/multi-grumpy/pr-77"
yq -n '.protocol="multi-grumpy"|.instance="pr-77"|.state="review"|.iteration=2|.gates={}|.history=[{"iteration":1,"feedback":"sec: missing anchor"}]' \
  > "$WORK/nb1/multi-grumpy/pr-77/security.yaml"
cas_push "$WORK/nb1" "seed pr-77 security active@iter2"
A=$(BRANCH=security "$NEXT" "$WORK/nb2" pr-77 .github/agent-factory/protocols/multi-grumpy/protocol.json continue)
check "branch continue: resumes iter 2"        '[ "$(jq -r .iteration <<<"$A")" = 2 ]'
check "branch continue: carries branch feedback" 'jq -r .feedback <<<"$A" | grep -q "missing anchor"'

# --- next.sh fanout planning: start on a fanout protocol seeds both branches ---
A=$("$NEXT" "$WORK/fo1" pr-50 .github/agent-factory/protocols/multi-grumpy/protocol.json start head50)
check "fanout start: action run-fanout"      '[ "$(jq -r .action <<<"$A")" = run-fanout ]'
check "fanout start: two branches listed"    '[ "$(jq -r ".branches|length" <<<"$A")" = 2 ]'
check "fanout start: lists grumpy workflow"  'jq -e ".branches[]|select(.id==\"grumpy\")|select(.workflow==\"grumpy-agent\")" <<<"$A" >/dev/null'
state_checkout "$WORK/fo2"
check "fanout start: grumpy file seeded active"   '[ "$(yq -r .state "$WORK/fo2/multi-grumpy/pr-50/grumpy.yaml")" = review ] && [ "$(yq -r .iteration "$WORK/fo2/multi-grumpy/pr-50/grumpy.yaml")" = 1 ]'
check "fanout start: security file seeded active" '[ "$(yq -r .state "$WORK/fo2/multi-grumpy/pr-50/security.yaml")" = review ] && [ "$(yq -r .iteration "$WORK/fo2/multi-grumpy/pr-50/security.yaml")" = 1 ]'
check "fanout start: instance file w/ head" '[ "$(yq -r .head_sha "$WORK/fo2/multi-grumpy/pr-50/_instance.yaml")" = head50 ] && [ "$(yq -r .joined "$WORK/fo2/multi-grumpy/pr-50/_instance.yaml")" = false ]'

# --- next.sh branch lifecycle: terminal branch halts; absent branch starts fresh ---
state_checkout "$WORK/bl1"; mkdir -p "$WORK/bl1/multi-grumpy/pr-60"
yq -n '.protocol="multi-grumpy"|.instance="pr-60"|.state="done"|.iteration=2|.gates={}|.history=[]' \
  > "$WORK/bl1/multi-grumpy/pr-60/grumpy.yaml"
cas_push "$WORK/bl1" "seed pr-60 grumpy done"
A=$(BRANCH=grumpy "$NEXT" "$WORK/bl2" pr-60 .github/agent-factory/protocols/multi-grumpy/protocol.json continue)
check "branch continue/terminal: halts" '[ "$(jq -r .action <<<"$A")" = halt ]'
A=$(BRANCH=grumpy "$NEXT" "$WORK/bl3" pr-61 .github/agent-factory/protocols/multi-grumpy/protocol.json continue)
check "branch continue/absent: fresh run-agent iter 1" \
  '[ "$(jq -r .action <<<"$A")" = run-agent ] && [ "$(jq -r .iteration <<<"$A")" = 1 ]'

# --- advance.sh branch-scoped: grumpy branch, all pass → done + per-branch publish + branch check-run ---
cp .github/agent-factory/protocols/grumpy/publish/publish-review-from-evidence.sh .github/agent-factory/protocols/multi-grumpy/publish/publish-grumpy.sh
chmod +x .github/agent-factory/protocols/multi-grumpy/publish/publish-grumpy.sh
export PR_HEAD_SHA="mgsha1"
OUT=$(BRANCH=grumpy PR=50 AGENT_RUN_ID=900 .github/agent-factory/engine/advance.py "$WORK/advmg1" pr-50 \
  .github/agent-factory/protocols/multi-grumpy/protocol.json "$WORK/verdicts-pass.json" tests/fixtures/evidence-complete.json 2>&1) || bad "advance(mg grumpy) nonzero"
git clone -q --branch agentic-state "$STATE_REMOTE" "$WORK/vmg1"
check "advance branch: grumpy.yaml state done"    '[ "$(yq -r .state "$WORK/vmg1/multi-grumpy/pr-50/grumpy.yaml")" = done ]'
check "advance branch: published via hook"        'grep -q "pulls/50/reviews" <<<"$OUT"'
check "advance branch: per-branch check-run name" 'grep -q "check-run multi-grumpy/grumpy " <<<"$OUT"'
check "advance branch: shared comment has grumpy section" 'grep -q "\*\*grumpy\*\*" <<<"$OUT"'
check "advance branch: shared comment tree/ link"         'grep -q "tree/agentic-state/multi-grumpy/pr-50" <<<"$OUT"'
check "advance branch: shared comment not blob link"      '! grep -q "blob/agentic-state" <<<"$OUT"'

# --- advance.sh fan-out signalling: terminal branch fires protocol-join; iterate carries branch ---
# iterate (fail, iter<max) on a branch → protocol-continue WITH branch
echo '{"results":[{"check":"schema-valid","pass":false,"feedback":"sec: bad anchor"}]}' > "$WORK/verdicts-secfail.json"
OUT=$(BRANCH=security PR=50 AGENT_RUN_ID=901 .github/agent-factory/engine/advance.py "$WORK/advmg2" pr-50 \
  .github/agent-factory/protocols/multi-grumpy/protocol.json "$WORK/verdicts-secfail.json" tests/fixtures/evidence-lazy.json 2>&1)
check "fanout iterate: protocol-continue fired" 'grep -q "protocol-continue" <<<"$OUT"'
check "fanout iterate: payload carries branch"  'grep -q "client_payload\[branch\]=security" <<<"$OUT"'
# terminal (done) on a branch → protocol-join fired
OUT=$(BRANCH=grumpy PR=51 AGENT_RUN_ID=902 .github/agent-factory/engine/advance.py "$WORK/advmg3" pr-51 \
  .github/agent-factory/protocols/multi-grumpy/protocol.json "$WORK/verdicts-pass.json" tests/fixtures/evidence-complete.json 2>&1)
check "fanout done: protocol-join fired" 'grep -q "protocol-join" <<<"$OUT"'

echo "-----"
echo "engine tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
