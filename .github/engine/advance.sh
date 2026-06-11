#!/usr/bin/env bash
# advance.sh <state_workdir> <instance-key> <protocol.json> <verdicts.json> <evidence.json>
# The ONLY writer of non-initial state. Reads check verdicts (never agent files,
# except evidence for publication AFTER checks passed), mutates state, CAS-pushes,
# and performs the consequent action: publish / re-dispatch / fail loudly.
# Tolerates a missing state file (recovers from a lost init, e.g. a plan job
# that failed after dispatch) by starting at {state: review, iteration: 1, history: []}.
# Env: AGENT_RUN_ID, GITHUB_REPOSITORY, PUBLISH_TOKEN (reviews+comments),
#      GH_TOKEN (repository_dispatch), ENGINE_LOCAL.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

DIR="$1"; INSTANCE="$2"; PROTO="$3"; VERDICTS="$4"; EVID="$5"
PID=$(protocol_id "$PROTO")
AGENT_STATE=$(jq -r '.states[] | select(.kind=="agent") | .id' "$PROTO")
[ -n "$AGENT_STATE" ] || { echo "[engine] protocol has no agent state" >&2; exit 1; }
state_checkout "$DIR"
SF=$(state_file "$DIR" "$PID" "$INSTANCE")
MAX=$(jq -r --arg s "$AGENT_STATE" '.states[] | select(.id==$s) | .max_iterations' "$PROTO")
PR="${PR:-$INSTANCE}"   # GitHub chrome (review/comment/check-run) targets the PR number from env; instance-key fallback keeps local ENGINE_LOCAL runs working
CR_NAME="$PID"         # check-run name derives from the protocol id

if [ ! -f "$SF" ]; then
  # advance on missing state = recover from lost init
  mkdir -p "$(dirname "$SF")"
  PID="$PID" INST="$INSTANCE" AS="$AGENT_STATE" yq -n '
    .protocol = strenv(PID) |
    .instance = strenv(INST) |
    .state = strenv(AS) |
    .iteration = 1 |
    .gates = {} |
    .history = []' > "$SF"
fi
ITER=$(yq -r '.iteration' "$SF")

ALL_PASS=$(jq -r '(.results | length) > 0 and ([.results[].pass] | all)' "$VERDICTS")
FB=$(jq -r '[.results[] | select(.pass | not) | .feedback] | join("; ")' "$VERDICTS")
[ -n "$FB" ] || FB=$(jq -r 'if (.results|length)==0 then "no check verdicts produced (checks job failure?)" else "" end' "$VERDICTS")
CHECKS_MAP=$(jq -c '[.results[] | {(.check): (if .pass then "pass" else "fail" end)}] | add' "$VERDICTS")

# history entry for this iteration (always recorded)
ITER="$ITER" RID="${AGENT_RUN_ID:-unknown}" CHECKS="$CHECKS_MAP" FB="$FB" yq -i '
  .history += [{
    "iteration": env(ITER),
    "agent_run_id": strenv(RID),
    "checks": env(CHECKS),
    "feedback": strenv(FB)
  }]' "$SF"

