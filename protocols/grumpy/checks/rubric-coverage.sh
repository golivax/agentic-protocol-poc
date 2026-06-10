#!/usr/bin/env bash
# Check: every reviewable changed file × every category has exactly one verdict.
# Usage: rubric-coverage.sh <evidence.json> <diff.txt> <changed-files.txt>
set -euo pipefail
EV="$1"; FILES="$3"
PROTO="$(cd "$(dirname "$0")/.." && pwd)/protocol.json"

mapfile -t CATS < <(jq -r '.categories[]' "$PROTO")
BAD=()
while IFS= read -r f; do
  case "$f" in *.js) ;; *) continue ;; esac
  for c in "${CATS[@]}"; do
    n=$(jq --arg p "$f" --arg c "$c" \
      '[.files[]? | select(.path==$p) | .verdicts[]? | select(.category==$c)] | length' "$EV")
    if [ "$n" != "1" ]; then BAD+=("$c × $f (verdicts: $n)"); fi
  done
done < "$FILES"

if [ "${#BAD[@]}" -gt 0 ]; then
  FB="Missing or duplicated rubric cells: $(IFS='; '; echo "${BAD[*]}")"
  jq -n --arg f "$FB" '{check:"rubric-coverage", pass:false, feedback:$f}'
else
  jq -n '{check:"rubric-coverage", pass:true, feedback:""}'
fi
