#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/journal_emitter.py (repacted EVENTS, m8a)
Summary: PostToolUse(TaskCreate|TaskUpdate|Agent) hook that writes four session-journal
         events the command prose used to write by hand in fenced bash blocks, with the
         same fields those blocks write:
           variety_assessed  on a TaskCreate/TaskUpdate carrying metadata.variety,
                             and on the dispatch-wiring TaskUpdate (owner + addBlockedBy,
                             the authoritative Task B signal); `scope: "dispatch"` marks a
                             Task B, no scope marks a feature-level stamp;
           agent_dispatch    on an Agent spawn: the spawned name, its primary work task,
                             and the current in_progress phase;
           phase_transition  on a status change of a PREPARE/ARCHITECT/CODE/TEST phase
                             task: started, completed, or skipped (with its reason);
           metadata_write    on a TaskUpdate writing metadata.teachback_submit or
                             metadata.handoff: task_id, key, agent (the task's owner)
                             and bytes (the value's UTF-8 JSON size), never the text.
         and, on an Agent spawn of a pact specialist, the SPAWN LEG of dispatch_site
         (shared/dispatch_site.py): one site per open non-TEACHBACK task the spawned
         name owns, deduped against the owner-write leg in task_lifecycle_gate; a
         specialist spawn owning no such task records journal_emit_skipped
         {skipped_type: dispatch_site, cause: no_owned_work_task}.
Used by: hooks.json PostToolUse entry (matcher "TaskCreate|TaskUpdate|Agent") and the
         UserPromptSubmit entry. Writes through shared/session_journal.append_event, whose
         repeat suppression (DEDUPED_EVENT_KEYS) makes this hook and a command block that
         still runs write each event once, whichever comes second.
         On UserPromptSubmit (refactor core lane, moved from missed_wake_scan): the one
         prompt_kind row per lead prompt and the straggler line for an idle notice older
         than its sender's latest task change (shared/prompt_kind.py), as additionalContext.

Never guesses. A field that cannot be derived from the payload plus a task read (no
in_progress phase task for agent_dispatch, an ambiguous work task, no created task id)
means the event is NOT written. comPACT's agent_dispatch and its CODE phase_transition
are therefore not emitted here (comPACT has no phase tasks); their blocks stay.

Frame: writes only from a frame whose journal is the canonical one
(pact_context.is_canonical_journal_frame), the same gate the dispatch_variety emit uses.
Fail-open: any error writes nothing and exits 0.

Input: JSON on stdin (tool_name, tool_input, tool_response, session_id, agent_type).
Output: {"suppressOutput": true}; exit 0.
"""

from __future__ import annotations

import json
import re
import sys

# Modules, not names: a test that patches shared.session_journal.append_event (or a
# task_utils reader) must reach this hook whenever it was first imported.
from shared import (constants, dispatch_helpers, dispatch_site, intentional_wait, pact_context,
                    prompt_kind, session_journal, task_utils, teachback_schema)

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})
PHASE_NAMES = ("PREPARE", "ARCHITECT", "CODE", "TEST")
_SUBJECT_PREFIX = re.compile(r"^([a-z0-9-]+): ")
_OPEN_STATUSES = ("pending", "in_progress")
WRITE_SIZE_KEYS = ("teachback_submit", "handoff")


# ---------------------------------------------------------------------------
# Pure derivations (unit-tested)
# ---------------------------------------------------------------------------

def project_variety(variety: object) -> dict:
    """The scores the journal mirrors: the 4 dimensions and the total, rationale strings
    dropped (as the prose blocks and the dispatch_variety emit do)."""
    if not isinstance(variety, dict):
        return {}
    return {key: variety[key] for key in teachback_schema.DISPATCH_VARIETY_KEYS if key in variety}


def subject_prefix(subject: object) -> str | None:
    """`name` for a subject of the form `name: ...`, else None."""
    match = _SUBJECT_PREFIX.match(subject) if isinstance(subject, str) else None
    return match.group(1) if match else None


def is_dispatch_task(task: dict, team_tasks: list[dict]) -> bool:
    """A Task B: a `name: ...` work task whose teachback gate is visible, either wired
    (a blockedBy id naming a TEACHBACK task) or, at TaskCreate time before the wiring
    exists, an OPEN `name: TEACHBACK ...` sibling (the dispatch prose creates Task A
    first). Anything else stamped with variety is feature-level."""
    subject = task.get("subject")
    prefix = subject_prefix(subject)
    if prefix is None or task_utils.is_teachback_subject(subject):
        return False
    by_id = {str(t.get("id")): t for t in team_tasks if isinstance(t, dict)}
    for blocker in task.get("blockedBy") or []:
        if task_utils.is_teachback_subject((by_id.get(str(blocker)) or {}).get("subject")):
            return True
    return any(
        task_utils.is_teachback_subject(t.get("subject"))
        and subject_prefix(t.get("subject")) == prefix
        and t.get("status") in _OPEN_STATUSES
        and str(t.get("id")) != str(task.get("id"))
        for t in by_id.values()
    )


def is_dispatch_wiring(tool_input: dict, team: str) -> bool:
    """The AUTHORITATIVE Task B signal: the terminal wiring write, `owner` resolving to a
    pact specialist AND `addBlockedBy` in the same tool_input. This is the one definition
    of "a dispatch wiring" that handoff_ordering_gate uses, imported from
    shared.dispatch_helpers, not re-implemented. is_dispatch_task is only the early guess
    made at TaskCreate, before the wiring exists."""
    return dispatch_helpers.is_owner_wiring_shape(tool_input) and \
        dispatch_helpers.is_pact_specialist_owner(tool_input.get("owner"), team)


def primary_work_task(name: str, team_tasks: list[dict]) -> dict | None:
    """The spawned name's Task B: the one open non-TEACHBACK task it owns. None when
    there is none or more than one (ambiguous: never guess)."""
    candidates = [
        t for t in team_tasks
        if isinstance(t, dict) and t.get("owner") == name
        and t.get("status") in _OPEN_STATUSES
        and not task_utils.is_teachback_subject(t.get("subject"))
    ]
    return candidates[0] if len(candidates) == 1 else None


def phase_of(task: dict | None) -> str | None:
    """PREPARE/ARCHITECT/CODE/TEST for a phase task (`PHASE: slug`), else None."""
    subject = (task or {}).get("subject")
    if not isinstance(subject, str):
        return None
    head = subject.split(":", 1)[0]
    return head if head in PHASE_NAMES and subject.startswith(head + ":") else None


def phase_transition_fields(task: dict, status: object, metadata: dict) -> dict | None:
    """The phase_transition payload for a phase task's status write, or None."""
    phase = phase_of(task)
    if phase is None:
        return None
    if status == "in_progress":
        return {"phase": phase, "status": "started", "skip_reason": "", "metadata": {}}
    if status == "completed":
        if metadata.get("skipped") is True:
            reason = metadata.get("skip_reason")
            return {"phase": phase, "status": "skipped",
                    "skip_reason": reason if isinstance(reason, str) else "", "metadata": {}}
        return {"phase": phase, "status": "completed", "skip_reason": "", "metadata": {}}
    return None


def created_task_id(tool_response: object, tool_input: dict) -> str:
    """The id a TaskCreate just made (tool_response.task.id, else an echoed taskId).
    Mirrors task_lifecycle_gate._created_task_id, which lives in a hook this package
    does not own and must not import."""
    task = tool_response.get("task") if isinstance(tool_response, dict) else None
    task_id = str(task.get("id") or "") if isinstance(task, dict) else ""
    return task_id or str(tool_input.get("taskId") or "")


# ---------------------------------------------------------------------------
# Event builders: each returns the events one payload should write
# ---------------------------------------------------------------------------

def variety_events(tool_name: str, tool_input: dict, tool_response: object,
                   team: str) -> list[dict]:
    metadata = tool_input.get("metadata") if isinstance(tool_input.get("metadata"), dict) else {}
    stamped = isinstance(metadata.get("variety"), dict) and bool(metadata["variety"])
    wiring = tool_name == "TaskUpdate" and is_dispatch_wiring(tool_input, team)
    if not (stamped or wiring):
        return []
    task_id = (created_task_id(tool_response, tool_input) if tool_name == "TaskCreate"
               else str(tool_input.get("taskId") or ""))
    if not task_id:
        return []
    task = task_utils.read_task_json(task_id, team)
    source = metadata["variety"] if stamped else (task.get("metadata") or {}).get("variety")
    variety = project_variety(source)
    if not variety or not task:
        return []
    if task_utils.is_teachback_subject(task.get("subject")):
        return []                     # a teachback gate is never stamped as a dispatch
    # The wiring is authoritative; the subject-prefix check is the early guess. A Task B
    # stamped before its Task A therefore leaves a FEATURE row, then the wiring writes a
    # DISPATCH row; readers resolve per task_id by the LATEST row
    # (session_journal.latest_variety_by_task).
    dispatch = wiring or is_dispatch_task(task, list(task_utils.iter_team_task_jsons(team)))
    fields = {"task_id": task_id, "variety": variety}
    if dispatch:
        fields["scope"] = constants.VARIETY_ASSESSED_DISPATCH_SCOPE
    return [session_journal.make_event("variety_assessed", **fields)]


def dispatch_events(tool_input: dict, team: str) -> list[dict]:
    name = tool_input.get("name")
    if not isinstance(name, str) or not name.strip():
        return []
    team_tasks = list(task_utils.iter_team_task_jsons(team))
    work = primary_work_task(name, team_tasks)
    phase = phase_of(task_utils.find_current_phase(team_tasks))
    if work is None or phase is None:
        return []                     # not deterministically derivable: never guess
    return [session_journal.make_event("agent_dispatch", agent=name, task_id=str(work.get("id")), phase=phase)]


def spawned_specialist_type(tool_input: dict, team: str) -> str | None:
    """The spawned teammate's pact-specialist agent type, or None when the spawn is not
    a named pact specialist. The spawn payload's subagent_type is read first: it is
    known before the team config records the member. The config is the fallback for a
    spawn that names no type."""
    name = tool_input.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    raw = tool_input.get("subagent_type")
    if isinstance(raw, str) and raw:
        agent_type = pact_context.strip_pact_namespace(raw)
        return agent_type if dispatch_helpers.is_registered_pact_specialist(agent_type) else None
    for member in pact_context._iter_members(team):
        if member.get("name") == name:
            agent_type = pact_context.member_agent_type(member)
            if agent_type and dispatch_helpers.is_registered_pact_specialist(agent_type):
                return agent_type
    return None


def spawn_site_tasks(tool_input: dict, team: str, team_tasks: list[dict]) -> list[dict] | None:
    """The tasks the spawn leg writes a dispatch_site for: every OPEN non-TEACHBACK
    task owned by the spawned name. None when the spawn is outside the population (not
    a pact specialist, or a teachback-exempt type such as the secretary); an empty list
    is a specialist dispatch with no site to carry it, which the caller records as a
    skip."""
    agent_type = spawned_specialist_type(tool_input, team)
    if agent_type is None or agent_type in intentional_wait.TEACHBACK_EXEMPT_AGENT_TYPES:
        return None
    name = tool_input.get("name")
    return [
        t for t in team_tasks
        if isinstance(t, dict) and t.get("owner") == name and t.get("id") is not None
        and t.get("status") in _OPEN_STATUSES
        and not task_utils.is_teachback_subject(t.get("subject"))
    ]


def emit_spawn_dispatch_sites(tool_input: dict, team: str) -> None:
    """Write the spawn leg of dispatch_site. Each write claims the shared marker, so a
    site the owner-write leg already wrote is not written twice."""
    tasks = spawn_site_tasks(tool_input, team, list(task_utils.iter_team_task_jsons(team)))
    if tasks is None:
        return
    for task in tasks:
        variety = project_variety(dispatch_helpers.variety_stamp_as_of_write({}, task))
        dispatch_site.emit(team, str(task.get("id")), variety)
    if not tasks:
        session_journal.append_event(session_journal.make_event(
            "journal_emit_skipped", skipped_type="dispatch_site",
            cause=dispatch_site.SKIP_CAUSE_NO_OWNED_WORK_TASK,
            agent=str(tool_input.get("name")).strip(),
        ))


def phase_events(tool_input: dict, team: str) -> list[dict]:
    status = tool_input.get("status")
    task_id = str(tool_input.get("taskId") or "")
    if status not in ("in_progress", "completed") or not task_id:
        return []
    task = task_utils.read_task_json(task_id, team)
    if not task:
        return []
    metadata = dict(task.get("metadata") or {})
    if isinstance(tool_input.get("metadata"), dict):
        metadata.update(tool_input["metadata"])
    fields = phase_transition_fields(task, status, metadata)
    return [session_journal.make_event("phase_transition", **fields)] if fields else []


def write_size_events(tool_input: dict, team: str) -> list[dict]:
    """One metadata_write per teachback_submit or handoff the TaskUpdate writes: the
    task, the key, the writer (the task's owner) and the UTF-8 bytes of the value as JSON.
    Counts only, never the text."""
    metadata = tool_input.get("metadata") if isinstance(tool_input.get("metadata"), dict) else {}
    task_id = str(tool_input.get("taskId") or "")
    keys = [key for key in WRITE_SIZE_KEYS if metadata.get(key) is not None]
    if not task_id or not keys:
        return []
    owner = task_utils.read_task_json(task_id, team).get("owner")
    agent = owner if isinstance(owner, str) else ""
    return [session_journal.make_event(
        "metadata_write", task_id=task_id, key=key, agent=agent,
        bytes=len(json.dumps(metadata[key], ensure_ascii=False).encode("utf-8", "replace")))
        for key in keys]


def events_for(input_data: dict, team: str) -> list[dict]:
    tool_name = input_data.get("tool_name")
    tool_input = input_data.get("tool_input") if isinstance(input_data.get("tool_input"), dict) else {}
    if tool_name == "Agent":
        return dispatch_events(tool_input, team)
    if tool_name not in ("TaskCreate", "TaskUpdate"):
        return []
    events = variety_events(tool_name, tool_input, input_data.get("tool_response"), team)
    if tool_name == "TaskUpdate":
        events += phase_events(tool_input, team)
        events += write_size_events(tool_input, team)
    return events


def prompt_output(input_data: dict) -> str:
    """UserPromptSubmit: the prompt_kind row, then the straggler line as additionalContext
    or suppressOutput. Both gate on the lead frame themselves and never raise."""
    straggler = prompt_kind.build_straggler_line(input_data)
    prompt_kind.journal_prompt_kind(input_data, straggler=straggler is not None)
    if not straggler:
        return _SUPPRESS_OUTPUT
    return json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                              "additionalContext": straggler}})


def main() -> int:
    output = _SUPPRESS_OUTPUT
    try:
        input_data = json.loads(sys.stdin.read() or "{}")
        if isinstance(input_data, dict):
            from shared.frame import resolve as resolve_frame
            # init stays: the journal writes below read this module's context, and a
            # memoised frame from an earlier check does not re-run it here.
            pact_context.init(input_data)
            if input_data.get("hook_event_name") == "UserPromptSubmit":
                print(prompt_output(input_data))
                return 0
            team = resolve_frame(input_data).context_team
            if team and pact_context.is_canonical_journal_frame(input_data):
                for event in events_for(input_data, team):
                    session_journal.append_event(event)
                if input_data.get("tool_name") == "Agent" and isinstance(input_data.get("tool_input"), dict):
                    emit_spawn_dispatch_sites(input_data["tool_input"], team)
    except Exception:   # noqa: BLE001 - fail-open: a journal mirror never breaks a tool call
        pass
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
