#!/usr/bin/env python3
import json, sys

evidence = json.load(open(sys.argv[1]))
summary = evidence.get("summary", "") or ""
if summary.strip():
    print(json.dumps({"check": "summary-present", "pass": True, "feedback": ""}))
else:
    print(json.dumps({"check": "summary-present", "pass": False,
                      "feedback": "summary missing/empty"}))
