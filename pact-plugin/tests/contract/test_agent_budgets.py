"""Location: pact-plugin/tests/contract/test_agent_budgets.py
Summary: Contract on the per-agent token budget keys in agents/*.md frontmatter.
  - `autoCompactWindow` (Claude Code 2.1.312+, docs/research/claude-code-watch.md CC-1): each
    specialist caps its own compaction window; the host reads a plain integer from 100000 to
    1000000 and silently ignores anything else (`200k` reads as 200), so the value is checked
    as digits in range. pact-orchestrator is the lead persona and carries no cap.
  - `effort` (CC-5): only the secretary and the auditor run below the session's effort; every
    other agent inherits it, so quality work is never lowered by accident.
Sources: https://code.claude.com/docs/en/sub-agents.md (supported frontmatter fields; plugin
agents drop only hooks, mcpServers and permissionMode) and the Claude Code CHANGELOG 2.1.312.
"""
from __future__ import annotations

import re

from tests.contract._surfaces import PLUGIN_ROOT, frontmatter, read

SPECIALIST_WINDOW = 200000
LIGHT_WINDOW = 120000
EXPECTED_WINDOW = {
    "pact-secretary": LIGHT_WINDOW,
    "pact-auditor": LIGHT_WINDOW,
    **{name: SPECIALIST_WINDOW for name in (
        "pact-architect", "pact-backend-coder", "pact-database-engineer", "pact-devops-engineer",
        "pact-frontend-coder", "pact-preparer", "pact-qa-engineer",
        "pact-security-engineer", "pact-test-engineer")},
}
EXPECTED_EFFORT = {"pact-secretary": "low", "pact-auditor": "medium"}
PLAIN_INT = re.compile(r"\A[0-9]+\Z")


def agent_frontmatters() -> dict[str, dict[str, str]]:
    return {p.stem: frontmatter(read(p)) for p in sorted((PLUGIN_ROOT / "agents").glob("*.md"))}


def test_agents_carry_the_expected_window_and_effort():
    fms = agent_frontmatters()
    windows = {name: fm["autoCompactWindow"] for name, fm in fms.items() if "autoCompactWindow" in fm}
    efforts = {name: fm["effort"] for name, fm in fms.items() if "effort" in fm}

    bad = {n: v for n, v in windows.items()
           if not PLAIN_INT.match(v) or not 100000 <= int(v) <= 1000000}
    assert not bad, f"autoCompactWindow must be a plain integer 100000-1000000 (host ignores it otherwise): {bad}"
    assert {n: int(v) for n, v in windows.items()} == EXPECTED_WINDOW
    assert efforts == EXPECTED_EFFORT
    assert "pact-orchestrator" in fms and "pact-orchestrator" not in windows
