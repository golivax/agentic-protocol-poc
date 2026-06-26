"""Full e2e oracle walk for recover-mental-model via NODE_PATH, asserting the
persisted state at every step.

Walk:
  start
  → advance recover.legion           (flat leg, pass)
  → advance recover.codeset          (flat leg, pass)
  → advance recover.socratic.phase1  (sub-pipeline first step, emits questions)
  → answer the answering gate (/answer q1: ...)  — auto-detected by _find_open_gate
  → continue recover.socratic.phase2 (seeded after gate advance)
  → advance recover.socratic.phase2  (pass)
  → join.py (top, no NODE_PATH)
  → continue combine                 (runs the push-mental-model merge hook)
  → assert _instance joined:true, phase=combine

State-path (single-phase fanout) drops the leading 'recover' id:
  recover.legion             → legion.yaml
  recover.codeset            → codeset.yaml
  recover.socratic           → socratic.yaml  (cursor)
  recover.socratic.phase1    → socratic.phase1.yaml
  recover.socratic.answering → socratic.answering.yaml  (gate)
  recover.socratic.phase2    → socratic.phase2.yaml
"""

import json
import pathlib
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT / ".github/agent-factory/engine"
PROTO = ROOT / ".github/agent-factory/protocols/recover-mental-model/protocol.json"

NEXT = ENG / "next.py"
ADVANCE = ENG / "advance.py"
JOIN = ENG / "join.py"

LEGION = {"run_id": "r", "files": [
    {"path": "CODEBASE.md"}, {"path": "codebase/index.jsonl"},
    {"path": "codebase/symbols.json"}, {"path": "config/directory-mappings.yaml"}]}
CODESET = {"run_id": "r", "files": [
    {"path": "AGENTS.md"}, {"path": ".claude/docs/knowledge.json"},
    {"path": ".claude/docs/get_context.py"}]}
PHASE1 = {"run_id": "r", "questions": [{"id": "q1", "text": "Why this change?"}],
          "files": [{"path": "QUESTION_TREE-x.adoc"}, {"path": "OPEN_QUESTIONS-x.adoc"}]}
PHASE2 = {"run_id": "r", "files": [
    {"path": "docs/specs/prd-x.adoc"}, {"path": "docs/specs/use-cases-x.adoc"},
    {"path": "docs/specs/adrs/x-adr-001-y.adoc"}, {"path": "docs/arc42/arc42-x.adoc"}]}


def _yaml(p):
    return yaml.safe_load(open(p))


def test_recover_unified_e2e(engine_env, tmp_path):
    base = dict(engine_env, PR_HEAD_SHA="sha2", AGENT_RUN_ID="r")

    def run(script, *args, **env_extra):
        e = dict(base); e.update(env_extra)
        r = subprocess.run(["python3", str(script), *map(str, args)],
                           text=True, capture_output=True, env=e)
        assert r.returncode == 0, f"{script.name} {args} failed:\n{r.stderr}"
        return r

    def reclone(tag):
        d = tmp_path / f"rc-{tag}"
        subprocess.run(["git", "clone", "-q", "-b", "agentic-state",
                        engine_env["STATE_REMOTE"], str(d)], check=True)
        return d / "recover-mental-model" / "pr-1"

    v = tmp_path / "v.json"
    v.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}]}))

    def adv(node, evidence):
        ev = tmp_path / f"ev-{node.replace('.', '_')}.json"
        ev.write_text(json.dumps(evidence))
        return run(ADVANCE, tmp_path / f"s-{node.replace('.', '_')}", "pr-1", PROTO, v, ev,
                   NODE_PATH=node)

    # 1. start → seed the three legs
    r1 = run(NEXT, tmp_path / "s1", "pr-1", PROTO, "start", "sha2")
    assert json.loads(r1.stdout)["action"] == "run-fanout"
    f1 = reclone("1")
    for leg in ("legion.yaml", "codeset.yaml", "socratic.yaml"):
        assert (f1 / leg).is_file(), f"{leg} not seeded after start"
    assert _yaml(f1 / "_instance.yaml").get("joined") is False

    # 2. flat legs done
    adv("recover.legion", LEGION)
    adv("recover.codeset", CODESET)
    f2 = reclone("2")
    assert _yaml(f2 / "legion.yaml")["state"] == "done"
    assert _yaml(f2 / "codeset.yaml")["state"] == "done"

    # join now should wait (socratic still in-flight)
    rj_early = run(JOIN, tmp_path / "sj0", "pr-1", PROTO)
    assert "not all terminal" in rj_early.stderr
    assert _yaml(reclone("je") / "_instance.yaml").get("joined") is not True

    # 3. socratic phase1 → emits questions, gate opens
    adv("recover.socratic.phase1", PHASE1)
    f3 = reclone("3")
    cur3 = _yaml(f3 / "socratic.yaml")
    assert cur3["sub_state"] == "answering", cur3
    assert cur3["state"] == "recover", cur3
    gate3 = _yaml(f3 / "socratic.answering.yaml")
    assert gate3["gates"]["state"] == "open"
    assert gate3["gates"]["questions"][0]["id"] == "q1"

    # 4. /answer closes the gate → cursor advances to phase2
    run(NEXT, tmp_path / "s4", "pr-1", PROTO, "answer",
        ANSWER_BODY="/answer q1: because it is safe", ANSWER_ACTOR="alice")
    f4 = reclone("4")
    assert _yaml(f4 / "socratic.yaml")["sub_state"] == "phase2"
    assert _yaml(f4 / "socratic.answering.yaml")["gates"]["state"] == "answered"
    # do_answer must NOT pre-seed phase2 (the continue seeds it)
    assert not (f4 / "socratic.phase2.yaml").is_file()

    # 5. continue → seeds phase2, run-agent with inputs
    r5 = run(NEXT, tmp_path / "s5", "pr-1", PROTO, "continue",
             NODE_PATH="recover.socratic.phase2")
    act5 = json.loads(r5.stdout)
    assert act5["action"] == "run-agent" and act5.get("path") == "recover.socratic.phase2"
    assert {"tree", "answers"} <= {i["as"] for i in act5.get("inputs", [])}
    assert (reclone("5") / "socratic.phase2.yaml").is_file()

    # 6. phase2 done → sub-pipeline ends → fire join
    r6 = adv("recover.socratic.phase2", PHASE2)
    assert "event_type=protocol-join" in r6.stderr
    assert _yaml(reclone("6") / "socratic.yaml")["state"] == "done"

    # 7. join → all three legs done → advance to combine
    rj = run(JOIN, tmp_path / "s7", "pr-1", PROTO)
    assert "event_type=protocol-continue" in rj.stderr
    assert "client_payload[path]=combine" in rj.stderr
    inst7 = _yaml(reclone("7") / "_instance.yaml")
    assert inst7.get("joined") is True and inst7.get("phase") == "combine"

    # 8. continue combine → runs the merge hook + finalizes
    r8 = run(NEXT, tmp_path / "s8", "pr-1", PROTO, "continue", NODE_PATH="combine")
    assert json.loads(r8.stdout).get("reason") == "merge:combine"
    final = _yaml(reclone("final") / "_instance.yaml")
    assert final.get("joined") is True and final.get("phase") == "combine"
