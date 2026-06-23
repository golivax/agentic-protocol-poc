#!/usr/bin/env python3
"""Publish hook for the summary branch.

ABI: <hook> <evidence.json> <instance-key>
Env: ENGINE_LOCAL, GITHUB_REPOSITORY, PUBLISH_TOKEN, PR
Prints {"conclusion","summary"} to stdout.
"""
import json, os, sys

# Import lib from the engine dir (same pattern as code-review publish hooks).
sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "..", "..", "..", "engine"))
import lib  # noqa: E402

evidence_path = sys.argv[1]
with open(evidence_path) as f:
    evidence = json.load(f)

summary_text = evidence.get("summary", "") or ""
pr = os.environ.get("PR", "")
body = f"**Change summary**\n\n{summary_text}" if summary_text else "(no summary produced)"
lib.post_pr_comment(pr, body)
print(json.dumps({"conclusion": "success", "summary": "Posted change summary."}))
