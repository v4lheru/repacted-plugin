"""
Location: pact-plugin/hooks/shared/ledger.py
Summary: The test-run ledger: LEDGER_FIELDS, make_ledger_record, ledger_append (one locked
         O_APPEND write per record, truncated and line-break-escaped so a record stays one
         line) and the readers (parse_ledger_lines, read_ledger_counted, read_ledger).
Used by: hooks/run_ledger.py, shared/finished_runs.py, bin/tcount, shared/evidence/ledger.py,
         shared/evidence/check.py, bin/repact-cost (via the bin/_repact_common shim).
Map (file order): the LEDGER_* schema constants, _finite, make_ledger_record, _dump_line,
_encode_record, _ends_mid_line, ledger_append, _usable_row, parse_ledger_lines,
read_ledger_counted, read_ledger.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
from pathlib import Path
from typing import Optional

from shared.clock import utc_now_iso
from shared.plugin_paths import ledger_path


LEDGER_SCHEMA_VERSION = 1
LEDGER_LINE_MAX = 4096          # one record per os.write stays atomic under O_APPEND
LEDGER_SOURCES = ("hook", "tcount")
LEDGER_COUNT_FIELDS = ("collected", "passed", "failed", "errors", "skipped")
# Field order is the schema (repact-user-layer.md section 11.3, plus "v" and "call_dirs").
# call_dirs: on a hook row, the directories its own Bash call was made in (run_ledger.call_dirs).
LEDGER_FIELDS = (
    "v", "ts", "session_id", "agent_id", "agent_name", "team", "task_id",
    "cwd", "git_head", "git_branch", "cmd", "runner",
    "collected", "passed", "failed", "errors", "skipped",
    "exit_code", "count_parsed", "source", "log_path", "call_dirs",
)
# Written only when given, after the fields above: a row without one is the row it always was.
# cmd_executed: on a tcount row, the argv tcount ran when it differs from cmd by a worker flag
# tcount added or lowered (cmd stays the command as typed: runs are told apart by it).
LEDGER_OPTIONAL_FIELDS = ("cmd_executed", "duration_s", "slowest")
LEDGER_NUMBER_FIELDS = ("duration_s",)        # optional, a finite int or float >= 0 (D15)
LEDGER_PAIR_FIELDS = ("slowest",)             # optional, a list of [file, seconds] pairs (a str, a finite number >= 0)
_TRUNCATION_MARK = "...[truncated]"
# str.splitlines() breaks a line at these, and json.dumps(ensure_ascii=False) writes them raw, so
# a command holding one read as two unparseable lines to every reader that splits that way.
_LINE_BREAKS = {"\u0085": "\\u0085", "\u2028": "\\u2028", "\u2029": "\\u2029"}


def _finite(value: int | float) -> bool:
    """math.isfinite, False for an int too large for a float (it raises OverflowError there)."""
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _file_seconds_pairs(value) -> bool:
    """True for a list of [file, seconds] pairs: a str and a finite number >= 0 (bools are not)."""
    if not isinstance(value, list):
        return False
    for pair in value:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return False
        file, seconds = pair
        if not isinstance(file, str) or isinstance(seconds, bool) or not isinstance(seconds, (int, float)) \
                or not _finite(seconds) or seconds < 0:
            return False
    return True


def make_ledger_record(**fields) -> dict:
    """Build a schema-complete ledger record.

    Missing fields become None, "v" and "ts" are filled in, and count_parsed defaults
    to True when any count is an int. Unknown fields raise, so a typo in a writer
    fails in its own tests instead of silently dropping a count. An optional field
    (LEDGER_OPTIONAL_FIELDS) is in the record only when it is given and not None.

    Raises:
        ValueError: unknown field, bad source, a non-int count, a non-bool count_parsed, an
            optional field that is not a str, a duration_s that is not a finite number >= 0, or a
            slowest that is not a list of [file, seconds] pairs.
    """
    unknown = set(fields) - set(LEDGER_FIELDS) - set(LEDGER_OPTIONAL_FIELDS)
    if unknown:
        raise ValueError(f"unknown ledger field(s): {', '.join(sorted(unknown))}")
    record = {name: fields.get(name) for name in LEDGER_FIELDS}
    for name in LEDGER_OPTIONAL_FIELDS:
        if fields.get(name) is not None:
            value = fields[name]
            if name in LEDGER_NUMBER_FIELDS:
                if isinstance(value, bool) or not isinstance(value, (int, float)) \
                        or not _finite(value) or value < 0:
                    raise ValueError(f"{name} must be a finite number >= 0")
            elif name in LEDGER_PAIR_FIELDS:
                if not _file_seconds_pairs(value):
                    raise ValueError(f"{name} must be a list of [file, seconds] pairs")
            elif not isinstance(value, str):
                raise ValueError(f"{name} must be a str")
            record[name] = value
    record["v"] = LEDGER_SCHEMA_VERSION
    record["ts"] = record["ts"] or utc_now_iso()
    if record["source"] not in LEDGER_SOURCES:
        raise ValueError(f"source must be one of {LEDGER_SOURCES}")
    for name in LEDGER_COUNT_FIELDS + ("exit_code",):
        value = record[name]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"{name} must be an int or None")
    if record["count_parsed"] is None:
        record["count_parsed"] = any(record[name] is not None for name in LEDGER_COUNT_FIELDS)
    if not isinstance(record["count_parsed"], bool):
        raise ValueError("count_parsed must be a bool")
    return record


def _dump_line(record: dict) -> bytes:
    """The record as one newline-terminated JSON line that no reader splits in two."""
    text = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    for char, escaped in _LINE_BREAKS.items():
        text = text.replace(char, escaped)
    return (text + "\n").encode("utf-8")


def _encode_record(record: dict) -> bytes:
    """One newline-terminated JSON line, shrinking cmd_executed/cmd/cwd/log_path to fit the cap."""
    record = dict(record)
    line = _dump_line(record)
    for name in ("cmd_executed", "cmd", "log_path", "cwd"):
        overflow = len(line) - LEDGER_LINE_MAX
        if overflow <= 0:
            break
        value = record.get(name)
        if isinstance(value, str) and value:
            keep = max(0, len(value.encode("utf-8")) - overflow - len(_TRUNCATION_MARK) - 8)
            record[name] = value.encode("utf-8")[:keep].decode("utf-8", "ignore") + _TRUNCATION_MARK
            line = _dump_line(record)
    if len(line) > LEDGER_LINE_MAX:
        raise ValueError("ledger record cannot be shrunk under the cap")
    return line


def _ends_mid_line(fd: int) -> bool:
    """True when the file's last byte is not a newline: a writer died, or the disk filled, inside
    a record. The next record then starts on a line of its own instead of joining the fragment."""
    if os.lseek(fd, 0, os.SEEK_END) == 0:
        return False
    os.lseek(fd, -1, os.SEEK_END)
    return os.read(fd, 1) != b"\n"


def ledger_append(record: dict, path: Optional[os.PathLike | str] = None) -> bool:
    """Append one record to the run ledger (file mode 600) as one whole line.

    Writers take an exclusive flock for the write, so two processes never interleave a record,
    and a record always starts on its own line, even after a fragment a dead writer left.
    The record is passed through make_ledger_record() first, so callers may pass the
    raw fields. Never raises: hooks must not fail, so any error returns False.

    Returns:
        True when the line was written.
    """
    try:
        line = _encode_record(make_ledger_record(**record))
        target = Path(path) if path else ledger_path()
        fd = os.open(target, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError:
                pass        # no flock on this filesystem: one O_APPEND write is still one line
            if _ends_mid_line(fd):
                line = b"\n" + line
            written = 0
            while written < len(line):          # O_APPEND: each write lands at the end
                written += os.write(fd, line[written:])
        finally:
            os.close(fd)                        # closing the descriptor releases the lock
        return True
    except (OSError, ValueError, TypeError):
        return False


def _usable_row(item) -> bool:
    """True for one JSON object whose path fields every reader can take: cwd and log_path a
    string or null, call_dirs a list or null, and no NUL in any of those paths. A list where a
    path belongs is unhashable in the readers' sets and a NUL makes every os.path call raise, so
    one such row used to stop the read, and the write-time gate then allowed any count."""
    if not isinstance(item, dict):
        return False
    for name in ("cwd", "log_path"):
        value = item.get(name)
        if value is not None and (not isinstance(value, str) or "\x00" in value):
            return False
    dirs = item.get("call_dirs")
    if dirs is None:
        return True
    return isinstance(dirs, list) and not any(isinstance(path, str) and "\x00" in path for path in dirs)


def parse_ledger_lines(data: bytes) -> tuple[list[dict], int]:
    """(records, skipped) for raw ledger bytes. Lines end at a newline byte and nowhere else
    (str.splitlines also breaks at U+2028, U+0085 and others, which older rows hold raw). A line
    that is not one usable JSON object (_usable_row), or that nests too deep to parse, is skipped
    and counted, never raised on; a blank line is neither."""
    records: list[dict] = []
    skipped = 0
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, RecursionError):
            item = None
        if _usable_row(item):
            records.append(item)
        else:
            skipped += 1
    return records, skipped


def read_ledger_counted(path: Optional[os.PathLike | str] = None) -> tuple[list[dict], int]:
    """(records, skipped): read_ledger's records, and how many lines it could not read."""
    target = Path(path) if path else ledger_path(create_dir=False)
    try:
        records, skipped = parse_ledger_lines(target.read_bytes())
    except OSError:
        return [], 0
    attributed = {r.get("log_path") for r in records if r.get("source") == "hook" and r.get("log_path")}
    return [r for r in records if not (r.get("source") == "tcount" and r.get("log_path") in attributed)], skipped


def read_ledger(path: Optional[os.PathLike | str] = None) -> list[dict]:
    """Every parseable record in the ledger, in file order (bad lines are skipped), one per run:
    a tcount run is written twice, by tcount and by the run-ledger hook that adds the agent, so
    the tcount row is dropped when a hook row carries the same log path. read_ledger_counted
    also says how many lines were skipped."""
    return read_ledger_counted(path)[0]