# run_publish_hook — resolve and run the protocol's publish-state executable.
# Echoes the hook's {conclusion,summary} JSON; on any resolution/exec failure,
# returns a neutral conclusion so the transition still completes. The hook runs
# trusted in engine-post (zone 4) and may hold the publish token — it is NOT a
# sandboxed check.
run_publish_hook() {
  local pubstate action exec_override pdir res kind path out
  pubstate=$(jq -r --arg s "$AGENT_STATE" '.states[] | select(.id==$s) | .next // empty' "$PROTO")
  action=$(jq -r --arg p "$pubstate" '.states[] | select(.id==$p) | .action // empty' "$PROTO")
  exec_override=$(jq -r --arg p "$pubstate" '.states[] | select(.id==$p) | .exec // empty' "$PROTO")
  pdir="$(cd "$(dirname "$PROTO")" && pwd)"
  if [ -z "$action" ] && [ -z "$exec_override" ]; then
    echo '{"conclusion":"neutral","summary":"no publish action defined"}'; return 0
  fi
  res=$(resolve_executable "$pdir/publish" "$action" "$pdir" "$exec_override")
  kind=${res%%$'\t'*}; path=${res#*$'\t'}
  if [ "$kind" = "ERR" ]; then
    echo "[advance] publish hook unresolved: $path" >&2
    echo '{"conclusion":"neutral","summary":"publish hook unresolved"}'; return 0
  fi
  if [ ! -x "$path" ]; then
    echo "[advance] publish hook not executable: $path" >&2
    echo '{"conclusion":"neutral","summary":"publish hook not executable"}'; return 0
  fi
  if ! out=$("$path" "$EVID" "$INSTANCE"); then
    echo "[advance] publish hook exited nonzero" >&2
    echo '{"conclusion":"neutral","summary":"publish hook failed"}'; return 0
  fi
  if jq -e 'type=="object" and has("conclusion") and has("summary")' <<<"$out" >/dev/null 2>&1; then
    echo "$out"
  else
    echo '{"conclusion":"neutral","summary":"publish hook returned no verdict"}'
  fi
}

# Render the status-comment body as a projection of state.history: one checklist
# line per iteration (rebuilt every transition, so it can't drift), a headline,
# and a link to the durable state file. The comment is a PR-specific view; the
# authoritative record is always agentic-state:<protocol-id>/<instance-key>.yaml.
render_status_body() {
  local sf="$1" headline="$2"   # PID/INSTANCE come from the enclosing scope (as in run_publish_hook)
  local link="https://github.com/$GITHUB_REPOSITORY/blob/agentic-state/$PID/$INSTANCE.yaml"
  # yq → JSON, then jq for the logic (mikefarah yq has no if/then/else or //).
  local lines
  lines=$(yq -o=json '.history' "$sf" | jq -r --arg max "$MAX" '.[] |
    if (.feedback // "") == ""
    then "- ✅ iteration \(.iteration)/\($max) — all checks passed"
    else "- ✗ iteration \(.iteration)/\($max) — \(.feedback)"
    end')
  printf '🔍 **%s · %s**\n\n%s\n\n%s\n\n[Full state & audit trail](%s)\n' \
    "$PID" "$INSTANCE" "$lines" "$headline" "$link"
}

# Branch ordering: mutate state → publish/side-effects that don't touch state →
# upsert_status_comment → cas_push LAST → dispatch.
# upsert before push: it may write status_comment_id into state.
SHA="${PR_HEAD_SHA:-}"   # the PR head commit the check run attaches to (from the orchestrator)
if [ "$ALL_PASS" = "true" ]; then
  yq -i '.state = "done"' "$SF"
  HOOK=$(run_publish_hook)
  CONCL=$(jq -r '.conclusion' <<<"$HOOK")
  CSUM=$(jq -r '.summary' <<<"$HOOK")
  set_check_run "$CR_NAME" "$SHA" completed "$CONCL" "Review complete" "$CSUM"
  upsert_status_comment "$SF" "$PR" "$(render_status_body "$SF" "✅ done — published.")"
  cas_push "$DIR" "$INSTANCE: checks passed at iteration $ITER → published, done"
elif [ "$ITER" -lt "$MAX" ]; then
  NEXT=$((ITER + 1))
  N="$NEXT" yq -i '.iteration = env(N)' "$SF"
  set_check_run "$CR_NAME" "$SHA" in_progress "" "Review in progress" "Iteration $ITER failed checks; retrying as iteration $NEXT/$MAX."
  upsert_status_comment "$SF" "$PR" "$(render_status_body "$SF" "⏳ iteration $ITER failed checks — retrying as iteration $NEXT/$MAX…")"
  cas_push "$DIR" "$INSTANCE: iteration $ITER failed checks → iteration $NEXT"
  gh_api api "repos/$GITHUB_REPOSITORY/dispatches" \
    -f event_type="protocol-continue" \
    -F "client_payload[protocol]=$PID" \
    -F "client_payload[instance]=$INSTANCE"
else
  yq -i '.state = "failed"' "$SF"
  set_check_run "$CR_NAME" "$SHA" completed failure "Review failed" "Could not produce a valid review after $MAX iterations."
  upsert_status_comment "$SF" "$PR" "$(render_status_body "$SF" "❌ **failed** after $MAX iterations.")"
  cas_push "$DIR" "$INSTANCE: iterations exhausted → failed"
fi
