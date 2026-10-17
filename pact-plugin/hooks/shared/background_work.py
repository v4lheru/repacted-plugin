"""Team-scoped registry of outstanding background Bash launches, plus the unflagged-wait fire predicate.

Records only shell launches with the harness background flag or a trailing bare `&`;
other background work (monitors, subagents, MCP tasks, other `&` shapes) is invisible,
so an absent advisory is not an all-clear. Rows: teammate, subagent and lead launches.
Readers match rows by `harness_task_id`, `agent_name` or `task_ids`; a new row shape must
be checked against all three, and name-keyed readers must skip `owner_role` rows.
Never raises; state goes through `state_file`; 24h TTL at read.

Map, in file order: clocks, wait classification and anchors, registry paths and record
sanitizing, load/save/append, task predicates, outstanding_unflagged (the surfacing read),
discharge and claim extension, unflagged_fire, Layer 3 clocks, idle counters, identity
resolution (frame_team_and_name, teammate_launch_name, bind_launcher_identity), and
record_background_launch (Layer 1 writer).
Rationale: the design notes
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .background_launch import (  # noqa: F401 — re-exported for callers of this module
    command_from_frame,
    is_background_launch,
    is_harness_background_bash,
    is_shell_backgrounded_bash,
)
from . import state_file
from .intentional_wait import canonical_since, validate_wait
from .pact_context import get_team_name
from .paths import get_claude_config_dir

REGISTRY_FILENAME = "background_work.json"
UNFLAGGED_IDLE_FILENAME = "unflagged_background_idle.json"
RECORD_TTL_SECONDS = 24 * 3600
LEAD_STALE_MINUTES = 10

# Layer 3 window when only registered_at is known (not evidence of idling); a judgement, not a measurement.
LEAD_UNIDLED_STALE_MINUTES = 30

UNFLAGGED_IDLE_THRESHOLD = 3

# Row owner role for an Agent-tool subagent launch (absence means a teammate row).
# The shell lands in the lead's job list with no owner; the row carries no task ids,
# which keeps it out of every task-keyed teammate surface.
OWNER_ROLE_SUBAGENT = "subagent"

# Row owner role for the lead's own launch: the lead's turn-end gate counts only these rows.
OWNER_ROLE_LEAD = "lead"

# The roles whose rows may carry no task ids. A role outside this set does not
# survive `_sanitize_record`.
_TASKLESS_OWNER_ROLES = (OWNER_ROLE_SUBAGENT, OWNER_ROLE_LEAD)

WAIT_CLASS_MISSING = "missing"
WAIT_CLASS_NULL = "null"
WAIT_CLASS_MALFORMED = "malformed"

# Scoping anchor key, distinct from the `since` freshness clock; absent and malformed are both surfaced.
WAIT_ANCHOR_KEY = "covers_since"
ANCHOR_CLASS_ABSENT = "absent"
ANCHOR_CLASS_MALFORMED = "malformed"


def parse_iso(ts: Any) -> datetime | None:
    """Parse a tz-aware ISO-8601 timestamp, or None if unusable."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now(now: datetime | None = None) -> str:
    if now is None:
        return canonical_since()
    return now.isoformat(timespec="seconds")


def classify_wait(task: Any) -> str | None:
    """Return the wait-absence class, or None if validate_wait succeeds."""
    if not isinstance(task, dict):
        return WAIT_CLASS_MISSING
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return WAIT_CLASS_MISSING
    if "intentional_wait" not in metadata:
        return WAIT_CLASS_MISSING
    wait = metadata.get("intentional_wait")
    if wait is None:
        return WAIT_CLASS_NULL
    if validate_wait(wait):
        return None
    return WAIT_CLASS_MALFORMED


def wait_scope_anchor(wait: Any) -> "tuple[datetime | None, str | None]":
    """The timestamp scoping what a wait covers, plus its anchor class.

    Returns (covers_since, None) when present and parseable, else (since, ABSENT|MALFORMED).
    Scope on the anchor, not `since`: `since` is re-stamped and would widen the wait forward.
    The fallback keeps old waits working and the missing anchor is surfaced. Pure.
    """
    if not isinstance(wait, dict):
        return None, ANCHOR_CLASS_ABSENT
    fallback = parse_iso(wait.get("since"))
    if WAIT_ANCHOR_KEY not in wait:
        return fallback, ANCHOR_CLASS_ABSENT
    anchor = parse_iso(wait.get(WAIT_ANCHOR_KEY))
    if anchor is None:
        return fallback, ANCHOR_CLASS_MALFORMED
    return anchor, None


