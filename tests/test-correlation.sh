#!/usr/bin/env bash
# Unit tests for lib.sh match_run_by_cid — the correlation-id run resolver.
# Pure: given a `gh run list --json databaseId,displayTitle` JSON array and a cid,
# print the databaseId of the run whose displayTitle carries the delimited token
# cid:[<cid>], else empty. No GitHub calls.
set -euo pipefail
cd "$(dirname "$0")/.."
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "ok: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL: $1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

source .github/agent-factory/engine/lib.sh

# Two concurrent runs of the SAME workflow, different cids. databaseId 222 is the
# newest (listed first) — the matcher must pick by cid, NOT by recency.
RUNS='[{"databaseId":222,"displayTitle":"Grumpy Agent · cid:[99-1-grumpy]"},{"databaseId":111,"displayTitle":"Grumpy Agent · cid:[42-1-grumpy]"}]'
check "collision: picks the run carrying our cid (not the newest)" \
  '[ "$(match_run_by_cid "$RUNS" "42-1-grumpy")" = "111" ]'
check "collision: picks the other cid correctly" \
  '[ "$(match_run_by_cid "$RUNS" "99-1-grumpy")" = "222" ]'

# No run carries the cid → empty (drives the orchestrator's fail-loud path).
check "no match → empty" \
  '[ -z "$(match_run_by_cid "$RUNS" "7-1-grumpy")" ]'

# Empty run list → empty.
check "empty list → empty" \
  '[ -z "$(match_run_by_cid "[]" "42-1-grumpy")" ]'

# Delimiter safety: one title's cid is a PREFIX of our cid. The bracketed token
# match must pick the exact one (databaseId 2), not the prefix one (databaseId 1).
PFX='[{"databaseId":1,"displayTitle":"x cid:[42-1-grumpy2]"},{"databaseId":2,"displayTitle":"x cid:[42-1-grumpy]"}]'
check "delimiter: prefix cid does not false-match" \
  '[ "$(match_run_by_cid "$PFX" "42-1-grumpy")" = "2" ]'

# A queued run may have a null/absent displayTitle; it must NOT abort the match —
# the real titled run is still resolved.
NULLT='[{"databaseId":7,"displayTitle":null},{"databaseId":8,"displayTitle":"Grumpy Agent · cid:[42-1-grumpy]"}]'
check "null displayTitle does not abort the match" \
  '[ "$(match_run_by_cid "$NULLT" "42-1-grumpy")" = "8" ]'

echo "-----"
echo "correlation tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
