# Protocol Visibility REST API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a small read-only REST API that gives client projects visibility into the protocol engine — protocol catalog, per-`<protocol,PR>` status and stats, engine-wide stats, and open gates — reading state from GitHub at request time.

**Architecture:** A standalone `api/` Python package (FastAPI) that **never imports the engine and never writes** to the `agentic-state` branch. `github_client.py` is the only unit that touches the network; `state_reader.py` is the only unit that knows the state-YAML shape (pure, no I/O); `app.py` wires routes, auth, and error mapping; `models.py` holds Pydantic response models; `config.py` holds env-driven settings. Per request: validate bearer token → fetch blobs/runs via `github_client` → interpret via `state_reader` → shape into `models`.

**Tech Stack:** Python 3 (3.14 available), FastAPI, httpx (sync `httpx.Client` for GitHub calls + FastAPI `TestClient`), PyYAML, Pydantic v2. Tests: pytest + respx (httpx mocking). Server: uvicorn.

The approved design spec is `docs/superpowers/specs/2026-06-24-rest-api-design.md`. The deferred-work list is `docs/API-BACKLOG.md`.

## Global Constraints

- **Never import the engine** (`.github/agent-factory/engine/*`) and **never write** to `agentic-state`. State files are a read-only data contract.
- **Network only in `github_client.py`. YAML/JSON shape knowledge only in `state_reader.py`.** `app.py` parses no YAML.
- All endpoints **except `/healthz`** require `Authorization: Bearer <API_BEARER_TOKEN>`.
- Tests must run with **no live GitHub token and no network** — `state_reader` against on-disk fixtures, `github_client` against respx mocks, `app` against a fake client via dependency override.
- New runtime deps live in `api/requirements.txt` — **do not** add web deps to the vendored engine or `tests/requirements-dev.txt` beyond what tests need (`fastapi`, `httpx`, `respx`).
- Follow the repo convention: pytest modules under `tests/`, `test_*.py`.

## The state read contract (verbatim from live `agentic-state`)

`state_reader` interprets these shapes. Examples are real, captured from `origin/agentic-state`.

**`<protocol>/pr-<N>/_instance.yaml`** — head + terminal signal:
```yaml
protocol: code-review
instance: pr-62
head_sha: 657e290beb6266ccd55b8bd95e247491e3468392
phase: approval            # current head phase id
joined: true
phase_label: approval gate # ⚙/pre-flight gate/review/approval gate/✅ done/❌ failed/⛔ blocked
status_comment_id: 4791028639
```

**Node file `<phase>.yaml` or `<phase>.<branch>.yaml`** (agent / fanout-leg):
```yaml
protocol: code-review
instance: pr-62
state: done                # done | failed | <active-node-id> (e.g. review, approval, clarify)
iteration: 1
gates: {}
head_sha: 657e290...
history:
- iteration: 1
  agent_run_id: '28110972887'
  checks: { schema-valid: pass, rubric-coverage: pass, traces-exist-in-diff: pass }
  feedback: ''
```

**Gate node** (`approval.yaml`, `<...>.clarify.yaml`):
```yaml
state: approval
gates:
  state: open              # open | answered | (absent when not a gate)
  history: []              # [{actor, answers:[...]}]
  questions:               # present for /answer-style gates
  - { id: q1, text: "..." }
```

**Sub-pipeline parent** (`deep.yaml`): has `state` + `sub_state`, `history: []`.
**Join marker** (`deep.analyze.__join.yaml`): `{joined: true}` — **ignore** in projections.
**Sidecars to ignore:** `*.evidence.json`, `*.answers.json`, `__join.yaml`, `_instance.yaml` (consumed separately).

**Protocol catalog source** — `protocol.json` on `PROTOCOLS_REF` (default `main`), e.g. `.github/agent-factory/protocols/<name>/protocol.json`:
```json
{ "name": "code-review", "version": "0.1.0",
  "triggers": [ { "on": "issue_comment", "comment_prefix": "/review", "command": "start" } ],
  "states": [ { "id": "preflight", "kind": "agent", "label": "pre-flight gate",
                "max_iterations": 2, "checks": [ {"run":"schema-valid","on_fail":"iterate"} ],
                "next": "review" }, ... ] }
```

**Status classification rule** (used by `/stats` and `/gates`): derive from `_instance.yaml.phase_label` — contains `✅`/`done` → `completed`; `❌`/`failed` → `failed`; `⛔`/`blocked` → `blocked`; otherwise → `running`.

---

### Task 1: Scaffold `api/` package, config, deps, and `/healthz`

**Files:**
- Create: `api/__init__.py` (empty)
- Create: `api/config.py`
- Create: `api/app.py`
- Create: `api/requirements.txt`
- Create: `tests/api/test_health.py`
- Modify: `tests/requirements-dev.txt`
- Modify: `tests/conftest.py` (prepend repo root to `sys.path` — see note)

> **Package-layout note (verified during execution):** Do **NOT** create `tests/api/__init__.py`. The production package is `api/` and the test dir is `tests/api/`; under pytest's default `prepend` import mode, a `tests/api/__init__.py` (with no `tests/__init__.py`) makes pytest put `tests/` on `sys.path` and import `tests/api/` *as* `api`, shadowing the production package (`ModuleNotFoundError: No module named 'api.app'`). Instead, `tests/conftest.py` prepends the repo root to `sys.path` so the real `api/` resolves, and `tests/api/` stays a namespace package — `from tests.api.fixtures_helper import ...` (used in later tasks) works without an `__init__.py`.
>
> **Second-`conftest.py` note (verified during execution):** Do **NOT** put a `conftest.py` in `tests/api/`. The engine tests use a bare `from conftest import FIXTURES, PROTOCOLS, run_check`, which pytest's prepend mode resolves to `tests/conftest.py` by module name. A second file literally named `conftest.py` (in `tests/api/`) is imported under the same top-level module name `conftest`, collides in `sys.modules`, and makes the engine tests import the wrong `FIXTURES` → 31 engine-test failures. Put the API test helpers in a normally-named module, `tests/api/fixtures_helper.py`, instead.

