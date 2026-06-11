#!/usr/bin/env bash
# next.sh <state_workdir> <pr_number> <protocol.json> [head_sha]
# Pure planner: reads (state, protocol), emits action JSON on stdout.
# Creates + pushes initial state on first contact (the one mutation it owns).
# When head_sha is given and differs from the recorded one, the PR was pushed to
# (a new commit) — reset to a fresh review of the new head (the prior review is
# preserved in the state branch's git history). This is what makes re-review on
# `pull_request: synchronize` correct: a terminal instance would otherwise halt.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

DIR="$1"; PR="$2"; PROTO="$3"; HEAD_SHA="${4:-}"
state_checkout "$DIR"
SF=$(state_file "$DIR" "$PR")
MAX=$(jq -r '.states[] | select(.id=="review") | .max_iterations' "$PROTO")

# Write a fresh-review state file for this PR (init or reset-on-new-head).
write_fresh_state() {
  mkdir -p "$(dirname "$SF")"
  PR="$PR" SHA="$HEAD_SHA" yq -n '
    .protocol = "grumpy-review" |
    .instance = "pr-" + env(PR) |
    .state = "review" |
    .iteration = 1 |
    .gates = {} |
    .head_sha = strenv(SHA) |
    .history = []' > "$SF"
}

if [ ! -f "$SF" ]; then
  write_fresh_state
  cas_push "$DIR" "init grumpy/pr-$PR"
  jq -n '{action:"run-agent", iteration:1, feedback:"", reason:""}'
  exit 0
fi

# New commit pushed to the PR → reset and re-review the new head.
STORED_SHA=$(yq -r '.head_sha // ""' "$SF")
if [ -n "$HEAD_SHA" ] && [ "$HEAD_SHA" != "$STORED_SHA" ]; then
  write_fresh_state
  cas_push "$DIR" "pr-$PR: new head $HEAD_SHA → fresh review"
  jq -n '{action:"run-agent", iteration:1, feedback:"", reason:"new head commit"}'
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
