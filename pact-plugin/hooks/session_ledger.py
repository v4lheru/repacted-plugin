#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_ledger.py
Summary: SessionEnd hook. Appends ONE row per session end to ${CLAUDE_PLUGIN_DATA}/sessions.jsonl:
         {date, session, project, team, report, open_user_actions}. `report` is
         repact-report's JSON for the session (hooks/shared/transcripts.py); `open_user_actions`
         is shared/user_actions.py's list (id and subject). This is the longitudinal
         record: what we did last week, trends per failure class, and the "re-measure after
         two more sessions" numbers.
         READERS TAKE THE LATEST ROW PER SESSION: a resumed session ends again and appends
         a second, cumulative row for the same session id.
         An in-process teammate's SessionEnd (its transcript sits under `subagents/`)
         writes nothing: the lead's row already covers the session.
Used by: hooks.json SessionEnd entry.

Detached: the hook starts a child (`session_ledger.py --row <transcript> <cwd>`) in its own
session and exits at once, so reading the session's transcripts never delays the exit.
Fail-open: any error writes nothing and exits 0.
D10: after the row, the child runs the project task log's sync once (hooks/shared/task_log/bridge.py), so a lead
acceptance the PostToolUse bridge missed is recorded at session end.
Privacy: counts, task ids and task subjects only. No transcript text.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LEDGER_NAME = "sessions.jsonl"
_SUPPRESS = json.dumps({"suppressOutput": True})


def is_teammate_transcript(transcript: str) -> bool:
    return Path(transcript).parent.name == "subagents"


def open_user_actions(tasks: list) -> list:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from shared.user_actions import open_user_actions as shared_open_user_actions

    return shared_open_user_actions(tasks)


def build_row(transcript: str, cwd: str | None) -> dict:
    from shared import transcripts as tr

    lead = Path(transcript)
    report = tr.build_report(lead)
    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session": lead.stem,
        "project": Path(cwd).name if cwd else None,
        "team": report.get("team"),
        "report": report,
        "open_user_actions": open_user_actions(tr.team_tasks(report.get("team"))),
    }


def append_row(row: dict) -> Path:
    from shared import plugin_paths as common

    path = common.plugin_data_dir() / LEDGER_NAME
    data = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


def sync_project_tasks(cwd: str | None) -> None:
    """D10: record any lead acceptance the hook missed in the project's task log (repact-task
    sync). One call, in this already-detached child. Never raises."""
    try:
        if cwd:
            from shared.task_log import bridge

            bridge.sync_quietly(cwd)
    except Exception:
        pass


def write_row(transcript: str, cwd: str | None) -> None:
    """The child's work. Never raises."""
    try:
        if Path(transcript).is_file() and not is_teammate_transcript(transcript):
            append_row(build_row(transcript, cwd))
            sync_project_tasks(cwd)
    except Exception:
        pass


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--row":
        write_row(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        return
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        transcript = payload.get("transcript_path") if isinstance(payload, dict) else None
        if isinstance(transcript, str) and transcript and not is_teammate_transcript(transcript):
            cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--row", transcript, cwd],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True,
            )
    except Exception:
        pass
    print(_SUPPRESS)


if __name__ == "__main__":
    main()
    sys.exit(0)
