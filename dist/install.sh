#!/usr/bin/env bash
set -euo pipefail

# ── defaults (overridable by flags) ───────────────────────────────────────────
SOURCE="golivax/agentic-protocol-poc"
REF="main"
DRY_RUN=0
FORCE=0
DRIFTED=""
BASE_URL=""
SUBCMD=""
PROTOCOLS=()
declare -A AGENT_ENGINES=()

die() { echo "error: $*" >&2; exit 1; }
log() { echo "▸ $*" >&2; }

# Fetch one file's raw contents from the source repo at the ref.
gh_raw() { gh api "repos/${SOURCE}/contents/$1?ref=${REF}" --jq '.content' | base64 -d; }

# List immediate child names of a directory in the source tree (type filterable).
gh_tree_children() {
  local dir="$1" kind="$2"
  gh api "repos/${SOURCE}/git/trees/${REF}:${dir}" \
    --jq ".tree[] | select(.type == \"${kind}\") | .path" 2>/dev/null || true
}

parse_args() {
  SUBCMD="${1:-}"; shift || true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --source) SOURCE="$2"; shift 2 ;;
      --ref) REF="$2"; shift 2 ;;
      --base-url) BASE_URL="$2"; shift 2 ;;
      --dry-run) DRY_RUN=1; shift ;;
      --force) FORCE=1; shift ;;
      -*) die "unknown flag: $1" ;;
      *) PROTOCOLS+=("$1"); shift ;;
    esac
  done
}

cmd_list() { gh_tree_children ".github/agent-factory/protocols" "tree"; }

case_dispatch() {
  case "$SUBCMD" in
    list) cmd_list ;;
    install) cmd_install ;;
    update) cmd_update ;;
    *) die "usage: install.sh {install|update|list} [protocol...] [--source o/r] [--ref R] [--base-url URL] [--dry-run] [--force]" ;;
  esac
}

WORKDIR=""
cleanup() { [[ -n "$WORKDIR" && -d "$WORKDIR" ]] && rm -rf "$WORKDIR"; }
trap cleanup EXIT

bootstrap_helpers() {
  WORKDIR="$(mktemp -d)"
  gh_raw "dist/manifest.json" > "$WORKDIR/manifest.json"
  gh_raw "dist/resolve.py"    > "$WORKDIR/resolve.py"
  gh_raw "dist/receipt.py"    > "$WORKDIR/receipt.py"
  # adopt manifest defaults the caller didn't override
  ENGINE_VERSION="$(python3 - "$WORKDIR/manifest.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["engine_version"])
PY
)"
  MIN_GH_AW="$(python3 - "$WORKDIR/manifest.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["min_gh_aw_version"])
PY
)"
}

preflight() {
  command -v git >/dev/null || die "git not found"
  command -v gh  >/dev/null || die "gh not found (install GitHub CLI ≥ 2.0)"
  gh auth status >/dev/null 2>&1 || die "gh not authenticated — run: gh auth login --scopes repo,workflow"
  local exts; exts="$(gh extension list 2>/dev/null || true)"
  grep -q 'github/gh-aw' <<<"$exts" || die "gh-aw missing — run: gh extension install github/gh-aw"
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "run inside a clone of the target repo"
  local slug; slug="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
  gh api "repos/${slug}" --jq '.permissions.push' | grep -q true \
    || die "you need write access to ${slug}"
}

# Echo the repo-relative files a protocol contributes (engine + workflows handled
# separately as the shared/common set).
protocol_files() {
  local proto="$1"
  gh api "repos/${SOURCE}/git/trees/${REF}:.github/agent-factory/protocols/${proto}?recursive=1" \
    --jq '.tree[] | select(.type=="blob") | .path' \
  | while IFS= read -r p; do printf '%s\n' ".github/agent-factory/protocols/${proto}/${p}"; done
}

common_files() {
  python3 - "$WORKDIR/manifest.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
for w in m["engine_workflows"]:
    print(w)
PY
  local engine_dir
  engine_dir="$(python3 - "$WORKDIR/manifest.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["engine_dir"])
PY
)"
  gh api "repos/${SOURCE}/git/trees/${REF}:${engine_dir}" \
    --jq '.tree[] | select(.type=="blob") | select(.path|endswith(".py")) | .path' \
  | while IFS= read -r p; do printf '%s\n' "${engine_dir}/${p}"; done
}

print_plan() {
  echo "# source: ${SOURCE}@${REF}  engine_version: ${ENGINE_VERSION}"
  echo "# common:"; common_files | sed 's/^/  /'
  local p
  for p in "${PROTOCOLS[@]}"; do
    echo "# protocol ${p}:"; protocol_files "$p" | sed 's/^/  /'
    echo "# agents ${p}:"
    gh_raw ".github/agent-factory/protocols/${p}/protocol.json" \
      | python3 "$WORKDIR/resolve.py" agents /dev/stdin | sed 's/^/  /'
  done
}

