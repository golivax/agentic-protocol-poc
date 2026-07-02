"""FIX1 — engine: run_conclude_hook must SURFACE a failed/crashed conclude hook
instead of swallowing it as a bland neutral no-verdict.

A conclude hook that exits non-zero OR emits non-JSON must:
  - yield a verdict whose conclusion is the failure value (not silently neutral),
  - carry the hook name + exit code + tail of stderr in the summary,
  - NEVER set blocked=true (halt is a separate gate concern),
  - and have its captured stderr scrubbed of secrets before it reaches the
    (public) summary or the job log.

A well-formed verdict (including {"blocked": true}) must pass through verbatim.
"""
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))
import advance  # noqa: E402


def _make_proto(tmp_path, hook_body):
    """Minimal protocol dir with a single agent state whose conclude hook has
    the given body. Returns (proto_dict, proto_path)."""
    pdir = tmp_path / "proto"
    (pdir / "publish").mkdir(parents=True)
    proto = {"name": "t", "states": [
        {"id": "s", "kind": "agent", "workflow": "w", "evidence": "e", "conclude": "hook"}]}
    proto_path = pdir / "protocol.json"
    proto_path.write_text(json.dumps(proto))
    hook = pdir / "publish" / "hook.py"
    hook.write_text(hook_body)
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return proto, str(proto_path)


def _call(tmp_path, proto, proto_path):
    evid = tmp_path / "ev.json"
    evid.write_text("{}")
    return advance.run_conclude_hook(
        proto_path, proto, "s", str(evid), "pr-1", blocking=False, dir_=None)


def test_fix1_surfaces_crashed_hook(tmp_path):
    """A hook that exits non-zero AND prints non-JSON must surface the failure:
    conclusion == "failure" (not a bland neutral) and the summary must name the
    hook, the exit code, and carry the error text."""
    body = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('boom: something exploded in the hook\\n')\n"
        "print('this is not json')\n"
        "sys.exit(7)\n"
    )
    proto, proto_path = _make_proto(tmp_path, body)
    res = _call(tmp_path, proto, proto_path)

    assert res is not None
    # NOT the pre-fix bland swallow.
    assert res.get("summary") != "conclude hook returned no verdict"
    assert res.get("conclusion") == "failure"
    # Never halts a normal pipeline.
    assert res.get("blocked") is False
    summary = res.get("summary", "")
    assert "hook.py" in summary, summary
    assert "7" in summary, summary            # exit code surfaced
    assert "exploded" in summary, summary     # stderr tail surfaced


def test_fix1_surfaces_nonjson_but_exit_zero(tmp_path):
    """Exit 0 but malformed (non-dict-verdict) stdout is still a FAILURE to
    surface — the old code returned a bland neutral for this too."""
    body = (
        "#!/usr/bin/env python3\n"
        "print('garbage-not-a-verdict')\n"
    )
    proto, proto_path = _make_proto(tmp_path, body)
    res = _call(tmp_path, proto, proto_path)
    assert res.get("conclusion") == "failure"
    assert res.get("blocked") is False
    assert res.get("summary") != "conclude hook returned no verdict"


def test_fix1_redacts_secret_in_stderr(tmp_path, capsys):
    """A secret leaked to the hook's stderr must be redacted (***) before it
    reaches the returned (public) summary AND the job log (sys.stderr)."""
    secret = "s3cr3t-publish-token-value-DO-NOT-LEAK"
    body = (
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "sys.stderr.write('failed cloning with token ' + os.environ['PUBLISH_TOKEN'] + ' end\\n')\n"
        "sys.exit(2)\n"
    )
    proto, proto_path = _make_proto(tmp_path, body)
    old = os.environ.get("PUBLISH_TOKEN")
    os.environ["PUBLISH_TOKEN"] = secret
    try:
        res = _call(tmp_path, proto, proto_path)
    finally:
        if old is None:
            os.environ.pop("PUBLISH_TOKEN", None)
        else:
            os.environ["PUBLISH_TOKEN"] = old

    summary = res.get("summary", "")
    assert secret not in summary, "secret leaked into the public summary"
    assert "***" in summary, summary
    # And not leaked into the job log either.
    captured = capsys.readouterr()
    assert secret not in captured.err, "secret leaked into the job log (stderr)"


def test_fix1_redacts_github_token_pattern(tmp_path):
    """A GitHub-style token appearing in stderr (not from our env) is redacted
    by the structural pattern branch."""
    body = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('remote said: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 rejected\\n')\n"
        "sys.exit(1)\n"
    )
    proto, proto_path = _make_proto(tmp_path, body)
    res = _call(tmp_path, proto, proto_path)
    summary = res.get("summary", "")
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in summary
    assert "***" in summary


def test_fix1_preserves_valid_verdict(tmp_path):
    """A well-formed verdict passes through verbatim (no wrapping)."""
    body = (
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'conclusion': 'clear', 'summary': 'all good', 'blocked': False}))\n"
    )
    proto, proto_path = _make_proto(tmp_path, body)
    res = _call(tmp_path, proto, proto_path)
    assert res == {"conclusion": "clear", "summary": "all good", "blocked": False}


def test_fix1_preserves_blocked_verdict(tmp_path):
    """A blocked gate verdict (blocked=true) must pass through UNCHANGED so
    preflight-gate / on_blocked=halt semantics are preserved."""
    body = (
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'conclusion': 'failure', 'summary': 'gate failed', 'blocked': True}))\n"
    )
    proto, proto_path = _make_proto(tmp_path, body)
    res = _call(tmp_path, proto, proto_path)
    assert res.get("blocked") is True
    assert res.get("summary") == "gate failed"
    assert res.get("conclusion") == "failure"


def test_fix1_redacts_openai_style_key(tmp_path):
    """An sk-/sk-ant- style API key in stderr is redacted by the STRUCTURAL
    pattern even when it is NOT in this job's environment (the advance/checks
    job holds no LLM creds, so env-value redaction alone would miss it)."""
    body = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('provider rejected key sk-proj-ABCDEF0123456789abcdef stop\\n')\n"
        "sys.exit(1)\n"
    )
    proto, proto_path = _make_proto(tmp_path, body)
    res = _call(tmp_path, proto, proto_path)
    summary = res.get("summary", "")
    assert "sk-proj-ABCDEF0123456789abcdef" not in summary, summary
    assert "***" in summary
