#!/usr/bin/env bash
# publish-review-from-evidence.sh <evidence.json> <instance-key>
# Grumpy's publication. Trust zone 4 (engine-post): repo-authored, holds the
# publish token — this is NOT a sandboxed check. Reads the evidence, posts a PR
# review, and prints {"conclusion","summary"} on stdout for the engine to relay
# to the grumpy-review check run.
# Env: ENGINE_LOCAL, GITHUB_REPOSITORY, PUBLISH_TOKEN, PR.
set -euo pipefail
EVID="$1"; INSTANCE="${2:-}"

event=$(jq -r 'if any(.files[]?.verdicts[]?; .verdict=="issues-found")
               then "REQUEST_CHANGES" else "APPROVE" end' "$EVID")
body=$(jq -r '
  [ .files[] | .path as $p | .verdicts[] | select(.verdict=="issues-found") | .findings[]
    | "### `\($p)`\n\(.comment)\n```js\n\(.existing_code)\n```" ] as $f |
  if ($f | length) > 0
  then "😤 Grumpy protocol review — \($f | length) issue(s), evidence verified by deterministic checks.\n\n" + ($f | join("\n\n"))
  else "😤 Fine. I examined every file against every category and found nothing worth complaining about. Don'\''t get used to it."
  end' "$EVID")

if [ "$event" = "REQUEST_CHANGES" ]; then
  conclusion="failure"; summary="Grumpy requested changes — resolve them before merging. See the review."
else
  conclusion="success"; summary="Grumpy examined every file × category and found nothing to fix."
fi

if [ "${ENGINE_LOCAL:-0}" = "1" ]; then
  echo "[ENGINE_LOCAL] POST repos/$GITHUB_REPOSITORY/pulls/$PR/reviews event=$event" >&2
  echo "$body" >&2
else
  if ! GH_TOKEN="$PUBLISH_TOKEN" gh api "repos/$GITHUB_REPOSITORY/pulls/$PR/reviews" \
       -f event="$event" -f body="$body" >/dev/null 2>&1; then
    if [ "$event" = "APPROVE" ]; then
      echo "[publish] APPROVE rejected (repo setting?); falling back to COMMENT" >&2
      GH_TOKEN="$PUBLISH_TOKEN" gh api "repos/$GITHUB_REPOSITORY/pulls/$PR/reviews" \
        -f event="COMMENT" -f body="$body" >/dev/null
    else
      echo "[publish] review submission failed for event=$event" >&2
      exit 1
    fi
  fi
fi

jq -nc --arg c "$conclusion" --arg s "$summary" '{conclusion:$c, summary:$s}'
