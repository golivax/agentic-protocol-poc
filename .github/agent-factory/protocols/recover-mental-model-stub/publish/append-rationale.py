#!/usr/bin/env python3
"""Merge hook for the combine state.

ABI: <hook> <workdir> <instance>
Env: ENGINE_LOCAL, GITHUB_REPOSITORY, PUBLISH_TOKEN, PR (inherited)
Reads:
  <workdir>/inputs/summary.json   — summary leg evidence
  <workdir>/inputs/rationale.json — rationale leg output (finalize evidence)
The engine calls this as `<hook> <workdir> <instance>`; the hook appends
"inputs" itself (`inputs_dir = os.path.join(sys.argv[1], "inputs")`).
Posts a combined markdown comment; prints {"conclusion","summary"}.
"""
import json, os, sys

# Import lib from the engine dir (same pattern as code-review publish hooks).
sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "..", "..", "..", "engine"))
import lib  # noqa: E402

inputs_dir = os.path.join(sys.argv[1], "inputs")


def _read(name):
    p = os.path.join(inputs_dir, f"{name}.json")
    if not os.path.isfile(p):
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}


summary_ev = _read("summary")
rationale_ev = _read("rationale")

summary_text = (summary_ev.get("summary", "") or "").strip()
rationale_text = (rationale_ev.get("rationale", "") or "").strip()

parts = []
if summary_text:
    parts.append(f"**Summary**\n\n{summary_text}")
if rationale_text:
    parts.append(f"**Rationale**\n\n{rationale_text}")

body = "\n\n---\n\n".join(parts) if parts else "(no output produced)"

pr = os.environ.get("PR", "")
lib.post_pr_comment(pr, body)
print(json.dumps({"conclusion": "success",
                  "summary": "Recovered mental model: summary + rationale posted."}))
