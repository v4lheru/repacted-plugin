"""The capped compaction-summary head (D8): pointers first, then the summary cut
at a line boundary within 8 KB, the full file's size and path on the last line."""

from __future__ import annotations

import json

from shared import summary_head
from shared.constants import (
    COMPACT_SUMMARY_HEAD_MAX_BYTES,
    COMPACT_SUMMARY_HEAD_NAME,
    COMPACT_SUMMARY_NAME,
)


def _session(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    folder = tmp_path / "pact-sessions" / "proj" / "sid"
    folder.mkdir(parents=True)
    (folder / "pact-session-context.json").write_text(
        json.dumps({"team_name": "session-abc", "project_dir": "", "session_id": "sid"}))
    tasks = tmp_path / "tasks" / "session-abc"
    tasks.mkdir(parents=True)
    (tasks / "7.json").write_text(json.dumps({
        "id": "7", "subject": "build the cap\nforged line", "status": "in_progress", "owner": "d8",
        "metadata": {"handoff": {"rev": 2, "amendments": {"amend_1": "ack"}},
                     "amend_1": "first", "amend_2": "second"}}))
    (tasks / "8.json").write_text(json.dumps({"id": "8", "subject": "done", "status": "completed"}))
    return folder


def test_a_40_kb_summary_gets_an_8_kb_head_with_pointers_first_and_the_cut_line_last(tmp_path, monkeypatch):
    folder = _session(tmp_path, monkeypatch)
    summary = "".join(f"line {n}: " + "x" * 70 + "\n" for n in range(500))
    assert len(summary.encode()) > 40_000
    (folder / COMPACT_SUMMARY_NAME).write_text(summary)

    assert summary_head.write_head(folder, summary)

    head = (folder / COMPACT_SUMMARY_HEAD_NAME).read_text()
    lines = head.splitlines()
    assert len(head.encode()) <= COMPACT_SUMMARY_HEAD_MAX_BYTES
    assert lines[0].startswith("## Pointers rebuilt from disk")
    assert "- #7 in_progress owner=d8 handoff rev 2 undisposed amend_2 | build the cap forged line" in lines
    assert not any("#8" in line for line in lines)
    body = head.split("## Summary\n", 1)[1].rsplit("[Summary cut", 1)[0]
    assert summary.startswith(body) and body.endswith("\n")
    assert lines[-1] == (
        f"[Summary cut at a line boundary: this head shows {len(body.encode())} of "
        f"{len(summary.encode())} bytes. Full text: {folder / COMPACT_SUMMARY_NAME} "
        f"(once processed, archived beside it as compact-summary-<timestamp>.txt).]"
    )
    assert (folder / COMPACT_SUMMARY_NAME).read_text() == summary


def test_a_short_summary_passes_through_whole(tmp_path, monkeypatch):
    folder = _session(tmp_path, monkeypatch)
    summary = "Summary:\n1. Primary request\n   - the cap\n"

    assert summary_head.write_head(folder, summary)

    head = (folder / COMPACT_SUMMARY_HEAD_NAME).read_text()
    assert head.startswith("## Pointers rebuilt from disk")
    assert head.endswith("## Summary\n" + summary)
    assert "Summary cut" not in head
