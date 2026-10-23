"""
Location: pact-plugin/tests/test_jsonl_readers_skip_too_deep_lines.py
Summary: A journal line nested too deeply for json to parse is a malformed line
         that drops only itself (upstream PACT #1650). Before, the
         RecursionError emptied the forward reader and lost the reverse scan's
         answer (a pause claim).
Used by: pytest.
"""

import json

from shared.journal_reader import read_events_at
from shared.session_journal import _read_last_event_at

TOO_DEEP = "[" * 100_000 + "]" * 100_000


def _journal(tmp_path, *lines):
    journal = tmp_path / "session-journal.jsonl"
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return journal


def test_both_journal_readers_skip_a_too_deep_line(tmp_path):
    paused = json.dumps({"type": "session_paused", "ts": "2026-10-23T00:00:00Z"})
    journal = _journal(tmp_path, paused, TOO_DEEP)

    assert [event["type"] for event in read_events_at(journal)] == ["session_paused"]
    assert _read_last_event_at(journal, "session_paused")["type"] == "session_paused"
