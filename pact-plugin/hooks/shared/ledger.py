"""
Location: pact-plugin/hooks/shared/ledger.py
Summary: The test-run ledger: LEDGER_FIELDS, make_ledger_record, ledger_append (one locked
         O_APPEND write per record, truncated and line-break-escaped so a record stays one
         line) and the readers (parse_ledger_lines, read_ledger_counted, read_ledger).
         The run readers (read_runs, RUN_STEM, _run_key, _record_time, _run_end) join a hook row
         to tcount's own row of the same run.
Used by: hooks/run_ledger.py, shared/finished_runs.py, bin/tcount, shared/evidence_check.py,
         shared/acceptance.py, shared/ref_dispositions.py, bin/repact-cost (via the
         bin/_repact_common shim).
Map (file order): the LEDGER_* schema constants, _finite, make_ledger_record, _dump_line,
_encode_record, _ends_mid_line, ledger_append, _usable_row, parse_ledger_lines,
read_ledger_counted, read_ledger, RUN_STEM, read_runs, _record_time, _run_end, _run_key.
"""
from __future__ import annotations

import datetime
import fcntl
import json
import math
import os
import re
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
# tree: on a tcount row, the content tree id of the run's code files (shared/content_tree.py),
# taken before the run: the evidence gate compares it with the tree at acceptance.
# run_dir: on a hook row, the real path of the directory its test run started in (tcount's own
# directory, else the one in force at the runner segment of the command: shared/run_place.py),
# taken when the row was written; the only place the evidence gate gives a hook row.
# env: on a tcount row, the variables of acceptance.SELECTING_ENV that were set ({name: value}):
# they change what a run selects or imports, so the evidence check reads them.
LEDGER_OPTIONAL_FIELDS = ("cmd_executed", "duration_s", "slowest", "tree", "run_dir", "env")
LEDGER_MAP_FIELDS = ("env",)                  # optional, a {str: str} map
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
            optional field that is not a str, a duration_s that is not a finite number >= 0, a
            slowest that is not a list of [file, seconds] pairs, or an env that is not a
            {str: str} map.
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
            elif name in LEDGER_MAP_FIELDS:
                if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                          for k, v in value.items()):
                    raise ValueError(f"{name} must be a map of str to str")
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
    """True for one JSON object whose path fields every reader can take: cwd, log_path and run_dir a
    string or null, call_dirs a list or null, and no NUL in any of those paths. A list where a
    path belongs is unhashable in the readers' sets and a NUL makes every os.path call raise, so
    one such row used to stop the read, and the write-time gate then allowed any count."""
    if not isinstance(item, dict):
        return False
    for name in ("cwd", "log_path", "run_dir"):
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



# ---------------------------------------------------------------------------
# Run readers (shared/evidence_check.py, shared/acceptance.py, shared/ref_dispositions.py)
# ---------------------------------------------------------------------------

# A run's log name: a tcount log name or path, its stem, or the short 'HHMMSSZ-pid' form
# (runs/2026-09-29T215711Z-26744-node.log, 215711Z-26744).
RUN_STEM = re.compile(r"(?<![\w-])(?:(\d{4}-\d{2}-\d{2})T)?(\d{6})Z-(\d{2,7})(?![\w])")


def read_runs(path: os.PathLike | str) -> tuple[list[dict], int]:
    """(records, skipped): one record per run. tcount's own row is dropped when a hook row carries
    its log path, and that hook row gets tcount's "run_cmd" (the argv it ran), "run_ended" (its
    stamp: the run's end, where the hook's is the end of the whole Bash call), "run_cwd",
    "run_tree" (where tcount ran and the content tree it saw, shared/content_tree.py) and
    "run_env" (tcount's env field). Every record gets "run_seq", the line position of the run's
    own tcount row (else of the record itself): tcount appends that row when the run ends, so it
    orders two runs whose one-second stamps tie, where a hook row lands only after the whole Bash
    call. A run_seq read from the file is overwritten. In memory only: the ledger is not written."""
    try:
        records, skipped = parse_ledger_lines(Path(path).read_bytes())
    except OSError:
        return [], 0
    own, place = {}, {}
    for seq, r in enumerate(records):
        if r.get("source") == "tcount" and isinstance(r.get("log_path"), str) and r["log_path"]:
            own[r["log_path"]], place[r["log_path"]] = r, seq
    hooked = {r["log_path"] for r in records
              if r.get("source") == "hook" and isinstance(r.get("log_path"), str) and r["log_path"]}
    runs = []
    for seq, record in enumerate(records):
        log = record.get("log_path") if isinstance(record.get("log_path"), str) else None
        if record.get("source") == "tcount" and log in hooked:
            continue
        record["run_seq"] = place[log] if record.get("source") == "hook" and log in own else seq
        if record.get("source") == "hook" and log in own:
            record["run_cmd"], record["run_ended"] = own[log].get("cmd"), own[log].get("ts")
            record["run_cwd"], record["run_tree"] = own[log].get("cwd"), own[log].get("tree")
            record["run_env"] = own[log].get("env")
        runs.append(record)
    return runs, skipped


def _record_time(record: dict, key: str = "ts") -> Optional[float]:
    """The epoch seconds of a row's ISO stamp (a trailing Z read as UTC on Python 3.9 too), or None."""
    value = record.get(key)
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value).timestamp()
    except ValueError:
        return None


def _run_end(record: dict) -> Optional[float]:
    """When the run ended: the stamp of tcount's own row (read_runs), else the row's."""
    return _record_time(record, "run_ended") or _record_time(record)


def _run_key(record: dict) -> Optional[tuple]:
    """(date or None, HHMMSS, pid) of the run's log name, or None."""
    match = RUN_STEM.search(Path(str(record.get("log_path") or "")).name)
    return match.groups() if match else None
