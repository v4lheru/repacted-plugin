"""
Location: pact-plugin/hooks/shared/task_store.py
Summary: The one module that opens a path under `<config>/tasks/`, the host's task store.
Used by: every hook and bin module that reads or updates a task file (the gates, the
emitters, repact-wait, the doctor, the replay corpus builder) and by task_utils, which
keeps re-exports of the three readers and the writer for one release. shared/ops/dispatch creates
the work task of a dispatch through `create_next`.

The store is a host-owned state cache: `tasks/<team>/<id>.json`, written first by the
host and mirrored to the session journal by the hooks. Only `create` makes a task file: the
resume migration calls it (a resume moves the earlier team's open tasks), and `create_next`
calls it for `repact dispatch` (shared/ops), the CLI that creates its own work task.
Every path the adapter hands out has passed the same rules, so no caller repeats them:

  - the team passes is_safe_path_component (one `[A-Za-z0-9_-]+` component) and the id
    is_safe_task_id (one file name: no separator, control character or dot name);
  - a team dir or task file that is a symlink, or resolves outside the root, is skipped;
  - a dotfile-prefixed *.json is ignored (the host never writes one);
  - a file whose JSON is not an object reads as None;
  - `update` goes through the host's own directory lock and never creates a file;
  - `create`, `retire` and `discard` (the migration's writers) take the same lock, never
    replace a file that exists and never follow a symlink.

Usage:
    store = TaskStore()                       # <config>/tasks
    task = store.get(TaskRef("session-ab12", "42"))
    for team in store.teams_holding("42"): ...
    store.update(TaskRef(team, "42"), mutate)  # mutate(task) -> True to write
    store.create(TaskRef(team, "42"), task)   # task_migration only; False if it exists
    store.create_next(team, lambda id: task)  # shared/ops/dispatch: the next free id, or None
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

from shared.paths import get_claude_config_dir, is_safe_path_component

STORE_DIRNAME = "tasks"
# The name a migrated source file is renamed to: not `<id>.json`, so neither the host nor
# `files` reads it as a task, and the content stays for a restore by hand.
RETIRED_SUFFIX = ".migrated"
HIGHWATERMARK_NAME = ".highwatermark"
# A fresh id has no lock holder and a dead team's lock is stale, so the migration's
# writers wait far less than `update` does: SessionStart must not stall on 200 files.
_MIGRATION_LOCK_WAIT = 2.0
# How many ids create_next tries when the host keeps taking the one it picked.
_CREATE_ATTEMPTS = 20
# A task id is one file name the host chose: no separator, no NUL or control character,
# not a dot name, and short enough for a file name with ".json" appended. Wider than a
# team name (the host has written ids with spaces and non-ASCII letters) but never a path.
_ID_FORBIDDEN = set("/\\\x00") | {chr(c) for c in range(0x20)} | {"\x7f"}
_ID_MAX_BYTES = 250


def is_safe_task_id(value) -> bool:
    if not isinstance(value, str) or not value or value.startswith("."):
        return False
    if any(ch in _ID_FORBIDDEN for ch in value) or ".." in value:
        return False
    return len(value.encode("utf-8", "replace")) <= _ID_MAX_BYTES


@dataclass(frozen=True)
class TaskRef:
    """One task file: `tasks/<team>/<task_id>.json`. An empty team names the legacy flat
    layout `tasks/<task_id>.json`, which read_task_json still falls back to."""

    team: str
    task_id: str


@dataclass(frozen=True)
class WatchKey:
    """What repact-wait polls: a change in any field is a write."""

    mtime_ns: int
    size: int
    inode: int


class TaskStore:
    """Reader and locked writer of the host's task store under one root."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else get_claude_config_dir() / STORE_DIRNAME

    @classmethod
    def for_config(cls, config_root: Path) -> "TaskStore":
        """The store of a config root other than the process's own."""
        return cls(Path(config_root) / STORE_DIRNAME)

    # -- paths (validated; the file need not exist) --------------------------------

    def _contained(self, path: Path) -> Optional[Path]:
        """`path` when it resolves under the root and is not a symlink, else None."""
        try:
            if path.is_symlink():
                return None
            path.resolve().relative_to(self.root.resolve())
        except (OSError, ValueError):
            return None
        return path

    def team_dir(self, team: str) -> Optional[Path]:
        """The team's directory, or None for an unsafe name or an escaping symlink. The
        directory may not exist yet."""
        if not is_safe_path_component(team):
            return None
        return self._contained(self.root / team)

    def path(self, ref: TaskRef) -> Optional[Path]:
        """The task file's path, or None when either segment is unsafe or a symlink
        escapes the root. The file may not exist."""
        if not is_safe_task_id(ref.task_id):
            return None
        if ref.team == "":
            directory: Optional[Path] = self._contained(self.root)
        else:
            directory = self.team_dir(ref.team)
        if directory is None:
            return None
        return self._contained(directory / f"{ref.task_id}.json")

    # -- reads ---------------------------------------------------------------------

    def teams(self) -> list[str]:
        """Every team directory (regular directories with safe names), sorted."""
        try:
            entries = sorted(os.scandir(self.root), key=lambda entry: entry.name)
        except OSError:
            return []
        names = []
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False) and is_safe_path_component(entry.name):
                    names.append(entry.name)
            except OSError:
                continue
        return names

    def files(self, team: str) -> list[Path]:
        """The team's task files (regular, non-dotfile *.json), sorted by name."""
        directory = self.team_dir(team)
        if directory is None:
            return []
        try:
            candidates = sorted(directory.glob("*.json"))
        except OSError:
            return []
        files = []
        for path in candidates:
            if path.name.startswith("."):
                continue
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                continue
            files.append(path)
        return files

    def iter_tasks(self, team: str) -> Iterator[dict]:
        """Parsed task objects of the team; an unreadable or non-object file is skipped."""
        for path in self.files(team):
            task = _load(path)
            if task is not None:
                yield task

    def list(self, team: str) -> list[dict]:
        return list(self.iter_tasks(team))

    def get(self, ref: TaskRef) -> Optional[dict]:
        """The task object, or None (absent, unreadable, symlink, not an object)."""
        path = self.path(ref)
        if path is None or not self.exists(ref):
            return None
        return _load(path)

    def get_digested(self, ref: TaskRef) -> Optional[tuple]:
        """(task object, sha256 hex of the file's bytes) from one read, or None as `get`.
        The digest names exactly the bytes the object was parsed from."""
        path = self.path(ref)
        if path is None or not self.exists(ref):
            return None
        try:
            data = path.read_bytes()
            task = _parse(data.decode("utf-8"))
        except (OSError, ValueError, RecursionError):
            return None
        return (task, hashlib.sha256(data).hexdigest()) if isinstance(task, dict) else None

    def exists(self, ref: TaskRef) -> bool:
        """True iff the task file is a regular file (the probe's primitive)."""
        path = self.path(ref)
        try:
            return path is not None and path.is_file()
        except OSError:
            return False

    def watch_key(self, ref: TaskRef) -> Optional[WatchKey]:
        path = self.path(ref)
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        return WatchKey(stat.st_mtime_ns, stat.st_size, stat.st_ino)

    def team_mtime_ns(self, team: str) -> Optional[int]:
        """The team directory's mtime (a new or replaced file changes it), else None."""
        directory = self.team_dir(team)
        if directory is None:
            return None
        try:
            return directory.stat().st_mtime_ns
        except OSError:
            return None

    def teams_holding(self, task_id: str) -> list[str]:
        """The teams whose directory holds `<task_id>.json`, sorted."""
        return [team for team in self.teams() if self.exists(TaskRef(team, task_id))]

    # -- the one writer ------------------------------------------------------------

    def update(self, ref: TaskRef, mutate: Callable[[dict], bool]) -> bool:
        """Read-modify-write under the host's own task lock, the DIRECTORY `<id>.json.lock`
        (state_file.host_locked_update), with a temp name unique to the write. Hook and
        host writers of the same task therefore serialise, and each re-reads what the
        other wrote. A regular-file flock sidecar there (1.5.0) made every later host
        TaskUpdate fail with ENOTDIR, so none is ever created.

        `mutate(task)` changes the dict in place and returns True to write it. Never
        creates a task file: an absent, empty or non-object file is left alone. Returns
        True iff the write landed; any OSError is False (a refused symlink or an escape
        included).
        """
        from shared import state_file

        path = self.path(ref)
        if path is None:
            return False

        def apply(text: str):
            try:
                task = _parse(text) if text else None
            except ValueError:
                task = None
            if not isinstance(task, dict) or not task or not mutate(task):
                return text, False, False
            try:
                return json.dumps(task, allow_nan=False), True, True
            except ValueError:  # the mutation added NaN or Infinity: nothing is written
                return text, False, False

        try:
            return bool(state_file.host_locked_update(path, apply, self.root))
        except OSError:
            return False

    # -- the migration's writers (shared/task_migration only) ------------------------

    def highwatermark(self, team: str) -> int:
        """The largest id the host says it has issued in the team (`.highwatermark`), or 0
        when the file is absent, a symlink, unreadable or not a plain non-negative number."""
        directory = self.team_dir(team)
        marker = None if directory is None else self._contained(directory / HIGHWATERMARK_NAME)
        try:
            if marker is None or not marker.is_file():
                return 0
            text = marker.read_text(encoding="ascii").strip()
        except (OSError, ValueError):
            return 0
        return int(text) if text and len(text) <= 18 and text.isascii() and text.isdigit() else 0

    def raise_highwatermark(self, team: str, number: int, create: bool = False) -> bool:
        """Raise the team's `.highwatermark` to `number`, so no id up to it is handed out
        again; never lowers it. A store with no marker gets one only with `create`. The
        number is written to a temp name and renamed into place. True iff the marker now
        covers `number`; a symlink, a non-file or any OSError is False."""
        directory = self.team_dir(team)
        marker = None if directory is None else self._contained(directory / HIGHWATERMARK_NAME)
        if marker is None or type(number) is not int or not 0 < number < 10 ** 18:
            return False
        temp = marker.with_name(f"{HIGHWATERMARK_NAME}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
        try:
            if os.path.lexists(marker):
                if marker.is_symlink() or not marker.is_file():
                    return False
            elif not create or not directory.is_dir():
                return False
            if self.highwatermark(team) >= number:
                return True
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
            with os.fdopen(os.open(str(temp), flags, 0o600), "w", encoding="ascii") as handle:
                handle.write(str(number))
            os.replace(temp, marker)
            return True
        except OSError:
            try:
                os.unlink(temp)
            except OSError:
                pass
            return False

    def _locked(self, path: Path, act: Callable[[Path], bool]) -> bool:
        """Run `act(target)` under the host's directory lock of `path`; False when the path
        leaves the root, the lock is not taken or `act` raises OSError."""
        from shared import state_file

        try:
            target = state_file._contained_directory(path, self.root) / path.name
            lock = target.with_name(target.name + ".lock")
            taken = state_file._take_dir_lock(lock, _MIGRATION_LOCK_WAIT, state_file.HOST_LOCK_STALE)
            if taken is None:
                return False
            try:
                return bool(act(target))
            finally:
                try:
                    now = os.lstat(lock)
                    if (now.st_dev, now.st_ino) == taken:
                        os.rmdir(lock)
                except OSError:
                    pass
        except OSError:
            return False

    def create(self, ref: TaskRef, task: dict) -> bool:
        """Create the task file with `task` as its content; never replaces one. The whole
        object is written to a temp name first and hard-linked into place, which fails when
        the name exists (a file, a symlink or anything else), so no reader sees a partial
        file and a host write that got there first is kept. The team directory must exist.
        Returns True iff the file reads back equal to `task`.
        """
        path = self.path(ref)
        if path is None or not ref.team or not isinstance(task, dict):
            return False
        try:
            if not path.parent.is_dir() or os.path.lexists(path):
                return False
            data = json.dumps(task, allow_nan=False).encode("utf-8")
        except (OSError, TypeError, ValueError, RecursionError):
            return False

        def link(target: Path) -> bool:
            temp = target.with_name(f".{target.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
            fd = os.open(str(temp), flags, 0o600)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.link(temp, target, follow_symlinks=False)
            finally:
                try:
                    os.unlink(temp)
                except OSError:
                    pass
            return True

        return self._locked(path, link) and self.get(ref) == task

    def largest_id(self, team: str) -> int:
        """The largest numeric id any file of the team names: a task file or a retired copy
        (`<id>.json.migrated*`), so a renamed id is never issued again. 0 when there is none."""
        directory = self.team_dir(team)
        try:
            names = os.listdir(directory) if directory is not None else []
        except OSError:
            return 0
        numbers = [int(stem) for stem in (name.split(".json", 1)[0] for name in names if ".json" in name)
                   if stem.isascii() and stem.isdigit() and len(stem) <= 18]
        return max(numbers, default=0)

    def create_next(self, team: str, build: Callable[[str], dict]) -> Optional[str]:
        """Create a task under the team's next free id (shared/ops/dispatch: the CLI makes its own
        task). The id is one past both the `.highwatermark` and every id on disk; `build(id)`
        returns the task object. The team directory is made when absent (the host makes it on
        the lead's first TaskCreate), and the marker is raised over the id afterwards. A
        collision (the host took the id first) moves to the next id. Returns the id, or None."""
        directory = self.team_dir(team)
        if directory is None:
            return None
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            return None
        for _attempt in range(_CREATE_ATTEMPTS):
            number = max(self.highwatermark(team), self.largest_id(team)) + 1
            ref = TaskRef(team, str(number))
            task = build(ref.task_id)
            if self.create(ref, task):
                self.raise_highwatermark(team, number, create=True)
                return ref.task_id
        return None

    def retire(self, ref: TaskRef, digest: Optional[str] = None) -> bool:
        """Rename the task file to `<id>.json.migrated` (or `.migrated.<n>` when an earlier
        one stands), so the id is no longer a task of this team. With `digest`, only while
        the file's bytes still hash to it (get_digested), checked under the lock: a write
        that landed after the copy was read keeps the file a task. True iff the rename
        landed; a symlink, a missing file or a taken lock is False and nothing changes."""
        path = self.path(ref)
        if path is None or not ref.team:
            return False

        def rename(target: Path) -> bool:
            if not stat.S_ISREG(os.lstat(target).st_mode):
                return False
            if digest is not None and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                return False
            for suffix in [RETIRED_SUFFIX] + [f"{RETIRED_SUFFIX}.{n}" for n in range(1, 10)]:
                retired = target.with_name(target.name + suffix)
                if not os.path.lexists(retired):
                    os.rename(target, retired)
                    return True
            return False

        return self._locked(path, rename)

    def discard(self, ref: TaskRef, task: dict) -> bool:
        """Remove a file `create` made, only while it still holds exactly `task` (the undo
        of a migration run that could not create every file). True iff it was removed."""
        path = self.path(ref)
        if path is None or not ref.team or not self.exists(ref):
            return False

        def remove(target: Path) -> bool:
            if _load(target) != task:
                return False
            os.unlink(target)
            return True

        return self._locked(path, remove)


def _refuse_constant(token: str):
    raise ValueError(f"{token} is not JSON")


def _finite_float(token: str) -> float:
    """float(token), refusing a literal that overflows to inf (1e999): json never sends it to
    parse_constant. An underflow (1e-999) is finite and reads as 0.0."""
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"{token} is out of the float range")
    return value


# CPython's default int digit limit (3.11+, and late 3.9/3.10 patches). Enforced here so
# every interpreter refuses the same files, and int() never runs quadratic on a huge token.
_INT_DIGITS_MAX = 4300


def _bounded_int(token: str) -> int:
    if len(token.lstrip("-")) > _INT_DIGITS_MAX:
        raise ValueError("integer has too many digits")
    return int(token)


def _parse(text: str):
    """`json.loads` without Python's extension: NaN, Infinity, -Infinity and a float literal
    that overflows to inf raise ValueError, as does an integer longer than _INT_DIGITS_MAX
    digits. The host never writes them, and a file that holds one is unreadable to a strict
    parser."""
    return json.loads(text, parse_constant=_refuse_constant, parse_float=_finite_float,
                      parse_int=_bounded_int)


def _load(path: Path) -> Optional[dict]:
    try:
        task = _parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return task if isinstance(task, dict) else None


# -- the probe marker: is the host still writing the store? ----------------------------
#
# hooks/task_store_probe.py writes `<session_dir>/task-store-probe.json` after each
# TaskCreate or TaskUpdate frame: `misses` counts consecutive frames whose task file was
# absent, `seen` the task keys that existed at an earlier probe, `halt` whether the probe
# has declared the store dark. dispatch_gate reads `halt` to refuse Agent spawns; a later
# probe that finds the file clears it.

PROBE_MARKER = "task-store-probe.json"
PROBE_MISS_LIMIT = 2
PROBE_SEEN_CAP = 200
HALT_RULE = "dispatch.task_store_absent"


def halt_sentence(team: str, task_id: str) -> str:
    return (f"⚠️ ALGEDONIC HALT: the host did not write tasks/{team}/{task_id}.json; gates, "
            "waits and HANDOFFs cannot work. Stop dispatching; run repact-doctor.")


def read_probe_marker(session_dir: str) -> dict:
    """The marker as a dict ({} when absent, unreadable or not an object)."""
    if not session_dir:
        return {}
    try:
        marker = json.loads((Path(session_dir) / PROBE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return marker if isinstance(marker, dict) else {}


def write_probe_marker(session_dir: str, marker: dict) -> bool:
    """Replace the marker under its lock; False when the session dir refuses the write."""
    from shared import state_file

    if not session_dir:
        return False
    text = json.dumps(marker, sort_keys=True)
    try:
        state_file.locked_update(Path(session_dir) / PROBE_MARKER,
                                 lambda old: (text, old != text, True), Path(session_dir))
    except OSError:
        return False
    return True


def store_halted(session_dir: str) -> bool:
    """True while the probe's marker says the host task store is dark."""
    return read_probe_marker(session_dir).get("halt") is True


# -- the readers and the writer task_utils used to hold ------------------------------

def iter_team_task_jsons(team_name: str, tasks_base_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield the parsed task objects of `tasks/<team_name>/*.json`. Never raises; yields
    nothing on an unsafe name, a missing dir or a symlink escape. `tasks_base_dir`
    overrides the root (tests)."""
    store = TaskStore(Path(tasks_base_dir)) if tasks_base_dir else TaskStore()
    yield from store.iter_tasks(team_name)


def read_task_json(task_id: str, team_name: Optional[str],
                   tasks_base_dir: Optional[str] = None) -> dict:
    """The raw task object by id: the team directory (name case-folded, as the marker
    sink folds it) first, then the legacy flat layout. {} on any failure: fail-open, the
    callers bypass rather than crash. The id is sanitised (`/`, `\\`, `..` dropped) before
    the adapter's own component check."""
    if not isinstance(task_id, str) or not task_id:
        return {}
    task_id = task_id.replace("/", "").replace("\\", "").replace("..", "")
    if not task_id:
        return {}
    store = TaskStore(Path(tasks_base_dir)) if tasks_base_dir else TaskStore()
    team = team_name.lower() if isinstance(team_name, str) else ""
    for ref in (TaskRef(team, task_id), TaskRef("", task_id)):
        if ref.team and not is_safe_path_component(ref.team):
            continue
        if store.exists(ref):
            task = store.get(ref)
            return task if task is not None else {}
    return {}


def update_task_file(team_name: str, task_id: str, mutate: Callable[[dict], bool]) -> bool:
    """`TaskStore().update(TaskRef(team_name, task_id), mutate)`; see TaskStore.update."""
    return TaskStore().update(TaskRef(team_name, task_id), mutate)
