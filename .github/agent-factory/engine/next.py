#!/usr/bin/env python3
# next.py <state_workdir> <instance-key> <protocol.json> <command> [head_sha]
# Pure planner: reads (state, protocol, command), emits an action JSON on stdout.
# The WORKFLOW decides what an event means and passes a command; the planner never
# sniffs events. Commands:
#   start    external request — fresh review from a clean slate (Absent or Terminal);
#            leave an in-flight review undisturbed (Active → halt).
#   reset    unconditional fresh review (a new head commit invalidates the old one).
#   continue the engine's own iterate loop — resume Active; halt on Terminal.
# head_sha (optional) is recorded as instance metadata (the check-run target); it is
# NEVER compared to decide policy — that decision lives in the workflow.
import json
import os
import re
import sys

# The script's directory is sys.path[0], so `import lib` finds lib.py alongside.
import lib
import paths

DIR = sys.argv[1]
INSTANCE = sys.argv[2]
PROTO = sys.argv[3]
COMMAND = sys.argv[4]
HEAD_SHA = sys.argv[5] if len(sys.argv) > 5 else ""
BRANCH = os.environ.get("BRANCH", "")
PHASE = os.environ.get("PHASE", "")
SUBSTATE = os.environ.get("SUBSTATE", "")
# NODE_PATH (NOT PATH — that is the OS executable search path) is the dot-joined
# tree-navigation path of a `continue` dispatch. When it resolves to a fanout
# node, the planner emits that fanout's children matrix (a nested fanout is
# dispatched as its own engine invocation). Empty → legacy flat/leaf resolution.
NODE_PATH = os.environ.get("NODE_PATH", "")

with open(PROTO) as f:
    proto_data = json.load(f)

PID = proto_data["name"]  # equivalent to lib.protocol_id(PROTO); proto_data already loaded

try:
    lib.check_depth(proto_data)
except ValueError as _e:
    sys.stderr.write(f"[next] {_e}\n")
    sys.exit(2)

# Check out the state branch first: both the fan-out planner (below) and the
# single-agent path write into DIR, and state_checkout only depends on DIR,
# so doing it here is behaviour-preserving for the single-agent path.
lib.state_checkout(DIR)


def _fanout_action(proto, path, branches):
    """Build the run-fanout action dict for the fanout at `path`. Single-phase
    keeps reason='fanout' with NO phase key; multi-phase uses reason='phase:<id>'
    and adds the phase key — byte-identical to the legacy start_fanout /
    seed_and_dispatch_phase emits. `branches` stays the authoritative key the GHA
    layer reads; `legs` is emitted alongside as the path-aware companion for
    nested-fanout matrix wiring."""
    multi = lib.is_multiphase(proto)
    act = {"action": "run-fanout", "iteration": 1, "feedback": "",
           "reason": (f"phase:{path[-1]}" if multi else "fanout")}
    if multi:
        act["phase"] = path[-1]
    act["branches"] = branches
    # `legs` is the path-aware companion to `branches` (Stage 3): one entry per
    # child carrying its full tree path. Additive — `branches` stays authoritative
    # for the depth-<=3 GHA layer; `legs` carries the tree path the nested-fanout
    # matrix needs. child_tree_path = fanout_tree_path + [branch_id].
    act["legs"] = [{"path": ".".join(path + [b["id"]])} for b in branches]
    return act


