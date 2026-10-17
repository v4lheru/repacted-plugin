"""
Location: pact-plugin/hooks/shared/dispatch_site.py
Summary: The dispatch_site O_EXCL marker (namespace + fixed key) and the one
         claim → checked write → unclaim-on-failure emit, shared by the two legs
         that write dispatch_site:
           * task_lifecycle_gate — the owner-WITNESSING TaskUpdate, when the
             owner already resolves through team config;
           * journal_emitter — the Agent spawn, which is the first moment a
             freshly named owner resolves at all.
Used by: hooks/task_lifecycle_gate.py, hooks/journal_emitter.py.

WHY TWO LEGS. Live dispatch practice writes `owner` BEFORE the spawn (TaskCreate,
TaskUpdate(owner), then Agent). At that owner write the new name is not yet a
team-config member, so the owner cannot resolve to a pact specialist and the
TaskUpdate leg returns without a trace. Measured on a real session (session
e1baab9f): 7 agent_dispatch events, 2 dispatch_site, 0 journal_emit_skipped —
every one of the 7 owner writes preceded its spawn; the 2 that landed were
rescued by a later owner-bearing write. The spawn leg closes that gap. Both legs
claim the SAME (team, task_id) marker, so whichever fires first emits and the
other is a no-op: one dispatch, one site.

Hermetic: nothing here raises to the caller.
"""

from __future__ import annotations

from .agent_handoff_marker import already_emitted, unclaim
from .session_journal import append_event_checked, make_event

# O_EXCL marker namespace for the dispatch_site family — a SEPARATE namespace
# from the agent_handoff and task_metadata_snapshot marker dirs so the three
# event families can never suppress one another. Module constant, never
# input-derived.
DISPATCH_SITE_MARKER_NAMESPACE: str = ".dispatch_site_emitted"

# Fixed third key component, making the effective dedup key exactly
# (team, task_id). Three things about this literal are load-bearing and none of
# them are visible from the value, so they are written here rather than left to
# be re-derived:
#
# 1. IT MUST BE NON-EMPTY. An empty occupant hits already_emitted's degenerate
#    guard, which FAIL-OPENS: no marker is written, nothing raises, and dedup
#    silently never fires while appearing to work.
# 2. IT MUST BE A CONSTANT, NOT occupant_hash(owner, subject). The dedup has to
#    be OWNER-INDEPENDENT: re-wiring the same Task-B to a DIFFERENT owner is
#    the same dispatch site, not a second one. The same holds across the two
#    legs: the spawn leg and the owner-write leg must collapse to one site.
# 3. The literal is spelled out rather than abbreviated because these markers
#    are met in a directory listing, not here: they land as `91-dispatch_site`.
DISPATCH_SITE_MARKER_KEY: str = "dispatch_site"

# journal_emit_skipped cause for a pact-specialist spawn that owns no open work
# task at spawn time: a dispatch with no site to carry it. Distinct from the
# write-failure causes in session_journal (returned_false / raised).
SKIP_CAUSE_NO_OWNED_WORK_TASK: str = "no_owned_work_task"


def claim(team_name: str, task_id: str) -> bool:
    """Test-and-set the marker. True when it was ALREADY claimed (skip the
    emit); False when this call claimed it (emit now)."""
    return already_emitted(
        team_name, task_id, DISPATCH_SITE_MARKER_KEY,
        namespace=DISPATCH_SITE_MARKER_NAMESPACE,
    )


def release(team_name: str, task_id: str) -> None:
    """Roll back a claim whose journal write failed, so the site is not
    suppressed forever with no journal entry to show for it."""
    unclaim(
        team_name, task_id, DISPATCH_SITE_MARKER_KEY,
        namespace=DISPATCH_SITE_MARKER_NAMESPACE,
    )


def emit(team_name: str, task_id: str, variety: dict | None = None) -> bool:
    """Claim, write one dispatch_site through append_event_checked (a dropped
    write leaves a journal_emit_skipped), and release the claim on failure.

    Returns True only when this call wrote the event. An already-claimed
    marker returns False and writes nothing: the site exists.
    """
    try:
        if claim(team_name, task_id):
            return False
    except Exception:
        return False
    try:
        fields: dict = {"task_id": str(task_id)}
        if variety:
            fields["variety"] = variety
        written = append_event_checked(
            make_event("dispatch_site", **fields), "dispatch_site", str(task_id),
        )
    except Exception:
        written = False
    if not written:
        try:
            release(team_name, task_id)
        except Exception:
            pass
    return written
