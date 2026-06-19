import os
import sys
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def test_label_text_explicit_state_label():
    proto = {"states": [{"id": "preflight", "kind": "agent", "label": "pre-flight gate"}]}
    assert lib.phase_label_text(proto, "preflight") == "pre-flight gate"


def test_label_text_humanizes_id_when_no_label():
    proto = {"states": [{"id": "code-review", "kind": "agent"}]}
    assert lib.phase_label_text(proto, "code-review") == "Code review"


def test_label_text_terminal_default():
    proto = {"states": []}
    assert lib.phase_label_text(proto, "done") == "✅ done"
    assert lib.phase_label_text(proto, "failed") == "❌ failed"
    assert lib.phase_label_text(proto, "blocked") == "⛔ blocked"
    assert lib.phase_label_text(proto, "setup") == "⚙ setup"


def test_label_text_terminal_override():
    proto = {"states": [], "phase_labels": {"done": "shipped 🚀"}}
    assert lib.phase_label_text(proto, "done") == "shipped 🚀"
    # unknown override key still falls back to default
    assert lib.phase_label_text(proto, "failed") == "❌ failed"


def _engine_local_env(monkeypatch):
    monkeypatch.setenv("ENGINE_LOCAL", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")


def _write_instance(tmp_path, pid, instance, data):
    inf = tmp_path / pid / instance / "_instance.yaml"
    inf.parent.mkdir(parents=True, exist_ok=True)
    with open(inf, "w") as fh:
        yaml.safe_dump(data, fh)
    return inf


def test_ensure_phase_label_records_applied(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "preflight", "kind": "agent", "label": "pre-flight gate"}]}
    inf = _write_instance(tmp_path, "p", "pr-1", {"protocol": "p", "instance": "pr-1"})
    lib.ensure_phase_label(str(tmp_path), "p", "pr-1", proto, "1", "preflight")
    assert yaml.safe_load(inf.read_text())["phase_label"] == "pre-flight gate"


def test_ensure_phase_label_idempotent_noop(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "review", "kind": "fanout"}]}
    inf = _write_instance(tmp_path, "p", "pr-2",
                          {"protocol": "p", "instance": "pr-2", "phase_label": "Review"})
    lib.ensure_phase_label(str(tmp_path), "p", "pr-2", proto, "1", "review")
    # unchanged; still "Review"
    assert yaml.safe_load(inf.read_text())["phase_label"] == "Review"


def test_ensure_phase_label_noop_without_instance_file(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "review", "kind": "agent"}]}
    # no _instance.yaml written → must not raise, must not create one
    lib.ensure_phase_label(str(tmp_path), "p", "pr-3", proto, "1", "review")
    assert not (tmp_path / "p" / "pr-3" / "_instance.yaml").exists()


def test_ensure_phase_label_terminal_key(tmp_path, monkeypatch):
    _engine_local_env(monkeypatch)
    proto = {"name": "p", "states": [{"id": "approval", "kind": "gate"}]}
    inf = _write_instance(tmp_path, "p", "pr-4",
                          {"protocol": "p", "instance": "pr-4", "phase_label": "Approval"})
    lib.ensure_phase_label(str(tmp_path), "p", "pr-4", proto, "1", "done")
    assert yaml.safe_load(inf.read_text())["phase_label"] == "✅ done"


from conftest import run_engine, read_state_yaml  # noqa: E402

CRP_PROTO = ROOT / ".github/agent-factory/protocols/code-review-pipeline/protocol.json"


def test_start_seeds_first_phase_label(engine_env, tmp_path):
    """A fresh `start` on code-review-pipeline records the first phase's label."""
    state_dir = tmp_path / "state"
    out, err, rc = run_engine(
        "next.py", state_dir, "pr-700", CRP_PROTO, "start", "deadbeef",
        env=engine_env,
    )
    assert rc == 0, err
    inf = state_dir / "code-review-pipeline" / "pr-700" / "_instance.yaml"
    data = read_state_yaml(inf)
    assert data["phase"] == "preflight"
    assert data["phase_label"] == "pre-flight gate"


