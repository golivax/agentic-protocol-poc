import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / ".github/agent-factory/engine"


def _probe_check(tmp_path):
    """A check that echoes PR_BODY/PR_TITLE from its env into feedback."""
    c = tmp_path / "echo-pr.py"
    c.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "print(json.dumps({'check':'echo-pr','pass':True,"
        "'feedback':'body=' + os.environ.get('PR_BODY','') + '|title=' + os.environ.get('PR_TITLE','')}))\n"
    )
    c.chmod(0o755)
    return c


def _protocol(tmp_path, check_path):
    p = tmp_path / "protocol.json"
    p.write_text(json.dumps({
        "name": "probe",
        "states": [{"id": "s", "kind": "agent",
                    "checks": [{"run": "echo-pr", "exec": str(check_path)}]}],
    }))
    return p


def test_checks_receive_pr_body_and_title(tmp_path):
    check = _probe_check(tmp_path)
    proto = _protocol(tmp_path, check)
    ev = tmp_path / "evidence.json"; ev.write_text("{}")
    diff = tmp_path / "diff.txt"; diff.write_text("")
    files = tmp_path / "files.txt"; files.write_text("")
    env = dict(os.environ)
    env["PR_BODY"] = "## Requirements\nDo the thing"
    env["PR_TITLE"] = "My PR"
    r = subprocess.run(
        ["python3", str(ENGINE / "run-checks.py"), str(proto), "s", str(ev), str(diff), str(files)],
        text=True, capture_output=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    verdict = out["results"][0]
    assert "body=## Requirements" in verdict["feedback"]
    assert "title=My PR" in verdict["feedback"]
