#!/usr/bin/env python3
"""dist/resolve.py — pure protocol resolution for the installer.

No network, no disk writes, stdlib only. The one logic-heavy seam: given a
parsed protocol.json, find every agent workflow it references at any nesting
depth (top-level states, fan-out branches, nested sub-pipelines, fan-outs
inside fan-outs).
"""
import json
import sys


def derive_agents(protocol):
    """Return de-duplicated agent workflow names in first-seen order.

    Walks the whole protocol structure; any dict with a string `workflow`
    value contributes that name. Order is deterministic (depth-first, key
    order as authored), which keeps the installer's `gh aw add` sequence and
    the receipt's file list stable across runs.
    """
    seen = []

    def walk(node):
        if isinstance(node, dict):
            wf = node.get("workflow")
            if isinstance(wf, str) and wf and wf not in seen:
                seen.append(wf)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(protocol)
    return seen


def main(argv):
    if len(argv) >= 3 and argv[1] == "agents":
        with open(argv[2]) as f:
            protocol = json.load(f)
        for name in derive_agents(protocol):
            print(name)
        return 0
    sys.stderr.write("usage: resolve.py agents <protocol.json>\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
