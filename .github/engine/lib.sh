#!/usr/bin/env bash
# Engine library. Sourced by next.sh / advance.sh and by tests.
# Env contract:
#   STATE_REMOTE  git URL for the state branch (https token URL in CI, local path in tests)
#   ENGINE_LOCAL  "1" → all gh API calls become no-op echoes (git still runs)
#   GITHUB_REPOSITORY  owner/repo

STATE_BRANCH="agentic-state"
GIT_ID=(-c user.email="engine@agentic-protocol-poc" -c user.name="protocol-engine")

# protocol_id <protocol.json> — the protocol's id (used as the state-path prefix
# and the status-comment display name). The engine NEVER hardcodes it.
protocol_id() { jq -r '.name' "$1"; }

# gh wrapper: every GitHub API call in the engine goes through this.
gh_api() {
  if [ "${ENGINE_LOCAL:-0}" = "1" ]; then
    echo "[ENGINE_LOCAL] gh $*" >&2
    return 0
  fi
  gh "$@"
}

# resolve_executable <search-dir> <name> <protocol-dir> <explicit-exec-or-empty>
# One resolution rule for any protocol-provided executable (a check OR a publish
# hook). Prints "OK\t<path>" when a single file is found, or "ERR\t<reason>".
# Resolution:
#   - if <explicit-exec> is set, use <protocol-dir>/<exec>
#   - else match <search-dir>/<name> or <search-dir>/<name>.* (extension-agnostic)
# Executability is the caller's responsibility (so it can word its own verdict).
resolve_executable() {
  local sdir="$1" name="$2" pdir="$3" ex="$4"
  if [ -n "$ex" ]; then
    if [ -f "$pdir/$ex" ]; then printf 'OK\t%s\n' "$pdir/$ex"
    else printf 'ERR\tdeclared exec not found: %s\n' "$ex"; fi
    return 0
  fi
  local g matches=()
  for g in "$sdir/$name" "$sdir/$name".*; do
    [ -f "$g" ] && matches+=("$g")
  done
  if   [ "${#matches[@]}" -eq 0 ]; then printf 'ERR\tno executable found (looked for %s/%s or %s/%s.*)\n' "$sdir" "$name" "$sdir" "$name"
  elif [ "${#matches[@]}" -gt 1 ]; then printf 'ERR\tambiguous: multiple files match %s/%s.* (%s); use an explicit "exec"\n' "$sdir" "$name" "${matches[*]}"
  else printf 'OK\t%s\n' "${matches[0]}"
  fi
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

# state_file <dir> <protocol-id> <instance-key> [branch]
#   no branch → single-agent path        <dir>/<pid>/<instance>.yaml
#   branch    → fan-out per-branch path   <dir>/<pid>/<instance>/<branch>.yaml
state_file() {
  if [ -n "${4:-}" ]; then echo "$1/$2/$3/$4.yaml"; else echo "$1/$2/$3.yaml"; fi
}

# instance_file <dir> <protocol-id> <instance-key> — shared per-instance bookkeeping
# (head_sha, status_comment_id, joined flag) for a fan-out instance.
instance_file() { echo "$1/$2/$3/_instance.yaml"; }

# render_fanout_status_body <state_dir> <pid> <instance> <protocol.json>
# Pure projection of ALL fan-out branch state files into ONE combined PR-comment
# body: a "**<branch>**" section per branch (its per-iteration checklist), an
# overall headline derived from the branches' .state values, and a link to the
# state DIRECTORY (the fan-out state lives in <pid>/<instance>/, not a single file,
# so the link is tree/… not blob/…yaml). A missing branch file renders "_pending_"
# and an empty history renders "_no iterations yet_" so partial / early / local
# state never aborts under `set -e`. Echoes the body; performs no API calls.
render_fanout_status_body() {
  local dir="$1" pid="$2" instance="$3" proto="$4"
  local link="https://github.com/$GITHUB_REPOSITORY/tree/$STATE_BRANCH/$pid/$instance"
  local sections="" states="" b sf max lines st
  while IFS= read -r b; do
    sf=$(state_file "$dir" "$pid" "$instance" "$b")
    if [ -f "$sf" ]; then
      max=$(jq -r --arg b "$b" '.states[] | select(.kind=="fanout") | .branches[] | select(.id==$b) | .max_iterations' "$proto")
      # yq → JSON then jq (mikefarah yq has no if/then/else or //); format kept in sync with render_status_body in advance.sh.
      lines=$(yq -o=json '.history' "$sf" | jq -r --arg max "$max" '.[] |
        if (.feedback // "") == ""
        then "- ✅ iteration \(.iteration)/\($max) — all checks passed"
        else "- ✗ iteration \(.iteration)/\($max) — \(.feedback)"
        end')
      [ -n "$lines" ] || lines="_no iterations yet_"
      st=$(yq -r '.state // ""' "$sf")
    else
      lines="_pending_"; st="pending"
    fi
    states="$states $st"
    sections="${sections}**${b}**"$'\n\n'"${lines}"$'\n\n'
  done < <(jq -r '.states[] | select(.kind=="fanout") | .branches[].id' "$proto")

  # Headline from the collected branch states: any non-terminal → in progress;
  # else all terminal and ≥1 failed → incomplete; else all done → complete.
  local any_active=false any_failed=false
  for st in $states; do
    case "$st" in
      done)   : ;;
      failed) any_failed=true ;;
      *)      any_active=true ;;
    esac
  done
  local headline
  if   [ "$any_active" = true ]; then headline="⏳ Review in progress…"
  elif [ "$any_failed" = true ]; then headline="❌ Review incomplete — a branch could not complete; merge is gated."
  else                                headline="✅ Review complete — published."
  fi

  printf '🔍 **%s · %s**\n\n%s%s\n\n[Full state & audit trail](%s)\n' \
    "$pid" "$instance" "$sections" "$headline" "$link"
}