def enter_node(proto, path, command, emit=True):
    """Recursive sequencer: seed the node at the tree-navigation `path` and, when
    `emit`, print its action JSON (run-agent / run-fanout / gate-open noop).

    Generalizes start_fanout + seed_and_dispatch_phase + seed_branch into one
    walk. INSTANCE-file / phase-label / cas_push side-effects stay with the
    callers (start_fanout, seed_and_dispatch_phase) — this function only seeds the
    node's own state file(s) and emits. Every file call routes the tree path
    through lib.state_path (single-phase drops the leading top fanout id), so
    depth-<=3 files stay byte-identical to the legacy seed_branch layout.

    `path` is rooted at the top phase/fanout id; e.g. the top fanout enters as
    [fanout_id]. `command` is carried for parity with the recursive callers."""
    kind = paths.node_kind(proto, path)
    node = paths.node_at_path(proto, path)
    life = paths.enclosing_fanout_id(proto, path)
    fpath = lib.state_path(proto, path)
    if kind == "sequence":
        first = paths.first_child_id(node)
        cf = lib.state_file(DIR, PID, INSTANCE, path=fpath)
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        lib.dump_yaml(cf, {"protocol": PID, "instance": INSTANCE, "state": life,
                           "sub_state": first, "iteration": 1, "gates": {}, "history": []})
        return enter_node(proto, path + [first], command, emit=emit)
    if kind == "fanout":
        # Top fanout (len 1) keeps the legacy _instance.yaml `joined` mechanism the
        # callers own. Only NESTED fanouts (len > 1) get a path-keyed __join.yaml
        # marker (a top fanout marker would be a new file under the instance dir →
        # breaks byte-identity). The file path routes through state_path.
        if len(path) > 1:
            lib.write_join(DIR, PID, INSTANCE, lib.state_path(proto, path), {"joined": False})
        branches = [_seed_child(proto, path + [b["id"]], b) for b in node.get("branches", [])]
        if emit:
            print(json.dumps(_fanout_action(proto, path, branches)))
            return None
        # emit=False → return the branch emit-dicts so the caller can print the
        # run-fanout AFTER its own instance-file / label / cas_push side-effects
        # (preserving the legacy seed→side-effects→cas_push→emit ordering).
        return branches
    if kind == "agent":
        sf = lib.state_file(DIR, PID, INSTANCE, path=fpath)
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        lib.dump_yaml(sf, {"protocol": PID, "instance": INSTANCE, "state": life or path[-1],
                           "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": []})
        if emit:
            act = {"action": "run-agent", "iteration": 1, "feedback": "",
                   "reason": f"phase:{path[-1]}"}
            if lib.is_multiphase(proto):
                act["phase"] = path[-1]
            print(json.dumps(act))
        return {"id": path[-1], "workflow": node.get("workflow"), "iteration": 1, "feedback": ""}
    if kind == "gate":
        pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
        lib.open_gate(DIR, PID, INSTANCE, PROTO, path[-1], HEAD_SHA, pr,
                      phase=(path[-1] if lib.is_multiphase(proto) else None))
        if emit:
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"gate-open:{path[-1]}"}))
        return None
    return None


def _seed_child(proto, path, cfg):
    """Seed one fan-out child (flat agent OR sub-pipeline) WITHOUT emitting; return
    its run-fanout branch dict (carrying `substate` for a sub-pipeline). The dict
    field-orderings and the per-file head_sha rule reproduce the legacy seed_branch
    output byte-for-byte for depth-<=3. All file paths route through lib.state_path."""
    life = paths.enclosing_fanout_id(proto, path)
    if paths.is_sequence(proto, path):
        first = paths.first_child_id(cfg)
        cf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, path))
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        lib.dump_yaml(cf, {"protocol": PID, "instance": INSTANCE, "state": life,
                           "sub_state": first, "iteration": 1, "gates": {}, "history": []})
        sf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, path + [first]))
        lib.dump_yaml(sf, {"protocol": PID, "instance": INSTANCE, "state": life,
                           "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": []})
        fc = paths.node_at_path(proto, path + [first])
        return {"id": path[-1], "workflow": fc.get("workflow"),
                "substate": first, "iteration": 1, "feedback": ""}
    sf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, path))
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    flat = {"protocol": PID, "instance": INSTANCE, "state": life,
            "iteration": 1, "gates": {}, "history": []}
    if lib.is_multiphase(proto):
        flat["head_sha"] = HEAD_SHA
    lib.dump_yaml(sf, flat)
    return {"id": path[-1], "workflow": cfg.get("workflow"), "iteration": 1, "feedback": ""}


def is_fanout():
    for s in proto_data.get("states", []):
        if s.get("kind") == "fanout":
            return True
    return False


def start_fanout():
    fstate = None
    for s in proto_data.get("states", []):
        if s.get("kind") == "fanout":
            fstate = s["id"]
            break

    # Delegate seeding to the recursive sequencer (top fanout → tree path
    # [fstate]); emit=False so we keep the legacy seed→instance→label→cas_push→emit
    # ordering. enter_node returns the branch emit-dicts for the deferred emit.
    branches = enter_node(proto_data, [fstate], COMMAND, emit=False)

    inf = lib.instance_file(DIR, PID, INSTANCE)
    os.makedirs(os.path.dirname(inf), exist_ok=True)
    lib.dump_yaml(inf, {
        "protocol": PID, "instance": INSTANCE, "head_sha": HEAD_SHA, "joined": False,
    })

    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, fstate)
    lib.cas_push(DIR, f"{PID}/{INSTANCE}: fan-out review ({COMMAND})")
    print(json.dumps(_fanout_action(proto_data, [fstate], branches)))