def wait_anchor_class(task: Any) -> str | None:
    """Anchor class for a task's VALID wait, or None when it is anchored."""
    if classify_wait(task) is not None:
        return None
    metadata = task.get("metadata") if isinstance(task, dict) else None
    wait = metadata.get("intentional_wait") if isinstance(metadata, dict) else None
    return wait_scope_anchor(wait)[1]


def _team_file(filename: str, team_name: str | None = None) -> Path | None:
    name = team_name if team_name is not None else get_team_name()
    if not isinstance(name, str) or not name:
        return None
    return get_claude_config_dir() / "teams" / name / filename


def registry_path(team_name: str | None = None) -> Path | None:
    """Team-scoped registry path, or None when the team name is unusable."""
    return _team_file(REGISTRY_FILENAME, team_name)


def unflagged_idle_path(team_name: str | None = None) -> Path | None:
    """Path of Layer 2's own idle counter (see the module docstring)."""
    return _team_file(UNFLAGGED_IDLE_FILENAME, team_name)


def _record_expired(record: dict, now: datetime) -> bool:
    registered = parse_iso(record.get("registered_at"))
    if registered is None:
        return True
    return (now - registered).total_seconds() >= RECORD_TTL_SECONDS


def _clean_task_ids(raw: Any) -> list[str] | None:
    """Normalize the task_ids field, or None when unusable (a bare string is rejected, not coerced)."""
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            return None
        out.append(item)
    return out or None


