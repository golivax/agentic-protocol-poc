# Stage 4c-build — deep-review-stub protocol + agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a live depth-4 `deep-review-stub` protocol (mirroring the proven `deep-fanout` topology) plus its 5 gh-aw stub agents, verified offline (pytest NODE_PATH walk + `gh aw compile`), so the interactive live-verification phase (Stage 4c-live) can walk it on real GitHub Actions.

**Architecture:** `deep-review-stub` reuses the existing protocol.json DSL and the unified NODE_PATH engine unchanged. A single permissive leaf schema + a single presence check (`finding-present`) are reused by all five leaves (DRY). Five thin gh-aw agents mirror `rmm-summary-agent.md`, each emitting minimal `{"finding": "..."}` evidence. The protocol is proven offline by a NODE_PATH e2e walk mirroring `tests/test_deep_fanout_e2e.py` before anything goes live.

**Tech Stack:** Python 3 + PyYAML (engine + checks + tests), pytest (dev), gh-aw (`gh aw compile`), GitHub Actions. Work on `main` (4a+4b merged at `d72aff5`; workflow-on-default-branch rule requires the protocol/agents/locks on `main`). 414 tests baseline.

## Global Constraints

- **Protocol DSL UNTOUCHED** — `deep-review-stub` is authored in the existing DSL; no new/renamed fields. (Standing constraint: keep the DSL human-intuitive; flag any change.)
- **Engine UNCHANGED** — 4c adds a protocol + agents only; do NOT modify `.github/agent-factory/engine/`. If a real engine gap appears, STOP and surface it (it would mean 4a/4b missed something).
- **Mirror existing patterns:** protocol layout mirrors `.github/agent-factory/protocols/recover-mental-model-stub/`; agents mirror `.github/workflows/rmm-summary-agent.md`; the offline walk mirrors `tests/test_deep_fanout_e2e.py`.
- **Check ABI:** `<check> <evidence.json> <diff.txt> <changed-files.txt>` → prints one JSON `{"check","pass","feedback"}`, exits 0. Reads node config from `CHECK_PARAMS` env (not needed here).
- **Publish hook ABI:** `<hook> <evidence.json> <instance-key>`, env `ENGINE_LOCAL/GITHUB_REPOSITORY/PUBLISH_TOKEN/PR`, prints `{"conclusion","summary"}`; trusted zone 4.
- **gh-aw agent frontmatter:** `strict:false`, `sandbox.agent:false`, LLM endpoint under `engine.env` (`ANTHROPIC_BASE_URL` literal + `ANTHROPIC_AUTH_TOKEN` from `secrets.ANTHROPIC_API_KEY`), model `claude-sonnet-4-6`, `run-name` embeds `cid:[<cid>]`, read-only permissions, evidence uploaded as artifact `evidence`.
- **After editing any `*-agent.md`, recompile** (`gh aw compile`) and commit the `.lock.yml`. The lock is what runs.
- **Topology (mirror deep-fanout):** `preflight`(fanout: `quick` flat ∥ `deep` sub-pipeline[`triage` agent → `analyze` fanout(`sec`∥`perf`) → `join-analyze` → `report` agent inputs:[sec,perf]]) → `join-preflight`(next: done). `max_depth: 4`. Trigger: issue_comment `/deep-review` → start.
- **Tests pytest**; `pytest tests/ -q` stays green (baseline 414). Work on `main`; git add/commit only (NO checkout/switch/reset).

## File structure

| File | Responsibility |
|---|---|
| `.github/agent-factory/protocols/deep-review-stub/protocol.json` | the depth-4 protocol (DSL) |
| `.../deep-review-stub/leaf.evidence.schema.json` | one permissive leaf schema (reused by all leaves) |
| `.../deep-review-stub/checks/finding-present` | one presence check (reused by all leaves) |
| `.../deep-review-stub/publish/post-finding` | minimal publish hook (posts a one-line PR comment) |
| `tests/test_deep_review_stub_e2e.py` | offline NODE_PATH walk proving the protocol on the unified engine |
| `.github/workflows/{quick,triage,sec,perf,report}-agent.md` | 5 gh-aw stub agents |
| `.github/workflows/{quick,triage,sec,perf,report}-agent.lock.yml` | compiled locks (committed) |

