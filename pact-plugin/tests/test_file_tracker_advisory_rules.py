"""When the file-conflict advisory stays quiet, and the one case it must keep.

A field session drew about 20 of these advisories and every one was harmless
(T-5af6ef74, T-917482fe): a fixer following a stopped coder, the lead editing
its own docs, the shared agent-memory inbox, and one advisory per edit of the
same file. Each rule below runs through the real hook process, as an
in-process team does: one session context, and the acting member named by the
frame.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_SESSION_ID = "aaaaaaaa-2222-4333-8444-555555555555"
_TEAM = "session-aaaaaaaa"
_SLUG = "advisory-project"
_HOOK = Path(__file__).resolve().parents[1] / "hooks" / "file_tracker.py"


def _team(tmp_path, members, tasks=()):
    """A team on disk: roster, task files, and the lead's session context."""
    config = tmp_path / ".claude"
    project = tmp_path / _SLUG
    (project / "docs").mkdir(parents=True)
    (project / "src").mkdir()
    session_dir = config / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(json.dumps({
        "team_name": _TEAM, "session_id": _SESSION_ID, "project_dir": str(project),
        "plugin_root": "", "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    _roster(tmp_path, members)
    (config / "tasks" / _TEAM).mkdir(parents=True)
    for number, (owner, status) in enumerate(tasks, start=1):
        _task(tmp_path, number, owner, status)
    return project


def _roster(tmp_path, members):
    team_dir = tmp_path / ".claude" / "teams" / _TEAM
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(json.dumps({
        "leadSessionId": _SESSION_ID,
        "members": [{"name": "team-lead", "agentType": "team-lead"}] + [
            {"name": m, "agentId": f"{m}@{_TEAM}", "agentType": "pact-backend-coder"}
            for m in members],
    }), encoding="utf-8")


def _task(tmp_path, number, owner, status):
    (tmp_path / ".claude" / "tasks" / _TEAM / f"{number}.json").write_text(json.dumps({
        "id": str(number), "subject": f"task {number}", "owner": owner, "status": status,
    }), encoding="utf-8")


def _edit(tmp_path, member, file_path, **frame_fields) -> str:
    """One PostToolUse Edit by `member` (None is the lead); returns the advisory."""
    frame = {"hook_event_name": "PostToolUse", "session_id": _SESSION_ID,
             "tool_name": "Edit", "tool_input": {"file_path": str(file_path)}}
    if member is None:
        frame["agent_type"] = "pact-orchestrator"
    else:
        frame.update(agent_type="pact-backend-coder", agent_id=f"{member}@{_TEAM}")
    frame.update(frame_fields)
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID")}
    env.update(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path / ".claude"),
               CLAUDE_PROJECT_DIR=str(tmp_path / _SLUG))
    proc = subprocess.run([sys.executable, str(_HOOK)], input=json.dumps(frame),
                          capture_output=True, text=True, timeout=30, env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout or "{}").get("hookSpecificOutput", {}).get("additionalContext", "")


def _editors(tmp_path, file_path):
    edits = json.loads((tmp_path / ".claude" / "teams" / _TEAM / "file-edits.json").read_text())
    return [e["agent"] for e in edits if e["file"] == os.path.realpath(file_path)]


def test_two_live_agents_with_open_tasks_still_draw_the_advisory(tmp_path):
    """THE KEPT CASE: a real overlap."""
    project = _team(tmp_path, ["coder-a", "coder-b"],
                    tasks=[("coder-a", "in_progress"), ("coder-b", "in_progress")])
    target = project / "src" / "shared.py"
    assert _edit(tmp_path, "coder-a", target) == ""
    warning = _edit(tmp_path, "coder-b", target)
    assert "File conflict" in warning and "coder-a" in warning, warning


def test_no_advisory_when_the_other_editor_is_stopped_with_its_tasks_completed(tmp_path):
    """A stopped agent leaves members[]; the fixer that follows it is a handover."""
    project = _team(tmp_path, ["coder", "fixer"],
                    tasks=[("coder", "completed"), ("fixer", "in_progress")])
    target = project / "src" / "shared.py"
    assert _edit(tmp_path, "coder", target) == ""
    _roster(tmp_path, ["fixer"])
    assert _edit(tmp_path, "fixer", target) == ""
    assert _editors(tmp_path, target) == ["coder", "fixer"]


def test_an_editor_with_no_member_entry_and_no_completed_work_still_draws_the_advisory(tmp_path):
    """Absence from members[] alone finishes nobody (review finding 9): a live
    subagent with no member entry is tracked under its agent_type and owns no
    task, and a member that left the roster may still hold an open task."""
    project = _team(tmp_path, ["coder", "fixer"],
                    tasks=[("coder", "in_progress"), ("fixer", "in_progress")])
    by_subagent = project / "src" / "by_subagent.py"
    assert _edit(tmp_path, "helper", by_subagent,
                 agent_type="general-purpose", agent_id="helper-7f3a") == ""
    assert _editors(tmp_path, by_subagent) == ["general-purpose"]
    assert "general-purpose" in _edit(tmp_path, "fixer", by_subagent)

    by_coder = project / "src" / "by_coder.py"
    assert _edit(tmp_path, "coder", by_coder) == ""
    _roster(tmp_path, ["fixer"])
    assert "coder" in _edit(tmp_path, "fixer", by_coder)