**Interfaces:**
- Produces: `api.config.Settings` (frozen dataclass) with fields `api_bearer_token: str`, `github_token: str`, `github_repo: str`, `state_branch: str`, `protocols_ref: str`, `engine_workflows: list[str]`, `github_api_url: str`; classmethod `Settings.from_env(env: Mapping[str,str]) -> Settings` (raises `ValueError` listing every missing required var). `api.app.create_app(settings: Settings, client=None) -> FastAPI`. Route `GET /healthz` → `{"status": "ok"}` (liveness only; readiness ping added in Task 8).

- [ ] **Step 1: Add test+runtime deps**

Append to `tests/requirements-dev.txt`:
```
fastapi
httpx
respx
```
Create `api/requirements.txt`:
```
fastapi
httpx
PyYAML
uvicorn
```
Run: `python3 -m pip install -r tests/requirements-dev.txt`
Expected: installs fastapi, httpx, respx (+ existing pytest, PyYAML).

- [ ] **Step 2: Write the failing test**

`tests/api/test_health.py`:
```python
from fastapi.testclient import TestClient
from api.app import create_app
from api.config import Settings

SETTINGS = Settings(
    api_bearer_token="t0ken", github_token="gh", github_repo="o/r",
    state_branch="agentic-state", protocols_ref="main",
    engine_workflows=[], github_api_url="https://api.github.com",
)

def test_healthz_is_open_and_ok():
    client = TestClient(create_app(SETTINGS))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

def test_from_env_reports_all_missing_required():
    import pytest
    with pytest.raises(ValueError) as e:
        Settings.from_env({})
    msg = str(e.value)
    assert "API_BEARER_TOKEN" in msg and "GITHUB_TOKEN" in msg and "GITHUB_REPO" in msg
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/api/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.app'`.

- [ ] **Step 4: Implement config and app**

`api/config.py`:
```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping

@dataclass(frozen=True)
class Settings:
    api_bearer_token: str
    github_token: str
    github_repo: str
    state_branch: str
    protocols_ref: str
    engine_workflows: list[str]
    github_api_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        required = ("API_BEARER_TOKEN", "GITHUB_TOKEN", "GITHUB_REPO")
        missing = [k for k in required if not env.get(k)]
        if missing:
            raise ValueError(f"missing required env vars: {', '.join(missing)}")
        raw = env.get("ENGINE_WORKFLOWS", "").strip()
        workflows = [w.strip() for w in raw.split(",") if w.strip()]
        return cls(
            api_bearer_token=env["API_BEARER_TOKEN"],
            github_token=env["GITHUB_TOKEN"],
            github_repo=env["GITHUB_REPO"],
            state_branch=env.get("STATE_BRANCH", "agentic-state"),
            protocols_ref=env.get("PROTOCOLS_REF", "main"),
            engine_workflows=workflows,
            github_api_url=env.get("GITHUB_API_URL", "https://api.github.com"),
        )
```

`api/app.py`:
```python
from __future__ import annotations
from fastapi import FastAPI
from api.config import Settings

def create_app(settings: Settings, client=None) -> FastAPI:
    app = FastAPI(title="Protocol Visibility API")
    app.state.settings = settings
    app.state.client = client  # GitHubClient injected in Task 8; None for now

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/api/test_health.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add api/ tests/api/ tests/requirements-dev.txt
git commit -m "feat(api): scaffold FastAPI package, config, and /healthz"
```

---

### Task 2: Capture real state + protocol fixtures

**Files:**
- Create: `tests/api/fixtures/state/code-review/pr-62/{_instance,preflight,approval,review.grumpy,review.security}.yaml`
- Create: `tests/api/fixtures/state/deep-review-stub/pr-88/{_instance,deep,deep.triage,deep.analyze.__join,deep.analyze.sec,deep.analyze.perf,deep.report,quick}.yaml`
- Create: `tests/api/fixtures/state/recover-mental-model-stub/pr-82/{_instance,rationale.clarify}.yaml`
- Create: `tests/api/fixtures/protocols/code-review.protocol.json`, `tests/api/fixtures/protocols/deep-review-stub.protocol.json`
- Create: `tests/api/fixtures_helper.py` (a plain module — **not** named `conftest.py`; see the second-`conftest.py` note above)
- Create: `tests/api/test_fixtures_sane.py`

**Interfaces:**
- Produces: helper `load_instance_files(protocol, pr) -> dict[str, str]` returning `{filename: text}` for every file under that instance dir, and module-level `FIXTURES: Path`. Both in `tests/api/fixtures_helper.py`, imported as `from tests.api.fixtures_helper import load_instance_files, FIXTURES`.

- [ ] **Step 1: Export real state fixtures from `origin/agentic-state`**

These are the real, current files — export them verbatim:
```bash
mkdir -p tests/api/fixtures/state/code-review/pr-62 \
         tests/api/fixtures/state/deep-review-stub/pr-88 \
         tests/api/fixtures/state/recover-mental-model-stub/pr-82 \
         tests/api/fixtures/protocols
git fetch -q origin agentic-state
for f in _instance preflight approval review.grumpy review.security; do
  git show "origin/agentic-state:code-review/pr-62/$f.yaml" \
    > "tests/api/fixtures/state/code-review/pr-62/$f.yaml"; done
for f in _instance deep deep.triage deep.analyze.__join deep.analyze.sec deep.analyze.perf deep.report quick; do
  git show "origin/agentic-state:deep-review-stub/pr-88/$f.yaml" \
    > "tests/api/fixtures/state/deep-review-stub/pr-88/$f.yaml"; done
for f in _instance rationale.clarify; do
  git show "origin/agentic-state:recover-mental-model-stub/pr-82/$f.yaml" \
    > "tests/api/fixtures/state/recover-mental-model-stub/pr-82/$f.yaml"; done
cp .github/agent-factory/protocols/code-review/protocol.json \
   tests/api/fixtures/protocols/code-review.protocol.json
cp .github/agent-factory/protocols/deep-review-stub/protocol.json \
   tests/api/fixtures/protocols/deep-review-stub.protocol.json
```
Expected: 15 fixture files created. (If a path is missing, list the instance with `git ls-tree -r --name-only origin/agentic-state | grep <protocol>/pr-<N>/` and adjust.)

