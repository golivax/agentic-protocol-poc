#!/usr/bin/env python3
"""socratic-answering-present — verify the answering step's evidence manifest lists
the answered Open Questions artifact, and carries a run id. Form/presence only —
the answering is automated (a claude run that researches + fills the OPEN leaves),
so this verifies the artifact exists, not the substance of the answers."""
import fnmatch
import json
import sys

REQUIRED = [
    "OPEN_QUESTIONS-*.adoc",
]

with open(sys.argv[1]) as f:
    evidence = json.load(f)

paths = [str(e.get("path", "")) for e in (evidence.get("files") or []) if e.get("path")]
missing = [pat for pat in REQUIRED if not any(fnmatch.fnmatch(p, pat) for p in paths)]
if not str(evidence.get("run_id", "") or "").strip():
    missing.append("run_id (empty/missing)")

if missing:
    print(json.dumps({"check": "socratic-answering-present", "pass": False,
                      "feedback": "missing: " + ", ".join(missing)}))
else:
    print(json.dumps({"check": "socratic-answering-present", "pass": True, "feedback": ""}))
