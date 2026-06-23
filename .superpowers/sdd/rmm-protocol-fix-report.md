# RMM Protocol Fix Report

## Important 1 — New merge hook test (`test_run_merge_hook`)

Added `test_run_merge_hook` to `tests/test_recover_mental_model.py`.

**Path resolution verified** (single-phase protocol → `is_multiphase=False` → `phase=None`):
- `summary` (flat branch) → `recover-mental-model-stub/pr-1/summary.evidence.json`
- `rationale` (sub-pipeline, last substate=`finalize`) → `recover-mental-model-stub/pr-1/rationale.finalize.evidence.json`

**Test pattern** (mirrors `test_merge.py::test_run_merge_hook`):
1. Sets `os.environ` + `lib.STATE_REMOTE` from `engine_env`.
2. Calls `lib.state_checkout(dir_)` to create the bare-remote-backed working dir.
3. Writes both leg-output files at the resolved paths.
4. Loads the protocol, gets the `combine` merge state via `lib.state_by_id`.
5. Calls `lib.run_merge_hook(dir_, "recover-mental-model-stub", "pr-1", proto_path, merge_state)`.
6. Asserts `conclusion == "success"` and `summary` is non-empty.

**Result:** PASSED — the hook ran, read both inputs, posted (no-op under ENGINE_LOCAL), returned `{"conclusion": "success", "summary": "Recovered mental model: summary + rationale posted."}`.

## Important 2 — Docstring ABI fix

In `.github/agent-factory/protocols/recover-mental-model-stub/publish/append-rationale.py`:

Changed: `ABI: <hook> <inputs-dir> <instance>`
To: `ABI: <hook> <workdir> <instance>`

Updated the Reads section from `<inputs-dir>/inputs/...` to `<workdir>/inputs/...` and added a note explaining that the hook appends `"inputs"` itself (`inputs_dir = os.path.join(sys.argv[1], "inputs")`). Code unchanged.

## Minor 3 — `with open(...)` style in 3 check files

Changed `json.load(open(sys.argv[1]))` → `with open(sys.argv[1]) as f: json.load(f)` in:
- `checks/summary-present.py`
- `checks/questions-present.py`
- `checks/rationale-present.py`

`answers-coverage.py` left unchanged (verbatim fixture copy).

All three retain `100755` exec bits (confirmed via `git ls-files -s`).

## Minor 4 — PR env on join call

In `test_full_pipeline`, added `ej["PR"] = "1"` to the join env dict for parity with the live engine path. Behavior unchanged under `ENGINE_LOCAL`.

## Test results

- `pytest tests/test_recover_mental_model.py -v`: **14 passed** (was 13 + 1 new = 14)
- `pytest tests/ -q`: **401 passed** (was 400 + 1 new = 401)
