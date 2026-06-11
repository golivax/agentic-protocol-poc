#!/usr/bin/env bash
# Engine library. Sourced by next.sh / advance.sh and by tests.
# Env contract:
#   STATE_REMOTE  git URL for the state branch (https token URL in CI, local path in tests)
#   ENGINE_LOCAL  "1" → all gh API calls become no-op echoes (git still runs)
#   GITHUB_REPOSITORY  owner/repo

STATE_BRANCH="agentic-state"
GIT_ID=(-c user.email="engine@agentic-protocol-poc" -c user.name="protocol-engine")

# gh wrapper: every GitHub API call in the engine goes through this.
gh_api() {
  if [ "${ENGINE_LOCAL:-0}" = "1" ]; then
    echo "[ENGINE_LOCAL] gh $*" >&2
    return 0
  fi
  gh "$@"
}

# state_checkout <dir> — clone the state branch; create it on origin if missing.
state_checkout() {
  local dir="$1"
  if git ls-remote --exit-code --heads "$STATE_REMOTE" "$STATE_BRANCH" >/dev/null 2>&1; then
    git clone -q --branch "$STATE_BRANCH" --single-branch "$STATE_REMOTE" "$dir"
  else
    git init -qb "$STATE_BRANCH" "$dir"
    git -C "$dir" remote add origin "$STATE_REMOTE"
    git -C "$dir" "${GIT_ID[@]}" commit -q --allow-empty -m "init agentic-state"
    git -C "$dir" push -q origin "$STATE_BRANCH"
  fi
}

# cas_push <dir> <message> — commit everything and push fast-forward-only.
# One retry via rebase: state files are per-PR (disjoint), and the per-PR
# concurrency group means a same-file race cannot happen; a rebase therefore
# always applies cleanly. Second rejection = fail loudly. NEVER force-push.
cas_push() {
  local dir="$1" msg="$2"
  git -C "$dir" add -A
  # An empty commit here means the engine pushed without changing state — a bug; fail loudly.
  git -C "$dir" "${GIT_ID[@]}" commit -qm "$msg"
  if ! git -C "$dir" push -q origin "$STATE_BRANCH" 2>/dev/null; then
    echo "[engine] CAS push rejected, rebasing once" >&2
    git -C "$dir" "${GIT_ID[@]}" pull -q --rebase origin "$STATE_BRANCH"
    git -C "$dir" push -q origin "$STATE_BRANCH"
  fi
}

# state_file <dir> <pr> — path to the instance's state file
state_file() { echo "$1/grumpy/pr-$2.yaml"; }

# upsert_status_comment <state_dir> <pr> <body>
# Single engine-owned PR comment, edited in place; id persisted in state.
# NOTE: mutates the state file but does NOT push — callers must cas_push afterwards.
upsert_status_comment() {
  local dir="$1" pr="$2" body="$3"
  local sf; sf=$(state_file "$dir" "$pr")
  local cid; cid=$(yq -r '.status_comment_id // ""' "$sf")
  if [ "${ENGINE_LOCAL:-0}" = "1" ]; then
    echo "[ENGINE_LOCAL] status comment pr#$pr: $body" >&2
    return 0
  fi
  if [ -z "$cid" ]; then
    cid=$(GH_TOKEN="$PUBLISH_TOKEN" gh api "repos/$GITHUB_REPOSITORY/issues/$pr/comments" \
      -f body="$body" --jq '.id')
    CID="$cid" yq -i '.status_comment_id = env(CID)' "$sf"
  else
    GH_TOKEN="$PUBLISH_TOKEN" gh api -X PATCH \
      "repos/$GITHUB_REPOSITORY/issues/comments/$cid" -f body="$body" >/dev/null
  fi
}

# set_check_run <head_sha> <status> <conclusion-or-empty> <title> <summary>
# Emit a "grumpy-review" check run on the PR's head commit so branch protection
# can gate the merge on protocol state. status is queued|in_progress|completed;
# conclusion (success|failure|action_required|…) is required iff status=completed
# and must be empty otherwise. A fresh run each call — GitHub uses the latest per
# name for the merge gate, so no id needs persisting.
#
# Uses the Actions GITHUB_TOKEN ($PUBLISH_TOKEN) with checks:write — the Checks
# API is App/Actions-token only (a PAT cannot create check runs). Best-effort:
# a failure here never breaks a transition.
set_check_run() {
  local sha="$1" status="$2" conclusion="$3" title="$4" summary="$5"
  if [ "${ENGINE_LOCAL:-0}" = "1" ]; then
    echo "[ENGINE_LOCAL] check-run grumpy-review sha=$sha status=$status conclusion=${conclusion:-none} title=$title" >&2
    return 0
  fi
  [ -n "$sha" ] || { echo "[engine] no head sha; skipping check run" >&2; return 0; }
  local args=(-f name="grumpy-review" -f head_sha="$sha" -f status="$status"
              -f "output[title]=$title" -f "output[summary]=$summary")
  [ -n "$conclusion" ] && args+=(-f conclusion="$conclusion")
  GH_TOKEN="$PUBLISH_TOKEN" gh api -X POST "repos/$GITHUB_REPOSITORY/check-runs" "${args[@]}" >/dev/null 2>&1 \
    || echo "[engine] check-run create failed (needs checks:write + Actions token; merge-gating needs branch protection)" >&2
}
