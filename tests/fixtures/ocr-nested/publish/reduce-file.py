#!/usr/bin/env python3
"""Per-file `reduce` merge hook: reduces over the nested `findings` fanout legs.
Reads inputs/findings.json (the collected per-finding rows) and reports how many
findings legs completed. Trivial + offline for the walk test."""
import json, os, sys
workdir = sys.argv[1]
findings = []
p = os.path.join(workdir, "inputs", "findings.json")
if os.path.isfile(p):
    with open(p) as f:
        findings = json.load(f)
done = [row for row in findings if row.get("state") == "done"]
print(json.dumps({"conclusion": "success",
                  "summary": f"reduced {len(done)}/{len(findings)} findings"}))
