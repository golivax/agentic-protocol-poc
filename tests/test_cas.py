import os
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github/agent-factory/engine"))


def _clone(origin, into):
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", str(origin), str(into)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(into), "config", k, v], check=True)


def test_cas_push_rebases_over_concurrent_writer(state_origin, tmp_path, monkeypatch):
    import lib
    # bootstrap: create agentic-state branch on the bare origin with an initial commit
    init = tmp_path / "init"
    subprocess.run(["git", "init", "-q", "-b", "agentic-state", str(init)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(init), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(init), "remote", "add", "origin", str(state_origin)], check=True)
    subprocess.run(["git", "-C", str(init), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(init), "push", "-q", "origin", "agentic-state"], check=True)
    # seed origin with one commit on agentic-state
    seed = tmp_path / "seed"; _clone(state_origin, seed)
    (seed / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "seed"], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "agentic-state"], check=True)
    # our working clone (writes b.txt)
    ours = tmp_path / "ours"; _clone(state_origin, ours)
    # a concurrent writer pushes c.txt AFTER we clone but BEFORE we push
    other = tmp_path / "other"; _clone(state_origin, other)
    (other / "c.txt").write_text("c")
    subprocess.run(["git", "-C", str(other), "add", "."], check=True)
    subprocess.run(["git", "-C", str(other), "commit", "-q", "-m", "other"], check=True)
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "agentic-state"], check=True)
    # now our push would be rejected; cas_push must rebase + land
    (ours / "b.txt").write_text("b")
    monkeypatch.setenv("STATE_BRANCH", "agentic-state")
    lib.cas_push(str(ours), "ours change")
    # both files exist on origin tip
    log = subprocess.run(["git", "-C", str(other), "pull", "-q"], capture_output=True)
    files = set(p.name for p in other.iterdir() if p.suffix == ".txt")
    assert {"a.txt", "b.txt", "c.txt"} <= files
    # verify the bounded retry loop parameter exists (not just a single-retry path)
    assert "attempts" in lib.cas_push.__code__.co_varnames
