#!/usr/bin/env python3
"""advance.py <state_workdir> <instance-key> <protocol.json> <verdicts.json> <evidence.json>
The ONLY writer of non-initial state. Reads check verdicts (never agent files,
except evidence for publication AFTER checks passed), mutates state, CAS-pushes,
and performs the consequent action: publish / re-dispatch / fail loudly.
Tolerates a missing state file (recovers from a lost init, e.g. a plan job
that failed after dispatch) by starting at {state: review, iteration: 1, history: []}.
Env: AGENT_RUN_ID, GITHUB_REPOSITORY, PUBLISH_TOKEN (reviews+comments),
     GH_TOKEN (repository_dispatch), ENGINE_LOCAL.
"""
import json
import os
import subprocess
import sys

# Import shared library from the same directory as this script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib


def gh_api(*args):
    """Run 'gh api ...' with ENGINE_LOCAL short-circuit."""
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] gh api {' '.join(args)}\n")
        return
    result = subprocess.run(
        ["gh", "api"] + list(args),
        text=True, capture_output=True
    )
    if result.returncode != 0:
        sys.stderr.write(f"[engine] gh api failed: {result.stderr}\n")


def fire_join(pid, instance, branch):
    """On a TERMINAL branch (done OR failed), signal the fan-out barrier.
    No-op for the single-agent path (branch empty)."""
    if not branch:
        return
    gh_api(
        "repos/" + os.environ.get("GITHUB_REPOSITORY", "") + "/dispatches",
        "-f", "event_type=protocol-join",   # -f: literal string; -F would add JSON quoting
        "-F", f"client_payload[protocol]={pid}",
        "-F", f"client_payload[instance]={instance}",
    )


def run_publish_hook(proto_path, proto, branch, agent_state, evid, instance, pid):
    """Resolve and run the protocol's publish-state executable.
    Returns {conclusion, summary} dict; on any resolution/exec failure,
    returns a neutral conclusion so the transition still completes."""

    if branch:
        # fan-out branch: get .publish from the branch entry
        action = None
        for state in proto.get("states", []):
            if state.get("kind") == "fanout":
                for b in state.get("branches", []):
                    if b["id"] == branch:
                        action = b.get("publish") or None
                        break
                break
        exec_override = ""
    else:
        # single-agent: publish hook is on the state after agent_state (.next)
        pubstate_id = None
        for state in proto.get("states", []):
            if state.get("id") == agent_state:
                pubstate_id = state.get("next") or None
                break
        action = None
        exec_override = ""
        if pubstate_id:
            for state in proto.get("states", []):
                if state.get("id") == pubstate_id:
                    action = state.get("action") or None
                    exec_override = state.get("exec") or ""
                    break

    pdir = os.path.dirname(os.path.abspath(proto_path))

    if not action and not exec_override:
        return {"conclusion": "neutral", "summary": "no publish action defined"}

    res = lib.resolve_executable(f"{pdir}/publish", action or "", pdir, exec_override)
    kind, path = res.split("\t", 1)

    if kind == "ERR":
        sys.stderr.write(f"[advance] publish hook unresolved: {path}\n")
        return {"conclusion": "neutral", "summary": "publish hook unresolved"}

    if not os.access(path, os.X_OK):
        sys.stderr.write(f"[advance] publish hook not executable: {path}\n")
        return {"conclusion": "neutral", "summary": "publish hook not executable"}

    # The hook is trusted (zone 4) and inherits the full parent env
    # (ENGINE_LOCAL, PUBLISH_TOKEN, GITHUB_REPOSITORY, PR).
    result = subprocess.run(
        [path, evid, instance],
        text=True, capture_output=False,
        stdout=subprocess.PIPE, stderr=None
    )
    if result.returncode != 0:
        sys.stderr.write("[advance] publish hook exited nonzero\n")
        return {"conclusion": "neutral", "summary": "publish hook failed"}

    out = result.stdout.strip()
    try:
        parsed = json.loads(out)
        if isinstance(parsed, dict) and "conclusion" in parsed and "summary" in parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    return {"conclusion": "neutral", "summary": "publish hook returned no verdict"}


