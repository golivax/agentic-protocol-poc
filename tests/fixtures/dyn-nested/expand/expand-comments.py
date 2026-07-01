#!/usr/bin/env python3
"""Stub expander (nested `comments` fanout): emits a fixed per-comment items list
from comments.json beside this script. Deterministic + offline for tests."""
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "comments.json")) as f:
    items = json.load(f)
print(json.dumps({"items": items}))
