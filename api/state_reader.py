from __future__ import annotations
import json
import yaml
from datetime import datetime

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
    keep = ("id", "kind", "label", "workflow", "max_iterations", "next", "of", "sub_state")
    out = {k: s[k] for k in keep if k in s}
    if "checks" in s:
        out["checks"] = s["checks"]
    if "branches" in s:
        # Summarize every branch the same way (a leaf branch simply has no
        # nested `states`); `keep` filters to present fields, so leaf branches
        # retain their `workflow`/`max_iterations`/`checks` instead of being
        # flattened to just id+workflow.
        out["branches"] = [_state_summary(b) for b in s["branches"]]
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


STATE_FILE_SUFFIX = ".yaml"
EVIDENCE_SUFFIX = ".evidence.json"
ANSWERS_SUFFIX = ".answers.json"

def evidence_projection(instance_files: dict[str, str]) -> dict:
    evidence, answers = {}, {}
    for name, text in instance_files.items():
        if name.endswith(EVIDENCE_SUFFIX):
            target, suffix = evidence, EVIDENCE_SUFFIX
        elif name.endswith(ANSWERS_SUFFIX):
            target, suffix = answers, ANSWERS_SUFFIX
        else:
            continue
        try:
            target[name[: -len(suffix)]] = json.loads(text)
        except (ValueError, TypeError):
            continue
    return {"evidence": evidence, "answers": answers}

# Files inside an instance dir that are not node-state files. Sidecars such as
# *.evidence.json / *.answers.json are excluded by the STATE_FILE_SUFFIX gate
# (they are not .yaml); these cover the .yaml bookkeeping files.
_IGNORE_FILES = ("_instance.yaml",)
_IGNORE_SUFFIXES = (".__join.yaml",)

def _is_node_file(name: str) -> bool:
    if not name.endswith(STATE_FILE_SUFFIX):
        return False
    if name in _IGNORE_FILES or name.endswith(_IGNORE_SUFFIXES):
        return False
    return True

def _node_status(node: dict) -> str:
    st = node.get("state")
    if st == "done":
        return "done"
    if st == "failed":
        return "failed"
    return "running"

def _phase_and_branch(filename: str):
    stem = filename[:-len(STATE_FILE_SUFFIX)]
    parts = stem.split(".", 1)
    return (parts[0], parts[1] if len(parts) > 1 else None)

def _checks_of(node: dict) -> dict:
    hist = node.get("history") or []
    if hist:
        return hist[-1].get("checks") or {}
    return {}

def _iterations_of(node: dict) -> int:
    hist = node.get("history") or []
    return len(hist) if hist else int(node.get("iteration", 0) or 0)

def _leaf_view(node: dict) -> dict:
    return {"status": _node_status(node), "iterations": _iterations_of(node),
            "checks": _checks_of(node)}

def status_projection(instance_files: dict[str, str]) -> dict:
    inst = yaml.safe_load(instance_files["_instance.yaml"]) or {}
    nodes = {}  # phase_id -> {"branches": {branch: node}} or {"single": node}
    order = []
    for name, text in instance_files.items():
        if not _is_node_file(name):
            continue
        phase, branch = _phase_and_branch(name)
        node = yaml.safe_load(text) or {}
        if phase not in nodes:
            nodes[phase] = {"branches": {}, "single": None}
            order.append(phase)
        if branch is None:
            nodes[phase]["single"] = node
        else:
            nodes[phase]["branches"][branch] = node

    phases = []
    for phase in order:
        entry = nodes[phase]
        if entry["branches"]:
            phases.append({
                "id": phase, "kind": "fanout",
                "status": _fanout_status(entry["branches"].values()),
                "branches": [dict(id=b, **_leaf_view(n))
                             for b, n in sorted(entry["branches"].items())],
            })
        else:
            node = entry["single"]
            gates = node.get("gates") or {}
            if isinstance(gates, dict) and "state" in gates:
                phases.append({"id": phase, "kind": "gate",
                               "status": _node_status(node),
                               "gate": {"open": gates.get("state") == "open"}})
            else:
                phases.append({"id": phase, "kind": "agent", **_leaf_view(node)})

    head_phase = inst.get("phase")
    head = {"phase": head_phase}
    head_entry = next((p for p in phases if p["id"] == head_phase), None)
    if head_entry:
        head["kind"] = head_entry["kind"]
        head["status"] = head_entry["status"]
    return {
        "protocol": inst.get("protocol"),
        "pr": int(str(inst.get("instance", "pr-0")).removeprefix("pr-")),
        "instance": inst.get("instance"),
        "head": head,
        "phases": phases,
    }

def _fanout_status(nodes) -> str:
    statuses = [_node_status(n) for n in nodes]
    if any(s == "failed" for s in statuses):
        return "failed"
    if all(s == "done" for s in statuses):
        return "done"
    return "running"

def instance_stats(instance_files: dict[str, str]) -> dict:
    inst = yaml.safe_load(instance_files["_instance.yaml"]) or {}
    transitions = 0
    iters_by_phase = {}
    for name, text in instance_files.items():
        if not _is_node_file(name):
            continue
        node = yaml.safe_load(text) or {}
        stem = name[:-len(STATE_FILE_SUFFIX)]
        n = _iterations_of(node)
        iters_by_phase[stem] = n
        transitions += len(node.get("history") or [])
    proj = status_projection(instance_files)
    completed = sum(1 for p in proj["phases"] if p["status"] == "done")
    failed = sum(1 for p in proj["phases"] if p["status"] == "failed")
    return {
        "protocol": inst.get("protocol"),
        "pr": int(str(inst.get("instance", "pr-0")).removeprefix("pr-")),
        "instance": inst.get("instance"),
        "state_transitions": transitions,
        "total_iterations": sum(iters_by_phase.values()),
        "iterations_by_phase": iters_by_phase,
        "phases_completed": completed,
        "phases_failed": failed,
        "current_phase": inst.get("phase"),
        "head_sha": inst.get("head_sha"),
    }


def classify_label(phase_label: str) -> str:
    s = (phase_label or "").lower()
    if "✅" in s or "done" in s:
        return "completed"
    if "❌" in s or "failed" in s:
        return "failed"
    if "⛔" in s or "blocked" in s:
        return "blocked"
    return "running"


def classify_instance(instance_yaml_text: str) -> str:
    inst = yaml.safe_load(instance_yaml_text) or {}
    return classify_label(inst.get("phase_label", ""))


def gate_view(instance_files: dict[str, str]):
    for name, text in instance_files.items():
        if not _is_node_file(name):
            continue
        node = yaml.safe_load(text) or {}
        gates = node.get("gates") or {}
        if isinstance(gates, dict) and gates.get("state") == "open":
            questions = [{"id": q.get("id"), "text": q.get("text")}
                         for q in (gates.get("questions") or [])]
            return {
                "phase": name[:-len(STATE_FILE_SUFFIX)],
                "open": True,
                "questions": questions,
                "awaiting": "answer" if questions else "approval",
            }
    return None


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def sum_run_minutes(runs: list[dict]) -> float:
    total = 0.0
    for r in runs:
        start = _parse_iso(r.get("run_started_at"))
        end = _parse_iso(r.get("updated_at"))
        if start and end and end >= start:
            total += (end - start).total_seconds() / 60.0
    return round(total, 1)
