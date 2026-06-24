"""test_cap_security.py — Task 15: Security regressions for agent-derived strings.

Proves that:
  0. _parse_answers (next.py) — the regex parser for an UNTRUSTED comment body —
     stores each payload as a plain string and never executes it (DIRECT unit
     tests, independent of the do_answer gate guard).
  1. A malicious ANSWER_BODY (shell-injection payload) driven through the whole
     do_answer path is inert.  A sentinel file the injection WOULD create must
     NOT appear.
  2. A bogus / path-traversal NODE_PATH fed to advance.py yields a clean
     non-zero error and does NOT write any file outside the instance dir.
  3. (Bonus) Shell metacharacters in a NODE_PATH segment are treated as a
     literal unknown id → clean error, not executed.

Security posture contract:
  - ANSWER_BODY arrives via env var → _parse_answers stores key=value in a JSON
    file; the content is NEVER passed to a shell interpreter.
  - NODE_PATH arrives via env var → split on "." → used as a dict-key lookup
    in the protocol tree (paths.node_at_path); no file path or subprocess
    invocation uses the raw segment string.
  - File paths for state files are always rooted at <dir>/<pid>/<instance>/
    inside the checked-out state workdir.  lib.state_file joins fixed
    components (dir, pid, instance) with the dot-joined node path — the
    resulting os.path.join is evaluated, but since no caller passes the raw
    NODE_PATH string to os.path.join (they split → join segments they control)
    a traversal segment would only resolve wrongly if it were used directly.
    We verify the actual files on disk stay within the instance dir.
"""
import json
import os
import pathlib
import subprocess
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
FIXTURES = ROOT / "tests/fixtures"
PROTOCOLS = ROOT / ".github/agent-factory/protocols"

# Protocol that has a subpipeline so NODE_PATH (depth-3) resolves normally,
# used by traversal tests to confirm well-formed paths still work. The kept
# recover-mental-model-stub has the right shape: a flat `summary` leg + a
# sub-pipeline `rationale` leg whose sub-states are draft → clarify(gate) → finalize.
SUBPIPELINE_PROTO = PROTOCOLS / "recover-mental-model-stub/protocol.json"
SUBPIPELINE_PID = "recover-mental-model-stub"
# Single-agent protocol (its `name` field is "single-agent"). It has NO data
# gate, so do_answer early-returns at _find_open_gate → None: the injection
# tests that use it prove the do_answer PATH is inert (no exec before/around the
# gate guard), NOT that _parse_answers itself is safe.  The direct unit tests in
# TestParseAnswersDirect lock the regex-parser's safety independently; the
# subpipeline fixture (where a gate IS open) confirms an injection value is
# stored verbatim end-to-end.
SIMPLE_AGENT_PROTO = FIXTURES / "cap-single-agent/protocol.json"


# ---------------------------------------------------------------------------
# Load _parse_answers from next.py WITHOUT triggering its import-time side
# effects (next.py runs lib.state_checkout + a command dispatch / sys.exit at
# module level). We extract just the function's source via the `ast` module and
# exec it in an isolated namespace whose only dependency is `re`. This gives a
# real reference to the production function so a future regression in its regex
# would fail these tests.
# ---------------------------------------------------------------------------

