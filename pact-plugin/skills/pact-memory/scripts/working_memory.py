"""Sync pact-memory records into the Working Memory and Retrieved Context sections of CLAUDE.md.

Keeps a rolling window of recent memories under token budgets, inside the PACT_MEMORY region of
the managed block. Writes go through file_lock and the containment-checked _atomic_write_text;
ambient (non-declared) writes are refused under pytest, from a redirected store, on a project-dir
disagreement or on a scope escape. Used by memory_api.py (sync_to_claude_md after a save,
sync_retrieved_to_claude_md after a search).

Map (file order): constants and twin markers; file_lock, line-ending helpers and
_atomic_write_text (drift-gated twins of hooks/shared/claude_md_manager.py); region and window
resolution; CLAUDE.md path resolution; token estimate, compression and budgets;
_sanitize_prompt_field (twin of hooks/shared/session_resume.py) and identifier checks; entry
formatting and section parsing; SyncResult and the ambient refusals; sync_to_claude_md,
project_memories_to_claude_md; retrieved-context parse, format and sync. Design notes:
the design notes.
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ask the config for the store origin; never re-derive it from PACT_TEST_MEMORY_DIR here.
try:
    from .config import STORE_ORIGIN_HOME, store_path_origin
except ImportError:
    from config import STORE_ORIGIN_HOME, store_path_origin

# Same dual import as above; pact_session carries the sys.path bootstrap and the session record.
try:
    from .pact_session import (
        ProjectScopeDisagreementError,
        env_record_project_dir_disagreement,
        format_project_dir_disagreement,
        get_project_dir_from_session_record,
        get_worktree_identity_from_session_record,
    )
except ImportError:
    from pact_session import (
        ProjectScopeDisagreementError,
        env_record_project_dir_disagreement,
        format_project_dir_disagreement,
        get_project_dir_from_session_record,
        get_worktree_identity_from_session_record,
    )

# Configure logging
logger = logging.getLogger(__name__)

# Working Memory: structured context synced from SQLite; coexists with the platform auto-memory.
WORKING_MEMORY_HEADER = "## Working Memory"
# The comment states no entry count: the newest entry alone can exceed the section budget.
WORKING_MEMORY_COMMENT = "<!-- Auto-managed by pact-memory skill. Full history searchable via pact-memory skill. Keyed by folder name, so another checkout with the same name shares this section. -->"
MAX_WORKING_MEMORIES = 3

# Constants for retrieved context section (searched/retrieved memories)
RETRIEVED_CONTEXT_HEADER = "## Retrieved Context"
RETRIEVED_CONTEXT_COMMENT = "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->"
MAX_RETRIEVED_MEMORIES = 3

# Token budget constants.
# Approximation: 1 token ~ 0.75 words, so word_count * 1.3 ~ token count.
WORKING_MEMORY_TOKEN_BUDGET = 800
RETRIEVED_CONTEXT_TOKEN_BUDGET = 500

# Summary characters _compress_memory_entry keeps; COMPRESSED_ENTRY_TOKEN_CEILING derives from it.
COMPRESSED_SUMMARY_CHAR_CAP = 120

# Estimated token cost of one compressed neighbour at its maximum (summary cap, date header and
# id line); an estimate, so it moves if the entry format changes.
COMPRESSED_ENTRY_TOKEN_CEILING = 128

# Prefix of the line carrying the durable record id; two writers and two readers must agree.
_MEMORY_ID_LABEL = "**Memory ID**"

# PACT boundary prefixes; a drift gate holds this copy equal to claude_md_manager's.
_PACT_BOUNDARY_ALT = "PACT_MEMORY_|PACT_MANAGED_|PACT_ROUTING_"

# Session-block prefix, deliberately not one of the PACT boundary prefixes.
_SESSION_BOUNDARY_ALT = "SESSION_"

# Derived from the prefix above, so a canonical rename reaches it through the drift gate.
_SESSION_END_MARKER = f"<!-- {_SESSION_BOUNDARY_ALT}END -->"

# Managed-region markers, drift-gated twins of claude_md_manager's.
_MANAGED_START_MARKER = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
_MANAGED_END_MARKER = "<!-- PACT_MANAGED_END -->"

# The inner memory-region markers, nested in the managed region; canonical names on purpose.
MEMORY_START_MARKER = "<!-- PACT_MEMORY_START -->"
MEMORY_END_MARKER = "<!-- PACT_MEMORY_END -->"

# file_lock twin of claude_md_manager.file_lock (drift-gated); same sidecar, same lock.
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL = 0.1


class LockDirError(OSError):
    """file_lock could not create or open its lock; the routed cause names it, paths stay out."""


# _sanitize_prompt_field twin of session_resume._sanitize_prompt_field (drift-gated, as are its
# constants).
_REFRESH_FIELD_TRUNCATION_LIMIT = 200
_REFRESH_PATH_TRUNCATION_LIMIT = 512

# Identifiers get their own bound: the free-text 200 is 3x what the generator emits.
_REFRESH_IDENTIFIER_TRUNCATION_LIMIT = 64

# Control characters collapsed in interpolated values: C0, DEL, C1 (incl. NEL) and U+2028/2029,
# anything that could break a value onto a new line.
_PROMPT_CONTROL_CHARS_RE = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]+")


@contextmanager
def file_lock(target_file: Path):
    """Acquire an exclusive sidecar file lock for a target CLAUDE.md path.

    Drift-gated twin of hooks/shared/claude_md_manager.file_lock (importing it would need the
    sys.path bootstrap pact_session.py carries); the body MUST stay byte-identical.

    NOT RE-ENTRANT (fcntl.flock): nesting the two sync sites under one target would self-deadlock
    until the fail-open TimeoutError. They are independent top-level calls, so no guard is added.
    """
    # Lock key = (st_dev, st_ino) of the resolved parent, where os.replace binds the leaf without
    # following it, + the literal leaf name: every spelling of that dir (case, Unicode form, symlink)
    # shares one lock; a missing parent keys on its path; hardlinks do not share, by design. The lock
    # dir depends on HOME only (never CLAUDE_CONFIG_DIR, which can switch per session), so writers
    # share it and it never lands in the repo where `git add -A` would stage it.
    parent = target_file.parent.resolve()
    try:
        st = parent.stat()
        lock_key = f"{st.st_dev}:{st.st_ino}/{target_file.name}"
    except OSError:
        lock_key = str(parent / target_file.name)
    state_home = os.environ.get("XDG_STATE_HOME", "")
    if not os.path.isabs(state_home):
        state_home = str(Path.home() / ".local" / "state")
    digest = hashlib.sha256(lock_key.encode("utf-8", "surrogateescape")).hexdigest()
    lock_path = Path(state_home) / "repacted" / "locks" / f"{digest}.lock"
    try:
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # 0o600: the lock file is keyed to user-private CLAUDE.md content.
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        print(f"PACT file_lock: lock dir {lock_path.parent} unwritable ({exc.strerror}); "
              "make it writable or set XDG_STATE_HOME", file=sys.stderr)
        raise LockDirError(exc.errno, f"lock dir unusable: {exc.strerror}", str(lock_path.parent)) from exc
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    # Warn before raising: callers fail open on TimeoutError, so a stuck holder would be silent.
                    print(
                        f"PACT file_lock timeout: failed to acquire "
                        f"lock on {lock_path} (for {target_file}) within "
                        f"{_LOCK_TIMEOUT_SECONDS}s; falling open",
                        file=sys.stderr,
                    )
                    raise TimeoutError(
                        f"Failed to acquire lock on {lock_path} within "
                        f"{_LOCK_TIMEOUT_SECONDS}s"
                    )
                time.sleep(_LOCK_POLL_INTERVAL)
        yield
    finally:
        # Release before close. flock is released automatically on fd close
        # by the kernel, but an explicit LOCK_UN ensures immediate release
        # even if close is delayed (e.g., by subsequent finalizer work).
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


# Why the sync sites lock the WHOLE read->mutate->write window (not just the
# write), shared by sync_to_claude_md and sync_retrieved_to_claude_md (each points here).
#   - read-under-lock is the load-bearing no-clobber property: a write-only lock
#     would let a 2nd writer read stale (pre-this-write) content, mutate it, and
#     overwrite this writer's entry the instant the lock releases — the exact
#     lost update the lock exists to prevent.
#   - lock identity is the sidecar keyed by the RESOLVED CLAUDE.md path, so this
#     serializes against session_init / session_resume: all writers resolve to
#     the same .claude/CLAUDE.md (CLAUDE_PROJECT_DIR is set every session → all
#     hit the env-var branch first) and thus share one lock in the per-user lock dir.
#   - CLAUDE_PROJECT_DIR edge: if it were ever unset AND the git-root/cwd
#     fallbacks diverged between processes, the sidecars would differ and the
#     lock would not serialize — accepted as out-of-contract (no safe fallback
#     action exists if the paths genuinely diverge).


class ContainmentError(OSError):
    """A CLAUDE.md write target escaped its project containment boundary (#1247).

    Subclasses OSError so a caller that does not name it explicitly still
    catches it via `except OSError`. Callers convert it to an OPAQUE skip
    message that does not leak the resolved victim path.

    Twin of ContainmentError in `hooks/shared/claude_md_manager.py` (importing
    it would require the sys.path bootstrap pact_session.py in this directory
    carries; the twin remains the chosen mechanism). The two class defs are
    trivial markers;
    the load-bearing logic is the containment CHECK inside `_atomic_write_text`,
    drift-gated by TestAtomicWriteTwinCopyDrift.
    """


def _detect_line_ending(name: str, parent_fd: int) -> str:
    """
    Report the line ending `name` uses today, read THROUGH `parent_fd`.

    Reads bytes (a text read has already translated CRLF), through the descriptor (no name race);
    the dominant ending wins, a tie, an absent target or a read failure reports LF. The full
    rationale is on the canonical.

    Twin copy: the canonical definition is in `hooks/shared/claude_md_manager.py`;
    importing it would require the sys.path bootstrap pact_session.py in this
    directory carries, the same consideration that keeps the `file_lock` and
    `_atomic_write_text` twins. The two bodies are gated identical by
    TestLineEndingHelperTwinCopyDrift.

    Args:
        name: The leaf name of the target, resolved against `parent_fd`.
        parent_fd: An open descriptor for the directory the write binds into.

    Returns:
        Either the two-character CRLF sequence or a single newline.
    """
    try:
        fd = os.open(name, os.O_RDONLY, dir_fd=parent_fd)
    except (OSError, NotImplementedError):
        return "\n"
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError:
        return "\n"
    finally:
        os.close(fd)
    raw = b"".join(chunks)
    crlf_count = raw.count(b"\r\n")
    lf_count = raw.count(b"\n") - crlf_count
    return "\r\n" if crlf_count > lf_count else "\n"


def _restore_line_ending(content: str, line_ending: str) -> str:
    """
    Apply `line_ending` to `content`, whatever endings `content` arrives with.

    IT NORMALISES FIRST, AND IT IS NOT SAFE WITHOUT THAT. An earlier form
    recorded a PRECONDITION that content reaches it with no carriage return in
    it, and applied a plain replace. That held while one caller fed it. At this
    seam the callers are ten and the seam cannot see where their content came
    from, so the precondition is a claim about callers rather than a property of
    this function. A plain replace on a string that carries CRLF gives a doubled
    carriage return, which is corruption of the user's own file.

    SO CRLF GOES BACK TO LF FIRST, AND THEN THE ENDING GOES ON. A caller that
    restores for itself is then a no-op rather than a corruption, which is a
    defect this function makes harmless rather than one it hides. The LF branch
    keeps its early return, so an LF file is byte-identical to what this wrote
    before.

    Twin copy: the canonical definition is in `hooks/shared/claude_md_manager.py`;
    importing it would require the sys.path bootstrap pact_session.py in this
    directory carries. The
    two bodies are gated identical by TestLineEndingHelperTwinCopyDrift.

    Args:
        content: Full file contents, with any line endings.
        line_ending: The ending to write, from `_detect_line_ending`.

    Returns:
        `content` with its endings replaced, or unchanged when the target is LF.
    """
    if line_ending == "\n":
        return content
    return content.replace("\r\n", "\n").replace("\n", line_ending)


def _atomic_write_text(target: Path, content: str, project_root: Path) -> None:
    """Replace `target`'s contents with `content` atomically, iff the directory
    the write will bind into is contained within `project_root` (#1247).

    `Path.write_text` truncates the file and THEN writes, so a crash, a full
    disk, or a kill between those two steps leaves a TRUNCATED CLAUDE.md. That
    file is gitignored and untracked in the projects this runs in, so there is
    no recovery path -- the user's pinned context is simply gone.

    Writing to a sibling temp file and renaming makes the replacement atomic: a
    reader sees either the whole old file or the whole new one, never a partial
    write. The temp file is created in the TARGET'S OWN DIRECTORY because
    `os.replace` is only atomic within a single filesystem.

    CONTAINMENT -- WHY THERE IS NO PATH RESOLVER HERE
    -------------------------------------------------

    PRECONDITION. This guard eliminates one class outright and narrows another.
    The class eliminated is disagreement between two path resolutions: only
    one traversal happens here and its result is held OPEN, so no second
    resolution exists to disagree with it. What is narrowed is time. The walk
    establishes ancestry AT THE INSTANT OF THE WALK, and a descriptor pins
    IDENTITY, not POSITION -- so containment holds provided the directory the
    write binds into does not change position relative to the anchor between
    the walk and the rename. Only the chain from that directory up to the
    anchor matters; relocating anything off it is irrelevant.

    An earlier form of this guard compared `str(target.resolve())` against the
    resolved root. `resolve()` follows every component INCLUDING the leaf; the
    write follows only the PARENT chain and then binds the leaf as a directory
    entry. Those are two independent traversals of two different path
    expressions, and on a two-leg symlink topology they name different
    directories -- so the guard certified a path the write never touched.

    The general defect is that `Path.resolve()` and `os.path.realpath` are
    SECOND implementations of path resolution, running in userspace, which the
    write does not use. A predicate built on one is sound exactly while the two
    agree, and the disagreement set (symlink loops, absent components) is
    discovered, never bounded. So this function asks the kernel instead:

      anchor  = (st_dev, st_ino) of os.stat(project_root)
      node    = the directory the write will bind into, held OPEN
      walk up via ".." comparing (st_dev, st_ino) until the anchor matches
      (CONTAINED) or a directory is its own parent (filesystem root, REFUSE)

    and then performs temp-create, fchmod, fsync, rename and cleanup THROUGH
    that same descriptor. The object CHECKED is the object MUTATED, by
    construction rather than by agreement. Consequences that fall out rather
    than being bolted on: version-invariant (no `Path.resolve()` exists here, so
    its 3.9-3.12 vs 3.13+ RuntimeError split is unreachable, not caught);
    strict (an absent parent raises instead of being lexically completed);
    immune to case-folding, Unicode normalisation form and trailing separators,
    because nothing is compared as text; and sibling-prefix (`/abc` under
    `/ab`) is refused structurally, since `/abc` never walks up to `/ab`'s
    inode.

    The parent is opened FOLLOWING symlinks -- see the inline note. Mounts
    beneath the project are ALLOWED: `..` from a mount root yields the mount
    POINT's parent, so the walk crosses one transparently, and the write still
    lands inside the anchor's subtree.

    THE LEAF IS NEVER CONSULTED, AND THAT COUPLES THIS TO THE WRITE SHAPE
    --------------------------------------------------------------------
    Containment is entirely a property of the parent chain, because the parent
    chain is the only part the kernel traverses on the way to the write. A leaf
    symlink pointing OUTSIDE the root is therefore ALLOWED, and that is the
    correct verdict, not a concession: `os.replace` is renameat(2), which
    unlinks whatever entry sits at the final name and binds the temp file's
    inode there. It never opens the leaf and never follows it, so the payload
    lands at the in-project entry and any outside victim is left byte-intact.

    That ALLOW is sound ONLY while the write replaces the leaf ENTRY rather
    than writing THROUGH it. Two rewrites would silently convert every such
    ALLOW into a real escape, with this predicate untouched: (1) resolving the
    target once and using it downstream -- proposed as a cheap way to make the
    check and the act agree, which it does, on the WRONG side, by making the
    write follow the leaf; (2) replacing temp-plus-rename with an open/truncate
    on the target. MEASURED, so the guarantee is stated at its real strength:
    with either rewrite spliced in, `TestR6InProjectRedirectResidualDocumented`
    turns RED on its victim-untouched assertion, so the in-project half of this
    coupling IS pinned by an executing test today. The out-of-project half is
    pinned separately, and could not be pinned at all before this predicate,
    because the earlier one refused that topology outright.

    Callers must already hold `file_lock` for the target. The lock closes the
    concurrent-writer window; this closes the crash/truncation window.

    Requires read+execute on the target's directory and write permission on it
    (to create the temp), where a bare `write_text` needed only permission on
    the file itself. A read-only directory holding a writable CLAUDE.md fails
    the write rather than truncating it -- a safe direction, but a real
    behaviour change.

    SCOPE LIMIT, stated because the natural summary of this change overclaims:
    `file_lock` runs BEFORE this function at every call site and does its own
    unprotected `target_file.resolve()`. Removing `Path.resolve()` from this
    guard makes the GUARD version-invariant; it does NOT make the write path
    version-invariant end-to-end, and a symlink loop still raises upstream.

    NOTE: a deliberate duplicate of `_atomic_write_text` in
    `hooks/shared/claude_md_manager.py`. Importing it would require the
    sys.path bootstrap pact_session.py in this directory carries, the same
    consideration that keeps the `file_lock` twin above. This twin IS
    drift-gated by
    TestAtomicWriteTwinCopyDrift: the
    containment CHECK is a security invariant that must not silently diverge
    between the hook and skill copies (#1118-class hazard). That gate compares
    only THIS function's body, which is why the check is inlined here rather
    than extracted -- a named helper would have to be twinned too, and would
    sit outside every drift gate in the repo.

    Args:
        target: Path to replace. Its parent directory must already exist.
        content: Full file contents to write.
        project_root: The trusted base directory `target` must be contained in.

    Raises:
        ContainmentError: the write's parent directory is not the project root
            or a descendant of it; or the boundary could not be established at
            all. Fail-CLOSED in every case, with a distinct message per cause.
    """
    # Refuse a missing anchor here: os.stat(str(None)) would use a directory named "None" as anchor.
    if project_root is None:
        raise ContainmentError(
            "refusing write: no containment anchor was supplied"
        )

    # #1247 CONTAINMENT, fail-CLOSED, BEFORE anything is created: kernel object
    # ancestry on a pinned directory descriptor. No Path.resolve(), no
    # os.path.realpath, no string comparison takes part in this decision.
    try:
        anchor_stat = os.stat(str(project_root))
        anchor_key = (anchor_stat.st_dev, anchor_stat.st_ino)
        # Follows symlinks like the kernel will; the ancestry test runs on this descriptor (no TOCTOU).
        parent_fd = os.open(str(target.parent), os.O_RDONLY | os.O_DIRECTORY)
    except (OSError, NotImplementedError):
        # Absent parent, non-directory, ELOOP and unsupported dir_fd (NotImplementedError) land here.
        raise ContainmentError(
            "refusing write: cannot establish the containment boundary"
        )

    walked = []
    try:
        # Inline literal (inside the twin-compared body); a liveness backstop, unreachable on sane filesystems.
        contained = False
        node = parent_fd
        for _ in range(1024):
            node_stat = os.fstat(node)
            if (node_stat.st_dev, node_stat.st_ino) == anchor_key:
                contained = True
                break
            try:
                up = os.open("..", os.O_RDONLY | os.O_DIRECTORY, dir_fd=node)
            except NotImplementedError:
                # Map only NotImplementedError (a RuntimeError, invisible to except OSError); a real OSError
                # propagates raw.
                raise ContainmentError(
                    "refusing write: platform lacks directory-descriptor "
                    "ancestry traversal"
                )
            walked.append(up)
            up_stat = os.fstat(up)
            if (up_stat.st_dev, up_stat.st_ino) == (
                node_stat.st_dev,
                node_stat.st_ino,
            ):
                # A directory that is its own parent is the filesystem root:
                # the walk is over and the anchor was never reached.
                break
            node = up
        else:
            raise ContainmentError(
                "refusing write: containment walk did not terminate"
            )
        if not contained:
            raise ContainmentError(
                "refusing write: target escapes the project containment boundary"
            )
    except BaseException:
        os.close(parent_fd)
        raise
    finally:
        for extra in walked:
            os.close(extra)

    try:
        # The seam keeps the target's line ending for every caller, read through parent_fd after the
        # containment walk (never by name).
        content = _restore_line_ending(
            content, _detect_line_ending(target.name, parent_fd)
        )
        tmp_name = f".{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(
                tmp_name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
                dir_fd=parent_fd,
            )
        except NotImplementedError:
            raise ContainmentError(
                "refusing write: platform lacks directory-descriptor file creation"
            )
        try:
            # os.fdopen takes ownership of fd only on success; if it raises, the
            # raw fd would leak (the cleanup below unlinks the temp FILE but
            # cannot close a descriptor it never received a handle for).
            try:
                # newline="": no translation, so the restored line ending is written as is; callers must not
                # restore again.
                handle = os.fdopen(fd, "w", encoding="utf-8", newline="")
            except BaseException:
                os.close(fd)
                raise
            with handle:
                handle.write(content)
                handle.flush()
                # fchmod on the open handle (no by-name race); makes the mode 0o600 regardless of umask.
                os.fchmod(handle.fileno(), 0o600)
                # Without the fsync the rename can be persisted while the data
                # behind it is not, which reintroduces the empty-file failure
                # this function exists to prevent.
                os.fsync(handle.fileno())
            try:
                os.replace(
                    tmp_name,
                    target.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            except NotImplementedError:
                raise ContainmentError(
                    "refusing write: platform lacks directory-descriptor rename"
                )
        except BaseException:
            # Best-effort temp removal: a stray file is preferable to losing the refusal reason.
            try:
                os.unlink(tmp_name, dir_fd=parent_fd)
            except (OSError, NotImplementedError):
                # Swallowed: an error here would replace the in-flight exception that explains the refusal.
                pass
            raise
    finally:
        os.close(parent_fd)


def extract_managed_region(content: str) -> Optional[Tuple[str, int]]:
    """Extract the PACT-managed region from CLAUDE.md content."""
    start_idx = content.find(_MANAGED_START_MARKER)
    if start_idx == -1:
        return None
    region_start = start_idx + len(_MANAGED_START_MARKER)
    end_idx = content.find(_MANAGED_END_MARKER, region_start)
    if end_idx == -1:
        return None
    return content[region_start:end_idx], region_start


def marker_line_span(text: str, literal: str) -> tuple[int, int] | None:
    """Span of the first line of `text` that IS `literal`, else None."""
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.strip() == literal:
            return offset, offset + len(line)
        offset += len(line)
    return None


def _resolve_write_window(content: str) -> tuple[str, int] | None:
    """Resolve the window a section write may search, or None to DECLINE."""
    region_result = extract_managed_region(content)
    if region_result is None:
        return content, 0

    narrowed = _narrow_to_memory_region(region_result[0], region_result[1])
    if narrowed is not None:
        return narrowed

    # Step 2 (managed-end then session-end markers) exists for availability, not security.
    region_text, region_start = region_result
    if _MANAGED_END_MARKER in content:
        session_end = marker_line_span(region_text, _SESSION_END_MARKER)
        if session_end is not None:
            inner_start = session_end[1]
            return region_text[inner_start:], region_start + inner_start

    # STEP 3.
    return None


def _narrow_to_memory_region(
    region_text: str, region_start: int
) -> tuple[str, int] | None:
    """Narrow an already-extracted managed region to the MEMORY region inside
    it, or None when the memory marker pair is not there.
    """
    start_span = marker_line_span(region_text, MEMORY_START_MARKER)
    if start_span is None:
        return None
    # The END of the marker line, so the window begins on the NEXT line and
    # `region_start` stays a line start for every offset computed below it.
    inner_start = start_span[1]
    tail = region_text[inner_start:]
    end_span = marker_line_span(tail, MEMORY_END_MARKER)
    if end_span is None:
        return None
    return tail[:end_span[0]], region_start + inner_start


def _find_existing_claude_md(base: Path) -> Optional[Path]:
    """Return the first existing CLAUDE.md under `base`, checking both
    supported locations in priority order.
    """
    dot_claude = base / ".claude" / "CLAUDE.md"
    if dot_claude.exists():
        return dot_claude
    legacy = base / "CLAUDE.md"
    if legacy.exists():
        return legacy
    return None


def _get_claude_md_path() -> Optional[Path]:
    """Get the path to CLAUDE.md in the project root."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        found = _find_existing_claude_md(Path(project_dir))
        if found is not None:
            return found

    # Session-record rung: below the env declaration, above the git/cwd derivations.
    record_dir = get_project_dir_from_session_record()
    if record_dir:
        found = _find_existing_claude_md(Path(record_dir))
        if found is not None:
            return found

    # Git fallback via --git-common-dir: inside a worktree --show-toplevel names the worktree, which
    # may not hold CLAUDE.md.
    from shared.project_scope import git_env_without_location

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
            env=git_env_without_location(),
        )
        if result.returncode == 0 and result.stdout.strip():
            common_dir = Path(result.stdout.strip())
            if not common_dir.is_absolute():
                common_dir = Path.cwd() / common_dir
            repo_root = common_dir.resolve().parent
            found = _find_existing_claude_md(repo_root)
            if found is not None:
                return found
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Last resort: current working directory
    return _find_existing_claude_md(Path.cwd())


def _resolve_display_claude_md_with_base() -> Tuple[Optional[Path], Optional[Path]]:
    """Resolve the display CLAUDE.md AND the trusted base directory it was found
    under, so a write caller can containment-check the target against the base
    the resolver actually used (#1247).
    """
    # Resolution must never raise into the sync path; on any failure (a bad
    # CLAUDE_PROJECT_DIR value, an inaccessible probe target, or a deleted cwd)
    # return (None, None) so the caller skips the sync and the save still succeeds.
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        if project_dir:
            base = Path(project_dir)
            found = _find_existing_claude_md(base)
            if found is not None:
                return found, base

        # Branch 1.5: session record (see the docstring's ordering). Same probe
        # shape as the env branch; a miss falls through to the git anchors.
        record_dir = get_project_dir_from_session_record()
        if record_dir:
            base = Path(record_dir)
            found = _find_existing_claude_md(base)
            if found is not None:
                return found, base

        # Worktree root via --show-toplevel, matching what session_init targets.
        from shared.project_scope import git_env_without_location

        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=5,
                env=git_env_without_location(),
            )
            if result.returncode == 0 and result.stdout.strip():
                worktree_root = Path(result.stdout.strip())
                found = _find_existing_claude_md(worktree_root)
                if found is not None:
                    return found, worktree_root
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Main-repo root via --git-common-dir: under .worktrees/ no session is rooted in the worktree.
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                capture_output=True,
                text=True,
                timeout=5,
                env=git_env_without_location(),
            )
            if result.returncode == 0 and result.stdout.strip():
                common_dir = Path(result.stdout.strip())
                if not common_dir.is_absolute():
                    common_dir = Path.cwd() / common_dir
                repo_root = common_dir.resolve().parent
                found = _find_existing_claude_md(repo_root)
                if found is not None:
                    return found, repo_root
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Last resort: current working directory
        cwd = Path.cwd()
        found = _find_existing_claude_md(cwd)
        return (found, cwd) if found is not None else (None, None)
    except Exception as e:
        logger.debug("display CLAUDE.md resolution failed, skipping sync: %s", e)
        return None, None


