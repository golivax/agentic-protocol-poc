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
import sys

# The script's directory is sys.path[0], so `import lib` finds lib.py alongside.
import lib

DIR = sys.argv[1]
INSTANCE = sys.argv[2]
PROTO = sys.argv[3]
COMMAND = sys.argv[4]
HEAD_SHA = sys.argv[5] if len(sys.argv) > 5 else ""
BRANCH = os.environ.get("BRANCH", "")
PHASE = os.environ.get("PHASE", "")

with open(PROTO) as f:
    proto_data = json.load(f)

PID = proto_data["name"]  # equivalent to lib.protocol_id(PROTO); proto_data already loaded

# Check out the state branch first: both the fan-out planner (below) and the
# single-agent path write into DIR, and state_checkout only depends on DIR,
# so doing it here is behaviour-preserving for the single-agent path.
lib.state_checkout(DIR)


def emit_run_fanout(branches):
    print(json.dumps({"action": "run-fanout", "iteration": 1, "feedback": "", "reason": "fanout", "branches": branches}))


def is_fanout():
    for s in proto_data.get("states", []):
        if s.get("kind") == "fanout":
            return True
    return False


def start_fanout():
    # Find the fanout state
    fstate = None
    branches_config = []
    for s in proto_data.get("states", []):
        if s.get("kind") == "fanout":
            fstate = s["id"]
            branches_config = s.get("branches", [])
            break

    # Seed one fresh state file per branch
    for b in branches_config:
        bid = b["id"]
        sf = lib.state_file(DIR, PID, INSTANCE, bid)
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        lib.dump_yaml(sf, {
            "protocol": PID,
            "instance": INSTANCE,
            "state": fstate,
            "iteration": 1,
            "gates": {},
            "history": [],
        })

    # Seed the shared _instance.yaml
    inf = lib.instance_file(DIR, PID, INSTANCE)
    os.makedirs(os.path.dirname(inf), exist_ok=True)
    lib.dump_yaml(inf, {
        "protocol": PID,
        "instance": INSTANCE,
        "head_sha": HEAD_SHA,
        "joined": False,
    })

    lib.cas_push(DIR, f"{PID}/{INSTANCE}: fan-out review ({COMMAND})")

    # Build branch dispatch list (id, workflow, iteration, feedback)
    branches = [
        {"id": b["id"], "workflow": b["workflow"], "iteration": 1, "feedback": ""}
        for b in branches_config
    ]
    emit_run_fanout(branches)


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
    if reset_instance:
        # Abandon the prior run's status comment so this run gets a FRESH one.
        # Render its final state FIRST (the files still exist), edit the old
        # comment once with a "superseded" banner above that frozen snapshot,
        # then drop the id — ensure_status_comment creates the new comment.
        old_cid = prev.get("status_comment_id")
        if old_cid:
            pr = INSTANCE[len("pr-"):] if INSTANCE.startswith("pr-") else INSTANCE
            frozen = lib.render_instance_status_body(DIR, PID, INSTANCE, PROTO)
            banner = (f"↻ _Superseded — a newer run started (new commit or "
                      f"`/review`); see the newest **{PID} · {INSTANCE}** comment below._")
            lib.finalize_superseded_comment(pr, old_cid, f"{banner}\n\n{frozen}")
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

    if kind == "fanout":
        branches_config = phase_state.get("branches", [])
        # Per-branch phase files carry head_sha (consistent with write_fresh_state;
        # the legacy start_fanout omits it — deliberate divergence).
        for b in branches_config:
            sf = lib.state_file(DIR, PID, INSTANCE, b["id"], phase=phase_id)
            os.makedirs(os.path.dirname(sf), exist_ok=True)
            lib.dump_yaml(sf, {
                "protocol": PID, "instance": INSTANCE, "state": phase_id,
                "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": [],
            })
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter fan-out phase {phase_id} ({command})")
        branches = [{"id": b["id"], "workflow": b["workflow"], "iteration": 1, "feedback": ""}
                    for b in branches_config]
        print(json.dumps({"action": "run-fanout", "iteration": 1, "feedback": "",
                          "reason": f"phase:{phase_id}", "phase": phase_id, "branches": branches}))
    else:
        sf = lib.state_file(DIR, PID, INSTANCE, phase=phase_id)
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        lib.dump_yaml(sf, {
            "protocol": PID, "instance": INSTANCE, "state": phase_id,
            "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": [],
        })
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


# Unbranched start/reset on a fan-out protocol routes to the planner BEFORE the
# single-agent agent-unit discovery (which has no kind:"agent" state to read and
# would error). The branched fan-out path (continue with BRANCH set) and the
# single-agent path both fall through this guard unchanged.
if COMMAND == "override":
    do_override()
    sys.exit(0)

if lib.is_multiphase(proto_data) and not PHASE and not BRANCH:
    # Multi-phase protocol, unbranched/unphased entry → seed the FIRST phase.
    if COMMAND in ("start", "reset"):
        first = lib.phase_states(proto_data)[0]["id"]
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

if not BRANCH and is_fanout() and not PHASE:
    if COMMAND in ("start", "reset"):
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
    _unit = lib.resolve_agent_unit(proto_data, PHASE, BRANCH)
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
                    phase=(PHASE if PHASE else None))


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
