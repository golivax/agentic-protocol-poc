#!/usr/bin/env python3
"""codeset-artifacts — verify the codeset leg's evidence manifest lists the core
artifacts produced by `python -m codeset .`, and carries a run id. Form/presence
only — never re-runs codeset."""
import fnmatch
import json
import sys

REQUIRED = [
    "AGENTS.md",
    ".claude/docs/knowledge.json",
    ".claude/docs/get_context.py",
]

with open(sys.argv[1]) as f:
    evidence = json.load(f)

paths = [str(e.get("path", "")) for e in (evidence.get("files") or []) if e.get("path")]
missing = [pat for pat in REQUIRED if not any(fnmatch.fnmatch(p, pat) for p in paths)]
if not str(evidence.get("run_id", "") or "").strip():
    missing.append("run_id (empty/missing)")

if missing:
    print(json.dumps({"check": "codeset-artifacts", "pass": False,
                      "feedback": "missing: " + ", ".join(missing)}))
else:
    print(json.dumps({"check": "codeset-artifacts", "pass": True, "feedback": ""}))
