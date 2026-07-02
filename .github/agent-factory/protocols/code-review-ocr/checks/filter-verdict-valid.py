#!/usr/bin/env python3
"""code-review-ocr check: a filter leg's verdict is well-formed. finding_id present;
keep is boolean; a KEPT finding carries an anchor (side + line). Form check only
(never judges whether keep is correct). ABI: <evidence> <diff> <changed>; exit 0."""
import json, sys

def main():
    try:
        ev = json.load(open(sys.argv[1]))
    except Exception as e:
        print(json.dumps({"check": "filter-verdict-valid", "pass": False,
                          "feedback": f"unreadable evidence: {e}"})); return
    if not isinstance(ev, dict):
        print(json.dumps({"check": "filter-verdict-valid", "pass": False,
                          "feedback": "evidence must be an object"})); return
    fid = ev.get("finding_id"); keep = ev.get("keep")
    ok = isinstance(fid, str) and fid.strip() and isinstance(keep, bool)
    fb = ""
    if not ok:
        fb = "finding_id must be a non-empty string and keep a boolean"
    elif keep:
        a = ev.get("anchor")
        if not (isinstance(a, dict) and a.get("side") in ("LEFT", "RIGHT") and isinstance(a.get("line"), int)):
            ok, fb = False, "a kept finding must carry an anchor {side, line}"
    print(json.dumps({"check": "filter-verdict-valid", "pass": bool(ok), "feedback": fb}))

if __name__ == "__main__":
    main()
