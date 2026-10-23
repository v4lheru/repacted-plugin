"""The session journal's read side: path, timestamp parsing and the event readers.

Location: pact-plugin/hooks/shared/journal_reader.py. A leaf (stdlib only), split out of
session_journal.py so pact_context can read a journal without importing session_journal, which
imports pact_context for the implicit session dir (an import cycle otherwise).
Used by: session_journal (binds these under its historical names and re-exports read_events_from)
and pact_context (the resume census and resume-timestamp witnesses).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def journal_path_from(session_dir: str) -> Path:
    """Compute the journal file path from an explicit session directory."""
    return Path(session_dir) / "session-journal.jsonl"


def normalize_trailing_z(value: Any) -> str:
    """Rewrite a SINGLE trailing `Z` UTC designator to `+00:00`, leaving any
    interior `Z` intact.
    """
    s = str(value)
    return s[:-1] + "+00:00" if s.endswith("Z") else s


def parse_ts(value: Any) -> datetime:
    """Parse an ISO-8601 timestamp, treating a trailing `Z` as `+00:00`.

    Never compare these as strings: '+' sorts before 'Z'. Raises on bad input.
    """
    return datetime.fromisoformat(normalize_trailing_z(value))


def ts_ge(event_ts: Any, since: str | None) -> bool:
    """Return True if `event_ts >= since` as parsed datetimes; a missing or bad ts is included."""
    if not since:
        return True
    try:
        return parse_ts(event_ts) >= parse_ts(since)
    except (ValueError, TypeError):
        return True


def read_events_at(
    journal: Path,
    event_type: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Shared read for both APIs: one bad line (bytes, JSON, or a non-dict) drops only itself.

    `since` keeps events with ts >= since (parsed).
    """
    try:
        if not journal.exists():
            return []

        events: list[dict[str, Any]] = []
        for line in journal.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                # A non-dict JSON line would raise AttributeError and drop the whole file; skip it.
                if not isinstance(event, dict):
                    continue
                if event_type and event.get("type") != event_type:
                    continue
                if not ts_ge(event.get("ts"), since):
                    continue
                events.append(event)
            except (json.JSONDecodeError, ValueError, RecursionError):
                continue  # Skip malformed lines, one nested too deep to parse included
        return events

    except Exception:
        return []


def read_events_from(
    session_dir: str,
    event_type: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read events from a specific session's journal (explicit path).
    """
    if not session_dir:
        # Warn before the silent empty result for an unset session_dir.
        print(
            "session_journal: read_events_from called with empty session_dir",
            file=sys.stderr,
        )
        return []
    journal = journal_path_from(session_dir)
    return read_events_at(journal, event_type, since)
