"""pact-memory retriever (D9.3): read-only, straight from memory.db, no secretary.

Location: pact-plugin/bin/_recall/pact_memory.py. It NEVER writes the store.

How it opens the DB (measured 2026-09-27, SQLite 3.53.4; lead ruling on #153 amend_1):
- The DB is in WAL mode (header bytes 18/19 = 2), and after a clean close its `-wal`/`-shm`
  sidecars are gone. A `mode=ro` open of such a DB in a writable directory does NOT fail: it
  CREATES both sidecars, and a read-only connection cannot remove them at close, so they stay
  behind in the store's directory. That is a write next to the store, so it is not used here.
- So: a WAL-mode DB with no `-wal` is opened `mode=ro&immutable=1`, which creates nothing. The
  risk is a torn read if a writer starts mid-read; that is documented and shadow-acceptable,
  and every error fails open to no recall from this store.
- When a `-wal` exists (a writer has it open, or sidecars were left behind), `mode=ro` reads
  through the existing sidecars like any reader. A DB not in WAL mode is opened `mode=ro`.
- A read-write open is never tried.
- Every immutable read is bracketed by a signature of the DB file (mtime_ns, size, -wal exists)
  taken before connect and after the read; any change discards the result. The architect measured
  31 and 53 silently inconsistent immutable reads under a writer that opens and closes; the
  bracket caught 53 of 53.

Schema check: there is no FTS table and no `user_version` (it is 0), so the check is a column
FINGERPRINT of the tables read. A mismatch fails open to no recall and says why once.
"""
from __future__ import annotations

import math
import os
import sqlite3
from pathlib import Path
from typing import Callable, Optional, TypeVar

from . import MAX_CANDIDATES, first_sentence, make_candidate, tokens

REQUIRED_COLUMNS = {
    "memories": {"id", "context", "goal", "lessons_learned", "decisions", "entities", "project_id", "created_at"},
    "files": {"id", "path"},
    "memory_files": {"memory_id", "file_id"},
}
T = TypeVar("T")
TEXT_COLUMNS = ("context", "goal", "lessons_learned", "decisions", "entities")


def db_path() -> Path:
    from _repact_common import claude_config_dir
    return claude_config_dir() / "pact-memory" / "memory.db"


def connect(path: Optional[Path] = None) -> tuple[Optional[sqlite3.Connection], str]:
    """(connection, how) or (None, why). Never opens read-write, never creates a sidecar."""
    path = Path(path or db_path())
    if not path.is_file():
        return None, "no pact-memory database"
    real = os.path.realpath(path)
    try:
        with open(real, "rb") as handle:
            wal_mode = handle.read(20)[18:20] == b"\x02\x02"
    except OSError as exc:
        return None, f"unreadable ({type(exc).__name__})"
    immutable = wal_mode and not os.path.exists(real + "-wal")
    how = "ro-immutable" if immutable else "ro"
    try:
        con = sqlite3.connect(f"file:{real}?mode=ro" + ("&immutable=1" if immutable else ""), uri=True)
        con.execute("select 1 from sqlite_master limit 1").fetchone()
        return con, how
    except sqlite3.Error as exc:
        return None, f"unreadable ({type(exc).__name__})"


def fingerprint_ok(con: sqlite3.Connection) -> tuple[bool, str]:
    for table, needed in REQUIRED_COLUMNS.items():
        try:
            have = {row[1] for row in con.execute(f"pragma table_info({table})")}
        except sqlite3.Error:
            return False, f"cannot read {table}"
        missing = needed - have
        if missing:
            return False, f"schema drift: {table} lacks {', '.join(sorted(missing))}"
    return True, ""


def _rows(con: sqlite3.Connection, ids: Optional[list[str]] = None) -> list[tuple]:
    cols = "id, " + ", ".join(TEXT_COLUMNS) + ", project_id"
    if ids is None:
        return list(con.execute(f"select {cols} from memories"))
    marks = ",".join("?" * len(ids))
    return list(con.execute(f"select {cols} from memories where id in ({marks})", ids))


def _candidate(row: tuple, score: float) -> tuple[Optional[dict], Optional[str]]:
    mid, context, goal = row[0], row[1] or "", row[2] or ""
    text = "\n".join(str(v or "") for v in row[1:1 + len(TEXT_COLUMNS)])
    summary = first_sentence(goal) or first_sentence(context) or context
    return make_candidate("pact-memory", mid, context or goal or mid, summary, score, text,
                          f"pact-memory/{mid}")