def seed_and_dispatch_phase(phase_id, command, reset_instance=False):
    """Multi-phase: seed the named phase's state + the instance cursor, push,
    and emit the phase's run action. Used for the first phase (start/reset) and
    for each subsequent phase (advance-phase).

    reset_instance=True is the RESTART path (a fresh start/reset re-entering the
    first phase). A restart must wipe the WHOLE prior run, not just re-seed phase
    one: stale later-phase leg files (e.g. review.grumpy.yaml) and instance
    markers (joined / overrides / halted) would otherwise survive and keep
    rendering in the status comment, and head_sha would stay pinned to the old
    commit. We delete every state file under the instance dir and rebuild
    _instance.yaml from scratch. The prior run's status comment is ABANDONED, not
    reused: it gets one final "superseded" edit (banner above its frozen state)
    and then status_comment_id is dropped, so this run creates a NEW comment —
    one edited-in-place comment per run reads far more clearly than a single
    comment rewritten across restarts. On a phase advance / override
    (reset_instance=False) earlier phases must be preserved, so we mutate in
    place exactly as before."""
    phase_state = lib.state_by_id(proto_data, phase_id)
    if phase_state is None:
        sys.stderr.write(f"[next] unknown phase '{phase_id}' in protocol\n")
        sys.exit(1)
    kind = phase_state.get("kind")
    inf = lib.instance_file(DIR, PID, INSTANCE)
    inst_dir = os.path.dirname(inf)
    os.makedirs(inst_dir, exist_ok=True)

    prev = lib.load_yaml(inf) if os.path.isfile(inf) else {}
    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    if reset_instance:
        # Abandon the prior run's status comment so this run gets a FRESH one.
        # Render its final state FIRST (the files still exist), edit the old
        # comment once with a "superseded" banner above that frozen snapshot,
        # then drop the id — ensure_status_comment creates the new comment.
        old_cid = prev.get("status_comment_id")
        if old_cid:
            frozen = lib.render_instance_status_body(DIR, PID, INSTANCE, PROTO)
            banner = (f"↻ _Superseded — a newer run started (new commit or "
                      f"`/review`); see the newest **{PID} · {INSTANCE}** comment below._")
            lib.finalize_superseded_comment(pr, old_cid, f"{banner}\n\n{frozen}")
        # Remove the prior run's phase label so a restart from e.g. "approval
        # gate" does not orphan it (the wipe below drops our tracking of it).
        lib.remove_pr_label(pr, prev.get("phase_label", ""))
        # Wipe every prior-run state file (phase yamls + fan-out legs + the old
        # _instance.yaml); cas_push stages the deletions. Start the instance clean.
        for name in os.listdir(inst_dir):
            p = os.path.join(inst_dir, name)
            if os.path.isfile(p):
                os.remove(p)
        inst = {}
    else:
        inst = prev

    inst.setdefault("protocol", PID)
    inst.setdefault("instance", INSTANCE)
    inst["phase"] = phase_id
    if HEAD_SHA:
        # Restart refreshes the head to the new commit; an in-pipeline advance
        # keeps the instance-seed head (per-phase files carry their own head_sha).
        if reset_instance:
            inst["head_sha"] = HEAD_SHA
        else:
            inst.setdefault("head_sha", HEAD_SHA)
    inst.setdefault("joined", False)
    lib.dump_yaml(inf, inst)

    # Sync the PR's phase label to this phase (removes setup / prior label).
    lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, phase_id)

    # All three arms delegate seeding to enter_node (emit=False), keep the
    # phase-specific cas_push message here, then emit — preserving the legacy
    # seed→cas_push→emit ordering exactly. The tree path of a top-level phase is
    # simply [phase_id].
    if kind == "fanout":
        # enter_node returns the branch emit-dicts (emit deferred to after cas_push).
        branches = enter_node(proto_data, [phase_id], command, emit=False)
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter fan-out phase {phase_id} ({command})")
        print(json.dumps(_fanout_action(proto_data, [phase_id], branches)))
    elif kind == "gate":
        # cursor already written above; enter_node's gate arm calls open_gate
        # (seeds the gate file + check-run + status comment). No agent dispatch —
        # the run ends and waits for a human.
        enter_node(proto_data, [phase_id], command, emit=False)
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: open gate {phase_id} ({command})")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"gate-open:{phase_id}"}))
    else:
        enter_node(proto_data, [phase_id], command, emit=False)
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter agent phase {phase_id} ({command})")
        print(json.dumps({"action": "run-agent", "iteration": 1, "feedback": "",
                          "reason": f"phase:{phase_id}", "phase": phase_id}))


