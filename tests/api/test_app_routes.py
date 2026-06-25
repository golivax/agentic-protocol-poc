from pathlib import Path
from fastapi.testclient import TestClient
from api.app import create_app
from api.config import Settings
from api.github_client import NotFound

FX = Path(__file__).parent / "fixtures"
S = Settings(api_bearer_token="t0ken", github_token="gh", github_repo="o/r",
             state_branch="agentic-state", protocols_ref="main",
             engine_workflows=[], github_api_url="https://api.github.com")

class FakeClient:
    """In-memory stand-in for GitHubClient, backed by fixture files."""
    PROTO_DIR = ".github/agent-factory/protocols"
    def __init__(self):
        self.runs = [{"run_started_at": "2026-06-24T10:00:00Z",
                      "updated_at": "2026-06-24T10:02:00Z", "name": "engine"}]
    def list_dir(self, path, ref):
        return ["code-review", "deep-review-stub"]
    def get_text(self, path, ref):
        if path.endswith("protocol.json"):
            name = path.split("/")[-2]
            f = FX / "protocols" / f"{name}.protocol.json"
            if not f.exists():
                raise NotFound(path)
            return f.read_text()
        # state file: "<protocol>/pr-<N>/<file>"
        f = FX / "state" / path
        if not f.exists():
            raise NotFound(path)
        return f.read_text()
    def list_tree(self, prefix):
        root = FX / "state" / prefix.rstrip("/")
        if not root.exists():
            return []
        return [str(Path(prefix.rstrip("/")) / p.relative_to(root))
                for p in root.rglob("*") if p.is_file()]
    def list_workflow_runs(self, workflows):
        return self.runs

def app():
    return TestClient(create_app(S, client=FakeClient()))

AUTH = {"Authorization": "Bearer t0ken"}

def test_protocols_requires_auth():
    assert app().get("/protocols").status_code == 401

def test_wrong_bearer_token_is_401():
    r = app().get("/protocols", headers={"Authorization": "Bearer not-the-token"})
    assert r.status_code == 401

def test_list_protocols():
    r = app().get("/protocols", headers=AUTH)
    assert r.status_code == 200
    assert {p["name"] for p in r.json()["protocols"]} == {"code-review", "deep-review-stub"}

def test_protocol_detail_404_for_unknown():
    assert app().get("/protocols/nope", headers=AUTH).status_code == 404

def test_instance_status():
    r = app().get("/protocols/code-review/instances/62/status", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["head"]["phase"] == "approval"

def test_instance_evidence_returns_node_keyed_bodies():
    r = app().get("/protocols/code-review/instances/62/evidence", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["protocol"] == "code-review" and body["pr"] == 62
    assert body["evidence"]["preflight"]["checks"][0]["id"] == "spec-adherence"
    assert body["evidence"]["review.security"]["dimension"] == "security"
    assert body["answers"]["approval"]["decision"] == "approve"

def test_instance_evidence_requires_auth():
    assert app().get("/protocols/code-review/instances/62/evidence").status_code == 401

def test_instance_evidence_unknown_instance_is_404():
    r = app().get("/protocols/code-review/instances/999/evidence", headers=AUTH)
    assert r.status_code == 404

def test_instance_list():
    r = app().get("/protocols/code-review/instances", headers=AUTH)
    assert r.status_code == 200
    assert 62 in r.json()["instances"]

def test_global_stats_has_minutes():
    r = app().get("/stats", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["action_minutes_approx"] == 2.0
    assert "code-review" in body["protocols"]

def test_gates_open_lists_pr62_approval():
    r = app().get("/gates", params={"status": "open", "protocol": "code-review"}, headers=AUTH)
    assert r.status_code == 200
    gates = r.json()["gates"]
    assert any(g["pr"] == 62 and g["awaiting"] == "approval" for g in gates)

def test_gates_unknown_protocol_is_404():
    r = app().get("/gates", params={"status": "open", "protocol": "nope"}, headers=AUTH)
    assert r.status_code == 404

def test_gates_traversal_protocol_is_400():
    # A query-sourced protocol with path-traversal chars must be rejected before
    # it reaches the GitHub URL builder.
    r = app().get("/gates", params={"protocol": "../../etc"}, headers=AUTH)
    assert r.status_code == 400

def test_protocol_detail_invalid_name_is_400():
    # A malformed-but-routable name (leading dot — not URL-normalized like "..")
    # is rejected by the validator before any GitHub call.
    r = app().get("/protocols/.hidden", headers=AUTH)
    assert r.status_code == 400

def test_notfound_from_blob_fetch_maps_to_404():
    # A client whose list_tree advertises a path but whose get_text 404s on it
    # (TOCTOU / permission gap) must surface as 404, not 500, via the global handler.
    class FlakyClient(FakeClient):
        def get_text(self, path, ref):
            if path.endswith("protocol.json"):
                return super().get_text(path, ref)
            raise NotFound(path)
    c = TestClient(create_app(S, client=FlakyClient()))
    r = c.get("/protocols/code-review/instances/62/status", headers=AUTH)
    assert r.status_code == 404

def test_healthz_open_no_auth():
    assert app().get("/healthz").status_code == 200
