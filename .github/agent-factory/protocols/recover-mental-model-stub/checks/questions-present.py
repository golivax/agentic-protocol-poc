#!/usr/bin/env python3
import json, sys

evidence = json.load(open(sys.argv[1]))
questions = evidence.get("questions", []) or []
missing = []
if not questions:
    missing.append("questions list is empty or missing")
else:
    for i, q in enumerate(questions):
        if not (q.get("id") or "").strip():
            missing.append(f"item[{i}] missing non-empty 'id'")
        if not (q.get("text") or "").strip():
            missing.append(f"item[{i}] missing non-empty 'text'")

if missing:
    print(json.dumps({"check": "questions-present", "pass": False,
                      "feedback": "; ".join(missing)}))
else:
    print(json.dumps({"check": "questions-present", "pass": True, "feedback": ""}))
