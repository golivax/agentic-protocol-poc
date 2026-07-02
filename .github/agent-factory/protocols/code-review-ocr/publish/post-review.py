#!/usr/bin/env python3
"""code-review-ocr top `merge` (zone 4, merge hook). ABI: <workdir> <instance>.

Reads inputs/files.json (the from_fanout rows over the `review` file legs — one
row per file, {leg_id,key,state,evidence}). Each row's `evidence` is the file's
per-file `reduce` result (reduce-file.py's printed {conclusion,summary,survivors}
dict, persisted as that leg's own output evidence by next.py's nested-merge arm
— see reduce-file.py's docstring and the task-6 report for the full carry-up
trace, including a documented engine-side gap where `evidence` currently comes
back None for a sub-pipeline leg under from_fanout).

Collects every row's `evidence.survivors`, dedups cross-file by
(path, side, line, existing_code), regroups by path, and posts ONE GitHub review
via the shared _review.py mechanism (copied verbatim from code-review's
publish/_review.py — same APPROVE/REQUEST_CHANGES + ENGINE_LOCAL dry-run
gating, reused unmodified).

_review.run()'s real signature (read from code-review/publish/_review.py and its
caller publish-grumpy.py) is `run(req_body, req_summary, ok_body, ok_summary)`;
it reads the review evidence itself from the file path in sys.argv[1] (the
single-agent/branch publish-hook ABI: `<hook> <evidence.json> <instance-key>`).
A merge hook's own ABI is `<hook> <workdir> <instance>` instead (see
run_merge_hook in lib.py, which invokes `[path, workdir, instance]`), so this
hook materializes the regrouped {"files":[...]} evidence to a temp JSON file and
splices that path into sys.argv before delegating — _review.run() is reused
completely unmodified, including its ENGINE_LOCAL dry-run print-instead-of-POST
gating."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _review  # noqa: E402  (shared mechanism, same dir)


def _dedup(findings):
    """Cross-file de-duplication by (path, side, line, existing_code): the same
    underlying code location flagged by more than one filter leg collapses to
    one review comment."""
    seen, out = set(), []
    for f in findings:
        k = (f.get("path"), f.get("side"), f.get("line"), f.get("existing_code"))
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


def _regroup(findings):
    """[{path,side,line,comment[,start_line]}] -> _review.py's expected
    {"files": [{"path", "verdicts": [{"verdict": "issues-found", "findings": [...]}]}]}
    shape (see _review._iter_verdicts / _collect_comments)."""
    by_path = {}
    for f in findings:
        by_path.setdefault(f.get("path"), []).append(f)
    files = []
    for path, fs in by_path.items():
        finding_objs = []
        for f in fs:
            fo = {"side": f.get("side", "RIGHT"), "line": f.get("line"),
                  "comment": f.get("comment", "")}
            if "start_line" in f:
                fo["start_line"] = f["start_line"]
            finding_objs.append(fo)
        files.append({"path": path, "verdicts": [
            {"verdict": "issues-found", "findings": finding_objs}]})
    return {"files": files}


def main():
    workdir, instance = sys.argv[1], sys.argv[2]
    rows = json.load(open(os.path.join(workdir, "inputs", "files.json")))
    findings = []
    for r in rows:
        ev = r.get("evidence")
        if isinstance(ev, dict):
            findings.extend(ev.get("survivors") or [])
    findings = _dedup(findings)
    evidence = _regroup(findings)

    fd, evpath = tempfile.mkstemp(prefix="post-review-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(evidence, f)
        # _review.run() reads sys.argv[1] as the evidence.json path — splice it in.
        sys.argv = [sys.argv[0], evpath, instance]
        _review.run(
            req_body="\U0001f9fe OCR review — {n} issue(s) across {nfiles} file(s) "
                     "survived per-finding filtering. See the inline comments.",
            req_summary="OCR review requested changes — resolve the flagged findings.",
            ok_body="\U0001f9fe OCR review: every candidate finding was filtered out; "
                    "nothing survived across all files.",
            ok_summary="OCR review found nothing to flag after per-finding filtering.")
    finally:
        os.unlink(evpath)


if __name__ == "__main__":
    main()