def _resolve_display_claude_md_path() -> Optional[Path]:
    """Resolve the CLAUDE.md the CURRENT SESSION displays (path only)."""
    return _resolve_display_claude_md_with_base()[0]


def _estimate_tokens(text: str) -> int:
    """Estimate token count for a text string."""
    if not text:
        return 0
    return int(len(text.split()) * 1.3)


def _compress_memory_entry(entry: str) -> str:
    """Compress a full memory entry to a date header, a summary and its key."""
    lines = entry.strip().split("\n")
    if not lines:
        return entry

    # Preserve the date header line (### YYYY-MM-DD HH:MM)
    date_line = lines[0]

    # Keep the recovery key line; an entry without an id keeps none.
    id_line = ""
    for line in lines[1:]:
        if line.startswith(_MEMORY_ID_LABEL):
            id_line = line
            break

    # Find the Context field and extract its first sentence
    summary_text = ""
    for line in lines[1:]:
        if line.startswith("**Context**:"):
            context_value = line.split("**Context**:", 1)[1].strip()
            # First sentence up to ". " (not ".", so v2.3.1 survives), or the character cap.
            period_idx = context_value.find(". ")
            if period_idx > 0 and period_idx < COMPRESSED_SUMMARY_CHAR_CAP:
                summary_text = context_value[:period_idx + 1]
            else:
                summary_text = context_value[:COMPRESSED_SUMMARY_CHAR_CAP]
                if len(context_value) > COMPRESSED_SUMMARY_CHAR_CAP:
                    summary_text += "..."
            break

    if not summary_text:
        # Fallback: use first non-header line content
        for line in lines[1:]:
            stripped = line.strip()
            if stripped and stripped.startswith("**") and "**:" in stripped:
                # Extract value from any bold field
                summary_text = (
                    stripped.split("**:", 1)[1].strip()[:COMPRESSED_SUMMARY_CHAR_CAP]
                )
                if len(stripped.split("**:", 1)[1].strip()) > COMPRESSED_SUMMARY_CHAR_CAP:
                    summary_text += "..."
                break

    # The id line goes last, the same shape as an uncompressed entry.
    tail = f"\n{id_line}" if id_line else ""
    if summary_text:
        return f"{date_line}\n**Summary**: {summary_text}{tail}"
    return f"{date_line}{tail}"


