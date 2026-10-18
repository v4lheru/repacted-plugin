"""
Location: pact-plugin/hooks/shared/distill_queue.py (KB full)
Summary: The distill queue: ids of ended lead sessions waiting for repact-distill, one id per
line in ${CLAUDE_PLUGIN_DATA}/distill/queue.txt (dir 0700, file 0600). Ids only: no path, no
transcript text. Deduped, bounded (the newest MAX_IDS ids; at most MAX_BYTES read), rewritten by
atomic replace under a non-blocking flock on queue.lock. A line that is not a valid id is
dropped on the next write. queue-errors.json counts the enqueue failures the hook swallowed.
Used by: hooks/distill_enqueue.py (enqueue, note_error), bin/repact-distill (queued, remove,
the `queue` line), bin/_doctor_state.py (check_distill: the count, the errors).
Stdlib only; nothing here reads a transcript.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import time
from pathlib import Path
from typing import Iterable, Optional

QUEUE_FILE = "queue.txt"
LOCK_FILE = "queue.lock"
ERRORS_FILE = "queue-errors.json"
DIR_MODE, FILE_MODE = 0o700, 0o600
MAX_ID_CHARS = 128
MAX_IDS = 500
MAX_BYTES = MAX_IDS * (MAX_ID_CHARS + 1)
LOCK_TRIES, LOCK_WAIT_S = 20, 0.01      # at most about 0.2 s, then the caller counts a failure
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,%d}" % (MAX_ID_CHARS - 1), re.ASCII)


def valid_id(value: object) -> bool:
    """True for a string a session id can be: ASCII letters, digits, `-` and `_`, at most
    MAX_ID_CHARS, so it can name no path (`..`, `/`) and no line break."""
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def queue_dir(create: bool = True) -> Path:
    from .plugin_paths import data_subdir

    path = data_subdir("distill", create=create)
    if create:
        os.chmod(path, DIR_MODE)
    return path


def _read_bytes(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return b""
    try:
        return os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)


def queued(directory: Optional[Path] = None) -> list:
    """The valid ids in queue order, deduped. A missing queue is []; an oversize one is read
    to MAX_BYTES and its cut last line dropped. Raises OSError on an unreadable queue."""
    data = _read_bytes((directory or queue_dir(create=False)) / QUEUE_FILE)
    lines = data[:MAX_BYTES].decode("ascii", errors="replace").splitlines()
    if len(data) > MAX_BYTES and lines:
        lines.pop()
    ids, seen = [], set()
    for line in lines:
        if valid_id(line) and line not in seen:
            seen.add(line)
            ids.append(line)
    return ids


def queued_count(directory: Optional[Path] = None) -> int:
    """len(queued()), 0 when the queue cannot be read (advice lines only)."""
    try:
        return len(queued(directory))
    except OSError:
        return 0


@contextlib.contextmanager
def _locked(directory: Path):
    fd = os.open(directory / LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, FILE_MODE)
    try:
        for attempt in range(LOCK_TRIES):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if attempt == LOCK_TRIES - 1:
                    raise TimeoutError("distill queue is locked") from None
                time.sleep(LOCK_WAIT_S)
        yield
    finally:
        os.close(fd)


def _replace(directory: Path, name: str, data: bytes) -> None:
    tmp = directory / f".{name}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, FILE_MODE)
    try:
        os.fchmod(fd, FILE_MODE)
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, directory / name)


def _write_ids(directory: Path, ids: list) -> None:
    _replace(directory, QUEUE_FILE, "".join(f"{i}\n" for i in ids[-MAX_IDS:]).encode("ascii"))


def enqueue(session_id: str, directory: Optional[Path] = None) -> bool:
    """Add one id at the end. False when it is already queued. Raises ValueError on an
    invalid id, OSError or TimeoutError when the queue cannot be written."""
    if not valid_id(session_id):
        raise ValueError("not a session id")
    directory = directory or queue_dir()
    with _locked(directory):
        ids = queued(directory)
        if session_id in ids:
            return False
        _write_ids(directory, ids + [session_id])
    return True


def remove(drop: Iterable[str], directory: Optional[Path] = None) -> int:
    """Remove every id in `drop`; returns how many left the queue."""
    drop = set(drop)
    directory = directory or queue_dir(create=False)
    if not drop or not (directory / QUEUE_FILE).exists():
        return 0
    with _locked(directory):
        ids = queued(directory)
        kept = [i for i in ids if i not in drop]
        if len(kept) != len(ids):
            _write_ids(directory, kept)
    return len(ids) - len(kept)


def error_count(directory: Optional[Path] = None) -> int:
    try:
        data = json.loads(_read_bytes((directory or queue_dir(create=False)) / ERRORS_FILE))
        count = data.get("count") if isinstance(data, dict) else 0
        return count if isinstance(count, int) and count > 0 else 0
    except (OSError, ValueError):
        return 0


def note_error(kind: str, directory: Optional[Path] = None) -> None:
    """Count one swallowed enqueue failure (its exception type only). Never raises."""
    try:
        directory = directory or queue_dir()
        record = {"count": error_count(directory) + 1, "last": str(kind)[:80],
                  "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _replace(directory, ERRORS_FILE, json.dumps(record).encode("utf-8"))
    except Exception:  # noqa: BLE001 - the note is advice; session end must not see it fail
        pass