- [ ] **Step 2: Write fixture helpers + sanity test**

`tests/api/fixtures_helper.py` (plain module — must NOT be named `conftest.py`):
```python
import pathlib

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

def load_instance_files(protocol, pr):
    d = FIXTURES / "state" / protocol / f"pr-{pr}"
    return {p.name: p.read_text() for p in d.iterdir() if p.is_file()}
```

`tests/api/test_fixtures_sane.py`:
```python
import json
import yaml
from tests.api.fixtures_helper import load_instance_files, FIXTURES

def test_instance_fixtures_parse_as_yaml():
    files = load_instance_files("code-review", 62)
    assert "_instance.yaml" in files
    inst = yaml.safe_load(files["_instance.yaml"])
    assert inst["protocol"] == "code-review" and inst["phase"] == "approval"

def test_protocol_fixture_parses_as_json():
    txt = (FIXTURES / "protocols" / "code-review.protocol.json").read_text()
    proto = json.loads(txt)
    assert proto["name"] == "code-review"
    assert any(s["id"] == "preflight" for s in proto["states"])
```

- [ ] **Step 3: Run the sanity test**

Run: `pytest tests/api/test_fixtures_sane.py -v`
Expected: PASS (2 passed). If FAIL, the export in Step 1 was incomplete — re-run it.

- [ ] **Step 4: Commit**

```bash
git add tests/api/fixtures tests/api/fixtures_helper.py tests/api/test_fixtures_sane.py
git commit -m "test(api): capture real agentic-state + protocol fixtures"
```

---

### Task 3: `state_reader` — protocol catalog

**Files:**
- Create: `api/state_reader.py`
- Create: `tests/api/test_state_reader_catalog.py`

**Interfaces:**
- Produces:
  - `list_protocols(protocol_jsons: list[str]) -> list[dict]` — each `{"name", "version", "triggers": [{"on","comment_prefix","command"}]}`. Input is a list of raw `protocol.json` text strings.
  - `protocol_detail(protocol_json: str) -> dict` — `{"name","version","triggers":[...],"states":[{"id","kind","label","max_iterations","checks":[...],"branches":[...],"next"}]}`. Missing optional fields are omitted, not faked.

- [ ] **Step 1: Write the failing test**

`tests/api/test_state_reader_catalog.py`:
```python
from pathlib import Path
from api import state_reader

FX = Path(__file__).parent / "fixtures" / "protocols"

def test_list_protocols_summarizes_name_version_triggers():
    cr = (FX / "code-review.protocol.json").read_text()
    deep = (FX / "deep-review-stub.protocol.json").read_text()
    out = state_reader.list_protocols([cr, deep])
    names = {p["name"] for p in out}
    assert names == {"code-review", "deep-review-stub"}
    cr_entry = next(p for p in out if p["name"] == "code-review")
    assert cr_entry["version"] == "0.1.0"
    assert any(t["comment_prefix"] == "/review" for t in cr_entry["triggers"])

def test_protocol_detail_exposes_state_graph():
    cr = (FX / "code-review.protocol.json").read_text()
    out = state_reader.protocol_detail(cr)
    assert out["name"] == "code-review"
    preflight = next(s for s in out["states"] if s["id"] == "preflight")
    assert preflight["kind"] == "agent"
    assert preflight["max_iterations"] == 2
    assert any(c["run"] == "spec-present" for c in preflight["checks"])
    review = next(s for s in out["states"] if s["id"] == "review")
    assert {b["id"] for b in review["branches"]} == {"grumpy", "security"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_state_reader_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.state_reader'`.

- [ ] **Step 3: Implement catalog functions**

`api/state_reader.py`:
```python
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
        out.append({
            "name": proto["name"],
            "version": proto.get("version", ""),
            "triggers": _trigger_summary(proto),
        })
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
    return {
        "name": proto["name"],
        "version": proto.get("version", ""),
        "max_depth": proto.get("max_depth"),
        "triggers": _trigger_summary(proto),
        "states": [_state_summary(s) for s in proto.get("states", [])],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_state_reader_catalog.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add api/state_reader.py tests/api/test_state_reader_catalog.py
git commit -m "feat(api): state_reader protocol catalog (list + detail)"
```

---

### Task 4: `state_reader` — instance status projection

**Files:**
- Modify: `api/state_reader.py`
- Create: `tests/api/test_state_reader_status.py`

**Interfaces:**
- Consumes: `load_instance_files(protocol, pr)` from `tests/api/fixtures_helper.py`.
- Produces:
  - `status_projection(instance_files: dict[str, str]) -> dict` — `{"protocol","pr","instance","head":{"phase","kind","status"},"phases":[...]}`. Each phase: `{"id","kind","status","iterations","checks"}`; fanout phases instead carry `"branches":[{"id","status","iterations","checks"}]`; gate phases carry `"gate":{"open":bool}`.
  - Helper `_node_status(node: dict) -> str` → `"done"|"failed"|"running"`.
  - `STATE_FILE_SUFFIX = ".yaml"`; constant set `_IGNORE_FILES` for `_instance.yaml`/`__join`/sidecars.

**Notes on the contract:** phase id is the node filename without `.yaml`; a leg file is `<phase>.<branch>.yaml` (split on first `.`). `kind` is inferred: a file whose node has `gates` with a `state` key → `gate`; multiple files sharing a `<phase>.` prefix → that phase is `fanout`; else `agent`. `iterations = len(history)` (fallback to `iteration` field). `checks` = last history entry's `checks` map (empty dict if no history). Head phase comes from `_instance.yaml.phase`; head status from `phase_label` via `classify_label` (added in Task 6 — for now compute head status from the head node's `_node_status`, and gate-open if its `gates.state == "open"`).

- [ ] **Step 1: Write the failing test**

