#!/usr/bin/env python3
import json, sys

evidence = json.load(open(sys.argv[1]))
rationale = evidence.get("rationale", "") or ""
if rationale.strip():
    print(json.dumps({"check": "rationale-present", "pass": True, "feedback": ""}))
else:
    print(json.dumps({"check": "rationale-present", "pass": False,
                      "feedback": "rationale missing/empty"}))
