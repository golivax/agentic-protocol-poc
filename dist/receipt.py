#!/usr/bin/env python3
"""dist/receipt.py — the install receipt: write, diff, drift, version compat.

stdlib only. The receipt (`.github/agent-factory/.install.json`) is the source
of truth for updates: what was installed, at what ref/versions, and the content
hash of every file so a re-sync can detect orphans and local drift.
"""
import hashlib
import json
import os
import sys


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_receipt(source, ref, engine_version, protocols, files, root):
    return {
        "source": source,
        "ref": ref,
        "engine_version": engine_version,
        "protocols": dict(protocols),
        "files": {p: file_hash(os.path.join(root, p)) for p in files},
    }


def write_receipt(path, receipt):
    with open(path, "w") as f:
        json.dump(receipt, f, indent=2, sort_keys=True)
        f.write("\n")


def main(argv):
    if len(argv) >= 8 and argv[1] == "write":
        out, source, ref, ev, protos_json, root = argv[2:8]
        files = argv[8:]
        rec = build_receipt(source, ref, ev, json.loads(protos_json), files, root)
        write_receipt(out, rec)
        return 0
    sys.stderr.write("usage: receipt.py write <out> <source> <ref> <engine_version> <protocols-json> <root> <file>...\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
