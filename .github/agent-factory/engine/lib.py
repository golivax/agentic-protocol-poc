#!/usr/bin/env python3
"""Engine shared library. Importable by the engine scripts AND a thin CLI
(`python3 lib.py <subcommand> ...`) for helpers the orchestrator calls inline."""
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


def state_file(d, pid, instance, branch=None, phase=None):
    """
    state_file <dir> <protocol-id> <instance-key> [branch] [phase]
      no branch, no phase → single-agent path     <dir>/<pid>/<instance>.yaml
      branch, no phase    → fan-out per-branch     <dir>/<pid>/<instance>/<branch>.yaml
      phase, no branch    → multi-phase agent      <dir>/<pid>/<instance>/<phase>.yaml
      phase + branch      → multi-phase fan-out leg <dir>/<pid>/<instance>/<phase>.<branch>.yaml
    """
    if phase and branch:
        return f"{d}/{pid}/{instance}/{phase}.{branch}.yaml"
    if phase:
        return f"{d}/{pid}/{instance}/{phase}.yaml"
    if branch:
        return f"{d}/{pid}/{instance}/{branch}.yaml"
    return f"{d}/{pid}/{instance}.yaml"


def state_by_id(protocol, state_id):
    """Return the state dict with the given id, or None."""
    for s in protocol.get("states", []):
        if s.get("id") == state_id:
            return s
    return None


def resolve_agent_unit(protocol, phase="", branch=""):
    """Resolve the agent unit for a leg: its agent_state id, max_iterations, and
    life_state (the .state value a live state file carries in flight). Mirrors the
    PHASE-first → BRANCH → single-agent ladder. Raises ValueError if unresolved."""
    if phase:
        st = state_by_id(protocol, phase)
        if not st:
            raise ValueError(f"no phase '{phase}' in protocol")
        if st.get("kind") == "fanout":
            if not branch:
                raise ValueError(f"PHASE='{phase}' is a fanout phase but BRANCH is empty")
            for b in st.get("branches", []):
                if b["id"] == branch:
                    return {"agent_state": branch, "max_iterations": b.get("max_iterations"), "life_state": phase}
            raise ValueError(f"no branch '{branch}' in phase '{phase}'")
        return {"agent_state": phase, "max_iterations": st.get("max_iterations"), "life_state": phase}
    if branch:
        agent_id = None
        max_it = None
        fanout_id = None
        for st in protocol.get("states", []):
            if st.get("kind") == "fanout":
                fanout_id = st["id"]
                for b in st.get("branches", []):
                    if b["id"] == branch:
                        agent_id = b["id"]
                        max_it = b.get("max_iterations")
                        break
                break
        if not agent_id:
            raise ValueError(f"no branch '{branch}' in protocol")
        return {"agent_state": agent_id, "max_iterations": max_it, "life_state": fanout_id}
    for st in protocol.get("states", []):
        if st.get("kind") == "agent":
            return {"agent_state": st["id"], "max_iterations": st.get("max_iterations"), "life_state": st["id"]}
    raise ValueError("protocol has no agent state")


def phase_states(protocol):
    """The ordered list of 'phase' states — those of kind agent or fanout.
    (join/deterministic states are transitions/terminals, not phases.)"""
    return [s for s in protocol.get("states", []) if s.get("kind") in ("agent", "fanout")]


def is_multiphase(protocol):
    """A protocol is multi-phase iff it has more than one agent|fanout phase.
    Single-phase protocols (grumpy=1 agent, multi-grumpy=1 fanout) keep the
    legacy layout + code paths untouched."""
    return len(phase_states(protocol)) > 1