`tests/api/test_state_reader_status.py`:
```python
from api import state_reader
from tests.api.fixtures_helper import load_instance_files

def test_status_projection_code_review_pr62():
    out = state_reader.status_projection(load_instance_files("code-review", 62))
    assert out["protocol"] == "code-review"
    assert out["pr"] == 62
    assert out["head"]["phase"] == "approval"
    phases = {p["id"]: p for p in out["phases"]}
    assert phases["preflight"]["kind"] == "agent"
    assert phases["preflight"]["status"] == "done"
    assert phases["preflight"]["checks"]["spec-present"] == "pass"
    assert phases["review"]["kind"] == "fanout"
    legs = {b["id"]: b for b in phases["review"]["branches"]}
    assert legs["grumpy"]["status"] == "done"
    assert legs["security"]["status"] == "done"
    assert phases["approval"]["kind"] == "gate"
    assert phases["approval"]["gate"]["open"] is True

def test_status_projection_ignores_sidecars_and_join_markers():
    out = state_reader.status_projection(load_instance_files("deep-review-stub", 88))
    ids = {p["id"] for p in out["phases"]}
    assert "deep.analyze.__join" not in ids
    assert all(not i.endswith(".json") for i in ids)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_state_reader_status.py -v`
Expected: FAIL — `AttributeError: module 'api.state_reader' has no attribute 'status_projection'`.

- [ ] **Step 3: Implement projection**

Append to `api/state_reader.py`:
```python
import re
import yaml

_IGNORE_SUFFIXES = (".evidence.json", ".answers.json")

def _is_node_file(name: str) -> bool:
    if not name.endswith(".yaml"):
        return False
    if name == "_instance.yaml" or name.endswith(".__join.yaml"):
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
    stem = filename[:-len(".yaml")]
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_state_reader_status.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add api/state_reader.py tests/api/test_state_reader_status.py
git commit -m "feat(api): state_reader instance status projection"
```

---

### Task 5: `state_reader` — per-instance stats

**Files:**
- Modify: `api/state_reader.py`
- Create: `tests/api/test_state_reader_stats.py`

**Interfaces:**
- Produces: `instance_stats(instance_files: dict[str, str]) -> dict` — `{"protocol","pr","instance","state_transitions","total_iterations","iterations_by_phase":{phase_or_phase.branch: n},"phases_completed","phases_failed","current_phase","head_sha"}`. `state_transitions` = total `history[]` entries across all node files. `iterations_by_phase` keyed by node stem (`review.grumpy`, `preflight`). `phases_completed`/`phases_failed` count top-level phases by `status_projection` status.

- [ ] **Step 1: Write the failing test**

`tests/api/test_state_reader_stats.py`:
```python
from api import state_reader
from tests.api.fixtures_helper import load_instance_files

def test_instance_stats_code_review_pr62():
    out = state_reader.instance_stats(load_instance_files("code-review", 62))
    assert out["protocol"] == "code-review"
    assert out["pr"] == 62
    assert out["current_phase"] == "approval"
    assert out["head_sha"] == "657e290beb6266ccd55b8bd95e247491e3468392"
    # preflight(1) + review.grumpy(1) + review.security(1) = 3 history entries
    assert out["state_transitions"] == 3
    assert out["iterations_by_phase"]["preflight"] == 1
    assert out["iterations_by_phase"]["review.grumpy"] == 1
    assert out["phases_completed"] >= 2   # preflight + review done
    assert out["phases_failed"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_state_reader_stats.py -v`
Expected: FAIL — no attribute `instance_stats`.

- [ ] **Step 3: Implement stats**

Append to `api/state_reader.py`:
```python
def instance_stats(instance_files: dict[str, str]) -> dict:
    inst = yaml.safe_load(instance_files["_instance.yaml"]) or {}
    transitions = 0
    iters_by_phase = {}
    for name, text in instance_files.items():
        if not _is_node_file(name):
            continue
        node = yaml.safe_load(text) or {}
        stem = name[:-len(".yaml")]
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_state_reader_stats.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/state_reader.py tests/api/test_state_reader_stats.py
git commit -m "feat(api): state_reader per-instance stats"
```

---

### Task 6: `state_reader` — instance classification, gates, action-minutes

**Files:**
- Modify: `api/state_reader.py`
- Create: `tests/api/test_state_reader_aggregate.py`

**Interfaces:**
- Produces:
  - `classify_label(phase_label: str) -> str` → `"completed"|"failed"|"blocked"|"running"`.
  - `classify_instance(instance_yaml_text: str) -> str` (reads `_instance.yaml` text → calls `classify_label`).
  - `gate_view(instance_files: dict[str,str]) -> dict | None` — first open gate as `{"phase","open":True,"questions":[{"id","text"}],"awaiting":"answer"|"approval"}` (questions present → `answer`, else `approval`); `None` if no open gate.
  - `sum_run_minutes(runs: list[dict]) -> float` — `runs` are GitHub run objects with `run_started_at` + `updated_at` ISO-8601; returns total wall-clock minutes (rounded to 1 dp). Unparseable/missing timestamps contribute 0.

- [ ] **Step 1: Write the failing test**

`tests/api/test_state_reader_aggregate.py`:
```python
from api import state_reader
from tests.api.fixtures_helper import load_instance_files

def test_classify_label_variants():
    assert state_reader.classify_label("✅ done") == "completed"
    assert state_reader.classify_label("❌ failed") == "failed"
    assert state_reader.classify_label("⛔ blocked") == "blocked"
    assert state_reader.classify_label("approval gate") == "running"

def test_gate_view_open_answer_gate():
    gv = state_reader.gate_view(load_instance_files("recover-mental-model-stub", 82))
    # pr-82's clarify gate is answered (closed) -> no OPEN gate
    assert gv is None

def test_gate_view_open_approval_gate():
    gv = state_reader.gate_view(load_instance_files("code-review", 62))
    assert gv is not None
    assert gv["phase"] == "approval"
    assert gv["open"] is True
    assert gv["awaiting"] == "approval"

def test_sum_run_minutes_wallclock():
    runs = [
        {"run_started_at": "2026-06-24T10:00:00Z", "updated_at": "2026-06-24T10:03:00Z"},
        {"run_started_at": "2026-06-24T11:00:00Z", "updated_at": "2026-06-24T11:01:30Z"},
        {"run_started_at": None, "updated_at": "2026-06-24T11:01:30Z"},  # contributes 0
    ]
    assert state_reader.sum_run_minutes(runs) == 4.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_state_reader_aggregate.py -v`
