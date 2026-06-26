#!/usr/bin/env python3
"""legion-artifacts — verify the legion leg's evidence manifest lists the core
.planning artifacts produced by `/legion:map`, and carries a run id (so the
combine hook can download its mm-tree artifact). Form/presence only — never
re-runs legion."""
import fnmatch
import json
import sys

REQUIRED = [
    "CODEBASE.md",
    "codebase/index.jsonl",
    "codebase/symbols.json",
    "config/directory-mappings.yaml",
]

with open(sys.argv[1]) as f:
    evidence = json.load(f)

paths = [str(e.get("path", "")) for e in (evidence.get("files") or []) if e.get("path")]
missing = [pat for pat in REQUIRED if not any(fnmatch.fnmatch(p, pat) for p in paths)]
if not str(evidence.get("run_id", "") or "").strip():
    missing.append("run_id (empty/missing)")

if missing:
    print(json.dumps({"check": "legion-artifacts", "pass": False,
                      "feedback": "missing: " + ", ".join(missing)}))
else:
    print(json.dumps({"check": "legion-artifacts", "pass": True, "feedback": ""}))
