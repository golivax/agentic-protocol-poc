#!/usr/bin/env python3
"""Mini-pipeline fan-out branch publish hook. No-op echo for tests.
ABI: <hook> <evidence.json> <instance-key> -> {"conclusion","summary"}."""
import json
print(json.dumps({"conclusion": "success", "summary": "alpha published"}))