# ---------------------------------------------------------------------------
# Integration tests: advance.py agent-phase label wiring
# ---------------------------------------------------------------------------
# These tests drive the REAL advance.py through the code-review-pipeline
# protocol's `preflight` agent phase and assert that _instance.yaml carries
# the correct phase_label at each terminal.
#
# Setup pattern (mirrors test_override.py / test_multiphase.py):
#   1. run next.py "start" to seed _instance.yaml + preflight.yaml on origin
#   2. run advance.py with controlled verdicts in a fresh clone dir
#   3. clone origin into a verify dir and assert _instance.yaml["phase_label"]
# ---------------------------------------------------------------------------

import json
import subprocess

LIB_PY = ENGINE / "lib.py"
ADVANCE_PY = ENGINE / "advance.py"
NEXT_PY = ENGINE / "next.py"
PID = "code-review-pipeline"


def _crp_env(state_origin, **extra):
    e = dict(os.environ)
    e["ENGINE_LOCAL"] = "1"
    e["STATE_REMOTE"] = str(state_origin)
    e["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    e.update(extra)
    return e


def _run(script, args, env):
    r = subprocess.run(["python3", str(script), *map(str, args)],
                       text=True, capture_output=True, env=env)
    return r.stdout, r.stderr, r.returncode


def _clone(state_origin, target):
    subprocess.run(["git", "clone", "-q", "--branch", "agentic-state",
                    str(state_origin), str(target)], check=True)


def _seed_preflight(state_origin, work, instance, *, state, iteration, head_sha):
    """Seed a preflight phase state + _instance cursor and push to origin."""
    env = _crp_env(state_origin)
    _run(LIB_PY, ["state-checkout", str(work)], env)
    base = work / PID / instance
    base.mkdir(parents=True, exist_ok=True)
    (base / "preflight.yaml").write_text(yaml.safe_dump({
        "protocol": PID, "instance": instance, "state": state,
        "iteration": iteration, "gates": {}, "head_sha": head_sha, "history": [],
    }))
    (base / "_instance.yaml").write_text(yaml.safe_dump({
        "protocol": PID, "instance": instance, "phase": "preflight",
        "head_sha": head_sha, "joined": False,
    }))
    _run(LIB_PY, ["cas-push", str(work), f"seed {instance}"], env)


def _write_json(path, obj):
    path.write_text(json.dumps(obj))
    return path


# Passing verdicts (no failures) → process=done
VERDICTS_PASS = {"results": [
    {"check": "preflight-schema-valid", "pass": True, "on_fail": "iterate", "feedback": ""},
]}

# Block-severity fail (no iterate fails) → process=done, blocking=True
VERDICTS_BLOCK = {"results": [
    {"check": "spec-present", "pass": False, "on_fail": "block", "feedback": "missing spec"},
    {"check": "preflight-schema-valid", "pass": True, "on_fail": "iterate", "feedback": ""},
]}

# Iterate-severity fail at max_iter → process=failed
VERDICTS_ITER_FAIL_EXHAUSTED = {"results": [
    {"check": "preflight-schema-valid", "pass": False, "on_fail": "iterate", "feedback": "bad evidence"},
]}

EVIDENCE_MIN = {"checks": [], "examined": []}


def test_advance_preflight_advance_to_next_sets_review_label(tmp_path):
    """preflight passes → cursor advances to 'review' → phase_label == phase_label_text(proto, 'review').

    This drives advance.py's agent-phase done + nxt branch, which must call
    lib.ensure_phase_label(dir_, pid, instance, proto, pr, nxt) before cas_push."""
    state_origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "agentic-state", str(state_origin)],
                   check=True)
    instance = "pr-801"

    # 1. Seed preflight at iteration 1 → push to origin
    _seed_preflight(state_origin, tmp_path / "seed", instance,
                    state="preflight", iteration=1, head_sha="sha-adv")

    # 2. Run advance.py with passing verdicts (conclude hook will return neutral/no-block)
    v = _write_json(tmp_path / "verdicts.json", VERDICTS_PASS)
    ev = _write_json(tmp_path / "evidence.json", EVIDENCE_MIN)
    env = _crp_env(state_origin, PHASE="preflight", PR="801", PR_HEAD_SHA="sha-adv")
    _, err, rc = _run(ADVANCE_PY, [tmp_path / "adv", instance, CRP_PROTO, v, ev], env)
    assert rc == 0, f"advance.py failed: {err}"

    # 3. Verify _instance.yaml on origin has the "review" phase label
    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load(
        (tmp_path / "verify" / PID / instance / "_instance.yaml").read_text()
    )
    proto_dict = json.load(open(CRP_PROTO))
    expected = lib.phase_label_text(proto_dict, "review")
    assert inf.get("phase_label") == expected, (
        f"expected phase_label={expected!r}, got {inf.get('phase_label')!r}"
    )
    # cursor must have advanced
    assert inf["phase"] == "review"


