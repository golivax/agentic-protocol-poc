"""M2a — multi-phase state machine tests.

lib helpers are pure; the next/advance/join tests drive the engine scripts in
ENGINE_LOCAL mode against a self-contained fixture protocol (tests/fixtures/
pipeline-mini): a `gate` agent phase → a single-branch `work` fan-out → `join`.
"""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
PROTOCOLS = ROOT / ".github/agent-factory/protocols"
FIXTURES = ROOT / "tests/fixtures"
MINI = FIXTURES / "pipeline-mini/protocol.json"
GRUMPY = FIXTURES / "single-agent/protocol.json"
MULTI = FIXTURES / "fanout-mini/protocol.json"

sys.path.insert(0, str(ENGINE))
import lib  # noqa: E402


def load(path):
    with open(path) as f:
        return json.load(f)


# --- lib.state_file phase arg ---

def test_state_file_legacy_single_agent():
    assert lib.state_file("/d", "p", "pr-1") == "/d/p/pr-1.yaml"


def test_state_file_legacy_fanout_branch():
    assert lib.state_file("/d", "p", "pr-1", branch="g") == "/d/p/pr-1/g.yaml"


def test_state_file_multiphase_agent():
    assert lib.state_file("/d", "p", "pr-1", phase="gate") == "/d/p/pr-1/gate.yaml"


def test_state_file_multiphase_fanout_branch():
    assert lib.state_file("/d", "p", "pr-1", branch="g", phase="work") == "/d/p/pr-1/work.g.yaml"


# --- protocol introspection ---

def test_is_multiphase_grumpy_false():
    assert lib.is_multiphase(load(GRUMPY)) is False


def test_is_multiphase_multigrumpy_false():
    assert lib.is_multiphase(load(MULTI)) is False


def test_is_multiphase_pipeline_true():
    assert lib.is_multiphase(load(MINI)) is True


def test_phase_states_are_agent_and_fanout_in_order():
    ids = [s["id"] for s in lib.phase_states(load(MINI))]
    assert ids == ["gate", "work"]


def test_phase_states_excludes_join():
    ids = [s["id"] for s in lib.phase_states(load(MINI))]
    assert "join" not in ids


def test_next_phase_id_follows_next():
    assert lib.next_phase_id(load(MINI), "gate") == "work"


def test_next_phase_id_terminal_is_none():
    # `work`.next is "join", a join state — not another phase → None
    assert lib.next_phase_id(load(MINI), "work") is None


def test_next_phase_id_unknown_is_none():
    assert lib.next_phase_id(load(MINI), "does-not-exist") is None


def test_state_by_id():
    assert lib.state_by_id(load(MINI), "join")["kind"] == "join"
    assert lib.state_by_id(load(MINI), "missing") is None


# --- fixture stub sanity (the engine resolves+runs these) ---

MINI_DIR = FIXTURES / "pipeline-mini"


def _run(path, *args, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([str(path), *args], text=True, capture_output=True, env=env)


def test_always_pass_check_abi():
    r = _run(MINI_DIR / "checks/always-pass.py", "ev", "diff", "files")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out == {"check": "always-pass", "pass": True, "feedback": ""}


def test_conclude_gate_clear_by_default(tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"gate": "clear"}))
    r = _run(MINI_DIR / "publish/conclude-gate.py", str(ev), "pr-1",
             env_extra={"BLOCKING": "0", "ENGINE_LOCAL": "1"})
    out = json.loads(r.stdout)
    assert out["blocked"] is False and out["conclusion"] and out["summary"]


def test_conclude_gate_blocked_by_evidence(tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"gate": "blocked"}))
    r = _run(MINI_DIR / "publish/conclude-gate.py", str(ev), "pr-1",
             env_extra={"BLOCKING": "0", "ENGINE_LOCAL": "1"})
    assert json.loads(r.stdout)["blocked"] is True


def test_conclude_gate_blocked_by_env(tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"gate": "clear"}))
    r = _run(MINI_DIR / "publish/conclude-gate.py", str(ev), "pr-1",
             env_extra={"BLOCKING": "1", "ENGINE_LOCAL": "1"})
    assert json.loads(r.stdout)["blocked"] is True


from conftest import state_origin, engine_env  # noqa: F401  (pytest fixtures)


def run_next(work_dir, instance, proto, command, env, phase="", branch="", head=""):
    e = dict(env)
    e["PHASE"] = phase
    e["BRANCH"] = branch
    r = subprocess.run(
        ["python3", str(ENGINE / "next.py"), str(work_dir), instance, str(proto), command, head],
        text=True, capture_output=True, env=e,
    )
    return r


