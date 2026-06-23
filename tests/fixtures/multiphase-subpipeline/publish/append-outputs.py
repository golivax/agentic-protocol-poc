#!/usr/bin/env python3
import json, os, sys

inputs_dir = os.path.join(sys.argv[1], "inputs")


def _read(name):
    p = os.path.join(inputs_dir, f"{name}.json")
    if not os.path.isfile(p):
        return {}
    try:
        return json.load(open(p))
    except (json.JSONDecodeError, ValueError):
        return {}


a = _read("a")
b = _read("b")
combined = (a.get("summary", "") + "\n" + b.get("summary", "")).strip()
print(json.dumps({"conclusion": "success",
                  "summary": f"Combined outputs:\n{combined}"}))