def _sanitize_record(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    agent_name = raw.get("agent_name")
    session_id = raw.get("session_id")
    task_ids = _clean_task_ids(raw.get("task_ids"))
    registered_at = raw.get("registered_at")
    # Only "subagent" and "lead" survive; only such a row may carry no task ids. The marker
    # grants nothing write access to this file does not already grant.
    owner_role = raw.get("owner_role")
    taskless_owner = isinstance(owner_role, str) and owner_role in _TASKLESS_OWNER_ROLES
    if not isinstance(agent_name, str) or not agent_name:
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    if task_ids is None and not taskless_owner:
        return None
    if parse_iso(registered_at) is None:
        return None
    out = {
        "agent_name": agent_name,
        "session_id": session_id,
        "task_ids": task_ids or [],
        "registered_at": registered_at,
    }
    if taskless_owner:
        out["owner_role"] = owner_role
    harness = raw.get("harness_task_id")
    if isinstance(harness, str) and harness:
        out["harness_task_id"] = harness
    command = raw.get("command")
    if isinstance(command, str) and command:
        out["command"] = command[:240]
    idled_at = raw.get("idled_at")
    if parse_iso(idled_at) is not None:
        out["idled_at"] = idled_at
    # Kept only when True; absence means a teammate row that expires on completion.
    if raw.get("anchor_completed") is True:
        out["anchor_completed"] = True
    return out


def _parse_records_text(text: str, now: datetime) -> list[dict]:
    try:
        raw = json.loads(text) if text.strip() else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    items = raw.get("records") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for item in items:
        record = _sanitize_record(item)
        if record is None or _record_expired(record, now):
            continue
        out.append(record)
    return out


def _teams_root() -> Path:
    """The directory every team-scoped state file must stay under."""
    return get_claude_config_dir() / "teams"


def _load_records(
    team_name: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Load non-expired records (24h TTL only). Fail-open to []."""
    path = registry_path(team_name)
    if path is None:
        return []
    now = now or utc_now()
    try:
        text = state_file.read_text(path, _teams_root())
    except FileNotFoundError:
        return []
    except (OSError, TypeError, ValueError):
        return []
    return _parse_records_text(text, now)


def _atomic_update_records(
    mutator,
    team_name: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Read-modify-write the registry under one lock. Fail-open: returns False
    on any error and never raises, including an unusable team directory and
    an undecodable registry."""
    clock = now if now is not None else utc_now()

    def _apply(text: str) -> "tuple[str, bool, bool]":
        current = _parse_records_text(text, clock)
        updated, changed = mutator(current)
        if not changed:
            return text, False, True
        clean = [r for r in (_sanitize_record(x) for x in updated) if r is not None]
        return json.dumps({"records": clean}), True, True

    try:
        path = registry_path(team_name)
        if path is None:
            return False
        return state_file.locked_update(path, _apply, _teams_root())
    except (OSError, TypeError, ValueError):
        return False


def save_records(
    records: list[dict],
    team_name: str | None = None,
) -> bool:
    """Replace the registry. Fail-open: return False on any error."""
    try:
        path = registry_path(team_name)
        if path is None:
            return False
        clean = [r for r in (_sanitize_record(x) for x in records) if r is not None]
        state_file.write_text(path, json.dumps({"records": clean}), _teams_root())
        return True
    except (OSError, TypeError, ValueError):
        return False


def append_record(
    record: dict,
    team_name: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Append one sanitized record. Fail-open. `now` drives the expiry prune inside the write."""
    clean = _sanitize_record(record)
    if clean is None:
        return False

    def _append(current: list[dict]) -> tuple[list[dict], bool]:
        current.append(clean)
        return current, True

    return _atomic_update_records(_append, team_name=team_name, now=now)


def record_task_ids(record: Any) -> list[str]:
    """The task ids a record covers. [] for anything malformed."""
    if not isinstance(record, dict):
        return []
    ids = record.get("task_ids")
    return list(ids) if isinstance(ids, list) else []


def matching_outstanding(
    task: Any,
    records: list[dict] | None = None,
    now: datetime | None = None,
    team_name: str | None = None,
) -> dict | None:
    """First outstanding record listing this task among its task_ids."""
    if not isinstance(task, dict):
        return None
    task_id = task.get("id")
    if task_id is None:
        return None
    task_id = str(task_id)
    rows = records if records is not None else _load_records(team_name, now=now)
    for record in rows:
        if task_id in record_task_ids(record):
            return record
    return None


def any_listed_task_flagged(
    record: Any,
    tasks: Any = None,
) -> bool:
    """True iff some task listed on the record carries a valid wait (no status filter).

    With `tasks` None, returns False. A record with `anchor_completed` is silenced only by
    a wait that covers its launch (`wait_covers_record`), not by an older leftover wait.
    """
    if not isinstance(tasks, list):
        return False
    listed = set(record_task_ids(record))
    if not listed:
        return False
    scoped = isinstance(record, dict) and record.get("anchor_completed") is True
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if str(task.get("id")) not in listed:
            continue
        if classify_wait(task) is not None:
            continue
        if scoped and not wait_covers_record(task, record):
            continue
        return True
    return False


def load_records_for_discharge(
    team_name: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """The UNGATED read (TTL only), for paths that must see flagged records.

    Discharge and missed_wake_scan.find_unanchored_waits need it. Anything that surfaces a
    record as outstanding must call `outstanding_unflagged` instead.
    """
    return _load_records(team_name, now=now)


def owner_anchor_tasks(tasks: Any, owner: Any) -> "tuple[list[str], bool]":
    """This owner's anchor task ids, and whether the anchor is already completed.

    Every in_progress task the owner holds, else its most recently completed one (a
    consultant); empty when it has none. Recency by integer id.
    """
    in_progress: list[str] = []
    completed_id: int | None = None
    completed: str | None = None
    if not isinstance(tasks, list):
        return [], False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if task.get("owner") != owner:
            continue
        task_id = task.get("id")
        if task_id is None:
            continue
        status = task.get("status")
        if status == "in_progress":
            in_progress.append(str(task_id))
        elif status == "completed":
            try:
                num = int(task_id)
            except (ValueError, TypeError):
                continue
            if completed_id is None or num > completed_id:
                completed_id, completed = num, str(task_id)
    if in_progress:
        return in_progress, False
    return ([completed], True) if completed is not None else ([], False)


def task_is_live(task: Any) -> bool:
    """True iff this task is still `in_progress` (the one liveness predicate)."""
    return isinstance(task, dict) and task.get("status") == "in_progress"


def has_live_listed_task(record: Any, tasks: Any) -> bool:
    """True iff some task this record covers is still live, or the record is anchor_completed.

    Consultant records have no completion expiry, only the 24h TTL (a weaker guarantee).
    """
    if not isinstance(tasks, list):
        return False
    if isinstance(record, dict) and record.get("anchor_completed") is True:
        return True
    listed = set(record_task_ids(record))
    return any(task_is_live(t) and str(t.get("id")) in listed for t in tasks)


def outstanding_unflagged(
    tasks: Any,
    team_name: str | None = None,
    now: datetime | None = None,
    records: list[dict] | None = None,
) -> list[dict]:
    """THE ONLY SANCTIONED READ PATH FOR SURFACING A RECORD TO A HUMAN OR AGENT.

    Gate A, expiry: a listed task is still in_progress. Gate B, suppression: no listed
    task carries a valid wait. Keep B out of any read the discharge uses, or the
    discharge never sees a flagged record to retire.
    """
    rows = records if records is not None else _load_records(team_name, now=now)
    if not isinstance(tasks, list):
        # No task set: surface nothing (an unevaluable gate is not a passed gate).
        return []
    out = []
    for record in rows:
        # GATE A — EXPIRY. No live task means the record is dead permanently.
        if not has_live_listed_task(record, tasks):
            continue
        # Gate B, suppression (must stay out of the discharge read).
        if any_listed_task_flagged(record, tasks):
            continue
        out.append(record)
    return out


def wait_covers_record(task: Any, record: Any) -> bool:
    """True iff this task's VALID wait acknowledges this record's launch (anchor >= registered_at).

    The flag, not the job's completion, is the acknowledgment. The `>=` keeps a wait for
    job 1 from acquitting a later job 2. Scope on the anchor, never on `since`. Pure.
    """
    if not isinstance(task, dict) or not isinstance(record, dict):
        return False
    if classify_wait(task) is not None:
        return False  # no valid wait to acknowledge anything
    metadata = task.get("metadata")
    wait = metadata.get("intentional_wait") if isinstance(metadata, dict) else None
    anchor, _ = wait_scope_anchor(wait)
    registered = parse_iso(record.get("registered_at"))
    if anchor is None or registered is None:
        return False
    return anchor >= registered


def discharge_acknowledged_for_owner(
    tasks: Any,
    owner: Any,
    team_name: str | None = None,
    now: datetime | None = None,
) -> int:
    """Drop every record that a valid wait on one of `owner`'s tasks acknowledges; return the count.

    Runs on TeammateIdle, so a flag-and-clear within one turn keeps its record (pinned by a test).
    """
    if not isinstance(tasks, list) or not owner:
        return 0
    owned = {
        str(t.get("id")): t
        for t in tasks
        if isinstance(t, dict) and t.get("owner") == owner and t.get("id") is not None
    }
    if not owned:
        return 0
    dropped = 0

    def _apply(records: list[dict]) -> tuple[list[dict], bool]:
        nonlocal dropped
        # Reset per call: the state-file writer may call this twice.
        dropped = 0
        kept = []
        for record in records:
            if any(
                task_id in owned and wait_covers_record(owned[task_id], record)
                for task_id in record_task_ids(record)
            ):
                dropped += 1
                continue
            kept.append(record)
        return kept, dropped > 0

    _atomic_update_records(_apply, team_name=team_name, now=now)
    return dropped


def extend_records_for_claim(
    owner: Any,
    task_id: Any,
    team_name: str | None = None,
    now: datetime | None = None,
) -> int:
    """Add a task its owner just claimed to every live record that owner launched; return the count.

    registered_at and anchor_completed stay as written. An absent registry creates no file.
    """
    if not isinstance(owner, str) or not owner or task_id is None:
        return 0
    task_id = str(task_id)
    if not task_id:
        return 0
    extended = 0

    def _apply(records: list[dict]) -> tuple[list[dict], bool]:
        nonlocal extended
        # Reset per call: the state-file writer may call this twice.
        extended = 0
        out = []
        for record in records:
            listed = record_task_ids(record)
            # Exclude non-teammate rows (any truthy owner_role): a member's task never joins a subagent's row.
            if (
                record.get("agent_name") == owner
                and not record.get("owner_role")
                and task_id not in listed
            ):
                record = dict(record)
                record["task_ids"] = listed + [task_id]
                extended += 1
            out.append(record)
        return out, extended > 0

    ok = _atomic_update_records(_apply, team_name=team_name, now=now)
    return extended if ok else 0


def unflagged_fire(
    task: Any,
    records: list[dict] | None = None,
    now: datetime | None = None,
    team_name: str | None = None,
    tasks: list | None = None,
) -> tuple[bool, str | None, dict | None]:
    """Fire when in_progress + outstanding record + no listed task flagged."""
    if not isinstance(task, dict):
        return False, None, None
    # Layer 2's per-task status gate; surfacing reads use outstanding_unflagged. A job ending
    # within one live task is retired by the discharge, not here.
    if not task_is_live(task):
        return False, None, None
    record = matching_outstanding(task, records=records, now=now, team_name=team_name)
    if record is None:
        return False, None, None
    if any_listed_task_flagged(record, tasks):
        return False, None, record
    wait_class = classify_wait(task)
    if wait_class is None:
        return False, None, record
    return True, wait_class, record


def stamp_idled_at(
    task_id: str,
    now: datetime | None = None,
    team_name: str | None = None,
) -> bool:
    """Set idled_at on the first matching record that lacks it."""
    if not isinstance(task_id, str) or not task_id:
        return False
    stamp = iso_now(now)
    changed = False

    def _apply(records: list[dict]) -> tuple[list[dict], bool]:
        nonlocal changed
        # Reset per call: the state-file writer may call this twice.
        changed = False
        out = []
        for record in records:
            if task_id in record_task_ids(record) and not record.get("idled_at"):
                record = dict(record)
                record["idled_at"] = stamp
                changed = True
            out.append(record)
        return out, changed

    ok = _atomic_update_records(_apply, team_name=team_name, now=now)
    return bool(ok and changed)


def effective_since(record: Any) -> tuple[datetime | None, int]:
    """The clock Layer 3 measures against and its window: (since, threshold_minutes).

    `idled_at` when present, else `registered_at` with the longer window, so a missed
    TeammateIdle cannot disable the lead surface.
    """
    if not isinstance(record, dict):
        return None, LEAD_STALE_MINUTES
    idled = parse_iso(record.get("idled_at"))
    if idled is not None:
        return idled, LEAD_STALE_MINUTES
    return parse_iso(record.get("registered_at")), LEAD_UNIDLED_STALE_MINUTES


def teammate_working(
    lead_transcript: str | None, agent_name: Any, now: datetime | None = None
) -> bool:
    """True only when the teammate's own transcript shows it working (shared/transcript_activity.py).

    Every other outcome is False, so the alarm fails toward firing.
    """
    try:
        from shared.transcript_activity import teammate_transcript, transcript_active

        path = teammate_transcript(lead_transcript, agent_name)
        if path is None:
            return False
        return transcript_active(path, now or utc_now()) is True
    except Exception:
        return False


def lead_stale(
    record: dict,
    now: datetime | None = None,
) -> bool:
    """True iff the record is older than the window its own clock carries."""
    since, threshold_minutes = effective_since(record)
    if since is None:
        return False
    now = now or utc_now()
    return (now - since).total_seconds() >= threshold_minutes * 60


def _parse_idle_counts_text(text: str) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_unflagged_idle_counts(team_name: str | None = None) -> dict:
    path = unflagged_idle_path(team_name)
    if path is None:
        return {}
    try:
        text = state_file.read_text(path, _teams_root())
    except FileNotFoundError:
        return {}
    except (OSError, TypeError, ValueError):
        return {}
    return _parse_idle_counts_text(text)


def update_unflagged_idle_counts(mutator, team_name: str | None = None) -> dict:
    """Atomic RMW of unflagged_background_idle.json. Fail-open to {}."""

    def _apply(text: str) -> "tuple[str, bool, dict]":
        counts = _parse_idle_counts_text(text)
        before = json.dumps(counts)
        updated = mutator(counts)
        if not isinstance(updated, dict):
            updated = {}
        new_text = json.dumps(updated)
        return new_text, new_text != before, updated

    try:
        path = unflagged_idle_path(team_name)
        if path is None:
            return {}
        return state_file.locked_update(path, _apply, _teams_root())
    # TypeError/ValueError: a malformed counter entry must not raise (module contract).
    except (OSError, TypeError, ValueError):
        return {}


def save_unflagged_idle_counts(counts: dict, team_name: str | None = None) -> bool:
    try:
        path = unflagged_idle_path(team_name)
        if path is None:
            return False
        text = json.dumps(counts if isinstance(counts, dict) else {})
        state_file.write_text(path, text, _teams_root())
        return True
    except (OSError, TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# Layer 1, the write path (hosted by track_files.py on PostToolUse; identity stays here).
# --------------------------------------------------------------------------


# The launch predicate lives in `background_launch` (re-exported above).


# Harness agent types, not this plugin's; a stale list is a known exposure.
_PLATFORM_AGENT_TYPES = frozenset(
    {"general-purpose", "Explore", "Plan", "statusline-setup"}
)

# An Agent-tool subagent's `agent_id`.
_SUBAGENT_ID = re.compile(r"a[0-9a-f]{16}")


def _known_agent_types() -> frozenset:
    """Agent-type stems this plugin ships, derived at runtime from agents/ (never a stale copy)."""
    try:
        agents_dir = Path(__file__).resolve().parents[2] / "agents"
        return frozenset(p.stem for p in agents_dir.glob("*.md"))
    except (OSError, IndexError):
        return frozenset()


def agent_type_names_a_member(
    agent_type: Any, team_name: str, *, agent_id: Any = None
) -> bool:
    """True iff the frame's `agent_type` is an IDENTITY (a team member's name) rather than a TYPE.

    In-process teammate frames carry the member's name in `agent_type` (fixture:
    tests/fixtures/role_frames.py). The `agent_id` shape is read first: a subagent-shaped id
    is never a member; a teammate-shaped id is compared whole. Known residuals (an unknown
    platform type equal to a member name; tmux unverified): see the doc file.
    """
    if not isinstance(agent_type, str) or not agent_type:
        return False
    if isinstance(agent_id, str):
        if _SUBAGENT_ID.fullmatch(agent_id):
            return False
        if re.fullmatch(re.escape(f"a{agent_type}-") + "[0-9a-f]{16}", agent_id):
            return _names_a_member(agent_type, team_name)
    from .session_state import is_safe_path_component

    if not is_safe_path_component(team_name):
        return False
    if agent_type in _PLATFORM_AGENT_TYPES:
        return False
    if agent_type in _known_agent_types():
        return False
    from .pact_context import _iter_members

    return any(
        isinstance(m, dict) and m.get("name") == agent_type
        for m in _iter_members(team_name)
    )


def _names_a_member(name: Any, team: str) -> bool:
    """True iff `name` is a member in `team`'s config. `team` may come from stdin."""
    from .pact_context import _iter_members
    from .session_state import is_safe_path_component

    if not isinstance(name, str) or not name or not is_safe_path_component(team):
        return False
    return any(m.get("name") == name for m in _iter_members(team))


def frame_team_and_name(input_data: Any) -> "tuple[str, str]":
    """(team, member name) for the frame's own session, "" for each part unresolved. Never raises.

    Routes: get_team_name(); the frame's team_name + teammate_name; the session registry;
    an `agent_id` of the form name@team. Route 1 yields no name.
    """
    try:
        if not isinstance(input_data, dict):
            return "", ""
        from .pact_context import init as init_context
        from .session_registry import resolve as registry_resolve

        init_context(input_data)
        team = get_team_name()
        if team:
            return team, ""
        frame_team = input_data.get("team_name")
        frame_name = input_data.get("teammate_name")
        if isinstance(frame_team, str) and _names_a_member(frame_name, frame_team):
            return frame_team, frame_name
        session_id = input_data.get("session_id")
        if isinstance(session_id, str) and session_id:
            name, _, team = (registry_resolve(session_id) or "").partition("@")
            if name and team:
                return team, name
        agent_id = input_data.get("agent_id")
        if isinstance(agent_id, str):
            name, _, team = agent_id.partition("@")
            if team and _names_a_member(name, team):
                return team, name
    except Exception:
        pass
    return "", ""


def teammate_is_separate_process(team_name: Any, member_name: Any) -> bool:
    """True iff `member_name`'s `backendType` in `team_name`'s config is exactly "tmux".

    A separate-process teammate is woken by its own job completion, so the stall layers
    skip it. Every doubt returns False (one extra advisory beats a silenced stall).
    """
    if not isinstance(team_name, str) or not isinstance(member_name, str) or not member_name:
        return False
    from .pact_context import _iter_members
    from .session_state import is_safe_path_component

    if not is_safe_path_component(team_name):
        return False
    return any(
        m.get("name") == member_name and m.get("backendType") == "tmux"
        for m in _iter_members(team_name)
    )


def teammate_launch_name(input_data: Any, team_name: str) -> str:
    """The member name of the teammate of `team_name` acting in this frame, or "".

    The launch advisory and Layer 1 both decide through this. Order: no agent_type or a
    lead spelling with an agent_id is not a teammate; agent_type naming a member; an
    agent_id name@team; else a non-lead session_id found in the session registry.
    """
    if not isinstance(input_data, dict) or not isinstance(team_name, str) or not team_name:
        return ""
    from .pact_context import LEAD_AGENT_TYPES, _read_lead_session_id
    from .session_registry import resolve as registry_resolve

    agent_type = input_data.get("agent_type")
    if not isinstance(agent_type, str) or not agent_type:
        return ""
    agent_id = input_data.get("agent_id")
    if agent_type in LEAD_AGENT_TYPES:
        # A lead spelling with no agent_id is the lead in the lead's session, and a
        # teammate spawned with the orchestrator type in any other (step 4 names it).
        if agent_id:
            return ""
    elif agent_type_names_a_member(agent_type, team_name, agent_id=input_data.get("agent_id")):
        return agent_type
    elif agent_id:
        if not isinstance(agent_id, str):
            return ""
        name, _, id_team = agent_id.partition("@")
        return name if name and id_team.lower() == team_name.lower() else ""
    session_id = input_data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return ""
    if session_id == _read_lead_session_id(team_name):
        return ""
    name, _, registry_team = (registry_resolve(session_id) or "").partition("@")
    return name if name and registry_team.lower() == team_name.lower() else ""


def is_teammate_launch_frame(input_data: Any, team_name: str) -> bool:
    """True iff the frame is a teammate of `team_name`: not the lead, and not an
    Agent-tool subagent. `teammate_launch_name` holds the steps."""
    return bool(teammate_launch_name(input_data, team_name))


def bind_launcher_identity(
    input_data: Any, team_name: str
) -> "tuple[str, str, list[str], bool] | None":
    """Return (agent_name, session_id, task_ids, anchor_completed), or None when identity is absent.

    `agent_type` is a member's name on teammate frames and a type on lead frames, so it
    is validated against the team config, never type-stripped. All in_progress tasks are
    returned; with none, the most recently completed (consultant). Silent beats wrong.
    """
    # Function-level: the whole module is imported only once a Bash frame
    # arrives, and these imports are needed only once identity is being bound.
    from .pact_context import resolve_agent_name
    from .session_registry import resolve as registry_resolve
    from .task_utils import iter_team_task_jsons

    if not isinstance(input_data, dict) or not team_name:
        return None
    session_id = input_data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None

    named_by_name = isinstance(input_data.get("agent_name"), str) and bool(
        input_data.get("agent_name")
    )
    agent_id = input_data.get("agent_id")
    named_by_id_split = isinstance(agent_id, str) and "@" in agent_id
    # The measured in-process route: neither field above is present on a Bash
    # PostToolUse frame, and `agent_type` carries the name instead.
    named_by_membership = agent_type_names_a_member(
        input_data.get("agent_type"), team_name, agent_id=input_data.get("agent_id")
    )
    registry_name = None
    # Collapse protection: without a name, an @-id or a validated membership match, refuse
    # rather than type-strip agent_type (a mis-bind names a teammate who did not launch).
    if not named_by_name and not named_by_id_split and not named_by_membership:
        resolved = registry_resolve(session_id)
        if resolved and "@" in resolved:
            registry_name = resolved.split("@")[0]
        else:
            return None

    # Record the value that passed validation: on the membership route use raw agent_type,
    # since resolve_agent_name would strip `pact-` and bind a sibling. Steps 1 and 2 still win.
    if registry_name:
        agent_name = registry_name
    elif named_by_name or named_by_id_split:
        agent_name = resolve_agent_name(input_data, team_name=team_name)
    else:
        agent_name = input_data.get("agent_type")
    if not agent_name:
        return None

    task_ids, anchor_completed = owner_anchor_tasks(
        list(iter_team_task_jsons(team_name)), agent_name
    )
    if not task_ids:
        return None
    return agent_name, session_id, task_ids, anchor_completed


def subagent_launcher_id(input_data: Any) -> str:
    """The Agent-tool subagent's own `agent_id` ("a" + 16 hex) on this frame, or "".

    An absent agent_id records nothing (absence is only inferred to mean the lead).
    """
    agent_id = input_data.get("agent_id") if isinstance(input_data, dict) else None
    if not isinstance(agent_id, str):
        return ""
    return agent_id if _SUBAGENT_ID.fullmatch(agent_id) else ""


def is_lead_launch_frame(input_data: Any) -> bool:
    """True iff the frame is the lead's own tool call: a lead `agent_type` AND no `agent_id`.

    A session_id that is not the team's leadSessionId is not the lead's, when it resolves.
    """
    from .pact_context import _read_lead_session_id, is_lead

    if not (isinstance(input_data, dict) and is_lead(input_data)
            and not input_data.get("agent_id")):
        return False
    # A teammate spawned with the orchestrator type carries a lead spelling too. Its
    # own session is not the lead's: such a frame is that teammate's, not the lead's.
    team, _name = frame_team_and_name(input_data)
    lead_session = _read_lead_session_id(team) if team else ""
    return not lead_session or input_data.get("session_id") == lead_session


def is_lead_call_moved_to_background(input_data: Any) -> bool:
    """True iff the lead's own Bash frame names a background job id in its tool_response.

    A foreground call moved to the background on timeout has no flag or `&`; this adds its row.
    """
    if not isinstance(input_data, dict) or input_data.get("tool_name") != "Bash":
        return False
    tool_response = input_data.get("tool_response")
    job_id = tool_response.get("backgroundTaskId") if isinstance(tool_response, dict) else None
    return isinstance(job_id, str) and bool(job_id) and is_lead_launch_frame(input_data)


def record_background_launch(input_data: Any, now: datetime | None = None) -> bool:
    """Write one registry row for a recordable teammate, subagent or lead launch."""
    if not is_background_launch(input_data) and not is_lead_call_moved_to_background(input_data):
        return False
    team_name, _name = frame_team_and_name(input_data)
    if not team_name:
        return False
    # Three launcher populations. The subagent check runs first; that is safe only while a
    # subagent id ("a" + 16 hex) cannot match a teammate id ("a" + name + "-" + 16 hex).
    subagent_id = subagent_launcher_id(input_data)
    # THE LEAD is the third population. Its row is what the lead's turn-end
    # gate counts, so it is written only for a frame positively the lead's.
    lead_launch = not subagent_id and is_lead_launch_frame(input_data)
    if not subagent_id and not lead_launch and not is_teammate_launch_frame(input_data, team_name):
        return False
    command = command_from_frame(input_data)
    # No durability filter: nothing here may REMOVE a launch from the registry (a text filter
    # dropped real work); the `&` check reads grammar and only adds rows.
    if subagent_id:
        # A subagent's id is its identity; no task ids, so only the TTL retires this row.
        session_id = input_data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return False
        agent_name, task_ids, anchor_completed = subagent_id, [], False
        owner_role = OWNER_ROLE_SUBAGENT
    elif lead_launch:
        # Taskless and TTL-retired like a subagent's row. The role stands in
        # for a name: every name-keyed reader skips a row carrying a role.
        session_id = input_data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return False
        agent_name, task_ids, anchor_completed = OWNER_ROLE_LEAD, [], False
        owner_role = OWNER_ROLE_LEAD
    else:
        bound = bind_launcher_identity(input_data, team_name)
        if bound is None:
            return False
        agent_name, session_id, task_ids, anchor_completed = bound
        owner_role = None
    # The harness job id lets the turn-end gate match a running job to its launcher.
    tool_response = input_data.get("tool_response")
    harness_task_id = (
        tool_response.get("backgroundTaskId") if isinstance(tool_response, dict) else None
    )
    if lead_launch and not (isinstance(harness_task_id, str) and harness_task_id):
        # The job id is the only key a lead row is ever matched on.
        return False
    return append_record(
        {
            "agent_name": agent_name,
            "session_id": session_id,
            "task_ids": task_ids,
            # Write-time fact: exempts the row from completion expiry in has_live_listed_task.
            "anchor_completed": anchor_completed,
            # Present only on a subagent's or the lead's row; absence is a teammate row.
            "owner_role": owner_role,
            "command": command,
            "harness_task_id": harness_task_id,
            # Pass `now`: a bare iso_now() reads the real clock and would diverge from the prune.
            "registered_at": iso_now(now),
        },
        team_name=team_name,
        now=now,
    )
