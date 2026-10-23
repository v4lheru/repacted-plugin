"""
Location: pact-plugin/hooks/settings_guard.py
Summary: The verdict on a write to Claude Code settings or the running plugin's hooks
         (settings.write), or to the workflow state the repact commands own: the task files
         and the PACT session dirs (settings.task_state), and a repo's tracked task log
         `.repacted/tasks.jsonl` (settings.task_log), or to what stands for the user's merge
         approval: the approval tokens, the session transcripts, and a shell run of a hook
         entry (settings.consent). Not a hook of its own: worktree_guard calls it for
         Edit|Write|MultiEdit|NotebookEdit and env_guard for Bash, so one call meets it once.
Used by: hooks/worktree_guard.py, hooks/env_guard.py; tested by tests/test_settings_guard.py.

Which paths are protected, and which Bash commands write them, is hooks/shell/settings.py.
This module adds the frame: a frame with any agent marker (an agent_id, an agent_type that
is not the lead's, or an actor that resolves to a teammate) is denied; the lead, or a
session with no marker (a user without teams), is asked, so the user can still approve a
settings change; unattended mode (REPACT_UNATTENDED or the session's flag) turns the ask
into a deny. A frame that cannot be read is denied. Task state is denied for every frame, the
lead's included: no one approves a hand edit of it, since the repact commands are the way
through and a lead that could write a task file could complete its own work unchecked.
The task log is refused to an agent frame only: a coder could otherwise append a `done` line
claiming evidence repact-task never checked, which a union merge then keeps; the lead's
direct-edit lane and a session with no agent still write it. Consent state is denied for
every frame: the user approves a gated git act by answering AskUserQuestion, never by an
approval of a hand-written token or transcript line.

The `shared` package is imported only once a protected place is hit, so the common call
pays for the path check alone.
"""

from __future__ import annotations

import os

from shell.settings import (_TRIGGERS, CLAUDE_CLI_WORDS, CONSENT_STATE, TASK_LOG, TASK_STATE,
                            protected_place, written_place)
from shell.words import Unreadable

RULE_ID = "settings.write"
RULE_TASK_STATE = "settings.task_state"
RULE_TASK_LOG = "settings.task_log"
RULE_CONSENT = "settings.consent"
FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def _shim_text() -> str | None:
    """The launcher shim's exact text, which lets `repact` on PATH name task state; None when
    shared.repact_shim cannot give it (the shim then counts as any other program)."""
    try:
        from shared import repact_shim
        return repact_shim.shim_text()
    except (ImportError, ValueError):
        return None


def _target(payload: dict) -> str | None:
    """What protected place this call would write, else None."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    if tool in FILE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return protected_place(path, cwd) if isinstance(path, str) and path else None
    if tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return None
        try:
            return written_place(command, cwd, shim=_shim_text())
        except Unreadable:
            folded = command.casefold()
            if any(word in folded for word in _TRIGGERS) or CLAUDE_CLI_WORDS.search(folded):
                return "a command that names settings or hooks and cannot be read"
    return None


def _unattended(payload: dict) -> bool:
    from shared import unattended

    if unattended.read_mode("").on:
        return True
    session_id = payload.get("session_id")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not isinstance(session_id, str) or not session_id or not project_dir:
        return False
    from shared.pact_context import reconstruct_session_dir

    return unattended.read_mode(reconstruct_session_dir(project_dir, session_id)).on


def _frame_agent_id(payload: dict):
    """The raw agent_id. Tighten-only: any value denies; its absence grants nothing."""
    return payload.get("agent_id")


def _agent_frame(payload: dict) -> bool:
    """True for a frame with any agent marker, or an actor that resolves to a teammate."""
    from shared import actor as actor_module

    agent_id = _frame_agent_id(payload)
    agent_type = payload.get("agent_type")
    if agent_id or (agent_type is not None and not isinstance(agent_type, str)):
        return True
    actor = actor_module.resolve_actor(payload)
    if agent_type and not actor.is_lead:
        return True
    return actor.source == "registry" or (actor.source == "host" and not actor.is_lead)


def _frame_decision(payload: dict) -> str:
    """deny for an agent frame or unattended mode, ask for the lead or an unmarked session."""
    if _agent_frame(payload):
        return "deny"
    return "deny" if _unattended(payload) else "ask"


def _output(decision: str, what: str) -> dict:
    if what in CONSENT_STATE:
        reason = (f"Blocked: this call would write or run {what}. Reading it is fine. A gated "
                  f"git act is approved only by the user's answer to AskUserQuestion, the "
                  f"literal command in the chosen option; ask, then run the command unchanged. "
                  f"[rule: {RULE_CONSENT}]")
    elif what == TASK_LOG:
        reason = (f"Blocked: this call would write {what}; an agent may not edit it by hand. "
                  f"Reading it is fine. Run repact-task (done, set, reopen) for the change, or "
                  f"hand the step to the lead. [rule: {RULE_TASK_LOG}]")
    elif what in TASK_STATE:
        reason = (f"Blocked: this call would write {what}, which only the repact commands and "
                  f"the task tools write. Reading it is fine. Run the matching command instead: "
                  f"repact submit (the coder), repact accept, reject or amend (the lead), or "
                  f"TaskUpdate for a task no repact command made. [rule: {RULE_TASK_STATE}]")
    elif decision == "ask":
        reason = (f"This call would write {what}. Approve only a settings change you asked "
                  f"for; /repacted:init makes the plugin's own changes. [rule: {RULE_ID}]")
    else:
        reason = (f"Blocked: this call would write {what}, which an agent may not change. Run "
                  f"/repacted:init, or ask the user to edit the file themselves. "
                  f"[rule: {RULE_ID}]")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


def decide(payload: dict) -> dict | None:
    """The PreToolUse output for one payload: a deny, an ask, or None when it writes no
    protected place. Never raises: an error on a protected write denies."""
    try:
        what = _target(payload)
    except Exception:   # noqa: BLE001 - fail closed only where a protected word is named
        folded = repr(payload.get("tool_input")).casefold()
        what = ("a path this check could not read"
                if any(word in folded for word in _TRIGGERS) else None)
    if not what:
        return None
    if what in TASK_STATE or what in CONSENT_STATE:
        return _output("deny", what)
    if what == TASK_LOG:
        try:
            agent = _agent_frame(payload)
        except Exception:   # noqa: BLE001 - an unreadable frame holds no lead authority
            agent = True
        return _output("deny", what) if agent else None
    try:
        decision = _frame_decision(payload)
    except Exception:   # noqa: BLE001 - an unreadable frame holds no lead authority
        decision = "deny"
    return _output(decision, what)