Expected: FAIL — no attribute `classify_label`.

- [ ] **Step 3: Implement**

Append to `api/state_reader.py`:
```python
from datetime import datetime

def classify_label(phase_label: str) -> str:
    s = (phase_label or "").lower()
    if "✅" in phase_label or "done" in s:
        return "completed"
    if "❌" in phase_label or "failed" in s:
        return "failed"
    if "⛔" in phase_label or "blocked" in s:
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
                "phase": name[:-len(".yaml")],
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_state_reader_aggregate.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add api/state_reader.py tests/api/test_state_reader_aggregate.py
git commit -m "feat(api): state_reader classification, gates, action-minutes"
```

---

### Task 7: `github_client` — typed GitHub REST client

**Files:**
- Create: `api/github_client.py`
- Create: `tests/api/test_github_client.py`

**Interfaces:**
- Produces:
  - Exceptions: `GitHubError` (base), `NotFound(GitHubError)`, `RateLimited(GitHubError)` (has `.retry_after: str | None`), `UpstreamError(GitHubError)`.
  - `class GitHubClient(settings: Settings, http: httpx.Client | None = None)` with methods:
    - `list_tree(prefix: str) -> list[str]` — recursive tree of `state_branch`, returns blob paths under `prefix` (e.g. `"code-review/"`).
    - `get_text(path: str, ref: str) -> str` — file content at `path`@`ref` (uses the contents API `Accept: raw`). Raises `NotFound` on 404.
    - `get_json(path: str, ref: str) -> str` — alias of `get_text` (kept for call-site clarity).
    - `list_workflow_runs(workflows: list[str]) -> list[dict]` — runs across given workflow filenames (or all repo runs if empty); each `{"run_started_at","updated_at","name"}`. Paginates up to 5 pages.
  - Maps HTTP 404→`NotFound`, 403/429 with rate-limit headers→`RateLimited`, other ≥400→`UpstreamError`.

- [ ] **Step 1: Write the failing test**

`tests/api/test_github_client.py`:
```python
import httpx, pytest, respx
from api.config import Settings
from api.github_client import GitHubClient, NotFound, RateLimited

S = Settings(api_bearer_token="t", github_token="gh", github_repo="o/r",
             state_branch="agentic-state", protocols_ref="main",
             engine_workflows=[], github_api_url="https://api.github.com")

@respx.mock
def test_list_tree_filters_by_prefix():
    respx.get("https://api.github.com/repos/o/r/git/trees/agentic-state").mock(
        return_value=httpx.Response(200, json={"tree": [
            {"path": "code-review/pr-62/_instance.yaml", "type": "blob"},
            {"path": "deep-review-stub/pr-88/quick.yaml", "type": "blob"},
            {"path": "code-review/pr-62", "type": "tree"},
        ]}))
    c = GitHubClient(S)
    out = c.list_tree("code-review/")
    assert out == ["code-review/pr-62/_instance.yaml"]

@respx.mock
def test_get_text_404_raises_notfound():
    respx.get("https://api.github.com/repos/o/r/contents/missing.yaml").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"}))
    c = GitHubClient(S)
    with pytest.raises(NotFound):
        c.get_text("missing.yaml", "agentic-state")

@respx.mock
def test_rate_limit_raises_with_retry_after():
    respx.get("https://api.github.com/repos/o/r/contents/x.yaml").mock(
        return_value=httpx.Response(403, headers={"Retry-After": "60",
            "X-RateLimit-Remaining": "0"}, json={"message": "rate limited"}))
    c = GitHubClient(S)
    with pytest.raises(RateLimited) as e:
        c.get_text("x.yaml", "agentic-state")
    assert e.value.retry_after == "60"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_github_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.github_client'`.

- [ ] **Step 3: Implement the client**

`api/github_client.py`:
```python
from __future__ import annotations
import httpx
from api.config import Settings

class GitHubError(Exception): ...
class NotFound(GitHubError): ...
class UpstreamError(GitHubError): ...
class RateLimited(GitHubError):
    def __init__(self, msg, retry_after=None):
        super().__init__(msg)
        self.retry_after = retry_after

class GitHubClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.s = settings
        self.http = http or httpx.Client(timeout=15.0)

    def _headers(self, accept="application/vnd.github+json"):
        return {"Authorization": f"Bearer {self.s.github_token}",
                "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}

    def _base(self):
        return f"{self.s.github_api_url}/repos/{self.s.github_repo}"

    def _check(self, r: httpx.Response):
        if r.status_code == 404:
            raise NotFound(r.url)
        if r.status_code in (403, 429) and r.headers.get("X-RateLimit-Remaining") == "0":
            raise RateLimited("github rate limit", r.headers.get("Retry-After"))
        if r.status_code >= 400:
            raise UpstreamError(f"{r.status_code} {r.url}")
        return r

    def list_tree(self, prefix: str) -> list[str]:
        url = f"{self._base()}/git/trees/{self.s.state_branch}"
        r = self._check(self.http.get(url, headers=self._headers(),
                                      params={"recursive": "1"}))
        tree = r.json().get("tree", [])
        return [e["path"] for e in tree
                if e.get("type") == "blob" and e["path"].startswith(prefix)]

    def get_text(self, path: str, ref: str) -> str:
        url = f"{self._base()}/contents/{path}"
        r = self._check(self.http.get(url, headers=self._headers("application/vnd.github.raw+json"),
                                      params={"ref": ref}))
        return r.text

    get_json = get_text

    def list_workflow_runs(self, workflows: list[str]) -> list[dict]:
        runs, sources = [], (workflows or [None])
        for wf in sources:
            base = (f"{self._base()}/actions/workflows/{wf}/runs" if wf
                    else f"{self._base()}/actions/runs")
            for page in range(1, 6):
                r = self._check(self.http.get(base, headers=self._headers(),
                                params={"per_page": 100, "page": page}))
                batch = r.json().get("workflow_runs", [])
                runs.extend({"run_started_at": x.get("run_started_at"),
                             "updated_at": x.get("updated_at"),
                             "name": x.get("name")} for x in batch)
                if len(batch) < 100:
                    break
        return runs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_github_client.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add api/github_client.py tests/api/test_github_client.py
git commit -m "feat(api): GitHub REST client with typed errors"
```