def _apply_entry_token_ceiling(entry: str, ceiling: int) -> str:
    """Cut ONE entry to a token ceiling by dropping whole field LINES."""
    if _estimate_tokens(entry) <= ceiling:
        return entry

    lines = entry.split("\n")
    if len(lines) <= 1:
        return entry

    # Index 0 is the date header. A `**Memory ID**` line is matched by
    # PREFIX wherever it sits, rather than by position, so the exemption
    # does not depend on it staying last.
    exempt = {0}
    for index, line in enumerate(lines):
        if line.startswith(_MEMORY_ID_LABEL):
            exempt.add(index)

    # DROP WHOLE LINES FIRST, from the end, and stop at ONE remaining
    # droppable line. That last line is handled below instead of dropped.
    kept = list(range(len(lines)))
    droppable = [i for i in reversed(range(len(lines))) if i not in exempt]

    # Every line exempt (date header and id lines only): return the entry whole.
    if not droppable:
        return entry

    for index in droppable[:-1] if droppable else []:
        if _estimate_tokens("\n".join(lines[i] for i in kept)) <= ceiling:
            break
        kept.remove(index)

    if _estimate_tokens("\n".join(lines[i] for i in kept)) <= ceiling:
        return "\n".join(lines[i] for i in kept)

    # Last resort: truncate the final droppable line in place rather than drop it, so a context-only
    # save keeps its context.
    last = droppable[-1]
    line = lines[last]
    fitted = list(kept)
    overhead = _estimate_tokens(
        "\n".join(lines[i] if i != last else "" for i in fitted)
    )
    budget_words = max(0, int((ceiling - overhead) / 1.3))

    # A field line keeps its complete `**Field**:` name or is absent.
    if budget_words < 1:
        fitted.remove(last)
        return "\n".join(lines[i] for i in fitted)

    words = line.split()
    truncated = " ".join(words[:budget_words])
    if len(truncated) < len(line):
        # Append the marker to the last kept word: it adds no word to the token estimate.
        truncated += "..."
    lines[last] = truncated
    return "\n".join(lines[i] for i in fitted)


