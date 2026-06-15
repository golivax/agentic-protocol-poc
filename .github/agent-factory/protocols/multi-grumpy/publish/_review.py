#!/usr/bin/env python3
"""Shared PR-review publication mechanism for multi-grumpy branches.
Imported by the thin per-branch entrypoints; not invoked directly."""
import json, os, subprocess, sys

def gh_api(path, method=None, input_json=None, token=None, jq=None):
    cmd = ["gh", "api", path]
    if jq: cmd += ["--jq", jq]
    if method: cmd += ["--method", method, "--input", "-"]
    env = dict(os.environ)
    if token: env["GH_TOKEN"] = token
    return subprocess.run(cmd, input=input_json, text=True, capture_output=True, env=env)

def run(req_body, req_summary, ok_body, ok_summary):
    """req_*: REQUEST_CHANGES wording (req_body may contain {n}/{nfiles}); ok_*: APPROVE wording."""
    evid = sys.argv[1]
    with open(evid) as f: ev = json.load(f)
    issues = any(v.get("verdict") == "issues-found"
                 for fe in ev.get("files", []) for v in fe.get("verdicts", []))
    event = "REQUEST_CHANGES" if issues else "APPROVE"
    comments = []
    for fe in ev.get("files", []):
        for v in fe.get("verdicts", []):
            if v.get("verdict") != "issues-found": continue
            for fd in v.get("findings", []):
                c = {"path": fe["path"], "side": fd["side"], "line": fd["line"], "body": fd["comment"]}
                if fd.get("start_line"):
                    c["start_line"] = fd["start_line"]; c["start_side"] = fd["side"]
                comments.append(c)
    n = len(comments); nfiles = len({c["path"] for c in comments})
    if event == "REQUEST_CHANGES":
        body, conclusion, summary = req_body.format(n=n, nfiles=nfiles), "failure", req_summary
    else:
        body, conclusion, summary = ok_body, "success", ok_summary
    base = {"event": event, "body": body, "comments": comments}
    repo, pr, token = os.environ["GITHUB_REPOSITORY"], os.environ["PR"], os.environ.get("PUBLISH_TOKEN", "")
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] POST repos/{repo}/pulls/{pr}/reviews\n")
        sys.stderr.write(json.dumps(base, indent=2) + "\n")
    else:
        commit = gh_api(f"repos/{repo}/pulls/{pr}", token=token, jq=".head.sha").stdout.strip()
        payload = {**base, "commit_id": commit}
        def post(p):
            r = gh_api(f"repos/{repo}/pulls/{pr}/reviews", method="POST",
                       input_json=json.dumps(p), token=token)
            if r.returncode != 0:
                sys.stderr.write(f"[publish] reviews POST failed: {r.stdout}{r.stderr}\n")
            return r.returncode == 0
        if not post(payload):
            if event == "APPROVE":
                sys.stderr.write("[publish] APPROVE rejected (repo setting?); falling back to COMMENT\n")
                payload["event"] = "COMMENT"
                if not post(payload):
                    sys.stderr.write("[publish] COMMENT fallback also failed\n"); sys.exit(1)
            else:
                sys.stderr.write(f"[publish] review submission failed for event={event}\n"); sys.exit(1)
    print(json.dumps({"conclusion": conclusion, "summary": summary}))