def match_trigger(protocol, event_name, action="", comment_body=""):
    """Map an ENTRY GitHub event to an engine command via protocol["triggers"].
    Returns the command ("start"/"reset"/...) or "" if nothing matches (the
    workflow then no-ops). Internal re-entry dispatches (protocol-continue /
    protocol-advance / protocol-join) are generic and NOT handled here."""
    for t in protocol.get("triggers", []):
        if t.get("on") != event_name:
            continue
        if event_name == "issue_comment":
            prefix = t.get("comment_prefix", "")
            if not prefix or comment_body.startswith(prefix):
                return t.get("command", "")
        elif event_name == "pull_request":
            actions = t.get("actions", [])
            if not actions or action in actions:
                return t.get("command", "")
        else:
            # generic event (e.g. workflow_dispatch): match on `on` alone.
            return t.get("command", "")
    return ""


def agent_workflow(protocol, phase="", branch=""):
    """Resolve the gh-aw agent workflow basename for a leg.
    phase set + fanout phase -> that branch's workflow;
    phase set + agent phase  -> the phase state's workflow;
    branch only (single-phase fanout) -> that branch's workflow;
    neither -> the first agent state's workflow. "" if unresolved."""
    if phase:
        st = state_by_id(protocol, phase)
        if st and st.get("kind") == "fanout":
            for b in st.get("branches", []):
                if b["id"] == branch:
                    return b.get("workflow", "")
            return ""
        return (st or {}).get("workflow", "")
    if branch:
        for st in protocol.get("states", []):
            if st.get("kind") == "fanout":
                for b in st.get("branches", []):
                    if b["id"] == branch:
                        return b.get("workflow", "")
        return ""
    for st in protocol.get("states", []):
        if st.get("kind") == "agent":
            return st.get("workflow", "")
    return ""


def route(protocols_dir, event_name, action="", comment_body="",
          dispatch_protocol="", is_pr_comment=True):
    """Pick the protocol to run for an incoming event by scanning all
    protocols/*/protocol.json `triggers` blocks. Protocol-agnostic router core.

    Returns {"protocol": <path>, "command": <cmd>, "skip": <bool>}:
      - repository_dispatch (dispatch_protocol set): the dispatch carries the
        protocol NAME (advance.py sends pid; protocol-join.yml rebuilds the path
        the same way), so reconstruct <protocols_dir>/<name>/protocol.json — the
        engine needs a path to open. No scan; command re-derived from the type.
      - issue_comment on a non-PR issue: skip (the engine ignores these anyway).
      - entry event (pull_request / PR issue_comment): glob protocols in sorted
        order, run match_trigger on each; 0 matches -> skip, exactly 1 -> route,
        >=2 -> raise ValueError (ambiguous; the router job then fails loudly).
    """
    if dispatch_protocol:
        return {"protocol": os.path.join(protocols_dir, dispatch_protocol, "protocol.json"),
                "command": "", "skip": False}
    if event_name == "issue_comment" and not is_pr_comment:
        return {"protocol": "", "command": "", "skip": True}
    matches = []
    for path in sorted(glob.glob(os.path.join(protocols_dir, "*", "protocol.json"))):
        with open(path) as f:
            proto = json.load(f)
        cmd = match_trigger(proto, event_name, action, comment_body)
        if cmd:
            matches.append((path, cmd))
    if not matches:
        return {"protocol": "", "command": "", "skip": True}
    if len(matches) > 1:
        names = ", ".join(p for p, _ in matches)
        # Describe WHAT collided in the trigger's own terms, not the raw GitHub
        # event/action (e.g. "issue_comment/created" hides that the comment text
        # "/grumpy" is the thing two protocols both matched).
        if event_name == "issue_comment":
            what = f'the comment "{comment_body}"'
        elif event_name == "pull_request":
            what = f'pull_request action "{action}"'
        else:
            what = f'event "{event_name}"'
        raise ValueError(
            f"ambiguous route: {what} matches {len(matches)} protocols "
            f"({names}); their triggers overlap - make them mutually exclusive "
            f"(no comment_prefix may be a prefix of another protocol's)")
    path, cmd = matches[0]
    return {"protocol": path, "command": cmd, "skip": False}


