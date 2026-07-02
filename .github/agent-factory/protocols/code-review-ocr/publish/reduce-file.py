#!/usr/bin/env python3
"""code-review-ocr per-file `reduce` (zone 4, merge hook). ABI: <workdir> <instance>.

Reads inputs/findings.json (the from_fanout rows over this file's per-finding
`filter` legs — one row per finding, {leg_id,key,state,evidence}) and keeps only
the findings whose filter verdict was keep:true, using the (possibly relocated)
anchor the filter evidence carries.

Prints {conclusion, summary, survivors}. `survivors` is an ADDITIONAL key beyond
the {conclusion,summary} publish-hook ABI — next.py's nested-merge arm (the
LEG-TERMINAL handling for a per-file `reduce`) persists this printed dict
VERBATIM as the file leg's own output evidence
(<lid>.reduce.evidence.json, via lib.output_artifact_path keyed on this node's
own tree path). That is the carry-up path: the top `merge`'s
`from_fanout: review` later collects each file leg's evidence via
lib.collect_fanout_evidence — which resolves a sub-pipeline leg's terminal
`reduce` sub-state evidence (fixed in ebe9368) — and reads `evidence.survivors`
from there. Keeping `survivors` inside the same printed JSON that already
carries {conclusion,summary} means the carry-up needs no separate side-channel
file and no protocol/engine ABI change.

NOTE: filter.evidence.schema.json only REQUIRES finding_id + keep (+ anchor when
kept) — it does not carry path/existing_code/comment. Those are read here
defensively via .get() so a filter agent that echoes them back round-trips a
complete finding; one that omits them degrades to an anchor-only survivor
(empty path/comment/existing_code). Task 7's ocr-filter-agent prompt should be
authored to echo {path, existing_code, comment} back in its evidence so the
posted review comments are non-empty in the live path.

State-only: no GitHub write (that happens once, in post-review.py)."""
import json
import os
import sys


def main():
    workdir, instance = sys.argv[1], sys.argv[2]
    rows = json.load(open(os.path.join(workdir, "inputs", "findings.json")))
    survivors = []
    for r in rows:
        ev = r.get("evidence") or {}
        if not isinstance(ev, dict) or ev.get("keep") is not True:
            continue
        a = ev.get("anchor") or {}
        survivor = {
            "finding_id": ev.get("finding_id"),
            "path": ev.get("path"),
            "existing_code": ev.get("existing_code", ""),
            "comment": ev.get("comment", ""),
            "side": a.get("side", ev.get("side", "RIGHT")),
            "line": a.get("line", ev.get("line")),
        }
        if "start_line" in a:
            survivor["start_line"] = a["start_line"]
        elif "start_line" in ev:
            survivor["start_line"] = ev["start_line"]
        survivors.append(survivor)
    print(json.dumps({
        "conclusion": "success",
        "summary": f"{len(survivors)} finding(s) kept",
        "survivors": survivors,
    }))


if __name__ == "__main__":
    main()
