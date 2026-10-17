"""
Location: pact-plugin/tests/test_emitter_occupant_identity.py (repacted EVENTS, m8a; M12)
Summary: The agent_handoff event's `agent` must name the CURRENT occupant of a task id.

M12 "journal agent field frozen": on the lead's TaskCompleted frame there is no stdin
team_name, so the emitter reads the task through the session context's team (and
read_task_json falls back to the bare tasks dir). When that team is stale and the id
was reused, it reads an OLDER occupant's file. The owner (the event's `agent`) and the
handoff then come from the first arc while task_subject comes from the platform, which
is exactly the 2026-09-19 report (agent frozen at the first arc, subject tracking the
current one). Measured over every local journal: 113 of 1,609 agent_handoff events carry
an agent that disagrees with the subject prefix.

The fix: when the disk subject disagrees with the platform's task_subject, the record
is another occupant. Resolve the task by subject among tasks/*/<id>.json. When no file,
or more than one team's file, matches, the event is still written (the harvest recovers
HANDOFFs from it) with agent null and attribution "unresolved": UNKNOWN is visible, never
a stale owner and never a dropped event (architect ruling on #124).
"""

from __future__ import annotations

import io
import json
import os
from unittest.mock import patch

import pytest

from fixtures.emitter import WRITABLE_TEST_JOURNAL

OLD_HANDOFF = {"produced": ["old schema migrations"], "decisions": ["old"]}
NEW_HANDOFF = {"produced": ["harvest of the new arc"], "decisions": ["new"]}


def write_task(config, team, task_id, subject, owner, handoff, age=0):
    path = config / "tasks" / team / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": task_id, "subject": subject, "owner": owner,
                                "status": "completed", "metadata": {"handoff": handoff}}))
    if age:
        stamp = path.stat().st_mtime - age
        os.utime(path, (stamp, stamp))
    return path


def run_lead_completion(tmp_path, monkeypatch, context_team, task_subject, task_id="35"):
    """The lead's TaskCompleted frame: no stdin team_name, no teammate_name."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    from agent_handoff_emitter import main
    events: list[dict] = []
    stdin = {"session_id": "lead-session", "hook_event_name": "TaskCompleted",
             "agent_type": "repacted:pact-orchestrator", "task_id": task_id,
             "task_subject": task_subject}
    context = {"team_name": context_team, "session_id": "", "project_dir": "",
               "plugin_root": "", "started_at": ""}
    with patch("agent_handoff_emitter.append_event", side_effect=lambda e: events.append(e) or True), \
         patch("agent_handoff_emitter.get_journal_path", return_value=WRITABLE_TEST_JOURNAL), \
         patch("agent_handoff_emitter.pact_context.get_pact_context", return_value=context), \
         patch("agent_handoff_emitter.emit_task_metadata_snapshot"), \
         patch("sys.stdin", io.StringIO(json.dumps(stdin))):
        with pytest.raises(SystemExit):
            main()
    return [e for e in events if e.get("type") == "agent_handoff"]


class TestOccupantIdentity:
    def test_a_resolved_event_carries_no_attribution_marker(self, tmp_path, monkeypatch):
        config = tmp_path / ".claude"
        write_task(config, "session-newteam", "35", "secretary: harvest", "secretary", NEW_HANDOFF)
        [event] = run_lead_completion(tmp_path, monkeypatch, "session-newteam", "secretary: harvest")
        assert "attribution" not in event

    def test_a_stale_team_read_resolves_the_current_occupant(self, tmp_path, monkeypatch):
        config = tmp_path / ".claude"
        write_task(config, "session-oldteam", "35", "schema-coder: write demo schema migrations",
                   "schema-coder", OLD_HANDOFF, age=86400)
        write_task(config, "session-newteam", "35", "secretary: harvest the UX arc",
                   "secretary", NEW_HANDOFF)
        [event] = run_lead_completion(tmp_path, monkeypatch, "session-oldteam",
                                      "secretary: harvest the UX arc")
        assert event["agent"] == "secretary"
        assert event["handoff"] == NEW_HANDOFF
        assert event["task_subject"] == "secretary: harvest the UX arc"

    def test_no_subject_match_writes_an_unresolved_event_without_the_stale_content(
            self, tmp_path, monkeypatch, capsys):
        config = tmp_path / ".claude"
        write_task(config, "session-oldteam", "35", "schema-coder: write demo schema migrations",
                   "schema-coder", OLD_HANDOFF)
        [event] = run_lead_completion(tmp_path, monkeypatch, "session-oldteam",
                                      "secretary: harvest the UX arc")
        assert event["agent"] is None and event["attribution"] == "unresolved"
        assert event["task_subject"] == "secretary: harvest the UX arc"
        assert event["handoff"] == {}                       # never the other occupant's
        assert "occupant unresolved" in capsys.readouterr().err

    def test_a_consistent_read_is_unchanged(self, tmp_path, monkeypatch):
        """Control: when the context team's record IS the current occupant, nothing moves."""
        config = tmp_path / ".claude"
        write_task(config, "session-newteam", "35", "secretary: harvest the UX arc",
                   "secretary", NEW_HANDOFF)
        write_task(config, "session-other", "35", "secretary: harvest the UX arc",
                   "other-owner", OLD_HANDOFF)
        [event] = run_lead_completion(tmp_path, monkeypatch, "session-newteam",
                                      "secretary: harvest the UX arc")
        assert (event["agent"], event["handoff"]) == ("secretary", NEW_HANDOFF)

    def test_two_teams_matching_is_unresolved_and_keeps_the_freshest_content(self, tmp_path, monkeypatch):
        config = tmp_path / ".claude"
        write_task(config, "session-oldteam", "35", "schema-coder: write migrations",
                   "schema-coder", OLD_HANDOFF)
        write_task(config, "session-a", "35", "secretary: harvest", "secretary-old",
                   OLD_HANDOFF, age=3600)
        write_task(config, "session-b", "35", "secretary: harvest", "secretary", NEW_HANDOFF)
        [event] = run_lead_completion(tmp_path, monkeypatch, "session-oldteam", "secretary: harvest")
        assert (event["agent"], event["attribution"], event["handoff"]) == (None, "unresolved", NEW_HANDOFF)

    def test_an_unresolved_event_passes_the_journal_schema(self, tmp_path):
        from shared import session_journal as sj
        ok = sj.append_event(sj.make_event("agent_handoff", agent=None, task_id="35",
                                           task_subject="s", handoff={}, attribution="unresolved"),
                             session_dir=str(tmp_path))
        assert ok
        assert not sj.append_event(sj.make_event("agent_handoff", agent=None, task_id="35",
                                                 task_subject="s", handoff={}),
                                   session_dir=str(tmp_path))       # null agent needs the marker

    def test_a_record_without_a_subject_is_not_second_guessed(self, tmp_path, monkeypatch):
        """Older fixtures and writers carry no subject; absence is not a mismatch."""
        path = tmp_path / ".claude" / "tasks" / "session-newteam" / "35.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"id": "35", "owner": "secretary", "status": "completed",
                                    "metadata": {"handoff": NEW_HANDOFF}}))
        [event] = run_lead_completion(tmp_path, monkeypatch, "session-newteam", "secretary: harvest")
        assert event["agent"] == "secretary"
