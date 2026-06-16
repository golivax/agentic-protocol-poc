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


def seed_and_dispatch_phase(phase_id, command):
    """Multi-phase: seed the named phase's state + the instance cursor, push,
    and emit the phase's run action. Used for the first phase (start/reset) and
    for each subsequent phase (advance-phase)."""
    phase_state = lib.state_by_id(proto_data, phase_id)
    if phase_state is None:
        sys.stderr.write(f"[next] unknown phase '{phase_id}' in protocol\n")
        sys.exit(1)
    kind = phase_state.get("kind")
    inf = lib.instance_file(DIR, PID, INSTANCE)
    os.makedirs(os.path.dirname(inf), exist_ok=True)

    inst = lib.load_yaml(inf) if os.path.isfile(inf) else {}
    inst.setdefault("protocol", PID)
    inst.setdefault("instance", INSTANCE)
    inst["phase"] = phase_id
    if HEAD_SHA:
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


# Unbranched start/reset on a fan-out protocol routes to the planner BEFORE the
# single-agent agent-unit discovery (which has no kind:"agent" state to read and
# would error). The branched fan-out path (continue with BRANCH set) and the
# single-agent path both fall through this guard unchanged.
if lib.is_multiphase(proto_data) and not PHASE and not BRANCH:
    # Multi-phase protocol, unbranched/unphased entry → seed the FIRST phase.
    if COMMAND in ("start", "reset"):
        first = lib.phase_states(proto_data)[0]["id"]
        seed_and_dispatch_phase(first, COMMAND)
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

# The "agent unit" (its id + max_iterations) comes from a fan-out BRANCH when
# BRANCH is set, else from the single-agent state. Single-agent path is the
# regression-guarded baseline and must stay byte-for-byte identical.
if BRANCH:
    AGENT_STATE = None
    MAX = None
    for s in proto_data.get("states", []):
        if s.get("kind") == "fanout":
            for b in s.get("branches", []):
                if b["id"] == BRANCH:
                    AGENT_STATE = b["id"]
                    MAX = b.get("max_iterations")
                    break
    if not AGENT_STATE:
        sys.stderr.write(f"[engine] no branch '{BRANCH}' in protocol\n")
        sys.exit(1)
else:
    AGENT_STATE = None
    MAX = None
    for s in proto_data.get("states", []):
        if s.get("kind") == "agent":
            AGENT_STATE = s["id"]
            MAX = s.get("max_iterations")
            break
    if not AGENT_STATE:
        sys.stderr.write("[engine] protocol has no agent state\n")
        sys.exit(1)

if MAX is None:
    sys.stderr.write(f"[engine] agent unit '{AGENT_STATE}' has no max_iterations\n")
    sys.exit(1)

# LIFE_STATE is the value a live state file's .state carries while the agent unit
# is in flight, and is what both write_fresh_state stamps and the lifecycle check
# compares against. Single-agent: the agent state id. Fan-out branch: the owning
# fan-out state's id (a branch's per-branch file records the fan-out state, not the
# branch id). For grumpy these are both "review", so the single-agent path is
# byte-for-byte unchanged.
if BRANCH:
    LIFE_STATE = None
    for s in proto_data.get("states", []):
        if s.get("kind") == "fanout":
            LIFE_STATE = s["id"]
            break
else:
    LIFE_STATE = AGENT_STATE

# BRANCH empty → single-agent path (branch=None)
SF = lib.state_file(DIR, PID, INSTANCE, BRANCH if BRANCH else None)


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
    print(json.dumps({"action": "run-agent", "iteration": iteration, "feedback": feedback, "reason": reason}))


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
