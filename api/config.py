from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping

@dataclass(frozen=True)
class Settings:
    api_bearer_token: str
    github_token: str
    github_repo: str
    state_branch: str
    protocols_ref: str
    engine_workflows: list[str]
    github_api_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        required = ("API_BEARER_TOKEN", "GITHUB_TOKEN", "GITHUB_REPO")
        missing = [k for k in required if not env.get(k)]
        if missing:
            raise ValueError(f"missing required env vars: {', '.join(missing)}")
        raw = env.get("ENGINE_WORKFLOWS", "").strip()
        workflows = [w.strip() for w in raw.split(",") if w.strip()]
        return cls(
            api_bearer_token=env["API_BEARER_TOKEN"],
            github_token=env["GITHUB_TOKEN"],
            github_repo=env["GITHUB_REPO"],
            state_branch=env.get("STATE_BRANCH", "agentic-state"),
            protocols_ref=env.get("PROTOCOLS_REF", "main"),
            engine_workflows=workflows,
            github_api_url=env.get("GITHUB_API_URL", "https://api.github.com"),
        )
