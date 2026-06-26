#!/usr/bin/env python3
"""Merge hook for the `combine` state of recover-mental-model.

Collects the three method outputs (legion-map, codeset-vibing, socratic) and
pushes them, all at once, into a single orphan `_mental_model` branch on the
target repo — recreated/overwritten every run. Mirrors the layout of
golivax2/yuanrong-datasystem@_mental_model:

    _mental_model/
      METHODS.txt
      legion-map/      <- legion leg tree
      vibed-codeset/   <- codeset leg tree
      socratic/        <- socratic leg (phase-2) tree

ABI: <hook> <workdir> <instance>
  <workdir>/inputs/{legion,codeset,socratic}.json   leg evidence (engine-materialized);
                                                    each carries a `run_id`.
Env: ENGINE_LOCAL, GITHUB_REPOSITORY, PUBLISH_TOKEN, PR, PR_HEAD_SHA (inherited).

Where the actual file trees come from:
  * CI (ENGINE_LOCAL unset): `gh run download <run_id> -n mm-tree-<leg>` per leg,
    using PUBLISH_TOKEN (needs actions:read + contents:write).
  * Local (ENGINE_LOCAL=1): trees are pre-staged at <workdir>/trees/<leg>/ by the
    caller (the pytest hook test) and the push targets the bare origin in
    MM_TARGET_REMOTE — no network.

Prints {"conclusion","summary"} to stdout. This hook runs TRUSTED in zone 4 (NOT a
sandboxed check) — it holds the publish token.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

# Import lib from the engine dir (same pattern as the other publish hooks).
sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "..", "..", "..", "engine"))
import lib  # noqa: E402

BRANCH = "_mental_model"

# leg name -> (input json basename, artifact name, destination dir in the branch)
LEGS = [
    ("legion", "legion", "mm-tree-legion", "legion-map"),
    ("codeset", "codeset", "mm-tree-codeset", "vibed-codeset"),
    ("socratic", "socratic", "mm-tree-socratic", "socratic"),
]

METHOD_LINES = {
    "legion": "legion-map   : claude -p /legion:map  (9thLevelSoftware/legion)",
    "codeset": "vibed-codeset: python -m codeset .   (codeset-vibing)",
    "socratic": "socratic     : /socratic-code-theory-recovery  (LLM-Coding/Semantic-Anchors)",
}


def _local():
    return os.environ.get("ENGINE_LOCAL", "0") == "1"


def _read_input(workdir, name):
    p = os.path.join(workdir, "inputs", f"{name}.json")
    if not os.path.isfile(p):
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}


def _git(cwd, *args, token=None):
    """Run git; never echo the token. Returns CompletedProcess (check=True)."""
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          text=True, capture_output=True)


def _fetch_tree(workdir, leg, run_id, artifact):
    """Materialize a leg's file tree at <workdir>/trees/<leg>/.

    Local mode: trees are already staged there. CI mode: download the artifact."""
    dest = os.path.join(workdir, "trees", leg)
    if _local():
        return dest if os.path.isdir(dest) else None
    os.makedirs(dest, exist_ok=True)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    env = dict(os.environ)
    # gh authenticates from GH_TOKEN; prefer the publish token.
    env["GH_TOKEN"] = os.environ.get("PUBLISH_TOKEN", os.environ.get("GH_TOKEN", ""))
    r = subprocess.run(
        ["gh", "run", "download", str(run_id), "--repo", repo,
         "-n", artifact, "-D", dest],
        text=True, capture_output=True, env=env)
    if r.returncode != 0:
        sys.stderr.write(f"[push-mm] gh run download {artifact} (run {run_id}) "
                         f"failed: {r.stderr}\n")
        return None
    return dest


def _target_remote():
    """Authenticated push URL for the orphan branch."""
    if _local():
        return os.environ.get("MM_TARGET_REMOTE", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("PUBLISH_TOKEN", "")
    if not repo:
        return ""
    if token:
        return f"https://x-access-token:{token}@github.com/{repo}.git"
    return f"https://github.com/{repo}.git"


def main():
    workdir = sys.argv[1]
    instance = sys.argv[2] if len(sys.argv) > 2 else ""

    present = []          # (leg, dest_dir, tree_path)
    for leg, inp, artifact, dest_dir in LEGS:
        ev = _read_input(workdir, inp)
        run_id = str(ev.get("run_id", "") or "")
        tree = _fetch_tree(workdir, leg, run_id, artifact)
        if tree and os.path.isdir(tree) and os.listdir(tree):
            present.append((leg, dest_dir, tree))
        else:
            sys.stderr.write(f"[push-mm] leg '{leg}' produced no tree — skipping\n")

    if not present:
        print(json.dumps({"conclusion": "neutral",
                          "summary": "No method produced a tree; nothing to push."}))
        return

    remote = _target_remote()
    if not remote:
        print(json.dumps({"conclusion": "neutral",
                          "summary": "No push remote resolved (need GITHUB_REPOSITORY + PUBLISH_TOKEN)."}))
        return

    sha = os.environ.get("PR_HEAD_SHA", "") or instance

    # Build a fresh orphan worktree and assemble the three subtrees.
    staging = tempfile.mkdtemp(prefix="mm-branch-")
    try:
        _git(staging, "init", "-q", "-b", BRANCH)
        method_lines = []
        for leg, dest_dir, tree in present:
            shutil.copytree(tree, os.path.join(staging, dest_dir))
            method_lines.append(METHOD_LINES.get(leg, dest_dir))
        with open(os.path.join(staging, "METHODS.txt"), "w") as f:
            f.write("Mental-model recovery methods (one directory each)\n")
            f.write(f"source commit: {sha}\n\n")
            f.write("\n".join(method_lines) + "\n")

        _git(staging, "add", "-A")
        _git(staging, "-c", "user.name=agentic-engine",
             "-c", "user.email=agentic-engine@users.noreply.github.com",
             "commit", "-q", "-m", f"mental model for {sha or instance}")
        # Single overwrite of the orphan branch (no history kept).
        _git(staging, "push", "--force", remote, f"HEAD:{BRANCH}")
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"[push-mm] git failed: {e.stderr}\n")
        print(json.dumps({"conclusion": "failure",
                          "summary": "Failed to push the _mental_model branch."}))
        return
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    legs_done = ", ".join(leg for leg, _, _ in present)
    pr = os.environ.get("PR", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    link = f"https://github.com/{repo}/tree/{BRANCH}" if repo else BRANCH
    lib.post_pr_comment(
        pr, f"🧠 **Mental model recovered** ({legs_done}) — pushed to "
            f"[`{BRANCH}`]({link}).")
    print(json.dumps({"conclusion": "success",
                      "summary": f"Pushed {BRANCH} ({legs_done})."}))


if __name__ == "__main__":
    main()
