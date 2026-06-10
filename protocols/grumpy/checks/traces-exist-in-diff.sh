#!/usr/bin/env bash
# Check: every positive claim (existing_code) and negative-attestation trace
# (examined identifier) verifiably exists in the independently fetched diff.
# Usage: traces-exist-in-diff.sh <evidence.json> <diff.txt> <changed-files.txt>
set -euo pipefail
EV="$1"; DIFF="$2"

norm() { tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//'; }
# Strip the +/- prefix from diff content lines so multi-line snippets quoted
# verbatim from the source match after normalization.
DIFF_NORM=$(sed -E 's/^[+-]//' "$DIFF" | norm)

# file_section <path> — the diff lines belonging to one file
file_section() {
  awk -v p="$1" '
    /^diff --git /{ tail = " b/" p; on = (substr($0, length($0)-length(tail)+1) == tail) } on
  ' "$DIFF"
}

BAD=()
while IFS= read -r row; do
  path=$(jq -r '.path' <<<"$row")
  cat=$(jq -r '.category' <<<"$row")
  kind=$(jq -r '.kind' <<<"$row")
  val=$(jq -r '.value' <<<"$row")
  if [ "$kind" = "snippet" ]; then
    v=$(norm <<<"$val")
    case "$DIFF_NORM" in *"$v"*) ;; *) BAD+=("existing_code not in diff ($cat × $path): \"$val\"") ;; esac
  else
    if ! file_section "$path" | grep -qF -- "$val"; then
      BAD+=("examined identifier not in $path's diff ($cat): \"$val\"")
    fi
  fi
done < <(jq -c '
  .files[]? | .path as $p | .verdicts[]? | .category as $c |
  ( (.findings // [])[] | {path:$p, category:$c, kind:"snippet", value:.existing_code} ),
  ( (.examined // [])[] | {path:$p, category:$c, kind:"identifier", value:.} )' "$EV")

if [ "${#BAD[@]}" -gt 0 ]; then
  FB="Unverifiable claims: $(IFS='; '; echo "${BAD[*]}")"
  jq -n --arg f "$FB" '{check:"traces-exist-in-diff", pass:false, feedback:$f}'
else
  jq -n '{check:"traces-exist-in-diff", pass:true, feedback:""}'
fi
