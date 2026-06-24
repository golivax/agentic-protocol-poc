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

# cmd_install / cmd_update are defined in later tasks.
cmd_install() { die "not yet implemented"; }
cmd_update() { die "not yet implemented"; }

parse_args "$@"
case_dispatch
