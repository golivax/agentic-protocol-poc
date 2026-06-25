from __future__ import annotations
import os
from api.config import Settings
from api.app import create_app
from api.github_client import GitHubClient

settings = Settings.from_env(os.environ)
app = create_app(settings, client=GitHubClient(settings))
