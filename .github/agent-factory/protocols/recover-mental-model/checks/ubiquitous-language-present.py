#!/usr/bin/env python3
"""ubiquitous-language-present — verify the leg's evidence manifest lists the domain
glossary (CONTEXT.md) produced by the domain-modeling skill, and carries a run id.
Form/presence only — never re-runs domain-modeling."""
import fnmatch
import json
import sys

REQUIRED = [
    "CONTEXT.md",
]

with open(sys.argv[1]) as f:
    evidence = json.load(f)

paths = [str(e.get("path", "")) for e in (evidence.get("files") or []) if e.get("path")]
missing = [pat for pat in REQUIRED if not any(fnmatch.fnmatch(p, pat) for p in paths)]
if not str(evidence.get("run_id", "") or "").strip():
    missing.append("run_id (empty/missing)")

if missing:
    print(json.dumps({"check": "ubiquitous-language-present", "pass": False,
                      "feedback": "missing: " + ", ".join(missing)}))
else:
    print(json.dumps({"check": "ubiquitous-language-present", "pass": True, "feedback": ""}))
