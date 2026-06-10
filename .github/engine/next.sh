#!/usr/bin/env bash
# next.sh <state_workdir> <pr_number> <protocol.json>
# Pure planner: reads (state, protocol), emits action JSON on stdout.
# Creates + pushes initial state on first contact (the one mutation it owns).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

DIR="$1"; PR="$2"; PROTO="$3"
state_checkout "$DIR"
SF=$(state_file "$DIR" "$PR")
MAX=$(jq -r '.states[] | select(.id=="review") | .max_iterations' "$PROTO")

if [ ! -f "$SF" ]; then
  mkdir -p "$(dirname "$SF")"
  PR="$PR" yq -n '
    .protocol = "grumpy-review" |
    .instance = "pr-" + env(PR) |
    .state = "review" |
    .iteration = 1 |
    .gates = {} |
    .history = []' > "$SF"
  cas_push "$DIR" "init grumpy/pr-$PR"
  jq -n '{action:"run-agent", iteration:1, feedback:"", reason:""}'
  exit 0
fi

STATE=$(yq -r '.state' "$SF")
ITER=$(yq -r '.iteration' "$SF")
case "$STATE" in
  done|failed)
    jq -n --arg s "$STATE" '{action:"halt", iteration:0, feedback:"", reason:("instance is terminal: " + $s)}'
    exit 0 ;;
esac
if [ "$ITER" -gt "$MAX" ]; then
  jq -n '{action:"halt", iteration:0, feedback:"", reason:"iterations exhausted"}'
  exit 0
fi

# Length-guard: plain .history[-1] errors under yq v4 when history is [] (re-run before first iteration completes).
FB=$(yq -r '.history | select(length > 0) | .[-1].feedback // ""' "$SF")
jq -n --argjson i "$ITER" --arg f "$FB" '{action:"run-agent", iteration:$i, feedback:$f, reason:""}'