def do_override():
    """HITL escape-hatch: a write-access human forces a *blocked* gate to advance
    one phase. Authorization happened in the workflow (ctx step); next.py only ever
    sees an authorized override. Reads the `halted` marker on _instance.yaml. On a
    valid blocked marker, records the override beside the failure, clears the
    marker, and seeds+dispatches the next phase. Otherwise posts an explanatory
    comment and halts — no state change. emit_halt is defined below this point in
    the script, so the halt JSON is printed inline here."""
    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    inf = lib.instance_file(DIR, PID, INSTANCE)

    def refuse(message, reason):
        lib.post_pr_comment(pr, message)
        print(json.dumps({"action": "halt", "iteration": 0, "feedback": "", "reason": reason}))

    if not os.path.isfile(inf):
        refuse(f"Nothing to override — no {PID} run exists for this PR.",
               "override: no instance")
        return

    inst = lib.load_yaml(inf)
    halted = inst.get("halted") or {}

    if halted.get("reason") == "blocked":
        blocked_phase = halted.get("phase")
        nxt = lib.next_phase_id(proto_data, blocked_phase)
        if not nxt:
            refuse("The blocked gate is the final phase; there is nothing to advance to.",
                   "override: no next phase")
            return
        actor = os.environ.get("OVERRIDE_ACTOR", "")
        reason = os.environ.get("OVERRIDE_REASON", "")
        inst.setdefault("overrides", []).append(
            {"phase": blocked_phase, "actor": actor, "reason": reason})
        inst.pop("halted", None)
        # Note: _instance.yaml's head_sha stays the instance-seed head (as on every
        # phase advance — seed_and_dispatch_phase uses setdefault). The authoritative
        # head the forced phase runs against is recorded per-phase in its own state
        # file; we intentionally do not rewrite the cursor head on override.
        lib.dump_yaml(inf, inst)  # persist before seed_and_dispatch_phase reloads inf
        note = f"⚠️ {blocked_phase} gate was blocked — overridden by @{actor}; proceeding to {nxt}."
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        # Advance exactly one phase. seed_and_dispatch_phase reloads _instance.yaml
        # (keeping the overrides[] record + cleared halted just written), sets the
        # cursor to nxt, CAS-pushes, and emits that phase's run action.
        seed_and_dispatch_phase(nxt, "override")
        return

    # Not a blocked halt → give a precise message: exhausted vs simply not-halted.
    cursor = inst.get("phase") or ""
    cursor_sf = lib.state_file(DIR, PID, INSTANCE, phase=cursor) if cursor else ""
    cursor_state = (lib.load_yaml(cursor_sf).get("state")
                    if cursor_sf and os.path.isfile(cursor_sf) else "")
    if cursor_state == "failed":
        refuse(f"The {cursor} gate is exhausted (it could not produce a valid result), "
               f"not blocked. Override only applies to a gate that ran and returned a "
               f"blocking verdict; re-run the pipeline instead.",
               "override: exhausted")
    else:
        refuse("Nothing to override — the pipeline is not currently halted at a "
               f"blocked gate (current phase: {cursor}).",
               "override: not halted")


