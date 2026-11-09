#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/unattended_prompt_gate.py
Summary: PermissionRequest hook. While unattended mode is ON for the session, a tool call
    that would wait on a permission prompt is denied at once with a reason, so the agent
    takes another route instead of stalling until someone comes back. While the mode is
    OFF it does nothing and the prompt shows as before (a first `gh` push the user wants
    to approve still asks).
Used by: pact-plugin/hooks/hooks.json PermissionRequest entry, through hooks/dispatch.py
    (dispatch_registry.REGISTRY["PermissionRequest"]).

Why a hook and not Claude Code's `permissions.denyInsteadOfPrompting` (2.1.319): that
setting is read only from user, --settings and managed settings, so it cannot follow the
session's flag (`repact-unattended on` mid-session, the owner's message turning it off),
and while it is on Claude Code withdraws AskUserQuestion, which the unattended gate and
the halt-resume path need. This hook reads the live switch (shared/unattended.read_mode)
on every prompt.

The host runs PermissionRequest hooks only when a call would otherwise prompt: allow
rules, the permission mode and PreToolUse decisions have already run. It never allows
anything; it only denies, and interrupt stays false so the turn goes on.

One `gate_refusal` journal row per deny: the hook, the tool name and the rule. Never the
tool input (a command line can carry a token).

Fail direction: to the prompt. Stdin that is not a JSON object, a session that cannot be
named, a flag that cannot be read (reads OFF) and any internal error give no decision.

Input: JSON on stdin, {"hook_event_name": "PermissionRequest", "tool_name": ..., "session_id": ...}
Output: deny = {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                 "decision": {"behavior": "deny", "message": ...}}}, exit 0;
        no decision = {"suppressOutput": true}, exit 0.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_hooks_dir = Path(__file__).resolve().parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

HOOK_NAME = "unattended_prompt_gate"
RULE = "unattended.prompt_denied"
_NO_DECISION = json.dumps({"suppressOutput": True})
_TOOL_MAX = 80


def _session_dir(payload: dict) -> str:
    """The session directory of this frame, or "" when it cannot be named."""
    from shared.pact_context import reconstruct_session_dir

    session_id = payload.get("session_id")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not isinstance(session_id, str) or not session_id or not project_dir:
        return ""
    return reconstruct_session_dir(project_dir, session_id)


def _tool(payload: dict) -> str:
    name = payload.get("tool_name")
    if not isinstance(name, str):
        return "this tool"
    shown = "".join(char for char in name if char.isprintable())[:_TOOL_MAX]
    return shown or "this tool"


def message(tool: str) -> str:
    return (f"Unattended mode is ON: nobody is here to answer a permission prompt, so this "
            f"{tool} call was denied without asking. Do not retry the same call. Take another "
            "route that needs no approval (a path inside your worktree or the scratchpad, a "
            "literal command instead of one built from $VAR or $(...), a dedicated tool), or "
            "report the step as blocked (open_questions `remaining:`) and go on with other "
            f"work. [rule: {RULE}]")


def decide(payload: dict) -> str | None:
    """The deny message for one payload, or None for no decision."""
    from shared import unattended

    session_dir = _session_dir(payload)
    if not unattended.read_mode(session_dir).on:
        return None
    tool = _tool(payload)
    if session_dir:
        try:
            from shared.session_journal import append_event, make_event

            append_event(make_event("gate_refusal", hook=HOOK_NAME, event="PermissionRequest",
                                    tool_name=tool, verdict="deny", rule=RULE),
                         session_dir=session_dir)
        except Exception:  # noqa: BLE001 - a row that cannot be written changes no verdict
            pass
    return message(tool)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason = decide(payload) if isinstance(payload, dict) else None
    except Exception as error:  # noqa: BLE001 - a check that cannot run decided nothing
        print(f"{HOOK_NAME}: internal error, no decision (the prompt shows): "
              f"{type(error).__name__}", file=sys.stderr)
        print(_NO_DECISION)
        return 0
    if reason is None:
        print(_NO_DECISION)
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {"behavior": "deny", "message": reason, "interrupt": False},
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
