#!/usr/bin/env python3
# join.py <state_workdir> <instance-key> <protocol.json>
# Fan-out barrier evaluator. Reads every branch state file for the instance; once
# ALL branches are terminal (done/failed) and the instance is not yet joined, sets
# the aggregate check-run (success iff every branch is `done`, else failure),
# renders the status comment, marks _instance.yaml joined, and CAS-pushes. Idempotent.
# Env: GITHUB_REPOSITORY, PUBLISH_TOKEN, PR, PR_HEAD_SHA, ENGINE_LOCAL.
import json
import os
import sys

# Allow importing lib from the same directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib

def main():
    if len(sys.argv) < 4:
        sys.stderr.write("usage: join.py <state_workdir> <instance-key> <protocol.json>\n")
        sys.exit(1)

    dir_ = sys.argv[1]
    instance = sys.argv[2]
    proto = sys.argv[3]

    pid = lib.protocol_id(proto)
    pr = os.environ.get("PR", instance)  # matches join.sh PR=${PR:-$INSTANCE}; PR unset only under ENGINE_LOCAL
    sha = os.environ.get("PR_HEAD_SHA", "")

    lib.state_checkout(dir_)
    inf = lib.instance_file(dir_, pid, instance)

    if not os.path.isfile(inf):
        sys.stderr.write(f"[join] no instance file for {pid}/{instance}\n")
        sys.exit(0)

    instance_data = lib.load_yaml(inf)
    if instance_data.get("joined"):   # engine only ever writes joined: true (a bool)
        sys.stderr.write(f"[join] {pid}/{instance} already joined; no-op\n")
        sys.exit(0)

    # Collect each branch's terminal state.
    with open(proto) as f:
        protocol = json.load(f)

    # Determine the fan-out phase to evaluate. Multi-phase: the cursor's phase.
    # Single-phase (multi-grumpy): the sole fan-out state (cursor absent).
    cursor_phase = instance_data.get("phase", "") or ""
    multiphase = lib.is_multiphase(protocol)
    fanout_state = None
    if multiphase and cursor_phase:
        st = lib.state_by_id(protocol, cursor_phase)
        if st and st.get("kind") == "fanout":
            fanout_state = st
    if fanout_state is None:
        for st in protocol.get("states", []):
            if st.get("kind") == "fanout":
                fanout_state = st
                break

    branches = [b["id"] for b in (fanout_state.get("branches", []) if fanout_state else [])]
    phase_for_path = cursor_phase if (multiphase and cursor_phase) else None

    all_terminal = True
    all_done = True
    for b in branches:
        sf = lib.state_file(dir_, pid, instance, b, phase=phase_for_path)
        st = ""
        if os.path.isfile(sf):
            try:
                branch_data = lib.load_yaml(sf)
                st = branch_data.get("state", "") or ""
            except Exception:
                st = ""
        # Missing file → not terminal (same as join.sh: yq on missing file → "")
        if st == "done":
            pass
        elif st == "failed":
            all_done = False
        else:
            all_terminal = False

    if not all_terminal:
        sys.stderr.write(f"[join] {pid}/{instance} not all terminal yet; waiting\n")
        sys.exit(0)

    if all_done:
        concl = "success"
        title = "Review complete"
        summary = "All review branches completed."
    else:
        concl = "failure"
        title = "Review incomplete"
        summary = "A review branch could not complete; merge is gated."

    lib.set_check_run(pid, sha, "completed", concl, title, summary)

    # Final shared-comment update: the closing headline now matches the aggregate.
    # Reads the comment id from _instance.yaml (inf) — the plan job created it — so
    # this only PATCHes. No-op echo under ENGINE_LOCAL.
    body = lib.render_instance_status_body(dir_, pid, instance, proto)
    lib.upsert_status_comment(inf, pr, body)

    # A fan-out phase is always terminal-before-join in the current model (its
    # `.next` is the join state), so once all branches are terminal the instance
    # is finalized here. A multi-fan-out pipeline would instead advance from the
    # JOIN state's `.next`; that is intentionally not supported yet.
    instance_data["joined"] = True
    lib.dump_yaml(inf, instance_data)
    lib.cas_push(dir_, f"{instance}: join → {concl} (all branches terminal)")


if __name__ == "__main__":
    main()
