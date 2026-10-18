"""_report_since: counts across every session in a time window, for `repact-report --since`.

Location: pact-plugin/bin/_report_since.py. Used by bin/repact-report (--since) and
tests/test_report_since.py. Sources, read-only:
  - each session journal, <config dir>/pact-sessions/<project>/<session>/session-journal.jsonl
    (hooks/shared/session_journal.py): refusals by rule id, dispatches, handoffs, teachbacks,
    tasks accepted and rejected, compactions;
  - the session ledger, sessions.jsonl in the plugin data dir (hooks/session_ledger.py): the
    numbers bin/_session_numbers.py prints per session, summed over the latest row of each
    session that ended in the window.
The JSON schema (SCHEMA below) is documented in reference/since-counts.md; change both together.

Privacy: counts only. A rule id is printed only when it has the shape of one (RULE_ID); any
other value counts under UNRECOGNISED. No prompt, command, subject, task id, teammate name,
file content or path is read into the result; the only names are project directory names.
An unreadable journal or ledger, a malformed line and an event with no usable time are
skipped and counted under `skipped`, never guessed.

Example:
    report = build(since_window("7d"), until=None, projects=None)
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _session_numbers as numbers  # noqa: E402
from shared import plugin_paths  # noqa: E402  (_session_numbers put hooks/ on the path)
from shared.transcript_activity import parse_ts  # noqa: E402

SCHEMA = "repact-report.since/1"
JOURNAL = "session-journal.jsonl"
DURATION = re.compile(r"(\d{1,6})([mhdw])")
UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
RULE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,79}")
UNRECOGNISED = "(unrecognised)"
REJECTION_KEYS = ("handoff_rejection", "teachback_rejection")
# The six numbers of bin/_session_numbers.py, summed: (JSON key, field path in a ledger row).
LEDGER_SUMS = (
    ("lead_calls", numbers.LEAD_CALLS),
    ("lead_input_side", numbers.LEAD_INPUT),
    ("teammate_calls", ("report", "spend", "teammate", "calls")),
    ("idle_only_cache_read", ("report", "wakes", "idle_only_cache_read")),
    ("rejected_tasks", ("report", "rejections", "rejected_tasks")),
    ("check_returns", ("report", "gates", "total", "returned")),
)


def since_window(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """`7d`, `12h`, `30m`, `2w` back from now, or an ISO date or time (UTC when it names no
    zone); None when it is neither."""
    if not isinstance(text, str):
        return None
    match = DURATION.fullmatch(text.strip())
    if match:
        return (now or datetime.now(timezone.utc)) - timedelta(**{UNITS[match.group(2)]: int(match.group(1))})
    return parse_ts(text.strip())


def journals(projects: Optional[Iterable[str]]) -> list[tuple[str, Path]]:
    """[(project name, journal path)] under pact-sessions, for the named projects or all."""
    root = plugin_paths.claude_config_dir() / "pact-sessions"
    wanted = set(projects) if projects is not None else None
    found = []
    try:
        for folder in sorted(root.iterdir()) if root.is_dir() else []:
            if folder.is_dir() and (wanted is None or folder.name in wanted):
                found.extend((folder.name, session / JOURNAL) for session in sorted(folder.iterdir())
                             if session.is_dir() and (session / JOURNAL).exists())
    except OSError:
        pass                     # an unlistable folder holds nothing countable
    return found


def _empty() -> dict:
    return {
        "sessions": 0,
        "projects": Counter(),
        "refusals": Counter(),
        "dispatches": {"allowed": 0, "denied": 0},
        "handoffs": 0,
        "teachbacks": 0,
        "tasks": {"accepted": 0, "handoff_rejected": 0, "teachback_rejected": 0},
        "compactions": 0,
        "skipped": {"unreadable_journals": 0, "malformed_journals": 0, "malformed_lines": 0,
                    "undated_events": 0, "unreadable_ledger": 0, "malformed_ledger_lines": 0},
    }


def _count_journal(totals: dict, project: str, path: Path, since: datetime,
                   until: Optional[datetime]) -> None:
    """Add one journal's in-window events to `totals`. Events are written with ts = now, so a
    journal last modified before `since` holds none and is not opened."""
    skipped = totals["skipped"]
    try:
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < since:
            return
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        skipped["unreadable_journals"] += 1
        return
    events = bad = good = 0
    accepted, rejected = set(), {key: set() for key in REJECTION_KEYS}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if not isinstance(event, dict):
            bad += 1
            continue
        good += 1
        when = parse_ts(event.get("ts"))
        if when is None:
            skipped["undated_events"] += 1
            continue
        if when < since or (until is not None and when > until):
            continue
        events += 1
        kind = event.get("type")
        if kind == "gate_refusal":
            rule = event.get("rule")
            totals["refusals"][rule if isinstance(rule, str) and RULE_ID.fullmatch(rule) else UNRECOGNISED] += 1
        elif kind == "dispatch_decision":
            totals["dispatches"]["allowed" if event.get("decision") == "ALLOW" else "denied"] += 1
        elif kind == "agent_handoff":
            totals["handoffs"] += 1
        elif kind == "metadata_write" and event.get("key") == "teachback_submit":
            totals["teachbacks"] += 1
        elif kind == "compaction_attributed":
            totals["compactions"] += 1
        elif kind == "task_metadata_snapshot":
            # Journalled when a task completes (hooks/shared/task_metadata_snapshot.py); the
            # occupant tells two tasks that reused one id apart. Only keys are read.
            task = (str(event.get("task_id")), str(event.get("occupant")))
            accepted.add(task)
            meta = event.get("metadata")
            for key in REJECTION_KEYS:
                if isinstance(meta, dict) and meta.get(key):
                    rejected[key].add(task)
    skipped["malformed_lines"] += bad
    if bad and not good:
        skipped["malformed_journals"] += 1
    if events:
        totals["sessions"] += 1
        totals["projects"][project] += 1
    totals["tasks"]["accepted"] += len(accepted)
    totals["tasks"]["handoff_rejected"] += len(rejected["handoff_rejection"])
    totals["tasks"]["teachback_rejected"] += len(rejected["teachback_rejection"])


def ledger_sums(ledger: Path, since: datetime, until: Optional[datetime],
                projects: Optional[Iterable[str]], skipped: dict) -> dict:
    """The latest ledger row of each session that ended in the window, summed per number. A
    row whose field is absent or not a count adds to `missing`, never 0."""
    wanted = set(projects) if projects is not None else None
    latest: dict = {}
    try:
        handle = open(ledger, encoding="utf-8", errors="replace")
    except OSError:
        skipped["unreadable_ledger"] += 1
        handle = None
    if handle is not None:
        with handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    row = None
                if not isinstance(row, dict) or not isinstance(row.get("session"), str):
                    skipped["malformed_ledger_lines"] += 1
                    continue
                latest[row["session"]] = row
    sums = {key: 0 for key, _ in LEDGER_SUMS}
    missing: Counter = Counter()
    rows = 0
    for row in latest.values():
        stamp = numbers.row_time(row)
        if stamp is None:
            continue
        ended = datetime.fromtimestamp(stamp, timezone.utc)
        if ended < since or (until is not None and ended > until):
            continue
        if wanted is not None and row.get("project") not in wanted:
            continue
        rows += 1
        for key, path in LEDGER_SUMS:
            value, _ = numbers.count(row, path)
            if value is None:
                missing[key] += 1
            else:
                sums[key] += value
    calls = sums["lead_calls"]
    per_call = round(sums["lead_input_side"] / calls) if calls and not missing["lead_calls"] \
        and not missing["lead_input_side"] else None
    return {"sessions": rows, **sums, "per_call_lead_input": per_call, "missing": dict(sorted(missing.items()))}


def build(since: datetime, until: Optional[datetime] = None, projects: Optional[Iterable[str]] = None,
          ledger: Optional[Path] = None, now: Optional[datetime] = None) -> dict:
    """The --since report as a JSON-ready dict (schema SCHEMA, reference/since-counts.md)."""
    projects = list(projects) if projects is not None else None
    totals = _empty()
    for project, path in journals(projects):
        _count_journal(totals, project, path, since, until)
    sums = ledger_sums(ledger or numbers.default_ledger(), since, until, projects, totals["skipped"])
    refusals = totals["refusals"]
    return {
        "schema": SCHEMA,
        "window": {"since": since.isoformat(), "until": (until or now or datetime.now(timezone.utc)).isoformat()},
        "scope": {"projects": sorted(projects) if projects is not None else "all"},
        "sessions": totals["sessions"],
        "sessions_by_project": dict(sorted(totals["projects"].items())),
        "refusals": {"total": sum(refusals.values()),
                     "by_rule": dict(sorted(refusals.items(), key=lambda kv: (-kv[1], kv[0])))},
        "dispatches": totals["dispatches"],
        "teammates_dispatched": totals["dispatches"]["allowed"],
        "teachbacks": totals["teachbacks"],
        "handoffs": totals["handoffs"],
        "tasks": totals["tasks"],
        "compactions": totals["compactions"],
        "ledger": sums,
        "skipped": totals["skipped"],
    }


def render(report: dict) -> list[str]:
    """About 12 lines of text for the same dict."""
    w, t, d, r, led = report["window"], report["tasks"], report["dispatches"], report["refusals"], report["ledger"]
    scope, by_project = report["scope"]["projects"], report["sessions_by_project"]
    busiest = sorted(by_project.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    lines = [f"since {w['since']} until {w['until']}  projects: {'all' if scope == 'all' else ', '.join(scope) or 'none'}",
             f"sessions             {report['sessions']}"
             + (f" in {len(by_project)} projects (" + ", ".join(f"{p} {n}" for p, n in busiest)
                + (", ..." if len(by_project) > 5 else "") + ")" if len(by_project) > 1 else ""),
             f"refusals             {r['total']}"]
    lines += [f"  {rule:<32}{n:>6}" for rule, n in list(r["by_rule"].items())[:10]]
    if len(r["by_rule"]) > 10:
        lines.append(f"  ... {len(r['by_rule']) - 10} more rule ids (--json lists all)")
    lines += [f"teammates dispatched {report['teammates_dispatched']} ({d['denied']} dispatches denied)",
              f"teachbacks, handoffs {report['teachbacks']} teachbacks, {report['handoffs']} handoffs",
              f"tasks                {t['accepted']} accepted; rejected: {t['handoff_rejected']} handoff, "
              f"{t['teachback_rejected']} teachback",
              f"compactions          {report['compactions']}",
              f"session ledger       {led['sessions']} ended: lead calls {led['lead_calls']:,}"
              + (f" ({led['per_call_lead_input']:,} input-side per call)" if led["per_call_lead_input"] is not None else "")
              + f", teammate calls {led['teammate_calls']:,}, idle-only cache-read {led['idle_only_cache_read']:,}, "
              f"rejected tasks {led['rejected_tasks']:,}, check returns {led['check_returns']:,}"
              + (f"; {sum(led['missing'].values())} fields missing" if led["missing"] else "")]
    skipped = {k: v for k, v in report["skipped"].items() if v}
    if skipped:
        lines.append("skipped              " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in skipped.items()))
    return lines
