"""
Location: pact-plugin/hooks/shared/actor.py
Summary: The one authority policy over a hook frame's identity. `resolve_actor` reads the
Frame (shared/frame.py, the memoised identity resolver) and classifies its trust SOURCE;
the predicates the gates branch on live here, once, and `unknown` holds no authority.
Used by: task_lifecycle_gate (may_complete, audit_verdict_mode), handoff_ordering_gate
(sees_handoff_disk_line), evidence_gate (bridge_writes), missed_wake_scan and the doctor
line (lead_surfacing), dispatch_registry.CheckContext.actor (one resolution per native
check). Contract test: tests/contract/test_lead_predicate_sole_access.py pins
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
privileged: it holds no lead verb, and a completion of an owned task by an actor nobody
can name, or that only a registry row names, is disputed like a self-completion. A host
teammate is disputed only on its own task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from . import frame as frame_module

Source = Literal["host", "lead", "registry", "unknown"]

RECOVER = "RECOVER"
MIRROR = "MIRROR"


@dataclass(frozen=True)
class Actor:
    """Who is writing in this hook frame, and how far to trust it."""

    agent_id: str          # "" when the frame carries none
    name: str              # label for messages; "lead" for the lead, "" when unknown
    source: Source
    is_lead: bool          # True only when source in ("host", "lead")
    team: str

    # -- the predicates the gates call (unknown and registry hold no authority) --------

    def may_complete(self, owner: Any) -> bool:
        """Whether completing a task owned by `owner` stands unchallenged. The lead: always.
        A host-named teammate: unless `owner` is itself. Registry and unknown: never for an
        owned task (least privileged). The caller applies the carve-outs
        (intentional_wait.is_self_complete_exempt)."""
        if self.is_lead:
            return True
        if not isinstance(owner, str) or not owner:
            return True
        if self.source == "host":
            return owner != self.name
        return False

    def audit_verdict_mode(self) -> str:
        """RECOVER (the lead's audit_summary write is routed to lead_close_note, the
        auditor's authored value kept) or MIRROR (the author's verdict is snapshotted)."""
        return RECOVER if self.is_lead else MIRROR

    def sees_handoff_disk_line(self) -> bool:
        """The 'HANDOFF on disk: rev N' / 'NO HANDOFF ON DISK' line after a completion."""
        return self.is_lead

    def bridge_writes(self) -> bool:
        """Whether this frame's acceptance or rejection reaches the project task log."""
        return self.is_lead

    def lead_surfacing(self) -> bool:
        """Lead-only context lines (missed-wake scan, doctor brief)."""
        return self.is_lead


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
    name = _trustworthy_name(payload, team) if team else None
    if name or pure_lead:
        return Actor(agent_id=agent_id, name=name or "lead", source="host",
                     is_lead=pure_lead, team=team)
    if not frame.has_agent_type and not agent_id and frame.is_lead:
        return Actor(agent_id="", name="lead", source="lead", is_lead=True, team=team)
    registered = _registry_member(frame, team) if team else ""
    if registered:
        return Actor(agent_id=agent_id, name=registered, source="registry",
                     is_lead=False, team=team)
    return Actor(agent_id=agent_id, name="", source="unknown", is_lead=False, team=team)