def test_advance_preflight_exhausted_sets_failed_label(tmp_path):
    """preflight exhausts iterations (iterate-fail at max_iter=2, iter=2) →
    phase_label == '❌ failed'.

    This drives advance.py's agent-phase failed branch guarded by kind=='agent'."""
    state_origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "agentic-state", str(state_origin)],
                   check=True)
    instance = "pr-802"

    # iteration == max_iterations (2) → no iterations remaining → process=failed
    _seed_preflight(state_origin, tmp_path / "seed", instance,
                    state="preflight", iteration=2, head_sha="sha-fail")

    v = _write_json(tmp_path / "verdicts.json", VERDICTS_ITER_FAIL_EXHAUSTED)
    ev = _write_json(tmp_path / "evidence.json", EVIDENCE_MIN)
    env = _crp_env(state_origin, PHASE="preflight", PR="802", PR_HEAD_SHA="sha-fail")
    _, err, rc = _run(ADVANCE_PY, [tmp_path / "adv", instance, CRP_PROTO, v, ev], env)
    assert rc == 0, f"advance.py failed: {err}"

    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load(
        (tmp_path / "verify" / PID / instance / "_instance.yaml").read_text()
    )
    # phase_label must be the terminal failed label
    assert inf.get("phase_label") == "❌ failed", (
        f"expected '❌ failed', got {inf.get('phase_label')!r}"
    )
    # phase state file must also be failed
    pf = yaml.safe_load(
        (tmp_path / "verify" / PID / instance / "preflight.yaml").read_text()
    )
    assert pf["state"] == "failed"


def test_advance_preflight_blocked_sets_blocked_label(tmp_path):
    """preflight concludes blocked (block-severity fail, on_blocked=halt) →
    phase_label == '⛔ blocked'.

    This drives advance.py's is_agent_phase + conclude.blocked + on_blocked=halt branch.
    The conclude hook for code-review-pipeline/preflight is conclude-preflight;
    with EVIDENCE_MIN (no 'spec' key) the hook should return blocked=False, but we
    use VERDICTS_BLOCK which sets blocking=True → conclude-preflight with BLOCKING=1
    returns blocked=True. The preflight state's on_blocked == 'halt' triggers the
    halted branch."""
    state_origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "agentic-state", str(state_origin)],
                   check=True)
    instance = "pr-803"

    _seed_preflight(state_origin, tmp_path / "seed", instance,
                    state="preflight", iteration=1, head_sha="sha-blk")

    v = _write_json(tmp_path / "verdicts.json", VERDICTS_BLOCK)
    ev = _write_json(tmp_path / "evidence.json", EVIDENCE_MIN)
    env = _crp_env(state_origin, PHASE="preflight", PR="803", PR_HEAD_SHA="sha-blk")
    _, err, rc = _run(ADVANCE_PY, [tmp_path / "adv", instance, CRP_PROTO, v, ev], env)
    assert rc == 0, f"advance.py failed: {err}"

    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load(
        (tmp_path / "verify" / PID / instance / "_instance.yaml").read_text()
    )
    assert inf.get("phase_label") == "⛔ blocked", (
        f"expected '⛔ blocked', got {inf.get('phase_label')!r}"
    )


