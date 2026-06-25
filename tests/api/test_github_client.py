import httpx, pytest, respx
from api.config import Settings
from api.github_client import GitHubClient, NotFound, RateLimited, UpstreamError

S = Settings(api_bearer_token="t", github_token="gh", github_repo="o/r",
             state_branch="agentic-state", protocols_ref="main",
             engine_workflows=[], github_api_url="https://api.github.com")

@respx.mock
def test_list_tree_filters_by_prefix():
    respx.get("https://api.github.com/repos/o/r/git/trees/agentic-state").mock(
        return_value=httpx.Response(200, json={"tree": [
            {"path": "code-review/pr-62/_instance.yaml", "type": "blob"},
            {"path": "deep-review-stub/pr-88/quick.yaml", "type": "blob"},
            {"path": "code-review/pr-62", "type": "tree"},
        ]}))
    c = GitHubClient(S)
    out = c.list_tree("code-review/")
    assert out == ["code-review/pr-62/_instance.yaml"]

@respx.mock
def test_get_text_404_raises_notfound():
    respx.get("https://api.github.com/repos/o/r/contents/missing.yaml").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"}))
    c = GitHubClient(S)
    with pytest.raises(NotFound):
        c.get_text("missing.yaml", "agentic-state")

@respx.mock
def test_rate_limit_raises_with_retry_after():
    respx.get("https://api.github.com/repos/o/r/contents/x.yaml").mock(
        return_value=httpx.Response(403, headers={"Retry-After": "60",
            "X-RateLimit-Remaining": "0"}, json={"message": "rate limited"}))
    c = GitHubClient(S)
    with pytest.raises(RateLimited) as e:
        c.get_text("x.yaml", "agentic-state")
    assert e.value.retry_after == "60"

@respx.mock
def test_bare_403_without_ratelimit_header_is_upstream_error():
    # A 403 that is NOT a rate-limit (no X-RateLimit-Remaining: 0) must map to
    # UpstreamError, not RateLimited.
    respx.get("https://api.github.com/repos/o/r/contents/x.yaml").mock(
        return_value=httpx.Response(403, json={"message": "forbidden"}))
    c = GitHubClient(S)
    with pytest.raises(UpstreamError):
        c.get_text("x.yaml", "agentic-state")

@respx.mock
def test_list_workflow_runs_maps_fields_and_stops_on_short_page():
    # One short page (<100) -> early exit; only the three mapped fields are kept.
    respx.get("https://api.github.com/repos/o/r/actions/workflows/agentic-engine.yml/runs").mock(
        return_value=httpx.Response(200, json={"workflow_runs": [
            {"run_started_at": "2026-06-24T10:00:00Z", "updated_at": "2026-06-24T10:02:00Z",
             "name": "engine", "id": 1, "extra": "dropped"},
            {"run_started_at": "2026-06-24T11:00:00Z", "updated_at": "2026-06-24T11:01:00Z",
             "name": "engine", "id": 2},
        ]}))
    c = GitHubClient(S)
    runs = c.list_workflow_runs(["agentic-engine.yml"])
    assert len(runs) == 2
    assert runs[0] == {"run_started_at": "2026-06-24T10:00:00Z",
                       "updated_at": "2026-06-24T10:02:00Z", "name": "engine"}
    assert "id" not in runs[0] and "extra" not in runs[0]

@respx.mock
def test_list_workflow_runs_empty_workflows_uses_repo_runs_endpoint():
    # Empty workflow list -> the repo-wide /actions/runs endpoint.
    respx.get("https://api.github.com/repos/o/r/actions/runs").mock(
        return_value=httpx.Response(200, json={"workflow_runs": [
            {"run_started_at": "2026-06-24T09:00:00Z", "updated_at": "2026-06-24T09:05:00Z",
             "name": "any"},
        ]}))
    c = GitHubClient(S)
    runs = c.list_workflow_runs([])
    assert len(runs) == 1 and runs[0]["name"] == "any"

@respx.mock
def test_list_dir_returns_entry_names():
    respx.get("https://api.github.com/repos/o/r/contents/.github/agent-factory/protocols").mock(
        return_value=httpx.Response(200, json=[
            {"name": "code-review", "type": "dir"},
            {"name": "deep-review-stub", "type": "dir"},
            {"name": "README.md", "type": "file"},
        ]))
    c = GitHubClient(S)
    assert c.list_dir(".github/agent-factory/protocols", "main") == \
        ["code-review", "deep-review-stub"]
