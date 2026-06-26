"""pytest for the recover-mental-model protocol.

Covers the three method legs (legion ∥ codeset ∥ socratic sub-pipeline), their
deterministic checks, the full engine walk under ENGINE_LOCAL, and the
push-mental-model merge hook that assembles + force-pushes the orphan
`_mental_model` branch.
"""
import importlib, json, os, subprocess, sys
from pathlib import Path
from conftest import ENGINE, PROTOCOLS, run_engine, run_check, read_state_yaml

sys.path.insert(0, str(ENGINE))
lib = importlib.import_module("lib")

PROTO_DIR = PROTOCOLS / "recover-mental-model"
PROTO = PROTO_DIR / "protocol.json"
CHECKS = PROTO_DIR / "checks"
HOOK = PROTO_DIR / "publish" / "push-mental-model.py"


# ─── check unit tests ────────────────────────────────────────────────────────

def _run(check_name, evidence_dict, tmp_path):
    ev = tmp_path / f"{check_name}.json"
    ev.write_text(json.dumps(evidence_dict))
    empty = tmp_path / "empty.txt"; empty.write_text("")
    return run_check(CHECKS / f"{check_name}.py", ev, empty, empty)


LEGION_OK = {"run_id": "123", "files": [
    {"path": "CODEBASE.md"}, {"path": "codebase/index.jsonl"},
    {"path": "codebase/symbols.json"}, {"path": "config/directory-mappings.yaml"}]}
CODESET_OK = {"run_id": "123", "files": [
    {"path": "AGENTS.md"}, {"path": "CLAUDE.md"},
    {"path": ".claude/docs/knowledge.json"}, {"path": ".claude/docs/get_context.py"}]}
SOCRATIC2_OK = {"run_id": "123", "files": [
    {"path": "docs/specs/prd-foo.adoc"}, {"path": "docs/specs/use-cases-foo.adoc"},
    {"path": "docs/specs/adrs/foo-adr-001-x.adoc"}, {"path": "docs/arc42/arc42-foo.adoc"}]}


def test_legion_artifacts_pass(tmp_path):
    assert _run("legion-artifacts", LEGION_OK, tmp_path)["pass"] is True


def test_legion_artifacts_fail_missing_file(tmp_path):
    ev = dict(LEGION_OK, files=[{"path": "CODEBASE.md"}])
    r = _run("legion-artifacts", ev, tmp_path)
    assert r["pass"] is False and "symbols.json" in r["feedback"]


def test_legion_artifacts_fail_no_run_id(tmp_path):
    ev = dict(LEGION_OK, run_id="")
    r = _run("legion-artifacts", ev, tmp_path)
    assert r["pass"] is False and "run_id" in r["feedback"]


def test_codeset_artifacts_pass(tmp_path):
    assert _run("codeset-artifacts", CODESET_OK, tmp_path)["pass"] is True


def test_codeset_artifacts_fail_missing(tmp_path):
    ev = dict(CODESET_OK, files=[{"path": "AGENTS.md"}])
    r = _run("codeset-artifacts", ev, tmp_path)
    assert r["pass"] is False and "knowledge.json" in r["feedback"]


def test_socratic_docs_present_pass(tmp_path):
    assert _run("socratic-docs-present", SOCRATIC2_OK, tmp_path)["pass"] is True


def test_socratic_docs_present_fail_missing_adr(tmp_path):
    ev = dict(SOCRATIC2_OK, files=[
        {"path": "docs/specs/prd-foo.adoc"}, {"path": "docs/specs/use-cases-foo.adoc"},
        {"path": "docs/arc42/arc42-foo.adoc"}])
    r = _run("socratic-docs-present", ev, tmp_path)
    assert r["pass"] is False and "adrs" in r["feedback"]


def test_questions_present_pass(tmp_path):
    r = _run("questions-present", {"questions": [{"id": "q1", "text": "Why?"}]}, tmp_path)
    assert r["pass"] is True


def test_questions_present_fail_empty(tmp_path):
    assert _run("questions-present", {"questions": []}, tmp_path)["pass"] is False


def test_answers_coverage_pass(tmp_path):
    r = _run("answers-coverage",
             {"questions": [{"id": "q1"}], "answers": {"q1": "because"}}, tmp_path)
    assert r["pass"] is True