def _apply_token_budget(
    entries: List[str],
    token_budget: int
) -> List[str]:
    """Apply a token budget to a list of memory entries."""
    if not entries:
        return entries

    # Reserve room for the compressed neighbours, then give the rest to the newest entry.
    entry_ceiling = (
        WORKING_MEMORY_TOKEN_BUDGET
        - (MAX_WORKING_MEMORIES - 1) * COMPRESSED_ENTRY_TOKEN_CEILING
    )
    entries = [_apply_entry_token_ceiling(e, entry_ceiling) for e in entries]

    # Check if already within budget
    total_tokens = sum(_estimate_tokens(e) for e in entries)
    if total_tokens <= token_budget:
        return entries

    # Strategy: keep newest entry full, compress the rest
    result = [entries[0]]
    for entry in entries[1:]:
        compressed = _compress_memory_entry(entry)
        result.append(compressed)

    # Check if compressed version fits
    total_tokens = sum(_estimate_tokens(e) for e in result)
    if total_tokens <= token_budget:
        return result

    # Still over budget: drop entries from the end until we fit.
    # Subtract the popped entry's tokens instead of recalculating the full sum.
    while len(result) > 1 and total_tokens > token_budget:
        removed = result.pop()
        total_tokens -= _estimate_tokens(removed)

    return result


