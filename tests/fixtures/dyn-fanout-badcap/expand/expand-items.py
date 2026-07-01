#!/usr/bin/env python3
"""Stub expander: emits a fixed items list from items.json beside this script."""
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "items.json")) as f:
    items = json.load(f)
print(json.dumps({"items": items}))
