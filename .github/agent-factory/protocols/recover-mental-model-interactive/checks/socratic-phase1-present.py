#!/usr/bin/env python3
"""socratic-phase1-present — verify the socratic phase-1 evidence manifest lists the
Question Tree + Open Questions artifacts, and carries a run id. Form/presence only
— never re-runs socratic."""
import fnmatch
import json
import sys

REQUIRED = [
    "QUESTION_TREE-*.adoc",
    "OPEN_QUESTIONS-*.adoc",
]

with open(sys.argv[1]) as f:
    evidence = json.load(f)

paths = [str(e.get("path", "")) for e in (evidence.get("files") or []) if e.get("path")]
missing = [pat for pat in REQUIRED if not any(fnmatch.fnmatch(p, pat) for p in paths)]
if not str(evidence.get("run_id", "") or "").strip():
    missing.append("run_id (empty/missing)")

if missing:
    print(json.dumps({"check": "socratic-phase1-present", "pass": False,
                      "feedback": "missing: " + ", ".join(missing)}))
else:
    print(json.dumps({"check": "socratic-phase1-present", "pass": True, "feedback": ""}))
