#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PASS=0; FAIL=0
HOOK=.github/agent-factory/protocols/grumpy/publish/publish-review-from-evidence.py

# run_local <evidence-file> → the JSON payload the hook would POST (ENGINE_LOCAL)
run_local() {
  ENGINE_LOCAL=1 GITHUB_REPOSITORY=acme/repo PR=8 "$HOOK" "$1" pr-8 2>&1 1>/dev/null \
    | sed -n '/^{/,$p'
}
check() { # <desc> <jq-filter-returning-true/false> <payload>
  local got; got=$(jq -r "$2" <<<"$3" 2>/dev/null || echo ERR)
  if [ "$got" = "true" ]; then PASS=$((PASS+1)); echo "ok: $1"
  else FAIL=$((FAIL+1)); echo "FAIL: $1 → $got"; fi
}

# Evidence with a single-line RIGHT, a multi-line RIGHT range, and a LEFT finding.
cat > /tmp/ev-pub.json <<'JSON'
{ "files": [
  { "path": "src/cache.js", "verdicts": [
    { "category": "naming", "verdict": "issues-found", "findings": [
      { "existing_code": "function set(key, value) {", "comment": "rename it", "side": "RIGHT", "line": 6 } ] },
    { "category": "duplication", "verdict": "issues-found", "findings": [
      { "existing_code": "block", "comment": "dup block", "side": "RIGHT", "start_line": 3, "line": 5 } ] },
    { "category": "performance", "verdict": "issues-found", "findings": [
      { "existing_code": "function set(key, val) {", "comment": "why removed", "side": "LEFT", "line": 6 } ] } ] } ] }
JSON
P=$(run_local /tmp/ev-pub.json)
check "event is REQUEST_CHANGES"         '.event == "REQUEST_CHANGES"' "$P"
check "three inline comments"            '(.comments | length) == 3' "$P"
check "single-line comment shape"        '.comments[0] == {path:"src/cache.js", side:"RIGHT", line:6, body:"rename it"}' "$P"
check "range comment has start_line"     '.comments[1].start_line == 3 and .comments[1].start_side == "RIGHT" and .comments[1].line == 5' "$P"
check "LEFT comment side"                '.comments[2].side == "LEFT" and .comments[2].line == 6' "$P"
check "body is a short overview"         '(.body | test("Grumpy")) and (.body | test("inline"))' "$P"

# Clean PR → APPROVE, no comments.
cat > /tmp/ev-clean.json <<'JSON'
{ "files": [ { "path": "src/cache.js", "verdicts": [
  { "category": "naming", "verdict": "none-found", "examined": ["get", "set"] } ] } ] }
JSON
Q=$(run_local /tmp/ev-clean.json)
check "clean → APPROVE"                  '.event == "APPROVE"' "$Q"
check "clean → no comments"              '(.comments | length) == 0' "$Q"

# --- publish-security: REQUEST_CHANGES body uses the security chrome ---
# run_local_security: captures the ENGINE_LOCAL POST payload JSON from stderr (same
# pattern as run_local above — 2>&1 1>/dev/null swaps streams so stderr is piped).
run_local_security() {
  ENGINE_LOCAL=1 GITHUB_REPOSITORY=acme/repo PR=8 \
    .github/agent-factory/protocols/multi-grumpy/publish/publish-security.py "$1" pr-8 2>&1 1>/dev/null \
    | sed -n '/^{/,$p'
}
S=$(run_local_security tests/fixtures/evidence-security.json)
check "publish-security: event is REQUEST_CHANGES"  '.event == "REQUEST_CHANGES"'   "$S"
check "publish-security: body has security heading" '(.body | test("🔒"))'          "$S"
check "publish-security: has one inline comment"    '(.comments | length) == 1'     "$S"

# The hook prints {conclusion,summary} on stdout; capture that separately.
SEC_STDOUT=$(ENGINE_LOCAL=1 GITHUB_REPOSITORY=acme/repo PR=8 \
  .github/agent-factory/protocols/multi-grumpy/publish/publish-security.py tests/fixtures/evidence-security.json pr-8 2>/dev/null)
check "publish-security: conclusion=failure"  '.conclusion == "failure"'  "$SEC_STDOUT"
check "publish-security: summary non-empty"   '(.summary | length) > 0'  "$SEC_STDOUT"

echo "-----"
echo "publish tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
