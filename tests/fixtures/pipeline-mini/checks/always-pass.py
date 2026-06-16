#!/usr/bin/env python3
"""Mini-pipeline test check: always passes. Honors the check ABI."""
import json
print(json.dumps({"check": "always-pass", "pass": True, "feedback": ""}))
