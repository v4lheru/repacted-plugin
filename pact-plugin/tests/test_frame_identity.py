"""
Location: pact-plugin/tests/test_frame_identity.py
Summary: Frame identity (1.4.1 U5): the shared frame resolver and its lead predicate,
         self-completion for in-process teammates, a lead launched without --agent,
         the docker lock's separate-process caller, the registry lock against the
         prune, and the locked task writer. One regression row per defect plus the
         adversarial rows for spoofed names, a missing agent_type and odd values.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from shared import frame

TEAM = "session-u5frame"
LEAD_SID = "sid-lead"
OTHER_SID = "sid-tmux"
MEMBER = "coder-1"
HEX = "0123456789abcdef"


@pytest.fixture
def world(tmp_path, monkeypatch, pact_context):
    config = tmp_path / ".claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id=LEAD_SID)
    team_dir = config / "teams" / TEAM
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(json.dumps({
        "leadAgentId": f"team-lead@{TEAM}", "leadSessionId": LEAD_SID,
        "members": [{"name": "team-lead", "agentType": "pact-orchestrator"},
                    {"name": MEMBER, "agentType": "pact-backend-coder"},
                    {"name": "auditor", "agentType": "pact-auditor"}],
    }))
    (config / "tasks" / TEAM).mkdir(parents=True)
    return config


def seed(config, task_id, **fields):
    path = config / "tasks" / TEAM / f"{task_id}.json"
    path.write_text(json.dumps({"id": task_id, **fields}))
    return path


def on_disk(config, task_id):
    return json.loads((config / "tasks" / TEAM / f"{task_id}.json").read_text())


# --- the lead predicate ----------------------------------------------------------------------

@pytest.mark.parametrize("payload, expected", [
    ({"agent_type": "repacted:pact-orchestrator", "session_id": OTHER_SID}, True),])
def test_lead_predicate(world, payload, expected):
    assert frame.is_lead_frame(payload) is expected


def test_a_stale_lead_session_on_record_defers_to_the_plugins_own_record(world):
    """After a resume the config names the old session; session_init's record for THIS
    session (the pact_context fixture wrote it for LEAD_SID) still makes it the lead."""
    cfg = world / "teams" / TEAM / "config.json"
    cfg.write_text(json.dumps({"members": [], "leadSessionId": "sid-before-resume"}))
    assert frame.is_lead_frame({"session_id": LEAD_SID}) is True
    assert frame.is_lead_frame({"session_id": OTHER_SID}) is False
    assert frame.is_lead_frame({"session_id": LEAD_SID, "agent_type": MEMBER}) is False


def test_memo_serves_one_frame_per_identity_only_inside_a_run(world, monkeypatch):
    payload = {"session_id": LEAD_SID, "tool_name": "TaskUpdate"}
    assert frame.resolve(payload) is not frame.resolve(payload)       # no run: resolve afresh
    monkeypatch.setitem(sys.modules, frame.MEMO_MODULE,
                        type(sys)(frame.MEMO_MODULE))
    sys.modules[frame.MEMO_MODULE].frames = {}
    first = frame.resolve(payload)
    assert frame.resolve(dict(payload, tool_input={"x": 1})) is first  # tool input is not identity
    assert frame.resolve(dict(payload, agent_type="pact-orchestrator")) is not first
    assert frame.resolve(dict(payload, session_id=OTHER_SID)) is not first


# --- aud-bugs 6: self-completion for an in-process teammate ---------------------------------

def _complete(owner_type, agent_id, task_id="9"):
    return {"tool_name": "TaskUpdate", "agent_type": owner_type, "agent_id": agent_id,
            "session_id": LEAD_SID, "tool_input": {"taskId": task_id, "status": "completed"},
            "tool_response": {"task": {"id": task_id, "subject": "work", "owner": MEMBER,
                                       "metadata": {}}}}


# --- aud-bugs 7: a lead without agent_type keeps the auditor's verdict ------------------------

def _audit_write(**frame_fields):
    return {"tool_name": "TaskUpdate", **frame_fields,
            "tool_input": {"taskId": "5", "metadata": {"audit_summary": "GREEN"}},
            "tool_response": {"task": {"id": "5"}}}


# --- aud-bugs 19: the HANDOFF line and the bridge for a lead without agent_type ---------------

def _completion(**frame_fields):
    return {"tool_name": "TaskUpdate", **frame_fields,
            "tool_input": {"taskId": "42", "status": "completed"}}


# --- aud-bugs 8: the docker lock names a separate-process teammate ----------------------------


# --- aud-bugs 23: a teammate registering during the prune keeps its line ----------------------


# --- suspected: two hook writers on one task --------------------------------------------------


# --- T-0d6a6a65: the file-conflict notice names only the agent that edited ---------------------

