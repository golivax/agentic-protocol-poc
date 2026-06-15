#!/usr/bin/env python3
"""Engine shared library. Importable by the engine scripts AND a thin CLI
(`python3 lib.py <subcommand> ...`) for helpers the orchestrator calls inline.
Ports .github/agent-factory/engine/lib.sh 1:1 — behavior must not change."""
import glob
import json
import os
import subprocess
import sys
import yaml

STATE_REMOTE = os.environ.get("STATE_REMOTE", "")
STATE_BRANCH = os.environ.get("STATE_BRANCH", "agentic-state")
GIT_ID = ["-c", "user.email=engine@agentic-protocol-poc",
          "-c", "user.name=protocol-engine"]


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def dump_yaml(path, data):
    # sort_keys=False + block style keeps a stable, human-readable git trail.
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


def git(dir_, *args, check=True, capture=False):
    return subprocess.run(["git", "-C", dir_] + list(args),
                          check=check, text=True, capture_output=capture)


def protocol_id(proto_path):
    """protocol_id <protocol.json> — the protocol's id."""
    with open(proto_path) as f:
        return json.load(f)["name"]


def state_file(d, pid, instance, branch=None):
    """
    state_file <dir> <protocol-id> <instance-key> [branch]
      no branch → single-agent path        <dir>/<pid>/<instance>.yaml
      branch    → fan-out per-branch path   <dir>/<pid>/<instance>/<branch>.yaml
    """
    if branch:
        return f"{d}/{pid}/{instance}/{branch}.yaml"
    return f"{d}/{pid}/{instance}.yaml"


def instance_file(d, pid, instance):
    """instance_file <dir> <protocol-id> <instance-key> — shared per-instance bookkeeping."""
    return f"{d}/{pid}/{instance}/_instance.yaml"


def state_checkout(dir_):
    """state_checkout <dir> — clone the state branch; create it on origin if missing."""
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", STATE_REMOTE, STATE_BRANCH],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        subprocess.run(
            ["git", "clone", "-q", "--branch", STATE_BRANCH, "--single-branch", STATE_REMOTE, dir_],
            check=True, text=True
        )
    else:
        subprocess.run(["git", "init", "-q", "--initial-branch", STATE_BRANCH, dir_], check=True, text=True)
        git(dir_, "remote", "add", "origin", STATE_REMOTE)
        git(dir_, *GIT_ID, "commit", "-q", "--allow-empty", "-m", "init agentic-state")
        git(dir_, "push", "-q", "origin", STATE_BRANCH)


def cas_push(dir_, msg):
    """
    cas_push <dir> <message> — commit everything and push fast-forward-only.
    One retry via rebase. NEVER force-push.
    """
    git(dir_, "add", "-A")
    # An empty commit here means the engine pushed without changing state — a bug; fail loudly.
    git(dir_, *GIT_ID, "commit", "-qm", msg)
    # check=False intentional: non-zero means the push was rejected; we rebase and retry below.
    result = subprocess.run(
        ["git", "-C", dir_, "push", "-q", "origin", STATE_BRANCH],
        text=True, capture_output=True
    )
    if result.returncode != 0:
        sys.stderr.write("[engine] CAS push rejected, rebasing once\n")
        git(dir_, *GIT_ID, "pull", "-q", "--rebase", "origin", STATE_BRANCH)
        git(dir_, "push", "-q", "origin", STATE_BRANCH)


def resolve_executable(sdir, name, pdir, ex=""):
    """
    resolve_executable <search-dir> <name> <protocol-dir> <explicit-exec-or-empty>
    Prints OK\t<path> or ERR\t<reason> to stdout.
    """
    if ex:
        path = f"{pdir}/{ex}"
        if os.path.isfile(path):
            return f"OK\t{path}"
        else:
            return f"ERR\tdeclared exec not found: {ex}"

    # Extension-agnostic: match <sdir>/<name> or <sdir>/<name>.*
    matches = []
    exact = f"{sdir}/{name}"
    if os.path.isfile(exact):
        matches.append(exact)
    # glob for extensions
    for g in sorted(glob.glob(f"{sdir}/{name}.*")):
        if os.path.isfile(g):
            matches.append(g)

    if len(matches) == 0:
        return f"ERR\tno executable found (looked for {sdir}/{name} or {sdir}/{name}.*)"
    elif len(matches) > 1:
        return f"ERR\tambiguous: multiple files match {sdir}/{name}.* ({' '.join(matches)}); use an explicit \"exec\""
    else:
        return f"OK\t{matches[0]}"


def set_check_run(name, sha, status, conclusion, title, summary):
    """
    set_check_run <name> <head_sha> <status> <conclusion-or-empty> <title> <summary>
    Best-effort: failure never breaks a transition.
    """
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(
            f"[ENGINE_LOCAL] check-run {name} sha={sha} status={status} "
            f"conclusion={conclusion or 'none'} title={title} summary={summary}\n"
        )
        return
    if not sha:
        sys.stderr.write("[engine] no head sha; skipping check run\n")
        return
    args = [
        "-f", f"name={name}",
        "-f", f"head_sha={sha}",
        "-f", f"status={status}",
        "-f", f"output[title]={title}",
        "-f", f"output[summary]={summary}",
    ]
    if conclusion:
        args += ["-f", f"conclusion={conclusion}"]
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    publish_token = os.environ.get("PUBLISH_TOKEN", "")
    env = dict(os.environ)
    if publish_token:
        env["GH_TOKEN"] = publish_token
    result = subprocess.run(
        ["gh", "api", "-X", "POST", f"repos/{repo}/check-runs"] + args,
        text=True, capture_output=True, env=env
    )
    if result.returncode != 0:
        sys.stderr.write(
            "[engine] check-run create failed (needs checks:write + Actions token; "
            "merge-gating needs branch protection)\n"
        )