def store_signature(path: Optional[Path] = None) -> tuple:
    """(mtime_ns, size, -wal exists) of the DB file: what an immutable read brackets."""
    real = os.path.realpath(path or db_path())
    try:
        st = os.stat(real)
        return st.st_mtime_ns, st.st_size, os.path.exists(real + "-wal")
    except OSError:
        return (None, None, None)


def _read(query: Callable[[sqlite3.Connection], T]) -> tuple[Optional[T], str]:
    """Run `query` on a checked read-only connection: (result, note) or (None, why).

    An immutable read is bracketed: the store's signature is taken before connect and again
    after the read, and any change discards the result (fail open). SQLite trusts an immutable
    file not to change, so a writer that opens, commits and closes during the read yields a
    silently inconsistent result that no error reports; the bracket is the only thing that sees it.
    """
    before = store_signature()
    con, how = connect()
    if con is None:
        return None, how
    try:
        ok, why = fingerprint_ok(con)
        if not ok:
            return None, why
        result = query(con)
    except sqlite3.Error as exc:
        return None, f"read failed ({type(exc).__name__})"
    finally:
        con.close()
    if how == "ro-immutable" and store_signature() != before:
        return None, "the store changed during an immutable read; result discarded"
    return result, how


def _same_file(touched: str, row_path: str) -> bool:
    """The touched path IS the row's file: equal, or the RELATIVE one of the two is a whole-segment
    suffix of the other (a row may store a repo-relative path; `b/x.py` is not `ab/x.py`). Two
    absolute paths must be equal."""
    row = row_path.replace("\\", "/")
    row = row[2:] if row.startswith("./") else row
    if touched == row:
        return True
    if not row.startswith("/"):
        return touched.endswith("/" + row)
    return not touched.startswith("/") and row.endswith("/" + touched)


def t2(touch: str) -> tuple[list[dict], list[str], str]:
    """Memories linked (memory_files) to a `files` row whose path is the touched path, or a
    suffix of it / it of the row. Returns (candidates, withheld, note)."""
    kind, _, value = touch.partition(":")
    if kind != "path" or not value:
        return [], [], ""
    norm = value.replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]

    def query(con: sqlite3.Connection) -> tuple[list[str], dict[str, int], dict[str, tuple]]:
        file_ids = [fid for fid, fpath in con.execute("select id, path from files where path like ?",
                                                      (f"%{base}",)) if fpath and _same_file(norm, fpath)]
        if not file_ids:
            return [], {}, {}
        marks = ",".join("?" * len(file_ids))
        counts: dict[str, int] = {}
        for (mid,) in con.execute(f"select memory_id from memory_files where file_id in ({marks})", file_ids):
            counts[mid] = counts.get(mid, 0) + 1
        top = sorted(counts, key=lambda m: (-counts[m], m))[:MAX_CANDIDATES]
        return top, counts, {r[0]: r for r in _rows(con, top)}

    found, note = _read(query)
    if found is None:
        return [], [], note
    top, counts, rows = found
    candidates, withheld = [], []
    for mid in top:
        if mid not in rows:
            continue
        cand, reason = _candidate(rows[mid], float(counts[mid]))
        (candidates.append(cand) if cand else withheld.append(f"pact-memory:{mid}"))
    return candidates, withheld, note


def search(query: str, k: int = MAX_CANDIDATES) -> tuple[list[dict], list[str], str]:
    """IDF-weighted term overlap over the memory text (no FTS table exists; D9.3's premise)."""
    terms = set(tokens(query))
    if not terms:
        return [], [], ""
    rows, note = _read(_rows)
    if rows is None:
        return [], [], note
    bags = [(row, set(tokens(" ".join(str(v or "") for v in row[1:1 + len(TEXT_COLUMNS)])))) for row in rows]
    df = {t: sum(1 for _, bag in bags if t in bag) for t in terms}
    scored = []
    for row, bag in bags:
        score = sum(math.log(1 + len(bags) / (1 + df[t])) for t in terms & bag)
        if score > 0:
            scored.append((score, row))
    candidates, withheld = [], []
    for score, row in sorted(scored, key=lambda s: (-s[0], s[1][0]))[:k]:
        cand, reason = _candidate(row, score)
        (candidates.append(cand) if cand else withheld.append(f"pact-memory:{row[0]}"))
    return candidates, withheld, note
