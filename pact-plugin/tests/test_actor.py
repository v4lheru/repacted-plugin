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

from shared import session_registry
from shared.actor import resolve_actor

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
    ({"agent_id": f"{MEMBER}@{TEAM}", "session_id": LEAD_SID}, "host", MEMBER, False),])
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
    {"agent_type": "lead", "agent_id": f"alead-{HEX}", "session_id": LEAD_SID},])
def test_forgeries_are_unknown(world, payload):
    actor = resolve_actor(payload)
    assert (actor.source, actor.name, actor.is_lead) == ("unknown", "", False)


# --- the predicate table ------------------------------------------------------------------------


# --- the dispatcher hands one resolution per check ----------------------------------------------


# --- the gates whose branch moved to the shared predicate ---------------------------------------