---

### Task 8: `app` — models, auth, routes, error mapping

**Files:**
- Create: `api/models.py`
- Modify: `api/app.py`
- Create: `tests/api/test_app_routes.py`

**Interfaces:**
- Consumes: `state_reader.*`, `github_client.GitHubClient` + its exceptions, `Settings`.
- Produces: a fully-wired app. Routes:
  - `GET /protocols`, `GET /protocols/{protocol}`
  - `GET /protocols/{protocol}/instances`
  - `GET /protocols/{protocol}/instances/{pr}/status`
  - `GET /protocols/{protocol}/instances/{pr}/stats`
  - `GET /stats`
  - `GET /gates?status=open&protocol=<name>`
  - `GET /healthz` (now pings GitHub; degrades to `{"status":"degraded"}` if unreachable, still 200)
- A `Protocols`-style port the app calls on `app.state.client`. The fake client in tests implements: `list_tree`, `get_text`, `list_workflow_runs`, and a helper to enumerate protocol json + instance dirs. Error mapping: `NotFound→404`, `RateLimited→429` (+`Retry-After`), `UpstreamError→502`, missing/bad bearer→`401`.

**Design note — how the app finds protocols + instances via the client:**
- Protocol names: the app reads `protocol.json` for each known protocol. Discover names by `list_tree` over the protocols dir on `protocols_ref`? No — the contents API is simpler: call `client.get_text(".github/agent-factory/protocols/{name}/protocol.json", protocols_ref)`. To enumerate names, add `client.list_dir(path, ref) -> list[str]` (directory entry names) in this task (small addition to `github_client.py`, tested via respx in the same file or inline). Instances for a protocol: `client.list_tree(f"{protocol}/")` on the state branch → parse `pr-<N>` path segments → unique sorted PRs.

- [ ] **Step 1: Add `list_dir` to the client (failing test first)**

Append to `tests/api/test_github_client.py`:
```python
@respx.mock
def test_list_dir_returns_entry_names():
    respx.get("https://api.github.com/repos/o/r/contents/.github/agent-factory/protocols").mock(
        return_value=httpx.Response(200, json=[
            {"name": "code-review", "type": "dir"},
            {"name": "deep-review-stub", "type": "dir"},
            {"name": "README.md", "type": "file"},
        ]))
    c = GitHubClient(S)
    assert c.list_dir(".github/agent-factory/protocols", "main") == \
        ["code-review", "deep-review-stub"]
```
Run: `pytest tests/api/test_github_client.py::test_list_dir_returns_entry_names -v` → FAIL (no `list_dir`).
Add to `GitHubClient`:
```python
    def list_dir(self, path: str, ref: str) -> list[str]:
        url = f"{self._base()}/contents/{path}"
        r = self._check(self.http.get(url, headers=self._headers(), params={"ref": ref}))
        return sorted(e["name"] for e in r.json() if e.get("type") == "dir")
```
Run again → PASS. Commit:
```bash
git add api/github_client.py tests/api/test_github_client.py
git commit -m "feat(api): client list_dir for protocol enumeration"
```

- [ ] **Step 2: Write the failing route tests**

`tests/api/test_app_routes.py`:
```python
from pathlib import Path
from fastapi.testclient import TestClient
from api.app import create_app
from api.config import Settings
from api.github_client import NotFound

FX = Path(__file__).parent / "fixtures"
S = Settings(api_bearer_token="t0ken", github_token="gh", github_repo="o/r",
             state_branch="agentic-state", protocols_ref="main",
             engine_workflows=[], github_api_url="https://api.github.com")

class FakeClient:
    """In-memory stand-in for GitHubClient, backed by fixture files."""
    PROTO_DIR = ".github/agent-factory/protocols"
    def __init__(self):
        self.runs = [{"run_started_at": "2026-06-24T10:00:00Z",
                      "updated_at": "2026-06-24T10:02:00Z", "name": "engine"}]
    def list_dir(self, path, ref):
        return ["code-review", "deep-review-stub"]
    def get_text(self, path, ref):
        if path.endswith("protocol.json"):
            name = path.split("/")[-2]
            f = FX / "protocols" / f"{name}.protocol.json"
            if not f.exists():
                raise NotFound(path)
            return f.read_text()
        # state file: "<protocol>/pr-<N>/<file>"
        f = FX / "state" / path
        if not f.exists():
            raise NotFound(path)
        return f.read_text()
    def list_tree(self, prefix):
        root = FX / "state" / prefix.rstrip("/")
        if not root.exists():
            return []
        return [str(Path(prefix.rstrip("/")) / p.relative_to(root))
                for p in root.rglob("*") if p.is_file()]
    def list_workflow_runs(self, workflows):
        return self.runs

def app():
    return TestClient(create_app(S, client=FakeClient()))

AUTH = {"Authorization": "Bearer t0ken"}

def test_protocols_requires_auth():
    assert app().get("/protocols").status_code == 401

def test_list_protocols():
    r = app().get("/protocols", headers=AUTH)
    assert r.status_code == 200
    assert {p["name"] for p in r.json()["protocols"]} == {"code-review", "deep-review-stub"}

def test_protocol_detail_404_for_unknown():
    assert app().get("/protocols/nope", headers=AUTH).status_code == 404

def test_instance_status():
    r = app().get("/protocols/code-review/instances/62/status", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["head"]["phase"] == "approval"

def test_instance_list():
    r = app().get("/protocols/code-review/instances", headers=AUTH)
    assert r.status_code == 200
    assert 62 in r.json()["instances"]

def test_global_stats_has_minutes():
    r = app().get("/stats", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["action_minutes_approx"] == 2.0
    assert "code-review" in body["protocols"]

def test_gates_open_lists_pr62_approval():
    r = app().get("/gates", params={"status": "open", "protocol": "code-review"}, headers=AUTH)
    assert r.status_code == 200
    gates = r.json()["gates"]
    assert any(g["pr"] == 62 and g["awaiting"] == "approval" for g in gates)

def test_healthz_open_no_auth():
    assert app().get("/healthz").status_code == 200
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/api/test_app_routes.py -v`
Expected: FAIL (routes/models not implemented; most return 404/500).

