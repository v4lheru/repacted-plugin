"""
Location: pact-plugin/tests/test_session_init_execution.py
Summary: The session_init split by execution mode: the lead's SessionStart records
the execution mode, a subagents session gets its bootstrap marker there (its Teams-only writer
never runs), and the Teams-only tmux notice is left out in subagents mode.
Used by: pytest.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from shared import execution, team_policy

_SESSION_ID = "cafe0000-1111-2222-3333-444455556666"
TEAMS_ENV = {execution.ENV: "teams", execution.TEAMS_FLAG: "1"}


@pytest.fixture
def session(monkeypatch, tmp_path):
    """A lead session dir under the config dir, with a plugin root and its context file."""
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in (execution.ENV, execution.TEAMS_FLAG, "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    session_dir = tmp_path / ".claude" / "pact-sessions" / "proj" / _SESSION_ID
    session_dir.mkdir(parents=True)
    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text('{"version": "2.1.0"}')
    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({"team_name": "session-cafe0000", "session_id": _SESSION_ID,
                                        "project_dir": str(tmp_path), "plugin_root": str(plugin_root)}))
    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)
    team_policy.reset()
    yield session_dir, plugin_root
    team_policy.reset()


def test_subagents_session_start_records_the_mode_and_writes_the_marker(session, tmp_path):
    from shared.marker_schema import is_marker_set
    from session_init import start_execution

    session_dir, plugin_root = session
    assert start_execution(str(session_dir), str(tmp_path), str(plugin_root)) == "subagents"
    assert execution.read(session_dir)["mode"] == "subagents"
    assert is_marker_set(session_dir)


def test_teams_session_start_leaves_the_marker_to_its_writer(session, tmp_path, monkeypatch):
    from shared.marker_schema import is_marker_set
    from session_init import start_execution

    session_dir, plugin_root = session
    for name, value in TEAMS_ENV.items():
        monkeypatch.setenv(name, value)
    assert start_execution(str(session_dir), str(tmp_path), str(plugin_root)) == "teams"
    assert execution.read(session_dir)["mode"] == "teams"
    assert not is_marker_set(session_dir)


def _notices(tmp_path) -> list:
    from shared.session_init_common import launch_notices

    messages: list = []
    with patch("shared.session_init_common.check_additional_directories", return_value=None), \
         patch("shared.session_init_common.notice_told", return_value=False), \
         patch("shared.teammate_mode.should_emit_inprocess_notice", return_value=True):
        launch_notices({"session_id": _SESSION_ID}, str(tmp_path), "startup", False, messages)
    return messages


def test_the_tmux_notice_is_teams_only(session, tmp_path, monkeypatch):
    from shared.session_init_common import _INPROCESS_MODE_NOTICE

    assert _INPROCESS_MODE_NOTICE not in _notices(tmp_path)
    for name, value in TEAMS_ENV.items():
        monkeypatch.setenv(name, value)
    assert _INPROCESS_MODE_NOTICE in _notices(tmp_path)