def _sanitize_prompt_field(
    value: str,
    limit: int = _REFRESH_FIELD_TRUNCATION_LIMIT,
) -> str:
    """Sanitize a record field value for interpolation into CLAUDE.md.

    Twin of hooks/shared/session_resume._sanitize_prompt_field — kept local
    as a drift-gated twin; importing the canonical would require the sys.path
    bootstrap pact_session.py in this directory carries.
    Body MUST stay byte-identical to the canonical copy (drift test enforces
    this); this docstring is allowed to differ. Change either copy and you
    change both in the SAME commit.

    Collapses control characters to single spaces, strips, and bounds the
    length. Callers MUST sanitize BEFORE they test the value for
    truthiness: an internal failure returns ``""`` so the caller drops that
    field's LINE, and a test of the RAW value would emit the field label
    with an empty value instead.
    """
    try:
        cleaned = _PROMPT_CONTROL_CHARS_RE.sub(" ", value).strip()
        if len(cleaned) > limit:
            cleaned = cleaned[:limit - 3] + "..."
        return cleaned
    except Exception:
        return ""


def _recover_identifier(raw: str) -> str:
    """Accept a raw identifier for the recovery-pointer fallback, or refuse it."""
    if not raw or len(raw) > _REFRESH_IDENTIFIER_TRUNCATION_LIMIT:
        return ""
    for character in raw:
        if not (character.isalnum() or character in "-_."):
            return ""
    return raw


