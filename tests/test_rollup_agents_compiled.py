# tests/test_rollup_agents_compiled.py
from pathlib import Path

WF = Path(".github/workflows")
ROLLUPS = ["adherence-rollup", "consistency-rollup"]


def test_all_rollup_md_and_locks_exist_with_gateway():
    for rollup in ROLLUPS:
        md = WF / f"{rollup}-agent.md"
        lock = WF / f"{rollup}-agent.lock.yml"
        assert md.exists(), f"missing {md}"
        assert lock.exists(), f"missing {lock}"
        t = md.read_text()
        assert "id: codex" in t and "model: gpt-5.5" in t
        assert "OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/" in t
        lt = lock.read_text()
        assert '"compiler_version":"v0.77.5"' in lt
        assert '"targets":{"openai":{"host":"arcyleung-ubuntu.tailb940e6.ts.net"}}' in lt
