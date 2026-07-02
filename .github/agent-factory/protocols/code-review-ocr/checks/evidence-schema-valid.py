#!/usr/bin/env python3
"""code-review-ocr check: generic top-level FORM validator for an OCR node's
flat evidence shape, driven by its declared JSON-schema file — NOT the
code-review rubric shape (files -> verdicts -> category). OCR's agents
(plan/main-review/filter) each emit a simple, differently-shaped evidence
object; rather than one hardcoded validator per node, this check reads the
schema FILE NAME from CHECK_PARAMS (`params.schema`, the check-owning node's
`params` — see protocol.json) and validates the evidence against it.

Deliberately dependency-free: `jsonschema` is a dev-only dependency, absent at
runtime (checks run in trust zone 3 with no package installs). So this does a
minimal-but-real FORM check only — top-level `required` keys present + JSON
type matches `properties[key].type` — never substance. That matches the
engine's own thesis (checks verify form, not correctness).

ABI: <evidence.json> <diff.txt> <changed-files.txt> -> one JSON object
{"check","pass","feedback"} on stdout, ALWAYS exit 0.
"""
import json
import os
import sys

CHECK_NAME = "evidence-schema-valid"

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    # integer/number are handled specially below (bool is a bool, not an int)
}


def emit(ok, feedback):
    print(json.dumps({"check": CHECK_NAME, "pass": ok, "feedback": feedback}))
    sys.exit(0)


def type_ok(value, declared_type):
    if declared_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py_type = _TYPE_MAP.get(declared_type)
    if py_type is None:
        return True  # unknown/unsupported declared type: don't block on it
    if py_type is bool:
        return isinstance(value, bool)
    return isinstance(value, py_type) and (py_type is not dict or True)


def check_required(obj, schema, errs, where):
    """Validate `obj` against `schema`'s top-level `required` + `properties[key].type`."""
    if not isinstance(obj, dict):
        errs.append(f"{where}: expected an object, got {type(obj).__name__}")
        return
    props = schema.get("properties", {}) or {}
    for key in schema.get("required", []) or []:
        if key not in obj:
            errs.append(f"{where}: missing required key {key!r}")
            continue
        prop_schema = props.get(key) or {}
        declared_type = prop_schema.get("type")
        if declared_type and not type_ok(obj[key], declared_type):
            errs.append(
                f"{where}: key {key!r} has wrong type "
                f"(want {declared_type}, got {type(obj[key]).__name__})"
            )
        elif declared_type == "array" and isinstance(obj[key], list):
            item_schema = prop_schema.get("items")
            if isinstance(item_schema, dict) and item_schema.get("required"):
                for i, item in enumerate(obj[key]):
                    check_required(item, item_schema, errs, f"{where}.{key}[{i}]")


def main():
    if len(sys.argv) < 2:
        emit(False, "usage: evidence-schema-valid.py <evidence.json> <diff.txt> <changed-files.txt>")

    ev_path = sys.argv[1]
    try:
        with open(ev_path) as f:
            ev = json.load(f)
    except Exception as e:
        emit(False, f"evidence file is missing or not valid JSON: {e}")

    if not isinstance(ev, dict):
        emit(False, f"evidence must be a JSON object, got {type(ev).__name__}")

    try:
        params = json.loads(os.environ.get("CHECK_PARAMS", "") or "{}")
    except Exception:
        params = {}
    if not isinstance(params, dict):
        params = {}

    schema_name = params.get("schema")
    if not schema_name:
        emit(False, "evidence-schema-valid: no params.schema in CHECK_PARAMS "
                    "(engine must pass params.schema for this check's node)")

    protodir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schema_path = os.path.join(protodir, schema_name)
    try:
        with open(schema_path) as f:
            schema = json.load(f)
    except Exception as e:
        emit(False, f"cannot load schema {schema_name!r} at {schema_path}: {e}")

    if not isinstance(schema, dict):
        emit(False, f"schema {schema_name!r} is not a JSON object")

    errs = []
    check_required(ev, schema, errs, "evidence")
    emit(len(errs) == 0, "; ".join(errs))


if __name__ == "__main__":
    main()
