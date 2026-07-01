#!/usr/bin/env python3
"""dyn-fanout-stub check: the evidence attests it `examined` this leg's file.
The file path is the leg's staged item, surfaced to the agent as inputs.file.path;
here we accept any non-empty `examined` array (form check, per the engine thesis:
verify the shape of evidence, never its substance). ABI: <evidence> <diff> <changed>."""
import json, sys

def main():
    try:
        ev = json.load(open(sys.argv[1]))
    except Exception as e:
        print(json.dumps({"check": "examined-file", "pass": False,
                          "feedback": f"unreadable evidence: {e}"}))
        return
    examined = ev.get("examined")
    ok = isinstance(examined, list) and len(examined) >= 1 and all(
        isinstance(x, str) and x.strip() for x in examined)
    print(json.dumps({"check": "examined-file", "pass": bool(ok),
                      "feedback": "" if ok else "evidence.examined must be a non-empty list of file paths"}))

if __name__ == "__main__":
    main()
