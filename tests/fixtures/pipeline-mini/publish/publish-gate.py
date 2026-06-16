#!/usr/bin/env python3
"""Mini-pipeline gate publish hook (side-effects half). No-op echo for tests.
ABI: <hook> <evidence.json> <instance-key> -> {"conclusion","summary"}."""
import json
print(json.dumps({"conclusion": "neutral", "summary": "gate published"}))