def do_resolve_gate():
    """Human approval gate resolution. write/admin auth happened in the workflow;
    next.py sees only an authorized actor. Reads GATE_DECISION/ACTOR/REASON/PR_AUTHOR
    from env, mutates the cursor gate's `gates` record, and advances (approve) or
    halts (request-changes / reject). Guards refuse with one PR comment + a halt
    action — no state change. A gate is 'live' when gates.state in {open,
    changes_requested}."""
    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    inf = lib.instance_file(DIR, PID, INSTANCE)
    decision = os.environ.get("GATE_DECISION", "")
    actor = os.environ.get("GATE_ACTOR", "")
    reason = os.environ.get("GATE_REASON", "")
    pr_author = os.environ.get("GATE_PR_AUTHOR", "")

    def refuse(message, code):
        lib.post_pr_comment(pr, message)
        print(json.dumps({"action": "halt", "iteration": 0, "feedback": "", "reason": code}))

    if not os.path.isfile(inf):
        refuse(f"Nothing to resolve — no {PID} run exists for this PR.", "gate: no instance")
        return
    inst = lib.load_yaml(inf)
    cursor = inst.get("phase") or ""
    cur_state = lib.state_by_id(proto_data, cursor)
    if not cursor or not cur_state or cur_state.get("kind") != "gate":
        refuse(f"Nothing to resolve — no approval gate is currently open for this PR "
               f"(current phase: {cursor or 'none'}).", "gate: not a gate")
        return

    sf = lib.state_file(DIR, PID, INSTANCE, phase=cursor)
    gdata = lib.load_yaml(sf) if os.path.isfile(sf) else {}
    g = gdata.get("gates") or {}
    gstate = g.get("state", "")
    sha = gdata.get("head_sha", "") or HEAD_SHA
    cr_name = f"{PID}/{cursor}"

    if gstate == "rejected":
        refuse("This gate was rejected; push a new commit or comment `/review` to "
               "restart the pipeline.", "gate: rejected")
        return
    if gstate not in ("open", "changes_requested"):
        refuse(f"Nothing to resolve — the {cursor} gate is not awaiting a decision "
               f"(state: {gstate or 'unknown'}).", "gate: not live")
        return
    if (decision == "approve" and cur_state.get("approve_excludes_author")
            and actor and actor == pr_author):
        refuse(f"@{actor} the PR author cannot approve their own gate; another "
               f"write-access reviewer must `/approve`.", "gate: self-approve")
        return

    g.setdefault("history", []).append({"decision": decision, "actor": actor, "reason": reason})

    if decision == "approve":
        g["state"] = "approved"
        gdata["gates"] = g
        lib.dump_yaml(sf, gdata)
        lib.set_check_run(cr_name, sha, "completed", "success", "Approved", f"Approved by @{actor}.")
        nxt = lib.next_phase_id(proto_data, cursor)
        if nxt:
            note = f"✅ {cursor} gate approved by @{actor}; proceeding to {nxt}."
            if reason:
                note += f"\n\n> {reason}"
            lib.post_pr_comment(pr, note)
            seed_and_dispatch_phase(nxt, "approve")   # sets cursor, pushes, emits run action
        else:
            lib.set_check_run(PID, sha, "completed", "success", "Complete", f"Approved by @{actor}.")
            note = f"✅ {cursor} gate approved by @{actor}; pipeline complete."
            if reason:
                note += f"\n\n> {reason}"
            lib.post_pr_comment(pr, note)
            body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
            lib.upsert_status_comment(inf, pr, body)
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "done")
            lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} approved by {actor} → done")
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"gate:approved:{cursor}"}))
        return

    if decision == "request-changes":
        g["state"] = "changes_requested"
        gdata["gates"] = g
        lib.dump_yaml(sf, gdata)
        lib.set_check_run(cr_name, sha, "completed", "failure", "Changes requested",
                          f"Changes requested by @{actor}.")
        body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
        lib.upsert_status_comment(inf, pr, body)
        note = (f"🔁 {cursor} gate — changes requested by @{actor}. Push a new commit to "
                f"re-run the pipeline, or a reviewer can `/approve`.")
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} changes requested by {actor}")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"gate:changes:{cursor}"}))
        return

    if decision == "reject":
        g["state"] = "rejected"
        gdata["gates"] = g
        gdata["state"] = "failed"
        lib.dump_yaml(sf, gdata)
        lib.set_check_run(cr_name, sha, "completed", "failure", "Rejected", f"Rejected by @{actor}.")
        lib.set_check_run(PID, sha, "completed", "failure", "Pipeline rejected", f"Rejected by @{actor}.")
        body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
        lib.upsert_status_comment(inf, pr, body)
        note = f"⛔ {cursor} gate rejected by @{actor}. Push a new commit or `/review` to restart."
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "failed")
        lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} rejected by {actor} → failed")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"gate:rejected:{cursor}"}))
        return

    refuse(f"Unknown gate decision '{decision}'.", "gate: unknown decision")


def _gate_phase(proto):
    """Phase qualifier for sub-pipeline gate/cursor state files: the fanout phase
    id in a multi-phase protocol, else None (single-phase → unqualified paths).
    Kept for the do_resolve_gate / do_override paths that still use legacy coords."""
    if lib.is_multiphase(proto):
        fo = lib._fanout_state(proto)
        return fo["id"] if fo else None
    return None


def _find_open_gate(proto, want=""):
    """Return the full tree-navigation path to the first open data-gate, or None.
    `want` is an optional branch id to restrict the search. Walks the top-level
    fanout's branches; for each sub-pipeline branch whose cursor sub_state is a
    gate in state 'open', returns the path [fanout_id, branch_id, gate_substate_id].
    For depth <=3 this is byte-identical to the old (branch_id, gate_id) pair."""
    fo = lib._fanout_state(proto)
    if not fo:
        return None
    fo_id = fo["id"]
    for b in fo.get("branches", []):
        bid = b["id"]
        if want and bid != want:
            continue
        branch_path = [fo_id, bid]
        cf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, branch_path))
        if not os.path.isfile(cf):
            continue
        cur = lib.load_yaml(cf)
        sub = cur.get("sub_state", "")
        for s in b.get("states", []):
            if s["id"] == sub and s.get("kind") == "gate":
                gate_path = branch_path + [sub]
                gsf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, gate_path))
                if os.path.isfile(gsf) and lib.load_yaml(gsf).get("gates", {}).get("state") == "open":
                    return gate_path
    return None



