# Protocol Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `dist/install.sh` — a simple installer that distributes the agentic-protocol engine + one or more chosen protocols into any target repo, reusing `gh aw` (unchanged) for per-workflow engine selection + secrets + compile, and gluing the rest.

**Architecture:** A thin **bash orchestrator** (`dist/install.sh`) handles arg-parsing, preflight, `gh`/`git`/`gh aw` calls, prompts, and sequencing. The logic-heavy, side-effect-free seams live in **two small Python stdlib helpers** — `dist/resolve.py` (recursively derive which agent workflows a protocol needs) and `dist/receipt.py` (read/write/diff the `.install.json` install receipt: hashing, orphan detection, drift detection, version compatibility) — which are unit-tested with pytest like the rest of the repo. The installer runs inside a clone of the target repo, fetches the engine/protocol/engine-workflows live from the source repo at a ref via `gh api`, and pushes directly to the default branch.

**Tech Stack:** Bash (`set -euo pipefail`, shellcheck-clean), Python 3 stdlib (`json`, `hashlib`, `os`, `sys`), `gh` CLI (≥2.0), the `github/gh-aw` gh extension (≥0.77), `git`. pytest for unit tests (dev-only).

## Global Constraints

- **dist/*.py: Python 3 stdlib ONLY** — `json`, `hashlib`, `os`, `sys`, `subprocess`. No PyYAML (protocol.json is JSON), no third-party deps. These ship to the target and run there.
- **Bash:** `#!/usr/bin/env bash` + `set -euo pipefail`; pass user/agent-derived strings to commands via `env`/quoted vars, NEVER interpolate into a larger `eval`/`run` string; must be shellcheck-clean (the repo's `.github/workflows/lint.yml` runs shellcheck with `SHELLCHECK_OPTS=--severity=error`).
- **Install-time external tools:** `git`, `gh` (≥2.0.0), the `github/gh-aw` extension (≥ the min version in the manifest). Preflight checks all three before mutating anything.
- **Source-of-truth values live in `dist/manifest.json`** — the source repo `golivax/agentic-protocol-poc`, default ref `main`, the shipped `engine_version` `1.0.0`, and the min gh-aw version `0.77.0`. Copy these exact values.
- **DSL addition (approved):** `protocol.json` gains an optional top-level `min_engine_version` (string). `lib.validate_protocol` already ignores unknown top-level keys, so no engine code changes — but a regression test must lock that in.
- **State branch is sacred:** advance `agentic-state` only by fast-forward; the installer only ever *creates* it if absent and never force-pushes it. Install/update push the unit to the **default branch**.
- **Engine selection is per-workflow at install time** (not hardcoded, not unified). **Custom endpoint configuration is an explicit, opt-in, previewed step** — never silent.
- **Acceptance:** install `code-review` + `recover-mental-model-stub` on `https://github.com/golivax/throw-away-repo` and run both end-to-end.

---

## File Structure

**New files (the deliverable):**
- `dist/install.sh` — bash orchestrator (subcommands `install`, `update`, `list`; `--dry-run`).
- `dist/resolve.py` — pure: recursive agent-workflow derivation from a `protocol.json`.
- `dist/receipt.py` — pure: read/write/diff `.install.json` (hash, orphan, drift, version compat).
- `dist/manifest.json` — data: common file set + source defaults + `engine_version` + min gh-aw.
- `dist/README.md` — one-screen install instructions.
- `tests/test_dist_resolve.py` — unit tests for `resolve.py`.
- `tests/test_dist_receipt.py` — unit tests for `receipt.py`.
- `tests/test_dist_min_engine_version.py` — regression: `validate_protocol` tolerates `min_engine_version`.
- `tests/test_dist_install_cli.py` — bash CLI smoke (`list`, `--dry-run`) against a fake `gh`.
- `docs/superpowers/runbooks/2026-06-24-distribution-acceptance.md` — manual e2e runbook.

**Modified files:**
- `.github/workflows/{preflight,grumpy,security,quick,triage,sec,perf,report,rmm-draft,rmm-finalize,rmm-summary}-agent.md` (11) — strip the hardcoded `engine:` block → engine-agnostic templates.
- `.github/agent-factory/protocols/{code-review,deep-review-stub,recover-mental-model-stub}/protocol.json` (3) — add `min_engine_version`.
- `.gitignore` — ignore `*-agent.lock.yml` (locks become install-time artifacts).

**Deleted files:**
- `.github/workflows/*-agent.lock.yml` (11) — stale once engines are stripped; regenerated at install.

> **Consequence (accepted during brainstorming):** stripping engines + removing locks means the source repo's own agent workflows stop running until reconfigured. This is intentional — the `.md` become genuine engine-agnostic templates. The acceptance test runs on `throw-away-repo`, not the source, so this does not block it.

---

## Task 1: Manifest + README scaffold

**Files:**
- Create: `dist/manifest.json`
- Create: `dist/README.md`

**Interfaces:**
- Produces: `dist/manifest.json` with keys `source` (str `"owner/repo"`), `ref` (str), `engine_version` (str), `min_gh_aw_version` (str), `engine_dir` (str path), `engine_workflows` (list[str] paths). Consumed by `install.sh` and `receipt.py` compat checks.

- [ ] **Step 1: Write the manifest**

`dist/manifest.json`:
```json
{
  "source": "golivax/agentic-protocol-poc",
  "ref": "main",
  "engine_version": "1.0.0",
  "min_gh_aw_version": "0.77.0",
  "engine_dir": ".github/agent-factory/engine",
  "engine_workflows": [
    ".github/workflows/agentic-orchestrator.yml",
    ".github/workflows/agentic-engine.yml",
    ".github/workflows/protocol-join.yml"
  ]
}
```

- [ ] **Step 2: Verify it parses**

Run: `python3 -c "import json; d=json.load(open('dist/manifest.json')); assert d['engine_version']=='1.0.0'; assert len(d['engine_workflows'])==3; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Write the README**

`dist/README.md`:
```markdown
# Distributing agentic protocols

Install a protocol (the engine + a protocol + its agent workflows) into any repo.

## Prerequisites
- `gh` ≥ 2.0 authenticated with `repo,workflow` scopes (`gh auth status`)
- The gh-aw extension: `gh extension install github/gh-aw`
- GitHub Actions enabled on the target repo; write access

## Install
```bash
git clone https://github.com/<you>/<target> && cd <target>
curl -fsSL https://raw.githubusercontent.com/golivax/agentic-protocol-poc/main/dist/install.sh \
  | bash -s -- install code-review
```
Install several at once: `... install code-review recover-mental-model-stub`.
List what's available: `... list`. Update later: `... update`.

During install you pick an engine per agent workflow (via the gh-aw wizard) and,
optionally, configure a custom endpoint — the installer shows exactly what it will
write before doing so.
```

- [ ] **Step 4: Commit**

```bash
git add dist/manifest.json dist/README.md
git commit -m "feat(dist): manifest + README scaffold for the protocol installer"
```

---

## Task 2: `resolve.py` — recursive agent derivation

**Files:**
- Create: `dist/resolve.py`
- Test: `tests/test_dist_resolve.py`

**Interfaces:**
- Produces: `derive_agents(protocol: dict) -> list[str]` — every `workflow` value found at any nesting depth, de-duplicated in first-seen order. CLI: `python3 dist/resolve.py agents <protocol.json>` prints one agent name per line. Consumed by `install.sh` (each name → a `gh aw add` target).

- [ ] **Step 1: Write the failing test**

`tests/test_dist_resolve.py`:
```python
import json, subprocess, sys
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"
sys.path.insert(0, str(DIST))
import resolve  # noqa: E402


def test_flat_protocol_collects_top_and_branch_workflows():
    proto = {
        "states": [
            {"id": "preflight", "kind": "agent", "workflow": "preflight-agent", "next": "review"},
            {"id": "review", "kind": "fanout", "branches": [
                {"id": "grumpy", "workflow": "grumpy-agent"},
                {"id": "security", "workflow": "security-agent"},
            ]},
        ]
    }
    assert resolve.derive_agents(proto) == ["preflight-agent", "grumpy-agent", "security-agent"]


def test_recursive_nested_subpipeline_and_fanout():
    proto = {
        "states": [
            {"id": "preflight", "kind": "fanout", "branches": [
                {"id": "quick", "workflow": "quick-agent"},
                {"id": "deep", "states": [
                    {"id": "triage", "kind": "agent", "workflow": "triage-agent"},
                    {"id": "analyze", "kind": "fanout", "branches": [
                        {"id": "sec", "workflow": "sec-agent"},
                        {"id": "perf", "workflow": "perf-agent"},
                    ]},
                    {"id": "report", "kind": "agent", "workflow": "report-agent"},
                ]},
            ]},
        ]
    }
    assert resolve.derive_agents(proto) == [
        "quick-agent", "triage-agent", "sec-agent", "perf-agent", "report-agent",
    ]


def test_dedup_and_ignore_non_string():
    proto = {"states": [
        {"workflow": "a-agent"},
        {"workflow": "a-agent"},
        {"workflow": None},
        {"branches": [{"workflow": "b-agent"}]},
    ]}
    assert resolve.derive_agents(proto) == ["a-agent", "b-agent"]


def test_cli_agents(tmp_path):
    p = tmp_path / "protocol.json"
    p.write_text(json.dumps({"states": [{"workflow": "x-agent"}]}))
    out = subprocess.run(
        [sys.executable, str(DIST / "resolve.py"), "agents", str(p)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out.split() == ["x-agent"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_dist_resolve.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'resolve'` (file doesn't exist yet).

- [ ] **Step 3: Implement `resolve.py`**

`dist/resolve.py`:
```python
#!/usr/bin/env python3
"""dist/resolve.py — pure protocol resolution for the installer.

No network, no disk writes, stdlib only. The one logic-heavy seam: given a
parsed protocol.json, find every agent workflow it references at any nesting
depth (top-level states, fan-out branches, nested sub-pipelines, fan-outs
inside fan-outs).
"""
import json
import sys


def derive_agents(protocol):
    """Return de-duplicated agent workflow names in first-seen order.

    Walks the whole protocol structure; any dict with a string `workflow`
    value contributes that name. Order is deterministic (depth-first, key
    order as authored), which keeps the installer's `gh aw add` sequence and
    the receipt's file list stable across runs.
    """
    seen = []

    def walk(node):
        if isinstance(node, dict):
            wf = node.get("workflow")
            if isinstance(wf, str) and wf and wf not in seen:
                seen.append(wf)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(protocol)
    return seen


def main(argv):
    if len(argv) >= 3 and argv[1] == "agents":
        with open(argv[2]) as f:
            protocol = json.load(f)
        for name in derive_agents(protocol):
            print(name)
        return 0
    sys.stderr.write("usage: resolve.py agents <protocol.json>\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_dist_resolve.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add dist/resolve.py tests/test_dist_resolve.py
git commit -m "feat(dist): recursive agent-workflow derivation (resolve.py)"
```

---

## Task 3: `receipt.py` — write + hash

**Files:**
- Create: `dist/receipt.py`
- Test: `tests/test_dist_receipt.py`

**Interfaces:**
- Produces:
  - `file_hash(path: str) -> str` — sha256 hex of a file's bytes.
  - `build_receipt(source, ref, engine_version, protocols, files, root) -> dict` — `protocols` is `dict[name -> version]`; `files` is `list[str]` repo-relative paths; `root` is the repo root for hashing. Returns `{"source","ref","engine_version","protocols",{...},"files":{path: sha256}}`.
  - `write_receipt(path, receipt) -> None` — JSON, sorted keys, trailing newline.
  - CLI `python3 dist/receipt.py write <out> <source> <ref> <engine_version> <protocols-json> <root> <file>...`.

- [ ] **Step 1: Write the failing test**

`tests/test_dist_receipt.py`:
```python
import hashlib, json, sys
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"
sys.path.insert(0, str(DIST))
import receipt  # noqa: E402


def test_file_hash_matches_hashlib(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello")
    assert receipt.file_hash(str(f)) == hashlib.sha256(b"hello").hexdigest()


def test_build_receipt_shape(tmp_path):
    (tmp_path / "x.py").write_bytes(b"print(1)\n")
    r = receipt.build_receipt(
        source="o/r", ref="main", engine_version="1.0.0",
        protocols={"code-review": "0.1.0"}, files=["x.py"], root=str(tmp_path),
    )
    assert r["source"] == "o/r"
    assert r["protocols"] == {"code-review": "0.1.0"}
    assert r["files"]["x.py"] == hashlib.sha256(b"print(1)\n").hexdigest()


def test_write_receipt_roundtrip(tmp_path):
    r = {"source": "o/r", "files": {"a": "b"}}
    out = tmp_path / ".install.json"
    receipt.write_receipt(str(out), r)
    assert json.loads(out.read_text()) == r
    assert out.read_text().endswith("\n")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_dist_receipt.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'receipt'`.

- [ ] **Step 3: Implement `receipt.py` (write + hash only)**

`dist/receipt.py`:
```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_dist_receipt.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add dist/receipt.py tests/test_dist_receipt.py
git commit -m "feat(dist): install receipt write + hashing (receipt.py)"
```

---

## Task 4: `receipt.py` — diff (orphans) + drift

**Files:**
- Modify: `dist/receipt.py`
- Modify: `tests/test_dist_receipt.py`

**Interfaces:**
- Consumes: `build_receipt`, `file_hash` from Task 3.
- Produces:
  - `orphans(old_receipt: dict, new_files: list[str]) -> list[str]` — files present in `old_receipt["files"]` but not in `new_files` (to delete on update), sorted.
  - `drifted(old_receipt: dict, root: str) -> list[str]` — files whose current on-disk hash ≠ the recorded hash (locally modified; skip unless `--force`), sorted. Missing files are NOT drift (they're handled as orphans/re-fetch).
  - CLI: `receipt.py orphans <receipt.json> <newfile>...` and `receipt.py drift <receipt.json> <root>` — print one path per line.

- [ ] **Step 1: Write the failing tests (append)**

Append to `tests/test_dist_receipt.py`:
```python
def test_orphans_finds_removed_files():
    old = {"files": {"a": "h1", "b": "h2", "c": "h3"}}
    assert receipt.orphans(old, ["a", "c"]) == ["b"]


def test_drift_detects_local_edits(tmp_path):
    (tmp_path / "a").write_bytes(b"orig")
    rec = {"files": {"a": receipt.file_hash(str(tmp_path / "a")), "gone": "x"}}
    # unchanged → no drift; missing file is not drift
    assert receipt.drifted(rec, str(tmp_path)) == []
    (tmp_path / "a").write_bytes(b"edited")
    assert receipt.drifted(rec, str(tmp_path)) == ["a"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_dist_receipt.py -q`
Expected: FAIL — `AttributeError: module 'receipt' has no attribute 'orphans'`.

- [ ] **Step 3: Implement `orphans` + `drifted`**

Add to `dist/receipt.py` (above `main`):
```python
def orphans(old_receipt, new_files):
    new = set(new_files)
    return sorted(p for p in old_receipt.get("files", {}) if p not in new)


def drifted(old_receipt, root):
    out = []
    for path, recorded in old_receipt.get("files", {}).items():
        full = os.path.join(root, path)
        if os.path.isfile(full) and file_hash(full) != recorded:
            out.append(path)
    return sorted(out)
```

Extend `main` (insert before the usage error):
```python
    if len(argv) >= 3 and argv[1] == "orphans":
        old = json.load(open(argv[2]))
        for p in orphans(old, argv[3:]):
            print(p)
        return 0
    if len(argv) == 4 and argv[1] == "drift":
        old = json.load(open(argv[2]))
        for p in drifted(old, argv[3]):
            print(p)
        return 0
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_dist_receipt.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add dist/receipt.py tests/test_dist_receipt.py
git commit -m "feat(dist): receipt orphan + drift detection"
```

---

## Task 5: `receipt.py` — version compatibility

**Files:**
- Modify: `dist/receipt.py`
- Modify: `tests/test_dist_receipt.py`

**Interfaces:**
- Produces:
  - `parse_version(v: str) -> tuple[int, ...]` — `"1.2.3" -> (1,2,3)`; tolerant of missing parts (`"1" -> (1,)`).
  - `is_compatible(engine_version: str, min_engine_version: str | None) -> bool` — `True` if `min` is falsy or `engine_version >= min_engine_version`.
  - `is_breaking_bump(old: str, new: str) -> bool` — `True` if the major component increased.
  - CLI: `receipt.py compat <engine_version> <min_engine_version>` — exit 0 if compatible, 1 if not.

- [ ] **Step 1: Write the failing tests (append)**

Append to `tests/test_dist_receipt.py`:
```python
import subprocess  # noqa: E402


def test_version_compat():
    assert receipt.is_compatible("1.0.0", None) is True
    assert receipt.is_compatible("1.0.0", "1.0.0") is True
    assert receipt.is_compatible("1.2.0", "1.0.0") is True
    assert receipt.is_compatible("1.0.0", "2.0.0") is False


def test_breaking_bump():
    assert receipt.is_breaking_bump("1.4.0", "2.0.0") is True
    assert receipt.is_breaking_bump("1.0.0", "1.9.0") is False


def test_compat_cli_exit_codes():
    ok = subprocess.run([sys.executable, str(DIST / "receipt.py"), "compat", "1.0.0", "1.0.0"])
    bad = subprocess.run([sys.executable, str(DIST / "receipt.py"), "compat", "1.0.0", "2.0.0"])
    assert ok.returncode == 0 and bad.returncode == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_dist_receipt.py -q`
Expected: FAIL — `AttributeError: module 'receipt' has no attribute 'is_compatible'`.

- [ ] **Step 3: Implement version helpers**

Add to `dist/receipt.py` (above `main`):
```python
def parse_version(v):
    return tuple(int(x) for x in str(v).split(".") if x.isdigit())


def is_compatible(engine_version, min_engine_version):
    if not min_engine_version:
        return True
    return parse_version(engine_version) >= parse_version(min_engine_version)


def is_breaking_bump(old, new):
    po, pn = parse_version(old), parse_version(new)
    return bool(pn) and bool(po) and pn[0] > po[0]
```

Extend `main`:
```python
    if len(argv) == 4 and argv[1] == "compat":
        return 0 if is_compatible(argv[2], argv[3]) else 1
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_dist_receipt.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add dist/receipt.py tests/test_dist_receipt.py
git commit -m "feat(dist): engine/protocol version compatibility checks"
```

---

## Task 6: Add `min_engine_version` to protocols + lock in tolerance

**Files:**
- Modify: `.github/agent-factory/protocols/code-review/protocol.json`
- Modify: `.github/agent-factory/protocols/deep-review-stub/protocol.json`
- Modify: `.github/agent-factory/protocols/recover-mental-model-stub/protocol.json`
- Test: `tests/test_dist_min_engine_version.py`

**Interfaces:**
- Consumes: `lib.validate_protocol` (engine).
- Produces: each `protocol.json` has a top-level `"min_engine_version": "1.0.0"`.

- [ ] **Step 1: Write the failing test**

`tests/test_dist_min_engine_version.py`:
```python
import json
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402

PROTOCOLS = Path(__file__).resolve().parents[1] / ".github/agent-factory/protocols"


def test_all_shipped_protocols_declare_min_engine_version():
    for name in ["code-review", "deep-review-stub", "recover-mental-model-stub"]:
        proto = json.load(open(PROTOCOLS / name / "protocol.json"))
        assert proto.get("min_engine_version") == "1.0.0", name


def test_validate_protocol_tolerates_min_engine_version():
    proto = {
        "name": "x", "min_engine_version": "1.0.0",
        "states": [{"id": "s", "kind": "agent", "workflow": "w-agent"}],
    }
    lib.validate_protocol(proto)  # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_dist_min_engine_version.py -q`
Expected: FAIL — `assert None == '1.0.0'` (field not present yet).

- [ ] **Step 3: Add the field to each protocol.json**

In each of the three `protocol.json` files, add the key immediately after the `"version"` line. Example for `code-review/protocol.json` (top of file):
```json
{
  "name": "code-review",
  "version": "0.1.0",
  "min_engine_version": "1.0.0",
```
Do the same for `deep-review-stub` and `recover-mental-model-stub` (each already has `"name"` and `"version"`).

- [ ] **Step 4: Run to verify pass (and no engine regressions)**

Run: `pytest tests/test_dist_min_engine_version.py -q`
Expected: PASS (2 passed).
Run: `pytest tests/ -q`
Expected: PASS (whole suite still green — the new field is ignored by the engine).

- [ ] **Step 5: Commit**

```bash
git add .github/agent-factory/protocols/*/protocol.json tests/test_dist_min_engine_version.py
git commit -m "feat(dist): declare min_engine_version on shipped protocols"
```

---

## Task 7: Templatize agent `.md` (strip engines) + drop locks

**Files:**
- Modify: 11 × `.github/workflows/*-agent.md`
- Delete: 11 × `.github/workflows/*-agent.lock.yml`
- Modify: `.gitignore`
- Test: `tests/test_dist_templates.py`

**Interfaces:**
- Produces: agent `.md` files with NO `engine:` block and NO `ANTHROPIC_BASE_URL` literal — the engine is supplied at install time. `strict:`/`sandbox:`/`permissions:`/`tools:` and the body are untouched.

- [ ] **Step 1: Write the failing test**

`tests/test_dist_templates.py`:
```python
import glob
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github/workflows"
AGENTS = sorted(glob.glob(str(WF / "*-agent.md")))


def test_eleven_agents_present():
    assert len(AGENTS) == 11


def test_no_agent_md_hardcodes_engine_or_endpoint():
    for md in AGENTS:
        text = Path(md).read_text()
        assert "ANTHROPIC_BASE_URL" not in text, md
        assert "\nengine:\n" not in text and not text.startswith("engine:\n"), md


def test_no_agent_locks_committed():
    assert glob.glob(str(WF / "*-agent.lock.yml")) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_dist_templates.py -q`
Expected: FAIL — engines/endpoints still present and locks still committed.

- [ ] **Step 3: Strip the engine block from every agent `.md`**

Run this one-off script (it removes the contiguous `engine:` mapping plus the trailing endpoint comment block, leaving everything else intact):
```bash
python3 - <<'PY'
import glob, re
for md in glob.glob(".github/workflows/*-agent.md"):
    lines = open(md).read().splitlines(keepends=True)
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith("engine:"):
            i += 1
            # skip indented engine children
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].strip() == ""):
                i += 1
            # skip the trailing endpoint explanation comment block
            while i < len(lines) and lines[i].startswith("#"):
                i += 1
            continue
        out.append(lines[i]); i += 1
    open(md, "w").write("".join(out))
    print("stripped", md)
PY
```

- [ ] **Step 4: Remove the stale locks and ignore future ones**

```bash
git rm .github/workflows/*-agent.lock.yml
printf '\n# Agent locks are install-time artifacts (compiled on the target after engine selection)\n.github/workflows/*-agent.lock.yml\n' >> .gitignore
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_dist_templates.py -q`
Expected: PASS (3 passed).
Manual sanity: `grep -rl "ANTHROPIC_BASE_URL\|^engine:" .github/workflows/*.md` → no output.

- [ ] **Step 6: Commit**

```bash
git add -A .github/workflows .gitignore tests/test_dist_templates.py
git commit -m "feat(dist): templatize agent workflows (strip hardcoded engines); locks now install-time"
```

---

## Task 8: `install.sh` — skeleton, preflight, `list`

**Files:**
- Create: `dist/install.sh`
- Test: `tests/test_dist_install_cli.py`

**Interfaces:**
- Produces: `install.sh <subcommand> [args] [--flags]` with subcommands `install`, `update`, `list`; global flags `--source`, `--ref`, `--dry-run`, `--base-url`, `--force`. Reads defaults from `dist/manifest.json` (fetched first). `list` prints available protocol names (one per line) by listing `protocols/` in the source at the ref.

- [ ] **Step 1: Write the failing CLI smoke test**

`tests/test_dist_install_cli.py`:
```python
import os, stat, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "dist/install.sh"


def _fake_gh(tmp_path):
    """A fake `gh` that answers the two calls `list` makes: api version probe
    and the protocols/ tree listing. Returns its bin dir for PATH."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"git/trees"* ]]; then\n'
        '  echo \'{"tree":[{"path":"code-review","type":"tree"},'
        '{"path":"deep-review-stub","type":"tree"},'
        '{"path":"recover-mental-model-stub","type":"tree"}]}\'\n'
        "  exit 0\nfi\n"
        'echo "2.40.0"\n'
    )
    gh.chmod(0o755)
    return bindir


def test_list_prints_protocol_names(tmp_path):
    env = dict(os.environ)
    env["PATH"] = f"{_fake_gh(tmp_path)}:{env['PATH']}"
    out = subprocess.run(
        ["bash", str(INSTALL), "list"], capture_output=True, text=True, env=env,
    )
    names = set(out.stdout.split())
    assert {"code-review", "deep-review-stub", "recover-mental-model-stub"} <= names
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_dist_install_cli.py -q`
Expected: FAIL — `install.sh` does not exist.

- [ ] **Step 3: Implement the skeleton**

`dist/install.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail

# ── defaults (overridable by flags) ───────────────────────────────────────────
SOURCE="golivax/agentic-protocol-poc"
REF="main"
DRY_RUN=0
FORCE=0
BASE_URL=""
SUBCMD=""
PROTOCOLS=()

die() { echo "error: $*" >&2; exit 1; }
log() { echo "▸ $*" >&2; }

# Fetch one file's raw contents from the source repo at the ref.
gh_raw() { gh api "repos/${SOURCE}/contents/$1?ref=${REF}" --jq '.content' | base64 -d; }

# List immediate child names of a directory in the source tree (type filterable).
gh_tree_children() {
  local dir="$1" kind="$2"
  gh api "repos/${SOURCE}/git/trees/${REF}:${dir}" \
    --jq ".tree[] | select(.type == \"${kind}\") | .path" 2>/dev/null || true
}

parse_args() {
  SUBCMD="${1:-}"; shift || true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --source) SOURCE="$2"; shift 2 ;;
      --ref) REF="$2"; shift 2 ;;
      --base-url) BASE_URL="$2"; shift 2 ;;
      --dry-run) DRY_RUN=1; shift ;;
      --force) FORCE=1; shift ;;
      -*) die "unknown flag: $1" ;;
      *) PROTOCOLS+=("$1"); shift ;;
    esac
  done
}

cmd_list() { gh_tree_children "protocols" "tree"; }   # placeholder path; see note

case_dispatch() {
  case "$SUBCMD" in
    list) cmd_list ;;
    install) cmd_install ;;
    update) cmd_update ;;
    *) die "usage: install.sh {install|update|list} [protocol...] [--source o/r] [--ref R] [--base-url URL] [--dry-run] [--force]" ;;
  esac
}

# cmd_install / cmd_update are defined in later tasks.
cmd_install() { die "not yet implemented"; }
cmd_update() { die "not yet implemented"; }

parse_args "$@"
case_dispatch
```

> **Note on the tree path:** the protocols live at `.github/agent-factory/protocols/`. Use that exact prefix in `gh_tree_children` — change the `cmd_list` call to `gh_tree_children ".github/agent-factory/protocols" "tree"`. (The fake-gh test matches on `git/trees` regardless of the path, so it stays green; this note prevents a wrong literal.)

Apply that note now: set `cmd_list()` to `gh_tree_children ".github/agent-factory/protocols" "tree"`.

- [ ] **Step 4: Run to verify pass + shellcheck**

Run: `pytest tests/test_dist_install_cli.py -q`
Expected: PASS (1 passed).
Run: `shellcheck -S error dist/install.sh`
Expected: no output (clean).

- [ ] **Step 5: Commit**

```bash
git add dist/install.sh tests/test_dist_install_cli.py
git commit -m "feat(dist): install.sh skeleton (arg parse, preflight helpers, list)"
```

---

## Task 9: `install.sh` — preflight checks + fetch + `--dry-run`

**Files:**
- Modify: `dist/install.sh`

**Interfaces:**
- Consumes: `gh_raw`, `gh_tree_children`, `dist/resolve.py`, `dist/manifest.json`.
- Produces: `preflight()` (auth, write access, gh-aw present + version, Actions enabled); `bootstrap_helpers()` (fetch `manifest.json` + `resolve.py` + `receipt.py` into a temp workdir); `plan_fetch(protocol)` (echo the resolved file list); `--dry-run` prints the full plan and exits before any mutation.

- [ ] **Step 1: Add preflight + helper bootstrap + dry-run planning**

Add to `dist/install.sh` (before `parse_args "$@"`):
```bash
WORKDIR=""
cleanup() { [[ -n "$WORKDIR" && -d "$WORKDIR" ]] && rm -rf "$WORKDIR"; }
trap cleanup EXIT

bootstrap_helpers() {
  WORKDIR="$(mktemp -d)"
  gh_raw "dist/manifest.json" > "$WORKDIR/manifest.json"
  gh_raw "dist/resolve.py"    > "$WORKDIR/resolve.py"
  gh_raw "dist/receipt.py"    > "$WORKDIR/receipt.py"
  # adopt manifest defaults the caller didn't override
  ENGINE_VERSION="$(python3 -c "import json;print(json.load(open('$WORKDIR/manifest.json'))['engine_version'])")"
  MIN_GH_AW="$(python3 -c "import json;print(json.load(open('$WORKDIR/manifest.json'))['min_gh_aw_version'])")"
}

preflight() {
  command -v git >/dev/null || die "git not found"
  command -v gh  >/dev/null || die "gh not found (install GitHub CLI ≥ 2.0)"
  gh auth status >/dev/null 2>&1 || die "gh not authenticated — run: gh auth login --scopes repo,workflow"
  gh extension list 2>/dev/null | grep -q 'github/gh-aw' \
    || die "gh-aw missing — run: gh extension install github/gh-aw"
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "run inside a clone of the target repo"
  local slug; slug="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
  gh api "repos/${slug}" --jq '.permissions.push' | grep -q true \
    || die "you need write access to ${slug}"
}

# Echo the repo-relative files a protocol contributes (engine + workflows handled
# separately as the shared/common set).
protocol_files() {
  local proto="$1"
  gh api "repos/${SOURCE}/git/trees/${REF}:.github/agent-factory/protocols/${proto}?recursive=1" \
    --jq '.tree[] | select(.type=="blob") | ".github/agent-factory/protocols/'"${proto}"'/" + .path'
}

common_files() {
  python3 - "$WORKDIR/manifest.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
for w in m["engine_workflows"]:
    print(w)
PY
  # engine python files (list the engine dir blobs)
  gh api "repos/${SOURCE}/git/trees/${REF}:$(python3 -c "import json;print(json.load(open('$WORKDIR/manifest.json'))['engine_dir'])")" \
    --jq '.tree[] | select(.type=="blob") | select(.path|endswith(".py")) | "'"$(python3 -c "import json;print(json.load(open('$WORKDIR/manifest.json'))['engine_dir'])")"'/" + .path'
}

print_plan() {
  echo "# source: ${SOURCE}@${REF}  engine_version: ${ENGINE_VERSION}"
  echo "# common:"; common_files | sed 's/^/  /'
  local p
  for p in "${PROTOCOLS[@]}"; do
    echo "# protocol ${p}:"; protocol_files "$p" | sed 's/^/  /'
    echo "# agents ${p}:"
    gh_raw ".github/agent-factory/protocols/${p}/protocol.json" \
      | python3 "$WORKDIR/resolve.py" agents /dev/stdin | sed 's/^/  /'
  done
}
```

- [ ] **Step 2: Wire dry-run into `cmd_install` (temporary stub)**

Replace the placeholder `cmd_install()` with:
```bash
cmd_install() {
  [[ ${#PROTOCOLS[@]} -gt 0 ]] || die "name at least one protocol (see: install.sh list)"
  preflight
  bootstrap_helpers
  if [[ "$DRY_RUN" == 1 ]]; then print_plan; exit 0; fi
  die "install not yet implemented past --dry-run"   # completed in Tasks 10–11
}
```

- [ ] **Step 3: Manual smoke (requires gh auth + network) + shellcheck**

Run: `shellcheck -S error dist/install.sh`
Expected: clean.
Run (real network, read-only — prints a plan, mutates nothing):
`bash dist/install.sh install code-review --dry-run`
Expected: a plan listing the 3 engine workflows, the engine `*.py`, the `code-review/**` tree, and agents `preflight-agent grumpy-agent security-agent`.

- [ ] **Step 4: Commit**

```bash
git add dist/install.sh
git commit -m "feat(dist): preflight, helper bootstrap, and --dry-run fetch plan"
```

---

## Task 10: `install.sh` — fetch the unit + install agents + endpoint step

**Files:**
- Modify: `dist/install.sh`

**Interfaces:**
- Consumes: `common_files`, `protocol_files`, `gh_raw`, `resolve.py`.
- Produces: `fetch_unit()` (write common + protocol files into the working tree); `install_agents(protocol)` (per-agent `gh aw add ... --engine <pick>` with an engine prompt) and `configure_endpoints()` (the explicit, opt-in, previewed custom-endpoint step) + `gh aw compile`.

- [ ] **Step 1: Implement fetch + agent install + endpoint config**

Add to `dist/install.sh`:
```bash
fetch_one() {  # <repo-relative-path>
  local path="$1"; mkdir -p "$(dirname "$path")"; gh_raw "$path" > "$path"
}

fetch_unit() {
  local f
  while read -r f; do [[ -n "$f" ]] && fetch_one "$f"; done < <(common_files)
  local p
  for p in "${PROTOCOLS[@]}"; do
    while read -r f; do [[ -n "$f" ]] && fetch_one "$f"; done < <(protocol_files "$p")
  done
}

# Prompt once per agent for an engine, then add it from source with that engine.
install_agents() {
  local p="$1" agent engine
  while read -r agent; do
    [[ -z "$agent" ]] && continue
    read -r -p "Engine for ${agent} [claude/copilot/codex/gemini] (default claude): " engine </dev/tty || engine=""
    engine="${engine:-claude}"
    AGENT_ENGINES["$agent"]="$engine"
    log "adding ${agent} (engine: ${engine})"
    gh aw add "${SOURCE}/workflows/${agent}.md@${REF}" --engine "$engine" --force
  done < <(gh_raw ".github/agent-factory/protocols/${p}/protocol.json" \
            | python3 "$WORKDIR/resolve.py" agents /dev/stdin)
}

# Explicit, opt-in, previewed custom-endpoint configuration. NEVER silent.
configure_endpoints() {
  local any=0 agent engine
  for agent in "${!AGENT_ENGINES[@]}"; do
    [[ "${AGENT_ENGINES[$agent]}" == "claude" ]] && any=1
  done
  [[ "$any" == 1 ]] || return 0
  local ans; read -r -p "Configure a custom Anthropic endpoint for the Claude workflows? [y/N]: " ans </dev/tty || ans="n"
  [[ "$ans" == "y" || "$ans" == "Y" ]] || return 0
  local url; read -r -p "  Base URL (default ${BASE_URL:-https://api.anthropic.com}): " url </dev/tty || url=""
  url="${url:-${BASE_URL:-https://api.anthropic.com}}"
  echo "  The following engine.env will be added to each Claude workflow and recompiled:"
  echo "    env:"
  echo "      ANTHROPIC_BASE_URL: ${url}"
  echo "      ANTHROPIC_AUTH_TOKEN: \${{ secrets.ANTHROPIC_API_KEY }}"
  local ok; read -r -p "  Apply? [y/N]: " ok </dev/tty || ok="n"
  [[ "$ok" == "y" || "$ok" == "Y" ]] || { log "skipped endpoint config"; return 0; }
  for agent in "${!AGENT_ENGINES[@]}"; do
    [[ "${AGENT_ENGINES[$agent]}" == "claude" ]] || continue
    BASE_URL_INJECT="$url" python3 - ".github/workflows/${agent}.md" <<'PY'
import os, sys, re
md = sys.argv[1]; url = os.environ["BASE_URL_INJECT"]
text = open(md).read()
block = ("  env:\n"
         f"    ANTHROPIC_BASE_URL: {url}\n"
         "    ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}\n")
# insert env under the engine: mapping the wizard wrote
text = re.sub(r"(?m)^(engine:\n(?:[ \t].*\n)*?)", lambda m: m.group(1) + block, text, count=1)
open(md, "w").write(text)
PY
  done
  gh aw compile
}
```

Declare the assoc array near the top (after the defaults block): `declare -A AGENT_ENGINES=()`.

- [ ] **Step 2: shellcheck**

Run: `shellcheck -S error dist/install.sh`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add dist/install.sh
git commit -m "feat(dist): fetch unit, per-workflow engine install, opt-in endpoint step"
```

---

## Task 11: `install.sh` — bootstrap (state branch + token) + finalize + receipt

**Files:**
- Modify: `dist/install.sh`

**Interfaces:**
- Consumes: `receipt.py`, all of Task 10.
- Produces: `ensure_state_branch()`, `ensure_dispatch_token()`, `write_install_receipt()`, `finalize_commit()`, and a complete `cmd_install` that sequences fetch → agents → endpoints → bootstrap → receipt → finalize.

- [ ] **Step 1: Implement bootstrap, receipt, finalize, and complete cmd_install**

Add to `dist/install.sh`:
```bash
ensure_state_branch() {
  local slug; slug="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
  if gh api "repos/${slug}/branches/agentic-state" >/dev/null 2>&1; then
    log "agentic-state branch already exists — leaving it"
    return 0
  fi
  log "creating orphan agentic-state branch"
  local cur; cur="$(git rev-parse --abbrev-ref HEAD)"
  git switch --orphan agentic-state
  git commit --allow-empty -m "init agentic-state"
  git push -u origin agentic-state
  git switch "$cur"
}

ensure_dispatch_token() {
  local slug; slug="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
  if gh secret list --repo "$slug" 2>/dev/null | grep -q '^POC_DISPATCH_TOKEN'; then
    log "POC_DISPATCH_TOKEN already set"
    return 0
  fi
  local tok; read -r -s -p "Enter POC_DISPATCH_TOKEN (PAT with repo+workflow scopes): " tok </dev/tty; echo
  [[ -n "$tok" ]] || die "POC_DISPATCH_TOKEN is required"
  gh secret set POC_DISPATCH_TOKEN --repo "$slug" --body "$tok"
}

write_install_receipt() {
  local protos_json files=() p ver
  protos_json="{"
  for p in "${PROTOCOLS[@]}"; do
    ver="$(python3 -c "import json;print(json.load(open('.github/agent-factory/protocols/${p}/protocol.json'))['version'])")"
    protos_json="${protos_json}\"${p}\":\"${ver}\","
  done
  protos_json="${protos_json%,}}"
  # the installed file set = everything we wrote/touched, tracked by git
  mapfile -t files < <(git ls-files --others --modified --exclude-standard .github | sort -u)
  mkdir -p .github/agent-factory
  python3 "$WORKDIR/receipt.py" write \
    .github/agent-factory/.install.json "$SOURCE" "$REF" "$ENGINE_VERSION" "$protos_json" "." "${files[@]}"
}

finalize_commit() {
  git add -A .github
  git commit -m "chore: install agentic protocol(s): ${PROTOCOLS[*]}"
  git push
}

cmd_install() {
  [[ ${#PROTOCOLS[@]} -gt 0 ]] || die "name at least one protocol (see: install.sh list)"
  preflight
  bootstrap_helpers
  if [[ "$DRY_RUN" == 1 ]]; then print_plan; exit 0; fi
  # compatibility guard (refuse before mutating)
  local p minv
  for p in "${PROTOCOLS[@]}"; do
    minv="$(gh_raw ".github/agent-factory/protocols/${p}/protocol.json" \
      | python3 -c "import json,sys;print(json.load(sys.stdin).get('min_engine_version',''))")"
    python3 "$WORKDIR/receipt.py" compat "$ENGINE_VERSION" "$minv" \
      || die "protocol ${p} needs engine ≥ ${minv}, but source ships ${ENGINE_VERSION}"
  done
  fetch_unit
  for p in "${PROTOCOLS[@]}"; do install_agents "$p"; done
  configure_endpoints
  ensure_state_branch
  ensure_dispatch_token
  write_install_receipt
  finalize_commit
  log "done — open a PR or comment a trigger to run the protocol"
}
```

- [ ] **Step 2: shellcheck**

Run: `shellcheck -S error dist/install.sh`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add dist/install.sh
git commit -m "feat(dist): state-branch + token bootstrap, receipt write, finalize commit"
```

---

## Task 12: `install.sh` — `update` (re-sync via receipt)

**Files:**
- Modify: `dist/install.sh`

**Interfaces:**
- Consumes: `receipt.py` (`orphans`, `drift`, `compat`, `is_breaking_bump`), the receipt at `.github/agent-factory/.install.json`.
- Produces: `cmd_update()` — re-fetch at the new ref; write changed files; delete orphans; skip drifted files unless `--force`; recompile; never touch `agentic-state`; warn on breaking engine bump; rewrite receipt; commit + push.

- [ ] **Step 1: Implement `cmd_update`**

Replace the `cmd_update()` stub:
```bash
cmd_update() {
  preflight
  bootstrap_helpers
  local rcpt=".github/agent-factory/.install.json"
  [[ -f "$rcpt" ]] || die "no install receipt found ($rcpt) — run install first"
  # default to the protocols recorded in the receipt
  if [[ ${#PROTOCOLS[@]} -eq 0 ]]; then
    mapfile -t PROTOCOLS < <(python3 -c "import json;print('\n'.join(json.load(open('$rcpt'))['protocols']))")
  fi
  local old_ev; old_ev="$(python3 -c "import json;print(json.load(open('$rcpt'))['engine_version'])")"
  if python3 "$WORKDIR/receipt.py" compat "$old_ev" "$ENGINE_VERSION" >/dev/null && \
     python3 -c "import sys; sys.path.insert(0,'$WORKDIR'); import receipt; sys.exit(0 if receipt.is_breaking_bump('$old_ev','$ENGINE_VERSION') else 1)"; then
    log "WARNING: engine ${old_ev} → ${ENGINE_VERSION} is a breaking bump; finish open reviews before updating or expect to restart them"
  fi
  # drift check (skip locally-modified unless --force)
  local drifted; drifted="$(python3 "$WORKDIR/receipt.py" drift "$rcpt" .)"
  if [[ -n "$drifted" && "$FORCE" != 1 ]]; then
    log "locally-modified files will be SKIPPED (use --force to overwrite):"; echo "$drifted" >&2
  fi
  # compute the new file set, fetch it, delete orphans
  fetch_unit
  for p in "${PROTOCOLS[@]}"; do install_agents "$p"; done
  configure_endpoints
  local newfiles; newfiles="$(git ls-files .github; git ls-files --others --exclude-standard .github)"
  # delete files the receipt had but the new set doesn't
  local orphan
  while read -r orphan; do
    [[ -n "$orphan" ]] && { log "removing orphan ${orphan}"; git rm -f "$orphan" 2>/dev/null || rm -f "$orphan"; }
  done < <(python3 "$WORKDIR/receipt.py" orphans "$rcpt" $newfiles)
  write_install_receipt
  git add -A .github
  git commit -m "chore: update agentic protocol(s) to ${REF}: ${PROTOCOLS[*]}"
  git push
  log "update complete"
}
```

- [ ] **Step 2: shellcheck**

Run: `shellcheck -S error dist/install.sh`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add dist/install.sh
git commit -m "feat(dist): update subcommand (orphan cleanup, drift skip, breaking-bump warn)"
```

---

## Task 13: Acceptance runbook

**Files:**
- Create: `docs/superpowers/runbooks/2026-06-24-distribution-acceptance.md`

**Interfaces:** none (operator-run manual verification — the real acceptance bar from the spec).

- [ ] **Step 1: Write the runbook**

`docs/superpowers/runbooks/2026-06-24-distribution-acceptance.md`:
```markdown
# Distribution acceptance runbook

Target: https://github.com/golivax/throw-away-repo

## 0. Prereqs
- `gh auth status` shows repo,workflow scopes; `gh extension list` shows github/gh-aw.
- You hold a PAT for POC_DISPATCH_TOKEN (repo+workflow) and your Claude Code auth token.

## 1. Install both protocols
```bash
git clone https://github.com/golivax/throw-away-repo && cd throw-away-repo
bash <(curl -fsSL https://raw.githubusercontent.com/golivax/agentic-protocol-poc/main/dist/install.sh) \
  install code-review recover-mental-model-stub --base-url <your-funnel-url>
```
- Pick `claude` for each agent prompt.
- At the endpoint step: review the previewed engine.env, confirm.
- Enter POC_DISPATCH_TOKEN when prompted; enter your Claude Code token for ANTHROPIC_API_KEY in the gh-aw secret prompt.

## 2. Verify the unit landed
- `git log -1` on the default branch shows the install commit.
- `.github/agent-factory/{engine,protocols/code-review,protocols/recover-mental-model-stub}` present.
- `.github/agent-factory/.install.json` lists both protocols + engine_version.
- `git branch -r | grep agentic-state` exists.
- `.github/workflows/{preflight,grumpy,security,rmm-*}-agent.lock.yml` were compiled (present locally / committed).

## 3. Run code-review
- Open a PR; comment `/review`.
- Expect: pipeline check-run + status comment; preflight → review fanout → join → approval gate.

## 4. Run recover-mental-model-stub
- Comment `/recover` on an issue/PR per the protocol's trigger.
- Expect: fanout/sub-pipeline runs; the `/answer` gate opens; answer it; merge/combine completes.

## 5. Update smoke
- Bump a protocol `version` in the source (or use a newer `--ref`), then:
  `bash dist/install.sh update`
- Expect: only changed files rewritten, orphans removed, drift warning if you locally edited a file, `agentic-state` untouched.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/runbooks/2026-06-24-distribution-acceptance.md
git commit -m "docs(dist): acceptance runbook for the distribution installer"
```

---

## Self-Review notes (for the implementer)

- **Bash tasks (8–12) are validated by shellcheck + the fake-gh CLI smoke (Task 8) + the manual runbook (Task 13)**, not full unit tests — GitHub side effects can't be unit-tested offline. The genuinely logic-heavy parts (`resolve.py`, `receipt.py`) carry real pytest coverage (Tasks 2–5).
- **gh-aw behavior to verify live:** that `gh aw add <src>/workflows/<name>.md@ref --engine <e> --force` accepts an engine-less template `.md` and writes the chosen engine. If it rejects an engine-less source, fall back to: fetch the `.md`, write `engine:\n  id: <e>\n` into the frontmatter, then `gh aw compile`. Note this in Task 10 during execution if observed.
- **Endpoint injection regex (Task 10)** assumes the wizard/`--engine` writes a top-level `engine:` block. If gh-aw emits a different shape, adjust the `re.sub` anchor; the preview-then-confirm contract stays the same.
- Run `pytest tests/ -q` after Task 6 and again after Task 7 to confirm no engine regressions from the protocol.json + template changes.