- [ ] **Step 4: Implement models**

`api/models.py`:
```python
from __future__ import annotations
from pydantic import BaseModel
from typing import Any

# Responses are shaped as plain dicts from state_reader; these models document
# and validate the top-level envelopes returned to clients.
class ProtocolList(BaseModel):
    protocols: list[dict[str, Any]]

class InstanceList(BaseModel):
    protocol: str
    instances: list[int]

class GatesResponse(BaseModel):
    gates: list[dict[str, Any]]

class GlobalStats(BaseModel):
    protocols: list[str]
    instances_total: int
    instances_running: int
    instances_completed: int
    instances_failed: int
    instances_blocked: int
    by_protocol: dict[str, dict[str, int]]
    action_minutes_approx: float
    action_minutes_note: str
```

- [ ] **Step 5: Implement routes + auth + error mapping**

Replace `api/app.py` with:
```python
from __future__ import annotations
import re
from fastapi import FastAPI, Depends, Header, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from api.config import Settings
from api import state_reader
from api.github_client import NotFound, RateLimited, UpstreamError

PROTO_DIR = ".github/agent-factory/protocols"
MINUTES_NOTE = ("approximate: sum of wall-clock (updated_at − run_started_at) "
                "over engine workflow runs")

def create_app(settings: Settings, client=None) -> FastAPI:
    app = FastAPI(title="Protocol Visibility API")
    app.state.settings = settings
    app.state.client = client

    def cl(request: Request):
        return request.app.state.client

    def require_auth(authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ").strip()
        if not token or token != settings.api_bearer_token:
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def _proto_json(client, name):
        try:
            return client.get_text(f"{PROTO_DIR}/{name}/protocol.json", settings.protocols_ref)
        except NotFound:
            raise HTTPException(status_code=404, detail=f"unknown protocol: {name}")

    def _instance_files(client, protocol, pr):
        paths = client.list_tree(f"{protocol}/pr-{pr}/")
        files = {p.split("/")[-1]: client.get_text(p, settings.state_branch) for p in paths}
        if "_instance.yaml" not in files:
            raise HTTPException(status_code=404, detail=f"no instance {protocol} pr-{pr}")
        return files

    def _pr_numbers(client, protocol):
        prs = set()
        for p in client.list_tree(f"{protocol}/"):
            m = re.search(rf"^{re.escape(protocol)}/pr-(\d+)/", p)
            if m:
                prs.add(int(m.group(1)))
        return sorted(prs)

    @app.exception_handler(RateLimited)
    def _rl(request, exc: RateLimited):
        h = {"Retry-After": exc.retry_after} if exc.retry_after else {}
        return JSONResponse(status_code=429, content={"error": "github rate limit"}, headers=h)

    @app.exception_handler(UpstreamError)
    def _up(request, exc: UpstreamError):
        return JSONResponse(status_code=502, content={"error": "github upstream error"})

    @app.get("/healthz")
    def healthz():
        client = app.state.client
        try:
            if client is not None:
                client.list_dir(PROTO_DIR, settings.protocols_ref)
            return {"status": "ok"}
        except Exception:
            return {"status": "degraded"}

    @app.get("/protocols", dependencies=[Depends(require_auth)])
    def list_protocols(request: Request):
        client = cl(request)
        names = client.list_dir(PROTO_DIR, settings.protocols_ref)
        jsons = []
        for n in names:
            try:
                jsons.append(client.get_text(f"{PROTO_DIR}/{n}/protocol.json", settings.protocols_ref))
            except NotFound:
                continue
        return {"protocols": state_reader.list_protocols(jsons)}

    @app.get("/protocols/{protocol}", dependencies=[Depends(require_auth)])
    def protocol_detail(protocol: str, request: Request):
        return state_reader.protocol_detail(_proto_json(cl(request), protocol))

    @app.get("/protocols/{protocol}/instances", dependencies=[Depends(require_auth)])
    def list_instances(protocol: str, request: Request):
        _proto_json(cl(request), protocol)  # 404 if unknown protocol
        return {"protocol": protocol, "instances": _pr_numbers(cl(request), protocol)}

    @app.get("/protocols/{protocol}/instances/{pr}/status", dependencies=[Depends(require_auth)])
    def instance_status(protocol: str, pr: int, request: Request):
        return state_reader.status_projection(_instance_files(cl(request), protocol, pr))

    @app.get("/protocols/{protocol}/instances/{pr}/stats", dependencies=[Depends(require_auth)])
    def instance_stats(protocol: str, pr: int, request: Request):
        return state_reader.instance_stats(_instance_files(cl(request), protocol, pr))

    @app.get("/stats", dependencies=[Depends(require_auth)])
    def global_stats(request: Request):
        client = cl(request)
        names = client.list_dir(PROTO_DIR, settings.protocols_ref)
        counts = {"running": 0, "completed": 0, "failed": 0, "blocked": 0}
        by_protocol, total = {}, 0
        for name in names:
            prs = _pr_numbers(client, name)
            by_protocol[name] = {"total": len(prs), "running": 0}
            for pr in prs:
                total += 1
                inst_txt = client.get_text(f"{name}/pr-{pr}/_instance.yaml", settings.state_branch)
                klass = state_reader.classify_instance(inst_txt)
                counts[klass] = counts.get(klass, 0) + 1
                if klass == "running":
                    by_protocol[name]["running"] += 1
        runs = client.list_workflow_runs(settings.engine_workflows)
        return {
            "protocols": names,
            "instances_total": total,
            "instances_running": counts["running"],
            "instances_completed": counts["completed"],
            "instances_failed": counts["failed"],
            "instances_blocked": counts["blocked"],
            "by_protocol": by_protocol,
            "action_minutes_approx": state_reader.sum_run_minutes(runs),
            "action_minutes_note": MINUTES_NOTE,
        }

    @app.get("/gates", dependencies=[Depends(require_auth)])
    def gates(request: Request, status: str = Query("open"),
              protocol: str | None = Query(None)):
        client = cl(request)
        names = [protocol] if protocol else client.list_dir(PROTO_DIR, settings.protocols_ref)
        out = []
        for name in names:
            for pr in _pr_numbers(client, name):
                gv = state_reader.gate_view(_instance_files(client, name, pr))
                if gv and (status != "open" or gv["open"]):
                    out.append({"protocol": name, "pr": pr, **gv})
        return {"gates": out}

    return app
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/api/test_app_routes.py -v`
Expected: PASS (all). Then run the whole api suite: `pytest tests/api -v` → all pass.

