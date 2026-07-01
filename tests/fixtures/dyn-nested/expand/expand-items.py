#!/usr/bin/env python3
"""Stub expander (outer `review` fanout): emits a fixed per-file items list from
items.json beside this script. Deterministic + offline for tests."""
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "items.json")) as f:
    items = json.load(f)
print(json.dumps({"items": items}))
