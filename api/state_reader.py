from __future__ import annotations
import json

def _trigger_summary(proto: dict) -> list[dict]:
    out = []
    for t in proto.get("triggers", []) or []:
        out.append({k: t[k] for k in ("on", "comment_prefix", "command") if k in t})
    return out

def list_protocols(protocol_jsons: list[str]) -> list[dict]:
    out = []
    for raw in protocol_jsons:
        proto = json.loads(raw)
        entry = {"name": proto["name"]}
        if "version" in proto:
            entry["version"] = proto["version"]
        entry["triggers"] = _trigger_summary(proto)
        out.append(entry)
    return sorted(out, key=lambda p: p["name"])

def _state_summary(s: dict) -> dict:
    keep = ("id", "kind", "label", "max_iterations", "next", "of", "sub_state")
    out = {k: s[k] for k in keep if k in s}
    if "checks" in s:
        out["checks"] = s["checks"]
    if "branches" in s:
        out["branches"] = [_state_summary(b) if "states" in b
                           else {k: b[k] for k in ("id", "workflow") if k in b}
                           for b in s["branches"]]
    if "states" in s:  # nested sub-pipeline
        out["states"] = [_state_summary(c) for c in s["states"]]
    return out

def protocol_detail(protocol_json: str) -> dict:
    proto = json.loads(protocol_json)
    out = {"name": proto["name"]}
    if "version" in proto:
        out["version"] = proto["version"]
    if "max_depth" in proto:
        out["max_depth"] = proto["max_depth"]
    out["triggers"] = _trigger_summary(proto)
    out["states"] = [_state_summary(s) for s in proto.get("states", [])]
    return out
