"""
Location: pact-plugin/tests/test_b2_occupant_identity.py (repacted TEAMMATE, m6; M12)
Summary: The lead-side (b2) agent_handoff emit must describe the task the platform
just completed, never an older occupant of the same id.

EVENTS fixed the b1 twin (agent_handoff_emitter, test_emitter_occupant_identity.py).
b2 carries the same risk in a different shape. The platform's TaskUpdate response has
no `task` (captured frame: tests/fixtures/role_frames.py), so the gate always reads the
record from disk through the SESSION CONTEXT's team. When that team is stale (the
documented resume split-brain), the read either returns another team's same-id file
(a WHOLE stale record: owner, subject and handoff of finished work, emitted as if it
were this completion) or, when the stale team dir is absent, nothing, and b2 drops the
HANDOFF. b2 has no platform subject to re-resolve by, so it re-resolves by the write it
just caused: the completed same-id file written seconds ago. One such file resolves;
more than one is recorded with agent null and attribution "unresolved"; nothing fresher
than the context team's own file leaves today's behaviour unchanged.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import task_lifecycle_gate as tlg  # noqa: E402

LEAD = "repacted:pact-orchestrator"
OLD_HANDOFF = {"produced": ["old schema migrations"], "decisions": ["old"]}
NEW_HANDOFF = {"produced": ["harvest of the new arc"], "decisions": ["new"]}


@pytest.fixture
def emit_events(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(tlg, "append_event", lambda e: events.append(e) or True)
    return events


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return tmp_path / ".claude"


def write_task(config, team, task_id, subject, owner, handoff, age=0):
    path = config / "tasks" / team / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": task_id, "subject": subject, "owner": owner,
                                "status": "completed", "metadata": {"handoff": handoff}}))
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def lead_completes(task_id="35"):
    """The production shape: no tool_response.task, so the gate reads disk."""
    return {
        "tool_name": "TaskUpdate",
        "agent_type": LEAD,
        "tool_input": {"taskId": task_id, "status": "completed"},
        "tool_response": {"success": True, "taskId": task_id,
                          "updatedFields": ["status"],
                          "statusChange": {"from": "in_progress", "to": "completed"}},
    }


def handoffs(events):
    return [e for e in events if e.get("type") == "agent_handoff"]


class TestB2OccupantIdentity:
    def test_a_stale_team_record_is_not_emitted_as_this_completion(
            self, config, pact_context, emit_events):
        write_task(config, "session-oldteam", "35", "schema-coder: write migrations",
                   "schema-coder", OLD_HANDOFF, age=86400)
        write_task(config, "session-newteam", "35", "secretary: harvest the UX arc",
                   "secretary", NEW_HANDOFF)
        pact_context(team_name="session-oldteam", session_id="s1")
        tlg.evaluate_lifecycle(lead_completes())
        [event] = handoffs(emit_events)
        assert event["agent"] == "secretary"
        assert event["task_subject"] == "secretary: harvest the UX arc"
        assert event["handoff"] == NEW_HANDOFF

    def test_an_absent_context_team_dir_does_not_drop_the_handoff(
            self, config, pact_context, emit_events):
        write_task(config, "session-newteam", "35", "secretary: harvest the UX arc",
                   "secretary", NEW_HANDOFF)
        pact_context(team_name="session-phantom", session_id="s1")
        tlg.evaluate_lifecycle(lead_completes())
        [event] = handoffs(emit_events)
        assert (event["agent"], event["handoff"]) == ("secretary", NEW_HANDOFF)

    def test_two_fresh_candidates_are_unresolved_never_the_stale_owner(
            self, config, pact_context, emit_events):
        write_task(config, "session-oldteam", "35", "schema-coder: write migrations",
                   "schema-coder", OLD_HANDOFF, age=86400)
        write_task(config, "session-a", "35", "secretary: harvest", "secretary-a",
                   OLD_HANDOFF, age=2)
        write_task(config, "session-b", "35", "secretary: harvest", "secretary-b",
                   NEW_HANDOFF)
        pact_context(team_name="session-oldteam", session_id="s1")
        tlg.evaluate_lifecycle(lead_completes())
        [event] = handoffs(emit_events)
        assert event["agent"] is None and event["attribution"] == "unresolved"
        assert event["handoff"] == NEW_HANDOFF       # the freshest write's content
        assert event["task_subject"] == "secretary: harvest"

    def test_a_consistent_read_is_unchanged(self, config, pact_context, emit_events):
        """Control: the context team's own file is the fresh write; an older
        same-id file elsewhere does not move it."""
        write_task(config, "session-newteam", "35", "secretary: harvest the UX arc",
                   "secretary", NEW_HANDOFF)
        write_task(config, "session-other", "35", "other: old arc", "other-owner",
                   OLD_HANDOFF, age=3600)
        pact_context(team_name="session-newteam", session_id="s1")
        tlg.evaluate_lifecycle(lead_completes())
        [event] = handoffs(emit_events)
        assert (event["agent"], event["handoff"]) == ("secretary", NEW_HANDOFF)
        assert "attribution" not in event

    def test_no_fresh_write_anywhere_is_unresolved_without_stale_content(
            self, config, pact_context, emit_events):
        """EVENTS' rule (architect-pact review of #136): the completion just
        written must be fresh somewhere. When neither the context team's file
        nor any other same-id file was written inside the window, the record
        read is another occupant's: write agent null, attribution
        "unresolved" and an EMPTY handoff, never the stale owner or content."""
        write_task(config, "session-ctx", "35", "coder: build it", "coder",
                   OLD_HANDOFF, age=600)
        write_task(config, "session-other", "35", "other: older", "other-owner",
                   OLD_HANDOFF, age=7200)
        pact_context(team_name="session-ctx", session_id="s1")
        tlg.evaluate_lifecycle(lead_completes())
        [event] = handoffs(emit_events)
        assert (event["agent"], event["attribution"], event["handoff"]) == (None, "unresolved", {})
        assert event["task_subject"] != "coder: build it"

    def test_a_fresh_context_record_alone_still_resolves(
            self, config, pact_context, emit_events):
        """Control for the rule above: a context-team file written inside the
        window, with nothing fresher elsewhere, is today's read."""
        write_task(config, "session-ctx", "35", "coder: build it", "coder", NEW_HANDOFF)
        write_task(config, "session-other", "35", "other: older", "other-owner",
                   OLD_HANDOFF, age=7200)
        pact_context(team_name="session-ctx", session_id="s1")
        tlg.evaluate_lifecycle(lead_completes())
        [event] = handoffs(emit_events)
        assert (event["agent"], event["handoff"]) == ("coder", NEW_HANDOFF)
        assert "attribution" not in event

    def test_the_write_after_completion_backstop_reresolves_too(
            self, config, pact_context, emit_events):
        """b2's second call site: a metadata-only handoff write on an
        already-completed task, read through the same stale context team."""
        write_task(config, "session-oldteam", "35", "schema-coder: write migrations",
                   "schema-coder", OLD_HANDOFF, age=86400)
        write_task(config, "session-newteam", "35", "secretary: harvest the UX arc",
                   "secretary", NEW_HANDOFF)
        pact_context(team_name="session-oldteam", session_id="s1")
        tlg.evaluate_lifecycle({
            "tool_name": "TaskUpdate",
            "agent_type": LEAD,
            "tool_input": {"taskId": "35", "metadata": {"handoff": NEW_HANDOFF}},
            "tool_response": {"success": True, "taskId": "35",
                              "updatedFields": ["metadata"]},
        })
        [event] = handoffs(emit_events)
        assert (event["agent"], event["handoff"]) == ("secretary", NEW_HANDOFF)
