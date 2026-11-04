#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/owner_prompt.py
Summary: The owner's typed prompt turns unattended mode off. One native check on three
    events: SessionStart (startup or resume) leaves the launch marker; PreToolUse
    ScheduleWakeup|CronCreate records the text of the prompt the model scheduled (and
    refuses the call when it cannot); UserPromptSubmit, while the mode is ON, turns it off
    when the prompt is one the owner typed: clear_sticky, write_flag(off, by=owner_prompt),
    one unattended_mode{state=off, cause=owner_prompt} row and one line of context.
Used by: hooks/dispatch_registry.py (native check `owner_prompt:dispatch_check` on
    UserPromptSubmit, SessionStart and PreToolUse); `python3 hooks/owner_prompt.py <event>`
    runs the same check alone.

What counts as typed. The host computes where a prompt came from but does not pass it to
the hook (payload keys seen: session_id, the transcript's path, cwd, prompt, prompt_id,
permission_mode, agent_id, agent_type, effort, hook_event_name, session_title), so the rule
is built from what the hook can see. A prompt counts only when ALL of this holds:
  - the session is interactive: CLAUDE_CODE_ENTRYPOINT is set and does not start with
    "sdk" (the host forces sdk-cli under -p; the CLI, Desktop and IDE sessions all count);
  - agent_type is absent or a lead spelling (a teammate's frame never counts);
  - the prompt carries no harness tag (HARNESS_TAGS): task notifications, teammate and
    cross-session messages, desktop scheduled tasks and command output all arrive tagged;
  - it does not open with a fixed text the host writes itself (HOST_PROMPTS: the
    usage-limit auto-resume, seen in transcripts as a plain-text prompt);
  - it is not the launch prompt of a run whose mode came from REPACT_UNATTENDED=1 (also
    `repact work --unattended`): a start or resume with that variable on leaves the launch
    marker, and the first prompt after it takes it. Any other resume leaves none;
  - it contains no prompt the model scheduled: ScheduleWakeup and CronCreate fires (also
    /loop) arrive as plain text equal to tool_input.prompt, so every such text is recorded
    here at PreToolUse, and the durable crons of <project>/.claude/scheduled_tasks.json are
    read too. A list that cannot be read refuses.
Known limit: a host-made plain-text prompt that is none of the above would count as typed;
the usage-limit resume was the only one found in 60 days of transcripts.

Fail direction: ON. Any error leaves the mode as it is (the owner keeps the manual
`repact-unattended off`), and the prompt goes through: this check never blocks a prompt.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from dispatch_registry import Result  # noqa: E402

OFF_CONTEXT = ("Unattended mode is off: you typed in the session. "
               "Ask your parked questions now (repact decide lists them).")
CAUSE = "owner_prompt"
SCHEDULING_TOOLS = frozenset({"ScheduleWakeup", "CronCreate"})
LAUNCH_SOURCES = frozenset({"startup", "resume"})
HEADLESS_ENTRYPOINT_PREFIX = "sdk"
# Plain-text prompts the host writes itself, matched at the start after prompt_text().
HOST_PROMPTS = (
    "Your claude.ai usage limit has reset.",
    "Stop hook feedback:",
    "Your response above was stopped by a safety classifier",
    "[Your previous response had no visible output.",
)
DURABLE_CRONS = (".claude", "scheduled_tasks.json")
DURABLE_MAX = 1 << 20
# An opening tag the harness wraps a prompt it made in; matched anywhere, any case.
HARNESS_TAGS = re.compile(
    r"<\s*(teammate-message|task-notification|scheduled-task|cross-session-message|channel"
    r"|system-reminder|command-[a-z-]+|bash-[a-z-]+|local-command-[a-z-]+)\b", re.IGNORECASE)
MAX_STDIN = 1 << 22


def _session_dir(payload: dict) -> str:
    """The session directory of this frame, or "" when it cannot be named."""
    from shared.pact_context import reconstruct_session_dir

    session_id = payload.get("session_id")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not isinstance(session_id, str) or not session_id or not project_dir:
        return ""
    return reconstruct_session_dir(project_dir, session_id)


def _durable_prompts(payload: dict) -> list[str] | None:
    """The prompts of the project's durable crons ([] when there are none), or None when a
    cron file exists and cannot be read or parsed."""
    from shared import unattended

    roots = {os.environ.get("CLAUDE_PROJECT_DIR", ""), payload.get("cwd") or ""}
    found: list[str] = []
    for root in sorted(item for item in roots if isinstance(item, str) and item):
        path = os.path.join(root, *DURABLE_CRONS)
        try:
            with open(path, "rb") as handle:
                raw = handle.read(DURABLE_MAX + 1)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
        try:
            data = json.loads(raw.decode("utf-8")) if len(raw) <= DURABLE_MAX else None
        except (ValueError, RecursionError):
            return None
        if not isinstance(data, dict):
            return None
        found.extend(unattended.prompt_text(text) for text in _prompt_values(data))
    return found


def _prompt_values(node) -> list[str]:
    """Every string held under a "prompt" key, at any depth."""
    if isinstance(node, dict):
        return [value for key, item in node.items()
                for value in ([item] if key == "prompt" and isinstance(item, str) else _prompt_values(item))]
    if isinstance(node, list):
        return [value for item in node for value in _prompt_values(item)]
    return []


def is_typed(payload: dict, session_dir: str) -> bool:
    """True only when the prompt is one the owner typed at the CLI terminal (module
    docstring). The launch marker is taken before this is asked."""
    from shared import unattended
    from shared.pact_context import is_lead

    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").strip()
    if not entrypoint or entrypoint.startswith(HEADLESS_ENTRYPOINT_PREFIX):
        return False
    if payload.get("agent_type") not in (None, "") and not is_lead(payload):
        return False
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or HARNESS_TAGS.search(prompt):
        return False
    recorded = unattended.scheduled_prompts(session_dir)
    durable = _durable_prompts(payload)
    if recorded is None or durable is None:
        return False
    text = unattended.prompt_text(prompt)
    if text.startswith(tuple(unattended.prompt_text(item) for item in HOST_PROMPTS)):
        return False
    return not any(item and item in text for item in recorded + durable)


def on_prompt(payload: dict) -> Result | None:
    """UserPromptSubmit: take the launch marker, then turn the mode off for a typed prompt."""
    from shared import unattended
    from shared.session_journal import append_event, make_event

    session_dir = _session_dir(payload)
    if not session_dir or not os.path.isdir(session_dir):
        return None
    launched = unattended.take_launch(session_dir)
    if launched or not unattended.read_mode(session_dir).on or not is_typed(payload, session_dir):
        return None
    row = make_event("unattended_mode", state="off", cause=CAUSE)
    unattended.clear_sticky(session_dir)
    unattended.write_flag(session_dir, False, row.get("ts", ""), by=CAUSE)
    append_event(row, session_dir=session_dir)
    return Result(context=OFF_CONTEXT)


def on_start(payload: dict) -> None:
    """SessionStart: a start or a resume with REPACT_UNATTENDED on leaves the launch marker."""
    from shared import unattended
    from shared.session_journal import make_event

    if payload.get("source") not in LAUNCH_SOURCES or not unattended.read_mode("", os.environ).on:
        return
    session_dir = _session_dir(payload)
    if session_dir:
        unattended.mark_launch(session_dir, make_event("unattended_mode").get("ts", ""))


def on_schedule(payload: dict) -> Result | None:
    """PreToolUse ScheduleWakeup|CronCreate: record the prompt; refuse when it cannot be."""
    from shared import unattended

    tool_input = payload.get("tool_input")
    text = tool_input.get("prompt") if isinstance(tool_input, dict) else None
    session_dir = _session_dir(payload)
    if not session_dir or not os.path.isdir(session_dir):
        return None
    if unattended.record_scheduled(session_dir, text if isinstance(text, str) else ""):
        return None
    return Result(decision="deny", rule="owner_prompt.unrecorded",
                  reason=("The scheduled prompt could not be recorded in the session directory "
                          f"({unattended.SCHEDULED_NAME}), so a wake-up could later read as the "
                          "owner's typing. Fix or remove that file, then schedule again."))


def dispatch_check(payload: dict, ctx=None) -> Result | None:
    """The dispatcher entry, for the three events. Never blocks a prompt or a start."""
    event = getattr(ctx, "event", "") or payload.get("hook_event_name", "")
    if event == "PreToolUse":
        if payload.get("tool_name") not in SCHEDULING_TOOLS:
            return None
        try:
            return on_schedule(payload)
        except Exception as error:  # noqa: BLE001 - an unrecorded schedule is refused
            return Result(decision="deny", rule="owner_prompt.error",
                          reason=f"The scheduled prompt could not be recorded ({type(error).__name__}).")
    try:
        if event == "SessionStart":
            on_start(payload)
        elif event == "UserPromptSubmit":
            return on_prompt(payload)
    except Exception as error:  # noqa: BLE001 - the mode stays as it is; the prompt goes on
        print(f"owner_prompt: {type(error).__name__}; unattended mode left as it is",
              file=sys.stderr)
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read(MAX_STDIN) or "{}")
    except ValueError:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    if len(sys.argv) > 1:
        payload.setdefault("hook_event_name", sys.argv[1])
    elif "tool_name" in payload:
        payload.setdefault("hook_event_name", "PreToolUse")
    result = dispatch_check(payload)
    if result is not None and result.decision == "deny":
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": result.reason}}))
        return 0
    if result is not None and result.context:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": payload.get("hook_event_name", ""),
            "additionalContext": result.context}}))
        return 0
    print(json.dumps({"suppressOutput": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
