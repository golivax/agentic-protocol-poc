import json, subprocess, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENG = ROOT/".github/agent-factory/engine"
def _yaml(p):
    import yaml; return yaml.safe_load(open(p))
def _rc(engine_env, tmp_path, pid, tag):
    d=tmp_path/f"rc-{tag}"
    subprocess.run(["git","clone","-q","-b","agentic-state",
                    engine_env["STATE_REMOTE"],str(d)],check=True)
    return d/pid/"pr-1"

def test_continue_at_approval_gate_opens_it(engine_env, tmp_path):
    proto = ROOT/".github/agent-factory/protocols/code-review-v1/protocol.json"
    base=dict(engine_env); base["PR_HEAD_SHA"]="s1"
    # seed an _instance with phase=approval (simulate post-join cursor)
    subprocess.run(["python3",str(ENG/"next.py"),str(tmp_path/"s"),"pr-1",str(proto),
                    "start","s1"],text=True,capture_output=True,env=base,check=True)
    e=dict(base); e["NODE_PATH"]="approval"
    r=subprocess.run(["python3",str(ENG/"next.py"),str(tmp_path/"c"),"pr-1",str(proto),
                      "continue"],text=True,capture_output=True,env=e)
    assert r.returncode==0, r.stderr
    assert json.loads(r.stdout)["reason"].startswith("gate-open")
    g=_yaml(_rc(engine_env,tmp_path,"code-review-v1","g")/"approval.yaml")
    assert g.get("gates",{}).get("state")=="open"