def _parse_answers(body):
    """Parse `/answer qID: value` pairs (one or many lines). Returns {id: value}.
    The body is UNTRUSTED input: it is parsed and stored in a JSON file whose
    path (never its content) is passed to the coverage check — safe."""
    out = {}
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("/answer"):
            line = line[len("/answer"):].strip()
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*[:=]\s*(.+)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def do_answer():
    """Parse /answer comments, accumulate answers, run coverage check, advance gate."""
    import subprocess as _sp
    pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
    body = os.environ.get("ANSWER_BODY", "")
    actor = os.environ.get("ANSWER_ACTOR", "")
    # Optional explicit branch: `/answer <branch> qID: val` — first bare token.
    want = ""
    head = body[len("/answer"):].strip() if body.startswith("/answer") else body
    first = head.split()[0] if head.split() else ""
    if first and ":" not in first and "=" not in first:
        want = first

    gate_path = _find_open_gate(proto_data, want)
    if gate_path is None:
        lib.post_pr_comment(pr, "No open question gate to answer right now.")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": "answer: no open gate"}))
        return

    # Derive coords from the gate tree path via path helpers.
    # branch_path is the cursor file's tree path (parent of the gate leaf).
    branch = gate_path[-2]
    gate = gate_path[-1]
    branch_path = gate_path[:-1]
    # ph is the phase qualifier for dispatch_continue (None in single-phase, fanout
    # id in multi-phase) — derived from enclosing_fanout_id filtered by is_multiphase.
    ph = (paths.enclosing_fanout_id(proto_data, gate_path)
          if lib.is_multiphase(proto_data) else None)
    # life is the leg's in-flight state value: the enclosing fanout id. This replaces
    # the old hardcoded `lib._fanout_state(proto_data)["id"]` (which was already fixed
    # in the prior task) and the old "_gate_phase"-based approach. For depth <=3 the
    # value is identical: enclosing_fanout_id(["review","B","clarify"]) == "review".
    life = paths.enclosing_fanout_id(proto_data, gate_path)

    # File paths all derived from the gate/branch tree paths via lib.state_path so
    # depth-<=3 filenames stay byte-identical (single-phase drops the leading fanout id).
    gsf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto_data, gate_path))
    gdata = lib.load_yaml(gsf)
    questions = gdata.get("gates", {}).get("questions", []) or []

    # Merge new answers into the persisted answers artifact.
    apath = lib.output_artifact_path(DIR, PID, INSTANCE,
                                     path=lib.state_path(proto_data, gate_path), kind="answers")
    existing = {}
    if os.path.isfile(apath):
        try:
            existing = json.load(open(apath)).get("answers", {}) or {}
        except (json.JSONDecodeError, ValueError):
            existing = {}
    existing.update(_parse_answers(body))
    doc = {"questions": questions, "answers": existing}
    os.makedirs(os.path.dirname(apath), exist_ok=True)
    with open(apath, "w") as fh:
        json.dump(doc, fh)

    # Run the gate's answers-coverage check over the synthesized doc.
    # The check receives FILE PATHS, not answer content — no injection risk.
    gate_cfg = next(s for s in lib.branch_substates(proto_data, branch) if s["id"] == gate)
    check_run = (gate_cfg.get("checks", [{}])[0]).get("run", "answers-coverage")
    pdir = os.path.dirname(os.path.abspath(PROTO))
    res = lib.resolve_executable(f"{pdir}/checks", check_run, pdir, "")
    kind, path = res.split("\t", 1)
    import tempfile
    empty_fd, empty = tempfile.mkstemp(prefix="answers-empty-")
    os.close(empty_fd)
    cov = _sp.run([path, apath, empty, empty], text=True, capture_output=True)
    verdict = json.loads(cov.stdout) if cov.stdout.strip() else {"pass": False, "feedback": "no verdict"}

    gdata["gates"].setdefault("history", []).append(
        {"actor": actor, "answers": list(_parse_answers(body).keys())})
    if not verdict.get("pass"):
        lib.dump_yaml(gsf, gdata)
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} gate {gate} partial answers")
        lib.post_pr_comment(pr, f"Recorded. Still needed: {verdict.get('feedback', '')}.")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": "answer: partial"}))
        return

    # Full coverage → close the gate, advance the branch cursor to the next sub-state.
    gdata["gates"]["state"] = "answered"
    lib.dump_yaml(gsf, gdata)
    nxt_sub = lib.next_substate_id(proto_data, branch, gate)
    cf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto_data, branch_path))
    cur = lib.load_yaml(cf)
    sha = gdata.get("head_sha", "") or HEAD_SHA
    if nxt_sub:
        nxt_path = branch_path + [nxt_sub]
        cur["sub_state"] = nxt_sub
        cur["state"] = life
        lib.dump_yaml(cf, cur)
        nsf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto_data, nxt_path))
        lib.dump_yaml(nsf, {"protocol": PID, "instance": INSTANCE, "state": life,
                            "iteration": 1, "gates": {}, "head_sha": sha, "history": []})
        lib.set_check_run(f"{PID}/{branch}/{gate}", sha, "completed", "success",
                          "Answered", f"Answered by @{actor}.")
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} gate {gate} answered -> {nxt_sub}")
        lib.post_pr_comment(pr, f"{gate} answered by @{actor}; continuing to {nxt_sub}.")
        lib.dispatch_continue(PID, INSTANCE, branch, nxt_sub, phase=ph or "")
    else:
        cur["state"] = "done"
        lib.dump_yaml(cf, cur)
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} gate {gate} answered -> leg done")
        lib.fire_join_dispatch(PID, INSTANCE)
    print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                      "reason": "answer: complete"}))