- [ ] **Step 7: Commit**

```bash
git add api/models.py api/app.py tests/api/test_app_routes.py
git commit -m "feat(api): models, auth, routes, and error mapping"
```

---

### Task 9: Entrypoint + run docs

**Files:**
- Create: `api/main.py`
- Create: `api/README.md`

**Interfaces:**
- Produces: `api.main:app` — a module-level ASGI app uvicorn can serve (`uvicorn api.main:app`), built from `Settings.from_env(os.environ)` with a real `GitHubClient`.

- [ ] **Step 1: Implement the entrypoint**

`api/main.py`:
```python
from __future__ import annotations
import os
from api.config import Settings
from api.app import create_app
from api.github_client import GitHubClient

settings = Settings.from_env(os.environ)
app = create_app(settings, client=GitHubClient(settings))
```

- [ ] **Step 2: Verify the entrypoint imports with env set**

Run:
```bash
API_BEARER_TOKEN=t GITHUB_TOKEN=t GITHUB_REPO=o/r \
  python3 -c "import api.main; print(type(api.main.app).__name__)"
```
Expected: prints `FastAPI`.

- [ ] **Step 3: Write run docs**

`api/README.md`:
```markdown
# Protocol Visibility API

Read-only REST API over the protocol engine's state. Reads the `agentic-state`
branch + Actions runs via the GitHub REST API at request time. Never writes state.

## Run

    python3 -m pip install -r api/requirements.txt
    export API_BEARER_TOKEN=...   # token clients must send
    export GITHUB_TOKEN=...       # server-side GitHub token (repo read)
    export GITHUB_REPO=owner/repo
    # optional: STATE_BRANCH (agentic-state), PROTOCOLS_REF (main),
    #           ENGINE_WORKFLOWS (csv of workflow filenames), GITHUB_API_URL
    uvicorn api.main:app --port 8000

OpenAPI docs at `/docs`. All endpoints except `/healthz` need `Authorization: Bearer $API_BEARER_TOKEN`.

## Endpoints

- `GET /protocols` — catalog
- `GET /protocols/{protocol}` — definition (state graph)
- `GET /protocols/{protocol}/instances` — PRs with runs
- `GET /protocols/{protocol}/instances/{pr}/status` — current status
- `GET /protocols/{protocol}/instances/{pr}/stats` — per-instance stats
- `GET /stats` — engine-wide stats (action minutes are wall-clock approx)
- `GET /gates?status=open[&protocol=]` — instances paused on a human gate
- `GET /healthz` — liveness/readiness (no auth)
```

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/ -q`
Expected: all tests pass (existing engine suite + new `tests/api`).

- [ ] **Step 5: Commit**

```bash
git add api/main.py api/README.md
git commit -m "feat(api): uvicorn entrypoint and run docs"
```

---

## Self-Review

**Spec coverage:**
- Data source = GitHub at request time → `github_client` (Task 7), no persistence. ✓
- Stack = FastAPI ✓ (Tasks 1, 8). Auth = shared bearer ✓ (Task 8 `require_auth`).
- Standalone `api/`, no engine import ✓ (Global Constraints; nothing imports the engine).
- Endpoints: catalog (T3/T8), list-instances (T8), status (T4/T8), stats (T5/T8), global stats (T6/T8), gates (T6/T8), healthz (T1/T8). ✓ All 8.
- Action minutes = wall-clock approx ✓ (`sum_run_minutes`, T6) + note string in `/stats`.
- Error mapping 404/401/429/502 ✓ (T8). Config env vars incl. `ENGINE_WORKFLOWS` default-all ✓ (T1).
- Testing: state_reader pure (T3–6), client respx (T7), app TestClient + fake client (T8), zero network/token ✓.
- History timeline deferred → `docs/API-BACKLOG.md` ✓ (not implemented here, by design).
- Future write endpoints = documented constraint only, not built ✓.

**Placeholder scan:** No TBD/TODO; every code step has complete code and exact commands. ✓

**Type consistency:** `status_projection`/`instance_stats`/`classify_instance`/`gate_view`/`sum_run_minutes`/`list_protocols`/`protocol_detail` names are consistent across tasks; `GitHubClient` methods (`list_tree`, `get_text`, `list_dir`, `list_workflow_runs`) match between T7, T8, and the `FakeClient`. The app reads instance files keyed by basename (`p.split("/")[-1]`), matching `load_instance_files`'s basename keys used by `state_reader` tests. ✓

**Known nuance to watch during execution:** `status_projection` infers phase order from dict iteration of `instance_files`; the `/status` `phases` array order is therefore not guaranteed to match protocol order. Tests assert by id lookup, not position, so this is acceptable for the PoC. If ordered phases are wanted later, sort against `protocol_detail` state order — noted, not required now.
