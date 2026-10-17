"""Non-dispatch tools pass the bootstrap gate once SessionStart resolved the team.

The secretary spawns on demand, so a session that never dispatches never puts a
team on disk, and the marker (keyed on the team being on disk) is never written.
Headless, that denied Write for the whole session (#138, m7(1)). Lead ruling on
#243: Edit, Write and NotebookEdit may treat the session as bootstrapped from the
team name SessionStart resolved; Agent keeps requiring the on-disk witness. Since
1.4.1 editing tools pass with no team resolved too; only Agent waits.
"""

import json
from pathlib import Path

import pytest

_SESSION_ID = "resolved-team-session-0001"
_SLUG = "resolved-team-project"


def _session(monkeypatch, tmp_path, team_name):
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True)
    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8")
    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({
        "team_name": team_name,
        "session_id": _SESSION_ID,
        "project_dir": f"/tmp/{_SLUG}",
        "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)
    # No teams/ dir and no marker: the headless no-dispatch state.
    assert not (tmp_path / ".claude" / "teams").exists()
    return session_dir


def _frame(tool_name, agent_type="pact-orchestrator", tool_input=None):
    data = {"hook_event_name": "PreToolUse", "session_id": _SESSION_ID,
            "tool_name": tool_name, "tool_input": tool_input or {}}
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


@pytest.mark.parametrize("tool_name", ["Edit", "Write", "NotebookEdit"])
def test_resolved_team_allows_non_dispatch_tools_without_marker(monkeypatch, tmp_path, tool_name):
    from bootstrap_gate import _check_tool_allowed

    _session(monkeypatch, tmp_path, "session-abc12345")
    assert _check_tool_allowed(_frame(tool_name)) is None


def test_resolved_team_still_denies_dispatch_without_disk_witness(monkeypatch, tmp_path):
    from bootstrap_gate import _check_tool_allowed

    _session(monkeypatch, tmp_path, "session-abc12345")
    frame = _frame("Agent", tool_input={"name": "coder", "subagent_type": "pact-backend-coder"})
    assert _check_tool_allowed(frame)


@pytest.mark.parametrize("team_name", ["", "   "])
@pytest.mark.parametrize("tool_name", ["Edit", "Write", "NotebookEdit"])
def test_unresolved_team_allows_non_dispatch_tools_too(monkeypatch, tmp_path, team_name, tool_name):
    """1.4.1 (design rule): editing tools never wait for bootstrap."""
    from bootstrap_gate import _check_tool_allowed

    _session(monkeypatch, tmp_path, team_name)
    assert _check_tool_allowed(_frame(tool_name)) is None


def test_resolved_team_write_exits_zero_through_main(monkeypatch, tmp_path, capsys):
    import io
    from unittest.mock import patch

    from bootstrap_gate import main

    _session(monkeypatch, tmp_path, "session-abc12345")
    with patch("sys.stdin", io.StringIO(json.dumps(_frame("Write")))):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 0
    assert "PACT bootstrap required" not in capsys.readouterr().out
