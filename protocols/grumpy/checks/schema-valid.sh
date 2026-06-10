#!/usr/bin/env bash
# Check: evidence file parses and matches the structural shape.
# Usage: schema-valid.sh <evidence.json> <diff.txt> <changed-files.txt>
set -euo pipefail
EV="$1"
PROTO="$(cd "$(dirname "$0")/.." && pwd)/protocol.json"

emit() { jq -n --argjson p "$1" --arg f "$2" '{check:"schema-valid", pass:$p, feedback:$f}'; }

if ! jq -e . "$EV" >/dev/null 2>&1; then
  emit false "evidence file is missing or not valid JSON"; exit 0
fi
if ! jq -e '.files | type == "array"' "$EV" >/dev/null 2>&1; then
  emit false "top-level .files array is missing"; exit 0
fi

CATS_JSON=$(jq -c '.categories' "$PROTO")
ERR=$(jq -r --argjson valid "$CATS_JSON" '
  [ .files[] | .path as $p | .verdicts[]? |
    if (.category as $c | $valid | index($c) | not)
      then "illegal category \(.category) in \($p)"
    elif (.verdict != "issues-found" and .verdict != "none-found")
      then "illegal verdict \(.verdict) for \(.category) × \($p)"
    elif .verdict == "issues-found" and ((.findings // []) | length) == 0
      then "issues-found with no findings: \(.category) × \($p)"
    elif .verdict == "issues-found" and ([(.findings // [])[] | ((.existing_code // "") | length) > 0 and ((.comment // "") | length) > 0] | all | not)
      then "finding with empty existing_code or comment: \(.category) × \($p)"
    elif .verdict == "none-found" and ((.examined // []) | length) == 0
      then "none-found with no examined identifiers: \(.category) × \($p)"
    else empty end
  ] | join("; ")' "$EV")

if [ -n "$ERR" ]; then emit false "$ERR"; else emit true ""; fi
