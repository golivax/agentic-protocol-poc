#!/usr/bin/env python3
"""Publish hook that writes a marker file to MARKER_DIR/<instance>-<branch>.published.
Used by the regression test for non-first-fanout publish.

ABI: <hook> <evidence.json> <instance-key>
Env: ENGINE_LOCAL, GITHUB_REPOSITORY, PUBLISH_TOKEN, PR, MARKER_DIR (test-injected)
Prints {"conclusion","summary"} to stdout.
"""
import json
import os
import sys

instance = sys.argv[2] if len(sys.argv) > 2 else "unknown"
branch = os.environ.get("BRANCH", "unknown")
marker_dir = os.environ.get("MARKER_DIR", "")

if marker_dir:
    os.makedirs(marker_dir, exist_ok=True)
    marker = os.path.join(marker_dir, f"{instance}-{branch}.published")
    with open(marker, "w") as fh:
        fh.write(f"published branch={branch} instance={instance}\n")

print(json.dumps({"conclusion": "success", "summary": f"published leg {branch}"}))
