"""
Location: pact-plugin/hooks/shared/approval_record.py
Summary: Is the user's approval of a gated git command on record? Read from the session
         transcript the host writes: an AskUserQuestion call and, after it, its tool_result
         line carrying the user's answers, within the approval window.
Used by: hooks/merge_guard_pre.py (answer_on_record, before a token is consumed); tested by
         tests/test_merge_approval_record.py. tests/conftest.py patches answer_on_record for
         the binding tests that run the guard with no transcript; it is never switched off
         by an env var, a flag or a file.

Why the transcript: the merge-guard token file sits in the config dir and its mint hook is a
script, so the model can produce one without the user (a crafted PostToolUse payload, or the
JSON written by hand). The transcript is written by the host. A line counts only when its
structure says AskUserQuestion: an assistant entry's tool_use block named AskUserQuestion,
then a later user entry whose tool_result block answers that tool_use id and whose
toolUseResult holds the answers. Text the model controls (a Bash command, its output, a
question's own wording) sits inside string fields of other entries and cannot take that
shape; tool_use ids come from the API.

The caller decides what the answers approve (`approves(questions, answers)`), so the mint
rules stay in one place (merge_guard_post).

Only an option the user picked counts: a result the host filled in itself (an idle
auto-resolve with afkTimeoutMs, followUp, a typed response in place of an option) is no answer,
and an answer that is not exactly one option label of its question (a multi-select join,
"(notes only)") is dropped before the caller sees it. Field names checked against the host's
AskUserQuestion result schema and the shape of real transcript lines (questions, answers,
annotations).

Fail closed: no transcript_path, an unreadable file, no matching pair, an answer older than
the window or a frame that is not the main session's (a subagent's agent_id, a teammate's
agent_type) is no approval. Gated git acts are the lead's; a subagent never borrows the
lead's answer.

Known limits: the model runs as the same OS user, so a write to the transcript by a route no
check refuses (shell/settings.py refuses Bash, Write and Edit writes there) would be read.
Only the last MAX_TAIL_BYTES of the transcript are read; an answer further back is no answer.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Callable, Optional

MAX_TAIL_BYTES = 16 * 1024 * 1024
# A transcript timestamp a little ahead of this process's clock still counts.
CLOCK_SKEW_S = 60
_TOOL = "AskUserQuestion"
_PREFILTER = (_TOOL.encode(), b'"toolUseResult"')


def _epoch(value) -> Optional[float]:
    """An entry timestamp ("...Z" ISO) as epoch seconds, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def main_frame(hook_input: dict) -> bool:
    """True for the main session's frame: no agent_id, and an agent_type only when it is a
    lead spelling. Any other marker is a subagent or a teammate."""
    if hook_input.get("agent_id"):
        return False
    agent_type = hook_input.get("agent_type")
    if agent_type is None or agent_type == "":
        return True
    try:
        from shared.pact_context import is_lead
    except Exception:   # noqa: BLE001 - an unreadable frame holds no consent
        return False
    return is_lead(hook_input)


def _tail_entries(path: str) -> list:
    """The JSON object entries in the last MAX_TAIL_BYTES of the file that could be an
    AskUserQuestion call or a tool result; a partial first line is dropped."""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - MAX_TAIL_BYTES)
        handle.seek(start)
        data = handle.read()
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]
    entries = []
    for raw in lines:
        if not any(mark in raw for mark in _PREFILTER):
            continue
        try:
            entry = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _blocks(entry: dict, kind: str) -> list:
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == kind]


def _host_filled(result: dict) -> bool:
    """Whether the host, not the user, closed the dialog or left the answer open: an idle
    auto-resolve (afkTimeoutMs, kept selections the user never submitted), a request for
    another round (followUp), or free text typed instead of an option (response)."""
    response = result.get("response")
    return result.get("afkTimeoutMs") is not None or result.get("followUp") not in (None, False) \
        or (response is not None and not (isinstance(response, str) and not response.strip()))


def _picked(questions: list, answers: dict) -> dict:
    """The answers that are exactly one option label of their own question: an option the
    user picked. A multi-select join, "(notes only)" or any other text is left out."""
    labels = {}
    for question in questions:
        if isinstance(question, dict) and isinstance(question.get("options"), list):
            labels[question.get("question")] = {option.get("label") for option in
                                                question["options"] if isinstance(option, dict)}
    return {text: answer for text, answer in answers.items()
            if isinstance(answer, str) and answer in labels.get(text, ())}


def answered_questions(transcript_path, window_s: float, now: float | None = None) -> list:
    """(questions, answers) of each AskUserQuestion the user answered within `window_s`
    seconds before `now`, oldest first. Main-thread entries only (isSidechain not true)."""
    if not isinstance(transcript_path, str) or not transcript_path:
        return []
    now = time.time() if now is None else now
    asked: dict = {}
    answered = []
    for entry in _tail_entries(transcript_path):
        if entry.get("isSidechain") is True:
            continue
        kind = entry.get("type")
        if kind == "assistant":
            for block in _blocks(entry, "tool_use"):
                tool_input = block.get("input")
                if block.get("name") == _TOOL and isinstance(block.get("id"), str) \
                        and isinstance(tool_input, dict) \
                        and isinstance(tool_input.get("questions"), list):
                    asked[block["id"]] = (tool_input["questions"], _epoch(entry.get("timestamp")))
        elif kind == "user":
            result = entry.get("toolUseResult")
            answers = result.get("answers") if isinstance(result, dict) else None
            if not isinstance(answers, dict) or _host_filled(result):
                continue
            at = _epoch(entry.get("timestamp"))
            if at is None or not (now - window_s <= at <= now + CLOCK_SKEW_S):
                continue
            for block in _blocks(entry, "tool_result"):
                question = asked.pop(block.get("tool_use_id"), None)
                if question is None or block.get("is_error") is True:
                    continue
                questions, asked_at = question
                picked = _picked(questions, answers)
                if picked and asked_at is not None and asked_at <= at:
                    answered.append((questions, picked))
    return answered


def answer_on_record(hook_input, approves: Callable[[list, dict], bool],
                     window_s: float, now: float | None = None) -> bool:
    """True when this frame is the main session's and its transcript holds an answer,
    within the window, that `approves(questions, answers)` accepts. Never raises."""
    try:
        if not isinstance(hook_input, dict) or not main_frame(hook_input):
            return False
        for questions, answers in answered_questions(hook_input.get("transcript_path"),
                                                     window_s, now):
            if approves(questions, answers):
                return True
    except Exception:   # noqa: BLE001 - an unreadable record is no approval
        return False
    return False