def test_multiphase_start_seeds_cursor_at_first_phase(tmp_path, engine_env):
    work = tmp_path / "state"
    r = run_next(work, "pr-1", MINI, "start", engine_env, head="abc")
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-agent"
    assert action["phase"] == "gate"
    inst = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/_instance.yaml")
    assert inst["phase"] == "gate"
    assert inst["head_sha"] == "abc"
    assert inst["joined"] is False
    gate = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/gate.yaml")
    assert gate["state"] == "gate" and gate["iteration"] == 1


def test_multiphase_start_does_not_seed_later_phases(tmp_path, engine_env):
    work = tmp_path / "state"
    run_next(work, "pr-1", MINI, "start", engine_env, head="abc")
    assert not os.path.exists(str(work) + "/pipeline-mini/pr-1/work.alpha.yaml")


def test_singlephase_grumpy_start_unchanged(tmp_path, engine_env):
    work = tmp_path / "state"
    r = run_next(work, "pr-1", GRUMPY, "start", engine_env, head="abc")
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-agent"
    assert "phase" not in action
    assert os.path.exists(str(work) + "/single-agent/pr-1.yaml")


def test_singlephase_multigrumpy_start_unchanged(tmp_path, engine_env):
    work = tmp_path / "state"
    r = run_next(work, "pr-1", MULTI, "start", engine_env, head="abc")
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-fanout"
    assert "phase" not in action
    assert os.path.exists(str(work) + "/fanout-mini/pr-1/_instance.yaml")
    assert os.path.exists(str(work) + "/fanout-mini/pr-1/grumpy.yaml")


def test_seed_unknown_phase_exits_nonzero(tmp_path, engine_env):
    # advance-phase with a PHASE that isn't a real state → clean non-zero exit
    work = tmp_path / "state"
    r = run_next(work, "pr-1", MINI, "advance-phase", engine_env, phase="nope")
    assert r.returncode != 0
    assert "unknown phase" in r.stderr


def test_phase_continue_resumes_gate(tmp_path, engine_env):
    work1 = tmp_path / "state1"
    run_next(work1, "pr-1", MINI, "start", engine_env, head="abc")
    # Mutate state on disk then push so the second invocation clones the updated state
    sf = str(work1) + "/pipeline-mini/pr-1/gate.yaml"
    data = lib.load_yaml(sf)
    data["iteration"] = 2
    data["history"] = [{"iteration": 1, "feedback": "fix the rubric"}]
    lib.dump_yaml(sf, data)
    subprocess.run(["git", "-C", str(work1), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work1), "-c", "user.name=t", "-c", "user.email=t@t",
             "commit", "-qm", "mutate for test"], check=True)
    subprocess.run(["git", "-C", str(work1), "push", "-q", "origin", "agentic-state"], check=True)
    work2 = tmp_path / "state2"
    r = run_next(work2, "pr-1", MINI, "continue", engine_env, phase="gate")
    assert r.returncode == 0, r.stderr
    action = json.loads(r.stdout)
    assert action["action"] == "run-agent"
    assert action["iteration"] == 2
    assert action["feedback"] == "fix the rubric"
    assert action["phase"] == "gate"


def test_phase_continue_terminal_halts(tmp_path, engine_env):
    work1 = tmp_path / "state1"
    run_next(work1, "pr-1", MINI, "start", engine_env, head="abc")
    # Mutate state to terminal then push so the second invocation clones the updated state
    sf = str(work1) + "/pipeline-mini/pr-1/gate.yaml"
    data = lib.load_yaml(sf)
    data["state"] = "done"
    lib.dump_yaml(sf, data)
    subprocess.run(["git", "-C", str(work1), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work1), "-c", "user.name=t", "-c", "user.email=t@t",
             "commit", "-qm", "mutate for test"], check=True)
    subprocess.run(["git", "-C", str(work1), "push", "-q", "origin", "agentic-state"], check=True)
    work2 = tmp_path / "state2"
    r = run_next(work2, "pr-1", MINI, "continue", engine_env, phase="gate")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["action"] == "halt"


def run_advance(work_dir, instance, proto, verdicts_path, evidence_path, env,
                phase="", branch=""):
    e = dict(env)
    e["PHASE"] = phase
    e["BRANCH"] = branch
    r = subprocess.run(
        ["python3", str(ENGINE / "advance.py"), str(work_dir), instance, str(proto),
         str(verdicts_path), str(evidence_path)],
        text=True, capture_output=True, env=e,
    )
    return r


def _verdicts_pass(p):
    p.write_text(json.dumps({"results": [{"check": "always-pass", "pass": True,
                                          "feedback": "", "on_fail": "iterate"}]}))
    return p


