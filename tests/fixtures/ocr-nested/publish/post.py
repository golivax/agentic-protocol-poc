#!/usr/bin/env python3
"""Top `merge` hook: reduces over the `review` (per-file) fanout legs. Reads
inputs/files.json (one row per file leg) and reports how many file legs
completed. Trivial + offline for the walk test."""
import json, os, sys
workdir = sys.argv[1]
files = []
p = os.path.join(workdir, "inputs", "files.json")
if os.path.isfile(p):
    with open(p) as f:
        files = json.load(f)
done = [row for row in files if row.get("state") == "done"]
print(json.dumps({"conclusion": "success",
                  "summary": f"merged {len(done)}/{len(files)} files"}))
