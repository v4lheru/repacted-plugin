#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/subagent_stop.py
Summary: SubagentStop check, both execution modes: the HANDOFF backstop. When a subagent working a `repact dispatch` task
    stops and the task has no HANDOFF of the current rev, it reads the last fenced ```json
    block of the subagent's own transcript (`agent_transcript_path`, never `output_file`, which
    is empty under `-p`) and stores it with `ops.submit.handoff(..., relayed=True)`. No block:
    the stop is refused once with "run `repact submit <id>` before you end". A block that fails
    the submit checks: refused once with the problems. Under stop_hook_active the refusal
    becomes a systemMessage and the stop lands.
Used by: hooks/dispatch_registry.py (native check `subagent_stop:dispatch_check`); `main` runs it
    alone when the dispatcher runs a deeply nested input apart. On the transcript boundary
    allowlist (tests/contract/test_transcript_boundary.py READERS).

Which task: the subagent's first prompt is the one `repact dispatch` prints, "Read
<session dir>/briefs/<id>.md and do what it says.", and its session dir must be the one this
session id resolves to. A frame that is no dispatched coder, a task that is not in_progress,
an open two-phase teachback (stored, not yet accepted: the coder ends its turn on the
`TEACHBACK SUBMITTED #<id>` line), a Teams teammate with an intentional_wait set, and a
current HANDOFF (one no rejection names) all pass. Anything this check cannot read passes
too: it is a backstop for `repact submit`, never a gate (task_write_guard is the gate).
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Optional

JSON_FENCE = re.compile(r"```json[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.DOTALL)
TRANSCRIPT_MAX = 64 * 1024 * 1024
BLOCK_MAX = 256 * 1024


def _text_of(entry: dict) -> str:
    """The text blocks of one transcript entry, joined; tool calls and results are skipped."""
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(block.get("text", "") for block in content
                     if isinstance(block, dict) and block.get("type") == "text"
                     and isinstance(block.get("text"), str))


def read_transcript(path: str) -> tuple:
    """(first user prompt, last fenced json block of an assistant message) of a subagent
    transcript; ("", None) when it cannot be read."""
    first, last = "", None
    try:
        file = Path(path)
        if not file.is_file() or file.stat().st_size > TRANSCRIPT_MAX:
            return "", None
        with file.open(encoding="utf-8", errors="replace") as lines:
            for line in lines:
                try:
                    entry = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(entry, dict):
                    continue
                kind = entry.get("type")
                if kind == "user" and not first:
                    first = _text_of(entry)
                elif kind == "assistant":
                    blocks = JSON_FENCE.findall(_text_of(entry))
                    if blocks:
                        last = blocks[-1]
    except OSError:
        return "", None
    return first, last


def _fold(text) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip() if isinstance(text, str) else ""


def has_current_handoff(meta: dict) -> bool:
    """A stored HANDOFF that no handoff_rejection names (a rejected rev needs a new submit)."""
    from shared import amendments as AM

    handoff = meta.get("handoff")
    rev = AM.handoff_rev(handoff)
    if not isinstance(handoff, dict) or rev is None:
        return False
    rejection = meta.get("handoff_rejection")
    return not (isinstance(rejection, dict) and rejection.get("rev") == rev)


def _task_of(payload: dict, first: str):
    """(ops Context, task id, task) of the dispatched task this subagent works, or None."""
    from shared.ops import OpError, frame, records
    from shared.ops.brief import BRIEF_PROMPT

    match = BRIEF_PROMPT.search(first or "")
    if match is None:
        return None
    try:
        ctx = records.resolve(None, None, payload.get("session_id"), frame.Caller(frame.BACKSTOP))
        if Path(match["path"]).parent.parent.resolve() != ctx.session_dir:
            return None
        task = ctx.store.get(ctx.ref(match["id"]))
    except (OpError, OSError, ValueError):
        return None
    if not isinstance(task, dict):
        return None
    dispatched = records.metadata(task).get("dispatch")
    if not isinstance(dispatched, dict):
        return None
    agent_type = payload.get("agent_type")
    if isinstance(agent_type, str) and agent_type and _fold(agent_type) != _fold(dispatched.get("agent")):
        return None
    return ctx, match["id"], task


def _relay(ctx, task_id: str, block: str) -> Optional[str]:
    """Store the block as the HANDOFF; the refusal text, or None when it was stored."""
    from shared.ops import OpError, submit
    from shared.task_store import _parse

    if len(block.encode("utf-8")) > BLOCK_MAX:
        return f"the json block is over {BLOCK_MAX // 1024} KB"
    try:
        given = _parse(block)
    except (ValueError, RecursionError) as error:
        return f"the last ```json block is not strict JSON ({str(error)[:120]})"
    try:
        submit.handoff(ctx, task_id, given, relayed=True)
    except OpError as error:
        return "; ".join(error.problems)[:1500]
    return None


def decide(payload: dict) -> tuple:
    """(refusal text, note) for this stop; (None, None) to let it land quietly."""
    path = payload.get("agent_transcript_path")
    if not isinstance(path, str) or not path:
        return None, None
    first, block = read_transcript(path)
    found = _task_of(payload, first)
    if found is None:
        return None, None
    ctx, task_id, task = found
    meta = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    if task.get("status") != "in_progress" or isinstance(meta.get("intentional_wait"), dict):
        return None, None
    teachback = meta.get("teachback")
    if not isinstance(teachback, dict):
        return (f"task #{task_id} has no teachback yet: run `repact submit {task_id} --teachback "
                "<file>` (your brief names the file) before you end."), None
    if not teachback.get("accepted") or has_current_handoff(meta):
        return None, None
    if block is None:
        return (f"task #{task_id} has no HANDOFF: run `repact submit {task_id}` before you end "
                "(your brief prints the command and the skeleton file)."), None
    refusal = _relay(ctx, task_id, block)
    if refusal is None:
        return None, f"subagent_stop: the HANDOFF of #{task_id} was relayed from the subagent's transcript."
    return (f"task #{task_id}: the HANDOFF in your last ```json block was not stored: {refusal}. "
            f"Fix every problem, then run `repact submit {task_id}` before you end."), None


def verdict(payload) -> tuple:
    """(decision, text): ("block", reason), ("message", text) or (None, "")."""
    if not isinstance(payload, dict):
        return None, ""
    try:
        refusal, note = decide(payload)
    except Exception:  # noqa: BLE001 - a backstop that cannot run lets the stop land
        return None, ""
    if refusal and payload.get("stop_hook_active"):
        return "message", (f"subagent_stop (refusal degraded by the stop_hook_active loop guard, "
                           f"stop allowed): {refusal}")
    if refusal:
        return "block", f"subagent_stop: {refusal}"
    return ("message", note) if note else (None, "")


def dispatch_check(payload: dict, ctx=None):
    """The dispatcher entry: a block, a systemMessage, or None."""
    import dispatch_registry as registry

    decision, text = verdict(payload)
    if decision == "block":
        return registry.Result(decision="block", reason=text)
    if decision == "message":
        return registry.Result(system_message=text)
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, RecursionError):
        payload = None
    decision, text = verdict(payload)
    if decision == "block":
        print(json.dumps({"decision": "block", "reason": text}))
    elif decision == "message":
        print(json.dumps({"systemMessage": text}))
    else:
        print(json.dumps({"suppressOutput": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
