"""
Location: pact-plugin/hooks/shared/actor.py
Summary: The one authority policy over a hook frame's identity. `resolve_actor` reads the
Frame (shared/frame.py, the memoised identity resolver) and classifies its trust SOURCE;
the predicates the gates branch on live here, once, and `unknown` holds no authority.
Used by: missed_wake_scan and the doctor line (lead_surfacing), ops_frame_guard
(runs_lead_ops), dispatch_registry.CheckContext.actor (one resolution per native check).
Contract test: tests/contract/test_lead_predicate_sole_access.py pins
`pact_context.is_lead(` out of the gates; tests/test_actor.py holds the forgeries.

Sources, first match wins:
  host      the harness set the identity: `agent_id` is `name@team` or the in-process
            `a<agent_type>-<16hex>` naming a member (dispatch_helpers.trustworthy_actor_name),
            or `agent_type` is a lead spelling (pact_context.is_lead, the pure predicate).
  lead      no agent_type, no agent_id, and the session is the team's lead: leadSessionId
            in the team config, or (after a resume, when that id is stale) the plugin's own
            SessionStart record for this session (frame.is_lead's second leg).
  registry  the session registry names a member of this team for the session id. A label
            only: a row is self-asserted, so no predicate GRANTS on it.
  unknown   everything else.

Authority is `is_lead`, true only for the host and lead sources. Unknown is least
privileged, except that an unmarked frame nobody names runs the lead's operations
(`runs_lead_ops`): it is the session's own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from . import frame as frame_module

Source = Literal["host", "lead", "registry", "unknown"]


@dataclass(frozen=True)
class Actor:
    """Who is writing in this hook frame, and how far to trust it."""

    agent_id: str          # "" when the frame carries none
    name: str              # label for messages; "lead" for the lead, "" when unknown
    source: Source
    is_lead: bool          # True only when source in ("host", "lead")
    team: str
    marked: bool = False   # the frame carries an agent_id or an agent_type

    # -- the predicates the gates call (unknown and registry hold no authority) --------

    def lead_surfacing(self) -> bool:
        """Lead-only context lines (missed-wake scan, doctor brief)."""
        return self.is_lead

    def runs_lead_ops(self) -> bool:
        """Whether this frame may run the lead's `repact` operations (dispatch, accept, reject,
        amend). The lead: yes. A frame the host or the registry names as an agent, or one that
        carries an agent_id or agent_type at all: no. A frame with no agent mark that nothing
        names is the session's own (a known limit: a subagent whose host passes no frame)."""
        if self.is_lead:
            return True
        return self.source == "unknown" and not self.marked


UNKNOWN = Actor(agent_id="", name="", source="unknown", is_lead=False, team="")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _trustworthy_name(payload: dict, team: str) -> Optional[str]:
    """dispatch_helpers.trustworthy_actor_name, plus: a `name@team` id names someone in
    THIS team only (the helper returns the name half for any team)."""
    agent_id = payload.get("agent_id")
    if isinstance(agent_id, str) and "@" in agent_id:
        if agent_id.partition("@")[2].lower() != team.lower():
            return None
    try:
        from .dispatch_helpers import trustworthy_actor_name

        return trustworthy_actor_name(payload, team)
    except Exception:
        return None


def _pure_lead(payload: dict) -> bool:
    try:
        from .pact_context import is_lead

        return is_lead(payload)
    except Exception:
        return False


def _registry_member(frame: frame_module.Frame, team: str) -> str:
    """The registry row's name half when its team half is this team, else ""."""
    name, _, row_team = frame.registry_name.partition("@")
    return name if name and row_team and row_team.lower() == team.lower() else ""


def resolve_actor(payload: Any, team: Optional[str] = None) -> Actor:
    """The Actor of `payload`. `team` overrides the session's team (a gate that resolved it
    already); otherwise the Frame's. Never raises: an unreadable frame is UNKNOWN."""
    if not isinstance(payload, dict):
        return UNKNOWN
    frame = frame_module.resolve(payload)
    agent_id = _text(payload.get("agent_id"))
    try:
        team = team or frame.team
    except Exception:
        team = ""
    pure_lead = _pure_lead(payload)
    marked = bool(frame.has_agent_type or payload.get("agent_id") not in (None, ""))
    name = _trustworthy_name(payload, team) if team else None
    if name or pure_lead:
        return Actor(agent_id=agent_id, name=name or "lead", source="host",
                     is_lead=pure_lead, team=team, marked=marked)
    if not marked and frame.is_lead:
        return Actor(agent_id="", name="lead", source="lead", is_lead=True, team=team)
    registered = _registry_member(frame, team) if team else ""
    if registered:
        return Actor(agent_id=agent_id, name=registered, source="registry",
                     is_lead=False, team=team, marked=marked)
    return Actor(agent_id=agent_id, name="", source="unknown", is_lead=False, team=team,
                 marked=marked)
