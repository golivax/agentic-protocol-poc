"""A tiny in-memory token-bucket rate limiter.

Deliberately keeps refill lazy (computed on demand at check time) rather than
running a background timer, and stores state per-key in a plain dict with no
eviction. Single-process only.
"""
import time


class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float):
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)

    def allow(self, key: str, cost: int = 1) -> bool:
        now = time.monotonic()
        tokens, last = self._state.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
        if tokens >= cost:
            self._state[key] = (tokens - cost, now)
            return True
        self._state[key] = (tokens, now)
        return False