def _record_timestamp(value: Any) -> Optional[datetime]:
    """Parse a record's `created_at` as stored or as `to_dict()` emits it."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _format_memory_entry(
    memory: Dict[str, Any],
    files: Optional[List[str]] = None,
    memory_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> str:
    """Format a memory as a markdown entry for CLAUDE.md."""
    # Get date and time for header
    stamp = created_at if created_at is not None else datetime.now(timezone.utc)
    date_str = stamp.strftime("%Y-%m-%d %H:%M")

    lines = [f"### {date_str}"]

    # Sanitize each value before testing truthiness: the sanitizer returns "" on failure.

    # Add context if present
    context = _sanitize_prompt_field(str(memory.get("context") or ""))
    if context:
        lines.append(f"**Context**: {context}")

    # Add goal if present
    goal = _sanitize_prompt_field(str(memory.get("goal") or ""))
    if goal:
        lines.append(f"**Goal**: {goal}")

    # Add decisions if present
    decisions = memory.get("decisions")
    if decisions:
        if isinstance(decisions, list):
            # Extract decision text from list of dicts or strings
            decision_texts = []
            for d in decisions:
                if isinstance(d, dict):
                    decision_texts.append(d.get("decision", str(d)))
                else:
                    decision_texts.append(str(d))
            # Sanitize the JOINED value, not each item: the join is what
            # reaches the file, and a per-item bound would let N items
            # multiply past the line bound the sanitize exists to set.
            joined = _sanitize_prompt_field(", ".join(str(t) for t in decision_texts))
            if joined:
                lines.append(f"**Decisions**: {joined}")
        elif isinstance(decisions, str):
            cleaned = _sanitize_prompt_field(decisions)
            if cleaned:
                lines.append(f"**Decisions**: {cleaned}")

    # Add lessons if present
    lessons = memory.get("lessons_learned")
    if lessons:
        if isinstance(lessons, list) and lessons:
            joined = _sanitize_prompt_field(", ".join(str(l) for l in lessons))
            if joined:
                lines.append(f"**Lessons**: {joined}")
        elif isinstance(lessons, str):
            cleaned = _sanitize_prompt_field(lessons)
            if cleaned:
                lines.append(f"**Lessons**: {cleaned}")

    # Add reasoning chains if present
    reasoning = memory.get("reasoning_chains")
    if reasoning:
        if isinstance(reasoning, list) and reasoning:
            joined = _sanitize_prompt_field(", ".join(str(r) for r in reasoning))
            if joined:
                lines.append(f"**Reasoning chains**: {joined}")
        elif isinstance(reasoning, str):
            cleaned = _sanitize_prompt_field(reasoning)
            if cleaned:
                lines.append(f"**Reasoning chains**: {cleaned}")

    # Add agreements if present
    agreements = memory.get("agreements_reached")
    if agreements:
        if isinstance(agreements, list) and agreements:
            joined = _sanitize_prompt_field(", ".join(str(a) for a in agreements))
            if joined:
                lines.append(f"**Agreements**: {joined}")
        elif isinstance(agreements, str):
            cleaned = _sanitize_prompt_field(agreements)
            if cleaned:
                lines.append(f"**Agreements**: {cleaned}")

    # Add disagreements resolved if present
    disagreements = memory.get("disagreements_resolved")
    if disagreements:
        if isinstance(disagreements, list) and disagreements:
            joined = _sanitize_prompt_field(", ".join(str(d) for d in disagreements))
            if joined:
                lines.append(f"**Disagreements resolved**: {joined}")
        elif isinstance(disagreements, str):
            cleaned = _sanitize_prompt_field(disagreements)
            if cleaned:
                lines.append(f"**Disagreements resolved**: {cleaned}")

    # Add files if present
    if files:
        # A path field, so the wider bound: legitimate absolute paths are long.
        joined_files = _sanitize_prompt_field(
            ", ".join(str(f) for f in files), _REFRESH_PATH_TRUNCATION_LIMIT
        )
        if joined_files:
            lines.append(f"**Files**: {joined_files}")

    # Add memory ID if provided
    if memory_id:
        # An identifier, not free text: use the identifier bound.
        raw_id = str(memory_id)
        cleaned_id = _sanitize_prompt_field(
            raw_id, _REFRESH_IDENTIFIER_TRUNCATION_LIMIT
        )
        # The sanitizer can drop the recovery pointer ("" on failure or all-control input); fall back to
        # the checked raw identifier so the pointer survives.
        if raw_id and (
            not cleaned_id
            or len(raw_id) > _REFRESH_IDENTIFIER_TRUNCATION_LIMIT
        ):
            cleaned_id = _recover_identifier(raw_id)
        if cleaned_id:
            lines.append(f"{_MEMORY_ID_LABEL}: {cleaned_id}")

    return "\n".join(lines)


def _find_terminator_offset(
    content: str,
    start: int,
    terminator_pattern: "re.Pattern[str]",
) -> int:
    """Find the absolute offset of the first line matching `terminator_pattern`."""
    pos = start
    while pos < len(content):
        nl = content.find("\n", pos)
        if nl == -1:
            line = content[pos:]
            line_end = len(content)
        else:
            line = content[pos:nl]
            line_end = nl + 1

        if terminator_pattern.match(line):
            return pos

        pos = line_end

    return len(content)


def _parse_working_memory_section(
    content: str
) -> Optional[Tuple[str, str, str, List[str]]]:
    """Parse CLAUDE.md content to extract working memory section."""
    # Bound to the memory region, not the managed region: the session block above it holds
    # caller-influenced values.
    window = _resolve_write_window(content)
    if window is None:
        return None
    scan_text, offset = window

    # The negative lookahead keeps a boundary marker from being consumed as the auto-managed comment.
    section_pattern = re.compile(
        r'^(## Working Memory)\s*\n'
        rf'(<!-- (?!(?:{_PACT_BOUNDARY_ALT}|{_SESSION_BOUNDARY_ALT}))'
        r'[^>]*-->)?\s*\n?',
        re.MULTILINE
    )

    match = section_pattern.search(scan_text)

    if not match:
        # Section doesn't exist
        return content, "", "", []

    section_start = match.start() + offset
    section_header_end = match.end()

    # Find where the next ## section starts (end of working memory section).
    # No fence-awareness needed — managed region contains only plugin-generated
    # content (round 10 structural guarantee).
    next_section_pattern = re.compile(
        rf'(#\s|##\s(?!Working Memory)|---|'
        rf'<!-- (?:{_PACT_BOUNDARY_ALT}|{_SESSION_BOUNDARY_ALT}))',
    )
    section_end_rel = _find_terminator_offset(
        scan_text, section_header_end, next_section_pattern
    )
    section_end = section_end_rel + offset

    before_section = content[:section_start]
    section_content = scan_text[section_header_end:section_end_rel].strip()
    after_section = content[section_end:]

    # Parse existing entries (each starts with ### YYYY-MM-DD)
    entry_pattern = re.compile(r'^### \d{4}-\d{2}-\d{2}', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(section_content)]

    existing_entries = []
    for i, start in enumerate(entry_starts):
        if i + 1 < len(entry_starts):
            entry = section_content[start:entry_starts[i + 1]].strip()
        else:
            entry = section_content[start:].strip()
        existing_entries.append(entry)

    return before_section, WORKING_MEMORY_HEADER, after_section, existing_entries


def _project_root_of(claude_md_path: Path) -> Path:
    """Return the project directory that owns `claude_md_path`."""
    parent = claude_md_path.parent
    return parent.parent if parent.name == ".claude" else parent


class SyncResult:
    """Outcome of a working-memory sync: DID IT WRITE, and WHY NOT."""

    __slots__ = ("wrote", "reason")

    # Reasons. WROTE is the only one for which `bool()` is True.
    WROTE = "wrote"
    REFUSED = "refused"          # guard declined; raised, then caught upstream
    SUPPRESSED = "suppressed"    # caller passed sync_to_claude=False
    UNRESOLVED = "unresolved"    # no CLAUDE.md resolved
    MISSING = "missing"          # resolved a path that does not exist
    FAILED = "failed"            # the write itself raised
    EMPTY = "empty"              # nothing to write; caller passed no entries
    # NO_WINDOW is its own reason so a reader sees which class of document stopped being written.
    NO_WINDOW = "no_window"      # no write window resolved; see _resolve_write_window

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.wrote = reason == self.WROTE

    def __bool__(self) -> bool:
        return self.wrote

    def __repr__(self) -> str:
        return f"SyncResult({self.reason!r})"

    def __eq__(self, other) -> bool:
        if isinstance(other, SyncResult):
            return self.reason == other.reason
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.reason)


class AmbientSyncRefused(RuntimeError):
    """Raised when a test process would sync to an ambiently-resolved CLAUDE.md."""


def _refuse_ambient_sync_on_project_dir_disagreement(
    target: Optional[Path],
    claude_md_root: Optional[Path] = None,
) -> None:
    """Refuse an AMBIENT working-memory sync when CLAUDE_PROJECT_DIR and the
    session record name different project directories.
    """
    if target is not None:
        return
    if claude_md_root is not None:
        return
    disagreement = env_record_project_dir_disagreement()
    if disagreement is None:
        return
    raise ProjectScopeDisagreementError(
        format_project_dir_disagreement(*disagreement)
    )


def _refuse_ambient_sync_on_declared_scope_escape(
    target: Optional[Path],
    claude_md_root: Optional[Path],
    resolved_root: Optional[Path],
    claude_md_path: Optional[Path],
) -> None:
    """Refuse an AMBIENT sync that resolved OUTSIDE its declared scope."""
    if target is not None or claude_md_root is not None:
        return
    if resolved_root is None or claude_md_path is None:
        return
    declared = os.environ.get("CLAUDE_PROJECT_DIR") or (
        get_project_dir_from_session_record() or ""
    )
    if not declared:
        return
    # Function-level: the shared package is importable only after
    # pact_session's sys.path bootstrap has run at module import.
    from shared.project_scope import stays_in_declared_project

    if stays_in_declared_project(
        Path(declared),
        Path(resolved_root),
        Path(claude_md_path),
        worktree_identity=get_worktree_identity_from_session_record(),
    ):
        return
    raise AmbientSyncRefused(
        f"the declared project scope ({declared}) resolved to a CLAUDE.md "
        f"under a different project ({resolved_root}); the projection was "
        "refused. Point CLAUDE_PROJECT_DIR at the project you mean, or pass "
        "an explicit target= / claude_md_root= to name the destination."
    )


def _refuse_ambient_target_under_pytest(
    target: Optional[Path],
    claude_md_root: Optional[Path] = None,
) -> None:
    """Refuse an AMBIENT working-memory sync when a TEST PROCESS spawned us."""
    if target is not None:
        return
    if claude_md_root is not None:
        return
    if "pytest" in sys.modules:
        return
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    raise AmbientSyncRefused(
        "refusing to sync working memory to an ambiently-resolved CLAUDE.md: "
        "PYTEST_CURRENT_TEST is set in this process's environment, so the "
        "destination would be the operator's live file. Pass an explicit "
        "target=, or use the CLI's --no-sync flag."
    )


def _target_is_inside_the_declared_project_dir(resolved_target: Path) -> bool:
    """Report whether resolution landed inside the caller's declared project dir."""
    declared = os.environ.get("CLAUDE_PROJECT_DIR")
    if not declared:
        return False
    try:
        Path(resolved_target).resolve().relative_to(Path(declared).resolve())
    except (ValueError, OSError):
        return False
    return True


def _refuse_ambient_sync_from_a_redirected_store(
    target: Optional[Path],
    claude_md_root: Optional[Path] = None,
    resolved_target: Optional[Path] = None,
) -> None:
    """Refuse an AMBIENT sync when the ROW went to a store that is not the default."""
    if target is not None:
        return
    if claude_md_root is not None:
        return
    if "pytest" in sys.modules:
        return
    origin = store_path_origin()
    if origin == STORE_ORIGIN_HOME:
        return
    # Last, and only with a resolved path: the declared-dir exemption must be earned.
    if resolved_target is not None and _target_is_inside_the_declared_project_dir(
        resolved_target
    ):
        return
    # THE ORIGIN WORD, NEVER THE PATH. `origin` is one of a closed set of words
    # ("scope", "environment"), so it names the redirect without putting a
    # filesystem path into a message that reaches a log and a caller.
    raise AmbientSyncRefused(
        "refusing to sync working memory to an ambiently-resolved CLAUDE.md: "
        f"the memory store is redirected (origin={origin}), so this entry "
        "would display in a file that does not read from the store that holds "
        "it. Pass an explicit target=, declare claude_md_root=, or use the "
        "CLI's --no-sync flag."
    )


