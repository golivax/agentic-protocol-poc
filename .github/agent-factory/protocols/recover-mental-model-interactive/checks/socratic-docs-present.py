#!/usr/bin/env python3
"""socratic-docs-present — verify the socratic phase-2 evidence manifest lists the
synthesized documentation set (PRD, use-cases, at least one ADR, arc42), and
carries a run id. Form/presence only — never re-runs socratic."""
import fnmatch
import json
import sys

REQUIRED = [
    "docs/specs/prd-*.adoc",
    "docs/specs/use-cases-*.adoc",
    "docs/specs/adrs/*.adoc",
    "docs/arc42/arc42-*.adoc",
]

with open(sys.argv[1]) as f:
    evidence = json.load(f)

paths = [str(e.get("path", "")) for e in (evidence.get("files") or []) if e.get("path")]
missing = [pat for pat in REQUIRED if not any(fnmatch.fnmatch(p, pat) for p in paths)]
if not str(evidence.get("run_id", "") or "").strip():
    missing.append("run_id (empty/missing)")

if missing:
    print(json.dumps({"check": "socratic-docs-present", "pass": False,
                      "feedback": "missing: " + ", ".join(missing)}))
else:
    print(json.dumps({"check": "socratic-docs-present", "pass": True, "feedback": ""}))
