"""Append-only JSONL event store for session workflow state (survives task-store GC).

File: ~/.claude/pact-sessions/{slug}/{session_id}/session-journal.jsonl (0o600, dir 0o700).
CLI: python3 session_journal.py write|read|read-last (used by commands and hooks).
Writes take an flock around an O_APPEND short-write loop; no fsync (best effort).

Map, in file order: schema tables (_REQUIRED_FIELDS_BY_TYPE, _OPTIONAL_FIELDS_BY_TYPE),
make_event, _validate_event_schema, repeats_latest_event, append_event,
append_event_checked, read_events, latest_variety_by_task, resolve_latest_artifacts,
_ts_supersedes, resolve_arc_start, read_last_event / read_last_event_from,
_read_last_event_at, path helpers, _atomic_write, _build_cli, main. The read side
(read_events_from, _read_events_at, _parse_ts, _ts_ge, _journal_path_from) lives in
journal_reader.py and is bound here under these names.

Rationale and the per-type writer registry: the design notes
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The read side lives in the leaf journal_reader (pact_context imports it there; importing this
# module would cycle). Package path first, bare path for the direct-script run.
try:
    from shared.journal_reader import (
        journal_path_from as _journal_path_from,
        parse_ts as _parse_ts,
        read_events_at as _read_events_at,
        read_events_from,
        ts_ge as _ts_ge,
    )
except ImportError:
    from journal_reader import (  # type: ignore[no-redef]
        journal_path_from as _journal_path_from,
        parse_ts as _parse_ts,
        read_events_at as _read_events_at,
        read_events_from,
        ts_ge as _ts_ge,
    )


# Schema version for journal events.
_SCHEMA_VERSION = 1


# Reverse scans read this many trailing bytes before a full-file slurp.
_TAIL_WINDOW_BYTES = 32 * 1024


# Per-type required fields -> expected type, matching what writers produce.
# Add a TestValidateEventSchemaPerType case for each new type; a typo'd type skips these checks.
# Writers per type: the design notes.
_REQUIRED_FIELDS_BY_TYPE: dict[str, dict[str, type]] = {
    # hooks/session_init.py; dropped when stdin lacks session_id.
    "session_start": {"session_id": str, "project_dir": str},
    # shared/compaction_owner.py; verdict teammate|lead|unknown, basis content|expired.
    "compaction_attributed": {"verdict": str, "basis": str},
    # shared/compaction_owner.py; cause no_free_stamp or the exception class.
    "compaction_summary_dropped": {"cause": str},
    # commands/orchestrate.md: the FEATURE-level variety (dispatch_variety is per dispatch).
    "variety_assessed": {"task_id": str, "variety": dict},
    # No writer since 2.1.0 (old journals carry it); GC-immune 5-key mirror; no reader today.
    "dispatch_variety": {"task_id": str, "variety": dict},
    # Two emit legs share one marker per (team, task_id). variety stays OPTIONAL: an
    # un-stamped dispatch must still count, not be rejected.
    "dispatch_site": {"task_id": str},
    # cause keeps raised and returned_false apart; only skipped_type "dispatch_site" is read today (Q5).
    "journal_emit_skipped": {"skipped_type": str, "cause": str},
    # shared/ops/submit.py on a teachback; read by wrap-up Q6.
    "teachback_ack": {"task_id": str, "rationale_articulates_this_dispatch": str},
    # No writer since 2.1.0 (old journals carry it); GC-immune mirror of task metadata.
    "task_metadata_snapshot": {"task_id": str, "metadata": dict},
    # orchestrate/comPACT; session_resume subscripts p["phase"], so phase is required.
    "phase_transition": {"phase": str, "status": str},
    # Only phase is required; other checkpoint fields vary.
    "checkpoint": {"phase": str},
    # commands/orchestrate.md + comPACT.md write agent_dispatch with agent,
    # task_id, phase (all quoted strings) + scope (list).
    "agent_dispatch": {"agent": str, "task_id": str, "phase": str},
    # shared/ops/accept.py; all four are read by the secretary.
    "agent_handoff": {
        "agent": str,
        "task_id": str,
        "task_subject": str,
        "handoff": dict,
    },
    # No writer since 2.1.0 (old journals carry it): a degraded stop-loop refusal.
    "handoff_refusal_degraded": {
        "agent_type": str,
        "detail": str,
        "classes": list,
    },
    # hooks/missed_wake_scan.py; since is also the re-arm discriminator.
    "missed_wake": {
        "task_id": str,
        "agent": str,
        "since": str,
    },
    # hooks/missed_wake_scan.py; only recorded launches (background flag or trailing &),
    # so absence proves nothing. task_ids is a list; dedup key (agent, registered_at).
    "unflagged_background_wait": {
        "agent": str,
        "registered_at": str,
        "task_ids": list,
    },
    # hooks/missed_wake_scan.py, once per (task_id, log_name).
    "idle_on_finished_run": {
        "task_id": str,
        "agent": str,
        "log_name": str,
    },
    # hooks/missed_wake_scan.py on each lead prompt: kind and length, never text.
    "prompt_kind": {
        "kind": str,
        "chars": int,
    },
    # turn_end_gate on every turn end with a running job, whatever the verdict.
    "background_stop_gate": {
        "role": str,
        "verdict": str,
        "running": int,
    },
    # task_log/bridge.py: who accepted a linked platform task; written before the task log.
    "project_task_acceptance": {
        "ptask": str,
        "op": str,
        "rev": int,
        "t": str,
    },
    # commands/orchestrate.md; CLI-only.
    "s2_state_seeded": {"worktree": str, "agents": list, "boundaries": dict},
    # commands/orchestrate.md + comPACT.md write commit with sha, message,
    # phase (all quoted strings).
    "commit": {"sha": str, "message": str, "phase": str},
    # commands/peer-review.md writes review_dispatch with pr_number (unquoted
    # int), pr_url (quoted string), reviewers (JSON list).
    "review_dispatch": {"pr_number": int, "pr_url": str, "reviewers": list},
    # commands/peer-review.md writes review_finding with severity, finding,
    # reviewer, task_id (all quoted strings).
    "review_finding": {"severity": str, "finding": str, "reviewer": str},
    # commands/peer-review.md writes remediation with cycle (unquoted int),
    # items (JSON list), fixer (quoted string).
    "remediation": {"cycle": int, "items": list, "fixer": str},
    # commands/peer-review.md writes pr_ready with pr_number (unquoted int),
    # pr_url (quoted string), commits (unquoted int).
    "pr_ready": {"pr_number": int, "pr_url": str, "commits": int},
    # commands/pause.md; pr_number and pr_url are optional.
    "session_paused": {
        "branch": str,
        "worktree_path": str,
        "consolidation_completed": bool,
    },
    # reference/pause/checkpoint.md; list fields are checked shallowly.
    "session_refreshed": {
        "consolidation_completed": bool,
        "halt_active": bool,
    },
    # commands/bootstrap.md at confirmed resumption; refresh_ts binds one refresh.
    "session_refresh_consumed": {
        "refresh_ts": str,
    },
    # Required: _pause_is_spent matches pause_ts exactly, so a missing value would never spend.
    "session_pause_consumed": {
        "pause_ts": str,
    },
    # hooks/session_init.py; presence is the whole signal (read by the secretary).
    "session_resumption_surfaced": {},
    # hooks/session_end.py and wrap-up; no required fields.
    "session_end": {},
    # Empty registration activates the optional-field checks (unknown types skip them).
    "cleanup_summary": {},
    # Existence is the signal for session_end.check_unpaused_pr.
    "session_consolidated": {},
    # Lead-frame emit sites (plan-mode, peer-review, orchestrate phases); paths is a full
    # list per emit, checked element-wise in _validate_event_schema.
    "artifact_paths": {"workflow": str, "feature": str, "paths": list},
    # hooks/unattended_gate.py and hooks/docker_lock_gate.py; never question or command text.
    "gate_refusal": {"hook": str, "verdict": str, "rule": str},
    # bin/repact-unattended and hooks/session_init.py.
    "unattended_mode": {"state": str, "cause": str},
    # bin/repact-unattended while the mode is on; short, single-line lead words.
    "unattended_default": {"ref": str, "question": str, "chose": str, "why": str},
    "unattended_halt": {"task_id": str, "question": str},
    # No writer since 2.1.0 (old journals carry it); byte counts only, summed by bin/repact-report.
    "metadata_write": {"task_id": str, "key": str, "bytes": int},
    # hooks/task_store_probe.py: the host did not write the task file it claimed to.
    "task_store_absent": {"team": str, "task_id": str, "misses": int},
}


# Per-type optional fields: absent passes, present must match the type.
# A type needs a _REQUIRED_FIELDS_BY_TYPE entry (even {}) to activate these.
# Add a TestValidateOptionalFieldTypes case for each new field.
_OPTIONAL_FIELDS_BY_TYPE: dict[str, dict[str, type]] = {
    # Clamped to str by session_init; this catches a writer that skips the clamp.
    "session_start": {"source": str},
    # hooks/dispatch.py: how the check refused (exit_2, deny, ask, decision_block); `rule`
    # is the stable id. Writers that journal their own refusals may omit it.
    "gate_refusal": {"channel": str},
    # Set when check_unpaused_pr finds an open, unpaused PR.
    "session_end": {"warning": str},
    # A pause with no PR omits pr_number/pr_url; resume_hint is shown on resume.
    "session_paused": {"pr_number": int, "pr_url": str, "resume_hint": str},
    # shared/compaction_owner.py adds `latency_s`, the seconds from staging a
    # summary to resolving it, whenever the staged record's time is readable.
    "compaction_attributed": {"latency_s": float},
    # ids: jobs concerned; cause: why allowed; ended: jobs the frame lists as finished.
    "background_stop_gate": {"ids": list, "cause": str, "ended": dict},
    # Lets sync ignore a record written for another repo.
    "project_task_acceptance": {"main": str},
    # *_ran tell "ran, found nothing" from "never ran"; counts only, no names.
    "cleanup_summary": {
        "teams_reaped": int,
        "teams_skipped": int,
        "tasks_reaped": int,
        "tasks_skipped": int,
        "teams_ttl_days": int,
        "tasks_ttl_days": int,
        "teams_ran": bool,
        "tasks_ran": bool,
    },
    # Audit fields for resume summaries; counts may be 0.
    "session_consolidated": {
        "pass": int,
        "task_count": int,
        "memories_saved": int,
    },
    # Mid-flight pointers, present only with an active workstream; enums are not enforced here.
    "session_refreshed": {
        "halt_task_ids": list,
        "feature_task_id": str,
        "feature_subject": str,
        "team_name": str,
        "next_phase": str,
        "worktrees": list,
        "pr_number": int,
    },
    "missed_wake": {
        "task_subject": str,
        "reason": str,
    },
    "unflagged_background_wait": {
        "command": str,
    },
    # straggler is absent on rows written before it existed.
    "prompt_kind": {
        "tag": str,
        "straggler": bool,
    },
    # concern is present only when the ack is "no" or "concern".
    "teachback_ack": {
        "concern": str,
    },
    # Absence must stay legal: it is what an un-stamped dispatch looks like.
    "dispatch_site": {
        "variety": dict,
    },
    # Top-level scope "dispatch" marks a per-dispatch event; absence means feature level.
    "variety_assessed": {
        "scope": str,
    },
    # agent is None with attribution "unresolved" when the occupant of a reused id is unknown.
    "agent_handoff": {
        "attribution": str,
    },
    # Optional: some skip causes have no task; agent names the spawn on no_owned_work_task.
    "journal_emit_skipped": {
        "task_id": str,
        "agent": str,
    },
    # occupant joins against agent_handoff's identity; truncated only when bounding fired.
    "task_metadata_snapshot": {
        "subject": str,
        "owner": str,
        "task_type": str,
        "truncated": bool,
        "occupant": str,
    },
    "remediation": {
        "task_id": str,
    },
    # task_id is absent for plan-mode and peer-review syntheses.
    "artifact_paths": {
        "task_id": str,
    },
}


# --- Write API ---


def make_event(event_type: str, **fields: Any) -> dict[str, Any]:
    """Construct a journal event dict with v=1 and ts=now (UTC), unless the caller passes ts."""
    event: dict[str, Any] = {
        "v": _SCHEMA_VERSION,
        "type": event_type,
    }
    event.update(fields)
    # setdefault keeps a caller-supplied ts.
    event.setdefault(
        "ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    return event


def _validate_event_schema(event: dict[str, Any]) -> tuple[bool, str]:
    """Validate an event's baseline and per-type schema; return (ok, reason).

    Baseline: v is an int (not bool), type is a non-blank str. Known types also need
    their required fields; optional fields are type-checked when present. int rejects
    bool, str rejects blank. Unknown types pass per-type checks by design.
    """
    v = event.get("v")
    if not isinstance(v, int) or isinstance(v, bool):
        return False, "v must be int"
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return False, "type must be non-empty str"
    required = _REQUIRED_FIELDS_BY_TYPE.get(event_type)
    if required is None:
        # Unknown event type — opt-in enforcement, pass through.
        return True, "ok"
    for field, expected_type in required.items():
        if (event_type == "agent_handoff" and field == "agent" and event.get(field) is None
                and event.get("attribution") == UNRESOLVED_ATTRIBUTION):
            continue  # an unresolved occupant is recorded, never dropped (see below)
        if field not in event or event[field] is None:
            return (
                False,
                f"missing required field '{field}' for type '{event_type}'",
            )
        value = event[field]
        # int fields must reject bool even though bool subclasses int —
        # symmetric with the baseline v check above.
        if expected_type is int and isinstance(value, bool):
            return (
                False,
                f"field '{field}' for type '{event_type}' must be int, "
                f"got bool",
            )
        if not isinstance(value, expected_type):
            return (
                False,
                f"field '{field}' for type '{event_type}' must be "
                f"{expected_type.__name__}, got {type(value).__name__}",
            )
        # A blank str field passes isinstance but breaks every consumer.
        if expected_type is str and not value.strip():
            return (
                False,
                f"field '{field}' for type '{event_type}' must be "
                f"non-empty string",
            )
    # Element-level backstop for artifact_paths only: no non-str or blank path reaches disk.
    if event_type == "artifact_paths":
        for element in event["paths"]:
            if not isinstance(element, str) or not element.strip():
                return (
                    False,
                    "field 'paths' for type 'artifact_paths' must contain "
                    "only non-empty strings",
                )
    # Optional fields: absent passes, present must match (same bool/blank rules).
    optional = _OPTIONAL_FIELDS_BY_TYPE.get(event_type, {})
    for field, expected_type in optional.items():
        if field not in event or event[field] is None:
            continue  # Absent optional field — pass through.
        value = event[field]
        if expected_type is int and isinstance(value, bool):
            return (
                False,
                f"optional field '{field}' for type '{event_type}' must "
                f"be int, got bool",
            )
        if not isinstance(value, expected_type):
            return (
                False,
                f"optional field '{field}' for type '{event_type}' must "
                f"be {expected_type.__name__}, got {type(value).__name__}",
            )
        if expected_type is str and not value.strip():
            return (
                False,
                f"optional field '{field}' for type '{event_type}' must "
                f"be non-empty string",
            )
        # A pause names a real PR or none (null). Zero or a negative number
        # was accepted and then hid the pause at session start.
        if event_type == "session_paused" and field == "pr_number" and value <= 0:
            return (
                False,
                f"optional field '{field}' for type '{event_type}' must "
                f"be a positive int, or null for a pause with no PR",
            )
    return True, "ok"


# --- Repeat suppression (EVENTS m8a) ---

# Events a hook emits while command prose may still write them too: a write that
# repeats the LATEST event with the same key is skipped, so they never double-write.
# Keyed on payload, never on a time window; v, ts and unlisted fields never decide.
UNRESOLVED_ATTRIBUTION = "unresolved"

# A block's literal agent name never replaces a real spawned name.
BLOCK_AGENT_LITERALS = frozenset({"preparer", "architect", "test-engineer"})

DEDUPED_EVENT_KEYS: dict[str, tuple[str, ...]] = {
    "variety_assessed": ("task_id", "scope"),
    "agent_dispatch": ("task_id",),
    "phase_transition": ("phase",),
}
_DEDUPE_COMPARED_FIELDS: dict[str, tuple[str, ...]] = {
    "variety_assessed": ("variety",),
    "agent_dispatch": ("phase",),
    "phase_transition": ("status", "skip_reason"),
}


def _comparable(event_type: str, field: str, value: Any) -> Any:
    """A field value normalised for the repeat check. Rationale strings never
    decide a repeat: the journal mirrors the scores, not the prose."""
    if event_type == "variety_assessed" and field == "variety" and isinstance(value, dict):
        return {k: v for k, v in value.items() if not str(k).endswith("_rationale")}
    if field == "skip_reason" and value is None:
        return ""
    return value


def repeats_latest_event(journal: Path, event: dict[str, Any]) -> bool:
    """True when `event` is a deduped type and the latest journal event with the
    same key carries identical compared fields. Any read problem answers False,
    so a failed check writes (a double write) rather than drops an event."""
    event_type = event.get("type")
    keys = DEDUPED_EVENT_KEYS.get(event_type)
    if not keys:
        return False
    try:
        key = tuple(event.get(k) for k in keys)
        latest = None
        for prior in _read_events_at(journal, event_type):
            if tuple(prior.get(k) for k in keys) == key:
                latest = prior
        if latest is None:
            return False
        same = all(
            _comparable(event_type, f, latest.get(f)) == _comparable(event_type, f, event.get(f))
            for f in _DEDUPE_COMPARED_FIELDS[event_type]
        )
        if same and event_type == "agent_dispatch":
            same = latest.get("agent") == event.get("agent") or event.get("agent") in BLOCK_AGENT_LITERALS
        return same
    except Exception:
        return False


def append_event(event: dict[str, Any], session_dir: str | None = None) -> bool:
    """Append one event to the current session's journal (or to `session_dir`).

    Returns True if the write landed, False on any error or schema rejection (fail-open).
    """
    try:
        # Fail-open: the reason is dropped here; the CLI path prints it.
        ok, _reason = _validate_event_schema(event)
        if not ok:
            return False

        # Auto-set timestamp if missing
        if "ts" not in event:
            event["ts"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        # Derive path from session context (implicit — current session)
        journal = _journal_path_from(session_dir) if session_dir else _journal_path()
        if journal is None:
            # Warn (do not fail) when the implicit API runs before pact_context.init().
            if not _pact_context_is_initialized():
                print(
                    "session_journal: append_event called before "
                    "pact_context.init() — returning False (this may "
                    "indicate a hook missing session_id)",
                    file=sys.stderr,
                )
            return False

        # A repeat of the latest same-key event is already recorded: success,
        # nothing to write (DEDUPED_EVENT_KEYS).
        if repeats_latest_event(journal, event):
            return True

        # Ensure directory exists (mkdir -p with 0o700)
        journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Serialize and write atomically via O_APPEND
        entry = json.dumps(event, separators=(",", ":")) + "\n"
        if not _atomic_write(journal, entry.encode("utf-8")):
            print(
                "session_journal: append_event failed: write error",
                file=sys.stderr,
            )
            return False
        return True

    except Exception as e:
        print(
            f"session_journal: append_event failed: {e}",
            file=sys.stderr,
        )
        return False


# --- Read API ---


# Cap on rendered exception text reaching stderr / the skip record.
_CHECKED_ERROR_TEXT_MAX = 200

# Raised and returned-false stay distinct: different origins, different fixes.
SKIP_CAUSE_RETURNED_FALSE = "returned_false"
SKIP_CAUSE_RAISED = "raised"


def _checked_error_text(error: BaseException) -> str:
    """Total renderer for an arbitrary exception: always returns a str."""
    try:
        type_name = type(error).__name__
    except BaseException:  # noqa: BLE001 — hostile metaclass must not escape
        type_name = "exception"
    if type(type_name) is not str:
        type_name = "exception"
    try:
        text = f"{type_name}: {error}"
    except BaseException:  # noqa: BLE001 — hostile __str__ must not escape
        text = f"{type_name}: <exception str() raised>"
    return _bounded_printable(text)


def _bounded_printable(text: str) -> str:
    """Bound to _CHECKED_ERROR_TEXT_MAX, then strip non-printables."""
    truncated = len(text) > _CHECKED_ERROR_TEXT_MAX
    if truncated:
        text = text[:_CHECKED_ERROR_TEXT_MAX]
    text = "".join(c if c.isprintable() else " " for c in text)
    return text + "...[truncated]" if truncated else text


def append_event_checked(
    event: dict, what: str, task_id: str = "", session_dir: str | None = None
) -> bool:
    """``append_event`` that records a ``journal_emit_skipped`` event when the write does not land.

    Returns the write outcome and never raises. The skip record uses a bare
    ``append_event`` (no recursion), so a journal that cannot be written at all
    loses both silently; see the design notes.
    """
    try:
        written = (
            append_event(event)
            if session_dir is None
            else append_event(event, session_dir=session_dir)
        )
        cause = SKIP_CAUSE_RETURNED_FALSE
        detail = "append_event returned False"
    except Exception as exc:
        written = False
        cause = SKIP_CAUSE_RAISED
        detail = f"append_event raised {_checked_error_text(exc)}"
    if written:
        return True

    safe_task_id = ""
    try:
        if task_id:
            raw = task_id if type(task_id) is str else f"{task_id}"
            safe_task_id = _bounded_printable(raw)
    except BaseException:  # noqa: BLE001 — rendering must not break the record
        safe_task_id = ""

    # Durable half — the record both checks read. Bare append_event, result
    # ignored: see the NO RECURSION note above.
    try:
        fields = {"skipped_type": what, "cause": cause}
        if safe_task_id:
            fields["task_id"] = safe_task_id
        skip = make_event("journal_emit_skipped", **fields)
        if session_dir is None:
            append_event(skip)
        else:
            append_event(skip, session_dir=session_dir)
    except BaseException:  # noqa: BLE001 — a failed skip record terminates here
        pass

    # Diagnostic half — unverified reach; wrapped because a raising print on a
    # PostToolUse path would flip an exit code that stdout JSON depends on.
    try:
        suffix = f" task_id={safe_task_id}" if safe_task_id else ""
        print(
            f"PACT journal emit dropped: {what} ({detail}){suffix}",
            file=sys.stderr,
        )
    except BaseException:  # noqa: BLE001 — diagnostic must not flip the exit code
        pass
    return False


def read_events(
    event_type: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read events from the current session's journal, optionally filtered by type.
    """
    try:
        journal = _journal_path()
        if journal is None:
            # Warn only when pact_context was never initialised (the missing-init bug).
            if not _pact_context_is_initialized():
                print(
                    "session_journal: read_events called before "
                    "pact_context.init() — returning [] (this may indicate "
                    "a hook missing session_id)",
                    file=sys.stderr,
                )
            return []
        return _read_events_at(journal, event_type, since)
    except Exception:
        return []


