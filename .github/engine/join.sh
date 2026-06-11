#!/usr/bin/env bash
# join.sh <state_workdir> <instance-key> <protocol.json>
# Fan-out barrier evaluator. Reads every branch state file for the instance; once
# ALL branches are terminal (done/failed) and the instance is not yet joined, sets
# the aggregate check-run (success iff every branch is `done`, else failure),
# renders the status comment, marks _instance.yaml joined, and CAS-pushes. Idempotent.
# Env: GITHUB_REPOSITORY, PUBLISH_TOKEN, PR, PR_HEAD_SHA, ENGINE_LOCAL.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

DIR="$1"; INSTANCE="$2"; PROTO="$3"
PID=$(protocol_id "$PROTO")
PR="${PR:-$INSTANCE}"
SHA="${PR_HEAD_SHA:-}"
state_checkout "$DIR"
INF=$(instance_file "$DIR" "$PID" "$INSTANCE")

if [ ! -f "$INF" ]; then echo "[join] no instance file for $PID/$INSTANCE" >&2; exit 0; fi
if [ "$(yq -r '.joined // false' "$INF")" = "true" ]; then
  echo "[join] $PID/$INSTANCE already joined; no-op" >&2; exit 0
fi

# Collect each branch's terminal state.
mapfile -t BRANCHES < <(jq -r '.states[] | select(.kind=="fanout") | .branches[].id' "$PROTO")
all_terminal=true; all_done=true
for b in "${BRANCHES[@]}"; do
  sf=$(state_file "$DIR" "$PID" "$INSTANCE" "$b")
  st=$(yq -r '.state // ""' "$sf" 2>/dev/null || echo "")
  case "$st" in
    done)        : ;;
    failed)      all_done=false ;;
    *)           all_terminal=false ;;
  esac
done

if [ "$all_terminal" != "true" ]; then
  echo "[join] $PID/$INSTANCE not all terminal yet; waiting" >&2; exit 0
fi

if [ "$all_done" = "true" ]; then
  CONCL="success"; TITLE="Review complete"; SUM="All review branches completed."
else
  CONCL="failure"; TITLE="Review incomplete"; SUM="A review branch could not complete; merge is gated."
fi
set_check_run "$PID" "$SHA" completed "$CONCL" "$TITLE" "$SUM"
yq -i '.joined = true' "$INF"
cas_push "$DIR" "$INSTANCE: join → $CONCL (all branches terminal)"