def next_phase_id(protocol, phase_id):
    """The next PHASE (agent|fanout state) reached by following `.next` from
    phase_id. Returns None if `.next` is absent or is not itself a phase
    (e.g. a join or a terminal) — i.e. there is no further phase to launch."""
    cur = state_by_id(protocol, phase_id)
    if not cur:
        return None
    nxt = cur.get("next")
    nxt_state = state_by_id(protocol, nxt) if nxt else None
    if nxt_state and nxt_state.get("kind") in ("agent", "fanout"):
        return nxt
    return None


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


def decide(results, iterations_remaining):
    """Pure fold: (check verdicts + severities) → (process, blocking).

    process  ∈ {"done","iterate","failed"} — the process axis that drives the
             iterate loop and terminal state.
    blocking : bool — did a `block`-severity check fail (the conclusion-axis
             input; no consumer yet — the M2 phase-gate reads it).

    Severity is each verdict's "on_fail" (default "iterate" when absent, so
    pre-severity verdicts and the single-agent regression path are unchanged).
    `iterate`-severity failures drive the loop; `block` failures never iterate
    but set blocking; `advisory` failures are recorded only. Zero verdicts is a
    checks-job failure → treated as a failed attempt.

    Callers must stamp `on_fail` onto each verdict from the protocol's check
    entry before calling (see run-checks.py); absent it, every failure defaults
    to `iterate` (v1 behavior).
    """
    if not results:
        return ("iterate" if iterations_remaining else "failed"), False
    def sev(v):
        return v.get("on_fail", "iterate")
    iterate_fail = any(not v.get("pass") and sev(v) == "iterate" for v in results)
    block_fail = any(not v.get("pass") and sev(v) == "block" for v in results)
    if iterate_fail:
        process = "iterate" if iterations_remaining else "failed"
    else:
        process = "done"
    return process, block_fail


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


def post_pr_comment(pr, body):
    """
    post_pr_comment <pr> <body>
    Post a NEW (untracked) PR/issue comment — used for one-off engine notices
    (e.g. HITL override announcements and refusals). Unlike upsert_status_comment
    it does not track or edit an id. Best-effort; ENGINE_LOCAL short-circuits.
    """
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] pr comment pr#{pr}: {body}\n")
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    publish_token = os.environ.get("PUBLISH_TOKEN", "")
    env = dict(os.environ)
    if publish_token:
        env["GH_TOKEN"] = publish_token
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{pr}/comments", "-f", f"body={body}"],
        text=True, capture_output=True, env=env,
    )
    if result.returncode != 0:
        sys.stderr.write(f"[engine] pr comment post failed (needs issues:write): {result.stderr.strip()}\n")


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


def has_fanout(protocol):
    """True iff the protocol has at least one fan-out state."""
    return any(s.get("kind") == "fanout" for s in protocol.get("states", []))


def _render_leg_section(sf, max_iter):
    """Project one leg's state file into (state, checklist-lines).
    Mirrors the per-branch rendering in render_fanout_status_body so the
    single-phase and multi-phase comments read identically per leg.
      missing file        → ("pending", "_pending_")
      file, empty history → (<state>, "_no iterations yet_")
      file, with history  → (<state>, "- ✅/✗ iteration n/m …")
    """
    if not os.path.isfile(sf):
        return "pending", "_pending_"
    data = load_yaml(sf)
    history = data.get("history", []) or []
    st = data.get("state", "") or ""
    if not history:
        return st, "_no iterations yet_"
    out = []
    for entry in history:
        it = entry.get("iteration", "?")
        fb = entry.get("feedback", "") or ""
        # `feedback` carries only iterate-severity failures, so a gate that fails
        # a block/advisory check leaves it empty. Fall back to the recorded checks
        # map so we never claim "all checks passed" when a non-iterate check failed.
        failed = [k for k, v in (entry.get("checks", {}) or {}).items() if v != "pass"]
        if fb:
            out.append(f"- ✗ iteration {it}/{max_iter} — {fb}")
        elif failed:
            out.append(f"- ⚠️ iteration {it}/{max_iter} — checks failed: {', '.join(sorted(failed))}")
        else:
            out.append(f"- ✅ iteration {it}/{max_iter} — all checks passed")
    return st, "\n".join(out)