# ---------------------------------------------------------------------------
# Integration tests: join.py phase-label wiring
# ---------------------------------------------------------------------------
# These tests drive the REAL join.py and assert that _instance.yaml carries
# the correct phase_label at each of join's three terminals:
#   1. join → opens following gate  (code-review-pipeline: review→approval)
#   2. join finalizes done          (multi-grumpy: all branches done)
#   3. join finalizes failed        (multi-grumpy: one branch failed)
#
# Seeding pattern mirrors test_join.py seed() and test_gate.py
# _seed_review_all_done(): state-checkout → write YAML files → cas-push,
# then run join.py in a fresh workdir, then clone origin to verify.
# ---------------------------------------------------------------------------

JOIN_PY = ENGINE / "join.py"
MG_PROTO = ROOT / ".github/agent-factory/protocols/multi-grumpy/protocol.json"
MG_PID = "multi-grumpy"

CRP_REVIEW_BRANCHES = [
    b["id"]
    for s in json.load(open(CRP_PROTO))["states"]
    if s["id"] == "review"
    for b in s["branches"]
]


def _join_env(state_origin, **extra):
    e = dict(os.environ)
    e["ENGINE_LOCAL"] = "1"
    e["STATE_REMOTE"] = str(state_origin)
    e["GITHUB_REPOSITORY"] = "golivax/agentic-protocol-poc"
    e.update(extra)
    return e


def _seed_mg(state_origin, work, instance, grumpy_state, security_state):
    """Seed multi-grumpy per-branch state + _instance.yaml and push."""
    env = _join_env(state_origin)
    _run(LIB_PY, ["state-checkout", str(work)], env)
    base = work / MG_PID / instance
    base.mkdir(parents=True, exist_ok=True)
    (base / "grumpy.yaml").write_text(json.dumps({
        "protocol": MG_PID, "instance": instance, "state": grumpy_state,
        "iteration": 1, "gates": {}, "history": [],
    }))
    (base / "security.yaml").write_text(json.dumps({
        "protocol": MG_PID, "instance": instance, "state": security_state,
        "iteration": 1, "gates": {}, "history": [],
    }))
    (base / "_instance.yaml").write_text(json.dumps({
        "protocol": MG_PID, "instance": instance, "head_sha": "jsha",
        "joined": False,
    }))
    _run(LIB_PY, ["cas-push", str(work), f"seed {instance} g={grumpy_state} s={security_state}"], env)


def _seed_crp_review_done(state_origin, work, instance):
    """Seed code-review-pipeline with all review branches done + _instance cursor."""
    env = _join_env(state_origin)
    _run(LIB_PY, ["state-checkout", str(work)], env)
    base = work / PID / instance
    base.mkdir(parents=True, exist_ok=True)
    (base / "_instance.yaml").write_text(yaml.safe_dump({
        "protocol": PID, "instance": instance, "phase": "review",
        "head_sha": "jsha", "joined": False, "status_comment_id": 9,
    }))
    for b in CRP_REVIEW_BRANCHES:
        (base / f"review.{b}.yaml").write_text(yaml.safe_dump({
            "protocol": PID, "instance": instance, "state": "done",
            "iteration": 1, "gates": {}, "head_sha": "jsha", "history": [],
        }))
    _run(LIB_PY, ["cas-push", str(work), f"seed {instance}"], env)


