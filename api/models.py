from __future__ import annotations
from pydantic import BaseModel
from typing import Any

# Responses are shaped as plain dicts from state_reader; these models document
# and validate the top-level envelopes returned to clients.
class ProtocolList(BaseModel):
    protocols: list[dict[str, Any]]

class InstanceList(BaseModel):
    protocol: str
    instances: list[int]

class GatesResponse(BaseModel):
    gates: list[dict[str, Any]]

class GlobalStats(BaseModel):
    protocols: list[str]
    instances_total: int
    instances_running: int
    instances_completed: int
    instances_failed: int
    instances_blocked: int
    by_protocol: dict[str, dict[str, int]]
    action_minutes_approx: float
    action_minutes_note: str
