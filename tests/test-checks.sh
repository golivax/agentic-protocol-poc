#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
FX=tests/fixtures
PASS=0; FAIL=0

# These checks read their rubric from CHECK_PARAMS (the engine forwards it in
# production). Direct invocations here must supply it; the grumpy rubric is the
# full five-category set. Individual cases override this inline where needed.
export CHECK_PARAMS='{"categories":["naming","error-handling","performance","duplication","security"]}'

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

# issues-found findings must carry a valid line/side anchor:
echo '{"files":[{"path":"a.js","verdicts":[{"category":"naming","verdict":"issues-found","findings":[{"existing_code":"x","comment":"y"}]}]}]}' > /tmp/ev-noanchor.json
assert_check schema-valid.sh false "anchor" /tmp/ev-noanchor.json
echo '{"files":[{"path":"a.js","verdicts":[{"category":"naming","verdict":"issues-found","findings":[{"existing_code":"x","comment":"y","side":"RIGHT","line":"3"}]}]}]}' > /tmp/ev-strline.json
assert_check schema-valid.sh false "anchor" /tmp/ev-strline.json
echo '{"files":[{"path":"a.js","verdicts":[{"category":"naming","verdict":"issues-found","findings":[{"existing_code":"x","comment":"y","side":"UP","line":3}]}]}]}' > /tmp/ev-badside.json
assert_check schema-valid.sh false "anchor" /tmp/ev-badside.json
echo '{"files":[{"path":"a.js","verdicts":[{"category":"naming","verdict":"issues-found","findings":[{"existing_code":"x","comment":"y","side":"RIGHT","line":3,"start_line":"two"}]}]}]}' > /tmp/ev-strstart.json
assert_check schema-valid.sh false "anchor" /tmp/ev-strstart.json
# integer start_line must be accepted:
echo '{"files":[{"path":"a.js","verdicts":[{"category":"naming","verdict":"issues-found","findings":[{"existing_code":"x","comment":"y","side":"RIGHT","line":5,"start_line":3}]}]}]}' > /tmp/ev-okstart.json
assert_check schema-valid.sh true "" /tmp/ev-okstart.json
# line=0 must be rejected (minimum is 1):
echo '{"files":[{"path":"a.js","verdicts":[{"category":"naming","verdict":"issues-found","findings":[{"existing_code":"x","comment":"y","side":"RIGHT","line":0}]}]}]}' > /tmp/ev-zeroline.json
assert_check schema-valid.sh false "anchor" /tmp/ev-zeroline.json

assert_check rubric-coverage.py true  ""                     "$FX/evidence-complete.json"
assert_check rubric-coverage.py false "security × src/auth.js" "$FX/evidence-lazy.json"
assert_check rubric-coverage.py false "duplication × src/report.js" "$FX/evidence-lazy.json"
# duplicated verdict for one cell is also a failure:
jq '.files[0].verdicts += [.files[0].verdicts[0]]' "$FX/evidence-complete.json" > /tmp/ev-dup.json
assert_check rubric-coverage.py false "naming × src/auth.js" /tmp/ev-dup.json

# changed-files without trailing newline must not exempt the last file:
printf 'src/auth.js\nsrc/report.js' > /tmp/files-nonewline.txt
assert_check rubric-coverage.py false "src/report.js" "$FX/evidence-lazy.json" "$FX/diff-pr1.txt" /tmp/files-nonewline.txt

# --- traces-exist-in-diff: anchors resolve to the claimed line(s) on the claimed side ---
assert_check traces-exist-in-diff.py true  "" "$FX/evidence-complete.json"

# fabricated snippet (content mismatch) and fabricated examined identifier both caught:
assert_check traces-exist-in-diff.py false "does not match" "$FX/evidence-fabricated.json"
assert_check traces-exist-in-diff.py false "renderDashboard" "$FX/evidence-fabricated.json"

DIFF2=$FX/diff-pr2-deletions.txt
FILES2=$FX/changed-files-pr2.txt

# correct single-line RIGHT anchor passes:
jq -n '{files:[{path:"src/cache.js",verdicts:[{category:"naming",verdict:"issues-found",findings:[{existing_code:"function set(key, value) {",comment:"name it",side:"RIGHT",line:6}]}]}]}' > /tmp/ev-anc-right.json
assert_check traces-exist-in-diff.py true "" /tmp/ev-anc-right.json "$DIFF2" "$FILES2"

# correct LEFT anchor (deleted line) passes:
jq -n '{files:[{path:"src/cache.js",verdicts:[{category:"naming",verdict:"issues-found",findings:[{existing_code:"function set(key, val) {",comment:"why",side:"LEFT",line:6}]}]}]}' > /tmp/ev-anc-left.json
assert_check traces-exist-in-diff.py true "" /tmp/ev-anc-left.json "$DIFF2" "$FILES2"

