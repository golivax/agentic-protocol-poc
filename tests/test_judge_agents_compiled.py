# tests/test_judge_agents_compiled.py
from pathlib import Path
import re
WF = Path(".github/workflows")
LEGS = ["spec-solves-issue", "plan-implements-spec", "code-implements-plan",
        "mm-compliance", "docs-updated-appropriately", "tests-updated-appropriately"]

def test_all_judge_md_and_locks_exist_with_gateway():
    for leg in LEGS:
        md = WF / f"{leg}-judge-agent.md"
        lock = WF / f"{leg}-judge-agent.lock.yml"
        assert md.exists(), f"missing {md}"
        assert lock.exists(), f"missing {lock}"
        t = md.read_text()
        assert "id: codex" in t and "model: gpt-5.5" in t
        assert "OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/" in t
        lt = lock.read_text()
        assert '"compiler_version":"v0.77.5"' in lt
        assert '"targets":{"openai":{"host":"arcyleung-ubuntu.tailb940e6.ts.net"}}' in lt
