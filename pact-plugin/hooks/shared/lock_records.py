"""The lock records of bin/repact-lock: where a lock file lives, how it is read, and whose it is.

Location: pact-plugin/hooks/shared/lock_records.py. Imported by bin/repact-lock, which writes
the records, and by hooks/docker_lock_gate.py, which reads them, so the two always agree on the
file name, the damage rules, the clock-skew window and owner matching. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import unicodedata
from pathlib import Path
from typing import Optional, Tuple

LOCKS_DIRNAME = "locks"
LOCK_SUFFIX = ".lock"
MAX_TTL_MINUTES = 24 * 60.0
MAX_LABEL_CHARS = 100
# A clock correction moves a stamp ahead by seconds or minutes: such a lock is held. A stamp
# further ahead is a record no acquire wrote, and it would hold for that long past its ttl.
MAX_SECONDS_AHEAD = 600.0
LINE_SEPARATOR_CATEGORIES = ("Zl", "Zp")


def label_problem(value: object) -> Optional[str]:
    """Why `value` cannot be a lock or owner name, else None. A name is one printable line."""
    if not isinstance(value, str) or not value.strip():
        return "is empty"
    if len(value) > MAX_LABEL_CHARS:
        return f"is longer than {MAX_LABEL_CHARS} characters"
    for char in value:
        category = unicodedata.category(char)
        if category.startswith("C") or category in LINE_SEPARATOR_CATEGORIES:
            return "holds a control, format or line-separator character"
    return None


def folded(name: str) -> str:
    """`name` with case and spacing folded: the form two spellings of one name share."""
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def same_owner(a: str, b: str) -> bool:
    return folded(a) == folded(b)


def lock_path(directory: Path, name: str) -> Path:
    """The lock file of `name`: a hash of its folded form, so 'Docker' and 'docker' are one
    resource and a slash or dots in a name never become a path."""
    return directory / (hashlib.sha256(folded(name).encode("utf-8")).hexdigest()[:32] + LOCK_SUFFIX)


def duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


def _positive_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:                       # an integer too large for a float
        return False


def read_lock(path: Path) -> Tuple[Optional[dict], Optional[str]]:
    """(record, None) for a held lock, (None, None) for no lock, (None, why) for a file that is
    not a lock record. Records are written whole (see write_lock), so a damaged file is never a
    write in progress. A symbolic link is never followed: no acquire writes one, and a link to
    nothing would read as free here while the exclusive create finds the name taken."""
    if path.is_symlink():
        return None, "a symbolic link"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, None
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return None, "empty file" if not raw.strip() else "not JSON"
    if not isinstance(record, dict):
        return None, "not a JSON object"
    for key in ("name", "owner"):
        if label_problem(record.get(key)):
            return None, f"bad {key}"
    for key in ("acquired", "ttl_s"):
        if not _positive_number(record.get(key)):
            return None, f"bad {key}"
    # A record no acquire could have written would otherwise never expire.
    if record["ttl_s"] > MAX_TTL_MINUTES * 60:
        return None, "bad ttl_s"
    if record["acquired"] > time.time() + MAX_SECONDS_AHEAD:
        return None, f"acquired more than {duration(MAX_SECONDS_AHEAD)} in the future"
    return record, None
