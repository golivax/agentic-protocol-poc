#!/usr/bin/env bash
# next.sh <state_workdir> <instance-key> <protocol.json> <command> [head_sha]
# Pure planner: reads (state, protocol, command), emits an action JSON on stdout.
# The WORKFLOW decides what an event means and passes a command; the planner never
# sniffs events. Commands:
#   start    external request — fresh review from a clean slate (Absent or Terminal);
#            leave an in-flight review undisturbed (Active → halt).
#   reset    unconditional fresh review (a new head commit invalidates the old one).
#   continue the engine's own iterate loop — resume Active; halt on Terminal.
# head_sha (optional) is recorded as instance metadata (the check-run target); it is
# NEVER compared to decide policy — that decision lives in the workflow.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

DIR="$1"; INSTANCE="$2"; PROTO="$3"; COMMAND="$4"; HEAD_SHA="${5:-}"
BRANCH="${BRANCH:-}"
PID=$(protocol_id "$PROTO")
# The "agent unit" (its id + max_iterations) comes from a fan-out BRANCH when
# BRANCH is set, else from the single-agent state. Single-agent path is the
# regression-guarded baseline and must stay byte-for-byte identical.
if [ -n "$BRANCH" ]; then
  AGENT_STATE=$(jq -r --arg b "$BRANCH" '.states[] | select(.kind=="fanout") | .branches[] | select(.id==$b) | .id' "$PROTO")
  [ -n "$AGENT_STATE" ] && [ "$AGENT_STATE" != "null" ] || { echo "[engine] no branch '$BRANCH' in protocol" >&2; exit 1; }
  MAX=$(jq -r --arg b "$BRANCH" '.states[] | select(.kind=="fanout") | .branches[] | select(.id==$b) | .max_iterations' "$PROTO")
else
  AGENT_STATE=$(jq -r '.states[] | select(.kind=="agent") | .id' "$PROTO")
  [ -n "$AGENT_STATE" ] || { echo "[engine] protocol has no agent state" >&2; exit 1; }
  MAX=$(jq -r --arg s "$AGENT_STATE" '.states[] | select(.id==$s) | .max_iterations' "$PROTO")
fi
[ -n "$MAX" ] && [ "$MAX" != "null" ] || { echo "[engine] agent unit '$AGENT_STATE' has no max_iterations" >&2; exit 1; }
# LIFE_STATE is the value a live state file's .state carries while the agent unit
# is in flight, and is what both write_fresh_state stamps and the lifecycle check
# compares against. Single-agent: the agent state id. Fan-out branch: the owning
# fan-out state's id (a branch's per-branch file records the fan-out state, not the
# branch id). For grumpy these are both "review", so the single-agent path is
# byte-for-byte unchanged.
if [ -n "$BRANCH" ]; then
  LIFE_STATE=$(jq -r '.states[] | select(.kind=="fanout") | .id' "$PROTO")
else
  LIFE_STATE="$AGENT_STATE"
fi
state_checkout "$DIR"
SF=$(state_file "$DIR" "$PID" "$INSTANCE" "$BRANCH")   # $BRANCH empty → single-agent path

write_fresh_state() {
  mkdir -p "$(dirname "$SF")"
  PID="$PID" INST="$INSTANCE" AS="$LIFE_STATE" SHA="$HEAD_SHA" yq -n '
    .protocol = strenv(PID) |
    .instance = strenv(INST) |
    .state = strenv(AS) |
    .iteration = 1 |
    .gates = {} |
    .head_sha = strenv(SHA) |
    .history = []' > "$SF"
}
emit_run_agent() { jq -n --argjson i "$1" --arg f "$2" --arg r "$3" '{action:"run-agent", iteration:$i, feedback:$f, reason:$r}'; }
emit_halt()      { jq -n --arg r "$1" '{action:"halt", iteration:0, feedback:"", reason:$r}'; }

start_fresh() {
  write_fresh_state
  cas_push "$DIR" "$PID/$INSTANCE: fresh review ($COMMAND)"
  emit_run_agent 1 "" "$COMMAND"
}

# Determine the instance lifecycle from the (optional) state file. Defensive reads
# (// fallbacks) keep a malformed/partial state file from aborting under `set -e`.
# Literal equality, NOT a case pattern: a case glob would treat metacharacters in
# $LIFE_STATE (if a future protocol used any) as wildcards.
LIFECYCLE="absent"; ITER=0
if [ -f "$SF" ]; then
  STATE=$(yq -r '.state // ""' "$SF")
  ITER=$(yq -r '.iteration // 0' "$SF")
  if [ "$STATE" = "$LIFE_STATE" ]; then
    # iterations 1..MAX are all valid attempts; > MAX means the loop is spent.
    if [ "$ITER" -gt "$MAX" ]; then LIFECYCLE="terminal"; else LIFECYCLE="active"; fi
  else
    LIFECYCLE="terminal"   # done / failed / any non-agent terminal
  fi
fi

case "$COMMAND" in
  reset) start_fresh ;;
  start)
    case "$LIFECYCLE" in
      absent|terminal) start_fresh ;;
      active)          emit_halt "review already in flight at iteration $ITER" ;;
    esac ;;
  continue)
    case "$LIFECYCLE" in
      absent)   start_fresh ;;
      active)   FB=$(yq -r '.history | select(length > 0) | .[-1].feedback // ""' "$SF")
                emit_run_agent "$ITER" "$FB" "resume" ;;
      terminal) emit_halt "instance is terminal" ;;
    esac ;;
  *) echo "[next] unknown command: $COMMAND" >&2; exit 2 ;;
esac