def render_pipeline_status_body(dir_, pid, instance, proto):
    """
    render_pipeline_status_body <state_dir> <pid> <instance> <protocol.json>
    Protocol-LEVEL projection for a MULTI-PHASE protocol: render every phase
    (agent + fan-out) in declared order into ONE PR-comment body. Unlike
    render_fanout_status_body (single fan-out phase, <instance>/<branch>.yaml),
    this resolves each leg with its phase id, so fan-out legs are found at
    <instance>/<phase>.<branch>.yaml — the fix for PR #65's stuck "_pending_".
    The audit link points at the instance directory (all phases live under it).
    """
    branch_val = os.environ.get("STATE_BRANCH", STATE_BRANCH)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    link = f"https://github.com/{repo}/tree/{branch_val}/{pid}/{instance}"

    protocol = load_yaml(proto)
    inf = instance_file(dir_, pid, instance)
    inst = load_yaml(inf) if os.path.isfile(inf) else {}
    overridden = {o.get("phase") for o in (inst.get("overrides") or [])}
    halted = inst.get("halted") or {}
    halted_phase = halted.get("phase") if halted.get("reason") == "blocked" else None

    sections = ""
    any_active = any_failed = False
    blocked_phase = None

    for ph in phase_states(protocol):
        ph_id = ph["id"]
        if ph.get("kind") == "fanout":
            for b in ph.get("branches", []):
                bid = b["id"]
                max_iter = b.get("max_iterations", "?")
                sf = state_file(dir_, pid, instance, bid, phase=ph_id)
                st, lines = _render_leg_section(sf, max_iter)
                sections += f"**{ph_id} · {bid}**\n\n{lines}\n\n"
                if st == "done":
                    pass
                elif st == "failed":
                    any_failed = True
                else:  # pending / in-flight
                    any_active = True
        else:  # agent phase
            max_iter = ph.get("max_iterations", "?")
            sf = state_file(dir_, pid, instance, phase=ph_id)
            st, lines = _render_leg_section(sf, max_iter)
            if ph_id == halted_phase:
                note = "\n⛔ blocked — a required gate did not pass; a write-access user can `/override`."
                blocked_phase = ph_id
            elif ph_id in overridden:
                note = "\n⚠️ blocked → overridden; proceeding."
            elif st == "done":
                note = "\n✅ clear."
            elif st == "failed":
                note = "\n❌ failed."
                any_failed = True
            else:  # pending / in-flight
                note = ""
                if st != "done":
                    any_active = True
            sections += f"**{ph_id}**\n\n{lines}\n{note}\n\n"

    if blocked_phase:
        headline = (f"⛔ Blocked at **{blocked_phase}** — a write-access user can comment "
                    f"`/override <reason>` to proceed past this gate.")
    elif any_failed:
        headline = "❌ Pipeline failed — a gate could not complete; merge is gated."
    elif any_active:
        headline = "⏳ In progress…"
    else:
        headline = "✅ Pipeline complete — published."

    return f"\U0001f50d **{pid} · {instance}**\n\n{sections}{headline}\n\n[Full state & audit trail]({link})\n"


def render_instance_status_body(dir_, pid, instance, proto_path):
    """Pick the right shared-comment renderer for an instance-keyed comment:
    multi-phase → the protocol-level pipeline renderer; single-phase fan-out →
    the legacy fan-out renderer (kept byte-identical)."""
    protocol = load_yaml(proto_path)
    if is_multiphase(protocol):
        return render_pipeline_status_body(dir_, pid, instance, proto_path)
    return render_fanout_status_body(dir_, pid, instance, proto_path)