def test_join_opens_gate_sets_gate_phase_label(tmp_path):
    """join (code-review-pipeline) clears all review branches done → opens 'approval' gate.
    _instance.yaml phase_label must equal phase_label_text(proto, 'approval').

    This drives the `if gns and gns.get("kind") == "gate":` branch in join.py
    which now calls lib.ensure_phase_label(..., gate_next) before cas_push."""
    state_origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "agentic-state", str(state_origin)],
                   check=True)
    instance = "pr-901"
    _seed_crp_review_done(state_origin, tmp_path / "seed", instance)

    env = _join_env(state_origin, PR="901", PR_HEAD_SHA="jsha")
    r = subprocess.run(
        ["python3", str(JOIN_PY), str(tmp_path / "w"), instance, str(CRP_PROTO)],
        text=True, capture_output=True, env=env,
    )
    assert r.returncode == 0, r.stderr

    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load(
        (tmp_path / "verify" / PID / instance / "_instance.yaml").read_text()
    )
    # cursor must have advanced to the gate
    assert inf.get("phase") == "approval", f"phase={inf.get('phase')!r}"
    assert inf.get("joined") is True

    proto_dict = json.load(open(CRP_PROTO))
    expected = lib.phase_label_text(proto_dict, "approval")
    assert inf.get("phase_label") == expected, (
        f"expected phase_label={expected!r}, got {inf.get('phase_label')!r}"
    )


def test_join_finalizes_done_sets_done_label(tmp_path):
    """join (multi-grumpy) all branches done → aggregate success.
    _instance.yaml phase_label must be '✅ done'.

    This drives the finalize tail in join.py where concl=='success', which now
    calls lib.ensure_phase_label(..., 'done') before cas_push."""
    state_origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "agentic-state", str(state_origin)],
                   check=True)
    instance = "pr-902"
    _seed_mg(state_origin, tmp_path / "seed", instance, "done", "done")

    env = _join_env(state_origin, PR="902", PR_HEAD_SHA="jsha")
    r = subprocess.run(
        ["python3", str(JOIN_PY), str(tmp_path / "w"), instance, str(MG_PROTO)],
        text=True, capture_output=True, env=env,
    )
    assert r.returncode == 0, r.stderr

    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load(
        (tmp_path / "verify" / MG_PID / instance / "_instance.yaml").read_text()
    )
    assert inf.get("joined") is True
    assert inf.get("phase_label") == "✅ done", (
        f"expected '✅ done', got {inf.get('phase_label')!r}"
    )


def test_join_finalizes_failed_sets_failed_label(tmp_path):
    """join (multi-grumpy) one branch failed → aggregate failure.
    _instance.yaml phase_label must be '❌ failed'.

    This drives the finalize tail in join.py where concl=='failure', which now
    calls lib.ensure_phase_label(..., 'failed') before cas_push."""
    state_origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "agentic-state", str(state_origin)],
                   check=True)
    instance = "pr-903"
    _seed_mg(state_origin, tmp_path / "seed", instance, "done", "failed")

    env = _join_env(state_origin, PR="903", PR_HEAD_SHA="jsha")
    r = subprocess.run(
        ["python3", str(JOIN_PY), str(tmp_path / "w"), instance, str(MG_PROTO)],
        text=True, capture_output=True, env=env,
    )
    assert r.returncode == 0, r.stderr

    _clone(state_origin, tmp_path / "verify")
    inf = yaml.safe_load(
        (tmp_path / "verify" / MG_PID / instance / "_instance.yaml").read_text()
    )
    assert inf.get("joined") is True
    assert inf.get("phase_label") == "❌ failed", (
        f"expected '❌ failed', got {inf.get('phase_label')!r}"
    )


# ---------------------------------------------------------------------------
# End-to-end phase-label progression + v1 regression guard (Task 7)
# ---------------------------------------------------------------------------

