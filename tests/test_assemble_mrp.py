"""Unit coverage for the mrp assembler's smm_compliance surfacing: the real
mm-compliance leg evidence is normalized into the custody pack shape, and the DEMO
placeholder is used only as a fallback when the leg is absent."""
import importlib.util

from conftest import PROTOCOLS

ASSEMBLE = PROTOCOLS / "code-review/scripts/mrp/assemble-mrp.py"


def _load():
    spec = importlib.util.spec_from_file_location("assemble_mrp", ASSEMBLE)
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
    assert dv["mm_doc"] == "worker/sched.py:42"
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
