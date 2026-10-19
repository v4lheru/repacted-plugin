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
import threading
import time
from pathlib import Path

import pytest

import dispatch
import docker_lock_gate
from shared import frame, session_registry
from shared.task_utils import update_task_file

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
    ({"agent_type": "repacted:pact-orchestrator", "session_id": OTHER_SID}, True),
    ({"session_id": LEAD_SID}, True),                       # a lead launched without --agent
    ({"agent_type": "", "session_id": LEAD_SID}, True),
    ({"session_id": OTHER_SID}, False),                     # a separate-process teammate
    ({}, False),                                            # no session id
    ({"session_id": ""}, False),
    ({"agent_type": MEMBER, "session_id": LEAD_SID}, False),  # in-process teammate, same session
    ({"agent_type": "Explore", "session_id": LEAD_SID}, False),  # an Agent-tool subagent
    ({"agent_type": [], "session_id": LEAD_SID}, False),    # a non-string type is present
    ({"agent_type": "pact-orchestrator ", "session_id": OTHER_SID}, False),
    ({"agent_type": "pact-orсhestrator", "session_id": OTHER_SID}, False),  # Cyrillic с
    ({"agent_name": "team-lead", "agent_id": f"team-lead@{TEAM}", "session_id": OTHER_SID},
     False),                                                # self-declared names decide nothing
    ({"session_id": LEAD_SID + "​"}, False),
])
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


def test_dispatch_opens_the_memo_under_the_same_name():
    assert dispatch.FRAME_MEMO == frame.MEMO_MODULE




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

def test_docker_lock_names_a_tmux_teammate_from_the_registry(world, monkeypatch):
    import shared.pact_context as pc

    monkeypatch.setattr(pc, "_context_path", None)   # its own process has no team context
    monkeypatch.setattr(pc, "_cache", None)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OTHER_SID)  # register() records this session
    session_registry.register(f"{MEMBER}@{TEAM}")
    payload = {"tool_name": "Bash", "session_id": OTHER_SID}
    assert docker_lock_gate.resolve_caller(payload) == f"{MEMBER}@{TEAM}"
    # A session nobody registered, or a name that is not a member, stays unnamed.
    assert docker_lock_gate.resolve_caller({"session_id": "sid-unknown"}) is None
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-forger")
    session_registry.register(f"team-lead2@{TEAM}")
    assert docker_lock_gate.resolve_caller({"session_id": "sid-forger"}) is None


# --- aud-bugs 23: a teammate registering during the prune keeps its line ----------------------

def test_registration_during_a_prune_survives(world, monkeypatch):
    import os

    import session_end

    registry = session_registry.get_registry_path()
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({"session_id": "old", "value": "x@session-dead"}) + "\n")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OTHER_SID)
    real_replace = os.replace
    racer = []

    def replace_after_a_registration(src, dst):
        racer.append(threading.Thread(target=session_registry.register,
                                      args=(f"{MEMBER}@{TEAM}",)))
        racer[0].start()
        time.sleep(0.3)                              # the register() runs, or waits on the lock
        real_replace(src, dst)

    monkeypatch.setattr(session_end.os, "replace", replace_after_a_registration)
    assert session_end._prune_registry_dead_teams(
        registry_path=registry, teams_dir=world / "teams") == 1
    racer[0].join(5)
    assert session_registry.resolve(OTHER_SID) == f"{MEMBER}@{TEAM}"


# --- suspected: two hook writers on one task --------------------------------------------------

def test_two_hook_writers_on_one_task_keep_both_keys(world):
    seed(world, "7", subject="work", owner=MEMBER, status="in_progress", metadata={})
    other = []

    def first(task):
        other.append(threading.Thread(target=update_task_file, args=(
            TEAM, "7", lambda t: t["metadata"].update(second=True) or True)))
        other[0].start()
        time.sleep(0.3)                              # the second writer runs, or waits on the lock
        task["metadata"]["first"] = True
        return True

    assert update_task_file(TEAM, "7", first) is True
    other[0].join(5)
    meta = on_disk(world, "7")["metadata"]
    assert meta.get("first") is True and meta.get("second") is True
    assert not list((world / "tasks" / TEAM).glob("*.tmp"))


def test_the_task_writer_never_creates_or_follows(world, tmp_path):
    assert update_task_file(TEAM, "404", lambda t: True) is False
    assert not (world / "tasks" / TEAM / "404.json").exists()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"id": "8"}))
    (world / "tasks" / TEAM / "8.json").symlink_to(outside)
    assert update_task_file(TEAM, "8", lambda t: t.update(x=1) or True) is False
    assert json.loads(outside.read_text()) == {"id": "8"}


# --- T-0d6a6a65: the file-conflict notice names only the agent that edited ---------------------

def test_an_in_process_editor_is_named_by_its_frame_not_the_shared_session_row(world, monkeypatch):
    """A stale leadSessionId lets in-process teammates register under the shared session,
    last one wins. An edit from another in-process teammate must not be filed under that
    last registrant's name."""
    import file_tracker
    from shared.pact_context import resolve_agent_name

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-current")
    (world / "teams" / TEAM / "config.json").write_text(json.dumps({
        "leadSessionId": "sid-stale",
        "members": [{"name": "mate1"}, {"name": "gates1"}]}))
    session_registry.register(f"gates1@{TEAM}")            # the last in-process registrant
    editor = {"session_id": "sid-current", "agent_type": "mate1", "agent_id": f"amate1-{HEX}"}
    assert resolve_agent_name(editor, TEAM) == "mate1"
    subagent = {"session_id": "sid-current", "agent_type": "Explore", "agent_id": f"a{HEX}"}
    assert resolve_agent_name(subagent, TEAM) == "Explore"
    tmux = {"session_id": "sid-current"}                    # no agent_id: the registry names it
    assert resolve_agent_name(tmux, TEAM) == "gates1"
    tracking = world / "teams" / TEAM / "file-edits.json"
    file_tracker.track_edit("/w/SKILL.md", resolve_agent_name(editor, TEAM), "Edit",
                            str(tracking), "sid-current")
    entries = json.loads(tracking.read_text())
    assert [e["agent"] for e in entries] == ["mate1"]