# Unbranched start/reset on a fan-out protocol routes to the planner BEFORE the
# single-agent agent-unit discovery (which has no kind:"agent" state to read and
# would error). The branched fan-out path (continue with BRANCH set) and the
# single-agent path both fall through this guard unchanged.
if COMMAND == "answer":
    do_answer()
    sys.exit(0)

if COMMAND == "override":
    do_override()
    sys.exit(0)

if COMMAND == "resolve-gate":
    do_resolve_gate()
    sys.exit(0)

if lib.is_multiphase(proto_data) and not PHASE and not BRANCH:
    # Multi-phase protocol, unbranched/unphased entry → seed the FIRST phase.
    if COMMAND in ("start", "reset"):
        first = lib.phase_states(proto_data)[0]["id"]
        pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
        lib.apply_setup_label(proto_data, pr)
        # Fresh entry → restart: wipe any prior run's state for this instance.
        seed_and_dispatch_phase(first, COMMAND, reset_instance=True)
        sys.exit(0)
    else:
        sys.stderr.write(f"[next] multi-phase '{COMMAND}' needs a PHASE\n")
        sys.exit(2)

if lib.is_multiphase(proto_data) and PHASE and COMMAND == "advance-phase":
    # Phase transition (advance.py already set the cursor to PHASE) → seed+dispatch it.
    seed_and_dispatch_phase(PHASE, COMMAND)
    sys.exit(0)

# A `continue` whose tree path resolves to a fanout node dispatches that fanout's
# children matrix (nested fanouts are entered as their own engine invocation).
# Sits before the legacy BRANCH/PHASE/SUBSTATE single-agent resolution so the
# flat/leaf continue path stays untouched.
if COMMAND == "continue" and NODE_PATH:
    _p = NODE_PATH.split(".")
    _kind = paths.node_kind(proto_data, _p)
    if _kind == "fanout":
        # Match the established seed(emit=False)→cas_push→emit ordering of
        # start_fanout / seed_and_dispatch_phase: enter_node seeds the leg files +
        # nested __join.yaml marker locally, cas_push publishes them to origin so
        # the matrix legs (which re-checkout state) find them, THEN emit.
        branches = enter_node(proto_data, _p, "continue", emit=False)
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter nested fanout {NODE_PATH} (continue)")
        print(json.dumps(_fanout_action(proto_data, _p, branches)))
        sys.exit(0)
    if _kind == "agent":
        # A `continue` onto an AGENT sub-state of a sub-pipeline leg (e.g. the
        # `report` sub-state after a nested join bubbled the cursor forward).
        # Seed its state file, cas_push so the dispatched agent finds it, then
        # emit a path-qualified run-agent action. Same seed→cas_push→emit order.
        node = paths.node_at_path(proto_data, _p)
        enter_node(proto_data, _p, "continue", emit=False)
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: continue agent {NODE_PATH}")
        act = {"action": "run-agent", "iteration": 1, "feedback": "",
               "reason": f"continue:{NODE_PATH}", "path": NODE_PATH,
               "workflow": node.get("workflow")}
        declared = lib.state_inputs(proto_data, _p[-1])
        if declared:
            # Path-aware: resolve each `from` OUTERMOST-search relative to this
            # node's tree path, so a nested agent's inputs reach an earlier
            # nested-fanout leg's evidence (e.g. report ← analyze.sec/perf).
            act["inputs"] = lib.resolve_inputs(
                proto_data, DIR, PID, INSTANCE,
                consuming_branch=(_p[-2] if len(_p) >= 2 else None),
                consuming_phase=None, inputs=declared, consuming_path=_p)
        print(json.dumps(act))
        sys.exit(0)
    if _kind == "gate":
        # A `continue` onto a GATE sub-state: enter_node's gate arm opens the gate
        # (seeds the gate file + check-run + status comment); cas_push publishes.
        enter_node(proto_data, _p, "continue", emit=False)
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: continue gate {NODE_PATH} open")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"gate-open:{NODE_PATH}"}))
        sys.exit(0)

