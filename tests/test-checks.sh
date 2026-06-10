#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
FX=tests/fixtures
PASS=0; FAIL=0

# assert_check <script> <expected_pass:true|false> <feedback_substring> <evidence> [diff] [files]
assert_check() {
  local script="$1" expect="$2" substr="$3" ev="$4" diff="${5:-$FX/diff-pr1.txt}" files="${6:-$FX/changed-files-pr1.txt}"
  local out; out=$("protocols/grumpy/checks/$script" "$ev" "$diff" "$files") || true
  local got; got=$(jq -r .pass <<<"$out")
  local fb; fb=$(jq -r .feedback <<<"$out")
  if [ "$got" = "$expect" ] && { [ -z "$substr" ] || [[ "$fb" == *"$substr"* ]]; }; then
    PASS=$((PASS+1)); echo "ok: $script $ev"
  else
    FAIL=$((FAIL+1)); echo "FAIL: $script $ev → pass=$got (want $expect), feedback=$fb (want *$substr*)"
  fi
}

assert_check schema-valid.sh true  ""                       "$FX/evidence-complete.json"
assert_check schema-valid.sh true  ""                       "$FX/evidence-lazy.json"      # lazy is structurally valid; coverage catches it
assert_check schema-valid.sh false "not valid JSON"         /dev/null
echo '{"files":[{"path":"a.js","verdicts":[{"category":"naming","verdict":"issues-found","findings":[]}]}]}' > /tmp/ev-nofindings.json
assert_check schema-valid.sh false "no findings"            /tmp/ev-nofindings.json
echo '{"files":[{"path":"a.js","verdicts":[{"category":"vibes","verdict":"none-found","examined":["x"]}]}]}' > /tmp/ev-badcat.json
assert_check schema-valid.sh false "illegal category"       /tmp/ev-badcat.json
echo '{"files":[{"path":"a.js","verdicts":[{"category":"naming","verdict":"none-found"}]}]}' > /tmp/ev-noexam.json
assert_check schema-valid.sh false "no examined"            /tmp/ev-noexam.json
echo '{"files":["a.js"]}' > /tmp/ev-strfile.json
assert_check schema-valid.sh false "not an object" /tmp/ev-strfile.json
echo '{"files":[{"path":"a.js","verdicts":"oops"}]}' > /tmp/ev-badverdicts.json
assert_check schema-valid.sh false "verdicts" /tmp/ev-badverdicts.json

assert_check rubric-coverage.sh true  ""                     "$FX/evidence-complete.json"
assert_check rubric-coverage.sh false "security × src/auth.js" "$FX/evidence-lazy.json"
assert_check rubric-coverage.sh false "duplication × src/report.js" "$FX/evidence-lazy.json"
# duplicated verdict for one cell is also a failure:
jq '.files[0].verdicts += [.files[0].verdicts[0]]' "$FX/evidence-complete.json" > /tmp/ev-dup.json
assert_check rubric-coverage.sh false "naming × src/auth.js" /tmp/ev-dup.json

# changed-files without trailing newline must not exempt the last file:
printf 'src/auth.js\nsrc/report.js' > /tmp/files-nonewline.txt
assert_check rubric-coverage.sh false "src/report.js" "$FX/evidence-lazy.json" "$FX/diff-pr1.txt" /tmp/files-nonewline.txt

assert_check traces-exist-in-diff.sh true  ""                          "$FX/evidence-complete.json"
assert_check traces-exist-in-diff.sh false "authenticateUser"          "$FX/evidence-fabricated.json"
assert_check traces-exist-in-diff.sh false "renderDashboard"           "$FX/evidence-fabricated.json"

echo "-----"
echo "checks tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
