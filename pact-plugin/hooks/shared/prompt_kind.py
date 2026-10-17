"""
Location: pact-plugin/hooks/shared/prompt_kind.py
Summary: What kind of turn a lead's UserPromptSubmit opened (typed text, a teammate message,
    an idle notice, a task notification, a cron tick), the one `prompt_kind` journal row
    per prompt (kind, length, straggler flag, allowlisted tag: never the text), and the
    straggler line for an idle notice older than its sender's latest task change.
Used by: hooks/journal_emitter.py (UserPromptSubmit). Moved out of missed_wake_scan.py in
    the refactor core lane: these are journal writes and a prompt read, not a scan of waits.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from shared.pact_context import get_team_name, is_lead
from shared.session_journal import append_event, get_journal_path, make_event
from shared.session_state import is_safe_path_component
from shared.task_store import TaskStore

PROMPT_EVENT = "prompt_kind"
_TEAMMATE_OPEN, _TEAMMATE_CLOSE = "<teammate-message", "</teammate-message>"
# Harness markup tag -> journaled kind. Only these keys reach the journal, so `tag` cannot
# carry prompt text. The two cron names are unverified.
PROMPT_TAG_KINDS = {
    "teammate-message": "teammate_message",     # idle_notification when every block is an idle notice
    "task-notification": "task_notification",
    "scheduled-task": "cron",
    "cron": "cron",
    "command-name": "user_typed",               # a slash command the user typed
    "command-message": "user_typed",
    "command-args": "user_typed",
    "bash-input": "user_typed",                 # a `!` shell line the user typed
    "bash-stdout": "other",
    "bash-stderr": "other",
    "local-command-stdout": "other",
    "local-command-stderr": "other",
    "local-command-caveat": "other",
    "cross-session-message": "other",
    "system-reminder": "other",
    "tick": "other",
}
_PROMPT_TAG = re.compile(r"\s*<([A-Za-z][A-Za-z0-9_-]{0,63})(?=[\s/>])")
_NOTICE_TS_MAX = 40   # a notice timestamp longer than this is not one the harness wrote


def idle_notice_bodies(prompt: str) -> "list[dict] | None":
    """Parsed bodies when the prompt is only teammate-message blocks whose bodies are all idle
    notices; else None. One forward str.find scan (no backtracking)."""
    at, bodies = 0, []
    while True:
        start = prompt.find(_TEAMMATE_OPEN, at)
        if prompt[at:start if start >= 0 else len(prompt)].strip():
            return None
        if start < 0:
            return bodies or None
        body_start = prompt.find(">", start)
        end = prompt.find(_TEAMMATE_CLOSE, body_start) if body_start >= 0 else -1
        if end < 0:
            return None
        try:
            body = json.loads(prompt[body_start + 1:end])
        except (ValueError, RecursionError):
            return None
        if not isinstance(body, dict) or body.get("type") != "idle_notification":
            return None
        at = end + len(_TEAMMATE_CLOSE)
        bodies.append(body)


def classify_prompt(prompt) -> "tuple[str, int, str | None]":
    """(kind, length in characters, leading tag or None) for a UserPromptSubmit `prompt`,
    decided from its shape alone. The tag is returned only as a key of PROMPT_TAG_KINDS;
    nothing else of the prompt is returned. Pure; never raises (a prompt that is not a
    string is `other`, length 0)."""
    if not isinstance(prompt, str):
        return "other", 0, None
    opening = _PROMPT_TAG.match(prompt)
    if opening is None:
        return "user_typed", len(prompt), None
    tag = opening.group(1)
    kind = PROMPT_TAG_KINDS.get(tag)
    if kind is None:
        return "other", len(prompt), None
    if tag == "teammate-message" and idle_notice_bodies(prompt) is not None:
        kind = "idle_notification"
    return kind, len(prompt), tag


def _notice_time(value) -> "datetime | None":
    """The tz-aware time of an idle notice's `timestamp`, or None when it is not a short
    ISO-8601 string with an offset. Never raises."""
    if not isinstance(value, str) or len(value) > _NOTICE_TS_MAX:
        return None
    if value.endswith("Z"):     # fromisoformat reads "Z" only from Python 3.11
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _utc_seconds(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def straggler_line(prompt, newest_change: dict) -> "str | None":
    """Straggler line for an idle-notice-only prompt, or None. A notice older than its
    sender's newest task change needs nothing. Pure; only a safe sender name and a
    re-rendered parsed timestamp reach the line, never notice text."""
    try:
        if not isinstance(prompt, str) or not isinstance(newest_change, dict):
            return None
        bodies = idle_notice_bodies(prompt)
        if not bodies:
            return None
        lines, seen = [], set()
        for body in bodies:
            name = body.get("from")
            if not is_safe_path_component(name) or name in seen:
                continue
            sent = _notice_time(body.get("timestamp"))
            changed = newest_change.get(name)
            if sent is None or not isinstance(changed, datetime) or changed.tzinfo is None:
                continue
            if sent < changed:
                seen.add(name)
                lines.append(f"{name}'s idle notice ({_utc_seconds(sent)}) is older than its "
                             f"latest task change ({_utc_seconds(changed)}): a straggler, no action.")
        return "\n".join(lines) or None
    except Exception:
        return None


def newest_change_by_owner(team_name: str, tasks_base_dir: "str | None" = None) -> dict:
    """Owner name -> tz-aware time of that owner's newest task file in the team's task
    directory. Reads each file's `owner` and mtime and nothing else, through the store's
    path defences (safe team name, anchored directory, no symlinks, no dotfiles). {} on error."""
    newest: dict = {}
    store = TaskStore(tasks_base_dir) if tasks_base_dir else TaskStore()
    for task_file in store.files(team_name):
        try:
            mtime = task_file.stat().st_mtime
            task = json.loads(task_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError, OverflowError):
            continue
        owner = task.get("owner") if isinstance(task, dict) else None
        if not is_safe_path_component(owner):
            continue
        changed = datetime.fromtimestamp(mtime, timezone.utc)
        if owner not in newest or changed > newest[owner]:
            newest[owner] = changed
    return newest


def build_straggler_line(input_data: dict, tasks_base_dir: "str | None" = None) -> "str | None":
    """The straggler line for the lead's UserPromptSubmit, or None in every other case (a
    teammate or plain frame, another event, a prompt that is not only idle notices, no
    team). Never raises."""
    try:
        if input_data.get("hook_event_name") != "UserPromptSubmit" or not is_lead(input_data):
            return None
        prompt = input_data.get("prompt")
        if classify_prompt(prompt)[0] != "idle_notification":
            return None
        team_name = get_team_name()
        if not team_name:
            return None
        return straggler_line(prompt, newest_change_by_owner(team_name, tasks_base_dir))
    except Exception:
        return None


def journal_prompt_kind(input_data: dict, straggler: bool = False) -> None:
    """Journal one prompt_kind row (kind, length, straggler flag, allowlisted tag) for a lead
    UserPromptSubmit: the idle-turn experiment. Never the text. Never raises."""
    try:
        if input_data.get("hook_event_name") != "UserPromptSubmit":
            return
        if not is_lead(input_data) or not get_journal_path():
            return
        kind, chars, tag = classify_prompt(input_data.get("prompt"))
        fields = {"kind": kind, "chars": chars, "straggler": straggler is True}
        if tag:
            fields["tag"] = tag
        append_event(make_event(PROMPT_EVENT, **fields))
    except Exception:
        return
