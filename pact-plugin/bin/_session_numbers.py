#!/usr/bin/env python3
"""_session_numbers: six numbers of one session, from its ledger row or computed now.

Location: pact-plugin/bin/_session_numbers.py. Used by bin/repact-session-numbers,
which wrap-up and pause run. Source: the latest row of the session in
${CLAUDE_PLUGIN_DATA}/sessions.jsonl (hooks/session_ledger.py appends one row at each session
end, from repact-report; a resumed session appends a cumulative row, so the latest row wins).
A row is written only when a session ends, so when the session has no row, or its row is older
than the newest transcript of the session, the row is computed now by the ledger writer's own
build_row (the two cannot differ) and the header says so. `ledger_only` reads the ledger alone.
The numbers are the plugin's own measure (input-side tokens are cache-read weighted), not
billing.

A number whose field is absent, or is not a count, prints "n/a" with the reason; never a
guess and never 0. A computation that fails prints its fixed reason, never the error.
Privacy: six named integer fields and the row's date are selected. No row, subject, project
name, message or other text of the ledger or of a transcript is printed or serialised.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _repact_common as common  # noqa: E402

LEDGER_NAME = "sessions.jsonl"       # written by hooks/session_ledger.py
LEDGER_WRITER = Path(__file__).resolve().parent.parent / "hooks" / "session_ledger.py"
NO_TRANSCRIPT = ", and no transcript of this session to compute from"
NOT_COMPUTED = ", and computing the numbers now failed"
SESSION_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
ROW_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
MAX_COUNT = 10 ** 15                 # a larger value is not a count of calls or tokens
LABEL_WIDTH = 22
EXAMPLE = "repact-session-numbers --session-dir ~/.claude/pact-sessions/<project>/<session id>"

LEAD_CALLS = ("report", "spend", "lead", "calls")
LEAD_INPUT = ("report", "spend", "lead", "input_side")
# (label, field path, unit); the per-call line is derived from the two lead fields.
NUMBERS = (
    ("lead calls", LEAD_CALLS, ""),
    ("per-call lead input", None, " input-side tokens"),
    ("teammate calls", ("report", "spend", "teammate", "calls"), ""),
    ("idle-only cache-read", ("report", "wakes", "idle_only_cache_read"), " tokens"),
    ("rejections", ("report", "rejections", "rejected_tasks"), " tasks"),
    ("check returns", ("report", "gates", "total", "returned"), " items"),
)


def default_ledger() -> Path:
    return common.plugin_data_dir(create=False) / LEDGER_NAME


def session_of(session: Optional[str], session_dir: Optional[str]) -> tuple[Optional[str], str]:
    """(session id, "") or (None, why not). The id is the explicit one, else the session
    directory's name, else the live session's id from the environment."""
    if session is None and session_dir is not None:
        folder = Path(session_dir).expanduser()
        if not folder.is_dir():
            return None, "no such session directory"
        session = folder.name
    if session is None:
        session = next((os.environ[name] for name in SESSION_ENV if os.environ.get(name)), None)
    if session is None:
        return None, "no session id: give --session or --session-dir"
    if not SESSION_ID.fullmatch(session):
        return None, "the session id is not an id"
    return session, ""


def latest_row(ledger: Path, session: str) -> tuple[Optional[dict], str]:
    """(the session's last row, "") or (None, why not)."""
    try:
        handle = open(ledger, encoding="utf-8", errors="replace")
    except OSError:
        return None, "no session ledger at the ledger path"
    found = None
    with handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("session") == session:
                found = row
    if found is None:
        return None, "no ledger row for this session: the row is written when a session ends"
    return found, ""


def row_time(row: dict) -> Optional[float]:
    stamp = row.get("date")
    if not isinstance(stamp, str) or not ROW_DATE.fullmatch(stamp):
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def computed(session: str, row: Optional[dict]) -> tuple[Optional[dict], str]:
    """What to add to the ledger's answer: (a row computed now, ""), or (None, "") when the
    ledger row is as new as the session's transcripts or is all there is, or (None, why
    nothing was computed).
    The lead transcript is found by repact-report's resolver (<config dir>/projects/*/<session
    id>.jsonl); the row is built by the ledger writer's build_row, whose `cwd` only names the
    project, which is not read here. Whatever the computation prints or raises is dropped."""
    try:
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            from shared import transcripts as tr

            lead = tr.find_lead_transcript(session=session)
            if lead is None:
                return None, NO_TRANSCRIPT if row is None else ""
            written = row_time(row) if row is not None else None
            newest = max(int(f["path"].stat().st_mtime) for f in tr.session_files(lead))
            if written is not None and written >= newest:
                return None, ""
            spec = importlib.util.spec_from_file_location("_session_ledger", LEDGER_WRITER)
            writer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(writer)
            live = writer.build_row(str(lead), None)
            return (live, "") if isinstance(live, dict) else (None, NOT_COMPUTED)
    except Exception:
        return None, NOT_COMPUTED


def count(row: dict, path: tuple) -> tuple[Optional[int], str]:
    """(the integer at `path`, "") or (None, why not)."""
    name = ".".join(path[1:])
    value = row
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None, f"the ledger row has no {name}"
        value = value[key]
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < MAX_COUNT:
        return None, f"{name} is not a count in the ledger row"
    return value, ""


def per_call(row: dict) -> tuple[Optional[int], str]:
    calls, why = count(row, LEAD_CALLS)
    if calls is None:
        return None, why
    if calls == 0:
        return None, "the ledger row counts no lead call"
    total, why = count(row, LEAD_INPUT)
    if total is None:
        return None, why
    return round(total / calls), ""


def lines(session: Optional[str], session_dir: Optional[str], ledger: Optional[str],
          ledger_only: bool = False) -> list[str]:
    """One header line, then one line per number."""
    sid, why = session_of(session, session_dir)
    row = live = None
    late = ""
    if sid is not None:
        row, why = latest_row(Path(ledger).expanduser() if ledger else default_ledger(), sid)
        if not ledger_only:
            live, late = computed(sid, row)
    if live is not None:
        row, dated = live, "computed now"
    elif row is None:
        why, dated = why + late, "no ledger row"
    else:
        dated = f"ledger row of {row['date']}" if row_time(row) is not None else "ledger row with no date"
        dated += late
    out = [f"session numbers of {sid or 'an unnamed session'}: {dated} (the plugin's measure, not billing)"]
    for label, path, unit in NUMBERS:
        value, reason = (None, why) if row is None else per_call(row) if path is None else count(row, path)
        shown = f"n/a ({reason})" if value is None else f"{value:,}{unit}"
        out.append(f"{label:<{LABEL_WIDTH}}{shown}")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="repact-session-numbers", description=__doc__.split("\n")[0],
                                     epilog=f"example: {EXAMPLE}")
    parser.add_argument("--session", help="the lead session id (default: the session directory's name)")
    parser.add_argument("--session-dir", help="the session directory (default: the live session's)")
    parser.add_argument("--ledger", help="the session ledger (default: sessions.jsonl in the plugin data dir)")
    parser.add_argument("--ledger-only", action="store_true",
                        help="read the ledger row only; never compute from the session's transcripts")
    args = parser.parse_args(argv)
    if args.session and args.session_dir and Path(args.session_dir).expanduser().name != args.session:
        parser.error("--session and --session-dir name two sessions")
    print("\n".join(lines(args.session, args.session_dir, args.ledger, args.ledger_only)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
