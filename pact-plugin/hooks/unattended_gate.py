#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/unattended_gate.py
Summary: PreToolUse hook (matcher: AskUserQuestion). While unattended mode is ON it
    refuses the question and tells the caller what to do for each question: take the
    option marked "(Recommended)" and record it, or, for an always-ask question, record
    a halt and stop that feature. While the mode is OFF it does nothing.
Used by: pact-plugin/hooks/hooks.json PreToolUse "AskUserQuestion" matcher entry.

The switch and the classifier live in shared/unattended.py. This hook decides nothing
about a merge, a push or any other act, and it writes no approval: a refused question
never runs, so no PostToolUse fires for it and the merge guard mints nothing from it.
It reaches no network, no model and no Telegram; it tells the caller to send the notice.

A question whose text was refused as always-ask earlier in the session stays
always-ask (code asked_before), so changing its options does not change its verdict.

Every refusal writes one `gate_refusal` journal row: the gate, the worst class, its
code, and per question a class, a code and a hash. Never text of the question.

Fail direction: to the terminal. Stdin that is not a JSON object, a flag that cannot be
read or parsed (journal row `unattended_mode`, state off) and any internal error allow
the call: the question is then asked and nothing is chosen by default.

Input: JSON on stdin, {"tool_name": "AskUserQuestion", "tool_input": {...}, "session_id": ...}
Output: deny = the JSON envelope on stdout, the reason on stderr, exit 2;
        allow = {"suppressOutput": true}, exit 0.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

_hooks_dir = Path(__file__).resolve().parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})
_TOOL_NAME = "AskUserQuestion"
_COMMAND = f"python3 {shlex.quote(str(_hooks_dir.parent / 'bin' / 'repact-unattended'))}"


def _session_dir(payload: dict) -> str:
    """The session directory of this frame, or "" when it cannot be named."""
    from shared.pact_context import reconstruct_session_dir

    session_id = payload.get("session_id")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not isinstance(session_id, str) or not session_id or not project_dir:
        return ""
    return reconstruct_session_dir(project_dir, session_id)


def _journal(session_dir: str, event_type: str, **fields) -> None:
    """Append one journal row, best effort."""
    if not session_dir:
        return
    try:
        from shared.session_journal import append_event, make_event

        append_event(make_event(event_type, **fields), session_dir=session_dir)
    except Exception:  # noqa: BLE001 - a row that cannot be written changes no verdict
        pass


def _asked_before(session_dir: str) -> frozenset[str]:
    """The hashes of the questions this gate refused as always-ask in this session."""
    if not session_dir:
        return frozenset()
    try:
        from shared import unattended
        from shared.session_journal import read_events_from

        return frozenset(
            item["ref"]
            for row in read_events_from(session_dir, "gate_refusal")
            if row.get("hook") == unattended.GATE_NAME and isinstance(row.get("questions"), list)
            for item in row["questions"]
            if isinstance(item, dict) and item.get("class") == unattended.CLASS_ALWAYS_ASK
            and isinstance(item.get("ref"), str) and item["ref"]
        )
    except Exception:  # noqa: BLE001 - no history means no sticky verdict, not an error
        return frozenset()


def decide(payload: dict) -> str | None:
    """The refusal text for one payload, or None to allow."""
    if payload.get("tool_name") != _TOOL_NAME:
        return None
    from shared import unattended

    session_dir = _session_dir(payload)
    mode = unattended.read_mode(session_dir)
    if mode.cause in unattended.INVALID_CAUSES:
        _journal(session_dir, "unattended_mode", state="off", cause=mode.cause)
        print(f"unattended_gate: the session flag is not usable ({mode.cause}); "
              "the mode reads OFF", file=sys.stderr)
    if not mode.on:
        return None
    verdicts = unattended.classify_call(payload.get("tool_input"))
    verdicts = unattended.all_always_ask(verdicts, "asked_before", _asked_before(session_dir))
    named = unattended.worst(verdicts)
    _journal(
        session_dir, "gate_refusal",
        hook=unattended.GATE_NAME, event="PreToolUse", tool_name=_TOOL_NAME, verdict="deny",
        rule=named.code, **{"class": named.cls},
        questions=[{"class": item.cls, "code": item.code, "ref": item.ref} for item in verdicts],
    )
    session_arg = f" --session-dir {shlex.quote(session_dir)}" if session_dir else ""
    return unattended.refusal_text(verdicts, _COMMAND, session_arg)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason = decide(payload) if isinstance(payload, dict) else None
    except Exception as error:  # noqa: BLE001 - a check that cannot run decided nothing
        print(f"unattended_gate: internal error, allowing (the question is asked): "
              f"{type(error).__name__}", file=sys.stderr)
        return 0
    if reason is None:
        print(_ALLOW_OUTPUT)
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
