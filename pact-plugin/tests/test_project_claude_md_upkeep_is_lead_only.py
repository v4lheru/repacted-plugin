"""
Location: pact-plugin/tests/test_project_claude_md_upkeep_is_lead_only.py
Summary: A teammate frame's session start neither creates nor migrates the
         project CLAUDE.md (upstream PACT #1650); a separate-process teammate
         starting in a worktree planted a managed CLAUDE.md there. A lead frame,
         and an unknown frame (rePACTed runs a lead without --agent), still do.
Used by: pytest.
"""

import pytest

from shared.session_init_common import project_claude_md_upkeep


@pytest.mark.parametrize("role, created", [("teammate", False), ("lead", True), ("unknown", True)])
def test_only_a_non_teammate_frame_creates_the_project_claude_md(tmp_path, monkeypatch, role, created):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    context_parts, system_messages = [], []

    project_claude_md_upkeep(role, context_parts, system_messages)

    assert (tmp_path / ".claude" / "CLAUDE.md").exists() is created