---

## Task 1: `deep-review-stub` protocol + schema + check + publish + structure test

**Files:**
- Create: `.github/agent-factory/protocols/deep-review-stub/protocol.json`, `.../leaf.evidence.schema.json`, `.../checks/finding-present`, `.../publish/post-finding`
- Test: `tests/test_deep_review_stub_e2e.py` (structure assertions in this task; full walk in Task 2)

**Interfaces:**
- Produces: a protocol named `deep-review-stub` with the deep-fanout topology, every leaf emitting `{"finding": str}` validated by `finding-present`; `report` declares `inputs:[{from:sec,as:sec},{from:perf,as:perf}]`. Workflows referenced: `quick-agent`/`triage-agent`/`sec-agent`/`perf-agent`/`report-agent` (created in Task 3).

- [ ] **Step 1: Write the protocol.json**

```json
{
  "name": "deep-review-stub",
  "version": "0.1.0",
  "max_depth": 4,
  "triggers": [
    { "on": "issue_comment", "comment_prefix": "/deep-review", "command": "start" }
  ],
  "states": [
    {
      "id": "preflight",
      "kind": "fanout",
      "branches": [
        { "id": "quick", "workflow": "quick-agent", "evidence": "leaf.evidence.schema.json",
          "max_iterations": 2, "checks": [{ "run": "finding-present", "on_fail": "iterate" }],
          "publish": "post-finding" },
        {
          "id": "deep",
          "states": [
            { "id": "triage", "kind": "agent", "workflow": "triage-agent",
              "evidence": "leaf.evidence.schema.json", "max_iterations": 2,
              "checks": [{ "run": "finding-present", "on_fail": "iterate" }] },
            {
              "id": "analyze",
              "kind": "fanout",
              "branches": [
                { "id": "sec", "workflow": "sec-agent", "evidence": "leaf.evidence.schema.json",
                  "max_iterations": 2, "checks": [{ "run": "finding-present", "on_fail": "iterate" }],
                  "publish": "post-finding" },
                { "id": "perf", "workflow": "perf-agent", "evidence": "leaf.evidence.schema.json",
                  "max_iterations": 2, "checks": [{ "run": "finding-present", "on_fail": "iterate" }],
                  "publish": "post-finding" }
              ],
              "next": "join-analyze"
            },
            { "id": "join-analyze", "kind": "join", "of": "analyze", "next": "report" },
            { "id": "report", "kind": "agent", "workflow": "report-agent",
              "evidence": "leaf.evidence.schema.json", "max_iterations": 2,
              "inputs": [{ "from": "sec", "as": "sec" }, { "from": "perf", "as": "perf" }],
              "checks": [{ "run": "finding-present", "on_fail": "iterate" }],
              "publish": "post-finding" }
          ]
        }
      ],
      "next": "join-preflight"
    },
    { "id": "join-preflight", "kind": "join", "of": "preflight", "next": "done" }
  ]
}
```

- [ ] **Step 2: Write the leaf schema** (`leaf.evidence.schema.json`)

```json
{ "$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
  "properties": { "finding": { "type": "string" } }, "required": ["finding"] }
```

- [ ] **Step 3: Write the check** (`checks/finding-present`, executable, `chmod +x`)

```python
#!/usr/bin/env python3
import json, sys
with open(sys.argv[1]) as f:
    evidence = json.load(f)
finding = (evidence.get("finding") or "")
if isinstance(finding, str) and finding.strip():
    print(json.dumps({"check": "finding-present", "pass": True, "feedback": ""}))
else:
    print(json.dumps({"check": "finding-present", "pass": False,
                      "feedback": "evidence must have a non-empty string 'finding'"}))
```

- [ ] **Step 4: Write the publish hook** (`publish/post-finding`, executable, `chmod +x`) — mirrors `recover-mental-model-stub/publish/publish-summary.py`

```python
#!/usr/bin/env python3
"""Publish hook: post the leg's finding as a PR comment. ABI: <hook> <evidence.json> <instance-key>."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "engine"))
import lib  # noqa: E402
with open(sys.argv[1]) as f:
    evidence = json.load(f)
finding = evidence.get("finding", "") or ""
pr = os.environ.get("PR", "")
body = f"**deep-review-stub finding**\n\n{finding}" if finding else "(no finding produced)"
lib.post_pr_comment(pr, body)
print(json.dumps({"conclusion": "success", "summary": "Posted finding."}))
```