GRUMPY_PROTO = ROOT / ".github/agent-factory/protocols/grumpy/protocol.json"


def test_phase_advance_relabels(engine_env, tmp_path):
    """Driving the cursor to the next phase via advance-phase relabels the PR."""
    # Each run_engine call clones fresh from origin into its own dir.
    state_dir = tmp_path / "state1"
    # start → preflight
    _, err, rc = run_engine("next.py", state_dir, "pr-701", CRP_PROTO,
                            "start", "cafe1234", env=engine_env)
    assert rc == 0, err
    inf = state_dir / "code-review-pipeline" / "pr-701" / "_instance.yaml"
    assert read_state_yaml(inf)["phase_label"] == "pre-flight gate"

    # advance-phase to the review fanout (orchestrator would set PHASE=review);
    # use a fresh clone dir — state_checkout always git-clones, can't reuse.
    env2 = dict(engine_env)
    env2["PHASE"] = "review"
    state_dir2 = tmp_path / "state2"
    _, err, rc = run_engine("next.py", state_dir2, "pr-701", CRP_PROTO,
                            "advance-phase", "cafe1234", env=env2)
    assert rc == 0, err
    inf2 = state_dir2 / "code-review-pipeline" / "pr-701" / "_instance.yaml"
    assert read_state_yaml(inf2)["phase_label"] == "review"


def test_v1_grumpy_records_no_phase_label(engine_env, tmp_path):
    """The single-agent v1 path has no _instance.yaml → no phase label, and the
    state file it writes carries no phase_label key (byte-identical baseline)."""
    state_dir = tmp_path / "state"
    _, err, rc = run_engine("next.py", state_dir, "pr-702", GRUMPY_PROTO,
                            "start", "f00dface", env=engine_env)
    assert rc == 0, err
    # no instance file at all for v1
    assert not (state_dir / "grumpy-review" / "pr-702" / "_instance.yaml").exists()
    # v1 single-agent path: state file lives at <pid>/<instance>.yaml (no subdir)
    sf = state_dir / "grumpy-review" / "pr-702.yaml"
    assert "phase_label" not in read_state_yaml(sf)


# ---------------------------------------------------------------------------
# Live-path (no ENGINE_LOCAL) tests for ensure_phase_label + apply_setup_label
# These tests monkeypatch lib.subprocess.run with a recorder and verify that
# the exact `gh` calls — label create, pr edit --remove-label, pr edit
# --add-label — are issued in the right order and with the right arguments.
# No real `gh` is invoked because subprocess.run is patched.
# ---------------------------------------------------------------------------

import types


class _FakeProc:
    """Minimal subprocess.CompletedProcess stand-in returned by the recorder."""
    returncode = 0
    stderr = ""
    stdout = ""


def _make_recorder():
    """Return (recorder_fn, recorded_calls_list).
    recorder_fn is a drop-in for subprocess.run; it appends each argv list."""
    calls = []

    def recorder(argv, **kwargs):
        calls.append(list(argv))
        return _FakeProc()

    return recorder, calls