def sync_to_claude_md(
    memory: Optional[Dict[str, Any]],
    files: Optional[List[str]] = None,
    memory_id: Optional[str] = None,
    target: Optional[Path] = None,
    claude_md_root: Optional[Path] = None,
    *,
    entries: Optional[List[str]] = None,
) -> "SyncResult":
    """Sync one memory entry into the Working Memory section of CLAUDE.md.

    Keeps at most MAX_WORKING_MEMORIES entries, newest first, under the section token budget.
    `entries` replaces the parsed section (used by project_memories_to_claude_md). `target` is an
    explicit path (never created); `claude_md_root` is a declared containment anchor that does not
    steer resolution, refuses a target outside it, and exempts the ambient refusals. Returns a
    SyncResult (truthy iff written, `.reason` always set). Ambient refusals raise
    AmbientSyncRefused or ProjectScopeDisagreementError instead of returning.
    """
    _refuse_ambient_target_under_pytest(target, claude_md_root)
    _refuse_ambient_sync_on_project_dir_disagreement(target, claude_md_root)

    if target is not None:
        claude_md_path = Path(target)
        resolved_root = _project_root_of(claude_md_path)
    else:
        claude_md_path, resolved_root = _resolve_display_claude_md_with_base()
    # Escape guard runs AFTER resolution because it needs the resolved
    # root; the guards above run before because they need only the
    # declaration. Same ordering in the sibling.
    _refuse_ambient_sync_on_declared_scope_escape(
        target, claude_md_root, resolved_root, claude_md_path
    )

    # The declared anchor replaces the containment base; it does not steer resolution.
    project_root = (
        Path(claude_md_root) if claude_md_root is not None else resolved_root
    )

    # The resolver returns two paths or two Nones; check both halves anyway.
    if claude_md_path is None or project_root is None:
        logger.debug("CLAUDE.md not found, skipping working memory sync")
        return SyncResult(SyncResult.UNRESOLVED)

    # Existence guard below the join, so it covers the explicit and ambient branches.
    if not claude_md_path.exists():
        if target is not None:
            logger.debug(
                "explicit sync target %s does not exist, skipping working "
                "memory sync (this never creates CLAUDE.md)", claude_md_path
            )
        else:
            logger.warning(
                "resolved display CLAUDE.md %s does not exist, skipping "
                "working memory sync (this never creates CLAUDE.md). Either "
                "the display resolver stopped returning only existing paths, "
                "or the file was removed after it resolved.", claude_md_path
            )
        return SyncResult(SyncResult.MISSING)

    # Redirected-store refusal: needs the resolved path, and must run before the lock.
    _refuse_ambient_sync_from_a_redirected_store(
        target, claude_md_root, claude_md_path
    )

    try:
        # Serialize the FULL read-modify-write window under the shared sidecar
        # lock (see the "why lock the whole window" note above file_lock for the
        # read-under-lock / lock-identity / CLAUDE_PROJECT_DIR rationale).
        with file_lock(claude_md_path):
            # Read current content
            content = claude_md_path.read_text(encoding="utf-8")

            # Parse existing working memory section
            parsed = _parse_working_memory_section(content)
            if parsed is None:
                # Decline loudly: bool() is False and .reason names the cause.
                return SyncResult(SyncResult.NO_WINDOW)
            before_section, section_header, after_section, existing_entries = parsed

            if entries is None:
                # Format new memory entry, then prepend it to the file's own.
                # A None here is a caller error; the raise lands in the
                # handler below as FAILED, the same as any other bad input.
                assert memory is not None, "the prepend arm needs a memory"
                new_entry = _format_memory_entry(memory, files, memory_id)
                all_entries = [new_entry] + existing_entries
            else:
                # REPLACE: the file's entries are not consulted
                all_entries = list(entries)
            trimmed_entries = all_entries[:MAX_WORKING_MEMORIES]

            # Apply token budget: compress older entries if over budget
            trimmed_entries = _apply_token_budget(
                trimmed_entries, WORKING_MEMORY_TOKEN_BUDGET
            )

            # Build new section content
            section_lines = [
                WORKING_MEMORY_HEADER,
                WORKING_MEMORY_COMMENT,
                ""  # Blank line after comment
            ]
            for entry in trimmed_entries:
                section_lines.append(entry)
                section_lines.append("")  # Blank line between entries

            section_text = "\n".join(section_lines)

            # Reconstruct file content
            if section_header:
                # Section existed, replace it
                new_content = before_section + section_text + after_section
            else:
                # Section didn't exist, append at end
                if not content.endswith("\n"):
                    content += "\n"
                new_content = content + "\n" + section_text

            # Write back to file (atomic: temp + rename, so a crash mid-write
            # cannot leave the always-loaded CLAUDE.md truncated)
            _atomic_write_text(claude_md_path, new_content, project_root)

        logger.info("Synced memory to CLAUDE.md Working Memory section")
        return SyncResult(SyncResult.WROTE)

    except Exception as e:
        logger.warning(f"Failed to sync memory to CLAUDE.md: {e}")
        return SyncResult(SyncResult.FAILED)


def project_memories_to_claude_md(
    memories: List[Dict[str, Any]],
    target: Optional[Path] = None,
    claude_md_root: Optional[Path] = None,
) -> "SyncResult":
    """Replace the Working Memory section with `memories`, newest first."""
    if not memories:
        return SyncResult(SyncResult.EMPTY)
    entries = [
        _format_memory_entry(
            m, m.get("files") or None, m.get("id"),
            created_at=_record_timestamp(m.get("created_at")),
        )
        for m in memories[:MAX_WORKING_MEMORIES]
    ]
    return sync_to_claude_md(
        None, target=target, claude_md_root=claude_md_root, entries=entries
    )


def _parse_retrieved_context_section(
    content: str
) -> Optional[Tuple[str, str, str, List[str]]]:
    """Parse CLAUDE.md content to extract retrieved context section."""
    # Bound to the memory region, as in _parse_working_memory_section.
    window = _resolve_write_window(content)
    if window is None:
        return None
    scan_text, offset = window

    # Pattern to find the Retrieved Context section.
    # Negative lookahead narrows to the plugin-managed boundary prefixes
    # — see _parse_working_memory_section for the full rationale (#404).
    section_pattern = re.compile(
        r'^(## Retrieved Context)\s*\n'
        rf'(<!-- (?!(?:{_PACT_BOUNDARY_ALT}|{_SESSION_BOUNDARY_ALT}))'
        r'[^>]*-->)?\s*\n?',
        re.MULTILINE
    )

    match = section_pattern.search(scan_text)

    if not match:
        # Section doesn't exist
        return content, "", "", []

    section_start = match.start() + offset
    section_header_end = match.end()

    # Find where the next ## section starts (end of retrieved context section).
    # No fence-awareness needed — managed region contains only plugin-generated
    # content (round 10 structural guarantee).
    next_section_pattern = re.compile(
        rf'(#\s|##\s(?!Retrieved Context)|---|'
        rf'<!-- (?:{_PACT_BOUNDARY_ALT}|{_SESSION_BOUNDARY_ALT}))',
    )
    section_end_rel = _find_terminator_offset(
        scan_text, section_header_end, next_section_pattern
    )
    section_end = section_end_rel + offset

    before_section = content[:section_start]
    section_content = scan_text[section_header_end:section_end_rel].strip()
    after_section = content[section_end:]

    # Parse existing entries (each starts with ### YYYY-MM-DD)
    entry_pattern = re.compile(r'^### \d{4}-\d{2}-\d{2}', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(section_content)]

    existing_entries = []
    for i, start in enumerate(entry_starts):
        if i + 1 < len(entry_starts):
            entry = section_content[start:entry_starts[i + 1]].strip()
        else:
            entry = section_content[start:].strip()
        existing_entries.append(entry)

    return before_section, RETRIEVED_CONTEXT_HEADER, after_section, existing_entries