def test_agent_phase_clear_advances_cursor_and_fires_protocol_advance(tmp_path, engine_env):
    # `start` seeds + pushes the gate state to origin; advance re-clones into a
    # fresh dir (state_checkout always clones, mirroring the plan/advance job split).
    run_next(tmp_path / "state1", "pr-1", MINI, "start", engine_env, head="abc")
    work = tmp_path / "state2"
    v = _verdicts_pass(tmp_path / "v.json")
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps({"gate": "clear"}))
    r = run_advance(work, "pr-1", MINI, v, ev, engine_env, phase="gate")
    assert r.returncode == 0, r.stderr
    inst = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/_instance.yaml")
    assert inst["phase"] == "work"
    gate = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/gate.yaml")
    assert gate["state"] == "done"
    assert "protocol-advance" in r.stderr
    assert "work" in r.stderr  # the next phase named in the dispatch intent


def test_agent_phase_blocked_halts_pipeline(tmp_path, engine_env):
    run_next(tmp_path / "state1", "pr-1", MINI, "start", engine_env, head="abc")
    work = tmp_path / "state2"
    v = _verdicts_pass(tmp_path / "v.json")
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps({"gate": "blocked"}))
    r = run_advance(work, "pr-1", MINI, v, ev, engine_env, phase="gate")
    assert r.returncode == 0, r.stderr
    gate = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/gate.yaml")
    assert gate["state"] == "failed"
    inst = lib.load_yaml(str(work) + "/pipeline-mini/pr-1/_instance.yaml")
    assert inst["phase"] == "gate"
    assert "protocol-advance" not in r.stderr


def test_join_reads_phase_prefixed_branch_states_and_finalizes(tmp_path, engine_env):
    # Each run_next clones fresh from origin into its dir; use separate dirs.
    run_next(tmp_path / "state1", "pr-1", MINI, "start", engine_env, head="abc")
    work = tmp_path / "state2"
    run_next(work, "pr-1", MINI, "advance-phase", engine_env, phase="work")
    # mark the single branch leg done (in the seeded work.alpha.yaml) and push
    sf = str(work) + "/pipeline-mini/pr-1/work.alpha.yaml"
    data = lib.load_yaml(sf)
    data["state"] = "done"
    lib.dump_yaml(sf, data)
    inf = str(work) + "/pipeline-mini/pr-1/_instance.yaml"
    inst = lib.load_yaml(inf); inst["phase"] = "work"; lib.dump_yaml(inf, inst)
    # commit+push the mutation so join's state_checkout (clone) sees it
    import subprocess as sp
    sp.run(["git", "-C", str(work), "add", "-A"], check=True, capture_output=True)
    sp.run(["git", "-C", str(work), "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-qm", "leg done"], check=True, capture_output=True)
    sp.run(["git", "-C", str(work), "push", "-q", "origin", "agentic-state"], check=True, capture_output=True)
    # run join in a fresh clone dir
    work2 = tmp_path / "state3"
    e = dict(engine_env); e["PR"] = "1"; e["PR_HEAD_SHA"] = "abc"
    r = subprocess.run(["python3", str(ENGINE / "join.py"), str(work2), "pr-1", str(MINI)],
                       text=True, capture_output=True, env=e)
    assert r.returncode == 0, r.stderr
    inst2 = lib.load_yaml(str(work2) + "/pipeline-mini/pr-1/_instance.yaml")
    assert inst2["joined"] is True


