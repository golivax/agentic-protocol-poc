#!/usr/bin/env python3
import json, os, sys
workdir = sys.argv[1]
legs = []
p = os.path.join(workdir, "inputs", "legs.json")
if os.path.isfile(p):
    with open(p) as f:
        legs = json.load(f)
done = [row for row in legs if row.get("state") == "done"]
print(json.dumps({"conclusion": "success", "summary": f"reduced {len(done)}/{len(legs)} legs"}))
