"""Shadow log and per-session ledger (D9.4 point 4, D9.2, D9.5).

Location: pact-plugin/bin/_recall/shadow.py.
- `${CLAUDE_PLUGIN_DATA}/recall/shadow.jsonl`: one line per moment (type, tool_use_id, matched
  trigger names or touch digests, candidates, would-inject lines, withheld count, latency) and one
  per pull (the agent reading a store item itself). It never holds the tool call's content: no
  command text, and no path or host in clear (a touch is logged as its digest). Every record
  also passes through `scrub_value` (D9s S3), because candidate text is text. At LOG_MAX_BYTES
  the log rotates to shadow.1.jsonl (one generation kept).
- `${CLAUDE_PLUGIN_DATA}/recall/sessions/<session>.json`: the ledger. It holds digests of the
  touches already seen (T2 fires on a FIRST touch only), the item keys already surfaced (each item
  fires at most once per session) and the would-inject count (capped at SESSION_CAP lines;
  after that the rest of the session is logged but marked capped). Each hook call holds
  `sessions/<session>.lock` (flock) from reading the ledger to saving it.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from _repact_common import plugin_data_dir, scrub_value, utc_now_iso

SESSION_CAP = 20
LOG_MAX_BYTES = 5_000_000
LOCK_WAIT_S = 5.0
LOCK_LEDGER = True          # test-only switch: the sabotage test turns the ledger lock off


def recall_dir(create: bool = True) -> Path:
    path = plugin_data_dir(create) / "recall"
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def log_path() -> Path:
    return recall_dir() / "shadow.jsonl"


def rotated_path() -> Path:
    return recall_dir() / "shadow.1.jsonl"


def digest(text: str) -> str:
    """A touch's stable, non-reversible id: the ledger and the log hold this, never the touch."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def _rotate_if_full(path: Path) -> None:
    try:
        if path.stat().st_size >= LOG_MAX_BYTES:
            os.replace(path, rotated_path())
    except OSError:
        pass


def append(record: dict) -> None:
    """One scrubbed JSON line, a single O_APPEND write; never raises."""
    try:
        line = json.dumps(scrub_value(dict({"ts": utc_now_iso()}, **record)), ensure_ascii=False,
                          separators=(",", ":")) + "\n"
        _rotate_if_full(log_path())
        fd = os.open(log_path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError):
        pass


def _safe_session(session_id: Optional[str]) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "no-session")[:80]


class LedgerBusy(Exception):
    """The session's ledger lock was not free within LOCK_WAIT_S: skip this call (fail open)."""


class Ledger:
    """Per-session state, used as a context manager: entering takes an exclusive flock on
    sessions/<session>.lock and then reads the ledger, `save()` rewrites it atomically, and exiting
    releases the lock. The hook is async and fires in parallel for parallel tool calls, so without
    the lock two calls would both read the ledger before either saved, and both would fire the
    same first touch (a lost update)."""

    def __init__(self, session_id: Optional[str]):
        name = _safe_session(session_id)
        self.path = recall_dir() / "sessions" / f"{name}.json"
        self.lock_path = self.path.with_name(f"{name}.lock")
        self._lock_fd: Optional[int] = None
        self.touched: set = set()
        self.fired: set = set()
        self.injected = 0

    def __enter__(self) -> "Ledger":
        if LOCK_LEDGER:
            self._acquire()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.touched = set(data.get("touched") or [])
        self.fired = set(data.get("fired") or [])
        self.injected = int(data.get("injected") or 0)
        return self

    def __exit__(self, *exc) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def _acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._lock_fd = fd
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise LedgerBusy(self.lock_path.name)
                time.sleep(0.005)

    def first_touch(self, touch: str) -> bool:
        """A touch is kept as a digest, so the ledger never holds a path or host in clear."""
        key = digest(touch)
        if key in self.touched:
            return False
        self.touched.add(key)
        return True

    @property
    def capped(self) -> bool:
        return self.injected >= SESSION_CAP

    def record_fired(self, keys: list[str], lines: int) -> None:
        self.fired.update(keys)
        self.injected += lines

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"touched": sorted(self.touched), "fired": sorted(self.fired),
                                       "injected": self.injected}), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass


def read_log(path: Optional[Path] = None) -> list[dict]:
    """Events oldest first: the rotated generation, then the live log (or just `path`)."""
    lines = []
    for part in ([path] if path else [rotated_path(), log_path()]):
        try:
            lines += part.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
    events = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events
