#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/task_store_probe.py
Summary: PostToolUse(TaskCreate|TaskUpdate) probe: did the host write the task file it
just claimed to? Two consecutive misses, or a miss on a task the probe saw before, is a
HALT (the task store is not writing): a systemMessage to the model, a `task_store_absent` journal row, and the
`halt` marker that makes dispatch_gate refuse every Agent spawn until a later probe finds
the file again.
Used by: hooks/dispatch_registry.py (native check `task_store_probe:dispatch_check`, right
after task_lifecycle_gate); hooks/dispatch_gate.py reads the marker through
shared.task_store.store_halted.

Why PostToolUse and not SessionStart: a hook cannot create a task, and a fresh session has
no task dir to inspect; the honest detector is the first write the host claims to have
made. The first miss is silent (a lazy host writer is a hypothesis the retry covers); the
second is not. A task file that is there but unreadable or malformed (not a JSON object)
counts as a miss. A frame whose id cannot name a file (`../x`) is not a miss: nothing is
probed and nothing is counted. A TaskUpdate that closes a task (status deleted or completed)
whose file is gone is an expected absence, not a miss: deleting a task removes its file. The
task is kept in the marker's `closed` list, so a later update to it is no miss either; a seen
task whose file vanishes on any other update still halts at once.
"""

from __future__ import annotations

import json
import sys

import shared.pact_context as pact_context
from shared.task_store import (PROBE_MISS_LIMIT, PROBE_SEEN_CAP, HALT_RULE, TaskRef, TaskStore,
                               halt_sentence, read_probe_marker, write_probe_marker)
from shared.user_actions import CLOSED_STATUSES

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})
PROBED_TOOLS = ("TaskCreate", "TaskUpdate")


def created_task_id(tool_response: object, tool_input: dict) -> str:
    """The id the frame names: tool_response.task.id (TaskCreate), else tool_input.taskId.
    Mirrors journal_emitter.created_task_id, a hook this module must not import."""
    task = tool_response.get("task") if isinstance(tool_response, dict) else None
    task_id = str(task.get("id") or "") if isinstance(task, dict) else ""
    return task_id or str(tool_input.get("taskId") or "")


def _keys(marker: dict, name: str) -> list:
    """The marker's list `name` of task keys, [] when absent or malformed."""
    value = marker.get(name)
    return [key for key in value if isinstance(key, str)] if isinstance(value, list) else []


def _carried(marker: dict, closed: list) -> dict:
    """The `closed` list to keep on a rewrite of the marker ({} while it is empty)."""
    return {"closed": closed} if closed else {}


def probe(payload: dict, store: TaskStore, session_dir: str, team: str) -> dict | None:
    """Run one probe. Returns the fields of a HALT (team, task_id, misses) or None."""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    task_id = created_task_id(payload.get("tool_response"), tool_input)
    ref = TaskRef(team, task_id)
    if not task_id or store.path(ref) is None:
        return None
    marker = read_probe_marker(session_dir)
    misses = marker.get("misses") if isinstance(marker.get("misses"), int) else 0
    seen, closed = _keys(marker, "seen"), _keys(marker, "closed")
    key = f"{team}/{task_id}"
    # A file that is there but cannot be read as a task object is a miss too: no reader can use it.
    if store.get(ref) is not None:
        if key not in seen:
            seen = (seen + [key])[-PROBE_SEEN_CAP:]
        write_probe_marker(session_dir, {**_carried(marker, closed), "misses": 0, "halt": False,
                                         "seen": seen})
        return None
    if payload.get("tool_name") == "TaskUpdate" and (
            key in closed or tool_input.get("status") in CLOSED_STATUSES):
        # Deleted or closed: its file may go with it. The counts stand as they were.
        if key not in closed:
            closed = (closed + [key])[-PROBE_SEEN_CAP:]
        write_probe_marker(session_dir, {**marker, "seen": [k for k in seen if k != key],
                                         "closed": closed})
        return None
    misses += 1
    halt = misses >= PROBE_MISS_LIMIT or (payload.get("tool_name") == "TaskUpdate" and key in seen)
    write_probe_marker(session_dir, {**_carried(marker, closed), "misses": misses, "halt": halt,
                                     "seen": seen, "team": team, "task_id": task_id})
    return {"team": team, "task_id": task_id, "misses": misses} if halt else None


def dispatch_check(payload: dict, ctx=None):
    """The dispatcher entry: a Result with the HALT systemMessage, or None."""
    import dispatch_registry as registry

    try:
        if payload.get("tool_name") not in PROBED_TOOLS:
            return None
        pact_context.init(payload)
        team, session_dir = pact_context.get_team_name(), pact_context.get_session_dir()
        if not team or not session_dir:
            return None
        fields = probe(payload, TaskStore(), session_dir, team)
    except Exception as error:  # noqa: BLE001 - a probe that cannot run decided nothing
        print(f"task_store_probe: internal error, nothing probed: {type(error).__name__}",
              file=sys.stderr)
        return None
    if fields is None:
        return None
    if ctx is not None:
        ctx.journal("task_store_absent", **fields)
    else:
        from shared.session_journal import append_event, make_event
        append_event(make_event("task_store_absent", **fields))
    return registry.Result(system_message=halt_sentence(fields["team"], fields["task_id"]),
                           rule=HALT_RULE, journaled=True)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = dispatch_check(payload) if isinstance(payload, dict) else None
    except Exception as error:  # noqa: BLE001
        print(f"task_store_probe: internal error, nothing probed: {type(error).__name__}",
              file=sys.stderr)
        result = None
    if result is None:
        print(_SUPPRESS_OUTPUT)
    else:
        print(json.dumps({"systemMessage": result.system_message}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