def latest_variety_by_task(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The LATEST variety_assessed event per task_id, feature-level and dispatch-marked
    alike. How variety consumers must resolve a task: an old journal can
    hold a FEATURE row for a work task stamped before its dispatch, then a
    DISPATCH row at the wiring; the later row is the truth for that task."""
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("type") == "variety_assessed" and isinstance(event.get("task_id"), str):
            latest[event["task_id"]] = event
    return latest


def resolve_latest_artifacts(
    events: list[dict[str, Any]],
    feature: str,
) -> dict[str, list[str]]:
    """Resolve the latest path-list per workflow for one feature (pure, no I/O).

    Each artifact_paths event carries the complete list for its (workflow, feature),
    so the latest event wins and lists are never merged; equal ts: last written wins.
    Malformed events and non-str paths are skipped; bad ts never raises.
    """
    latest_by_workflow: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("feature") != feature:
            continue
        workflow = event.get("workflow")
        paths = event.get("paths")
        if not isinstance(workflow, str) or not isinstance(paths, list):
            continue
        prior = latest_by_workflow.get(workflow)
        if prior is None or _ts_supersedes(event.get("ts"), prior.get("ts")):
            latest_by_workflow[workflow] = event
    return {
        workflow: [p for p in event["paths"] if isinstance(p, str)]
        for workflow, event in latest_by_workflow.items()
    }


def _ts_supersedes(candidate_ts: Any, incumbent_ts: Any) -> bool:
    """True if a later-iterated `candidate_ts` should supersede the incumbent (newer or equal).

    Fail-open: an unparseable candidate never wins; an unparseable incumbent always
    loses; naive timestamps are read as UTC.
    """
    try:
        candidate = _parse_ts(candidate_ts)
    except (ValueError, TypeError):
        return False
    try:
        incumbent = _parse_ts(incumbent_ts)
    except (ValueError, TypeError):
        return True
    # Read a naive ts as UTC so naive-vs-aware compares by instant.
    candidate = candidate if candidate.tzinfo is not None else candidate.replace(tzinfo=timezone.utc)
    incumbent = incumbent if incumbent.tzinfo is not None else incumbent.replace(tzinfo=timezone.utc)
    try:
        return candidate >= incumbent
    except TypeError:
        return False


def resolve_arc_start(
    variety_assessed_events: list[dict[str, Any]],
    feature_task_id: str,
) -> str | None:
    """The current arc's start for `--since`: ts of the latest feature-level variety_assessed.

    Matches `feature_task_id`; skips dispatch-scoped events and unparseable ts. Pure.
    """
    latest_ts: str | None = None
    latest_dt: datetime | None = None
    for event in variety_assessed_events:
        if not isinstance(event, dict) or event.get("scope") == "dispatch":
            continue
        if str(event.get("task_id")) != str(feature_task_id):
            continue
        ts = event.get("ts")
        if not ts:
            continue
        try:
            dt = _parse_ts(ts)
            if latest_dt is None or dt > latest_dt:
                latest_dt, latest_ts = dt, ts
        except (ValueError, TypeError):
            continue
    return latest_ts


def read_last_event(
    event_type: str,
    since: str | None = None,
    exclude_scope: str | None = None,
) -> dict[str, Any] | None:
    """
    Read the most recent event of a given type from the current session's journal.
    """
    try:
        journal = _journal_path()
        if journal is None:
            # Warn only when pact_context was never initialised (the missing-init bug).
            if not _pact_context_is_initialized():
                print(
                    "session_journal: read_last_event called before "
                    "pact_context.init() — returning None (this may "
                    "indicate a hook missing session_id)",
                    file=sys.stderr,
                )
            return None
        return _read_last_event_at(journal, event_type, since, exclude_scope)
    except Exception:
        return None


def read_last_event_from(
    session_dir: str,
    event_type: str,
    since: str | None = None,
    exclude_scope: str | None = None,
) -> dict[str, Any] | None:
    """
    Read the most recent event of a given type from a specific session's journal.
    """
    if not session_dir:
        # Warn before the silent None for an unset session_dir.
        print(
            "session_journal: read_last_event_from called with "
            "empty session_dir",
            file=sys.stderr,
        )
        return None
    journal = _journal_path_from(session_dir)
    return _read_last_event_at(journal, event_type, since, exclude_scope)


def _scan_lines_for_event(
    lines: list[str],
    event_type: str,
    since: str | None = None,
    exclude_scope: str | None = None,
) -> dict[str, Any] | None:
    """Reverse-scan decoded lines for the latest `event_type` event, honouring since/exclude_scope.

    A malformed or non-dict line is skipped, never aborting the scan. exclude_scope
    lets variety recovery skip per-dispatch "dispatch" mirrors.
    """
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                continue
            if event.get("type") == event_type and _ts_ge(
                event.get("ts"), since
            ):
                if (
                    exclude_scope is not None
                    and event.get("scope") == exclude_scope
                ):
                    continue
                return event
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _read_last_event_at(
    journal: Path,
    event_type: str,
    since: str | None = None,
    exclude_scope: str | None = None,
) -> dict[str, Any] | None:
    """Shared reverse scan: the trailing _TAIL_WINDOW_BYTES first, then the whole file.

    Decodes with errors="replace" so one bad byte cannot hide session_paused.
    """
    try:
        if not journal.exists():
            return None

        size = journal.stat().st_size
        if size == 0:
            return None

        if size <= _TAIL_WINDOW_BYTES:
            # Small journal: single full read, no tail-window optimization.
            return _scan_lines_for_event(
                journal.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines(),
                event_type,
                since,
                exclude_scope,
            )

        # Large journal: tail-window-first, full-slurp fallback.
        with journal.open("rb") as fh:
            fh.seek(size - _TAIL_WINDOW_BYTES)
            tail_bytes = fh.read()
        tail_text = tail_bytes.decode("utf-8", errors="replace")
        tail_lines = tail_text.splitlines()
        # The tail window starts mid-line: drop the partial first line.
        if tail_lines:
            tail_lines = tail_lines[1:]

        match = _scan_lines_for_event(
            tail_lines, event_type, since, exclude_scope
        )
        if match is not None:
            return match

        # Tail miss: fall back to the full file.
        return _scan_lines_for_event(
            journal.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines(),
            event_type,
            since,
            exclude_scope,
        )

    except Exception:
        return None


def get_journal_path() -> str:
    """
    Return the absolute path to the journal file for the current session.
    """
    journal = _journal_path()
    if journal is None:
        return ""
    return str(journal)


# --- Internal ---


def _get_session_dir() -> str:
    """
    Resolve session directory via pact_context.get_session_dir().
    """
    try:
        from shared.pact_context import get_session_dir
    except ImportError:
        from pact_context import get_session_dir  # type: ignore[no-redef]
    return get_session_dir()


def _pact_context_is_initialized() -> bool:
    """Return True iff pact_context.init() has run in this process (drives the missing-init warning)."""
    try:
        from shared.pact_context import is_initialized
    except ImportError:
        from pact_context import is_initialized  # type: ignore[no-redef]
    return is_initialized()


def _journal_path() -> Path | None:
    """
    Derive the journal file path from the current session context.
    """
    session_dir = _get_session_dir()
    if not session_dir:
        return None
    return Path(session_dir) / "session-journal.jsonl"


def _validate_cli_session_dir(session_dir: str) -> int:
    """Validate CLI --session-dir: non-empty and absolute. Return 1 (with a stderr reason) or 0."""
    if not session_dir:
        print(
            "session_journal: --session-dir must be non-empty",
            file=sys.stderr,
        )
        return 1
    if not Path(session_dir).is_absolute():
        print(
            "session_journal: --session-dir must be an absolute path",
            file=sys.stderr,
        )
        return 1
    return 0


def _atomic_write(path: Path, data: bytes) -> bool:
    """Append *data* to *path* under an exclusive flock; True on success.

    The lock keeps writes larger than PIPE_BUF from interleaving; the loop retries
    short writes. No fsync, on purpose (hot path); see the doc file.
    """
    try:
        fd = os.open(
            str(path),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            return False
        try:
            view = memoryview(data)
            total = 0
            while total < len(view):
                try:
                    n = os.write(fd, view[total:])
                except OSError:
                    return False
                if n <= 0:
                    # Non-progressing write: fail rather than spin.
                    return False
                total += n
            return True
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# --- CLI ---


def _build_cli():
    """Build the journal CLI. argparse stays off the hook import path."""
    import argparse

    def _cli_examples(*lines: str) -> str:
        return "Examples:\n" + "\n".join(f"  {line}" for line in lines)

    class _TeachParser(argparse.ArgumentParser):
        """Usage errors stay exit 2 and append one pasteable example on stderr."""

        _teach_example = ""

        def error(self, message):
            self.print_usage(sys.stderr)
            extra = (
                f"\n\nExamples:\n  {self._teach_example}\n"
                if self._teach_example
                else "\n"
            )
            self.exit(2, f"{self.prog}: error: {message}{extra}")

    prog = sys.argv[0]
    fmt = argparse.RawDescriptionHelpFormatter
    # Interpreter-prefixed so a pasted example executes verbatim: the script
    # has no shebang or exec bit, so the bare path is not shell-executable.
    write_ex = (
        f'python3 "{prog}" write'
        " --type decision --session-dir /abs/session --stdin"
    )
    read_ex = f'python3 "{prog}" read --session-dir /abs/session'
    last_ex = (
        f'python3 "{prog}" read-last'
        " --type phase_transition --session-dir /abs/session"
    )

    parser = _TeachParser(
        prog=prog,
        description="Session journal CLI — append and query JSONL events.",
        formatter_class=fmt,
        epilog=_cli_examples(write_ex, read_ex, last_ex),
    )
    parser._teach_example = write_ex
    sub = parser.add_subparsers(dest="command", parser_class=_TeachParser)
    sub.required = True

    # --- write ---
    write_p = sub.add_parser(
        "write",
        help="Append an event to the journal",
        formatter_class=fmt,
        epilog=_cli_examples(write_ex),
    )
    write_p._teach_example = write_ex
    write_p.add_argument("--type", required=True, dest="event_type",
                         help="Event type string (e.g. phase_transition)")
    write_p.add_argument("--session-dir", required=True,
                         help="Session directory path")
    # --stdin exists so command files can pipe JSON by heredoc, avoiding quote breakage in --data.
    data_group = write_p.add_mutually_exclusive_group()
    data_group.add_argument("--data", default=None,
                            help="JSON object of extra event fields "
                                 "(mutually exclusive with --stdin)")
    data_group.add_argument("--stdin", action="store_true",
                            help="Read the JSON object of extra event "
                                 "fields from standard input "
                                 "(mutually exclusive with --data)")

    # --- read ---
    read_p = sub.add_parser(
        "read",
        help="Read events (JSON array to stdout)",
        formatter_class=fmt,
        epilog=_cli_examples(read_ex),
    )
    read_p._teach_example = read_ex
    read_p.add_argument("--session-dir", required=True,
                        help="Session directory path")
    read_p.add_argument("--type", default=None, dest="event_type",
                        help="Filter by event type")
    read_p.add_argument("--since", default=None,
                        help="Arc-scope lower bound (inclusive): only events "
                             "with ts >= this ISO-8601 UTC timestamp. Parsed, "
                             "not string-compared; fail-open on unparseable.")

    # --- read-last ---
    last_p = sub.add_parser(
        "read-last",
        help="Read the most recent event of a type",
        formatter_class=fmt,
        epilog=_cli_examples(last_ex),
    )
    last_p._teach_example = last_ex
    last_p.add_argument("--session-dir", required=True,
                        help="Session directory path")
    last_p.add_argument("--type", required=True, dest="event_type",
                        help="Event type to find")
    last_p.add_argument("--since", default=None,
                        help="Arc-scope lower bound (inclusive): only consider "
                             "events with ts >= this ISO-8601 UTC timestamp.")
    last_p.add_argument("--exclude-scope", default=None, dest="exclude_scope",
                        help="Skip events whose TOP-LEVEL scope field equals "
                             "this value (e.g. --exclude-scope dispatch keeps "
                             "a variety_assessed read-last on the feature-"
                             "level assessment, skipping per-dispatch mirrors).")
    return parser


def main() -> int:
    """
    CLI entry point for session journal operations.
    """
    args = _build_cli().parse_args()

    if args.command == "write":
        # Neither flag set means "{}".
        if args.stdin:
            data_source = "stdin"
            try:
                raw = sys.stdin.read()
            except OSError as exc:
                print(f"session_journal: failed to read --stdin: {exc}",
                      file=sys.stderr)
                return 1
        else:
            data_source = "--data"
            raw = args.data if args.data is not None else "{}"

        try:
            extra = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"session_journal: invalid {data_source} JSON: {exc}",
                  file=sys.stderr)
            return 1

        if not isinstance(extra, dict):
            print(f"session_journal: {data_source} must be a JSON object",
                  file=sys.stderr)
            return 1

        event = make_event(args.event_type, **extra)

        # Same validation as append_event, but the CLI prints the reason.
        ok, reason = _validate_event_schema(event)
        if not ok:
            print(
                f"session_journal: invalid event schema ({reason})",
                file=sys.stderr,
            )
            return 1

        # Same --session-dir contract for write, read and read-last.
        rc = _validate_cli_session_dir(args.session_dir)
        if rc != 0:
            return rc

        journal = _journal_path_from(args.session_dir)
        if repeats_latest_event(journal, event):
            print(
                f"session_journal: {args.event_type} already recorded "
                "(identical to the latest event with the same key); not written",
                file=sys.stderr,
            )
            return 0
        journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        entry = json.dumps(event, separators=(",", ":")) + "\n"
        if not _atomic_write(journal, entry.encode("utf-8")):
            print("session_journal: write failed", file=sys.stderr)
            return 1
        return 0

    elif args.command == "read":
        rc = _validate_cli_session_dir(args.session_dir)
        if rc != 0:
            return rc
        events = read_events_from(
            args.session_dir, event_type=args.event_type, since=args.since
        )
        print(json.dumps(events))
        return 0

    elif args.command == "read-last":
        rc = _validate_cli_session_dir(args.session_dir)
        if rc != 0:
            return rc
        event = read_last_event_from(
            args.session_dir,
            args.event_type,
            since=args.since,
            exclude_scope=args.exclude_scope,
        )
        if event is None:
            print("null")
        else:
            print(json.dumps(event))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
