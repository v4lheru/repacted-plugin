"""The per-prompt bootstrap advisory once the session team is resolved (#245).

Since #243, Edit/Write/NotebookEdit pass the bootstrap gate once SessionStart resolved the
team; only Agent still waits for the team on disk. In a session that never dispatches the
marker is never written, so the advisory repeated on every prompt and kept saying Edit and
Write were blocked. With a resolved team it is now emitted once per session, makes no such
claim, and does not ask for a second bootstrap. An unresolved team keeps the old text.
"""

import json
from pathlib import Path

import pytest

_SESSION_ID = "advisory-session-0001"
_SLUG = "advisory-project"


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
        "team_name": team_name, "session_id": _SESSION_ID, "project_dir": f"/tmp/{_SLUG}",
        "plugin_root": str(plugin_root), "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)
    return session_dir


def _prompt():
    return {"hook_event_name": "UserPromptSubmit", "session_id": _SESSION_ID,
            "prompt": "fix the typo", "agent_type": "pact-orchestrator"}


def test_resolved_team_advisory_is_emitted_once_per_session(monkeypatch, tmp_path):
    from bootstrap_prompt_gate import _check_bootstrap_needed

    _session(monkeypatch, tmp_path, "session-abc12345")
    first = _check_bootstrap_needed(_prompt())
    assert first and 'Skill("repacted:bootstrap")' in first
    assert _check_bootstrap_needed(_prompt()) is None
    assert _check_bootstrap_needed(_prompt()) is None


def test_resolved_team_advisory_makes_no_claim_that_editing_is_blocked(monkeypatch, tmp_path):
    from bootstrap_prompt_gate import _check_bootstrap_needed

    _session(monkeypatch, tmp_path, "session-abc12345")
    text = _check_bootstrap_needed(_prompt())
    assert "(Edit, Write)" not in text and "mechanically blocked" not in text
    assert "Agent" in text          # the dispatch case is still named


def test_resolved_team_advisory_does_not_push_a_second_bootstrap(monkeypatch, tmp_path):
    from bootstrap_prompt_gate import _check_bootstrap_needed

    _session(monkeypatch, tmp_path, "session-abc12345")
    text = _check_bootstrap_needed(_prompt())
    assert "REQUIRED" not in text
    assert "not run yet" in text


@pytest.mark.parametrize("team_name", ["", "   "])
def test_unresolved_team_keeps_the_old_advisory_on_every_prompt(monkeypatch, tmp_path, team_name):
    from bootstrap_prompt_gate import _BOOTSTRAP_INSTRUCTION_TEMPLATE, _check_bootstrap_needed

    session_dir = _session(monkeypatch, tmp_path, team_name)
    expected = _BOOTSTRAP_INSTRUCTION_TEMPLATE.format(
        session_dir_hint=f"\n\nPACT_SESSION_DIR={session_dir}")
    assert _check_bootstrap_needed(_prompt()) == expected
    assert _check_bootstrap_needed(_prompt()) == expected