def render_status_body(sf, headline, pid, instance, max_iter, github_repository):
    """Render the status-comment body as a projection of state.history.
    Byte-identical to the bash render_status_body function."""
    state_branch = os.environ.get("STATE_BRANCH", "agentic-state")
    link = f"https://github.com/{github_repository}/blob/{state_branch}/{pid}/{instance}.yaml"

    state_data = lib.load_yaml(sf)
    history = state_data.get("history", []) or []

    lines_list = []
    for entry in history:
        it = entry.get("iteration", "?")
        fb = entry.get("feedback", "") or ""
        if not fb:
            lines_list.append(f"- ✅ iteration {it}/{max_iter} — all checks passed")
        else:
            lines_list.append(f"- ✗ iteration {it}/{max_iter} — {fb}")
    lines = "\n".join(lines_list)

    return f"\U0001f50d **{pid} · {instance}**\n\n{lines}\n\n{headline}\n\n[Full state & audit trail]({link})\n"


def update_status_comment(sf, inf, branch, pr, pid, instance, proto_path, dir_,
                          headline, max_iter, github_repository):
    """Branch-aware status-comment writer."""
    if branch:
        # fan-out branch: shared comment keyed in _instance.yaml
        if not os.path.isfile(inf):
            return
        body = lib.render_fanout_status_body(dir_, pid, instance, proto_path)
        lib.upsert_status_comment(inf, pr, body)
    else:
        body = render_status_body(sf, headline, pid, instance, max_iter, github_repository)
        lib.upsert_status_comment(sf, pr, body)