def test_e2e_mini_pipeline_clear_path(tmp_path, engine_env):
    """Full walk: start → advance gate (clear) → advance-phase work → advance alpha → join."""
    inst = "pr-1"

    # 1. start (fresh dir) → seeds _instance.yaml + gate.yaml on origin
    s1 = tmp_path / "s1"
    r1 = run_next(s1, inst, MINI, "start", engine_env, head="abc")
    assert r1.returncode == 0, r1.stderr
    assert lib.load_yaml(str(s1) + "/pipeline-mini/pr-1/_instance.yaml")["phase"] == "gate"

    # 2. advance gate with clear evidence → cursor advances to "work", protocol-advance fired
    s2 = tmp_path / "s2"
    v1 = _verdicts_pass(tmp_path / "v1.json")
    ev1 = tmp_path / "ev1.json"
    ev1.write_text(json.dumps({"gate": "clear"}))
    r2 = run_advance(s2, inst, MINI, v1, ev1, engine_env, phase="gate")
    assert r2.returncode == 0, r2.stderr
    inst2 = lib.load_yaml(str(s2) + "/pipeline-mini/pr-1/_instance.yaml")
    assert inst2["phase"] == "work"
    gate2 = lib.load_yaml(str(s2) + "/pipeline-mini/pr-1/gate.yaml")
    assert gate2["state"] == "done"
    assert "protocol-advance" in r2.stderr

    # 3. advance-phase work (fresh dir) → seeds the work.alpha fan-out leg on origin
    s3 = tmp_path / "s3"
    r3 = run_next(s3, inst, MINI, "advance-phase", engine_env, phase="work")
    assert r3.returncode == 0, r3.stderr
    assert os.path.exists(str(s3) + "/pipeline-mini/pr-1/work.alpha.yaml")

    # 4. advance alpha leg with passing verdicts → leg state == "done", protocol-join fired
    s4 = tmp_path / "s4"
    v2 = _verdicts_pass(tmp_path / "v2.json")
    ev2 = tmp_path / "ev2.json"
    ev2.write_text(json.dumps({}))
    r4 = run_advance(s4, inst, MINI, v2, ev2, engine_env, phase="work", branch="alpha")
    assert r4.returncode == 0, r4.stderr
    alpha4 = lib.load_yaml(str(s4) + "/pipeline-mini/pr-1/work.alpha.yaml")
    assert alpha4["state"] == "done"
    assert "protocol-join" in r4.stderr

    # 5. join (fresh dir) → instance marked joined == True
    s5 = tmp_path / "s5"
    e5 = dict(engine_env)
    e5["PR"] = "1"
    e5["PR_HEAD_SHA"] = "abc"
    r5 = subprocess.run(
        ["python3", str(ENGINE / "join.py"), str(s5), inst, str(MINI)],
        text=True, capture_output=True, env=e5,
    )
    assert r5.returncode == 0, r5.stderr
    inst5 = lib.load_yaml(str(s5) + "/pipeline-mini/pr-1/_instance.yaml")
    assert inst5["joined"] is True


# --- restart resets the WHOLE instance (the /review re-trigger & synchronize bug) ---

def _git_push_all(work_dir, msg):
    subprocess.run(["git", "-C", str(work_dir), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work_dir), "-c", "user.name=t", "-c",
                    "user.email=t@t", "commit", "-qm", msg], check=True)
    subprocess.run(["git", "-C", str(work_dir), "push", "-q", "origin",
                    "agentic-state"], check=True)


def test_multiphase_restart_clears_stale_prior_run_state(tmp_path, engine_env):
    """A second `start` (a `/review` re-trigger) must reset the WHOLE instance,
    not just re-seed the first phase. Stale later-phase state files and instance
    markers (joined / overrides / halted) from the prior run must be cleared and
    head_sha refreshed. The prior run's status comment is ABANDONED, not reused:
    its id is dropped (so a fresh comment is created) and it gets one final
    `superseded` edit. This is the PR #69 bug: the status comment kept showing
    stale `review · grumpy/security` sections after `/review`."""
    # 1) First run: seed gate + instance at an old head, push.
    work1 = tmp_path / "state1"
    run_next(work1, "pr-1", MINI, "start", engine_env, head="oldsha")
    base = str(work1) + "/pipeline-mini/pr-1"
    # 2) Simulate the prior run having advanced to the fan-out phase, been
    #    overridden + halted, and acquired a status comment; push it.
    inst = lib.load_yaml(base + "/_instance.yaml")
    inst["joined"] = True
    inst["overrides"] = [{"phase": "gate", "actor": "x", "reason": "y"}]
    inst["halted"] = {"phase": "gate", "reason": "blocked"}
    inst["status_comment_id"] = 12345
    lib.dump_yaml(base + "/_instance.yaml", inst)
    lib.dump_yaml(base + "/work.alpha.yaml", {
        "protocol": "pipeline-mini", "instance": "pr-1", "state": "work",
        "iteration": 1, "gates": {}, "history": []})
    _git_push_all(work1, "simulate prior run")
    # 3) Second `start` (the /review re-trigger) at a NEW head, fresh clone.
    work2 = tmp_path / "state2"
    r = run_next(work2, "pr-1", MINI, "start", engine_env, head="newsha")
    assert r.returncode == 0, r.stderr
    b2 = str(work2) + "/pipeline-mini/pr-1"
    # Stale later-phase leg gone.
    assert not os.path.exists(b2 + "/work.alpha.yaml")
    inst2 = lib.load_yaml(b2 + "/_instance.yaml")
    assert inst2["joined"] is False
    assert not inst2.get("overrides")
    assert not inst2.get("halted")
    assert inst2["head_sha"] == "newsha"
    # Old comment ABANDONED: its id is dropped so a fresh comment is created,
    # and the old one (12345) got a final "superseded" edit (ENGINE_LOCAL logs it).
    assert "status_comment_id" not in inst2
    assert "[ENGINE_LOCAL] supersede comment 12345" in r.stderr
    assert "Superseded" in r.stderr
    # First phase re-seeded fresh.
    gate = lib.load_yaml(b2 + "/gate.yaml")
    assert gate["state"] == "gate" and gate["iteration"] == 1
