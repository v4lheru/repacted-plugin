"""
Location: pact-plugin/hooks/shared/hook_latency.py
Summary: How long each hook run took. The dispatcher appends one line per run to
    <session dir>/hook-latency.ndjson; this module writes that line, reads the lines back and
    sums them into p50, p95 and max per event, per check and overall.
Used by: hooks/dispatch.py (record, after its result is printed and its rows are written),
    shared/transcripts.py (summarise and render, for bin/repact-report) and
    tests/perf/hook_latency.py (read_rows and percentile, for the CI budget check).

A line: {"v": 1, "ts": "<UTC>", "event": "PreToolUse", "group": "Bash", "total_ms": 41.2,
"checks": {"git_commit_check": 3.1, ...}}. total_ms is the dispatcher's own wall time, from
its first statement (dispatch.STARTED) to just before the line is written: imports, every
check, combining, printing, the journal and cost rows. The interpreter's start before that
first statement and the write itself fall outside it; tests/perf/hook_latency.py measures
the whole process from outside. A check's ms is its handler, the time its timeout measures.
Numbers, event, group and check names only: never a payload, command or path.

Cost: one stat and one O_APPEND write of a line under LINE_LIMIT bytes. No lock (an append
this small lands whole), no read, no JSON load. Past MAX_BYTES the file moves to
hook-latency.ndjson.1 (one older file is kept), so a session holds at most about twice
MAX_BYTES. A record that fails writes nothing and raises nothing.

The suffix is .ndjson, not .jsonl, on purpose: readers of a session dir glob *.jsonl as
journal files, and these lines are not journal rows.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Iterable, Optional

LATENCY_FILE = "hook-latency.ndjson"
MAX_BYTES = 4 * 1024 * 1024
LINE_LIMIT = 4000
NAME_LIMIT = 64
TOP_CHECKS = 5


def _ms(seconds: float) -> float:
    return round(max(seconds, 0.0) * 1000.0, 2)


def _name(value) -> str:
    return value[:NAME_LIMIT] if isinstance(value, str) else ""


def line_for(event: str, group: Optional[str], total_s: float, checks: Iterable) -> bytes:
    """The JSON line for one run; the per-check map is dropped when it would pass LINE_LIMIT."""
    row = {"v": 1, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "event": _name(event), "group": _name(group or ""), "total_ms": _ms(total_s),
           "checks": {_name(name): _ms(seconds) for name, seconds in checks}}
    data = (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > LINE_LIMIT:
        row["checks"] = {}
        data = (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
    return data


def session_dir_of(payload: dict) -> str:
    """The run's existing session dir, else "" (a dir is never created here)."""
    session_id = payload.get("session_id")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not (isinstance(session_id, str) and session_id and project_dir):
        return ""
    from .pact_context import reconstruct_session_dir

    folder = reconstruct_session_dir(project_dir, session_id)
    return folder if folder and os.path.isdir(folder) else ""


def append(folder: str, data: bytes) -> bool:
    """Append one line to folder's latency file, moving a full file aside first."""
    path = os.path.join(folder, LATENCY_FILE)
    try:
        if os.stat(path).st_size > MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return True


def record(payload: dict, event: str, group: Optional[str], total_s: float, checks: list) -> bool:
    """Write one run's line to its session dir; False when there is none or the write fails."""
    try:
        folder = session_dir_of(payload)
        return bool(folder) and append(folder, line_for(event, group, total_s, checks))
    except Exception:  # noqa: BLE001 - a time that cannot be written changes no decision
        return False


# --- reading -------------------------------------------------------------------------

def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def read_rows(folder: Path) -> list:
    """The well-formed lines of a session dir's latency files, older file first."""
    rows = []
    for name in (LATENCY_FILE + ".1", LATENCY_FILE):
        try:
            text = (Path(folder) / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and _number(row.get("total_ms")) is not None:
                rows.append(row)
    return rows


def percentile(values: list, q: float) -> float:
    """Nearest-rank percentile (q in 0..100) of a non-empty list."""
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def stats(values: list) -> dict:
    return {"runs": len(values), "p50_ms": percentile(values, 50), "p95_ms": percentile(values, 95),
            "max_ms": max(values)}


def summarise(folders: Iterable[Path]) -> dict:
    """p50, p95 and max of total_ms overall and per event, and of each check's ms."""
    overall, events, checks = [], {}, {}
    for folder in folders:
        for row in read_rows(folder):
            total = _number(row["total_ms"])
            overall.append(total)
            events.setdefault(_name(row.get("event")) or "?", []).append(total)
            timed = row.get("checks") if isinstance(row.get("checks"), dict) else {}
            for name, value in timed.items():
                if _number(value) is not None:
                    checks.setdefault(_name(name), []).append(_number(value))
    if not overall:
        return {"runs": 0}
    return {"runs": len(overall), "overall": stats(overall),
            "events": {name: stats(values) for name, values in sorted(events.items())},
            "checks": {name: stats(values) for name, values in sorted(checks.items())}}


def render(summary: dict) -> list:
    """The report lines: overall, each event, and the TOP_CHECKS slowest checks by p95."""
    if not summary.get("runs"):
        return []
    o = summary["overall"]
    lines = [f"hook latency         {o['runs']} runs: p50 {o['p50_ms']:.1f} ms, p95 {o['p95_ms']:.1f} ms, "
             f"max {o['max_ms']:.1f} ms (dispatcher wall, interpreter start excluded)"]
    for name, s in summary["events"].items():
        lines.append(f"  {name:<28}{s['runs']:>6} runs  p50 {s['p50_ms']:>7.1f}  p95 {s['p95_ms']:>7.1f} ms")
    slowest = sorted(summary["checks"].items(), key=lambda kv: (-kv[1]["p95_ms"], kv[0]))[:TOP_CHECKS]
    for name, s in slowest:
        lines.append(f"  check {name:<22}{s['runs']:>6} runs  p50 {s['p50_ms']:>7.1f}  p95 {s['p95_ms']:>7.1f} ms")
    return lines
