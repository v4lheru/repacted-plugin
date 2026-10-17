"""Is a teammate working right now? Read from its transcript.

Location: pact-plugin/hooks/shared/transcript_activity.py. Stdlib only, so the
lead's missed-wake scan (hooks/missed_wake_scan.py via shared/background_work.py) and the
operator tools (shared/transcripts.py, loading this by path) share one definition of "busy".

A teammate is WORKING when its transcript shows either:
  - a tool call from its LAST message still waiting for its result, started within
    PENDING_MAX_SECONDS of `now`. A foreground Bash call can run 10 minutes and a
    full-suite wait longer, with nothing written in between, so a write window alone
    would call a busy teammate idle. The cap keeps a teammate that died mid-call from
    being called busy for ever; reading only the last message keeps an orphaned call
    from doing the same; or
  - an entry written within ACTIVE_WINDOW_SECONDS (10 min) of `now` while its turn is
    still open. A teammate whose turn ENDED is idle however recent its last write: nothing
    wakes it when its background job finishes.

Layout (the platform's, measured 2026-09-27): the lead transcript is
<projects>/<slug>/<session>.jsonl and each in-process teammate writes
<projects>/<slug>/<session>/subagents/agent-a<name>-<16 hex>.jsonl.

Only timestamps, entry types, tool_use ids and stop reasons are read. No message text
leaves this module.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

ACTIVE_WINDOW_SECONDS = 600
PENDING_MAX_SECONDS = 3600
TAIL_BYTES = 262_144
_HEX_SUFFIX = re.compile(r"-[0-9a-f]{16}\.jsonl$")


def parse_ts(value) -> Optional[datetime]:
    """An entry timestamp ("...Z" ISO) as an aware UTC datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def subagents_dir(lead_transcript: os.PathLike | str) -> Path:
    """<session>.jsonl -> <session>/subagents."""
    path = Path(lead_transcript)
    return path.with_suffix("") / "subagents"


def teammate_transcript(lead_transcript, name: str) -> Optional[Path]:
    """The newest agent-a<name>-<16 hex>.jsonl beside the lead transcript, or None."""
    if not lead_transcript or not isinstance(name, str) or not name or "/" in name:
        return None
    try:
        folder = subagents_dir(lead_transcript)
        prefix = f"agent-a{name}"
        found = [p for p in folder.glob(f"{prefix}-*.jsonl")
                 if p.name[len(prefix):len(prefix) + 1] == "-" and _HEX_SUFFIX.search(p.name)
                 and len(p.name) == len(prefix) + 1 + 16 + len(".jsonl")]
        if not found:
            return None
        return max(found, key=lambda p: p.stat().st_mtime)
    except OSError:
        return None


def tail_entries(path: os.PathLike | str, max_bytes: int = TAIL_BYTES) -> list:
    """The parsed JSON entries in the last `max_bytes` of a transcript (partial first line dropped)."""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - max_bytes)
        handle.seek(start)
        data = handle.read()
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]
    out = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


DONE_STOPS = ("end_turn", "stop_sequence", "refusal")   # each one ends the turn


def activity(entries: Iterable[dict]) -> dict:
    """What the entries say, in order.

    last_ts        the newest entry timestamp.
    pending_since  the start of the oldest tool call in the LAST assistant message that has
                   no result yet. Only the last message counts, so a call orphaned in an
                   earlier message cannot keep a teammate busy.
    stop_reason    the last assistant message's final stop reason (streamed entries of one
                   message carry None until the last one).
    turn_open      the turn has not ended: the last message did not stop with end_turn,
                   stop_sequence or refusal, or a message (not a tool result) arrived after it.
    """
    last_ts = None
    last_mid = object()
    pending: dict = {}
    stop_reason = None
    woken = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ts = parse_ts(entry.get("timestamp"))
        if ts is not None and (last_ts is None or ts > last_ts):
            last_ts = ts
        kind = entry.get("type")
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        content = message.get("content")
        if kind == "assistant":
            if message.get("id") != last_mid:
                last_mid = message.get("id")
                pending = {}
                stop_reason = None
            woken = False
            if message.get("stop_reason") is not None:
                stop_reason = message.get("stop_reason")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                    pending[block["id"]] = ts
        elif kind == "user":
            items = content if isinstance(content, list) else []
            results = [b for b in items if isinstance(b, dict) and b.get("type") == "tool_result"]
            for block in results:
                pending.pop(block.get("tool_use_id"), None)
            if content and not (items and len(results) == len(items)):
                woken = True
    starts = [t for t in pending.values() if t is not None]
    return {
        "last_ts": last_ts,
        "pending_since": min(starts) if starts else None,
        "stop_reason": stop_reason,
        "turn_open": stop_reason not in DONE_STOPS or woken,
    }


def is_active(act: dict, now: datetime) -> bool:
    """WORKING, the one predicate the alarm and repact-status share: a call from the last
    message still running (started within PENDING_MAX_SECONDS), or a write within
    ACTIVE_WINDOW_SECONDS while the turn is still open. A recent write after the turn
    ended is not work: nothing will wake that teammate."""
    pending = act.get("pending_since")
    if pending is not None and (now - pending).total_seconds() <= PENDING_MAX_SECONDS:
        return True
    last = act.get("last_ts")
    return (bool(act.get("turn_open")) and last is not None
            and (now - last).total_seconds() <= ACTIVE_WINDOW_SECONDS)


def transcript_active(path, now: datetime) -> Optional[bool]:
    """True/False from the transcript's tail, or None when it cannot be read."""
    try:
        return is_active(activity(tail_entries(path)), now)
    except (OSError, ValueError):
        return None