def test_answers_coverage_fail_missing(tmp_path):
    r = _run("answers-coverage",
             {"questions": [{"id": "q1"}, {"id": "q2"}], "answers": {"q1": "y"}}, tmp_path)
    assert r["pass"] is False and "q2" in r["feedback"]


# ─── push-mental-model merge hook ────────────────────────────────────────────

def test_push_mental_model_hook(tmp_path):
    """Stage the three leg trees + inputs, run the hook under ENGINE_LOCAL against a
    bare origin, then clone `_mental_model` and assert the assembled layout."""
    # Bare origin that stands in for the target repo.
    origin = tmp_path / "target.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    workdir = tmp_path / "wd"
    inputs = workdir / "inputs"; inputs.mkdir(parents=True)
    for leg in ("legion", "codeset", "socratic"):
        (inputs / f"{leg}.json").write_text(json.dumps({"run_id": "r", "files": []}))
        tree = workdir / "trees" / leg
        tree.mkdir(parents=True)
        (tree / "FILE.txt").write_text(f"{leg} output\n")
    # a nested file in one tree, to confirm copytree recursion
    (workdir / "trees" / "socratic" / "docs").mkdir()
    (workdir / "trees" / "socratic" / "docs" / "prd.adoc").write_text("= PRD\n")

    env = dict(os.environ)
    env.update(ENGINE_LOCAL="1", MM_TARGET_REMOTE=str(origin),
               PR="7", PR_HEAD_SHA="deadbeef", GITHUB_REPOSITORY="")
    r = subprocess.run(["python3", str(HOOK), str(workdir), "pr-7"],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["conclusion"] == "success", out
    assert "legion" in out["summary"] and "socratic" in out["summary"]

    # Clone the orphan branch and assert the layout.
    view = tmp_path / "view"
    subprocess.run(["git", "clone", "-q", "-b", "_mental_model", str(origin), str(view)],
                   check=True)
    assert (view / "METHODS.txt").is_file()
    assert (view / "legion-map" / "FILE.txt").is_file()
    assert (view / "vibed-codeset" / "FILE.txt").is_file()
    assert (view / "socratic" / "FILE.txt").is_file()
    assert (view / "socratic" / "docs" / "prd.adoc").is_file()
    methods = (view / "METHODS.txt").read_text()
    assert "legion-map" in methods and "vibed-codeset" in methods and "socratic" in methods
    assert "deadbeef" in methods


def test_push_mental_model_hook_no_trees_is_neutral(tmp_path):
    """No leg produced a tree → neutral conclusion, no push attempted."""
    workdir = tmp_path / "wd"; (workdir / "inputs").mkdir(parents=True)
    env = dict(os.environ)
    env.update(ENGINE_LOCAL="1", MM_TARGET_REMOTE=str(tmp_path / "nope.git"), PR="7")
    r = subprocess.run(["python3", str(HOOK), str(workdir), "pr-7"],
                       text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["conclusion"] == "neutral"


# ─── full e2e pipeline ───────────────────────────────────────────────────────

def test_full_pipeline(tmp_path, engine_env):
    """start → legion ∥ codeset ∥ socratic(phase1→answering→phase2) → join → combine."""
    passv = tmp_path / "v.json"
    passv.write_text(json.dumps({"results": [
        {"check": "synthetic-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))

    def adv(node, evidence_dict):
        ev = tmp_path / f"ev-{node.replace('.', '_')}.json"
        ev.write_text(json.dumps(evidence_dict))
        e = dict(engine_env, PR_HEAD_SHA="abc123", AGENT_RUN_ID="r", NODE_PATH=node)
        out, err, rc = run_engine("advance.py", tmp_path / f"dir-{node.replace('.', '_')}",
                                  "pr-1", PROTO, passv, ev, env=e)
        assert rc == 0, f"advance {node} failed:\n{err}"
        return out + err

    def clone():
        work = tmp_path / f"work-{clone.n}"; clone.n += 1
        subprocess.run(["git", "clone", "-q", engine_env["STATE_REMOTE"], str(work)], check=True)
        return work
    clone.n = 0

    # 1. start → seed the fanout
    out, err, rc = run_engine("next.py", tmp_path / "dir-next", "pr-1", PROTO, "start",
                              "abc123", env=engine_env)
    assert rc == 0, f"next start failed:\n{err}"

    # 2. the two flat legs
    adv("recover.legion", LEGION_OK)
    adv("recover.codeset", CODESET_OK)

    # 3. socratic phase1 (emits questions → gate opens)
    adv("recover.socratic.phase1",
        {"run_id": "r", "questions": [{"id": "q1", "text": "Why?"}],
         "files": [{"path": "QUESTION_TREE-x.adoc"}, {"path": "OPEN_QUESTIONS-x.adoc"}]})
    w = clone()
    cur = read_state_yaml(w / "recover-mental-model/pr-1/socratic.yaml")
    assert cur["sub_state"] == "answering", f"expected answering gate, got {cur}"

    # 4. answer the gate
    ea = dict(engine_env, ANSWER_BODY="/answer q1: because reasons",
              ANSWER_ACTOR="alice", PR_HEAD_SHA="abc123")
    out, err, rc = run_engine("next.py", tmp_path / "dir-answer", "pr-1", PROTO, "answer", env=ea)
    assert rc == 0, f"answer failed:\n{err}"
    assert "client_payload[path]=recover.socratic.phase2" in err, (
        f"do_answer must emit a path-form continue to phase2, got:\n{err}")
    w2 = clone()
    cur2 = read_state_yaml(w2 / "recover-mental-model/pr-1/socratic.yaml")
    assert cur2["sub_state"] == "phase2", f"expected phase2, got {cur2}"

    # 5. socratic phase2
    adv("recover.socratic.phase2", SOCRATIC2_OK)

    # 6. join — all three legs done → advance to combine
    ej = dict(engine_env, PR_HEAD_SHA="abc123", PR="1")
    out, err, rc = run_engine("join.py", tmp_path / "dir-join", "pr-1", PROTO, env=ej)
    assert rc == 0, f"join failed:\n{err}"
    jc = out + err
    assert "event_type=protocol-continue" in jc and "client_payload[path]=combine" in jc, (
        f"expected join → protocol-continue path=combine, got:\n{jc}")
    w3 = clone()
    inst = read_state_yaml(w3 / "recover-mental-model/pr-1/_instance.yaml")
    assert inst.get("joined") is True and inst.get("phase") == "combine", inst

    # 7. continue combine → runs the merge hook (neutral here: no real trees to push
    #    under ENGINE_LOCAL, but the hook resolves + finalizes the cursor to done).
    ec = dict(engine_env, PR_HEAD_SHA="abc123", PR="1", NODE_PATH="combine")
    out2, err2, rc2 = run_engine("next.py", tmp_path / "dir-merge", "pr-1", PROTO,
                                 "continue", env=ec)
    assert rc2 == 0, f"merge continue failed:\n{err2}"
    assert json.loads(out2).get("reason") == "merge:combine"
    assert "title=Combined" in (out2 + err2)
    # cursor finalized: instance joined + parked at the combine phase
    inst2 = read_state_yaml(clone() / "recover-mental-model/pr-1/_instance.yaml")
    assert inst2.get("joined") is True and inst2.get("phase") == "combine"


def test_run_merge_hook_resolves_three_legs(tmp_path, engine_env):
    """lib.run_merge_hook materializes all three leg evidence files into inputs/ and
    invokes push-mental-model. Under ENGINE_LOCAL with no staged trees it returns a
    neutral verdict (nothing to push) — but proves resolution + dispatch wiring."""
    for k, v in engine_env.items():
        os.environ[k] = v
    lib.STATE_REMOTE = engine_env["STATE_REMOTE"]
    dir_ = str(tmp_path / "dir")
    lib.state_checkout(dir_)
    base = os.path.join(dir_, "recover-mental-model", "pr-1")
    os.makedirs(base, exist_ok=True)
    for leg, ev in (("legion", LEGION_OK), ("codeset", CODESET_OK)):
        with open(os.path.join(base, f"{leg}.evidence.json"), "w") as f:
            json.dump(ev, f)
    # socratic leg output is its last sub-state (phase2)
    with open(os.path.join(base, "socratic.phase2.evidence.json"), "w") as f:
        json.dump(SOCRATIC2_OK, f)

    proto_path = str(PROTO)
    proto = json.load(open(proto_path))
    merge_state = lib.state_by_id(proto, "combine")
    res = lib.run_merge_hook(dir_, "recover-mental-model", "pr-1", proto_path, merge_state)
    # ENGINE_LOCAL + no staged trees → neutral (no push), but a valid verdict shape.
    assert res["conclusion"] in ("neutral", "success") and res.get("summary")
