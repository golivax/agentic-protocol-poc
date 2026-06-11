#!/usr/bin/env bash
# publish-review-from-evidence.sh <evidence.json> <instance-key>
# Grumpy's publication. Trust zone 4 (engine-post): repo-authored, holds the
# publish token — this is NOT a sandboxed check. Reads the evidence, posts ONE PR
# review with native inline comments (each issues-found finding anchored to its
# verified line[/range] on its side), and prints {"conclusion","summary"} on
# stdout for the engine to relay to the grumpy-review check run.
# Env: ENGINE_LOCAL, GITHUB_REPOSITORY, PUBLISH_TOKEN, PR.
set -euo pipefail
EVID="$1"; INSTANCE="${2:-}"   # INSTANCE accepted for calling-convention symmetry (unused today)

event=$(jq -r 'if any(.files[]?.verdicts[]?; .verdict=="issues-found")
               then "REQUEST_CHANGES" else "APPROVE" end' "$EVID")

# Build comments[]: one inline comment per issues-found finding. Single-line
# findings carry {path,line,side,body}; ranged findings also add {start_line,start_side}.
# A ranged finding's start_side must equal its side (GitHub API rule); the
# evidence carries no separate start_side, so we reuse .side.
comments=$(jq -c '
  [ .files[]? | .path as $p | .verdicts[]?
    | select(.verdict=="issues-found") | .findings[]?
    | { path: $p, side: .side, line: .line, body: .comment }
      + ( if .start_line then { start_line: .start_line, start_side: .side } else {} end )
  ]' "$EVID")

n=$(jq 'length' <<<"$comments")
nfiles=$(jq '[.[].path] | unique | length' <<<"$comments")

if [ "$event" = "REQUEST_CHANGES" ]; then
  body="😤 Grumpy protocol review — $n issue(s) across $nfiles file(s), evidence verified by deterministic checks. Griping inline."
  conclusion="failure"; summary="Grumpy requested changes — resolve them before merging. See the inline comments."
else
  body="😤 Fine. I examined every file against every category and found nothing worth complaining about. Don't get used to it."
  conclusion="success"; summary="Grumpy examined every file × category and found nothing to fix."
fi

base=$(jq -nc --arg event "$event" --arg body "$body" --argjson comments "$comments" \
  '{event:$event, body:$body, comments:$comments}')

if [ "${ENGINE_LOCAL:-0}" = "1" ]; then
  echo "[ENGINE_LOCAL] POST repos/$GITHUB_REPOSITORY/pulls/$PR/reviews" >&2
  echo "$base" | jq . >&2
else
  # Pin comments to the reviewed head so positions resolve against that commit.
  COMMIT=$(GH_TOKEN="$PUBLISH_TOKEN" gh api "repos/$GITHUB_REPOSITORY/pulls/$PR" --jq .head.sha)
  payload=$(jq -nc --argjson b "$base" --arg c "$COMMIT" '$b + {commit_id:$c}')
  if ! GH_TOKEN="$PUBLISH_TOKEN" gh api "repos/$GITHUB_REPOSITORY/pulls/$PR/reviews" \
       --method POST --input - <<<"$payload" >/dev/null 2>&1; then
    if [ "$event" = "APPROVE" ]; then
      echo "[publish] APPROVE rejected (repo setting?); falling back to COMMENT" >&2
      payload=$(jq -c '.event="COMMENT"' <<<"$payload")
      if ! GH_TOKEN="$PUBLISH_TOKEN" gh api "repos/$GITHUB_REPOSITORY/pulls/$PR/reviews" \
           --method POST --input - <<<"$payload" >/dev/null 2>&1; then
        echo "[publish] COMMENT fallback also failed" >&2
        exit 1
      fi
    else
      echo "[publish] review submission failed for event=$event" >&2
      exit 1
    fi
  fi
fi

jq -nc --arg c "$conclusion" --arg s "$summary" '{conclusion:$c, summary:$s}'
