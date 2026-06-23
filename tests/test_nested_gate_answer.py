import json
import subprocess

import yaml

from conftest import FIXTURES, run_engine, read_state_yaml

PROTO = FIXTURES / "gate-deep/protocol.json"


def _seed_open_gate(work, branch_seq_file, gate_id, questions):
    """Seed gate-deep/pr-1 state so the engine sees the inner fanout in flight
    with the named nested gate OPEN as the live sub-state of its sequence.

    Single-phase → state_path drops the leading 'outer' id, so files are:
      B.yaml                        (top branch B cursor → sub_state 'inner')
      <branch_seq_file>             (nested sequence cursor → sub_state = the gate)
      <branch_seq_file>.<gate>.yaml (the gate state file, gates.state=open)
    """
    base = work / "gate-deep" / "pr-1"
    base.mkdir(parents=True, exist_ok=True)
    (base / "B.yaml").write_text(
        "protocol: gate-deep\ninstance: pr-1\nstate: outer\nsub_state: inner\n")
    (base / branch_seq_file).write_text(
        f"protocol: gate-deep\ninstance: pr-1\nstate: inner\nsub_state: {gate_id}\n")
    gate_file = base / branch_seq_file.replace(".yaml", f".{gate_id}.yaml")
    gd = {"protocol": "gate-deep", "instance": "pr-1", "state": "inner",
          "iteration": 1, "head_sha": "deadbeef",
          "gates": {"state": "open", "questions": questions, "history": []}}
    gate_file.write_text(yaml.safe_dump(gd))


def _push_seed(work, env):
    """Commit the seeded `work` tree onto the bare agentic-state origin so a
    fresh next.py state_checkout sees it."""
    remote = env["STATE_REMOTE"]
    subprocess.run(["git", "init", "-q", "-b", "agentic-state", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "seed"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "--force", remote,
                    "agentic-state"], check=True)


def _clone(dest, env):
    subprocess.run(["git", "clone", "-q", env["STATE_REMOTE"], str(dest)], check=True)
    return dest / "gate-deep" / "pr-1"


def _answer_env(engine_env, body):
    e = dict(engine_env)
    e["ANSWER_BODY"] = body
    e["ANSWER_ACTOR"] = "alice"
    e["PR_HEAD_SHA"] = "deadbeef"
    return e


def test_find_open_gate_descends_to_depth5(tmp_path, engine_env):
    _seed_open_gate(tmp_path / "work", "B.inner.C.yaml", "clarify",
                    [{"id": "q1", "text": "db?"}])
    _push_seed(tmp_path / "work", engine_env)
    e = _answer_env(engine_env, "/answer q1: postgres")
    out, err, rc = run_engine("next.py", tmp_path / "dir2", "pr-1", PROTO, "answer",
                              "deadbeef", env=e)
    assert rc == 0, err
    # It FOUND the depth-5 gate (not "no open gate") and accepted full coverage.
    assert "no open question gate" not in (out + err).lower()


def test_nested_answer_advances_cursor_and_dispatches_path(tmp_path, engine_env):
    _seed_open_gate(tmp_path / "work", "B.inner.C.yaml", "clarify",
                    [{"id": "q1", "text": "db?"}])
    _push_seed(tmp_path / "work", engine_env)
    e = _answer_env(engine_env, "/answer q1: postgres")
    out, err, rc = run_engine("next.py", tmp_path / "dir2", "pr-1", PROTO, "answer",
                              "deadbeef", env=e)
    assert rc == 0, err
    after = _clone(tmp_path / "verify", engine_env)
    cur = read_state_yaml(after / "B.inner.C.yaml")
    assert cur["sub_state"] == "wrap"            # advanced to the next sibling
    assert cur["state"] == "inner"               # leg stays in flight (enclosing fanout id)
    gate = read_state_yaml(after / "B.inner.C.clarify.yaml")
    assert gate["gates"]["state"] == "answered"
    # Re-dispatched protocol-continue carrying the next sibling's TREE path:
    assert "client_payload[path]=outer.B.inner.C.wrap" in err


def test_nested_answer_partial_keeps_gate_open(tmp_path, engine_env):
    _seed_open_gate(tmp_path / "work", "B.inner.C.yaml", "clarify",
                    [{"id": "q1", "text": "db?"}, {"id": "q2", "text": "cache?"}])
    _push_seed(tmp_path / "work", engine_env)
    e = _answer_env(engine_env, "/answer q1: postgres")   # q2 missing → partial
    out, err, rc = run_engine("next.py", tmp_path / "dir2", "pr-1", PROTO, "answer",
                              "deadbeef", env=e)
    assert rc == 0, err
    after = _clone(tmp_path / "verify", engine_env)
    gate = read_state_yaml(after / "B.inner.C.clarify.yaml")
    assert gate["gates"]["state"] == "open"      # still open
    cur = read_state_yaml(after / "B.inner.C.yaml")
    assert cur["sub_state"] == "clarify"         # cursor did not move


def test_nested_answer_gate_as_last_fires_nested_join(tmp_path, engine_env):
    _seed_open_gate(tmp_path / "work", "B.inner.E.yaml", "ask",
                    [{"id": "q1", "text": "ok?"}])
    _push_seed(tmp_path / "work", engine_env)
    e = _answer_env(engine_env, "/answer q1: yes")
    out, err, rc = run_engine("next.py", tmp_path / "dir2", "pr-1", PROTO, "answer",
                              "deadbeef", env=e)
    assert rc == 0, err
    after = _clone(tmp_path / "verify", engine_env)
    cur = read_state_yaml(after / "B.inner.E.yaml")
    assert cur["state"] == "done"                # leg terminal
    # Fired the NESTED join carrying the enclosing inner-fanout TREE path:
    assert "event_type=protocol-join" in err
    assert "client_payload[path]=outer.B.inner" in err