- [ ] **Step 5: Write the structure test** (`tests/test_deep_review_stub_e2e.py`)

```python
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))
import paths
PROTO = ROOT / ".github/agent-factory/protocols/deep-review-stub/protocol.json"

def test_deep_review_stub_topology():
    p = json.load(open(PROTO))
    assert p["name"] == "deep-review-stub"
    assert paths.max_static_depth(p) == 4
    assert paths.node_kind(p, ["preflight"]) == "fanout"
    assert paths.node_kind(p, ["preflight", "deep"]) == "sequence"
    assert paths.node_kind(p, ["preflight", "deep", "analyze"]) == "fanout"
    assert paths.next_sibling(p, ["preflight", "deep", "analyze"]) == "join-analyze"
    assert paths.next_sibling(p, ["preflight", "deep", "triage"]) == "analyze"

def test_deep_review_stub_validates():
    import lib
    lib.validate_protocol(json.load(open(PROTO)))  # must not raise
```

- [ ] **Step 6: Run + verify**

Run: `chmod +x .github/agent-factory/protocols/deep-review-stub/checks/finding-present .github/agent-factory/protocols/deep-review-stub/publish/post-finding`
Run: `pytest tests/test_deep_review_stub_e2e.py -v` → PASS. `pytest tests/ -q` → green (414 + 2).
Run (check ABI smoke): `echo '{"finding":"x"}' > /tmp/e.json && .github/agent-factory/protocols/deep-review-stub/checks/finding-present /tmp/e.json /dev/null /dev/null` → prints `{"check":"finding-present","pass":true,...}`.

- [ ] **Step 7: Commit**

```bash
git add .github/agent-factory/protocols/deep-review-stub tests/test_deep_review_stub_e2e.py
git commit -m "feat(protocol): deep-review-stub depth-4 protocol + finding check/schema/publish"
```

---

## Task 2: Offline NODE_PATH e2e walk for deep-review-stub

**Files:**
- Modify: `tests/test_deep_review_stub_e2e.py` (append the full walk)

**Interfaces:**
- Consumes: the Task 1 protocol; engine scripts via subprocess; `engine_env`/`STATE_REMOTE` from conftest.
- Produces: proof that `deep-review-stub` walks start→done on the unified engine offline (the exact sequence Stage 4c-live will watch on real Actions).

- [ ] **Step 1: Write the walk test** — model EXACTLY on `tests/test_deep_fanout_e2e.py::test_deep_fanout_walks_to_done` (read it first), substituting protocol path `deep-review-stub` and feeding `{"finding":"x"}` evidence (so `finding-present` passes). The numbered sequence: start → advance `preflight.quick` (pass) + drive `preflight.deep.triage` → continue `preflight.deep.analyze` (seeds sec/perf) → advance `preflight.deep.analyze.sec`+`.perf` → join `NODE_PATH=preflight.deep.analyze` (bubbles → report) → continue `preflight.deep.report` → advance report → join (top, `preflight`) → `_instance.joined` + done. Assert state at each step via reclone (same helpers as the deep-fanout test).

```python
# tests/test_deep_review_stub_e2e.py (append)
import subprocess
ENG = ROOT / ".github/agent-factory/engine"
def _pass_finding(tmp_path, tag):
    v = tmp_path / f"v-{tag}.json"
    v.write_text(json.dumps({"results": [
        {"check": "finding-present", "pass": True, "feedback": "", "on_fail": "iterate"}]}))
    ev = tmp_path / f"e-{tag}.json"; ev.write_text(json.dumps({"finding": "x"}))
    return v, ev

def test_deep_review_stub_walks_to_done(engine_env, tmp_path):
    # Mirror tests/test_deep_fanout_e2e.py::test_deep_fanout_walks_to_done exactly,
    # with PROTO=deep-review-stub and _pass_finding evidence. Drive next.py/advance.py/
    # join.py as subprocesses with NODE_PATH per leg; reclone the bare origin between
    # steps; assert: quick done, deep cursor triage→analyze→report, analyze __join
    # joined, report done, _instance.joined true. (See deep-fanout test for the 10
    # numbered steps + exact assertions; replicate them against this protocol dir.)
    ...
```

