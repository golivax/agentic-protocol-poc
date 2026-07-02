#!/usr/bin/env python3
"""Stub expander (nested `findings` fanout): emits a fixed per-finding items list
from findings.json beside this script. Deterministic + offline for tests."""
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "findings.json")) as f:
    items = json.load(f)
print(json.dumps({"items": items}))
