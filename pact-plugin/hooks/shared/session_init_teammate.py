"""
Location: pact-plugin/hooks/shared/session_init_teammate.py
Summary: SessionStart for a separate-process teammate frame: the peer-context
    body, with no orchestrator directive.
Used by: hooks/session_init.py

Moved out of session_init.py unchanged.
"""

from __future__ import annotations

from shared.pact_context import get_session_id
from shared.peer_context import get_peer_context
from shared.session_registry import resolve as _registry_resolve


def inject_peer_context(input_data: dict, team_name: str, context_parts: list) -> None:
    """Insert the peer-context body at the head of context_parts. Never raises."""
    # Family E relocation (#806): a separate-process (e.g. tmux/iterm2)
    # teammate fires its OWN SessionStart — unlike an in-process teammate,
    # which fires SubagentStart (covered by peer_inject). The two surfaces
    # are mode-exclusive (one teammate fires exactly one), so injecting the
    # peer-context body here causes no double-injection. classify_session_role
    # is the fail-safe gate: only a genuine "teammate" frame takes this branch;
    # "lead" AND "unknown"/empty agent_type both fall to the else-branch, which
    # keeps the existing orchestrator-directive ladder UNCHANGED. Emitting the
    # marker-free body (include_role_marker=False) ALSO suppresses the
    # "YOUR PACT ROLE: orchestrator" block for teammate frames — that
    # unconditional orchestrator block was the mis-roling bug (a teammate
    # self-identifying as orchestrator); the role marker is omitted because
    # the spawn prompt already owns the role and session_init lacks agent_name
    # under tmux. This is a CONDITIONAL EMISSION, not a new numbered step.
    # O1 fix + Finding-1. team_name above is the #989-aligned name
    # (identity-matched on this teammate's OWN session_id, defaulting to
    # generate_team_name) — for a tmux teammate this is derived from the
    # teammate's OWN session, NOT the lead's team — so resolve the lead's
    # team + this teammate's own member name from the
    # self-registration registry: the teammate wrote {own session_id →
    # name@team} at its first action, so a self-lookup by our OWN
    # session_id recovers both the @team (the lead's team — the datum a
    # teammate cannot otherwise compute) AND the name (for EXACT-name
    # self-exclusion: the full peer list, not the agentType-narrowed one).
    # On a miss (no registration / in-process / any error), fall back to
    # the session-derived team_name + stdin agent_name — no worse than
    # pre-fix; _registry_resolve never raises.
    # Finding-1 (security): the resolver + get_peer_context now read a LIVE
    # config that could be malformed — wrap the whole resolve→build→insert
    # in a fail-open guard so it degrades to NO injection and NEVER lets an
    # exception reach main()'s outer except → _build_safety_net_context
    # (which would mis-role the teammate as orchestrator). Mirrors
    # peer_inject's fail-open-to-no-injection contract.
    try:
        _resolved = _registry_resolve(get_session_id())
        if _resolved and "@" in _resolved:
            _own, _, _tn = _resolved.partition("@")
        else:
            _tn, _own = team_name, input_data.get("agent_name", "")
        _peer_body = get_peer_context(
            agent_type=input_data.get("agent_type", ""),
            team_name=_tn,
            agent_name=_own,
            include_role_marker=False,
        )
        if _peer_body:
            context_parts.insert(0, _peer_body)
    except Exception:
        pass  # fail-open: no injection; never the orchestrator safety-net
