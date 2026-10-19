"""
Location: pact-plugin/hooks/shared/autopilot_run.py
Summary: The run log of /repacted:autopilot: <session dir>/autopilot.jsonl, one JSON row per
         line, appended and never rewritten. A `start` row holds the run's limits, a `begin` row
         is the checkpoint before each task, a `record` row an outcome, a `stop` row the reason
         the loop ended, and an `evidence_hold` row (written by hooks/shared/task_log/bridge.py)
         a task accepted with no evidence while unattended, whose row stays open (never logged for
         a row already closed). hooks/shared/task_log/close_gate.py writes a `close_waiver` row
         for an attended waiver and a `park` row when unattended a waiver parked the row for a
         human. The rows after the last `start` are the current run, so a fresh
         lead context needs no memory of the command's arguments.
Used by: bin/repact-autopilot (every verb) and hooks/shared/workflow_state.py (render: the one
         "Autopilot:" line of the resume screen, so a compaction picks the loop up).

Every reader is fail-open: an unreadable or partly corrupt file is read as the rows it holds.

Example:
    append(session_dir, {"kind": "begin", "run": run["id"], "ref": "T-0123abcd"})
    print(run_line(session_dir))
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

RUN_FILE = "autopilot.jsonl"
OUTCOMES = ("done", "halted", "failed")
MAX_ROW_BYTES = 4096


def now() -> datetime:
    """The current time, tz-aware. REPACT_AUTOPILOT_NOW (tests only) pins it."""
    pinned = os.environ.get("REPACT_AUTOPILOT_NOW", "")
    if pinned:
        return datetime.fromisoformat(pinned).astimezone()
    return datetime.now().astimezone()


def stamp(moment: Optional[datetime] = None) -> str:
    return (moment or now()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def until_at(hhmm: str, moment: datetime) -> datetime:
    """The next local HH:MM after `moment`: an overnight run started at 22:00 with 07:00 ends
    the next morning. Raises ValueError on anything but HH:MM."""
    hour, _, minute = hhmm.partition(":")
    if not (len(hour) == 2 and len(minute) == 2 and hour.isdigit() and minute.isdigit()):
        raise ValueError("--until takes HH:MM")
    target = moment.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    return target if target > moment else target + timedelta(days=1)


def read_rows(session_dir: str) -> List[dict]:
    """Every well-formed row, oldest first; a corrupt line is skipped."""
    try:
        text = (Path(session_dir) / RUN_FILE).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("kind"), str):
            rows.append(row)
    return rows


def append(session_dir: str, row: dict) -> bool:
    """One O_APPEND write of one line; False when it could not be written."""
    line = json.dumps({"at": stamp(), **row}, sort_keys=True) + "\n"
    data = line.encode("utf-8")
    if len(data) > MAX_ROW_BYTES:
        return False
    try:
        fd = os.open(str(Path(session_dir) / RUN_FILE), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except OSError:
        return False
    return True


def current_run(rows: List[dict]) -> Optional[dict]:
    """{start, begun, records, stop} of the rows after the last start, or None with no start."""
    starts = [index for index, row in enumerate(rows) if row["kind"] == "start"]
    if not starts:
        return None
    start = rows[starts[-1]]
    tail = [row for row in rows[starts[-1] + 1:] if row.get("run") == start.get("id")]
    stops = [row for row in tail if row["kind"] == "stop"]
    return {"start": start, "begun": [row["ref"] for row in tail if row["kind"] == "begin"],
            "records": [row for row in tail if row["kind"] == "record"],
            "stop": stops[0] if stops else None}


def in_flight(run: dict) -> Optional[str]:
    """The last task begun in the run and not yet recorded, else None."""
    recorded = {row.get("ref") for row in run["records"]}
    open_refs = [ref for ref in run["begun"] if ref not in recorded]
    return open_refs[-1] if open_refs else None


def evidence_holds(rows: List[dict]) -> dict:
    """{ref: why} for the refs of the current run whose acceptance found no evidence while
    unattended (task_log/bridge.hold): `record done` leaves them open with that reason."""
    run = current_run(rows)
    if run is None:
        return {}
    run_id = run["start"].get("id")
    return {row["ref"]: str(row.get("why") or "no evidence") for row in rows
            if row["kind"] == "evidence_hold" and row.get("run") == run_id and isinstance(row.get("ref"), str)}


def held_refs(rows: List[dict]) -> set:
    """Refs this session recorded halted or failed and never recorded done afterwards."""
    held = set()
    for row in rows:
        if row["kind"] == "record":
            (held.discard if row.get("outcome") == "done" else held.add)(row.get("ref"))
    return held


def spend_usd(session_dir: str) -> Tuple[Optional[float], str]:
    """(the session's estimated spend in dollars, a note) or (None, why it cannot be read).
    The session id is the session dir's name; the read keeps its place in the cost cap's cache."""
    from . import cost_cap, session_spend

    lead, why = cost_cap.lead_transcript({"session_id": Path(session_dir).name})
    if lead is None:
        return None, why
    try:
        spend = session_spend.session_spend(lead, Path(session_dir) / session_spend.CACHE_FILE)
    except session_spend.SpendUnreadable as error:
        return None, str(error)
    return round(spend.usd, 4), ("a lower bound: part of the transcripts was not read"
                                 if spend.partial else "")


def run_line(session_dir: str) -> Optional[str]:
    """The resume screen's line for an active run, or None (no run, a stopped one, any error)."""
    try:
        run = current_run(read_rows(session_dir))
        if run is None or run["stop"] is not None:
            return None
        outcomes = [row.get("outcome") for row in run["records"]]
        flight = in_flight(run)
        doing = f"task {flight} in progress" if flight else "between tasks"
        return (f"Autopilot: run {run['start'].get('id')} active since {run['start'].get('at')}; "
                f"{outcomes.count('done')} done, {outcomes.count('halted')} halted; {doing}. "
                "Continue the loop: /repacted:autopilot (it resumes the run).")
    except Exception:  # noqa: BLE001 - the screen never fails on this line
        return None