def _format_retrieved_entry(
    memory: Dict[str, Any],
    query: str,
    score: Optional[float] = None,
    memory_id: Optional[str] = None
) -> str:
    """Format a retrieved memory as a markdown entry for CLAUDE.md."""
    # Get date and time for header
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    lines = [f"### {date_str}"]
    # The query is caller-supplied text, so it is sanitized like every other
    # interpolated value. It is NOT guarded by a truthiness test: an empty
    # query renders an empty pair of quotes, which is the pre-fix behaviour.
    lines.append(f"**Query**: \"{_sanitize_prompt_field(str(query or ''))}\"")

    if score is not None:
        lines.append(f"**Relevance**: {score:.2f}")

    # Sanitize at the shared limit; the hand-rolled truncation here disagreed with it.
    context = _sanitize_prompt_field(str(memory.get("context") or ""))
    if context:
        lines.append(f"**Context**: {context}")

    # Add goal if present
    goal = _sanitize_prompt_field(str(memory.get("goal") or ""))
    if goal:
        lines.append(f"**Goal**: {goal}")

    # Add memory ID if provided
    if memory_id:
        # An identifier, not free text: use the identifier bound.
        raw_id = str(memory_id)
        cleaned_id = _sanitize_prompt_field(
            raw_id, _REFRESH_IDENTIFIER_TRUNCATION_LIMIT
        )
        # The sanitizer can drop the recovery pointer; fall back to the checked raw identifier.
        if raw_id and (
            not cleaned_id
            or len(raw_id) > _REFRESH_IDENTIFIER_TRUNCATION_LIMIT
        ):
            cleaned_id = _recover_identifier(raw_id)
        if cleaned_id:
            lines.append(f"{_MEMORY_ID_LABEL}: {cleaned_id}")

    return "\n".join(lines)


def sync_retrieved_to_claude_md(
    memories: List[Dict[str, Any]],
    query: str,
    scores: Optional[List[float]] = None,
    memory_ids: Optional[List[str]] = None,
    claude_md_root: Optional[Path] = None
) -> SyncResult:
    """Sync retrieved memories into the Retrieved Context section of CLAUDE.md.

    Keeps the last MAX_RETRIEVED_MEMORIES, newest first. Reached from PACTMemory.search() by
    default, so the ambient refusals apply here too; `claude_md_root` is the declared anchor, as on
    sync_to_claude_md. Returns a SyncResult (truthy iff written).
    """
    if not memories:
        return SyncResult(SyncResult.EMPTY)

    # Same ambient refusal as the save path; the declared anchor is the only exemption here.
    _refuse_ambient_target_under_pytest(None, claude_md_root)

    # Disagreement guard too; its raise lands in the warning swallow at the tail.
    _refuse_ambient_sync_on_project_dir_disagreement(None, claude_md_root)

    claude_md_path, resolved_root = _resolve_display_claude_md_with_base()
    _refuse_ambient_sync_on_declared_scope_escape(
        None, claude_md_root, resolved_root, claude_md_path
    )

    # Declared anchor replaces the containment base; it does not steer
    # resolution. The two Nones stay in separate variables for the same reason
    # they do in the sibling.
    project_root = (
        Path(claude_md_root) if claude_md_root is not None else resolved_root
    )

    if claude_md_path is None or project_root is None:
        logger.debug("CLAUDE.md not found, skipping retrieved context sync")
        return SyncResult(SyncResult.UNRESOLVED)

    # Existence guard: a total resolver returns a path that may not exist.
    if not claude_md_path.exists():
        logger.warning(
            "resolved display CLAUDE.md %s does not exist, skipping retrieved "
            "context sync (this never creates CLAUDE.md). Either the display "
            "resolver stopped returning only existing paths, or the file was "
            "removed after it resolved.", claude_md_path
        )
        return SyncResult(SyncResult.MISSING)

    # Same position and same reasons as in the sibling: below resolution because
    # it judges the RESOLVED destination, above `file_lock` because that call
    # creates directories.
    _refuse_ambient_sync_from_a_redirected_store(
        None, claude_md_root, claude_md_path
    )

    try:
        # Serialize the FULL read-modify-write window under the shared sidecar
        # lock (see the "why lock the whole window" note above file_lock for the
        # read-under-lock / lock-identity / CLAUDE_PROJECT_DIR rationale).
        with file_lock(claude_md_path):
            # Read current content
            content = claude_md_path.read_text(encoding="utf-8")

            # Parse existing retrieved context section
            parsed = _parse_retrieved_context_section(content)
            if parsed is None:
                # The same decline as the sibling writer, for the same cause.
                return SyncResult(SyncResult.NO_WINDOW)
            before_section, section_header, after_section, existing_entries = parsed

            # Format new entries (only the top result to avoid clutter)
            new_entries = []
            top_memory = memories[0]
            score = scores[0] if scores else None
            memory_id = memory_ids[0] if memory_ids else None
            new_entry = _format_retrieved_entry(top_memory, query, score, memory_id)
            new_entries.append(new_entry)

            # Build new entries list: new entry first, then existing (up to max - 1)
            all_entries = new_entries + existing_entries
            trimmed_entries = all_entries[:MAX_RETRIEVED_MEMORIES]

            # Per-entry ceiling first, then the section budget (a second ceiling site, separate code).
            entry_ceiling = RETRIEVED_CONTEXT_TOKEN_BUDGET // MAX_RETRIEVED_MEMORIES
            trimmed_entries = [
                _apply_entry_token_ceiling(e, entry_ceiling) for e in trimmed_entries
            ]

            # Over budget: drop the oldest retrieved entries rather than compress them.
            total_tokens = sum(_estimate_tokens(e) for e in trimmed_entries)
            while len(trimmed_entries) > 1 and total_tokens > RETRIEVED_CONTEXT_TOKEN_BUDGET:
                removed = trimmed_entries.pop()
                total_tokens -= _estimate_tokens(removed)

            # Build new section content
            section_lines = [
                RETRIEVED_CONTEXT_HEADER,
                RETRIEVED_CONTEXT_COMMENT,
                ""  # Blank line after comment
            ]
            for entry in trimmed_entries:
                section_lines.append(entry)
                section_lines.append("")  # Blank line between entries

            section_text = "\n".join(section_lines)

            # Reconstruct file content
            if section_header:
                # Section existed, replace it
                # Ensure blank line before next section
                if after_section and not after_section.startswith("\n"):
                    new_content = before_section + section_text + "\n" + after_section
                else:
                    new_content = before_section + section_text + after_section
            else:
                # Section didn't exist, insert before Working Memory if it exists
                working_memory_match = re.search(
                    r'^## Working Memory',
                    content,
                    re.MULTILINE
                )
                if working_memory_match:
                    # Insert before Working Memory with blank line
                    insert_pos = working_memory_match.start()
                    new_content = content[:insert_pos] + section_text + "\n" + content[insert_pos:]
                else:
                    # Append at end
                    if not content.endswith("\n"):
                        content += "\n"
                    new_content = content + "\n" + section_text

            # Write back to file (atomic: temp + rename, so a crash mid-write
            # cannot leave the always-loaded CLAUDE.md truncated)
            _atomic_write_text(claude_md_path, new_content, project_root)

        logger.info("Synced retrieved memories to CLAUDE.md Retrieved Context section")
        return SyncResult(SyncResult.WROTE)

    except Exception as e:
        logger.warning(f"Failed to sync retrieved memories to CLAUDE.md: {e}")
        return SyncResult(SyncResult.FAILED)
