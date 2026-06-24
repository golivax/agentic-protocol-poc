import os, stat, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "dist/install.sh"


def _fake_gh(tmp_path):
    """A fake `gh` for the `list` call. It serves canned `git/trees` JSON and
    emulates gh's built-in `--jq`: when invoked with `--jq <filter>` it applies
    that filter to the canned JSON via the real `jq` binary (a test-only dep —
    the installer relies on gh's built-in jq, not an external one). So
    `gh_tree_children`'s `--jq` yields the three protocol names. Returns its bin
    dir for PATH."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(
        r"""#!/usr/bin/env bash
# Emulate gh's built-in --jq: find the filter following a --jq arg, if any.
filter=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[i]}" == "--jq" ]]; then
    filter="${args[i + 1]}"
  fi
done
if [[ "$*" == *"git/trees"* ]]; then
  json='{"tree":[{"path":"code-review","type":"tree"},{"path":"deep-review-stub","type":"tree"},{"path":"recover-mental-model-stub","type":"tree"}]}'
  if [[ -n "$filter" ]]; then
    printf '%s' "$json" | jq -r "$filter"
  else
    printf '%s\n' "$json"
  fi
  exit 0
fi
echo "2.40.0"
"""
    )
    gh.chmod(0o755)
    return bindir


def test_list_prints_protocol_names(tmp_path):
    env = dict(os.environ)
    env["PATH"] = f"{_fake_gh(tmp_path)}:{env['PATH']}"
    out = subprocess.run(
        ["bash", str(INSTALL), "list"], capture_output=True, text=True, env=env,
    )
    names = set(out.stdout.split())
    assert {"code-review", "deep-review-stub", "recover-mental-model-stub"} <= names
