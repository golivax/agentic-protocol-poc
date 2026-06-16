#!/usr/bin/env python3
"""advance.py <state_workdir> <instance-key> <protocol.json> <verdicts.json> <evidence.json>
The ONLY writer of non-initial state. The iterate/done/failed decision is the pure
lib.decide() fold over verdict severities. Reads check verdicts (never agent files,
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


def run_conclude_hook(proto_path, proto, state_id, evid, instance, blocking):
    """Resolve+run the optional `conclude` hook for an agent state. Returns
    {conclusion,summary,blocked} or None if the state declares none. Trusted
    (zone 4). Receives BLOCKING via env."""
    state = lib.state_by_id(proto, state_id)
    action = (state or {}).get("conclude") or None
    if not action:
        return None
    pdir = os.path.dirname(os.path.abspath(proto_path))
    res = lib.resolve_executable(f"{pdir}/publish", action, pdir, "")
    kind, path = res.split("\t", 1)
    if kind == "ERR" or not os.access(path, os.X_OK):
        sys.stderr.write(f"[advance] conclude hook unresolved/not-exec: {path}\n")
        return {"conclusion": "neutral", "summary": "conclude hook unresolved", "blocked": False}
    env = dict(os.environ)
    env["BLOCKING"] = "1" if blocking else "0"
    result = subprocess.run([path, evid, instance], text=True,
                            stdout=subprocess.PIPE, env=env)
    try:
        parsed = json.loads(result.stdout.strip())
        if isinstance(parsed, dict) and "blocked" in parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return {"conclusion": "neutral", "summary": "conclude hook returned no verdict", "blocked": False}


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
    phase = os.environ.get("PHASE", "")
    pr = os.environ.get("PR", instance)
    agent_run_id = os.environ.get("AGENT_RUN_ID", "unknown")
    github_repository = os.environ.get("GITHUB_REPOSITORY", "")

    # Load protocol
    with open(proto_path) as f:
        proto = json.load(f)

    pid = lib.protocol_id(proto_path)

    # NOTE: this PHASE/branch agent-unit resolution mirrors next.py's. The two
    # should be extracted into a shared lib.resolve_agent_unit() in M2b (deferred
    # to avoid touching the byte-identical legacy branches this milestone).

    # Resolve agent_state and max_iterations. PHASE-first (multi-phase), mirroring
    # next.py: find the phase state by id; if fanout, resolve the branch within it;
    # else the phase itself is the agent unit. The elif/else branches are the v1/v2
    # regression baseline and must stay byte-identical to the pre-multiphase code.
    if phase:
        phase_state = lib.state_by_id(proto, phase)
        if phase_state and phase_state.get("kind") == "fanout":
            agent_state = branch
            max_iter = None
            found = False
            for b in phase_state.get("branches", []):
                if b["id"] == branch:
                    max_iter = b.get("max_iterations")
                    found = True
                    break
            if not found:
                sys.stderr.write(f"[advance] no branch '{branch}' in fanout phase '{phase}'\n")
                sys.exit(1)
            life_state = phase
        else:
            agent_state = phase
            max_iter = phase_state.get("max_iterations") if phase_state else None
            life_state = phase
    elif branch:
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
    sf = lib.state_file(dir_, pid, instance,
                        branch=(branch if branch else None),
                        phase=(phase if phase else None))
    if phase and branch:
        cr_name = f"{pid}/{phase}/{branch}"
    elif phase:
        cr_name = f"{pid}/{phase}"
    elif branch:
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
    # DECIDE: the process axis (iterate/done/failed) is a pure fold over the
    # verdicts + their on_fail severities. `blocking` (a block-severity fail)
    # has no consumer in M1 — the M2 phase-gate will read it.
    process, blocking = lib.decide(results, iterations_remaining=(iter_ < max_iter))

    # Feedback fed back to the agent: only iterate-severity failures, since the
    # agent cannot fix advisory/block facts by re-running. Defaulting on_fail to
    # "iterate" keeps the single-agent regression path byte-identical (all v1
    # checks are iterate-severity, so this is every non-pass verdict).
    fb_parts = [r.get("feedback", "") for r in results
                if not r.get("pass", False) and r.get("on_fail", "iterate") == "iterate"]
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
    if process == "done":
        # Mark this phase/unit done.
        state_data = lib.load_yaml(sf)
        state_data["state"] = "done"
        lib.dump_yaml(sf, state_data)

        this_state = lib.state_by_id(proto, agent_state)
        is_agent_phase = phase and this_state and this_state.get("kind") == "agent"
        conclude = run_conclude_hook(proto_path, proto, agent_state, evid, instance, blocking) if is_agent_phase else None

        # Always run publish for side-effects (e.g. post the review). For an agent
        # phase with a `conclude` hook, conclude overrides only the verdict axis
        # (conclusion/summary); publish still fires.
        hook = run_publish_hook(proto_path, proto, branch, agent_state, evid, instance, pid)
        if conclude is not None:
            concl = conclude.get("conclusion", "neutral")
            csum = conclude.get("summary", "")
        else:
            concl = hook.get("conclusion", "neutral")
            csum = hook.get("summary", "")

        if is_agent_phase and conclude is not None and conclude.get("blocked") and (this_state.get("on_blocked") == "halt"):
            # GATE BLOCKED → terminate the pipeline before the next phase.
            state_data = lib.load_yaml(sf)
            state_data["state"] = "failed"
            lib.dump_yaml(sf, state_data)
            lib.set_check_run(pid, sha, "completed", "failure", "Gate blocked",
                              csum or "A required gate did not pass; pipeline halted.")
            lib.set_check_run(cr_name, sha, "completed", "failure", "Gate blocked", csum)
            lib.cas_push(dir_, f"{instance}: phase {phase} blocked → pipeline halted")
        elif is_agent_phase:
            # GATE CLEAR → advance the cursor and launch the next phase.
            nxt = lib.next_phase_id(proto, agent_state)
            lib.set_check_run(cr_name, sha, "completed",
                              "success" if concl != "blocked" else "failure",
                              "Gate complete", csum)
            inst = lib.load_yaml(inf) if os.path.isfile(inf) else {}
            if nxt:
                inst["phase"] = nxt
                lib.dump_yaml(inf, inst)
                lib.cas_push(dir_, f"{instance}: phase {phase} clear → advancing to {nxt}")
                gh_api(
                    f"repos/{github_repository}/dispatches",
                    "-f", "event_type=protocol-advance",
                    "-F", f"client_payload[protocol]={pid}",
                    "-F", f"client_payload[instance]={instance}",
                    "-F", f"client_payload[phase]={nxt}",
                )
            else:
                # No further phase → close the pipeline-level (aggregate) check-run.
                lib.set_check_run(pid, sha, "completed", "success", "Complete", csum)
                lib.cas_push(dir_, f"{instance}: phase {phase} clear → done (no further phase)")
        else:
            # Single-agent or fan-out leg → today's behavior unchanged.
            lib.set_check_run(cr_name, sha, "completed", concl, "Review complete", csum)
            update_status_comment(
                sf, inf, branch, pr, pid, instance, proto_path, dir_,
                "✅ done — published.",
                max_iter, github_repository
            )
            lib.cas_push(dir_, f"{instance}: checks passed at iteration {iter_} → published, done")
            fire_join(pid, instance, branch)

    elif process == "iterate":
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

        # Re-dispatch. Carry `phase` so a multi-phase agent/fan-out phase resumes
        # in the SAME phase on re-entry (the orchestrator relays payload.phase ->
        # PHASE). Empty/absent for single-phase protocols → byte-identical payload.
        redispatch = [
            f"repos/{github_repository}/dispatches",
            "-f", "event_type=protocol-continue",
            "-F", f"client_payload[protocol]={pid}",
            "-F", f"client_payload[instance]={instance}",
            "-F", f"client_payload[branch]={branch}",
        ]
        if phase:
            redispatch += ["-F", f"client_payload[phase]={phase}"]
        gh_api(*redispatch)

    else:  # process == "failed"
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
