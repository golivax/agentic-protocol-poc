#!/usr/bin/env bash
set -euo pipefail

# ── defaults (overridable by flags) ───────────────────────────────────────────
SOURCE="golivax/agentic-protocol-poc"
REF="main"
DRY_RUN=0
FORCE=0
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
  local path="$1"; mkdir -p "$(dirname "$path")"; gh_raw "$path" > "$path"
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

cmd_install() {
  [[ ${#PROTOCOLS[@]} -gt 0 ]] || die "name at least one protocol (see: install.sh list)"
  preflight
  bootstrap_helpers
  if [[ "$DRY_RUN" == 1 ]]; then print_plan; exit 0; fi
  die "install not yet implemented past --dry-run"   # completed in Tasks 9–10
}
cmd_update() { die "not yet implemented"; }

parse_args "$@"
case_dispatch