# correct multi-line RIGHT range passes:
jq -n '{files:[{path:"src/cache.js",verdicts:[{category:"duplication",verdict:"issues-found",findings:[{existing_code:"function get(key) {\n  return store[key];\n}",comment:"blk",side:"RIGHT",start_line:3,line:5}]}]}]}' > /tmp/ev-anc-range.json
assert_check traces-exist-in-diff.py true "" /tmp/ev-anc-range.json "$DIFF2" "$FILES2"

# wrong line (content mismatch) fails:
jq -n '{files:[{path:"src/cache.js",verdicts:[{category:"naming",verdict:"issues-found",findings:[{existing_code:"function set(key, value) {",comment:"x",side:"RIGHT",line:7}]}]}]}' > /tmp/ev-anc-wrongline.json
assert_check traces-exist-in-diff.py false "does not match" /tmp/ev-anc-wrongline.json "$DIFF2" "$FILES2"

# wrong side (added line claimed on LEFT, content mismatch) fails:
jq -n '{files:[{path:"src/cache.js",verdicts:[{category:"naming",verdict:"issues-found",findings:[{existing_code:"function set(key, value) {",comment:"x",side:"LEFT",line:6}]}]}]}' > /tmp/ev-anc-wrongside.json
assert_check traces-exist-in-diff.py false "does not match" /tmp/ev-anc-wrongside.json "$DIFF2" "$FILES2"

# line outside any hunk fails:
jq -n '{files:[{path:"src/cache.js",verdicts:[{category:"naming",verdict:"issues-found",findings:[{existing_code:"whatever",comment:"x",side:"RIGHT",line:99}]}]}]}' > /tmp/ev-anc-noline.json
assert_check traces-exist-in-diff.py false "not on RIGHT" /tmp/ev-anc-noline.json "$DIFF2" "$FILES2"

# start_line >= line fails:
jq -n '{files:[{path:"src/cache.js",verdicts:[{category:"naming",verdict:"issues-found",findings:[{existing_code:"x",comment:"x",side:"RIGHT",start_line:5,line:5}]}]}]}' > /tmp/ev-anc-badrange.json
assert_check traces-exist-in-diff.py false "must be <" /tmp/ev-anc-badrange.json "$DIFF2" "$FILES2"

# cross-hunk range fails:
jq -n '{files:[{path:"src/cache.js",verdicts:[{category:"naming",verdict:"issues-found",findings:[{existing_code:"irrelevant",comment:"x",side:"RIGHT",start_line:5,line:22}]}]}]}' > /tmp/ev-anc-crosshunk.json
assert_check traces-exist-in-diff.py false "contiguous" /tmp/ev-anc-crosshunk.json "$DIFF2" "$FILES2"

# examined identifier absent from the file's diff still fails (path not regex; renamed section):
sed 's|b/src/auth.js|b/src/authXjs|; s|a/src/auth.js|a/src/authXjs|' "$FX/diff-pr1.txt" > /tmp/diff-xjs.txt
jq -n '{files:[{path:"src/auth.js",verdicts:[{category:"naming",verdict:"none-found",examined:["login"]}]}]}' > /tmp/ev-regexpath.json
assert_check traces-exist-in-diff.py false "login" /tmp/ev-regexpath.json /tmp/diff-xjs.txt

# missing args → clean JSON rejection, exit 0 (ABI contract):
out=$(protocols/grumpy/checks/traces-exist-in-diff.py 2>/dev/null); rc=$?
if [ "$rc" = "0" ] && [ "$(jq -r .pass <<<"$out")" = "false" ]; then
  PASS=$((PASS+1)); echo "ok: traces-exist-in-diff handles missing args"
else
  FAIL=$((FAIL+1)); echo "FAIL: traces-exist-in-diff missing-args rc=$rc out=$out"
fi

DIFF3=$FX/diff-pr3-filedelete.txt
FILES3=$FX/changed-files-pr3.txt

# deleted file: a LEFT anchor on a removed line resolves (regression guard):
jq -n '{files:[{path:"src/legacy.js",verdicts:[{category:"naming",verdict:"issues-found",findings:[{existing_code:"function legacy(a) {",comment:"bad name",side:"LEFT",line:1}]}]}]}' > /tmp/ev-del-left.json
assert_check traces-exist-in-diff.py true "" /tmp/ev-del-left.json "$DIFF3" "$FILES3"

# deleted file: an examined identifier from the removed code resolves:
jq -n '{files:[{path:"src/legacy.js",verdicts:[{category:"naming",verdict:"none-found",examined:["legacy"]}]}]}' > /tmp/ev-del-exam.json
assert_check traces-exist-in-diff.py true "" /tmp/ev-del-exam.json "$DIFF3" "$FILES3"

echo "-----"
echo "checks tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