def ensure_status_comment(state_dir, pid, instance, proto_path, pr):
    """
    ensure_status_comment <state_dir> <pid> <instance> <protocol.json> <pr>
    Create-once guard for the shared instance-level status comment.  Reads the
    instance file's status_comment_id; if empty → render + upsert + cas_push;
    if already set → no-op.  Now also fires for a multi-phase protocol whose
    FIRST phase is an agent (e.g. preflight), so the protocol-level comment +
    audit link appear the moment the pipeline starts. A single-agent protocol
    (no fan-out, not multi-phase) has no shared comment → no-op.
    """
    protocol = load_yaml(proto_path)
    if not is_multiphase(protocol) and not has_fanout(protocol):
        return  # single-agent path: status lives in the per-state file, no shared comment
    inf = instance_file(state_dir, pid, instance)
    inst_data = load_yaml(inf) if os.path.isfile(inf) else {}
    cid = inst_data.get("status_comment_id", "") or ""
    if cid:
        # Already created on a previous run — idempotent no-op.
        return
    body = render_instance_status_body(state_dir, pid, instance, proto_path)
    upsert_status_comment(inf, pr, body)
    cas_push(state_dir, f"{instance}: ensure shared status comment")


def _cli(argv):
    if not argv:
        sys.stderr.write("lib.py: no subcommand given\n")
        sys.exit(2)
    cmd, args = argv[0], argv[1:]
    if cmd == "protocol-id":
        print(protocol_id(args[0]))
    elif cmd == "state-file":
        # state-file <dir> <pid> <instance> [branch] [phase]   (positional; pass "" for branch to get a phase-only path)
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
    elif cmd == "post-pr-comment":
        # post-pr-comment <pr> <body>
        post_pr_comment(args[0], args[1])
    elif cmd == "cas-push":
        # cas-push <dir> <message>
        cas_push(*args)
    elif cmd == "resolve-executable":
        # resolve-executable <sdir> <name> <pdir> [exec]
        ex = args[3] if len(args) > 3 else ""
        print(resolve_executable(args[0], args[1], args[2], ex))
    elif cmd == "state-checkout":
        state_checkout(args[0])
    elif cmd == "ensure-status-comment":
        # ensure-status-comment <state_dir> <pid> <instance> <protocol.json> <pr>
        ensure_status_comment(args[0], args[1], args[2], args[3], args[4])
    elif cmd == "match-trigger":
        # match-trigger <protocol.json> <event_name> <action> <comment_body>
        with open(args[0]) as f:
            proto = json.load(f)
        ev = args[1] if len(args) > 1 else ""
        act = args[2] if len(args) > 2 else ""
        body = args[3] if len(args) > 3 else ""
        print(match_trigger(proto, ev, act, body))
    elif cmd == "agent-workflow":
        # agent-workflow <protocol.json> <phase> <branch>
        with open(args[0]) as f:
            proto = json.load(f)
        ph = args[1] if len(args) > 1 else ""
        br = args[2] if len(args) > 2 else ""
        print(agent_workflow(proto, ph, br))
    elif cmd == "route":
        # route <protocols_dir> <event_name> <action> <comment_body> <dispatch_protocol> <is_pr_comment>
        pdir = args[0]
        ev = args[1] if len(args) > 1 else ""
        act = args[2] if len(args) > 2 else ""
        body = args[3] if len(args) > 3 else ""
        disp = args[4] if len(args) > 4 else ""
        ispr = (args[5].lower() == "true") if len(args) > 5 else True
        try:
            r = route(pdir, ev, act, body, disp, ispr)
        except ValueError as e:
            sys.stderr.write(f"lib.py route: {e}\n")
            sys.exit(1)
        print(f"protocol={r['protocol']}")
        print(f"skip={'true' if r['skip'] else 'false'}")
    else:
        sys.stderr.write(f"lib.py: unknown subcommand {cmd}\n")
        sys.exit(2)


if __name__ == "__main__":
    _cli(sys.argv[1:])
