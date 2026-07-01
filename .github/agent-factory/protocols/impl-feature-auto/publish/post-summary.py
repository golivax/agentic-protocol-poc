#!/usr/bin/env python3
"""post-summary (zone 4) — after implement's checks pass, resolve the PR by
pr_branch and comment on the originating issue with a link. ENGINE_LOCAL=1 does no
GitHub I/O. ABI: post-summary.py <evidence.json> <instance-key>; env ENGINE_LOCAL,
GITHUB_REPOSITORY, PUBLISH_TOKEN, PR. Prints {"conclusion","summary"}.

This hook only summarises/links — the PR WRITE is gh-aw safe-outputs (implement)."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "engine"))
import lib  # noqa: E402


def _local():
    return os.environ.get("ENGINE_LOCAL", "0") == "1"


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    instance = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        with open(ev_path) as fh:
            ev = json.load(fh)
    except (OSError, ValueError):
        ev = {}
    pr_branch = (ev.get("pr_branch") or "").strip()
    issue = lib.pr_from_instance(instance)

    if not pr_branch:
        print(json.dumps({"conclusion": "neutral",
                          "summary": "implement produced no pr_branch; no PR to link."}))
        return

    if _local():
        print(json.dumps({"conclusion": "success",
                          "summary": f"[local] would link PR on branch {pr_branch} to {instance}."}))
        return

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    env = dict(os.environ)
    env["GH_TOKEN"] = os.environ.get("PUBLISH_TOKEN", os.environ.get("GH_TOKEN", ""))
    r = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--head", pr_branch,
         "--state", "open", "--json", "number,url", "--limit", "1"],
        text=True, capture_output=True, env=env)
    prs = []
    if r.returncode == 0 and r.stdout.strip():
        try:
            prs = json.loads(r.stdout)
        except ValueError:
            prs = []
    if not prs:
        print(json.dumps({"conclusion": "failure",
                          "summary": f"No open PR found for branch {pr_branch}."}))
        return
    pr_num, pr_url = prs[0].get("number"), prs[0].get("url")
    if str(issue).isdigit():
        lib.post_pr_comment(issue,
                            f"🤖 **Feature implemented** — opened PR #{pr_num}: {pr_url}\n\n"
                            f"{ev.get('summary', '').strip()}")
    print(json.dumps({"conclusion": "success",
                      "summary": f"Linked PR #{pr_num} for issue {instance}."}))


if __name__ == "__main__":
    main()
