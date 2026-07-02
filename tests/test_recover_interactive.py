"""recover-mental-model-interactive: the issue-gated socratic answering path.

The interactive protocol is identical to recover-mental-model except socratic
`answering` is a human GATE on a dedicated issue (channel: issue) instead of an
agent. Covers the issue helpers, the issue-channel gate open, and a full ENGINE_LOCAL
walk: start → legion/codeset/ubiq → phase1 (emits questions) → answering issue gate
opens → /answer → gate answered (+ issue closed) → phase2 → join → combine.
"""
import importlib, json, os, subprocess, sys
from pathlib import Path
from conftest import ENGINE, PROTOCOLS, run_engine, read_state_yaml

sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")

PROTO = PROTOCOLS / "recover-mental-model-interactive" / "protocol.json"

LEGION = {"run_id": "r", "files": [
    {"path": "CODEBASE.md"}, {"path": "codebase/index.jsonl"},
    {"path": "codebase/symbols.json"}, {"path": "config/directory-mappings.yaml"}]}
CODESET = {"run_id": "r", "files": [
    {"path": "AGENTS.md"}, {"path": ".claude/docs/knowledge.json"},
    {"path": ".claude/docs/get_context.py"}]}
UBIQ = {"run_id": "r", "files": [{"path": "CONTEXT.md"}]}
PHASE1 = {"run_id": "r", "questions": [{"id": "q1", "text": "Why this design? (Architect)"}],
          "files": [{"path": "QUESTION_TREE-x.adoc"}, {"path": "OPEN_QUESTIONS-x.adoc"}]}
PHASE2 = {"run_id": "r", "files": [
    {"path": "docs/specs/prd-x.adoc"}, {"path": "docs/specs/use-cases-x.adoc"},
    {"path": "docs/specs/adrs/x-adr-001-y.adoc"}, {"path": "docs/arc42/arc42-x.adoc"}]}


# ─── issue helpers + issue-channel gate (ENGINE_LOCAL: log-only, no network) ──

def test_create_close_issue_engine_local(monkeypatch):
    monkeypatch.setenv("ENGINE_LOCAL", "1")
    assert lib.create_issue("t", "b") == "0"   # stub number under ENGINE_LOCAL
    lib.close_issue("0", "done")               # must not raise


def test_issue_question_body_has_marker_and_yaml():
    body = lib.issue_question_body("recover-mental-model-interactive", "ui-x",
                                   "socratic.answering",
                                   [{"id": "q1", "text": "Why?"}])
    assert "agentic-mm: protocol=recover-mental-model-interactive instance=ui-x" in body
    assert "```yaml" in body and "id: q1" in body


def test_open_gate_issue_channel(tmp_path, engine_env):
    for k, v in engine_env.items():
        os.environ[k] = v
    lib.STATE_REMOTE = engine_env["STATE_REMOTE"]
    d = str(tmp_path / "s")
    lib.state_checkout(d)
    inst = lib.instance_file(d, "p", "ui-x")
    os.makedirs(os.path.dirname(inst), exist_ok=True)
    lib.dump_yaml(inst, {"protocol": "p", "instance": "ui-x", "joined": False})
    qs = [{"id": "q1", "text": "Why?"}]
    lib.open_gate(d, "p", "ui-x", str(PROTO), "answering", "sha", "ui-x",
                  questions=qs, path=["socratic", "answering"], channel="issue")
    gf = read_state_yaml(lib.state_file(d, "p", "ui-x", path=["socratic", "answering"]))
    assert gf["gates"]["state"] == "open"
    assert gf["gates"]["channel"] == "issue"
    assert gf["gates"]["issue"] == "0"          # stub from ENGINE_LOCAL create_issue
    assert gf["gates"]["questions"] == qs


# ─── full ENGINE_LOCAL walk ──────────────────────────────────────────────────

def test_interactive_e2e(tmp_path, engine_env):
    base = dict(engine_env, PR_HEAD_SHA="sha", AGENT_RUN_ID="r")
    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))

    def run(script, *args, **env_extra):
        e = dict(base); e.update(env_extra)
        r = subprocess.run(["python3", str(ENGINE / script), *map(str, args)],
                           text=True, capture_output=True, env=e)
        assert r.returncode == 0, f"{script} {args} failed:\n{r.stderr}"
        return r

    def reclone(tag):
        d = tmp_path / f"rc-{tag}"
        subprocess.run(["git", "clone", "-q", "-b", "agentic-state",
                        engine_env["STATE_REMOTE"], str(d)], check=True)
        return d / "recover-mental-model-interactive" / "pr-1"

    def adv(node, evidence):
        ev = tmp_path / f"ev-{node.replace('.', '_')}.json"
        ev.write_text(json.dumps(evidence))
        return run("advance.py", tmp_path / f"s-{node.replace('.', '_')}", "pr-1", PROTO,
                   passv, ev, NODE_PATH=node)

    run("next.py", tmp_path / "s1", "pr-1", PROTO, "start", "sha")
    adv("recover.legion", LEGION)
    adv("recover.codeset", CODESET)
    adv("recover.ubiquitous-language", UBIQ)

    # phase1 emits questions → answering ISSUE gate opens
    adv("recover.socratic.phase1", PHASE1)
    f = reclone("gate")
    cur = read_state_yaml(f / "socratic.yaml")
    assert cur["sub_state"] == "answering", cur
    gate = read_state_yaml(f / "socratic.answering.yaml")
    assert gate["gates"]["state"] == "open"
    assert gate["gates"]["channel"] == "issue"
    assert gate["gates"]["issue"] == "0"
    assert gate["gates"]["questions"][0]["id"] == "q1"

    # /answer on the issue → gate answered, cursor → phase2 (issue closed under
    # ENGINE_LOCAL is a log-only no-op; do_answer must not crash).
    r = run("next.py", tmp_path / "s-ans", "pr-1", PROTO, "answer",
            ANSWER_BODY="/answer q1: because it is layered", ANSWER_ACTOR="alice")
    assert "[ENGINE_LOCAL] close issue #0" in r.stderr   # protocol auto-closed the issue
    f2 = reclone("ans")
    assert read_state_yaml(f2 / "socratic.yaml")["sub_state"] == "phase2"
    assert read_state_yaml(f2 / "socratic.answering.yaml")["gates"]["state"] == "answered"

    # continue → seeds phase2 (run-agent with tree+answers), then advance phase2
    r5 = run("next.py", tmp_path / "s-cont", "pr-1", PROTO, "continue",
             NODE_PATH="recover.socratic.phase2")
    act = json.loads(r5.stdout)
    assert act["action"] == "run-agent" and act["path"] == "recover.socratic.phase2"
    assert {"tree", "answers"} <= {i["as"] for i in act.get("inputs", [])}

    r6 = adv("recover.socratic.phase2", PHASE2)
    assert "event_type=protocol-join" in r6.stderr

    rj = run("join.py", tmp_path / "s-join", "pr-1", PROTO, PR="1")
    assert "client_payload[path]=combine" in rj.stderr
    inst = read_state_yaml(reclone("join") / "_instance.yaml")
    assert inst.get("joined") is True and inst.get("phase") == "combine"
