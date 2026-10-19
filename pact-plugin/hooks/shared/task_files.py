"""
Location: pact-plugin/hooks/shared/task_files.py
Summary: Find and read a team's task files and session journals: task_path, load_task,
         resolve_team (the team of a session that holds a task id), team_from_session,
         session_journal and task_from_journal (a stand-in task rebuilt from the journal once
         the task file is gone). Labelling only: the result chooses which file to read and
         grants nothing. Moved out of the evidence package when 2.1.0 replaced it with
         shared/evidence_check.py.
Used by: shared/frame.py, shared/task_log/bridge.py and close_gate.py, bin/repact-task,
         bin/repact-wait and the front door's readers (bin/_front/).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from shared.plugin_paths import claude_config_dir, task_store
from shared.team_labels import teammate_label

PATH_SEGMENT = re.compile(r"(?!\.\.?$)[A-Za-z0-9_.-]{1,128}")   # one plain path segment: a team name or task id


def task_path(team: str, task_id: str) -> Optional[Path]:
    """The task file's path from the store adapter; None for an unsafe segment."""
    from shared.task_store import TaskRef
    return task_store().path(TaskRef(team, task_id))


def load_task(path: Optional[Path]) -> Optional[dict]:
    if path is None:
        return None
    try:
        task = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):   # nested past the parser's depth
        return None
    return task if isinstance(task, dict) else None


def lead_session_id(team: str) -> Optional[str]:
    """The team config's leadSessionId, or None."""
    try:
        path = claude_config_dir() / "teams" / team / "config.json"
        return json.loads(path.read_text(encoding="utf-8")).get("leadSessionId")
    except (OSError, ValueError, AttributeError):
        return None


def session_journal(session_id: str) -> Optional[Path]:
    """The newest session-journal.jsonl of that session, or None."""
    paths = list(claude_config_dir().glob(f"pact-sessions/*/{session_id}/session-journal.jsonl"))
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def team_journals(team: str) -> list[Path]:
    """The session journals of that team, oldest first: the team config's lead session, and every
    session whose own pact-session-context.json names the team. After a resume the config still
    names the old session id while the journal sits under the new one (measured 2026-10-08 on a
    live session), and a config-less team has no config to ask."""
    sessions = []
    lead = lead_session_id(team)
    if isinstance(lead, str) and PATH_SEGMENT.fullmatch(lead):
        sessions.append(lead)
    for context in (claude_config_dir() / "pact-sessions").glob("*/*/pact-session-context.json"):
        try:
            named = json.loads(context.read_text(encoding="utf-8")).get("team_name")
        except (OSError, ValueError, AttributeError):
            continue
        if named == team and context.parent.name not in sessions:
            sessions.append(context.parent.name)
    journals = {journal for journal in map(session_journal, sessions) if journal is not None}
    return sorted(journals, key=lambda path: (path.stat().st_mtime, str(path)))


def _journal_events(journal: Path, task_id: str, kinds: tuple) -> list[dict]:
    """The journal's rows of those kinds for task_id, in file order; damaged lines are skipped."""
    rows = []
    with open(journal, encoding="utf-8") as handle:
        for raw in handle:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") in kinds \
                    and str(event.get("task_id")) == str(task_id):
                rows.append(event)
    return rows


def task_from_journal(team: Optional[str], task_id: str, with_metadata: bool = False) -> Optional[dict]:
    """A stand-in task built from the session journal's latest `agent_handoff` for task_id,
    for when the task file is gone (a finished team's tasks are cleaned up; the journal stays).
    None when the team, its journals (team_journals) or the event is missing.

    with_metadata also folds in the latest `task_metadata_snapshot` of the same task (the
    teachback, rulings and grants; the HANDOFF is never in a snapshot), and then a task with a
    snapshot and no HANDOFF is returned too. A task id is reused across arcs (ids restart after a
    drain), so a snapshot under another subject than the HANDOFF's is a different task and is
    left out, the LATEST task of that id is the one returned, and `arcs` counts the distinct
    subjects seen under the id.
    """
    if not isinstance(team, str) or not PATH_SEGMENT.fullmatch(team):
        return None
    try:
        kinds = ("agent_handoff", "task_metadata_snapshot") if with_metadata else ("agent_handoff",)
        rows = [row for journal in team_journals(team) for row in _journal_events(journal, task_id, kinds)]
    except (OSError, ImportError):
        return None
    handoffs = [row for row in rows if row["type"] == "agent_handoff" and isinstance(row.get("handoff"), dict)]
    latest = handoffs[-1] if handoffs else None
    subject = latest.get("task_subject") if latest else None
    snapshots = [row for row in rows if row["type"] == "task_metadata_snapshot"
                 and isinstance(row.get("metadata"), dict)
                 and (latest is None or row.get("subject") == subject)]
    snapshot = snapshots[-1] if snapshots else None
    if latest is None and snapshot is None:
        return None
    metadata = {key: value for key, value in (snapshot or {}).get("metadata", {}).items() if key != "handoff"}
    if latest is not None:
        metadata["handoff"] = latest["handoff"]
    return {"id": str(task_id), "subject": (latest or {}).get("task_subject") or (snapshot or {}).get("subject") or "",
            "owner": latest.get("agent") if latest else snapshot.get("owner"),
            "metadata": metadata, "source": "journal",
            "arcs": len({str(row.get("task_subject") or row.get("subject")) for row in rows})}


def team_from_session(session_id: Optional[str]) -> Optional[str]:
    """Team named by this session's pact-session-context.json, if one exists."""
    if not session_id:
        return None
    for context in (claude_config_dir() / "pact-sessions").glob(f"*/{session_id}/pact-session-context.json"):
        try:
            team = json.loads(context.read_text(encoding="utf-8")).get("team_name")
        except (OSError, ValueError):
            continue
        if isinstance(team, str) and team:
            return team
    return None


def resolve_team(session_id: Optional[str], task_id: str) -> Optional[str]:
    """The team of this session that holds the task: the labelling registry's team, then the
    session context's, the first whose <task_id>.json exists; else None, 'unknown team'.

    Never another session's team: a task id repeats across teams. The registry can
    hold a phantom team under a lead's session, so the session context is tried
    after it, not only when it names no team.
    """
    if not isinstance(task_id, str) or not PATH_SEGMENT.fullmatch(task_id):
        return None                     # a forged id (`../other-team/5`) names no file of this session's
    for team in (teammate_label(session_id)[1], team_from_session(session_id)):
        path = task_path(team, task_id) if isinstance(team, str) else None
        if path is not None and PATH_SEGMENT.fullmatch(team) and path.exists():
            return team
    return None
