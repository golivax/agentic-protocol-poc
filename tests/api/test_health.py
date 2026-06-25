from fastapi.testclient import TestClient
from api.app import create_app
from api.config import Settings

SETTINGS = Settings(
    api_bearer_token="t0ken", github_token="gh", github_repo="o/r",
    state_branch="agentic-state", protocols_ref="main",
    engine_workflows=[], github_api_url="https://api.github.com",
)

def test_healthz_is_open_and_ok():
    client = TestClient(create_app(SETTINGS))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

def test_from_env_reports_all_missing_required():
    import pytest
    with pytest.raises(ValueError) as e:
        Settings.from_env({})
    msg = str(e.value)
    assert "API_BEARER_TOKEN" in msg and "GITHUB_TOKEN" in msg and "GITHUB_REPO" in msg