if not BRANCH and is_fanout() and not PHASE:
    if COMMAND in ("start", "reset"):
        pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
        lib.apply_setup_label(proto_data, pr)
        start_fanout()
        sys.exit(0)
    elif COMMAND == "continue":
        sys.stderr.write("[next] fanout 'continue' requires a BRANCH\n")
        sys.exit(2)
    else:
        sys.stderr.write(f"[next] unknown command: {COMMAND}\n")
        sys.exit(2)

# The "agent unit" (its id + max_iterations + life_state) comes from
# lib.resolve_agent_unit: PHASE-first → BRANCH → single-agent.
# Single-agent path is the regression-guarded baseline and must stay byte-for-byte
# identical. Error messages are mapped back to the original next.py / engine prefixes.
try:
    _unit = lib.resolve_agent_unit(proto_data, PHASE, BRANCH, SUBSTATE)
except ValueError as e:
    _msg = str(e)
    if _msg.startswith("no phase") or _msg.startswith("PHASE=") or "in phase '" in _msg:
        sys.stderr.write(f"[next] {_msg}\n")
    else:
        sys.stderr.write(f"[engine] {_msg}\n")
    sys.exit(1)
AGENT_STATE = _unit["agent_state"]
MAX = _unit["max_iterations"]
LIFE_STATE = _unit["life_state"]

if MAX is None:
    sys.stderr.write(f"[engine] agent unit '{AGENT_STATE}' has no max_iterations\n")
    sys.exit(1)

# BRANCH/PHASE empty → single-agent path (branch=None, phase=None)
SF = lib.state_file(DIR, PID, INSTANCE,
                    branch=(BRANCH if BRANCH else None),
                    phase=(PHASE if PHASE else None),
                    substate=(SUBSTATE if SUBSTATE else None))


def write_fresh_state():
    os.makedirs(os.path.dirname(SF), exist_ok=True)
    lib.dump_yaml(SF, {
        "protocol": PID,
        "instance": INSTANCE,
        "state": LIFE_STATE,
        "iteration": 1,
        "gates": {},
        "head_sha": HEAD_SHA,
        "history": [],
    })


def emit_run_agent(iteration, feedback, reason):
    action = {"action": "run-agent", "iteration": iteration, "feedback": feedback, "reason": reason}
    if PHASE:
        action["phase"] = PHASE
    if SUBSTATE:
        action["substate"] = SUBSTATE
    declared = lib.state_inputs(proto_data, AGENT_STATE)
    if declared:
        action["inputs"] = lib.resolve_inputs(
            proto_data, DIR, PID, INSTANCE,
            consuming_branch=(BRANCH or None), consuming_phase=(PHASE or None),
            inputs=declared)
    print(json.dumps(action))


def emit_halt(reason):
    print(json.dumps({"action": "halt", "iteration": 0, "feedback": "", "reason": reason}))


def start_fresh():
    write_fresh_state()
    lib.cas_push(DIR, f"{PID}/{INSTANCE}: fresh review ({COMMAND})")
    emit_run_agent(1, "", COMMAND)


# Determine the instance lifecycle from the (optional) state file. Defensive reads
# (// fallbacks) keep a malformed/partial state file from aborting under set -e.
# Literal equality, NOT a case pattern: a case glob would treat metacharacters in
# LIFE_STATE (if a future protocol used any) as wildcards.
LIFECYCLE = "absent"
ITER = 0

if os.path.isfile(SF):
    sf_data = lib.load_yaml(SF)
    STATE = sf_data.get("state") or ""
    ITER = sf_data.get("iteration") or 0
    if STATE == LIFE_STATE:
        # iterations 1..MAX are all valid attempts; > MAX means the loop is spent.
        if ITER > MAX:
            LIFECYCLE = "terminal"
        else:
            LIFECYCLE = "active"
    else:
        LIFECYCLE = "terminal"  # done / failed / any non-agent terminal

if COMMAND == "reset":
    start_fresh()
elif COMMAND == "start":
    if LIFECYCLE in ("absent", "terminal"):
        start_fresh()
    else:  # active
        emit_halt(f"review already in flight at iteration {ITER}")
elif COMMAND == "continue":
    if LIFECYCLE == "absent":
        start_fresh()
    elif LIFECYCLE == "active":
        sf_data = lib.load_yaml(SF)
        history = sf_data.get("history") or []
        FB = ""
        if history:
            FB = history[-1].get("feedback") or ""
        emit_run_agent(ITER, FB, "resume")
    else:  # terminal
        emit_halt("instance is terminal")
else:
    sys.stderr.write(f"[next] unknown command: {COMMAND}\n")
    sys.exit(2)