# upsert_status_comment <state_file> <pr> <body>
# Single engine-owned PR comment, edited in place; id persisted in state.
# NOTE: mutates the state file but does NOT push — callers must cas_push afterwards.
upsert_status_comment() {
  local sf="$1" pr="$2" body="$3"
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

# set_check_run <name> <head_sha> <status> <conclusion-or-empty> <title> <summary>
# Emit a check run on the PR's head commit so branch protection can gate the merge
# on protocol state. status is queued|in_progress|completed; conclusion
# (success|failure|action_required|…) is required iff status=completed and must be
# empty otherwise. A fresh run each call — GitHub uses the latest per name for the
# merge gate, so no id needs persisting.
#
# Uses the Actions GITHUB_TOKEN ($PUBLISH_TOKEN) with checks:write — the Checks API
# is App/Actions-token only (a PAT cannot create check runs). Best-effort: a failure
# here never breaks a transition.
set_check_run() {
  local name="$1" sha="$2" status="$3" conclusion="$4" title="$5" summary="$6"
  if [ "${ENGINE_LOCAL:-0}" = "1" ]; then
    echo "[ENGINE_LOCAL] check-run $name sha=$sha status=$status conclusion=${conclusion:-none} title=$title summary=$summary" >&2
    return 0
  fi
  [ -n "$sha" ] || { echo "[engine] no head sha; skipping check run" >&2; return 0; }
  local args=(-f name="$name" -f head_sha="$sha" -f status="$status"
              -f "output[title]=$title" -f "output[summary]=$summary")
  [ -n "$conclusion" ] && args+=(-f conclusion="$conclusion")
  GH_TOKEN="$PUBLISH_TOKEN" gh api -X POST "repos/$GITHUB_REPOSITORY/check-runs" "${args[@]}" >/dev/null 2>&1 \
    || echo "[engine] check-run create failed (needs checks:write + Actions token; merge-gating needs branch protection)" >&2
}
