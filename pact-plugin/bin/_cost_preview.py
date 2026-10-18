"""_cost_preview: the one line the lead prints before it dispatches specialists.

Location: pact-plugin/bin/_cost_preview.py. Used by bin/repact-cost --preview N, which
commands/orchestrate.md and commands/comPACT.md run before their first dispatch.

The line names the planned dispatches, their estimated cost and its basis, the session's spend
so far and the session cost cap (shared/cost_cap.py). Every dollar figure is an estimate at
list API prices, not billing (shared/session_spend.py). The per-dispatch estimate is the
median spend of one teammate transcript over the past sessions on this machine (this session
excluded: its teammates may still be running) when there are at least MIN_DISPATCHES of them,
else DEFAULT_DISPATCH_USD, labelled as a default. Past sessions are read from their spend
caches; up to BACKFILL_SESSIONS sessions without one are read now, within a byte budget, and
cached. Nothing of a transcript but token counts and model names is read into a result.

Example:
    repact-cost --preview 3
    Cost preview: 3 dispatches, about $15.00 ($5.00 each: median of 50 past dispatches on this
    machine); this session so far $100.00; no session cap (PACT_SESSION_COST_CAP_USD). ...
"""
from __future__ import annotations

import os
import re
import statistics
from pathlib import Path
from typing import List, Optional, Tuple

from shared import cost_cap, plugin_paths, session_spend

MIN_DISPATCHES = 5
HISTORY_SESSIONS = 40            # newest spend caches read
BACKFILL_SESSIONS = 5            # past sessions without a cache read now
BACKFILL_BUDGET = 256 * 1024 * 1024
# MEASURED 2026-10-17: the median of 109 teammate transcripts of one multi-agent session on the
# author's machine (Opus 5.5 at list prices, cache reads included). Used only with too few
# recorded dispatches on this machine, and the line says so.
DEFAULT_DISPATCH_USD = 4.46
DEFAULT_BASIS = "a default: no recorded dispatches yet on this machine"
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _sessions_root() -> Path:
    return plugin_paths.claude_config_dir() / "pact-sessions"


def _lead_of(session: str) -> Optional[Path]:
    hits = sorted((plugin_paths.claude_config_dir() / "projects").glob(f"*/{session}.jsonl"))
    return hits[0] if hits else None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _done_teammates(entries: dict) -> List[float]:
    return [entry["usd"] for name, entry in entries.items()
            if session_spend.TEAMMATE_FILE.match(name) and entry.get("done") and entry["usd"] > 0]


def past_dispatches(exclude: Optional[str]) -> List[float]:
    """Spend per teammate transcript over this machine's past sessions, newest first."""
    root = _sessions_root()
    try:
        folders = sorted((p for p in root.glob("*/*") if p.is_dir() and p.name != exclude),
                         key=_mtime, reverse=True)
    except OSError:
        return []
    values: List[float] = []
    budget, backfilled = BACKFILL_BUDGET, 0
    for folder in folders[:HISTORY_SESSIONS]:
        cache = folder / session_spend.CACHE_FILE
        entries = session_spend.load_cache(cache)
        if not entries and backfilled < BACKFILL_SESSIONS and budget > 0:
            lead = _lead_of(folder.name) if SESSION_ID.fullmatch(folder.name) else None
            if lead is None:
                continue
            backfilled += 1
            try:
                spend = session_spend.session_spend(lead, cache, budget)
            except session_spend.SpendUnreadable:
                continue
            budget -= spend.read
            entries = session_spend.load_cache(cache)
        values.extend(_done_teammates(entries))
    return values


def per_dispatch(exclude: Optional[str]) -> Tuple[float, str]:
    """(dollars per dispatch, the basis the line prints)."""
    values = past_dispatches(exclude)
    if len(values) >= MIN_DISPATCHES:
        return statistics.median(values), f"median of {len(values)} past dispatches on this machine"
    if values:
        return DEFAULT_DISPATCH_USD, (f"a default: only {len(values)} recorded dispatches on this "
                                      f"machine, fewer than {MIN_DISPATCHES}")
    return DEFAULT_DISPATCH_USD, DEFAULT_BASIS


def current(session: Optional[str], session_dir: Optional[str]
            ) -> Tuple[Optional[session_spend.Spend], str]:
    """(this session's spend, "") or (None, why not)."""
    if not session or not SESSION_ID.fullmatch(session):
        return None, "no session id"
    lead = _lead_of(session)
    if lead is None:
        return None, "no transcript of this session"
    if session_dir is None:
        found = [p for p in _sessions_root().glob(f"*/{session}") if p.is_dir()]
        session_dir = str(max(found, key=_mtime)) if found else None
    cache = Path(session_dir) / session_spend.CACHE_FILE if session_dir else None
    try:
        return session_spend.session_spend(lead, cache, 4 * session_spend.DEFAULT_BYTE_BUDGET), ""
    except session_spend.SpendUnreadable as error:
        return None, str(error)


def preview_line(dispatches: int, session: Optional[str], session_dir: Optional[str],
                 cwd: Optional[str] = None) -> str:
    each, basis = per_dispatch(session)
    planned = each * dispatches
    noun = "dispatch" if dispatches == 1 else "dispatches"
    parts = [f"Cost preview: {dispatches} {noun}, about {_money(planned)} "
             f"({_money(each)} each: {basis})"]
    spend, why = current(session, session_dir)
    if spend is None:
        parts.append(f"this session so far: unknown ({why})")
    else:
        bound = "at least " if spend.partial else ""
        parts.append(f"this session so far {bound}{_money(spend.usd)}")
    cap, source, problem = cost_cap.resolve_cap(cwd)
    if cap is None:
        parts.append(f"no session cap ({cost_cap.CAP_OPTION}"
                     + (f": {problem}" if problem else "") + ")")
    elif spend is None:
        parts.append(f"cap {_money(cap)} set by {source}")
    else:
        left = cap - spend.usd
        cap_text = f"cap {_money(cap)} set by {source}, {_money(max(left, 0.0))} left"
        if planned > left:
            cap_text += "; these dispatches would pass it"
        parts.append(cap_text)
    return "; ".join(parts) + f". The figures are an {session_spend.LABEL}."


def session_from_env() -> Optional[str]:
    return next((os.environ[name] for name in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")
                 if os.environ.get(name)), None)
