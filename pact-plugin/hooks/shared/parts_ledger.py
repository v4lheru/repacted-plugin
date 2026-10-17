"""The session-start byte ledger: what each part of the SessionStart context costs.

Location: pact-plugin/hooks/shared/parts_ledger.py. session_init.py builds its
`additionalContext` from a list of parts that several builders append to or insert into. This
module records the bytes per named part and writes them to the session's `context-cost.json`
(hooks/dispatch.py's counter file) under `session_init`, so `repact-report` can print the top
parts and the caps in session_resume.py are sized on a measurement, not a guess. No behaviour
change: a ledger that cannot be written changes nothing the model sees.

Two ways to name bytes:
  note(name, text)        a builder names the part it is about to append (the dynamic parts).
  account(name, parts)    main() names everything the list gained since the last account that
                          no note claimed (a builder's unnamed appends or inserts).
record(...) then writes {source, frame, context, system, parts} where `context` is the byte
length of the joined additionalContext and `parts` the named bytes (the join separators and any
part nobody named show as the difference).
"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER_KEY = "session_init"
COST_FILE = "context-cost.json"

_parts: dict[str, int] = {}
_seen = 0          # bytes of the parts list at the last account()
_noted_since = 0   # bytes note() named since the last account()


def _size(text) -> int:
    return len(text.encode("utf-8", "replace")) if isinstance(text, str) else 0


def _total(parts) -> int:
    return sum(_size(part) for part in parts) if isinstance(parts, (list, tuple)) else 0


def reset() -> None:
    global _seen, _noted_since
    _parts.clear()
    _seen = _noted_since = 0


def note(name: str, text) -> None:
    """Name the bytes of one part a builder appends."""
    global _noted_since
    size = _size(text)
    if size:
        _parts[name] = _parts.get(name, 0) + size
        _noted_since += size


def account(name: str, parts) -> None:
    """Name what the list gained since the last account, less what note() already named."""
    global _seen, _noted_since
    now = _total(parts)
    delta = now - _seen - _noted_since
    if delta > 0:
        _parts[name] = _parts.get(name, 0) + delta
    _seen, _noted_since = now, 0


def ledger() -> dict[str, int]:
    return dict(_parts)


def merge(text: str, entry: dict) -> tuple:
    """state_file.locked_update's apply: the counter file with the session_init entry replaced
    (the last start wins; the per-check tables are untouched)."""
    try:
        counters = json.loads(text) if text.strip() else {}
    except ValueError:
        counters = {}
    if not isinstance(counters, dict):
        counters = {}
    counters[LEDGER_KEY] = entry
    counters.setdefault("v", 1)
    return json.dumps(counters, sort_keys=True), True, None


def record(session_dir: str, source: str, frame: str, context_parts, system_messages) -> None:
    """Write the ledger for this start to the session's counter file. Never raises."""
    try:
        if not session_dir or not Path(session_dir).is_dir():
            return
        joined = " | ".join(p for p in context_parts if isinstance(p, str)) if context_parts else ""
        system = " | ".join(m for m in system_messages if isinstance(m, str)) if system_messages else ""
        entry = {"source": source, "frame": frame, "context": _size(joined), "system": _size(system),
                 "parts": dict(sorted(_parts.items(), key=lambda kv: (-kv[1], kv[0])))}
        from shared import state_file
        state_file.locked_update(Path(session_dir) / COST_FILE, lambda text: merge(text, entry),
                                 Path(session_dir))
    except Exception:  # noqa: BLE001 - a count that cannot be written changes no decision
        pass


def read(session_dir: str) -> dict | None:
    """The last start's entry from the session's counter file, or None."""
    try:
        counters = json.loads((Path(session_dir) / COST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = counters.get(LEDGER_KEY) if isinstance(counters, dict) else None
    return entry if isinstance(entry, dict) else None
