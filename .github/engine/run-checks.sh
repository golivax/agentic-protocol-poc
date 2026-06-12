#!/usr/bin/env bash
# run-checks.sh <protocol.json> <state-id> <evidence.json> <diff.txt> <changed-files.txt>
#
# Data-driven, language-agnostic check runner. Reads the check list for <state-id>
# from the protocol, resolves each check to an executable, runs it against the
# check ABI, and prints the aggregated verdicts as {"results":[{check,pass,feedback}…]}.
#
# Check ABI (any language — bash, python, go, …):
#   <executable> <evidence.json> <diff.txt> <changed-files.txt>
#     → one JSON object {"check","pass","feedback"} on stdout, exit 0.
#
# Resolution per protocol check entry {"run":"<name>", "exec":"<path>"?}:
#   - if "exec" is set, run <protocol-dir>/<exec>
#   - else find <protocol-dir>/checks/<name> or checks/<name>.* (extension-agnostic)
#
# Robustness: a check that is missing, non-executable, crashes (non-zero exit),
# or prints a non-conforming verdict becomes a failing verdict — one bad check
# never aborts the run. The runner holds NO credentials (trust zone 3).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

PROTO="$1"; STATE="$2"; EV="$3"; DIFF="$4"; FILES="$5"
PDIR="$(cd "$(dirname "$PROTO")" && pwd)"

# Resolve the params object for the check-owning node (the branch when BRANCH is
# set, otherwise the state) and forward it to every check as CHECK_PARAMS. Checks
# read their scoped config (e.g. the rubric categories) from this blob instead of
# reaching into protocol.json — the runner never interprets its contents.
if [ -n "${BRANCH:-}" ]; then
  PARAMS=$(jq -c --arg s "$STATE" --arg b "$BRANCH" \
    '.states[] | select(.id==$s) | .branches[]? | select(.id==$b) | .params // {}' "$PROTO")
else
  PARAMS=$(jq -c --arg s "$STATE" '.states[] | select(.id==$s) | .params // {}' "$PROTO")
fi

fail_verdict() { jq -nc --arg c "$1" --arg f "$2" '{check:$c, pass:false, feedback:$f}'; }

RESULTS="[]"
while IFS= read -r entry; do
  name=$(jq -r '.run' <<<"$entry")
  ex=$(jq -r '.exec // empty' <<<"$entry")

  path=""
  res=$(resolve_executable "$PDIR/checks" "$name" "$PDIR" "$ex")
  # resolve_executable returns "OK\t<path>" or "ERR\t<reason>"; split on the first
  # tab (resolved paths are git-managed and never contain a literal tab).
  kind=${res%%$'\t'*}; rest=${res#*$'\t'}
  if [ "$kind" = "ERR" ]; then
    V=$(fail_verdict "$name" "$rest")
  else
    path="$rest"
  fi

  if [ -n "$path" ]; then
    if [ ! -x "$path" ]; then
      V=$(fail_verdict "$name" "check is not executable: $path (chmod +x and add a shebang)")
    else
      out=$(CHECK_PARAMS="$PARAMS" "$path" "$EV" "$DIFF" "$FILES" 2>/dev/null) && rc=0 || rc=$?
      if [ "$rc" -ne 0 ]; then
        V=$(fail_verdict "$name" "check exited $rc (a check must exit 0 and print a JSON verdict)")
      elif ! jq -e 'type=="object" and has("check") and has("pass") and has("feedback")' <<<"$out" >/dev/null 2>&1; then
        V=$(fail_verdict "$name" "check did not print a valid {check,pass,feedback} JSON verdict")
      else
        V=$(jq -c . <<<"$out")
      fi
    fi
  fi

  RESULTS=$(jq -c --argjson v "$V" '. + [$v]' <<<"$RESULTS")
done < <(
  if [ -n "${BRANCH:-}" ]; then
    jq -c --arg s "$STATE" --arg b "$BRANCH" \
      '.states[] | select(.id==$s) | .branches[]? | select(.id==$b) | .checks[]?' "$PROTO"
  else
    jq -c --arg s "$STATE" '.states[] | select(.id==$s) | .checks[]?' "$PROTO"
  fi
)

jq -c '{results: .}' <<<"$RESULTS"
