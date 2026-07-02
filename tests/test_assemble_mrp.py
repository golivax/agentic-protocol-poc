"""Unit coverage for the mrp assembler's smm_compliance surfacing: the real
mm-compliance leg evidence is normalized into the pack shape (null when the leg is
absent), and to-evidence surfaces smm_compliance into the engine evidence too."""
import importlib.util
import json

from conftest import PROTOCOLS

ASSEMBLE = PROTOCOLS / "code-review/scripts/mrp/assemble-mrp.py"
TO_EVIDENCE = PROTOCOLS / "code-review/scripts/mrp/to-evidence.py"


def _load(path=ASSEMBLE, name="assemble_mrp"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MM_EVIDENCE = {
    "verdict": "diverges",
    "divergences": [
        {"decision": "ADR-001 master/worker split",
         "detail": "adds a second scheduler in the worker",
         "evidence": "worker/sched.py:42",
         "fix": "route through the master scheduler"},
    ],
    "examined": ["_mm/socratic/docs/specs/adrs/adr-001.adoc", "worker/sched.py"],
}


def test_normalize_compliance_maps_engine_evidence_to_custody_shape():
    mod = _load()
    out = mod._normalize_compliance(MM_EVIDENCE)
    assert "demo" not in out
    assert out["verdict"] == "diverges"
    dv = out["divergences"][0]
    # engine {decision, detail, evidence, fix} -> custody {mm_doc, decision, contradiction, evidence_path, fix}
    assert dv["decision"] == "ADR-001 master/worker split"
    assert dv["contradiction"] == "adds a second scheduler in the worker"
    assert dv["evidence_path"] == "worker/sched.py:42"
    # mm_doc points at the MM decision/doc (the ADR ref), NOT the code path
    assert dv["mm_doc"] == "ADR-001 master/worker split"
    assert dv["fix"] == "route through the master scheduler"
    # examined is trace-only and dropped from the pack shape
    assert "examined" not in out


def test_normalize_compliance_none_for_non_dict():
    mod = _load()
    assert mod._normalize_compliance(None) is None
    assert mod._normalize_compliance("nope") is None


def test_assemble_uses_real_compliance_when_present():
    mod = _load()
    task_ctx = {"pr": 7, "inputs": {"mm-compliance": MM_EVIDENCE}}
    pack = mod.assemble(task_ctx, agent={}, pr={})
    assert "demo" not in pack["smm_compliance"]
    assert pack["smm_compliance"]["verdict"] == "diverges"
    assert pack["smm_compliance"]["divergences"][0]["contradiction"] == "adds a second scheduler in the worker"


def test_assemble_smm_compliance_null_when_leg_absent():
    mod = _load()
    pack = mod.assemble({"pr": 7, "inputs": {}}, agent={}, pr={})
    assert pack["smm_compliance"] is None


def test_to_evidence_surfaces_smm_compliance(tmp_path):
    asm = _load()
    ev = _load(TO_EVIDENCE, "to_evidence")
    pack = asm.assemble({"pr": 7, "inputs": {"mm-compliance": MM_EVIDENCE}}, agent={}, pr={})
    p = tmp_path / "mrp.json"
    p.write_text(json.dumps(pack))
    evidence = ev.evidence_from_pack(str(p))
    assert evidence["smm_compliance"]["verdict"] == "diverges"
    assert evidence["smm_compliance"]["divergences"][0]["mm_doc"] == "ADR-001 master/worker split"


def test_to_evidence_smm_compliance_null_when_leg_absent(tmp_path):
    asm = _load()
    ev = _load(TO_EVIDENCE, "to_evidence")
    pack = asm.assemble({"pr": 7, "inputs": {}}, agent={}, pr={})
    p = tmp_path / "mrp.json"
    p.write_text(json.dumps(pack))
    evidence = ev.evidence_from_pack(str(p))
    assert evidence["smm_compliance"] is None


def test_to_evidence_surfaces_routed_spots(tmp_path):
    asm = _load()
    ev = _load(TO_EVIDENCE, "to_evidence")
    spots = [{"spot_id": "s1", "cohort": "core", "diff_hunk_pointer": "a.py:10", "risk_source": "critique"}]
    pack = asm.assemble({"pr": 7, "inputs": {}}, agent={"routed_spots": spots}, pr={})
    p = tmp_path / "mrp.json"
    p.write_text(json.dumps(pack))
    evidence = ev.evidence_from_pack(str(p))
    assert evidence["routed_spots"] == spots


def test_to_evidence_routed_spots_empty_when_absent(tmp_path):
    asm = _load()
    ev = _load(TO_EVIDENCE, "to_evidence")
    pack = asm.assemble({"pr": 7, "inputs": {}}, agent={}, pr={})
    p = tmp_path / "mrp.json"
    p.write_text(json.dumps(pack))
    evidence = ev.evidence_from_pack(str(p))
    assert evidence["routed_spots"] == []
