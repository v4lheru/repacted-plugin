"""
Location: pact-plugin/bin/_scratch_prune.py
Summary: repact-scratch-prune's logic: delete rebuildable build output from stale Claude Code
session temp folders, and nothing else.
Used by: bin/repact-scratch-prune (by hand: a dry run unless --apply) and hooks/doctor_brief.py,
which starts `--auto` detached and niced at most once a day.

Layout (observed 2026-10-03 on macOS): <root>/<cwd-slug>/<session-uuid>/ holding scratchpad/ and
tasks/. The root is claude-<uid> under $CLAUDE_CODE_TMPDIR, else under /tmp resolved (/private/tmp
on macOS; not $TMPDIR, which macOS points elsewhere). REPACT_SCRATCH_ROOT or --root overrides it.

A session folder is pruned only when all of these hold:
  - the root is a real directory (not a symlink), owned by this user, writable by nobody else;
  - its slug folder starts with '-' and its own name is a lowercase UUID, both real directories
    owned by this user (any other folder under the root is not Claude's and is never read);
  - it is not the current session (--skip-session) nor one Claude Code lists as running
    (<config dir>/sessions/*.json);
  - nothing in it changed for STALE_DAYS: the newest of mtime and ctime over every entry, by
    lstat. ctime counts because `cp -a`, `rsync -a` and `tar x` set old mtimes on a fresh copy;
  - every directory in it could be read (an unreadable one leaves its activity unknown).
Inside such a folder only rebuildable directories go (named_rebuildable, holds_rebuildable),
and only in the shape their tool writes them: a generic name like build/ needs its tool's
output inside (SHAPES), and a cache that also keeps hand-written config loses only its cache
parts (.cargo/registry, never .cargo/config.toml). .git stays: history is the one thing in a
scratch copy that cannot be rebuilt, so a target holding a .git entry of any kind at any depth
(a clone, a worktree's .git file, a symlink) is left whole, and nothing inside a .git is ever a
target. Every other file stays.

Safety: the walk never follows a symlink, and every open below the root is relative to its
parent's descriptor with O_NOFOLLOW, so nothing outside the root is reached. Deletion is
shutil.rmtree with dir_fd (3.11+), which refuses a symlink and resists a swap mid-run; before
3.11, which lacks that keyword, _rmtree_at makes the same descriptor-relative walk. On a platform
without descriptor-relative calls (SAFE_DELETE False) nothing is deleted. Permission and other
OS errors are counted and skipped, never raised.

--auto (the hook's run) takes a lock in --state-dir, runs at most once per RUN_EVERY, writes
STATE_NAME, and writes REPORT_NAME only when it freed REPORT_MIN or more; doctor_brief shows
that report once, on the next lead session start.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT_ENV = "REPACT_SCRATCH_ROOT"
TMPDIR_ENV = "CLAUDE_CODE_TMPDIR"
STALE_DAYS = 3
RUN_EVERY = datetime.timedelta(hours=24)
REPORT_MIN = 1 << 30
STATE_NAME = "scratch-prune.json"           # equal to doctor_brief's (pinned by a test)
REPORT_NAME = "scratch-prune-report.json"   # equal to doctor_brief's (pinned by a test)
LOCK_NAME = "scratch-prune.lock"
SESSION_NAME = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
NAMED = object()  # survey's mark for a directory its parent found rebuildable by name
IN_GIT = object()  # survey's mark for a directory inside a .git: never a target
clock = time.time  # the staleness clock (tests move it)
RMTREE_DIR_FD = sys.version_info >= (3, 11)   # shutil.rmtree's dir_fd keyword
SAFE_DELETE = shutil.rmtree.avoids_symlink_attacks and (RMTREE_DIR_FD or (
    {os.open, os.stat, os.unlink, os.rmdir} <= os.supports_dir_fd and os.scandir in os.supports_fd))

# Rebuilt by an install or a build, whatever sits beside them.
ALWAYS = frozenset({
    "node_modules", ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".gradle",
    ".pnpm-store", ".rustup", ".nvm",
})
# Generic names, so only beside the project file whose build makes them.
PY_OR_JS = ("package.json", "pyproject.toml", "setup.py")
DOTNET = ("*.csproj", "*.fsproj", "*.vbproj")
BESIDE = {
    "dist": PY_OR_JS, "build": PY_OR_JS, "out": ("package.json",), ".output": ("package.json",),
    "target": ("Cargo.toml", "pom.xml"), "bin": DOTNET, "obj": DOTNET,
}
# A generic name whose project file can sit beside hand-written scripts too (a Vue 2 build/ of
# webpack configs): it goes only holding its tool's output, a web build's index.html or
# asset-manifest.json, or setuptools' lib*, bdist.* and temp.* folders.
_SETUPTOOLS = re.compile(r"(lib|bdist|temp)(\..+)?")
SHAPES = {
    "build": lambda dirs, files: bool({"index.html", "asset-manifest.json"} & set(files))
    or any(_SETUPTOOLS.fullmatch(name) for name in dirs),
}
# Package caches and virtualenvs, known by what they hold.
HOLDS = {".venv": "pyvenv.cfg", "venv": "pyvenv.cfg", ".npm": "_cacache", ".dotnet": "sdk"}
# Caches that also keep hand-written config (.cargo/config.toml, credentials.toml,
# .nuget/NuGet/NuGet.Config): known by the first name, only the named parts go.
CACHE_PARTS = {".cargo": ("registry", frozenset({"registry", "git"})),
               ".nuget": ("packages", frozenset({"packages"}))}


def has_marker(names, markers) -> bool:
    for marker in markers:
        if marker.startswith("*."):
            if any(name.endswith(marker[1:]) for name in names):
                return True
        elif marker in names:
            return True
    return False


def is_toolchain(dirfd: int, dirs, files) -> bool:
    """An SDK or toolchain install under any name: .NET (dotnet + sdk/ + shared/), Go (VERSION +
    bin/go + src/), or a Node.js tarball (bin/node + include/node/)."""
    if "dotnet" in files and {"sdk", "shared"} <= set(dirs):
        return True
    if "VERSION" in files and {"bin", "src", "pkg"} <= set(dirs):
        return _is_file(dirfd, "bin/go") and _is_dir(dirfd, "src/runtime")
    if {"bin", "include", "lib"} <= set(dirs):
        return _is_file(dirfd, "bin/node") and _is_dir(dirfd, "include/node")
    return False


def _lstat_at(dirfd: int, relpath: str):
    parent = dirfd
    parts = relpath.split("/")
    opened = []
    try:
        for part in parts[:-1]:
            parent = os.open(part, DIR_FLAGS, dir_fd=parent)
            opened.append(parent)
        return os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
    except OSError:
        return None
    finally:
        for fd in opened:
            os.close(fd)


def _is_file(dirfd: int, relpath: str) -> bool:
    st = _lstat_at(dirfd, relpath)
    return st is not None and stat.S_ISREG(st.st_mode)


def _is_dir(dirfd: int, relpath: str) -> bool:
    st = _lstat_at(dirfd, relpath)
    return st is not None and stat.S_ISDIR(st.st_mode)


def named_rebuildable(name: str, parent_name: str, parent_dirs, parent_files) -> bool:
    """Known by its name alone, by its name and a project file beside it, or as the cache part
    of a CACHE_PARTS folder."""
    if name in ALWAYS or (name in BESIDE and has_marker(parent_files, BESIDE[name])):
        return True
    marker, parts = CACHE_PARTS.get(parent_name, ("", ()))
    return name in parts and marker in parent_dirs


def has_shape(name: str, dirs, files) -> bool:
    """A directory named rebuildable holds what its tool writes (SHAPES; any other name does)."""
    shape = SHAPES.get(name)
    return shape is None or shape(dirs, files)


def holds_rebuildable(name: str, dirfd: int, dirs, files) -> bool:
    """Known by what it holds: `dirfd`, `dirs` and `files` are its own descriptor and listing."""
    if name in HOLDS and HOLDS[name] in set(dirs) | set(files):
        return True
    return is_toolchain(dirfd, dirs, files)


@dataclass
class Session:
    slug: str
    name: str
    skipped: str = ""                                   # why it was left alone, or ''
    targets: dict = field(default_factory=dict)         # relpath -> bytes on disk
    freed: int = 0
    errors: int = 0

    @property
    def size(self) -> int:
        return sum(self.targets.values())


def _usage(st, seen) -> int:
    if st.st_nlink > 1 and not stat.S_ISDIR(st.st_mode):
        key = (st.st_dev, st.st_ino)
        if key in seen:
            return 0
        seen.add(key)
    return st.st_blocks * 512


def survey(session_fd: int, cutoff: float, session: Session) -> None:
    """One walk: the newest activity (stopping at the first entry newer than `cutoff`), and the
    rebuildable directories with their bytes on disk. Never follows a symlink."""
    errors = []
    own = os.fstat(session_fd)
    if max(own.st_mtime, own.st_ctime) > cutoff:
        session.skipped = "active"
        return
    # dirpath -> the rebuildable dirpath that holds it, or None; NAMED when its parent's visit
    # found it rebuildable by name; IN_GIT inside a .git. Only a visited directory becomes a
    # target, and fwalk never visits a symlink, so a symlink named node_modules is never one.
    owner = {".": None}
    seen, holds_git = set(), set()
    walk = os.fwalk(".", dir_fd=session_fd, follow_symlinks=False, onerror=errors.append)
    try:
        for dirpath, dirs, files, dirfd in walk:
            holder = owner.pop(dirpath, None)
            name = dirpath.rpartition("/")[2]
            if holder is NAMED and not has_shape(name, dirs, files):
                holder = None
            if holder is NAMED or (holder is None and dirpath != "."
                                   and holds_rebuildable(name, dirfd, dirs, files)):
                holder = dirpath
                session.targets[holder] = 0
            if isinstance(holder, str) and ".git" in (*dirs, *files):
                holds_git.add(holder)      # any kind: directory, worktree file, symlink
            real_dirs, dir_names = [], set(dirs)
            for entry in (*dirs, *files):
                try:
                    st = os.stat(entry, dir_fd=dirfd, follow_symlinks=False)
                except OSError as exc:
                    errors.append(exc)
                    continue
                if max(st.st_mtime, st.st_ctime) > cutoff:
                    session.skipped = "active"
                    session.targets.clear()
                    return
                if isinstance(holder, str):
                    session.targets[holder] += _usage(st, seen)
                if stat.S_ISDIR(st.st_mode) and entry in dir_names:
                    real_dirs.append(entry)
            dirs[:] = real_dirs        # fwalk then never even opens a symlink's target
            for entry in dirs:
                if holder is None and entry == ".git":
                    owner[f"{dirpath}/{entry}"] = IN_GIT
                    continue
                named = holder is None and named_rebuildable(entry, name, dir_names, files)
                owner[f"{dirpath}/{entry}"] = NAMED if named else holder
    finally:
        walk.close()
    for holder in holds_git:
        del session.targets[holder]
    if errors:
        session.skipped = f"unreadable ({len(errors)} error(s))"
        session.targets.clear()


def remove(session_fd: int, relpath: str, uid: int) -> tuple[int, bool]:
    """Delete one surveyed directory. Returns (errors, gone). Each component is opened with
    O_NOFOLLOW relative to its parent; the last must be a real directory this user owns."""
    parts = relpath.split("/")[1:]                     # relpath starts with "./"
    if not parts or any(p in ("", ".", "..") for p in parts):
        return 1, False
    parent, opened = session_fd, []
    try:
        for part in parts[:-1]:
            parent = os.open(part, DIR_FLAGS, dir_fd=parent)
            opened.append(parent)
        st = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid:
            return 1, False
        failures = []
        if RMTREE_DIR_FD:
            handler = "onexc" if sys.version_info >= (3, 12) else "onerror"
            shutil.rmtree(parts[-1], dir_fd=parent, **{handler: lambda *args: failures.append(args)})
        else:
            try:
                _rmtree_at(parent, parts[-1], st, failures)
            except RecursionError as exc:      # deeper than the stack: what is left stays
                failures.append(exc)
        try:
            os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            gone = False
        except FileNotFoundError:
            gone = True
        return len(failures), gone
    except OSError:
        return 1, False
    finally:
        for fd in opened:
            os.close(fd)


def _rmtree_at(parent: int, name: str, expected, failures: list) -> None:
    """shutil.rmtree(name, dir_fd=parent) for Python before 3.11, the walk shutil makes there.
    Each directory is opened O_NOFOLLOW relative to its parent's descriptor and must be the entry
    just listed (`expected`, its lstat: same device and inode), so a directory swapped for a
    symlink or another directory mid-run fails the open or the check and is counted, never
    entered. Errors go to `failures`; nothing raises but RecursionError."""
    try:
        fd = os.open(name, DIR_FLAGS, dir_fd=parent)
    except OSError as exc:
        failures.append(exc)
        return
    try:
        if not os.path.samestat(expected, os.fstat(fd)):
            failures.append(OSError(f"{name} changed during the delete"))
            return
        with os.scandir(fd) as listing:
            entries = list(listing)
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                failures.append(exc)
                continue
            if stat.S_ISDIR(st.st_mode):
                _rmtree_at(fd, entry.name, st, failures)
            else:
                try:
                    os.unlink(entry.name, dir_fd=fd)
                except OSError as exc:
                    failures.append(exc)
    except OSError as exc:
        failures.append(exc)
        return
    finally:
        os.close(fd)
    try:
        os.rmdir(name, dir_fd=parent)
    except OSError as exc:
        failures.append(exc)


def remaining(session_fd: int, relpath: str) -> int:
    """Bytes still on disk under `relpath` after a partial delete."""
    total, seen = 0, set()
    parts = relpath.split("/")[1:]
    parent, opened = session_fd, []
    try:
        for part in parts:
            parent = os.open(part, DIR_FLAGS, dir_fd=parent)
            opened.append(parent)
        for _, dirs, files, dirfd in os.fwalk(".", dir_fd=parent, follow_symlinks=False):
            for entry in (*dirs, *files):
                try:
                    total += _usage(os.stat(entry, dir_fd=dirfd, follow_symlinks=False), seen)
                except OSError:
                    pass
    except OSError:
        pass
    finally:
        for fd in opened:
            os.close(fd)
    return total


def default_root() -> Path:
    override = os.environ.get(ROOT_ENV)
    if override:
        return Path(override)
    base = os.environ.get(TMPDIR_ENV) or "/tmp"
    return Path(os.path.realpath(base)) / f"claude-{os.getuid()}"


def config_dir() -> Path:
    config = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(config).expanduser() if config else Path.home() / ".claude"


def running_sessions() -> set:
    """Session ids Claude Code lists as running (<config>/sessions/<pid>.json). Best effort."""
    found = set()
    try:
        entries = list((config_dir() / "sessions").glob("*.json"))
    except OSError:
        return found
    for path in entries:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("sessionId"), str):
            found.add(data["sessionId"])
    return found


def open_root(root: Path, uid: int) -> tuple[int | None, str]:
    """A descriptor on the root, or (None, why not)."""
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, f"unreadable ({exc.strerror})"
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return None, "not a real directory"
    if st.st_uid != uid:
        return None, "not owned by this user"
    if st.st_mode & 0o022:
        return None, "writable by other users"
    try:
        fd = os.open(root, DIR_FLAGS)
    except OSError as exc:
        return None, f"unreadable ({exc.strerror})"
    if not os.path.samestat(st, os.fstat(fd)):
        os.close(fd)
        return None, "changed while opening"
    return fd, ""


def _owned_dirs(dirfd: int, uid: int, accept) -> list[str]:
    names = []
    try:
        with os.scandir(dirfd) as entries:
            for entry in entries:
                if not accept(entry.name):
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode) and st.st_uid == uid:
                    names.append(entry.name)
    except OSError:
        pass
    return sorted(names)


def prune(root: Path, apply: bool, days: float, skip: set) -> tuple[list, str]:
    """Survey (and with `apply`, prune) every session folder under `root`. Returns
    (sessions, why the root was refused or '')."""
    uid = os.getuid()
    root_fd, refused = open_root(root, uid)
    if root_fd is None:
        return [], refused
    if apply and not SAFE_DELETE:
        os.close(root_fd)
        return [], "this platform cannot delete relative to a directory descriptor"
    cutoff = clock() - days * 86400
    sessions = []
    try:
        for slug in _owned_dirs(root_fd, uid, lambda n: n.startswith("-")):
            try:
                slug_fd = os.open(slug, DIR_FLAGS, dir_fd=root_fd)
            except OSError:
                continue
            try:
                for name in _owned_dirs(slug_fd, uid, SESSION_NAME.fullmatch):
                    session = Session(slug, name)
                    sessions.append(session)
                    if name in skip:
                        session.skipped = "current or running"
                        continue
                    try:
                        session_fd = os.open(name, DIR_FLAGS, dir_fd=slug_fd)
                    except OSError:
                        session.skipped = "unreadable"
                        continue
                    try:
                        survey(session_fd, cutoff, session)
                        if apply and not session.skipped:
                            for relpath, size in session.targets.items():
                                errors, gone = remove(session_fd, relpath, uid)
                                session.errors += errors
                                session.freed += size if gone else max(0, size - remaining(session_fd, relpath))
                    finally:
                        os.close(session_fd)
            finally:
                os.close(slug_fd)
    finally:
        os.close(root_fd)
    return sessions, ""


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def report_lines(sessions: list, apply: bool, root: Path, refused: str) -> list[str]:
    if refused:
        return [f"scratch-prune: root {root} left alone: {refused}."]
    lines = []
    pruned = [s for s in sessions if not s.skipped and s.targets]
    for s in sorted(pruned, key=lambda s: -s.size):
        names = sorted({rel.rsplit("/", 1)[-1] for rel in s.targets})
        shown = ", ".join(names[:6]) + (", ..." if len(names) > 6 else "")
        amount = human(s.freed if apply else s.size)
        lines.append(f"  {amount:>9}  {s.slug}/{s.name}  ({len(s.targets)} dir(s): {shown})")
    total = sum(s.freed for s in pruned) if apply else sum(s.size for s in pruned)
    errors = sum(s.errors for s in sessions)
    stale = len([s for s in sessions if not s.skipped])
    verb = "freed" if apply else "dry run: would free"
    tail = "" if apply else "; run with --apply to delete"
    lines.append(f"scratch-prune: {verb} {human(total)} from {len(pruned)} of {stale} stale session "
                 f"folder(s) under {root} ({len(sessions) - stale} skipped as active, running or unreadable){tail}.")
    if errors:
        lines.append(f"scratch-prune: {errors} item(s) could not be deleted (permissions or in use); they were left.")
    return lines


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def auto(state_dir: Path, root: Path, skip: set) -> int:
    """The hook's run: once per RUN_EVERY, one at a time, silent, never failing."""
    import fcntl

    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / LOCK_NAME, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0                                   # another run holds it
        state = state_dir / STATE_NAME
        try:
            if time.time() - state.stat().st_mtime < RUN_EVERY.total_seconds():
                return 0
        except OSError:
            pass
        started = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        _write_json(state, {"started": started})
        sessions, refused = prune(root, apply=True, days=STALE_DAYS, skip=skip)
        pruned = [s for s in sessions if not s.skipped and s.targets]
        result = {"started": started, "root": str(root), "refused": refused,
                  "freed": sum(s.freed for s in pruned), "sessions": len(pruned),
                  "errors": sum(s.errors for s in sessions)}
        _write_json(state, result)
        if result["freed"] >= REPORT_MIN:
            _write_json(state_dir / REPORT_NAME, result)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="repact-scratch-prune", description=__doc__.split("\n")[2])
    parser.add_argument("--apply", action="store_true", help="delete (default: a dry run that only reports)")
    parser.add_argument("--root", type=Path, help=f"temp root (default: ${ROOT_ENV}, else claude-<uid> under /tmp)")
    parser.add_argument("--days", type=float, default=STALE_DAYS, help="untouched for this many days (min 1)")
    parser.add_argument("--skip-session", action="append", default=[], help="a session id never to touch")
    parser.add_argument("--auto", action="store_true", help="the session-start run: --apply, once a day, silent")
    parser.add_argument("--state-dir", type=Path, help="where --auto keeps its stamp, lock and report")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be 1 or more")
    if args.auto and not args.state_dir:
        parser.error("--auto needs --state-dir")
    root = args.root or default_root()
    skip = set(args.skip_session) | running_sessions()
    if args.auto:
        try:
            return auto(args.state_dir, root, skip)
        except Exception:  # detached and unattended: never a traceback, never a failure
            return 0
    sessions, refused = prune(root, args.apply, args.days, skip)
    sys.stdout.reconfigure(errors="backslashreplace")
    print("\n".join(report_lines(sessions, args.apply, root, refused)))
    return 1 if refused else 0