def match_run_by_cid(runs_json, cid):
    """
    match_run_by_cid <runs-json> <cid>
    Pure resolver: finds the databaseId whose displayTitle contains the delimited
    token "cid:[<cid>]". Returns the id as a string, or empty string if none match.
    """
    needle = f"cid:[{cid}]"
    try:
        runs = json.loads(runs_json)
    except json.JSONDecodeError:
        return ""
    for run in runs:
        title = run.get("displayTitle") or ""
        if needle in title:
            return str(run["databaseId"])
    return ""


def upsert_status_comment(sf, pr, body):
    """
    upsert_status_comment <state_file> <pr> <body>
    Single engine-owned PR comment, edited in place; id persisted in state.
    Mutates the state file but does NOT push.
    """
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] status comment pr#{pr}: {body}\n")
        return

    state = load_yaml(sf)
    cid = state.get("status_comment_id", "") or ""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    publish_token = os.environ.get("PUBLISH_TOKEN", "")
    env = dict(os.environ)
    if publish_token:
        env["GH_TOKEN"] = publish_token

    if not cid:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/{pr}/comments",
             "-f", f"body={body}", "--jq", ".id"],
            text=True, capture_output=True, env=env, check=True
        )
        new_cid = result.stdout.strip()
        state["status_comment_id"] = int(new_cid) if new_cid.isdigit() else new_cid
        dump_yaml(sf, state)
    else:
        subprocess.run(
            ["gh", "api", "-X", "PATCH",
             f"repos/{repo}/issues/comments/{cid}",
             "-f", f"body={body}"],
            text=True, capture_output=True, env=env, check=True
        )


def render_fanout_status_body(dir_, pid, instance, proto):
    """
    render_fanout_status_body <state_dir> <pid> <instance> <protocol.json>
    Pure projection of ALL fan-out branch state files into ONE combined PR-comment body.
    """
    branch_val = os.environ.get("STATE_BRANCH", STATE_BRANCH)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    link = f"https://github.com/{repo}/tree/{branch_val}/{pid}/{instance}"

    protocol = load_yaml(proto)

    # Find the fanout state and its branches
    branches = []
    for state in protocol.get("states", []):
        if state.get("kind") == "fanout":
            for b in state.get("branches", []):
                branches.append(b)
            break

    sections = ""
    states_list = []

    for b in branches:
        bid = b["id"]
        max_iter = b.get("max_iterations", "?")
        sf = state_file(dir_, pid, instance, bid)

        if os.path.isfile(sf):
            branch_state = load_yaml(sf)
            history = branch_state.get("history", []) or []
            st = branch_state.get("state", "") or ""

            if history:
                lines_list = []
                for entry in history:
                    it = entry.get("iteration", "?")
                    fb = entry.get("feedback", "") or ""
                    if fb == "":
                        lines_list.append(f"- ✅ iteration {it}/{max_iter} — all checks passed")
                    else:
                        lines_list.append(f"- ✗ iteration {it}/{max_iter} — {fb}")
                lines = "\n".join(lines_list)
            else:
                lines = "_no iterations yet_"
        else:
            lines = "_pending_"
            st = "pending"

        states_list.append(st)
        sections += f"**{bid}**\n\n{lines}\n\n"

    # Headline from branch states
    any_active = False
    any_failed = False
    for st in states_list:
        if st == "done":
            pass
        elif st == "failed":
            any_failed = True
        else:
            any_active = True

    if any_active:
        headline = "⏳ Review in progress…"
    elif any_failed:
        headline = "❌ Review incomplete — a branch could not complete; merge is gated."
    else:
        headline = "✅ Review complete — published."

    return f"\U0001f50d **{pid} · {instance}**\n\n{sections}{headline}\n\n[Full state & audit trail]({link})\n"


def _cli(argv):
    if not argv:
        sys.stderr.write("lib.py: no subcommand given\n")
        sys.exit(2)
    cmd, args = argv[0], argv[1:]
    if cmd == "protocol-id":
        print(protocol_id(args[0]))
    elif cmd == "state-file":
        # state-file <dir> <pid> <instance> [branch]
        print(state_file(*args))
    elif cmd == "instance-file":
        print(instance_file(*args))
    elif cmd == "set-check-run":
        # set-check-run <name> <sha> <status> <conclusion> <title> <summary>
        set_check_run(*args)
    elif cmd == "match-run-by-cid":
        # match-run-by-cid <runs-json> <cid>
        # args[0] = runs_json, args[1] = cid  (same order as the bash function)
        result = match_run_by_cid(args[0], args[1])
        if result:
            print(result)
    elif cmd == "render-fanout-status-body":
        # render-fanout-status-body <dir> <pid> <instance> <protocol.json>
        print(render_fanout_status_body(*args), end="")
    elif cmd == "upsert-status-comment":
        # upsert-status-comment <state_file> <pr> <body>
        upsert_status_comment(*args)
    elif cmd == "cas-push":
        # cas-push <dir> <message>
        cas_push(*args)
    elif cmd == "resolve-executable":
        # resolve-executable <sdir> <name> <pdir> [exec]
        ex = args[3] if len(args) > 3 else ""
        print(resolve_executable(args[0], args[1], args[2], ex))
    elif cmd == "state-checkout":
        state_checkout(args[0])
    else:
        sys.stderr.write(f"lib.py: unknown subcommand {cmd}\n")
        sys.exit(2)


if __name__ == "__main__":
    _cli(sys.argv[1:])