def test_ensure_phase_label_live_removal_set(tmp_path, monkeypatch):
    """ensure_phase_label (live path, no ENGINE_LOCAL) with a prev label and a new
    label: must issue --remove-label for BOTH prev AND setup_text (if they differ
    from the new label) and then create + add the new label, then write phase_label
    to _instance.yaml.

    Asserts:
    - removal set is exactly {prev_label, setup_text} (both differ from new)
    - gh label create is called for new
    - gh pr edit --add-label is called for new
    - _instance.yaml["phase_label"] updated to new label text
    """
    monkeypatch.delenv("ENGINE_LOCAL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

    proto = {
        "name": "p",
        "states": [{"id": "review", "kind": "agent", "label": "Code Review"}],
    }
    setup_text = lib.phase_label_text(proto, "setup")   # "⚙ setup"
    prev_label = "pre-flight gate"
    new_label = lib.phase_label_text(proto, "review")   # "Code Review"

    # Write _instance.yaml with an existing prev label
    inf = tmp_path / "p" / "pr-10" / "_instance.yaml"
    inf.parent.mkdir(parents=True, exist_ok=True)
    yaml.safe_dump({"protocol": "p", "instance": "pr-10", "phase_label": prev_label}, inf.open("w"))

    recorder, calls = _make_recorder()
    monkeypatch.setattr(lib.subprocess, "run", recorder)

    lib.ensure_phase_label(str(tmp_path), "p", "pr-10", proto, "10", "review")

    # Collect gh pr edit --remove-label targets
    removed = set()
    for argv in calls:
        if "pr" in argv and "edit" in argv and "--remove-label" in argv:
            idx = argv.index("--remove-label")
            removed.add(argv[idx + 1])

    # Both prev and setup_text must have been removed (neither equals new_label)
    assert prev_label in removed, f"expected {prev_label!r} in removed={removed}"
    assert setup_text in removed, f"expected {setup_text!r} in removed={removed}"

    # gh label create must have been called for new_label
    create_calls = [a for a in calls if "label" in a and "create" in a]
    assert any(new_label in a for a in create_calls), (
        f"gh label create not found for {new_label!r}: {create_calls}"
    )

    # gh pr edit --add-label must have been called for new_label
    add_calls = [a for a in calls if "pr" in a and "edit" in a and "--add-label" in a]
    assert any(new_label in a for a in add_calls), (
        f"gh pr edit --add-label not found for {new_label!r}: {add_calls}"
    )

    # _instance.yaml must record the new label
    updated = yaml.safe_load(inf.read_text())
    assert updated["phase_label"] == new_label, (
        f"expected phase_label={new_label!r}, got {updated.get('phase_label')!r}"
    )


def test_ensure_phase_label_live_no_remove_when_prev_equals_new(tmp_path, monkeypatch):
    """ensure_phase_label is a no-op when prev == new — no gh calls at all."""
    monkeypatch.delenv("ENGINE_LOCAL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

    proto = {"name": "p", "states": [{"id": "review", "kind": "agent", "label": "Code Review"}]}
    current_label = lib.phase_label_text(proto, "review")  # "Code Review"

    inf = tmp_path / "p" / "pr-11" / "_instance.yaml"
    inf.parent.mkdir(parents=True, exist_ok=True)
    yaml.safe_dump({"protocol": "p", "instance": "pr-11", "phase_label": current_label},
                   inf.open("w"))

    recorder, calls = _make_recorder()
    monkeypatch.setattr(lib.subprocess, "run", recorder)

    lib.ensure_phase_label(str(tmp_path), "p", "pr-11", proto, "11", "review")

    assert calls == [], f"expected no gh calls, got: {calls}"


def test_apply_setup_label_live(tmp_path, monkeypatch):
    """apply_setup_label (live path) must issue gh label create + gh pr edit --add-label
    for the setup label text."""
    monkeypatch.delenv("ENGINE_LOCAL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

    proto = {"name": "p", "states": []}
    setup_text = lib.phase_label_text(proto, "setup")  # "⚙ setup"

    recorder, calls = _make_recorder()
    monkeypatch.setattr(lib.subprocess, "run", recorder)

    lib.apply_setup_label(proto, "42")

    # gh label create must be called
    create_calls = [a for a in calls if "label" in a and "create" in a]
    assert any(setup_text in a for a in create_calls), (
        f"gh label create not called for {setup_text!r}: {create_calls}"
    )

    # gh pr edit --add-label must be called for setup_text
    add_calls = [a for a in calls if "pr" in a and "edit" in a and "--add-label" in a]
    assert any(setup_text in a for a in add_calls), (
        f"gh pr edit --add-label not called for {setup_text!r}: {add_calls}"
    )