- [ ] **Step 2: Run to verify** — `pytest tests/test_deep_review_stub_e2e.py -v` → PASS. If any step fails, it reveals a real divergence between this protocol and the engine — STOP and report (do NOT weaken assertions). `pytest tests/ -q` green.

- [ ] **Step 3: Commit**

```bash
git add tests/test_deep_review_stub_e2e.py
git commit -m "test(deep-review-stub): offline NODE_PATH e2e walk start→done"
```

---

## Task 3: Five gh-aw stub agents + compile

**Files:**
- Create: `.github/workflows/quick-agent.md`, `triage-agent.md`, `sec-agent.md`, `perf-agent.md`, `report-agent.md`
- Create (generated): the corresponding `*.lock.yml` via `gh aw compile`

**Interfaces:**
- Produces: 5 gh-aw workflows whose names match the protocol's `workflow` fields (`quick-agent` etc.), each emitting `/tmp/gh-aw/evidence.json` = `{"finding": "..."}`.

- [ ] **Step 1: Author the agents** — copy `.github/workflows/rmm-summary-agent.md` as the template for each. Per agent, change: the `name`/`run-name` (e.g. "Deep-Review Quick Agent (protocol leg: quick)"), and the prompt body to instruct producing `{"finding": "..."}` for that leg's concern. Keep ALL frontmatter identical to rmm-summary-agent (strict/sandbox/engine.env/permissions/tools/pre-agent-steps/post-steps/timeout). The five legs + their concern:
  - `quick-agent`: a quick high-level finding about the PR.
  - `triage-agent`: triage the PR (what areas need deep review).
  - `sec-agent`: a security-oriented finding.
  - `perf-agent`: a performance-oriented finding.
  - `report-agent`: read `aw_context.inputs` (`sec`, `perf` — each the upstream leg's evidence JSON) and emit a combined `{"finding": "..."}` referencing both. (Mirror how `rmm-finalize-agent.md` reads `aw_context.inputs`.)

  Evidence rules block (in each prompt): top-level object MUST have exactly one key `"finding"`, a non-empty string; write only `/tmp/gh-aw/evidence.json`; no GitHub interaction.

- [ ] **Step 2: Compile**

Run: `gh aw compile`
Expected: regenerates all `*.lock.yml` including the 5 new ones, no errors.

- [ ] **Step 3: Verify lock files exist + are valid**

Run: `ls .github/workflows/{quick,triage,sec,perf,report}-agent.lock.yml` → all present.
Run (if actionlint available): `./actionlint .github/workflows/{quick,triage,sec,perf,report}-agent.lock.yml` — note gh-aw lock files are generated; if actionlint flags generated output, note it (the lint CI gate scopes to hand-written workflows only).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/quick-agent.md .github/workflows/triage-agent.md .github/workflows/sec-agent.md .github/workflows/perf-agent.md .github/workflows/report-agent.md .github/workflows/quick-agent.lock.yml .github/workflows/triage-agent.lock.yml .github/workflows/sec-agent.lock.yml .github/workflows/perf-agent.lock.yml .github/workflows/report-agent.lock.yml
git commit -m "feat(agents): 5 deep-review-stub gh-aw stub agents + compiled locks"
```

---

## Self-review checklist (before the final review)

- [ ] `pytest tests/ -q` green (414 + the new deep-review-stub structure + walk tests).
- [ ] `deep-review-stub` walks start→done offline via NODE_PATH (Task 2).
- [ ] `lib.validate_protocol` accepts deep-review-stub (no authoring errors).
- [ ] 5 agent `.md` + 5 `.lock.yml` present; `gh aw compile` clean; agent names match the protocol `workflow` fields.
- [ ] No engine (`.github/agent-factory/engine/`) change.
- [ ] DSL untouched (deep-review-stub uses only existing fields).

## Hand-off to Stage 4c-live (interactive, NOT in this plan)

After this plan: the gated production push (`origin/main`), the live `/deep-review` walk, re-verify `/review` + `/recover`, the live-debug pass, and the `protocol-join.yml` `client_payload`-in-`run:` hardening. Those require real Actions runs + user confirmation and are run interactively, not subagent-automated.
