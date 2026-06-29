"""Full e2e oracle walk for recover-mental-model via NODE_PATH, asserting the
persisted state at every step.

The socratic sub-pipeline is fully automated (phase1 → answering → phase2, all
agent steps — no human gate). Each sub-state advance seeds + dispatches the next.

Walk:
  start
  → advance recover.legion            (flat leg, pass)
  → advance recover.codeset           (flat leg, pass)
  → advance recover.socratic.phase1   (sub-pipeline first step → seeds answering)
  → advance recover.socratic.answering(auto-answer step → seeds phase2)
  → advance recover.socratic.phase2   (pass → leg done → fire join)
  → join.py (top, no NODE_PATH)
  → continue combine                  (runs the push-mental-model merge hook)
  → assert _instance joined:true, phase=combine

State-path (single-phase fanout) drops the leading 'recover' id:
  recover.legion              → legion.yaml
  recover.codeset             → codeset.yaml
  recover.socratic            → socratic.yaml  (cursor)
  recover.socratic.phase1     → socratic.phase1.yaml
  recover.socratic.answering  → socratic.answering.yaml
  recover.socratic.phase2     → socratic.phase2.yaml
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
UBIQ = {"run_id": "r", "files": [{"path": "CONTEXT.md"}]}
PHASE1 = {"run_id": "r", "files": [
    {"path": "QUESTION_TREE-x.adoc"}, {"path": "OPEN_QUESTIONS-x.adoc"}]}
ANSWERING = {"run_id": "r", "files": [
    {"path": "QUESTION_TREE-x.adoc"}, {"path": "OPEN_QUESTIONS-x.adoc"}]}
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
    for leg in ("legion.yaml", "codeset.yaml", "ubiquitous-language.yaml", "socratic.yaml"):
        assert (f1 / leg).is_file(), f"{leg} not seeded after start"
    assert _yaml(f1 / "_instance.yaml").get("joined") is False

    # 2. flat legs done
    adv("recover.legion", LEGION)
    adv("recover.codeset", CODESET)
    adv("recover.ubiquitous-language", UBIQ)
    f2 = reclone("2")
    assert _yaml(f2 / "legion.yaml")["state"] == "done"
    assert _yaml(f2 / "codeset.yaml")["state"] == "done"
    assert _yaml(f2 / "ubiquitous-language.yaml")["state"] == "done"

    # join now should wait (socratic still in-flight)
    rj_early = run(JOIN, tmp_path / "sj0", "pr-1", PROTO)
    assert "not all terminal" in rj_early.stderr
    assert _yaml(reclone("je") / "_instance.yaml").get("joined") is not True

    # 3. socratic phase1 → advances cursor to answering (agent→agent, no gate).
    #    advance does NOT pre-seed; the dispatched continue seeds the next sub-state.
    adv("recover.socratic.phase1", PHASE1)
    cur3 = _yaml(reclone("3") / "socratic.yaml")
    assert cur3["sub_state"] == "answering", cur3
    assert cur3["state"] == "recover", cur3

    # 3b. continue → seeds answering + emits run-agent (with the phase1 tree input)
    r3b = run(NEXT, tmp_path / "s3b", "pr-1", PROTO, "continue",
              NODE_PATH="recover.socratic.answering")
    act3b = json.loads(r3b.stdout)
    assert act3b["action"] == "run-agent" and act3b.get("path") == "recover.socratic.answering"
    assert "tree" in {i["as"] for i in act3b.get("inputs", [])}
    assert (reclone("3b") / "socratic.answering.yaml").is_file()

    # 4. socratic answering → advances cursor to phase2
    adv("recover.socratic.answering", ANSWERING)
    assert _yaml(reclone("4") / "socratic.yaml")["sub_state"] == "phase2"

    # 4b. continue → seeds phase2 + emits run-agent (tree + answers inputs)
    r4b = run(NEXT, tmp_path / "s4b", "pr-1", PROTO, "continue",
              NODE_PATH="recover.socratic.phase2")
    act4b = json.loads(r4b.stdout)
    assert act4b["action"] == "run-agent"
    assert {"tree", "answers"} <= {i["as"] for i in act4b.get("inputs", [])}
    assert (reclone("4b") / "socratic.phase2.yaml").is_file()

    # 5. socratic phase2 → sub-pipeline ends → fire join
    r5 = adv("recover.socratic.phase2", PHASE2)
    assert "event_type=protocol-join" in r5.stderr
    assert _yaml(reclone("5") / "socratic.yaml")["state"] == "done"

    # 6. join → all three legs done → advance to combine
    rj = run(JOIN, tmp_path / "s6", "pr-1", PROTO)
    assert "event_type=protocol-continue" in rj.stderr
    assert "client_payload[path]=combine" in rj.stderr
    inst6 = _yaml(reclone("6") / "_instance.yaml")
    assert inst6.get("joined") is True and inst6.get("phase") == "combine"

    # 7. continue combine → runs the merge hook + finalizes
    r7 = run(NEXT, tmp_path / "s7", "pr-1", PROTO, "continue", NODE_PATH="combine")
    assert json.loads(r7.stdout).get("reason") == "merge:combine"
    final = _yaml(reclone("final") / "_instance.yaml")
    assert final.get("joined") is True and final.get("phase") == "combine"