fetch_one() {  # <repo-relative-path>
  local path="$1"
  if [[ "$FORCE" != 1 && -n "$DRIFTED" ]] && grep -qxF "$path" <<<"$DRIFTED"; then
    log "skipping locally-modified ${path} (use --force to overwrite)"
    return 0
  fi
  mkdir -p "$(dirname "$path")"; gh_raw "$path" > "$path"
}

fetch_unit() {
  local f
  while read -r f; do [[ -n "$f" ]] && fetch_one "$f"; done < <(common_files)
  local p
  for p in "${PROTOCOLS[@]}"; do
    while read -r f; do [[ -n "$f" ]] && fetch_one "$f"; done < <(protocol_files "$p")
  done
}

# Prompt once per agent for an engine, then add it from source with that engine.
install_agents() {
  local p="$1" agent engine
  while read -r agent; do
    [[ -z "$agent" ]] && continue
    read -r -p "Engine for ${agent} [claude/copilot/codex/gemini] (default claude): " engine </dev/tty || engine=""
    engine="${engine:-claude}"
    AGENT_ENGINES["$agent"]="$engine"
    log "adding ${agent} (engine: ${engine})"
    gh aw add "${SOURCE}/workflows/${agent}.md@${REF}" --engine "$engine" --force
  done < <(gh_raw ".github/agent-factory/protocols/${p}/protocol.json" \
            | python3 "$WORKDIR/resolve.py" agents /dev/stdin)
}

# Explicit, opt-in, previewed custom-endpoint configuration. NEVER silent.
configure_endpoints() {
  local any=0 agent engine
  for agent in "${!AGENT_ENGINES[@]}"; do
    [[ "${AGENT_ENGINES[$agent]}" == "claude" ]] && any=1
  done
  [[ "$any" == 1 ]] || return 0
  local ans; read -r -p "Configure a custom Anthropic endpoint for the Claude workflows? [y/N]: " ans </dev/tty || ans="n"
  [[ "$ans" == "y" || "$ans" == "Y" ]] || return 0
  local url; read -r -p "  Base URL (default ${BASE_URL:-https://api.anthropic.com}): " url </dev/tty || url=""
  url="${url:-${BASE_URL:-https://api.anthropic.com}}"
  echo "  The following engine.env will be added to each Claude workflow and recompiled:"
  echo "    env:"
  echo "      ANTHROPIC_BASE_URL: ${url}"
  echo "      ANTHROPIC_AUTH_TOKEN: \${{ secrets.ANTHROPIC_API_KEY }}"
  local ok; read -r -p "  Apply? [y/N]: " ok </dev/tty || ok="n"
  [[ "$ok" == "y" || "$ok" == "Y" ]] || { log "skipped endpoint config"; return 0; }
  for agent in "${!AGENT_ENGINES[@]}"; do
    [[ "${AGENT_ENGINES[$agent]}" == "claude" ]] || continue
    BASE_URL_INJECT="$url" python3 - ".github/workflows/${agent}.md" <<'PY'
import os, sys, re
md = sys.argv[1]; url = os.environ["BASE_URL_INJECT"]
text = open(md).read()
authtok = "    ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}\n"
baseurl = f"    ANTHROPIC_BASE_URL: {url}\n"
# Idempotent: the source default may ALREADY carry engine.env. Never append a
# second env: block (invalid YAML).
if re.search(r"(?m)^    ANTHROPIC_BASE_URL:.*$", text):
    # overwrite the existing base URL line in place
    text = re.sub(r"(?m)^    ANTHROPIC_BASE_URL:.*$", baseurl.rstrip("\n"), text)
elif re.search(r"(?m)^  env:\s*$", text):
    # an env: block exists but no base URL — add our two lines under it
    text = re.sub(r"(?m)^(  env:\s*\n)", lambda m: m.group(1) + baseurl + authtok, text, count=1)
else:
    # no env: at all — insert a fresh env: block right under engine:
    block = "  env:\n" + baseurl + authtok
    text = re.sub(r"(?m)^(engine:\n(?:[ \t].*\n)*?)", lambda m: m.group(1) + block, text, count=1)
open(md, "w").write(text)
PY
  done
  gh aw compile
}

ensure_state_branch() {
  local slug; slug="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
  if gh api "repos/${slug}/branches/agentic-state" >/dev/null 2>&1; then
    log "agentic-state branch already exists — leaving it"
    return 0
  fi
  log "creating orphan agentic-state branch"
  local cur; cur="$(git rev-parse --abbrev-ref HEAD)"
  git switch --orphan agentic-state
  git commit --allow-empty -m "init agentic-state"
  git push -u origin agentic-state
  git switch "$cur"
}

