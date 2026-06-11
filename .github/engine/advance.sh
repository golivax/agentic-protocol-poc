#!/usr/bin/env bash
# advance.sh <state_workdir> <pr> <protocol.json> <verdicts.json> <evidence.json>
# The ONLY writer of non-initial state. Reads check verdicts (never agent files,
# except evidence for publication AFTER checks passed), mutates state, CAS-pushes,
# and performs the consequent action: publish / re-dispatch / fail loudly.
# Tolerates a missing state file (recovers from a lost init, e.g. a plan job
# that failed after dispatch) by starting at {state: review, iteration: 1, history: []}.
# Env: AGENT_RUN_ID, GITHUB_REPOSITORY, PUBLISH_TOKEN (reviews+comments),
#      GH_TOKEN (repository_dispatch), ENGINE_LOCAL.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

DIR="$1"; PR="$2"; PROTO="$3"; VERDICTS="$4"; EVID="$5"
state_checkout "$DIR"
SF=$(state_file "$DIR" "$PR")
MAX=$(jq -r '.states[] | select(.id=="review") | .max_iterations' "$PROTO")

if [ ! -f "$SF" ]; then
  # advance on missing state = recover from lost init
  mkdir -p "$(dirname "$SF")"
  PR="$PR" yq -n '
    .protocol = "grumpy-review" |
    .instance = "pr-" + env(PR) |
    .state = "review" |
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

publish_review() {
  local event body
  event=$(jq -r 'if any(.files[]?.verdicts[]?; .verdict=="issues-found")
                 then "REQUEST_CHANGES" else "APPROVE" end' "$EVID")
  body=$(jq -r '
    [ .files[] | .path as $p | .verdicts[] | select(.verdict=="issues-found") | .findings[]
      | "### `\($p)`\n\(.comment)\n```js\n\(.existing_code)\n```" ] as $f |
    if ($f | length) > 0
    then "😤 Grumpy protocol review — \($f | length) issue(s), evidence verified by deterministic checks.\n\n" + ($f | join("\n\n"))
    else "😤 Fine. I examined every file against every category and found nothing worth complaining about. Don'\''t get used to it."
    end' "$EVID")
  if [ "${ENGINE_LOCAL:-0}" = "1" ]; then
    echo "[ENGINE_LOCAL] POST repos/$GITHUB_REPOSITORY/pulls/$PR/reviews event=$event" >&2
    echo "$body" >&2
    return 0
  fi
  # APPROVE requires the repo's "Allow GitHub Actions to approve pull requests"
  # setting; if it's off (or the bot otherwise can't approve), fall back to a
  # COMMENT review so a clean result still publishes and the state still advances.
  if ! GH_TOKEN="$PUBLISH_TOKEN" gh api "repos/$GITHUB_REPOSITORY/pulls/$PR/reviews" \
       -f event="$event" -f body="$body" >/dev/null 2>&1; then
    if [ "$event" = "APPROVE" ]; then
      echo "[advance] APPROVE rejected (repo setting?); falling back to COMMENT" >&2
      GH_TOKEN="$PUBLISH_TOKEN" gh api "repos/$GITHUB_REPOSITORY/pulls/$PR/reviews" \
        -f event="COMMENT" -f body="$body" >/dev/null
    else
      echo "[advance] review submission failed for event=$event" >&2
      return 1
    fi
  fi
}

# Render the status-comment body as a projection of state.history: one checklist
# line per iteration (rebuilt every transition, so it can't drift), a headline,
# and a link to the durable state file. The comment is a PR-specific view; the
# authoritative record is always agentic-state:grumpy/pr-<N>.yaml.
render_status_body() {
  local sf="$1" pr="$2" headline="$3"
  local link="https://github.com/$GITHUB_REPOSITORY/blob/agentic-state/grumpy/pr-$pr.yaml"
  # yq → JSON, then jq for the logic (mikefarah yq has no if/then/else or //).
  local lines
  lines=$(yq -o=json '.history' "$sf" | jq -r --arg max "$MAX" '.[] |
    if (.feedback // "") == ""
    then "- ✅ iteration \(.iteration)/\($max) — all checks passed"
    else "- ✗ iteration \(.iteration)/\($max) — \(.feedback)"
    end')
  printf '🔍 **grumpy-review · pr-%s**\n\n%s\n\n%s\n\n[Full state & audit trail](%s)\n' \
    "$pr" "$lines" "$headline" "$link"
}

# Branch ordering: mutate state → publish/side-effects that don't touch state →
# upsert_status_comment → cas_push LAST → dispatch.
# upsert before push: it may write status_comment_id into state.
SHA="${PR_HEAD_SHA:-}"   # the PR head commit the check run attaches to (from the orchestrator)
if [ "$ALL_PASS" = "true" ]; then
  yq -i '.state = "done"' "$SF"
  publish_review
  # Check run mirrors the review verdict so branch protection can gate the merge:
  # issues-found → failure (changes must be addressed); clean → success.
  # NB: use `failure`, not `action_required` — the latter makes GitHub render a
  # phantom "workflow awaiting approval" prompt on the PR (it's meant for "the App
  # needs you to authorize something", not "the review failed").
  if jq -e 'any(.files[]?.verdicts[]?; .verdict=="issues-found")' "$EVID" >/dev/null 2>&1; then
    set_check_run "$SHA" completed failure "Changes requested" "Grumpy requested changes — resolve them before merging. See the review."
  else
    set_check_run "$SHA" completed success "Approved" "Grumpy examined every file × category and found nothing to fix."
  fi
  upsert_status_comment "$DIR" "$PR" "$(render_status_body "$SF" "$PR" "✅ **done** — review published.")"
  cas_push "$DIR" "pr-$PR: checks passed at iteration $ITER → published, done"
elif [ "$ITER" -lt "$MAX" ]; then
  NEXT=$((ITER + 1))
  N="$NEXT" yq -i '.iteration = env(N)' "$SF"
  set_check_run "$SHA" in_progress "" "Grumpy review in progress" "Iteration $ITER failed checks; retrying as iteration $NEXT/$MAX."
  upsert_status_comment "$DIR" "$PR" "$(render_status_body "$SF" "$PR" "⏳ iteration $ITER failed checks — retrying as iteration $NEXT/$MAX…")"
  cas_push "$DIR" "pr-$PR: iteration $ITER failed checks → iteration $NEXT"
  gh_api api "repos/$GITHUB_REPOSITORY/dispatches" \
    -f event_type="grumpy-continue" -F "client_payload[pr]=$PR"
else
  yq -i '.state = "failed"' "$SF"
  set_check_run "$SHA" completed failure "Review failed" "Could not produce a valid review after $MAX iterations."
  upsert_status_comment "$DIR" "$PR" "$(render_status_body "$SF" "$PR" "❌ **failed** after $MAX iterations.")"
  cas_push "$DIR" "pr-$PR: iterations exhausted → failed"
fi