def main():
    if len(sys.argv) != 6:
        sys.stderr.write(
            "usage: advance.py <state_workdir> <instance-key> <protocol.json> "
            "<verdicts.json> <evidence.json>\n"
        )
        sys.exit(1)

    dir_ = sys.argv[1]
    instance = sys.argv[2]
    proto_path = sys.argv[3]
    verdicts_path = sys.argv[4]
    evid = sys.argv[5]

    branch = os.environ.get("BRANCH", "")
    pr = os.environ.get("PR", instance)
    agent_run_id = os.environ.get("AGENT_RUN_ID", "unknown")
    github_repository = os.environ.get("GITHUB_REPOSITORY", "")

    # Load protocol
    with open(proto_path) as f:
        proto = json.load(f)

    pid = lib.protocol_id(proto_path)

    # Resolve agent_state and max_iterations
    if branch:
        agent_state = branch
        max_iter = None
        for state in proto.get("states", []):
            if state.get("kind") == "fanout":
                for b in state.get("branches", []):
                    if b["id"] == branch:
                        max_iter = b.get("max_iterations")
                        break
                break
        # LIFE_STATE: the owning fan-out state's id
        life_state = None
        for state in proto.get("states", []):
            if state.get("kind") == "fanout":
                life_state = state["id"]
                break
    else:
        agent_state = None
        for state in proto.get("states", []):
            if state.get("kind") == "agent":
                agent_state = state["id"]
                break
        if not agent_state:
            sys.stderr.write("[engine] protocol has no agent state\n")
            sys.exit(1)
        max_iter = None
        for state in proto.get("states", []):
            if state.get("id") == agent_state:
                max_iter = state.get("max_iterations")
                break
        life_state = agent_state

    # State file and check-run name
    sf = lib.state_file(dir_, pid, instance, branch)
    if branch:
        cr_name = f"{pid}/{branch}"
    else:
        cr_name = pid

    # Checkout state
    lib.state_checkout(dir_)

    # Recover missing state file
    if not os.path.isfile(sf):
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        seed = {
            "protocol": pid,
            "instance": instance,
            "state": life_state,
            "iteration": 1,
            "gates": {},
            "history": [],
        }
        lib.dump_yaml(sf, seed)

    # Read current state
    state_data = lib.load_yaml(sf)
    iter_ = int(state_data.get("iteration", 1))
    max_iter = int(max_iter) if max_iter is not None else 3

    # Load verdicts
    with open(verdicts_path) as f:
        verdicts = json.load(f)

    results = verdicts.get("results", [])
    all_pass = len(results) > 0 and all(r.get("pass", False) for r in results)

    # Compute feedback string
    fb_parts = [r.get("feedback", "") for r in results if not r.get("pass", False)]
    fb = "; ".join(p for p in fb_parts if p)
    if not fb and len(results) == 0:
        fb = "no check verdicts produced (checks job failure?)"

    # Checks map: {check: "pass"/"fail"}
    checks_map = {}
    for r in results:
        checks_map[r["check"]] = "pass" if r.get("pass", False) else "fail"

    # Append history entry
    history_entry = {
        "iteration": iter_,
        "agent_run_id": agent_run_id,
        "checks": checks_map,
        "feedback": fb,
    }
    state_data = lib.load_yaml(sf)
    if "history" not in state_data or state_data["history"] is None:
        state_data["history"] = []
    state_data["history"].append(history_entry)
    lib.dump_yaml(sf, state_data)

    sha = os.environ.get("PR_HEAD_SHA", "")
    inf = lib.instance_file(dir_, pid, instance)

    # Branch: mutate state → publish/side-effects → status-comment → cas_push → dispatch
    if all_pass:
        # Mark done
        state_data = lib.load_yaml(sf)
        state_data["state"] = "done"
        lib.dump_yaml(sf, state_data)

        hook = run_publish_hook(proto_path, proto, branch, agent_state, evid, instance, pid)
        concl = hook.get("conclusion", "neutral")
        csum = hook.get("summary", "")

        lib.set_check_run(cr_name, sha, "completed", concl, "Review complete", csum)
        update_status_comment(
            sf, inf, branch, pr, pid, instance, proto_path, dir_,
            "✅ done — published.",
            max_iter, github_repository
        )
        lib.cas_push(dir_, f"{instance}: checks passed at iteration {iter_} → published, done")
        fire_join(pid, instance, branch)

    elif iter_ < max_iter:
        next_iter = iter_ + 1
        state_data = lib.load_yaml(sf)
        state_data["iteration"] = next_iter
        lib.dump_yaml(sf, state_data)

        lib.set_check_run(
            cr_name, sha, "in_progress", "",
            "Review in progress",
            f"Iteration {iter_} failed checks; retrying as iteration {next_iter}/{max_iter}."
        )
        update_status_comment(
            sf, inf, branch, pr, pid, instance, proto_path, dir_,
            f"⏳ iteration {iter_} failed checks — retrying as iteration {next_iter}/{max_iter}…",
            max_iter, github_repository
        )
        lib.cas_push(dir_, f"{instance}: iteration {iter_} failed checks → iteration {next_iter}")

        # Re-dispatch
        gh_api(
            f"repos/{github_repository}/dispatches",
            "-f", "event_type=protocol-continue",
            "-F", f"client_payload[protocol]={pid}",
            "-F", f"client_payload[instance]={instance}",
            "-F", f"client_payload[branch]={branch}",
        )

    else:
        # Exhausted
        state_data = lib.load_yaml(sf)
        state_data["state"] = "failed"
        lib.dump_yaml(sf, state_data)

        lib.set_check_run(
            cr_name, sha, "completed", "failure",
            "Review failed",
            f"Could not produce a valid review after {max_iter} iterations."
        )
        update_status_comment(
            sf, inf, branch, pr, pid, instance, proto_path, dir_,
            f"❌ **failed** after {max_iter} iterations.",
            max_iter, github_repository
        )
        lib.cas_push(dir_, f"{instance}: iterations exhausted → failed")
        fire_join(pid, instance, branch)


if __name__ == "__main__":
    main()