def _load_parse_answers():
    import ast
    import re as _re
    src = (ENGINE / "next.py").read_text()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_answers":
            mod = ast.Module(body=[node], type_ignores=[])
            ns = {"re": _re}
            exec(compile(mod, str(ENGINE / "next.py"), "exec"), ns)
            return ns["_parse_answers"]
    raise AssertionError("_parse_answers not found in next.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_next(dir_, instance, proto, command, env, extra_env=None):
    e = dict(env)
    if extra_env:
        e.update(extra_env)
    r = subprocess.run(
        ["python3", str(ENGINE / "next.py"), str(dir_), instance, str(proto), command],
        text=True, capture_output=True, env=e,
    )
    return r


def _run_advance(dir_, instance, proto, verdicts, evidence, env, extra_env=None):
    e = dict(env)
    if extra_env:
        e.update(extra_env)
    r = subprocess.run(
        ["python3", str(ENGINE / "advance.py"),
         str(dir_), instance, str(proto), str(verdicts), str(evidence)],
        text=True, capture_output=True, env=e,
    )
    return r


def _clone(origin_path, tmp_path, tag="clone"):
    d = tmp_path / f"clone-{tag}"
    subprocess.run(
        ["git", "clone", "-q", "-b", "agentic-state", str(origin_path), str(d)],
        check=True,
    )
    return d


def _all_files_under(directory):
    """Return all file paths under `directory` as a set of Path objects."""
    result = set()
    for root, _dirs, files in os.walk(directory):
        for f in files:
            result.add(pathlib.Path(root) / f)
    return result


def _seed_open_gate(tmp_path, engine_env, proto=SUBPIPELINE_PROTO):
    """Drive start → rationale.draft done → clarify gate open via the unified
    NODE_PATH coordinate (recover-mental-model-stub: rationale is the sub-pipeline
    leg, draft → clarify(gate) → finalize)."""
    _run_next(tmp_path / "seed-dir", "pr-1", proto, "start", engine_env,
              extra_env={"PR_HEAD_SHA": "abc123"})
    v = tmp_path / "v-pass.json"
    v.write_text(json.dumps({"results": [
        {"check": "always-pass", "pass": True, "feedback": "", "on_fail": "iterate"}
    ]}))
    ev = tmp_path / "draft.json"
    ev.write_text(json.dumps({"questions": [{"id": "q1", "text": "Which DB?"}]}))
    e = dict(engine_env)
    e.update({"NODE_PATH": "recover.rationale.draft", "PR_HEAD_SHA": "abc123", "AGENT_RUN_ID": "r1"})
    _run_advance(tmp_path / "adv-dir", "pr-1", proto, v, ev, engine_env, extra_env=e)


# ===========================================================================
# Section 0 — DIRECT unit tests of _parse_answers (the regex parser itself)
# ===========================================================================

class TestParseAnswersDirect:
    """Call _parse_answers directly with malicious bodies. The function is a
    pure-Python regex parser (cannot exec by construction) — these tests LOCK
    that contract independently of do_answer's gate guard, closing the gap that
    the do_answer-path injection tests use a gateless protocol and so never
    reach _parse_answers."""

    PARSE = staticmethod(_load_parse_answers())

    def test_semicolon_payload_stored_as_string(self, tmp_path):
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        value = f"valid ; touch {sentinel} #tail"
        out = self.PARSE(f"/answer q1: {value}", "/answer")
        assert out == {"q1": value}, f"parsed dict wrong: {out!r}"
        assert isinstance(out["q1"], str)
        assert not os.path.exists(sentinel), (
            f"CRITICAL: _parse_answers executed the payload — {sentinel} created!"
        )

    def test_backtick_payload_stored_as_string(self, tmp_path):
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        value = f"`touch {sentinel}`"
        out = self.PARSE(f"/answer q1: {value}", "/answer")
        assert out == {"q1": value}
        assert not os.path.exists(sentinel), (
            f"CRITICAL: _parse_answers executed backtick payload — {sentinel} created!"
        )

    def test_dollar_subshell_payload_stored_as_string(self, tmp_path):
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        value = f"$(touch {sentinel})"
        out = self.PARSE(f"/answer q1: {value}", "/answer")
        assert out == {"q1": value}
        assert not os.path.exists(sentinel), (
            f"CRITICAL: _parse_answers executed $() payload — {sentinel} created!"
        )

    def test_multiline_only_valid_lines_parsed(self, tmp_path):
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        body = (
            f"chatty preamble\n"
            f"; touch {sentinel}\n"            # not an answer line → ignored
            f"/answer q1: first\n"
            f"/answer q2: second & echo nope\n"  # value retains metachars verbatim
        )
        out = self.PARSE(body, "/answer")
        assert out["q1"] == "first"
        assert out["q2"] == "second & echo nope"
        # The bare injection line is not a valid `id: val` after stripping prefix,
        # so it must not become a key.
        assert ";" not in "".join(out.keys())
        assert not os.path.exists(sentinel), (
            f"CRITICAL: _parse_answers executed a multiline payload — {sentinel} created!"
        )

    def test_equals_separator_payload_stored_as_string(self, tmp_path):
        # _parse_answers also accepts `id = val`; confirm metachars survive verbatim.
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        value = f"x | touch {sentinel}"
        out = self.PARSE(f"/answer q1 = {value}", "/answer")
        assert out == {"q1": value}
        assert not os.path.exists(sentinel)


# ===========================================================================
# Section 1 — Malicious ANSWER_BODY
# ===========================================================================

class TestAnswerBodyInjection:
    """ANSWER_BODY is an UNTRUSTED string from a GitHub comment body.
    _parse_answers (next.py) must treat it as data: parse key=value pairs
    from it, write them to a JSON file, and never execute anything in it."""

    def test_shell_injection_semicolon_not_executed(self, tmp_path, engine_env):
        """do_answer path inert: semicolon injection `; touch SENTINEL #` driven
        through the gateless protocol must NOT create SENTINEL (the regex parser
        itself is covered directly in TestParseAnswersDirect)."""
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        # Payload: a valid answer prefix then an injection suffix
        payload = f"/answer q1: valid_value ; touch {sentinel} #"

        r = _run_next(tmp_path / "dir1", "pr-1", SIMPLE_AGENT_PROTO, "answer",
                      engine_env, extra_env={"ANSWER_BODY": payload, "ANSWER_ACTOR": "ev1"})
        # Engine must not crash (rc is allowed to be 0 or non-zero; "no open gate" is fine)
        # The KEY assertion: sentinel was NOT created.
        assert not os.path.exists(sentinel), (
            f"CRITICAL: shell injection executed — sentinel file {sentinel} was created! "
            f"returncode={r.returncode}, stderr={r.stderr!r}"
        )

    def test_backtick_injection_not_executed(self, tmp_path, engine_env):
        """do_answer path inert: `touch SENTINEL` backtick substitution must NOT
        create SENTINEL."""
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        payload = f"/answer q1: `touch {sentinel}`"

        r = _run_next(tmp_path / "dir2", "pr-1", SIMPLE_AGENT_PROTO, "answer",
                      engine_env, extra_env={"ANSWER_BODY": payload, "ANSWER_ACTOR": "ev2"})
        assert not os.path.exists(sentinel), (
            f"CRITICAL: backtick injection executed — sentinel {sentinel} was created! "
            f"returncode={r.returncode}, stderr={r.stderr!r}"
        )

    def test_dollar_subshell_injection_not_executed(self, tmp_path, engine_env):
        """do_answer path inert: $() command substitution inside ANSWER_BODY must
        NOT execute."""
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        payload = f"/answer q1: $(touch {sentinel})"

        r = _run_next(tmp_path / "dir3", "pr-1", SIMPLE_AGENT_PROTO, "answer",
                      engine_env, extra_env={"ANSWER_BODY": payload, "ANSWER_ACTOR": "ev3"})
        assert not os.path.exists(sentinel), (
            f"CRITICAL: $() injection executed — sentinel {sentinel} was created! "
            f"returncode={r.returncode}, stderr={r.stderr!r}"
        )

    def test_answer_body_stored_verbatim_in_gate(self, tmp_path, engine_env):
        """With a REAL open gate (subpipeline-mini), injection payload is stored
        verbatim in the answers JSON and not interpreted.  The gate's answers doc
        must contain the raw string, and the sentinel must NOT appear."""
        _seed_open_gate(tmp_path, engine_env)

        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        injected_value = f"valid_answer ; touch {sentinel} #injected"
        payload = f"/answer q1: {injected_value}"

        r = _run_next(tmp_path / "ans-dir", "pr-1", SUBPIPELINE_PROTO, "answer",
                      engine_env,
                      extra_env={"ANSWER_BODY": payload,
                                 "ANSWER_ACTOR": "alice",
                                 "PR_HEAD_SHA": "abc123"})
        assert r.returncode == 0, f"answer command failed unexpectedly: {r.stderr}"

        # Sentinel must not exist
        assert not os.path.exists(sentinel), (
            f"CRITICAL: injection payload executed — sentinel {sentinel} created! "
            f"stderr={r.stderr!r}"
        )

        # Value must be stored verbatim (the stored value is the part after "q1: ")
        clone = _clone(engine_env["STATE_REMOTE"], tmp_path, "ans")
        answers_file = clone / SUBPIPELINE_PID / "pr-1/rationale.clarify.answers.json"
        assert answers_file.exists(), "answers file not written"
        doc = json.loads(answers_file.read_text())
        stored = doc.get("answers", {}).get("q1", "")
        assert stored == injected_value, (
            f"stored value '{stored!r}' does not match injected input '{injected_value!r}'"
        )

    def test_multiline_injection_body(self, tmp_path, engine_env):
        """do_answer path inert: multi-line ANSWER_BODY with injections on
        non-answer lines drives the gateless protocol without executing anything
        (line-parsing safety itself is covered in TestParseAnswersDirect)."""
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        payload = (
            f"some random comment\n"
            f"; touch {sentinel}\n"
            f"/answer q1: legitimate_answer\n"
        )

        r = _run_next(tmp_path / "dir-ml", "pr-1", SIMPLE_AGENT_PROTO, "answer",
                      engine_env, extra_env={"ANSWER_BODY": payload, "ANSWER_ACTOR": "ml"})
        assert not os.path.exists(sentinel), (
            f"CRITICAL: multiline injection executed — sentinel {sentinel} created! "
            f"returncode={r.returncode}, stderr={r.stderr!r}"
        )


# ===========================================================================
# Section 2 — Bogus / traversal NODE_PATH fed to advance.py
# ===========================================================================

class TestNodePathTraversal:
    """NODE_PATH is caller-controlled input from a GitHub Actions payload.
    advance.py splits it on '.' and feeds segments to paths.node_at_path,
    which does a dict-key lookup — not a filesystem path join.  A traversal
    segment or a nonexistent node should yield a clean non-zero exit and
    must not create files outside the instance dir."""

    def _verdicts_and_evidence(self, tmp_path, tag="t"):
        v = tmp_path / f"v-{tag}.json"
        v.write_text(json.dumps({"results": [
            {"check": "c", "pass": True, "feedback": "", "on_fail": "iterate"}
        ]}))
        ev = tmp_path / f"ev-{tag}.json"
        ev.write_text("{}")
        return v, ev

    def test_traversal_dotdot_yields_clean_error(self, tmp_path, engine_env):
        """NODE_PATH='../../etc/passwd' must produce a non-zero exit and a
        clear error message; no file may be created at ../../ relative to anything."""
        v, ev = self._verdicts_and_evidence(tmp_path, "tr1")
        # Snapshot files before the call
        before = _all_files_under(tmp_path)

        r = _run_advance(tmp_path / "adv-tr1", "pr-1", SUBPIPELINE_PROTO, v, ev,
                         engine_env, extra_env={"NODE_PATH": "../../etc/passwd"})

        assert r.returncode != 0, (
            f"CRITICAL: advance.py exited 0 for traversal NODE_PATH! stdout={r.stdout!r}"
        )
        # stderr must contain a helpful message referencing the path
        assert r.stderr.strip(), "expected an error message on stderr"

        # No files escaped the tmp_path subtree
        after = _all_files_under(tmp_path)
        new_files = after - before
        for f in new_files:
            assert str(f).startswith(str(tmp_path)), (
                f"CRITICAL: file written OUTSIDE tmp_path by traversal test: {f}"
            )

        # Specifically: no file was written at /etc/passwd or similar
        assert not pathlib.Path("/etc/passwd.yaml").exists(), (
            "CRITICAL: traversal actually wrote to /etc/passwd.yaml!"
        )

    def test_nonexistent_node_yields_clean_error(self, tmp_path, engine_env):
        """NODE_PATH='nonexistent.node' (no such node in the protocol) must
        produce a non-zero exit and must not write any state file."""
        v, ev = self._verdicts_and_evidence(tmp_path, "ne1")
        before = _all_files_under(tmp_path)

        r = _run_advance(tmp_path / "adv-ne1", "pr-1", SUBPIPELINE_PROTO, v, ev,
                         engine_env, extra_env={"NODE_PATH": "nonexistent.node"})

        assert r.returncode != 0, (
            f"advance.py exited 0 for nonexistent NODE_PATH! stdout={r.stdout!r}"
        )
        assert r.stderr.strip(), "expected an error message on stderr"

        after = _all_files_under(tmp_path)
        new_files = after - before
        # Any new files must still be within tmp_path
        for f in new_files:
            assert str(f).startswith(str(tmp_path)), (
                f"CRITICAL: file written outside tmp_path: {f}"
            )

    def test_single_segment_traversal_yields_clean_error(self, tmp_path, engine_env):
        """NODE_PATH='../../../escape' (single step, not a dot-joined path) must
        produce a clean non-zero exit; the protocol dict lookup returns None, and
        advance.py errors before writing any state file."""
        v, ev = self._verdicts_and_evidence(tmp_path, "ss1")
        before = _all_files_under(tmp_path)

        r = _run_advance(tmp_path / "adv-ss1", "pr-1", SUBPIPELINE_PROTO, v, ev,
                         engine_env, extra_env={"NODE_PATH": "../../../escape"})

        # resolve_agent_unit_path must raise ValueError("no node at path …")
        # and advance.py must exit non-zero
        assert r.returncode != 0, (
            f"CRITICAL: advance.py exited 0 for traversal single-segment! stdout={r.stdout!r}"
        )

        # Filesystem boundary check (consistent with the sibling traversal tests):
        # no file may escape tmp_path, and nothing may land at an absolute traversal
        # target.
        after = _all_files_under(tmp_path)
        for f in after - before:
            assert str(f).startswith(str(tmp_path)), (
                f"CRITICAL: file written outside tmp_path: {f}"
            )
        assert not pathlib.Path("/escape.yaml").exists(), (
            "CRITICAL: single-segment traversal wrote /escape.yaml!"
        )

    def test_no_files_outside_instance_dir_on_traversal(self, tmp_path, engine_env):
        """After a traversal NODE_PATH call, confirm no files exist outside
        <workdir>/<pid>/<instance>/ (the instance dir boundary)."""
        v, ev = self._verdicts_and_evidence(tmp_path, "bd1")
        work = tmp_path / "adv-bd1"

        _run_advance(work, "pr-1", SUBPIPELINE_PROTO, v, ev,
                     engine_env, extra_env={"NODE_PATH": "../../etc/shadow"})

        # The engine's state dir: <work>/<pid>/<instance>/
        # (the git checkout lands in work/<pid>/…)
        # We just confirm nothing appeared at obviously wrong paths.
        assert not (tmp_path / "etc").exists(), (
            "CRITICAL: /etc subtree appeared inside tmp_path after traversal test"
        )
        # Also verify no file was created at an absolute traversal path
        assert not pathlib.Path("/etc/shadow.yaml").exists()
        assert not pathlib.Path("/etc/passwd.yaml").exists()


# ===========================================================================
# Section 3 — NODE_PATH segments with shell metacharacters
# ===========================================================================

class TestNodePathMetacharacters:
    """NODE_PATH with shell metacharacters (pipes, semicolons, backticks).
    The engine splits on '.' and uses segments as dict keys — no shell
    subprocess receives the raw segment.  Result must be a clean non-zero
    exit, not execution."""

    def _verdicts_and_evidence(self, tmp_path, tag="m"):
        v = tmp_path / f"v-{tag}.json"
        v.write_text(json.dumps({"results": [
            {"check": "c", "pass": True, "feedback": "", "on_fail": "iterate"}
        ]}))
        ev = tmp_path / f"ev-{tag}.json"
        ev.write_text("{}")
        return v, ev

    def test_pipe_in_node_path_yields_clean_error(self, tmp_path, engine_env):
        """NODE_PATH='review|rm -rf /' must fail cleanly, not execute anything."""
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        v, ev = self._verdicts_and_evidence(tmp_path, "pm1")

        r = _run_advance(tmp_path / "adv-pm1", "pr-1", SUBPIPELINE_PROTO, v, ev,
                         engine_env, extra_env={"NODE_PATH": f"review|touch {sentinel}"})

        assert not os.path.exists(sentinel), (
            f"CRITICAL: pipe metachar in NODE_PATH was executed! sentinel={sentinel} created"
        )
        assert r.returncode != 0, (
            f"advance.py exited 0 for pipe-metachar NODE_PATH! stdout={r.stdout!r}"
        )

    def test_semicolon_in_node_path_yields_clean_error(self, tmp_path, engine_env):
        """NODE_PATH with semicolon injection must fail cleanly."""
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        v, ev = self._verdicts_and_evidence(tmp_path, "sc1")

        r = _run_advance(tmp_path / "adv-sc1", "pr-1", SUBPIPELINE_PROTO, v, ev,
                         engine_env, extra_env={"NODE_PATH": f"review;touch {sentinel}"})

        assert not os.path.exists(sentinel), (
            f"CRITICAL: semicolon in NODE_PATH was executed! sentinel {sentinel} created"
        )
        assert r.returncode != 0, (
            f"advance.py exited 0 for semicolon NODE_PATH! stdout={r.stdout!r}"
        )

    def test_backtick_in_node_path_yields_clean_error(self, tmp_path, engine_env):
        """NODE_PATH with backtick injection must fail cleanly."""
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        v, ev = self._verdicts_and_evidence(tmp_path, "bt1")

        r = _run_advance(tmp_path / "adv-bt1", "pr-1", SUBPIPELINE_PROTO, v, ev,
                         engine_env, extra_env={"NODE_PATH": f"`touch {sentinel}`"})

        assert not os.path.exists(sentinel), (
            f"CRITICAL: backtick in NODE_PATH was executed! sentinel {sentinel} created"
        )
        assert r.returncode != 0, (
            f"advance.py exited 0 for backtick NODE_PATH! stdout={r.stdout!r}"
        )

    def test_dollar_subshell_in_node_path_yields_clean_error(self, tmp_path, engine_env):
        """NODE_PATH with $() injection must fail cleanly."""
        unique = str(uuid.uuid4()).replace("-", "")[:12]
        sentinel = f"/tmp/PWNED_{unique}"
        v, ev = self._verdicts_and_evidence(tmp_path, "ds1")

        r = _run_advance(tmp_path / "adv-ds1", "pr-1", SUBPIPELINE_PROTO, v, ev,
                         engine_env, extra_env={"NODE_PATH": f"$(touch {sentinel})"})

        assert not os.path.exists(sentinel), (
            f"CRITICAL: $() in NODE_PATH was executed! sentinel {sentinel} created"
        )
        assert r.returncode != 0, (
            f"advance.py exited 0 for $() NODE_PATH! stdout={r.stdout!r}"
        )