ensure_dispatch_token() {
  local slug; slug="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
  if gh secret list --repo "$slug" 2>/dev/null | grep -q '^POC_DISPATCH_TOKEN'; then
    log "POC_DISPATCH_TOKEN already set"
    return 0
  fi
  local tok; read -r -s -p "Enter POC_DISPATCH_TOKEN (PAT with repo+workflow scopes): " tok </dev/tty; echo >/dev/tty
  [[ -n "$tok" ]] || die "POC_DISPATCH_TOKEN is required"
  gh secret set POC_DISPATCH_TOKEN --repo "$slug" --body "$tok"
}

write_install_receipt() {
  local protos_json files=() p ver
  protos_json="{"
  for p in "${PROTOCOLS[@]}"; do
    ver="$(python3 - ".github/agent-factory/protocols/${p}/protocol.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["version"])
PY
)"
    protos_json="${protos_json}\"${p}\":\"${ver}\","
  done
  protos_json="${protos_json%,}}"
  # the installed file set = everything we wrote/touched, tracked by git
  mapfile -t files < <(git ls-files --others --modified --exclude-standard .github | sort -u)
  mkdir -p .github/agent-factory
  python3 "$WORKDIR/receipt.py" write \
    .github/agent-factory/.install.json "$SOURCE" "$REF" "$ENGINE_VERSION" "$protos_json" "." "${files[@]}"
}

finalize_commit() {
  git add -A .github
  git commit -m "chore: install agentic protocol(s): ${PROTOCOLS[*]}"
  git push
}

cmd_install() {
  [[ ${#PROTOCOLS[@]} -gt 0 ]] || die "name at least one protocol (see: install.sh list)"
  preflight
  bootstrap_helpers
  if [[ "$DRY_RUN" == 1 ]]; then print_plan; exit 0; fi
  # compatibility guard (refuse before mutating)
  local p minv
  for p in "${PROTOCOLS[@]}"; do
    minv="$(gh_raw ".github/agent-factory/protocols/${p}/protocol.json" \
      | python3 -c "import json,sys;print(json.load(sys.stdin).get('min_engine_version',''))")"
    python3 "$WORKDIR/receipt.py" compat "$ENGINE_VERSION" "$minv" \
      || die "protocol ${p} needs engine ≥ ${minv}, but source ships ${ENGINE_VERSION}"
  done
  fetch_unit
  for p in "${PROTOCOLS[@]}"; do install_agents "$p"; done
  configure_endpoints
  ensure_state_branch
  ensure_dispatch_token
  write_install_receipt
  finalize_commit
  log "done — open a PR or comment a trigger to run the protocol"
}
cmd_update() {
  preflight
  bootstrap_helpers
  local rcpt=".github/agent-factory/.install.json"
  [[ -f "$rcpt" ]] || die "no install receipt found ($rcpt) — run install first"
  # default to the protocols recorded in the receipt
  if [[ ${#PROTOCOLS[@]} -eq 0 ]]; then
    mapfile -t PROTOCOLS < <(python3 -c "import json;print('\n'.join(json.load(open('$rcpt'))['protocols']))")
  fi
  local old_ev; old_ev="$(python3 -c "import json;print(json.load(open('$rcpt'))['engine_version'])")"
  if python3 -c "import sys; sys.path.insert(0,'$WORKDIR'); import receipt; sys.exit(0 if receipt.is_breaking_bump('$old_ev','$ENGINE_VERSION') else 1)"; then
    log "WARNING: engine ${old_ev} → ${ENGINE_VERSION} is a breaking bump; finish open reviews before updating or expect to restart them"
  fi
  # drift check (skip locally-modified unless --force)
  local drifted; drifted="$(python3 "$WORKDIR/receipt.py" drift "$rcpt" .)"
  if [[ -n "$drifted" && "$FORCE" != 1 ]]; then
    log "locally-modified files will be SKIPPED (use --force to overwrite):"; echo "$drifted" >&2
  fi
  DRIFTED="$drifted"
  # compute the new file set, fetch it, delete orphans
  fetch_unit
  for p in "${PROTOCOLS[@]}"; do install_agents "$p"; done
  configure_endpoints
  local newfiles; newfiles="$(git ls-files .github; git ls-files --others --exclude-standard .github)"
  # delete files the receipt had but the new set doesn't
  local orphan
  while read -r orphan; do
    [[ -n "$orphan" ]] && { log "removing orphan ${orphan}"; git rm -f "$orphan" 2>/dev/null || rm -f "$orphan"; }
  done < <(python3 "$WORKDIR/receipt.py" orphans "$rcpt" $newfiles)
  write_install_receipt
  git add -A .github
  git commit -m "chore: update agentic protocol(s) to ${REF}: ${PROTOCOLS[*]}"
  git push
  log "update complete"
}

parse_args "$@"
case_dispatch
