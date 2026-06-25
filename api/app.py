from __future__ import annotations
from fastapi import FastAPI
from api.config import Settings

def create_app(settings: Settings, client=None) -> FastAPI:
    app = FastAPI(title="Protocol Visibility API")
    app.state.settings = settings
    app.state.client = client  # GitHubClient injected in Task 8; None for now

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
