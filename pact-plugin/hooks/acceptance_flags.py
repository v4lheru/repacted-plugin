#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/acceptance_flags.py
Summary: PreToolUse(TaskUpdate) hook for the acceptance moment. When an update sets
         status "completed" (the lead accepting a HANDOFF or a teachback), it surfaces the
         write-time checks' lines for the task to the accepting lead as additionalContext:
         one line per check outcome (items returned once, repeats passed, and the notes: the
         run-evidence items NO-RUN, MISMATCH and EXIT, the advisories, the undisposed
         uncertainty items and amendments; hooks/write_checks.py, hooks/amendment_gate.py),
         then "These lines come from the write-time checks, which ask no model."
         (ev.acceptance_message). Nothing at all when the checks found nothing, or when
         modes.handoff_evidence is off. Reading a local file takes microseconds.
         It reads no team file: an update that accepts nothing gets no context.
Used by: hooks.json PreToolUse entry (matcher "TaskUpdate"), added by the fork's
         UL-integrate milestone. Shares hooks/shared/evidence/ with write_checks.py.

It NEVER blocks: it returns no permissionDecision, always exits 0, and swallows any error.
"""
from __future__ import annotations

import json
import sys


def run(payload: dict) -> dict | None:
    if payload.get("tool_name") != "TaskUpdate":
        return None
    try:
        context = acceptance_lines(payload)
    except Exception:   # noqa: BLE001 - a hook must never fail the tool call
        context = None
    if not context:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}


def acceptance_lines(payload: dict) -> str | None:
    """The write-time checks' lines for the task this update accepts, or None."""
    tool_input = payload.get("tool_input") or {}
    task_id = str(tool_input.get("taskId") or "")
    if tool_input.get("status") != "completed" or not task_id:
        return None
    from shared import evidence as ev
    if ev.use_mode("handoff_evidence") == "off":
        return None
    try:    # the dispatcher's one team per run; a by-path load has no `shared` package
        from shared.frame import resolve as resolve_frame
        team = resolve_frame(payload).task_team(task_id)
    except ImportError:
        team = ev.resolve_team(payload.get("session_id"), task_id)
    # One line per check outcome (returns, repeats passed, notes): the lead sees every return
    # and every override (hooks/write_checks.py, D30).
    return ev.acceptance_message(team, task_id) or None


def main() -> int:
    try:
        output = run(json.loads(sys.stdin.read() or "{}"))
        if output:
            print(json.dumps(output))
    except Exception:   # noqa: BLE001 - a hook must never fail the tool call
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
