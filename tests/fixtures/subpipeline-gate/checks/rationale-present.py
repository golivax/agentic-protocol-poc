#!/usr/bin/env python3
import json, sys

with open(sys.argv[1]) as f:
    evidence = json.load(f)
rationale = evidence.get("rationale", "") or ""
if rationale.strip():
    print(json.dumps({"check": "rationale-present", "pass": True, "feedback": ""}))
else:
    print(json.dumps({"check": "rationale-present", "pass": False,
                      "feedback": "rationale missing/empty"}))
