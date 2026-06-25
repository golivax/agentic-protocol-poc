from __future__ import annotations
import httpx
from api.config import Settings

class GitHubError(Exception): ...
class NotFound(GitHubError): ...
class UpstreamError(GitHubError): ...
class RateLimited(GitHubError):
    def __init__(self, msg, retry_after=None):
        super().__init__(msg)
        self.retry_after = retry_after

class GitHubClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.s = settings
        self.http = http or httpx.Client(timeout=15.0)

    def _headers(self, accept="application/vnd.github+json"):
        return {"Authorization": f"Bearer {self.s.github_token}",
                "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}

    def _base(self):
        return f"{self.s.github_api_url}/repos/{self.s.github_repo}"

    def _check(self, r: httpx.Response):
        if r.status_code == 404:
            raise NotFound(r.url)
        if r.status_code in (403, 429) and r.headers.get("X-RateLimit-Remaining") == "0":
            raise RateLimited("github rate limit", r.headers.get("Retry-After"))
        if r.status_code >= 400:
            raise UpstreamError(f"{r.status_code} {r.url}")
        return r

    def list_tree(self, prefix: str) -> list[str]:
        url = f"{self._base()}/git/trees/{self.s.state_branch}"
        r = self._check(self.http.get(url, headers=self._headers(),
                                      params={"recursive": "1"}))
        tree = r.json().get("tree", [])
        return [e["path"] for e in tree
                if e.get("type") == "blob" and e["path"].startswith(prefix)]

    def get_text(self, path: str, ref: str) -> str:
        url = f"{self._base()}/contents/{path}"
        r = self._check(self.http.get(url, headers=self._headers("application/vnd.github.raw+json"),
                                      params={"ref": ref}))
        return r.text

    get_json = get_text

    def list_dir(self, path: str, ref: str) -> list[str]:
        url = f"{self._base()}/contents/{path}"
        r = self._check(self.http.get(url, headers=self._headers(), params={"ref": ref}))
        return sorted(e["name"] for e in r.json() if e.get("type") == "dir")

    def list_workflow_runs(self, workflows: list[str]) -> list[dict]:
        runs, sources = [], (workflows or [None])
        for wf in sources:
            base = (f"{self._base()}/actions/workflows/{wf}/runs" if wf
                    else f"{self._base()}/actions/runs")
            for page in range(1, 6):
                r = self._check(self.http.get(base, headers=self._headers(),
                                params={"per_page": 100, "page": page}))
                batch = r.json().get("workflow_runs", [])
                runs.extend({"run_started_at": x.get("run_started_at"),
                             "updated_at": x.get("updated_at"),
                             "name": x.get("name")} for x in batch)
                if len(batch) < 100:
                    break
        return runs
