#!/usr/bin/env python3
"""Mini-pipeline gate conclude hook (the decide->act seam, decide half).

ABI: <hook> <evidence.json> <instance-key>; env BLOCKING in {"0","1"}.
Prints {"conclusion","summary","blocked"}. Blocked iff BLOCKING==1 OR the
evidence carries {"gate":"blocked"} — this is the test's control knob.
"""
import json
import os
import sys

blocked = os.environ.get("BLOCKING", "0") == "1"
try:
    with open(sys.argv[1]) as f:
        ev = json.load(f)
    if isinstance(ev, dict) and ev.get("gate") == "blocked":
        blocked = True
except (OSError, ValueError, IndexError):
    pass

if blocked:
    print(json.dumps({"conclusion": "blocked", "summary": "gate blocked", "blocked": True}))
else:
    print(json.dumps({"conclusion": "clear", "summary": "gate clear", "blocked": False}))
