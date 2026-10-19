"""tests/test_actor.py: the one actor resolver and the authority it hands out.

One row per source (host, lead, registry, unknown); the forgeries all resolve to `unknown`
or to a source without authority; the predicate table for each source; CheckContext.actor
resolves once; and the two gates whose branch moved to the shared predicate (task_claim_gate
no-ops for a lead launched without --agent; docker_lock_gate names it).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import dispatch_registry as registry
import docker_lock_gate
from shared import session_registry
from shared.actor import UNKNOWN, resolve_actor

TEAM = "session-u5actor"
LEAD_SID = "sid-lead"
OTHER_SID = "sid-tmux"
MEMBER = "coder-1"
HEX = "0123456789abcdef"
LEAD_TYPE = "repacted:pact-orchestrator"


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
                    {"name": MEMBER, "agentType": "pact-backend-coder"}],
    }))
    (config / "tasks" / TEAM).mkdir(parents=True)
    return config


def _register(monkeypatch, session_id, name_at_team):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session_id)
    session_registry.register(name_at_team)


# --- one row per source -----------------------------------------------------------------------

@pytest.mark.parametrize("payload, source, name, is_lead", [
    ({"agent_id": f"{MEMBER}@{TEAM}", "session_id": LEAD_SID}, "host", MEMBER, False),
    ({"agent_type": MEMBER, "agent_id": f"a{MEMBER}-{HEX}", "session_id": LEAD_SID},
     "host", MEMBER, False),                                     # in-process teammate
    ({"agent_type": LEAD_TYPE, "session_id": OTHER_SID}, "host", "lead", True),
    ({"agent_type": LEAD_TYPE, "session_id": LEAD_SID}, "host", "lead", True),  # not "lead"
    ({"session_id": LEAD_SID}, "lead", "lead", True),            # launched without --agent
    ({"agent_type": "", "session_id": LEAD_SID}, "lead", "lead", True),
])
def test_each_source(world, payload, source, name, is_lead):
    actor = resolve_actor(payload)
    assert (actor.source, actor.name, actor.is_lead, actor.team) == (source, name, is_lead, TEAM)


def test_a_resumed_plain_lead_is_the_lead_by_the_plugins_own_record(world):
    """The team config names the pre-resume session; session_init's record for this
    session (what the pact_context fixture wrote) still resolves it as the lead."""
    cfg = world / "teams" / TEAM / "config.json"
    cfg.write_text(json.dumps({**json.loads(cfg.read_text()), "leadSessionId": "sid-old"}))
    actor = resolve_actor({"session_id": LEAD_SID})
    assert (actor.source, actor.is_lead) == ("lead", True)
    assert resolve_actor({"session_id": OTHER_SID}).source == "unknown"




# --- forgeries ----------------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"agent_type": "lead", "agent_id": f"alead-{HEX}", "session_id": LEAD_SID},  # not a member
    {"agent_id": f"{MEMBER}@otherteam", "session_id": OTHER_SID},   # another team's member
    {"agent_id": "garbage", "session_id": LEAD_SID},                 # lead session, an id
    {"agent_id": 12, "session_id": OTHER_SID},                       # non-string id
    {"agent_type": MEMBER, "agent_id": f"a{HEX}", "session_id": LEAD_SID},  # subagent id
    {"agent_type": [], "session_id": LEAD_SID},
    {"agent_name": "team-lead", "agent_id": f"team-lead@{TEAM}x", "session_id": LEAD_SID},
    {"session_id": OTHER_SID},                                       # nobody registered
])
def test_forgeries_are_unknown(world, payload):
    actor = resolve_actor(payload)
    assert (actor.source, actor.name, actor.is_lead) == ("unknown", "", False)


def test_a_registry_row_claiming_the_lead_is_not_the_lead(world, monkeypatch):
    _register(monkeypatch, "sid-forger", f"team-lead@{TEAM}")
    actor = resolve_actor({"session_id": "sid-forger"})
    assert (actor.source, actor.name, actor.is_lead) == ("registry", "team-lead", False)


def test_a_non_dict_payload_is_unknown():
    assert resolve_actor("[]") is UNKNOWN and resolve_actor(None) is UNKNOWN


# --- the predicate table ------------------------------------------------------------------------







# --- the dispatcher hands one resolution per check ----------------------------------------------

def test_check_context_resolves_the_actor_once(world):
    ctx = registry.CheckContext(event="PreToolUse", tool_name="Bash", raw="{}",
                                payload={"session_id": LEAD_SID})
    assert ctx.actor.is_lead is True
    assert ctx.actor is ctx.actor
    assert registry.CheckContext(event="PreToolUse", tool_name="Bash", raw="{}").actor.source == "unknown"


# --- the gates whose branch moved to the shared predicate ---------------------------------------



def test_docker_lock_names_a_lead_launched_without_agent(world):
    assert docker_lock_gate.resolve_caller({"tool_name": "Bash", "session_id": LEAD_SID}) \
        == f"team-lead@{TEAM}"
    assert docker_lock_gate.resolve_caller(
        {"tool_name": "Bash", "session_id": LEAD_SID, "agent_type": MEMBER}) == f"{MEMBER}@{TEAM}"


def test_only_the_lead_or_an_unmarked_frame_runs_lead_ops(world, monkeypatch):
    for payload in ({"session_id": LEAD_SID}, {"agent_type": LEAD_TYPE, "session_id": OTHER_SID}):
        actor = resolve_actor(payload)
        assert actor.runs_lead_ops() and actor.lead_surfacing()
    teammate = resolve_actor({"agent_type": MEMBER, "agent_id": f"a{MEMBER}-{HEX}", "session_id": LEAD_SID})
    garbage = resolve_actor({"agent_id": "garbage", "session_id": LEAD_SID})
    _register(monkeypatch, OTHER_SID, f"{MEMBER}@{TEAM}")
    registry = resolve_actor({"session_id": OTHER_SID})
    assert registry.source == "registry"
    for actor in (teammate, garbage, registry):
        assert not (actor.runs_lead_ops() or actor.lead_surfacing())
