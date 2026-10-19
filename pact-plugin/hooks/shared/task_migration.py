"""
Location: pact-plugin/hooks/shared/task_migration.py
Summary: Moves the open tasks of an earlier team into this session's team on a resume
(the design notes, RS-1 to RS-8).
Used by: hooks/session_init.py (SessionStart with source "resume", the lead frame, through
session_init_lead.migrate_on_resume) and bin/repact-migrate (the lead's explicit run after
a crash); both call migrate_for_session and print resume_tasks_note. migrate_on_resume
prints unplaced_note when the resume left the plugin on the earlier team.

A resume gets a new host team with an empty task store, so the earlier team's open tasks
are out of reach of TaskList, TaskGet and TaskUpdate. `plan` is pure and decides what moves
and under which id; `migrate` does the I/O in two phases so a run that fails part-way
leaves both stores as they were:

  1. create every destination file (TaskStore.create, which never replaces a file); if
     one fails, the files this run created are removed and nothing else happens;
  2. emit one journal row per task, every row before any rename, then rename each source
     to `<id>.json.migrated`, only while its bytes are still the ones that were copied.

A source task file is disk state that any earlier teammate could write, so nothing in it
is trusted: ids and team names are re-validated, edges are rewritten only through the id
table, and a `migrated_from` found in a source file is rebuilt, never copied. For the same
reason a destination task is the copy of a source task only when a `migrated` row of this
session's journal says so (`copied`, the injected reader), the row's `source_sha256` is the
digest of the source file as it stands now, and the destination file still names that source.
What a task's own metadata claims proves nothing alone, and a source with no such row is
copied again, never retired.

Usage:
    result = migrate_for_session("session-old", "session-new", emit)
    result.status  # "moved", "nothing" or "refused" (then result.reason says why)
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from shared.paths import is_safe_path_component
from shared.payload_digest import stored_teachback
from shared.task_store import TaskRef, TaskStore, is_safe_task_id, store_halted

OPEN_STATUSES = ("pending", "in_progress")
MAX_OPEN_TASKS = 200
MAX_TASK_BYTES = 1024 * 1024
# Bounds on what one run reads from a source store, so a planted store cannot stall
# SessionStart: over either, nothing moves.
MAX_SOURCE_FILES = 2000
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_DROPPED_EDGES = 50
MAX_ROW_KEYS = 50
# The longest team name the migration handles: the bound the plugin puts on an agent name
# (dispatch_gate.NAME_MAX_LENGTH). migrate refuses a longer source or destination team, and a
# longer `origin` team or journal-row team is dropped, never cut to another name.
MAX_TEAM_CHARS = 64

STATUS_MOVED = "moved"
STATUS_NOTHING = "nothing"
STATUS_REFUSED = "refused"

REASON_BAD_TEAM = "bad_team"
REASON_LONG_TEAM = "long_team"
REASON_DARK_STORE = "dark_store"
REASON_STANDING_CONFIG = "standing_config"
REASON_PEER_SESSION = "peer_session"
REASON_TOO_MANY = "too_many"
REASON_UNREADABLE = "unreadable"
REASON_CREATE_FAILED = "create_failed"

# ASCII digits only: `str.isdigit` and `\d` also match digits of other scripts, which
# `int` reads as numbers and the host does not.
_NUMERIC = re.compile(r"[0-9]{1,18}")
# The largest whole number the host holds exactly. It takes its next id from the highest
# file id, so a larger numeric id is never carried into a store.
MAX_NUMERIC_ID = 2 ** 53 - 1
_ROW_AT = re.compile(r"[0-9T:+-]{1,32}")
_EDGE_FIELDS = ("blocks", "blockedBy")
_KEY_NAME = re.compile(r"[A-Za-z0-9_]{1,64}")
_OWNER_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_OWNER_SHOWN = 40
_REV_MAX = 10 ** 6
_SHA256 = re.compile(r"[0-9a-f]{64}")
_RETIRED_NAME = re.compile(r"(.+)\.json\.migrated(?:\.[0-9]+)?")


@dataclass(frozen=True)
class Plan:
    """What one run would create. `id_map` holds every source id that has a destination
    id (planned or migrated by an earlier run); `open_count` every open source task,
    movable or not; `skipped` the open ones that failed the RS-2 shape rules."""

    tasks: list
    id_map: dict
    open_count: int
    skipped: int


@dataclass(frozen=True)
class Moved:
    """One migrated task, as the note prints it: nothing here is free text from disk."""

    old_id: str
    new_id: str
    owner: str
    state: str


@dataclass
class Result:
    status: str
    source_team: str = ""
    reason: str = ""
    open_count: int = 0
    moved: list = field(default_factory=list)
    recovered: int = 0
    skipped: int = 0
    retire_failed: int = 0
    emit_failed: int = 0
    # Destination ids of copies that exist but do not read as a task: their sources stand.
    unread_copies: list = field(default_factory=list)


# -- the pure part: RS-2 to RS-5 ---------------------------------------------------------

def _number(task_id: str) -> Optional[int]:
    return int(task_id) if _NUMERIC.fullmatch(task_id) else None


def _beyond_exact(task_id: str) -> bool:
    """An id of ASCII digits (of any length, so past _NUMERIC too) above MAX_NUMERIC_ID."""
    return task_id.isascii() and task_id.isdigit() and int(task_id) > MAX_NUMERIC_ID


def _id_order(task_id: str):
    number = _number(task_id)
    return (0, number, task_id) if number is not None else (1, 0, task_id)


def _is_open(task) -> bool:
    return isinstance(task, dict) and task.get("status") in OPEN_STATUSES


def _movable(task: dict) -> bool:
    """RS-2, the part a parsed object can answer (the file rules are the reader's)."""
    metadata = task.get("metadata")
    return (
        is_safe_task_id(task.get("id"))
        and not _beyond_exact(task["id"])
        and isinstance(task.get("subject"), str)
        and isinstance(task.get("description"), str)
        and isinstance(task.get("blocks"), list)
        and isinstance(task.get("blockedBy"), list)
        and (metadata is None or isinstance(metadata, dict))
    )


def _assign_ids(source_ids: list, dest_ids: set, dest_highwater: int) -> dict:
    """RS-3: an id is kept unless the destination already issued it; a colliding task
    gets the next number above every id the destination holds, its high-water mark and
    every id this run keeps, in ascending source-id order."""
    issued = {task_id.casefold() for task_id in dest_ids if isinstance(task_id, str)}
    ordered = sorted(source_ids, key=_id_order)
    kept = []
    for task_id in ordered:
        number = _number(task_id)
        if task_id.casefold() in issued or (number is not None and number <= dest_highwater):
            continue
        kept.append(task_id)
        issued.add(task_id.casefold())
    numbers = [_number(task_id) for task_id in (*dest_ids, *kept) if isinstance(task_id, str)]
    counter = max([dest_highwater] + [number for number in numbers if number is not None])
    id_map = {task_id: task_id for task_id in kept}
    for task_id in ordered:
        if task_id not in id_map:
            counter += 1
            id_map[task_id] = str(counter)
    return id_map


def _rewrite_edges(task: dict, id_map: dict) -> list:
    """RS-4: rewrite `blocks` and `blockedBy` through the table in place and return the
    dropped entries (not a string, not in the moved set, or the task itself)."""
    dropped = []
    for name in _EDGE_FIELDS:
        kept = []
        for entry in task[name]:
            new_id = id_map.get(entry) if isinstance(entry, str) else None
            if new_id is None or new_id == task["id"]:
                if len(dropped) < MAX_DROPPED_EDGES:
                    dropped.append({"field": name, "id": entry if is_safe_task_id(entry) else None})
            elif new_id not in kept:
                kept.append(new_id)
        task[name] = kept
    return dropped


def _safe_team(team) -> bool:
    return is_safe_path_component(team) and len(team) <= MAX_TEAM_CHARS


def _origin(claimed) -> Optional[dict]:
    """The first hop of a task that is moved again: the source's own `migrated_from`
    names it. Only a safe team and id are carried; everything else it claims is dropped."""
    if not isinstance(claimed, dict):
        return None
    for candidate in (claimed.get("origin"), claimed):
        if (isinstance(candidate, dict) and _safe_team(candidate.get("team"))
                and is_safe_task_id(candidate.get("id"))):
            return {"team": candidate["team"], "id": candidate["id"]}
    return None


def _destination_task(source: dict, id_map: dict, source_team: str, now: str) -> dict:
    """RS-5: the object to create, built in place on `source` (plan's own copy). Owner,
    status and every metadata key are kept; the dead life's wait moves into
    `migrated_from`, which is always built here."""
    task = source
    old_id = task["id"]
    task["id"] = id_map[old_id]
    dropped = _rewrite_edges(task, id_map)
    metadata = task.get("metadata") or {}
    wait = metadata.pop("intentional_wait", None)
    owner = task.get("owner")
    migrated_from = {
        "team": source_team,
        "id": old_id,
        "at": now,
        "status_was": task["status"],
        "owner_was": owner if isinstance(owner, str) else None,
        "wait_was": wait if isinstance(wait, dict) else None,
        "dropped_edges": dropped,
    }
    origin = _origin(metadata.get("migrated_from"))
    if origin is not None:
        migrated_from["origin"] = origin
    metadata["migrated_from"] = migrated_from
    task["metadata"] = metadata
    return task


def plan(source_tasks: list, dest_ids: set, dest_highwater: int, source_team: str,
         now: str, already: Optional[dict] = None) -> Plan:
    """Decide what moves and under which id. Pure: no I/O, the inputs are not changed.

    Args:
        source_tasks: parsed task objects of the source store (any status, any shape).
        dest_ids: every id the destination store holds a file for.
        dest_highwater: the destination's `.highwatermark` (0 when it has none).
        source_team: the team the tasks come from, written into `migrated_from`.
        now: the run's UTC timestamp, written into `migrated_from.at`.
        already: source id -> destination id of tasks an earlier run created. They are
            not planned again; edges to them are rewritten through this table.

    Returns:
        A Plan; `tasks` are in ascending source-id order.
    """
    already = {key: value for key, value in (already or {}).items()
               if isinstance(key, str) and isinstance(value, str)}
    highwater = dest_highwater if type(dest_highwater) is int and dest_highwater > 0 else 0
    movable, open_count, skipped = {}, 0, 0
    for task in source_tasks:
        if not _is_open(task):
            continue
        open_count += 1
        if not _movable(task) or task["id"] in movable:
            skipped += 1
        elif task["id"] not in already:
            try:
                movable[task["id"]] = copy.deepcopy(task)
            except RecursionError:  # nested deeper than a copy can follow
                skipped += 1
    id_map = _assign_ids(list(movable), set(dest_ids) | set(already.values()), highwater)
    id_map.update(already)
    tasks = [_destination_task(movable[task_id], id_map, source_team, now)
             for task_id in sorted(movable, key=_id_order)]
    return Plan(tasks=tasks, id_map=id_map, open_count=open_count, skipped=skipped)


# -- what the journal row and the note may carry ---------------------------------------------

def stored_state(metadata) -> str:
    """Which payload the task holds, from key presence and a bounded `rev` only."""
    if not isinstance(metadata, dict):
        return "nothing stored"
    handoff = metadata.get("handoff")
    if isinstance(handoff, dict):
        rev = handoff.get("rev")
        if type(rev) is int and 0 < rev < _REV_MAX:
            return f"HANDOFF rev {rev} stored"
        return "HANDOFF stored"
    if "teachback_submit" in metadata or stored_teachback(metadata) is not None:
        return "teachback stored"
    return "nothing stored"


def shown_owner(owner) -> str:
    """The owner cut to id characters and 40 of them; "no owner" when nothing is left."""
    if not isinstance(owner, str):
        return "no owner"
    return _OWNER_UNSAFE.sub("", owner)[:_OWNER_SHOWN] or "no owner"


def shown_id(task_id) -> str:
    """A task id as the note prints it: id characters only, 40 of them, else "?"."""
    if not isinstance(task_id, str):
        return "?"
    return _OWNER_UNSAFE.sub("", task_id)[:_OWNER_SHOWN] or "?"


def _row_migrated_from(claimed, source: Optional[tuple] = None) -> dict:
    """`migrated_from` as the journal row carries it, rebuilt field by field: the task's own
    object is metadata a teammate can write, so no key or value is copied unchecked. Each
    field is its checked value or None; the owner is cut as the note cuts it; the wait is
    never carried. `source` is the (team, id) the run retires. When the task names another
    hop (a copy that travelled on), only `source` is reported.
    """
    claimed = claimed if isinstance(claimed, dict) else {}
    named = (claimed.get("team"), claimed.get("id"))
    if source is not None and named != source:
        return {"team": source[0], "id": source[1]}
    at, status, owner = claimed.get("at"), claimed.get("status_was"), claimed.get("owner_was")
    edges = claimed.get("dropped_edges")
    row = {
        "team": named[0] if _safe_team(named[0]) else None,
        "id": named[1] if is_safe_task_id(named[1]) else None,
        "at": at if isinstance(at, str) and _ROW_AT.fullmatch(at) else None,
        "status_was": status if isinstance(status, str) and status in OPEN_STATUSES else None,
        "owner_was": (_OWNER_UNSAFE.sub("", owner)[:_OWNER_SHOWN] or None) if isinstance(owner, str) else None,
        "dropped_edges": [
            {"field": edge["field"], "id": edge.get("id") if is_safe_task_id(edge.get("id")) else None}
            for edge in (edges if isinstance(edges, list) else [])[:MAX_DROPPED_EDGES * 2]
            if isinstance(edge, dict) and isinstance(edge.get("field"), str)
            and edge["field"] in _EDGE_FIELDS
        ][:MAX_DROPPED_EDGES],
    }
    origin = _origin({"origin": claimed.get("origin")})
    if origin is not None:
        row["origin"] = origin
    return row


def journal_row(dest_team: str, task: dict, recovered: bool = False,
                source: Optional[tuple] = None, digest: Optional[str] = None) -> dict:
    """The row `emit` gets for one migrated task: key names only, never a metadata value, and
    `migrated_from` rebuilt from checked fields (_row_migrated_from) without the wait.
    `source` is the (team, id) of the source file this run retires and `digest` the sha256
    of its bytes as copied (`source_sha256`): a later run takes the row as proof for those
    bytes only."""
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    keys = sorted(key for key in metadata if isinstance(key, str) and _KEY_NAME.fullmatch(key))
    row = {
        "kind": "migrated",
        "team": dest_team,
        "task_id": task.get("id"),
        "keys": keys[:MAX_ROW_KEYS],
        "migrated_from": _row_migrated_from(metadata.get("migrated_from"), source),
    }
    if isinstance(digest, str) and _SHA256.fullmatch(digest):
        row["source_sha256"] = digest
    if recovered:
        row["recovered"] = True
    return row


# How many moved tasks the resume note lists before "and N more".
NOTE_LISTED_TASKS = 10

_REFUSAL_CLAUSES = {
    REASON_DARK_STORE: "the host is not writing task files in this session",
    REASON_STANDING_CONFIG: ("that team's config.json still stands, so its life crashed or is "
                             "still running in another window"),
    REASON_PEER_SESSION: "another session of this project may still use that team",
    REASON_TOO_MANY: f"it holds more than {MAX_OPEN_TASKS} open tasks",
    REASON_UNREADABLE: "a task store could not be read",
    REASON_CREATE_FAILED: ("a task file could not be created in this team's store, and what was "
                           "created was removed"),
}
_LONG_TEAM_NOTE = (f"Resume: nothing was moved from the earlier team: its name or this team's is "
                   f"over {MAX_TEAM_CHARS} characters, which the task migration does not handle.")


def resume_tasks_note(result: object) -> Optional[str]:
    """What the resume migration did, for the lead's SessionStart context, or None when
    there is nothing to say (no earlier team, no open task, an unsafe team name).

    Every value comes from a task file, so nothing is printed as stored: ids and owners are
    cut to id characters and 40 of them, the state is one of four fixed phrases, and the
    team name has passed is_safe_path_component. Never raises.
    """
    try:
        if not isinstance(result, Result) or not result.source_team:
            return None
        prior, shown = result.source_team, shown_id
        if result.status == STATUS_REFUSED and result.reason == REASON_LONG_TEAM:
            return _LONG_TEAM_NOTE
        if result.status == STATUS_REFUSED:
            clause = _REFUSAL_CLAUSES.get(result.reason)
            if clause is None:
                return None
            command = {
                REASON_STANDING_CONFIG: f" If that life is over, run `repact-migrate {prior}` to move them.",
                REASON_CREATE_FAILED: f" Run `repact-migrate {prior}` to try again.",
            }.get(result.reason, "")
            return (f"Resume: the earlier team `{prior}` still holds {result.open_count} open "
                    f"task(s) that TaskList cannot show. Nothing was moved: {clause}.{command}")
        lines = []
        if result.moved:
            pairs = [f"#{shown(m.old_id)}->#{shown(m.new_id)}" for m in result.moved
                     if m.old_id != m.new_id]
            ids = "Ids kept." if not pairs else (
                "Ids changed: " + ", ".join(pairs[:NOTE_LISTED_TASKS])
                + (f" and {len(pairs) - NOTE_LISTED_TASKS} more" if len(pairs) > NOTE_LISTED_TASKS else "")
                + "; the others kept.")
            lines.append(f"Resume: moved {len(result.moved)} open task(s) from team `{prior}` "
                         f"into this team. {ids}")
            lines += [f"#{shown(m.new_id)} {m.owner} {m.state}"
                      for m in result.moved[:NOTE_LISTED_TASKS]]
            extra = len(result.moved) - NOTE_LISTED_TASKS
            if extra > 0:
                lines.append(f"and {extra} more")
            lines.append("The earlier life's teammates are gone. Per task: accept what is stored, "
                         "respawn the owner under the same name, or set a new owner.")
        if result.recovered:
            lines.append(f"Resume: finished an earlier move from team `{prior}`: {result.recovered} "
                         "task(s) already in this team, source file(s) now renamed.")
        partial = [f"{count} {what}" for count, what in (
            (result.skipped, f"file(s) in team `{prior}` not readable as a task and left in place"),
            (result.retire_failed, f"source file(s) not renamed: run `repact-migrate {prior}` to "
                                   "rename them"),
            (result.emit_failed, "journal row(s) not written"),
            (len(result.unread_copies),
             f"task(s) of team `{prior}` left in place: the copy in this team does not read as a "
             f"task ({', '.join('#' + shown(i) for i in result.unread_copies[:NOTE_LISTED_TASKS])})"),
        ) if count]
        if partial:
            lines.append("Resume, partial: " + "; ".join(partial) + ".")
        return "\n".join(lines) or None
    except Exception:  # noqa: BLE001 - a note, never a reason to fail SessionStart
        return None


def unplaced_note(team: str) -> Optional[str]:
    """For a resume that left the plugin on the earlier life's team: say so when that team
    holds open tasks, else None.

    The host mints a team on every plain resume, so its task tools are then on a team the
    plugin could not prove is this session's. Nothing can be moved, because the destination
    is not known. Only a count from the store and a team name that has passed
    is_safe_path_component are printed. Never raises.
    """
    try:
        if not is_safe_path_component(team):
            return None
        read = _read_source(TaskStore(), team)
        count = sum(1 for task in read[0] if _is_open(task)) if read else 0
    except Exception:  # noqa: BLE001 - a note, never a reason to fail SessionStart
        return None
    if not count:
        return None
    return (f"Resume: the plugin could not tell which team the host gave this resumed session "
            f"and still names `{team}`, which holds {count} open task(s). If TaskList does not "
            "show them, the host is on another team and nothing was moved: tell the user "
            "before any task work.")


# -- the I/O part: RS-1 and RS-6 ---------------------------------------------------------------

def _task_stems(directory: Path) -> Optional[set]:
    """The ids the directory holds a `<id>.json` name for (any file type), or None when
    it cannot be listed."""
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    return {name[:-5] for name in names if name.endswith(".json") and not name.startswith(".")}


def _retired_stems(directory: Path) -> set:
    """The ids the directory holds a retired name for (`<id>.json.migrated[.n]`): ids the
    store has issued, so no run hands them out again. Empty when it cannot be listed."""
    try:
        names = os.listdir(directory)
    except OSError:
        return set()
    found = (_RETIRED_NAME.fullmatch(name) for name in names if not name.startswith("."))
    return {match.group(1) for match in found if match}


def _read_source(store: TaskStore, team: str):
    """(tasks, skipped, digests) of the source store, or None when it is over the read
    bounds; `digests` maps an id to the sha256 of the bytes its task was parsed from.
    A file over 1 MiB, unparseable, or whose `id` is not its file stem is counted as
    skipped and left alone (RS-2); a symlink is never opened (TaskStore.files)."""
    files = store.files(team)
    if len(files) > MAX_SOURCE_FILES:
        return None
    tasks, skipped, total, digests = [], 0, 0, {}
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            skipped += 1
            continue
        if size > MAX_TASK_BYTES:
            skipped += 1
            continue
        total += size
        if total > MAX_SOURCE_BYTES:
            return None
        read = store.get_digested(TaskRef(team, path.name[:-5]))
        if read is None or read[0].get("id") != path.name[:-5]:
            skipped += 1
            continue
        tasks.append(read[0])
        digests[read[0]["id"]] = read[1]
    return tasks, skipped, digests


def _hops(rows) -> dict:
    """(team, id) -> [(team, id, source digest), ...]: where each `migrated` journal row says
    a task file was copied to. A row of another kind or shape, with an unsafe team or id, or
    with no sha256 of its source (a row older than the digest) is dropped."""
    hops = {}
    for row in rows:
        source = row.get("migrated_from") if isinstance(row, dict) else None
        if not isinstance(source, dict) or row.get("kind") != "migrated":
            continue
        digest = row.get("source_sha256")
        ends = (source.get("team"), source.get("id"), row.get("team"), row.get("task_id"))
        if (_safe_team(ends[0]) and is_safe_task_id(ends[1])
                and _safe_team(ends[2]) and is_safe_task_id(ends[3])
                and isinstance(digest, str) and _SHA256.fullmatch(digest)):
            hops.setdefault(ends[:2], []).append((*ends[2:], digest))
    return hops


def _copies(hops: dict, source: tuple, digest: Optional[str], dest_team: str):
    """Yield (destination id, last source) for every chain of rows that leads from `source`
    into `dest_team`, through each later team the copy travelled on from; `last source` is
    the (team, id) the destination file was copied from. With `digest` (the source still
    stands), only a first row written for exactly those bytes starts a chain."""
    seen, queue = {source}, [(source, digest)]
    while queue:
        at, wanted = queue.pop()
        for team, task_id, row_digest in hops.get(at, ()):
            if wanted is not None and row_digest != wanted:
                continue
            if team == dest_team:
                yield task_id, at
            if (team, task_id) not in seen:
                seen.add((team, task_id))
                queue.append(((team, task_id), None))


def _proven_copy(store: TaskStore, hops: dict, source: tuple, digest: Optional[str],
                 dest_team: str, dest_ids: set):
    """(destination id, task) of the copy the rows prove for `source`; (id, None) when a
    file stands at a row's id but does not read as a task with that id; else (None, None).

    A missing file proves nothing (the copy was deleted). A readable task proves the copy
    only when its own `migrated_from` names the source it was copied from: the plan wrote
    that and no ordinary task write changes it, so another task that took the id fails it.
    """
    unread = None
    for new_id, last in _copies(hops, source, digest, dest_team):
        if new_id not in dest_ids:
            continue
        task = store.get(TaskRef(dest_team, new_id))
        if task is None or task.get("id") != new_id:
            unread = unread or new_id
            continue
        metadata = task.get("metadata")
        claimed = metadata.get("migrated_from") if isinstance(metadata, dict) else None
        if isinstance(claimed, dict) and (claimed.get("team"), claimed.get("id")) == last:
            return new_id, task
    return unread, None


def _earlier_run(store: TaskStore, source_team: str, dest_team: str, by_id: dict,
                 dest_ids: set, copied, digests: dict):
    """(already, recover, unread) from the `migrated` rows `copied` returns: `already` maps
    a source id to the destination id an earlier run gave it; `recover` lists the
    (destination task, source id) pairs whose source file still stands and must be retired
    (RS-6); `unread` the destination ids whose file does not read as a task, so their
    standing sources are neither retired nor copied.

    Task metadata alone is never the proof: a teammate writes it, and a false claim must
    not retire a task that was never copied. No reader, a reader that fails, no row, a row
    for other source bytes, or a destination that is gone or is another task all mean "not
    copied", so the source moves again.
    """
    try:
        hops = _hops(copied()) if copied is not None else {}
    except Exception:  # noqa: BLE001 - the reader is injected; a journal that cannot answer proves nothing
        hops = {}
    already, recover, unread = {}, [], []
    for team, old_id in sorted(hops, key=lambda end: _id_order(end[1])):
        source = by_id.get(old_id)
        if team != source_team or (source is not None and not (_is_open(source) and _movable(source))):
            continue
        digest = digests.get(old_id, "") if source is not None else None
        new_id, task = _proven_copy(store, hops, (team, old_id), digest, dest_team, dest_ids)
        if new_id is None or (task is None and source is None):
            continue
        already[old_id] = new_id
        if task is None:
            unread.append(new_id)
        elif source is not None:
            recover.append((task, old_id))
    return already, recover, unread


def _standing_config(store: TaskStore, team: str) -> bool:
    """True when `teams/<team>/config.json` exists in any form. The earlier life then
    crashed or still runs in another window, and no file tells the two apart."""
    return os.path.lexists(store.root.parent / "teams" / team / "config.json")


def _refusal(store: TaskStore, source_team: str, open_count: int, session_dir: str,
             peer_names_team, allow_standing_config: bool) -> str:
    """RS-1 checks 1 to 4 in order: the first that refuses, or ""."""
    if store_halted(session_dir):
        return REASON_DARK_STORE
    if not allow_standing_config and _standing_config(store, source_team):
        return REASON_STANDING_CONFIG
    try:
        if peer_names_team is not None and peer_names_team(source_team):
            return REASON_PEER_SESSION
    except Exception:  # noqa: BLE001 - a check that cannot answer refuses
        return REASON_PEER_SESSION
    if open_count > MAX_OPEN_TASKS:
        return REASON_TOO_MANY
    return ""


def _create_all(store: TaskStore, dest_team: str, tasks: list) -> bool:
    """Create every task or none: on the first failure the files this run created are
    removed (only while each still holds exactly what this run wrote)."""
    created = []
    for task in tasks:
        if store.create(TaskRef(dest_team, task["id"]), task):
            created.append(task)
            continue
        for done in created:
            store.discard(TaskRef(dest_team, done["id"]), done)
        return False
    return True


def _reserve(store: TaskStore, team: str, ids: list, create: bool) -> None:
    """Raise the team's `.highwatermark` over the numeric `ids`, so an id this run used is
    not issued again after its file is renamed or deleted. Best effort: the retired names
    and the files themselves reserve the ids too."""
    numbers = [number for number in map(_number, ids) if number is not None]
    if numbers:
        store.raise_highwatermark(team, max(numbers), create=create)


def _emit_row(emit, row: dict, result: Result) -> None:
    """Hand one row to the injected writer; False or an exception is a count, never a stop."""
    try:
        emitted = bool(emit(row))
    except Exception:  # noqa: BLE001 - the writer is injected; any failure is a count
        emitted = False
    if not emitted:
        result.emit_failed += 1


def migrate(store: TaskStore, source_team: str, dest_team: str,
            emit: Callable[[dict], bool], now: str, allow_standing_config: bool = False, *,
            session_dir: str = "",
            peer_names_team: Optional[Callable[[str], bool]] = None,
            copied: Optional[Callable[[], list]] = None) -> Result:
    """Move the open tasks of `source_team` into `dest_team`. Never raises on a bad store.

    Args:
        store: the task store both teams live in.
        emit: called with one journal row per retired task; False or an exception is
            counted in `emit_failed` and does not stop the run.
        copied: returns the `migrated` rows `emit` wrote in this session (the journal). They
            are the only proof that an earlier run copied a task; None proves nothing.
        now: the run's UTC timestamp (`migrated_from.at`).
        allow_standing_config: skip RS-1 check 2 (repact-migrate, the lead's explicit act).
        session_dir: this session's directory, for the dark-store marker (RS-1 check 1).
        peer_names_team: RS-1 check 3, True when another session of the project names the
            team. None skips the check; migrate_for_session always passes one.

    Returns:
        A Result. "refused" and "nothing" mean no task file was created or renamed.
    """
    if (not is_safe_path_component(source_team) or not is_safe_path_component(dest_team)
            or source_team == dest_team):
        return Result(STATUS_REFUSED, reason=REASON_BAD_TEAM)
    if not _safe_team(source_team) or not _safe_team(dest_team):
        return Result(STATUS_REFUSED, source_team[:MAX_TEAM_CHARS], REASON_LONG_TEAM)
    result = Result(STATUS_NOTHING, source_team)
    source_dir, dest_dir = store.team_dir(source_team), store.team_dir(dest_team)
    if source_dir is None or dest_dir is None:
        return Result(STATUS_REFUSED, reason=REASON_BAD_TEAM)
    if not source_dir.is_dir():
        return result
    read = _read_source(store, source_team) if _task_stems(source_dir) is not None else None
    if read is None:
        result.status, result.reason = STATUS_REFUSED, REASON_UNREADABLE
        return result
    source_tasks, result.skipped, digests = read
    by_id = {task["id"]: task for task in source_tasks}
    # A team with no task store yet holds nothing: the refusal checks still run, so the note
    # names the real reason, and a create then fails cleanly (the host makes the directory).
    dest_ids = _task_stems(dest_dir) if os.path.lexists(dest_dir) else set()
    already, recover, result.unread_copies = (
        _earlier_run(store, source_team, dest_team, by_id, dest_ids, copied, digests)
        if dest_ids else ({}, [], []))
    # A retired name is an id the destination has issued: it is never handed out again.
    planned = plan(source_tasks, (dest_ids or set()) | _retired_stems(dest_dir),
                   store.highwatermark(dest_team), source_team, now, already)
    result.skipped += planned.skipped
    result.open_count = planned.open_count
    if not planned.tasks and not recover:
        return result
    reason = REASON_UNREADABLE if dest_ids is None else _refusal(
        store, source_team, planned.open_count, session_dir, peer_names_team, allow_standing_config)
    if not reason and not _create_all(store, dest_team, planned.tasks):
        reason = REASON_CREATE_FAILED
    if reason:
        result.status, result.reason = STATUS_REFUSED, reason
        return result
    _reserve(store, dest_team, [task["id"] for task in planned.tasks], create=True)
    retiring = [(task, old_id, True) for task, old_id in recover]
    retiring += [(task, task["metadata"]["migrated_from"]["id"], False) for task in planned.tasks]
    # Every row before any rename: a run that dies between the two leaves a proven copy and
    # a standing source, which the next run retires. A row that cannot be written never
    # keeps a task in two stores: the failure is counted and the rename still happens.
    for task, old_id, recovered in retiring:
        _emit_row(emit, journal_row(dest_team, task, recovered, (source_team, old_id),
                                    digests.get(old_id)), result)
    for task, old_id, recovered in retiring:
        # Only the bytes that were copied are retired: a source written since stays a task.
        if not store.retire(TaskRef(source_team, old_id), digests.get(old_id, "")):
            result.retire_failed += 1
        if not recovered:
            result.moved.append(Moved(old_id, task["id"], shown_owner(task.get("owner")),
                                      stored_state(task["metadata"])))
    _reserve(store, source_team, [old_id for _, old_id, _ in retiring], create=False)
    result.status, result.recovered = STATUS_MOVED, len(recover)
    return result


def migrate_for_session(source_team: str, dest_team: str, emit: Callable[[dict], bool],
                        allow_standing_config: bool = False, *,
                        copied: Optional[Callable[[], list]] = None) -> Result:
    """`migrate` on the process's own store with this session's refusal checks wired in.
    The one entry both callers use, so neither can run without the peer-session check.
    `copied` reads back the rows `emit` wrote (session_init_lead.read_migration_rows)."""
    from shared import pact_context

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return migrate(TaskStore(), source_team, dest_team, emit, now, allow_standing_config,
                   session_dir=pact_context.get_session_dir(),
                   peer_names_team=pact_context.peer_session_names_team, copied=copied)
