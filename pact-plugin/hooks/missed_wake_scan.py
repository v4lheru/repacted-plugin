#!/usr/bin/env python3
"""UserPromptSubmit + SessionStart hook: lead-side surfacer of stalled teammates.

Lead frames only (is_lead). One model, `StaleWait`, is built once per run from the team's
task files: every in_progress task holding a well-formed intentional_wait, with its reason,
resolver, scope anchor and the two staleness readings (by `since`, by the anchor). Three
surfacers read it: a teammate idling on awaiting_lead_completion past the threshold (the
missed-wake alarm, with a once-per-(task, since) missed_wake row); candidate mutual peer
waits and unanchored waits discharging a record, which are two predicates over the same rows
rather than two scans; and a teammate idle on a finished unattended run (shared/finished_runs).
A fourth alarm, stale unflagged background launches, reads the background registry, not the
waits. The prompt_kind row and the straggler line live in journal_emitter (shared/prompt_kind).
No markers: live task state and journal reads are the dedup.

# livelock-safe: additionalContext only on a surface path; suppressOutput otherwise; exit 0
# on every path; the only write is the journal append.
Rationale: the design notes
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Add hooks directory to path for shared package imports (mirrors teammate_idle.py).
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

import shared.pact_context as pact_context
from shared.background_work import wait_scope_anchor
from shared.constants import COMPACTION_TEAMMATE_CLAUSE
from shared.finished_runs import find_idle_on_finished_runs, read_run_ledger, run_summary
from shared.intentional_wait import validate_wait, wait_stale
from shared.pact_context import is_lead
from shared.session_journal import append_event, get_journal_path, make_event, read_events
from shared.session_state import _sanitize_member_name
from shared.task_utils import get_task_list

# Suppress false "hook error" display in Claude Code UI on bare exit paths.
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# The canonical missed-wake gap: a teammate idling for the lead's completion + wake. Matches
# this exact reason, not every lead-resolved wait.
_MISSED_WAKE_REASON = "awaiting_lead_completion"

# additionalContext events whose hookEventName the platform expects echoed back.
# The hook is registered ONLY on these two; the firing event is read from stdin.
_SURFACE_EVENTS = ("UserPromptSubmit", "SessionStart")

_UNFLAGGED_EVENT = "unflagged_background_wait"
_FINISHED_RUN_EVENT = "idle_on_finished_run"


# --- the model ------------------------------------------------------------------------

@dataclass(frozen=True)
class StaleWait:
    """One in_progress task's well-formed wait, read once. `stale` is wait_stale over the
    wait's own `since`; `anchor_stale` the same threshold over its scope anchor
    (covers_since, else since), which a re-stamped `since` cannot refresh."""
    task: dict
    since: str
    reason: str
    resolver: str
    anchor: "datetime | None"
    stale: bool
    anchor_stale: bool


def stale_waits(tasks, now: "datetime | None" = None) -> "list[StaleWait]":
    """The StaleWait rows of a task list (a list already of rows is returned as is). Skips a
    malformed task instead of raising: one raise would drop every surface."""
    if tasks and all(isinstance(item, StaleWait) for item in tasks):
        return list(tasks)
    rows = []
    for task in tasks or []:
        if not isinstance(task, dict) or task.get("status") != "in_progress":
            continue
        metadata = task.get("metadata")
        wait = metadata.get("intentional_wait") if isinstance(metadata, dict) else None
        if not validate_wait(wait):
            continue
        try:
            anchor, _ = wait_scope_anchor(wait)
        except Exception:
            anchor = None
        rows.append(StaleWait(
            task=task, since=str(wait.get("since") or ""), reason=str(wait.get("reason") or ""),
            resolver=str(wait.get("expected_resolver") or ""), anchor=anchor,
            stale=bool(wait_stale(wait, _now=now)),
            anchor_stale=anchor is not None
            and bool(wait_stale({**wait, "since": anchor.isoformat()}, _now=now))))
    return rows


def _fields(task: dict) -> "tuple[str, str, str, str]":
    """(task_id, owner, since, subject) of a task; strings, empty where absent."""
    wait = (task.get("metadata") or {}).get("intentional_wait") or {}
    return (str(task.get("id") or ""), task.get("owner") or "", wait.get("since") or "",
            task.get("subject") or "")


def _label(task: dict) -> str:
    """`#id (owner: subject)` with every teammate-authored field sanitised (F31: newline, NEL,
    U+2028/9 and control characters could forge lines in the lead's context)."""
    task_id, owner, _since, subject = _fields(task)
    label = f"#{_sanitize_member_name(task_id) or '?'} ({_sanitize_member_name(owner) or 'unknown'}"
    subject = _sanitize_member_name(subject)
    return label + (f": {subject})" if subject else ")")


def _age_minutes(since: str, now: datetime) -> "int | None":
    """Whole minutes since `since` (tz-aware ISO-8601), or None if unparseable."""
    try:
        parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return None if parsed.tzinfo is None else max(0, int((now - parsed).total_seconds() // 60))
    except (ValueError, TypeError):
        return None


def _stamp(now: "datetime | None") -> dict:
    return {"ts": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")} if now else {}


def _emitted(event_type: str, key_a: str, key_b: str) -> set:
    """(a, b) pairs already recorded under `event_type` in this session's journal."""
    return {(ev.get(key_a), ev.get(key_b)) for ev in read_events(event_type)
            if isinstance(ev, dict) and ev.get(key_a) and ev.get(key_b)}


# --- missed wake: a teammate idling on the lead -----------------------------------------

def find_stale_missed_wakes(tasks: list, now: "datetime | None" = None) -> list:
    """Tasks idling on awaiting_lead_completion past wait_stale's threshold. Pure."""
    return [row.task for row in stale_waits(tasks, now)
            if row.reason == _MISSED_WAKE_REASON and row.stale]


def emit_forensic(stale: list, now: "datetime | None" = None) -> None:
    """A missed_wake row for each stale wait not yet recorded; no-op when the journal does not
    resolve (only the lead's process resolves it). Never raises."""
    try:
        if not stale or not get_journal_path():
            return
        emitted = _emitted("missed_wake", "task_id", "since")
        for task in stale:
            task_id, owner, since, subject = _fields(task)
            safe_owner = _sanitize_member_name(owner)   # an owner sanitised to "" fails the schema
            if not task_id or not since or not safe_owner or (task_id, since) in emitted:
                continue
            fields = {"task_id": task_id, "agent": safe_owner, "since": since}
            if _sanitize_member_name(subject):
                fields["task_subject"] = _sanitize_member_name(subject)
            fields["reason"] = _MISSED_WAKE_REASON
            append_event(make_event("missed_wake", **fields, **_stamp(now)))
            emitted.add((task_id, since))
    except Exception:
        pass   # best-effort forensic record; never break the surface path or exit-0


def build_surface(stale: list, now: "datetime | None" = None,
                  shown: "set | frozenset" = frozenset()) -> "str | None":
    """Lead-facing text for still-stale missed wakes, or None. One line per task plus the
    action; when every stale wait was already alarmed on (`shown`), one short line (audit 22:
    the full alarm is not re-injected on every prompt during a legitimate hold)."""
    if not stale:
        return None
    now = now or datetime.now(timezone.utc)

    def age_of(task):
        age = _age_minutes(_fields(task)[2], now)
        return f"~{age}min" if age is not None else "stale"

    if shown and all((_fields(task)[0], _fields(task)[2]) in shown for task in stale):
        short = [f"{_label(task).split(':')[0].rstrip(')')}, {age_of(task)})" for task in stale]
        return ("PACT missed-wake alarm, still open (the full alarm with its three causes "
                "was shown earlier this session): " + "; ".join(short))
    lines = [f"- Task {_label(task)} — idle {age_of(task)} on awaiting_lead_completion" for task in stale]
    return (
        "PACT missed-wake alarm: the teammate(s) below are idling on awaiting_lead_completion "
        "past the staleness threshold. KNOWN: the wait is well-formed and stale and an idle "
        "teammate cannot self-wake, so it will not resolve itself. THE CAUSE IS NOT KNOWN; "
        "several produce this, needing three responses. (1) SEND THE WAKE — the "
        "directive is on disk and the wake did not arrive: send the one-line wake (id and digest). "
        "(2) NOTHING, THE WAIT IS LEGITIMATE — a deliberate hold, not reached yet, or a "
        "re-stamped teammate still genuinely waiting; no hook can see a hold. (3) THE TEAMMATE "
        "MUST CLEAR ITS OWN FLAG — it was woken and did not. ACTION: check which, then send a "
        "wake-SendMessage to each that needs one (or re-set / complete the task) — re-shows as "
        "one line every turn until it resolves.\n" + "\n".join(lines))


# --- peer and unanchored waits: two predicates over the same rows -----------------------

def find_mutual_waits(tasks: list, now: "datetime | None" = None) -> list:
    """Distinct owners each idling on `peer`, aged past the threshold from their scope anchor.
    A candidate, not a proven cycle (expected_resolver names a kind, not an agent); blind to
    user/external resolvers. Age comes from the anchor because a re-stamped `since` stays fresh."""
    waiting = [row.task for row in stale_waits(tasks, now) if row.resolver == "peer" and row.anchor_stale]
    owners = {t["owner"] for t in waiting if isinstance(t.get("owner"), str) and t["owner"]}
    return waiting if len(owners) >= 2 else []


def build_mutual_surface(mutual: list) -> "str | None":
    """Lead-facing text for a candidate mutual wait. Names it as a candidate."""
    if not mutual:
        return None
    return (
        "POSSIBLE MUTUAL WAIT — every one of these is idling on a PEER and none has moved past "
        "the staleness threshold:\n" + "\n".join(f"- Task {_label(task)}" for task in mutual)
        + "\nNobody here is waiting on you, so this will not resolve itself and no teammate can "
        "see it — each one can only see its own wait. This is a CANDIDATE, not a proven cycle: "
        "the flag records that a peer is expected, never which peer, so two agents waiting on a "
        "third look identical to two waiting on each other. Ask each what it is waiting for. "
        "Waits on `user` or `external` CANNOT appear here at all, so this finding never rules a "
        "deadlock out.")


def find_unanchored_waits(tasks: list, team_name: "str | None" = None,
                          now: "datetime | None" = None) -> list:
    """(task, anchor class) for each wait with no covers_since that covers a recorded launch
    through the `since` fallback. Gated on a covered record, NOT on staleness: the harmless
    never-re-stamped waits would show and the drifted re-stamped ones hide. Never raises."""
    try:
        from shared.background_work import (load_records_for_discharge, record_task_ids,
                                            wait_anchor_class, wait_covers_record)
        records = load_records_for_discharge(team_name, now=now) if team_name else []
    except Exception:
        return []
    out = []
    for row in stale_waits(tasks, now) if records else []:
        try:
            anchor_class = wait_anchor_class(row.task)
            task_id = str(row.task.get("id"))
            if anchor_class and any(task_id in record_task_ids(r) and wait_covers_record(row.task, r)
                                    for r in records):
                out.append((row.task, anchor_class))
        except Exception:
            continue
    return out


def build_unanchored_surface(unanchored: list) -> "str | None":
    """Lead-facing text naming each wait with no usable scoping anchor."""
    lines = [f"- Task {_label(task).split(':')[0].rstrip(')')}) — anchor {anchor_class}"
             for task, anchor_class in unanchored if isinstance(task, dict)]
    if not lines:
        return None
    return (
        "BACKGROUND WORK DISCHARGED ON A FALLBACK ANCHOR — each of these waits is valid and is "
        "currently acquitting a recorded launch WITHOUT a `covers_since` to say which launches "
        "it covers, so the scope is taken from `since`, which agents re-stamp:\n" + "\n".join(lines)
        + "\nNothing is stalled. What is uncertain is whether the wait was really raised BEFORE "
        "the launch it is acquitting: if it was re-stamped, `since` has moved forward and may now "
        "cover work the teammate never acknowledged. Ask the teammate what its wait was raised "
        "for. `absent` means the wait predates the field, was written under the earlier "
        "instruction or from a template that omits it, or an agent dropped it on a re-SET by "
        "rewriting the wait without `covers_since`; the scan cannot tell which. `malformed` means "
        "an agent wrote an unparseable value and has a bug worth naming.")


# --- unflagged background launches (the registry, not the waits) ------------------------

def find_stale_unflagged_background(
    team_name: str, tasks: "list | None" = None, now: "datetime | None" = None,
    lead_transcript: "str | None" = None,
) -> list:
    """Launch records past their own staleness window. Reads only through outstanding_unflagged
    (task-status and flagged-wait gates). Leaves out a separate-process (tmux) teammate, woken
    by its own completion, and one whose own transcript shows work in the last 10 minutes."""
    try:
        from shared.background_work import (lead_stale, outstanding_unflagged,
                                            teammate_is_separate_process, teammate_working)
        if tasks is None:
            tasks = get_task_list()
        return [r for r in outstanding_unflagged(tasks, team_name, now=now)
                if lead_stale(r, now=now)
                and not teammate_is_separate_process(team_name, r.get("agent_name"))
                and not teammate_working(lead_transcript, r.get("agent_name"), now=now)]
    except Exception:
        return []


def emit_unflagged_forensic(stale: list, now: "datetime | None" = None) -> None:
    """Once-per-(agent, registered_at) forensic event. Best-effort, never raises."""
    try:
        if not stale or not get_journal_path():
            return
        emitted = _emitted(_UNFLAGGED_EVENT, "agent", "registered_at")
        for record in stale:
            agent, registered, task_ids = (record.get("agent_name"), record.get("registered_at"),
                                           record.get("task_ids"))
            if not agent or not registered or not isinstance(task_ids, list) or (agent, registered) in emitted:
                continue
            payload = {"agent": _sanitize_member_name(str(agent)), "registered_at": str(registered),
                       "task_ids": [s for s in (_sanitize_member_name(str(t)) for t in task_ids) if s]}
            command = record.get("command")
            if isinstance(command, str) and command:
                payload["command"] = _sanitize_member_name(command)
            append_event(make_event(_UNFLAGGED_EVENT, **payload, **_stamp(now)))
            emitted.add((agent, registered))
    except Exception:
        return


def build_unflagged_surface(stale: list) -> "str | None":
    """Lead-facing text naming teammates with an unflagged recorded launch; one entry per
    teammate with every task id. Not a census (only harness-flagged or trailing-& launches are
    recorded), and the text says so."""
    by_agent: dict = {}
    for record in stale or []:
        agent = _sanitize_member_name(str(record.get("agent_name") or ""))
        if not agent:
            continue
        raw_ids = record.get("task_ids")
        seen = by_agent.setdefault(agent, [])
        for task_id in (_sanitize_member_name(str(t)) for t in (raw_ids if isinstance(raw_ids, list) else [])):
            if task_id and task_id not in seen:
                seen.append(task_id)
    lines = [f"{agent} (task(s) {', '.join(ids)})" if ids else agent for agent, ids in by_agent.items()]
    if not lines:
        return None
    return (
        "UNFLAGGED BACKGROUND WORK — these teammates launched background work and have no "
        "flagged wait: " + "; ".join(lines)
        + ". The job may already have finished. SendMessage each one to collect its result or SET "
        "metadata.intentional_wait. This is NOT a missed wake — nobody is waiting on you; they "
        "failed to flag their own wait. These teammates are not woken by their own job finishing. "
        "This list covers recorded shell launches only: a teammate waiting on a monitor, a "
        "subagent, an MCP task, a workflow or a scheduled wakeup will not appear, and neither will "
        "a shell launch backgrounded in a shape the recorder does not match, so do not read a "
        "short list as an all-clear.")


# --- a teammate idle on a finished unattended run ---------------------------------------

def surface_idle_on_finished_runs(found: list) -> "str | None":
    """One line per (task, run) not yet shown this session, or None. The journal's
    idle_on_finished_run rows are the dedup state, and a line is shown only when its row was
    written, so an unresolvable journal shows nothing rather than the same line every prompt."""
    try:
        if not found or not get_journal_path():
            return None
        shown = _emitted(_FINISHED_RUN_EVENT, "task_id", "log_name")
        lines = []
        for task, row in found:
            task_id, owner = str(task.get("id") or ""), task.get("owner") or ""
            log_name = os.path.basename(str(row.get("log_path")))
            safe_owner = _sanitize_member_name(owner)
            if not task_id or not safe_owner or (task_id, log_name) in shown:
                continue
            if not append_event(make_event(_FINISHED_RUN_EVENT, task_id=task_id, agent=safe_owner,
                                           log_name=log_name)):
                continue
            shown.add((task_id, log_name))
            lines.append(f"{safe_owner} (#{_sanitize_member_name(task_id) or '?'}) is idle on a finished "
                         f"run: {_sanitize_member_name(log_name)}, {run_summary(row)}; nudge it")
        return "\n".join(lines) or None
    except Exception:
        return None


# --- the run ----------------------------------------------------------------------------

def run_surface(input_data: dict, now: "datetime | None" = None) -> "str | None":
    """Run every lead-side alarm and return the additionalContext, or None. Lead frames only.
    `now` is the one clock for every alarm (tests pass a fixed time). The alarms are
    independent: none returns early on another's absence."""
    if not is_lead(input_data):
        return None
    now = now if now is not None else datetime.now(timezone.utc)
    parts = []
    tasks = get_task_list()
    waits = stale_waits(tasks, now=now)
    if tasks:
        stale = find_stale_missed_wakes(waits, now=now)
        if stale:
            # Read before emit_forensic records this prompt's waits: those already in the
            # journal were alarmed on in full at an earlier prompt.
            try:
                shown = _emitted("missed_wake", "task_id", "since") if get_journal_path() else set()
            except Exception:
                shown = set()
            emit_forensic(stale, now=now)
            parts.append(build_surface(stale, now=now, shown=shown))
        parts.append(build_mutual_surface(find_mutual_waits(waits, now=now)))
        if waits:   # the ledger is read only when an open task holds a valid wait
            try:
                finished = find_idle_on_finished_runs(tasks, read_run_ledger(), now=now)
            except Exception:
                finished = []
            parts.append(surface_idle_on_finished_runs(finished))
    try:
        team_name = pact_context.get_team_name()
        if team_name:
            unflagged = find_stale_unflagged_background(
                team_name, tasks, now=now, lead_transcript=input_data.get("transcript_path"))
            if unflagged:
                emit_unflagged_forensic(unflagged, now=now)
                parts.append(build_unflagged_surface(unflagged))
            parts.append(build_unanchored_surface(find_unanchored_waits(waits, team_name, now=now)))
    except Exception:
        pass
    parts = [part for part in parts if part]
    if not parts:
        return None
    surface = "\n\n".join(parts)
    # An in-process teammate's compaction arrives lead-shaped, and nothing here can tell it
    # from the lead's, so the scan runs for both; the clause lets a teammate set it aside.
    if input_data.get("hook_event_name") == "SessionStart" and input_data.get("source") == "compact":
        surface = f"{COMPACTION_TEAMMATE_CLAUSE}\n\n{surface}"
    return surface


def main() -> None:
    # Broad catch-all keeps exit 0 on every path (livelock safety beats observability here).
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            input_data = None
        if not isinstance(input_data, dict):   # a non-dict payload would crash is_lead
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        pact_context.init(input_data)
        surface = run_surface(input_data)
        if surface:
            # Echo the firing event's name back per the additionalContext contract; the hook
            # is registered only on the two surface events.
            event = input_data.get("hook_event_name")
            if not isinstance(event, str) or event not in _SURFACE_EVENTS:
                event = "UserPromptSubmit"
            print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": surface}}))
        else:
            print(_SUPPRESS_OUTPUT)
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)


if __name__ == "__main__":
    main()
