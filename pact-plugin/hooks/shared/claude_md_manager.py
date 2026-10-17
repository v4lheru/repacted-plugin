"""Project CLAUDE.md manipulation for session_init: the PACT_MANAGED region and its migration.

The file lives at $CLAUDE_PROJECT_DIR/.claude/CLAUDE.md (preferred) or ./CLAUDE.md
(legacy); resolve_project_claude_md_path picks the existing one, else the preferred path.

Map, in file order: file_lock (sidecar lock), ContainmentError, line-ending helpers,
_atomic_write_text (contained atomic replace), marker and comment constants,
_strip_legacy_lines, strip_orphan_kernel_block, extract_managed_region,
resolve_project_claude_md_path, ensure_dot_claude_parent, ensure_project_memory_md,
migrate_to_managed_structure, _build_migrated_content.
file_lock, _atomic_write_text and the line-ending helpers have twins in
skills/pact-memory/scripts/working_memory.py whose bodies (comments included) are
drift-gated byte-identical.
Rationale: the design notes
"""

from __future__ import annotations

import fcntl  # Unix-only; PACT supports macOS/Linux. No Windows compat shim.
import hashlib
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .failure_cause import failure_cause
from .paths import get_claude_config_dir

# Project-level CLAUDE.md is preferred at .claude/CLAUDE.md (the new default)
# but Claude Code also accepts ./CLAUDE.md for backwards compatibility.
_DOT_CLAUDE_RELATIVE = Path(".claude") / "CLAUDE.md"
_LEGACY_RELATIVE = Path("CLAUDE.md")

# Sidecar lock (`<state>/repacted/locks/<sha256>.lock`, keyed by the target path) serializing
# read-mutate-write between concurrent session_init hooks; independent of the target's inode.
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL = 0.1


class LockDirError(OSError):
    """file_lock could not create or open its lock; the routed cause names it, paths stay out."""



@contextmanager
def file_lock(target_file: Path):
    """Acquire an exclusive sidecar file lock for a target CLAUDE.md path.

    Not re-entrant. The sidecar sits in the per-user lock dir, outside the repo, and is never
    removed (removing it inside the lock window races other waiters). Raises TimeoutError
    after _LOCK_TIMEOUT_SECONDS; callers fail open.
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

# Outer boundary wrapping all PACT-managed content in project CLAUDE.md.
# User-owned content goes OUTSIDE this block.
MANAGED_START_MARKER = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
MANAGED_END_MARKER = "<!-- PACT_MANAGED_END -->"

# Inner boundary wrapping project memory sections (Retrieved Context,
# Pinned Context, Working Memory) for hook targeting (#404).
MEMORY_START_MARKER = "<!-- PACT_MEMORY_START -->"
MEMORY_END_MARKER = "<!-- PACT_MEMORY_END -->"

# The auto-managed comment each memory heading carries, shared by both writers here.
# Spelled, not imported: hooks cannot import skills/pact-memory/scripts/working_memory.py
# (it resolves only under pytest). tests/test_managed_comment_mirror.py holds the two equal.
RETRIEVED_CONTEXT_COMMENT = "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->"
WORKING_MEMORY_COMMENT = "<!-- Auto-managed by pact-memory skill. Full history searchable via pact-memory skill. Keyed by folder name, so another checkout with the same name shares this section. -->"

# Canonical H1 for every template site (here and session_resume Case 0).
MANAGED_TITLE = "# PACT Framework and Managed Project Memory"

# Boundary prefixes that end a section scan. Twin: working_memory._PACT_BOUNDARY_ALT (drift-tested).
PACT_BOUNDARY_PREFIXES: tuple[str, ...] = (
    "PACT_MEMORY_",
    "PACT_MANAGED_",
    "PACT_ROUTING_",
)

# Embed this alternation in every scan terminator; never hard-code the prefixes.
_BOUNDARY_ALT = "|".join(PACT_BOUNDARY_PREFIXES)

# Session markers: not PACT_ boundary prefixes; the scan prefix below is derived from them.
SESSION_START_MARKER = "<!-- SESSION_START -->"
SESSION_END_MARKER = "<!-- SESSION_END -->"
# Common prefix of both markers, so renaming either carries into every terminator.
SESSION_BOUNDARY_PREFIX = os.path.commonprefix(
    [SESSION_START_MARKER, SESSION_END_MARKER]
).removeprefix("<!-- ")

# An empty prefix would match every HTML comment and truncate sections; refuse at import.
if not SESSION_BOUNDARY_PREFIX:
    raise ValueError(
        "SESSION_BOUNDARY_PREFIX derived empty: "
        f"{SESSION_START_MARKER!r} and {SESSION_END_MARKER!r} share no "
        "common prefix. An empty prefix matches every HTML comment and "
        "truncates every section scan. Give the two markers a common name."
    )

# Applied per line, outside fences, by _strip_legacy_lines (MULTILINE once deleted fenced examples).
_STALE_ORCHESTRATOR_LINE_RE = re.compile(
    r"^The global PACT Orchestrator is loaded from `~/\.claude/CLAUDE\.md`\.?\s*$",
)


class ContainmentError(OSError):
    """A CLAUDE.md write target escaped its project containment boundary (#1247).

    Subclasses OSError; callers turn it into an opaque skip message (no victim path).
    Twin of ContainmentError in skills/pact-memory/scripts/working_memory.py.
    """


def _detect_line_ending(name: str, parent_fd: int) -> str:
    """Report the line ending `name` uses today, read THROUGH `parent_fd`.

    Reads bytes (text reads already translated CRLF). Dominant wins, ties and absent or
    unreadable targets give LF. Twin: working_memory.py (drift-gated).
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
    """Apply `line_ending` to `content`, normalising CRLF to LF first (so a double restore is a no-op).

    Twin: working_memory.py (drift-gated).
    """
    if line_ending == "\n":
        return content
    return content.replace("\r\n", "\n").replace("\n", line_ending)


def _atomic_write_text(target: Path, content: str, project_root: Path) -> None:
    """Replace `target`'s contents atomically (temp + fsync + rename), iff the directory the
    write binds into is contained within `project_root` (#1247).

    Containment is kernel-object ancestry on a pinned parent descriptor, never a path
    resolver; the leaf is never consulted, which is sound only while the write replaces
    the leaf entry. Callers hold file_lock. Raises ContainmentError (fail-closed).

    PRECONDITION. This guard eliminates one class outright and narrows another.
    The class eliminated is disagreement between two path resolutions: only
    one traversal happens here and its result is held OPEN, so no second
    resolution exists to disagree with it. What is narrowed is time. The walk
    establishes ancestry AT THE INSTANT OF THE WALK, and a descriptor pins
    IDENTITY, not POSITION -- so containment holds provided the directory the
    write binds into does not change position relative to the anchor between
    the walk and the rename. Only the chain from that directory up to the
    anchor matters; relocating anything off it is irrelevant.

    See the design notes before changing the write shape.
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


def _strip_legacy_lines(content: str) -> str:
    """Remove obsolete legacy-template lines outside fenced code blocks (backtick and tilde
    fences per CommonMark 4.5); fenced content is preserved byte for byte. Pure.
    """
    # CommonMark 4.5: a fence closes only with the same character and a run at least as long.
    pos = 0
    fence_open_len = 0  # 0 = not inside a fence
    fence_char = ""     # "`" or "~" when inside a fence
    out_parts: list[str] = []
    while pos < len(content):
        nl = content.find("\n", pos)
        if nl == -1:
            line = content[pos:]
            raw_segment = line
            line_end = len(content)
        else:
            line = content[pos:nl]
            raw_segment = content[pos:nl + 1]
            line_end = nl + 1

        stripped = line.lstrip()

        if fence_open_len == 0:
            # Not inside a fence — check for fence open
            if stripped.startswith("```"):
                run_len = len(stripped) - len(stripped.lstrip("`"))
                fence_open_len = run_len
                fence_char = "`"
                out_parts.append(raw_segment)
            elif stripped.startswith("~~~"):
                run_len = len(stripped) - len(stripped.lstrip("~"))
                fence_open_len = run_len
                fence_char = "~"
                out_parts.append(raw_segment)
            elif _STALE_ORCHESTRATOR_LINE_RE.match(line):
                # Non-fenced legacy line: drop it entirely
                pass
            else:
                out_parts.append(raw_segment)
        else:
            # Inside a fence — check for fence close (same char, run >= open)
            if fence_char == "`" and stripped.startswith("```"):
                run_len = len(stripped) - len(stripped.lstrip("`"))
                # A closing fence has no info string.
                after_run = stripped[run_len:].strip()
                if run_len >= fence_open_len and not after_run:
                    fence_open_len = 0
                    fence_char = ""
            elif fence_char == "~" and stripped.startswith("~~~"):
                run_len = len(stripped) - len(stripped.lstrip("~"))
                after_run = stripped[run_len:].strip()
                if run_len >= fence_open_len and not after_run:
                    fence_open_len = 0
                    fence_char = ""
            # Keep fence body verbatim regardless
            out_parts.append(raw_segment)

        pos = line_end

    return "".join(out_parts)


def strip_orphan_kernel_block() -> str | None:
    """SUNSET BEFORE v5.0.0: strip the obsolete PACT_START/PACT_END kernel block from ~/.claude/CLAUDE.md.

    Idempotent. A malformed marker pair returns a "Migration skipped: ..." string, which
    session_init routes to systemMessage (hook stderr is not shown to users).
    """
    target_file = get_claude_config_dir() / "CLAUDE.md"
    if not target_file.exists():
        return None

    # Serialize concurrent session_init hooks on the home file; fail open on timeout.
    try:
        with file_lock(target_file):
            # Containment in _atomic_write_text (inside this lock) replaces the old leaf is_symlink guard.
            # Never resolve the target and reuse it: the write would follow the leaf.
            try:
                content = target_file.read_text(encoding="utf-8")
            except OSError:
                return None

            START_MARKER = "<!-- PACT_START:"
            END_MARKER = "<!-- PACT_END -->"

            has_start = START_MARKER in content
            has_end = END_MARKER in content

            if not has_start and not has_end:
                # Normal idempotent no-op for already-migrated installs.
                return None

            if has_start != has_end:
                # One marker only: no-op to avoid data loss, and report it.
                which = "PACT_START" if has_start else "PACT_END"
                missing = "PACT_END" if has_start else "PACT_START"
                return (
                    f"Migration skipped: {target_file} contains "
                    f"{which} but no matching {missing}. To avoid data "
                    f"loss, inspect the file and either remove the "
                    f"orphan {which} marker or restore the matching "
                    f"{missing} marker."
                )

            pre_marker, rest = content.split(START_MARKER, 1)
            if END_MARKER not in rest:
                # END marker exists in content but appears textually
                # before START. Same defensive handling.
                return (
                    f"Migration skipped: {target_file} contains "
                    "both PACT_START and PACT_END markers but PACT_END "
                    "appears before PACT_START. Inspect the file and "
                    "reorder or remove the orphan markers."
                )

            _, post_marker = rest.split(END_MARKER, 1)

            # Preserve one blank line at the removal boundary so the
            # user's spacing around the obsolete block survives the strip.
            pre_clean = pre_marker.rstrip("\r\n")
            post_clean = post_marker.lstrip("\r\n")
            if pre_clean and post_clean:
                new_content = pre_clean + "\n\n" + post_clean
            elif pre_clean:
                new_content = pre_clean + "\n"
            elif post_clean:
                new_content = post_clean
            else:
                new_content = ""

            try:
                # Anchor is the global config dir, a different trust boundary from a project root (R4).
                _atomic_write_text(
                    target_file, new_content, get_claude_config_dir()
                )
                return (
                    f"Removed obsolete PACT kernel block from {target_file}"
                )
            except ContainmentError:
                # Opaque skip; target_file is never resolved, so it cannot name a symlink victim.
                return (
                    f"Migration skipped: {target_file} path "
                    "precondition not met."
                )
            except OSError as e:
                # "Failed" is the routing token for session_init; the cause is a closed vocabulary (no path).
                return (
                    f"Failed to remove stale kernel block: {failure_cause(e)}"
                )
    except TimeoutError:
        return (
            f"Failed to acquire lock on {target_file} within 5s "
            "(another session_init hook may be running concurrently). "
            "Kernel-block migration skipped; will retry on next session "
            "start."
        )
    except OSError:
        # Lock acquisition errors (not TimeoutError) skip at the same level; opaque, no sidecar path.
        return (
            f"Could not acquire lock on {target_file} "
            "(path precondition not met); kernel-block migration skipped."
        )


def extract_managed_region(content: str) -> tuple[str, int] | None:
    """Extract the PACT-managed region: (region_text, start_offset after the start marker), or None.

    The region holds only plugin-generated content, so consumers need no fence awareness.
    """
    start_idx = content.find(MANAGED_START_MARKER)
    if start_idx == -1:
        return None
    region_start = start_idx + len(MANAGED_START_MARKER)
    end_idx = content.find(MANAGED_END_MARKER, region_start)
    if end_idx == -1:
        return None
    return content[region_start:end_idx], region_start


def resolve_project_claude_md_path(
    project_dir: str | os.PathLike[str],
) -> tuple[Path, str]:
    """Resolve the project CLAUDE.md path: (path, source) with source "dot_claude", "legacy" or "new_default"."""
    base = Path(project_dir)
    dot_claude = base / _DOT_CLAUDE_RELATIVE
    legacy = base / _LEGACY_RELATIVE

    if dot_claude.exists():
        return dot_claude, "dot_claude"
    if legacy.exists():
        return legacy, "legacy"
    return dot_claude, "new_default"


def ensure_dot_claude_parent(path: Path) -> None:
    """Ensure a CLAUDE.md path's parent exists (created 0o700).

    Raises OSError when the parent is a regular file, naming it, rather than a later ContainmentError.
    """
    parent = path.parent
    if parent.exists() and not parent.is_dir():
        raise OSError(f"{parent} exists but is not a directory")
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def ensure_project_memory_md() -> str | None:
    """Create a project CLAUDE.md with the managed structure when neither location exists.

    Returns a status message, or None when a file already exists.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file, source = resolve_project_claude_md_path(project_dir)

    # Don't overwrite existing project CLAUDE.md (either location)
    if source != "new_default":
        return None

    # Outer PACT_MANAGED wraps all managed content; PACT_MEMORY wraps the memory sections.
    memory_template = f"""{MANAGED_START_MARKER}
{MANAGED_TITLE}

{SESSION_START_MARKER}
## Current Session
<!-- Auto-managed by session_init hook. Overwritten each session. -->
{SESSION_END_MARKER}

{MEMORY_START_MARKER}
## Retrieved Context
{RETRIEVED_CONTEXT_COMMENT}

## Pinned Context

## Working Memory
{WORKING_MEMORY_COMMENT}
{MEMORY_END_MARKER}

{MANAGED_END_MARKER}
"""

    # Serialize check + write so two hooks cannot both create the file; fail open on timeout.
    try:
        ensure_dot_claude_parent(target_file)
        with file_lock(target_file):
            # Containment in _atomic_write_text (inside the lock) replaces the old leaf is_symlink guard.
            if target_file.exists():
                return None
            try:
                _atomic_write_text(
                    target_file, memory_template, Path(project_dir)
                )
                return "Created project CLAUDE.md with memory sections"
            except ContainmentError:
                return "Project CLAUDE.md skipped: path precondition not met."
            except OSError as e:
                # `failed` IS THE ROUTING TOKEN (session_init step 3). The
                # prefix stays byte-identical. Only the cause token changed.
                return f"Project CLAUDE.md failed: {failure_cause(e)}"
    except TimeoutError:
        return (
            "Failed to acquire lock on project CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "Project CLAUDE.md creation skipped; will retry on next session start."
        )
    except OSError as e:
        # Lock-acquisition failure. Same routing token, same closed
        # vocabulary as the inner arm above.
        return f"Project CLAUDE.md failed: {failure_cause(e)}"


def migrate_to_managed_structure() -> str | None:
    """One-time migration: wrap existing project CLAUDE.md content in PACT_MANAGED and PACT_MEMORY.

    Idempotent (no-op when PACT_MANAGED_START is present); file_lock, containment, fail-open.
    The legacy "# Project Memory" heading becomes "# PACT Framework and Managed Project Memory";
    all three memory headings are emitted; unrecognised content lands after PACT_MANAGED_END.
    Returns a status, None on no-op, or a "failed"/"skipped" string for systemMessages.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file, source = resolve_project_claude_md_path(project_dir)

    if source == "new_default":
        return None  # File doesn't exist; ensure_project_memory_md() handles creation

    try:
        with file_lock(target_file):
            # Containment in _atomic_write_text (inside the lock) replaces the old leaf is_symlink guard.
            try:
                content = target_file.read_text(encoding="utf-8")
            except OSError:
                return None

            # Idempotent guard: already migrated
            if MANAGED_START_MARKER in content:
                return None

            new_content = _build_migrated_content(content)

            try:
                _atomic_write_text(
                    target_file, new_content, Path(project_dir)
                )
                return "Migrated project CLAUDE.md to managed structure (#404)"
            except ContainmentError:
                return "Migration skipped: project CLAUDE.md path precondition not met."
            except OSError as e:
                # `failed` IS THE ROUTING TOKEN (session_init step 3b). The
                # prefix stays byte-identical. Only the cause token changed.
                return f"Migration failed: {failure_cause(e)}"
    except TimeoutError:
        return (
            "Failed to acquire lock on project CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "CLAUDE.md migration skipped; will retry on next session start."
        )
    except OSError:
        # Lock acquisition PermissionError skips like a timeout; opaque, no sidecar path.
        return (
            "Could not acquire lock on project CLAUDE.md "
            "(path precondition not met); CLAUDE.md migration skipped."
        )


def _build_migrated_content(content: str) -> str:
    """Transform old-format CLAUDE.md content into the managed structure (pure; idempotent).

    User content above the first PACT section lands below PACT_MANAGED_END.
    """
    # Idempotency guard: already migrated → no-op
    if MANAGED_START_MARKER in content:
        return content

    # Extract session block if present (between markers)
    session_block = ""
    content_sans_routing = content
    content_sans_session = content_sans_routing
    session_start = SESSION_START_MARKER
    session_end = SESSION_END_MARKER
    if session_start in content_sans_routing and session_end in content_sans_routing:
        pattern = re.compile(
            re.escape(session_start) + r".*?" + re.escape(session_end),
            re.DOTALL,
        )
        match = pattern.search(content_sans_routing)
        if match:
            session_block = match.group(0)
            content_sans_session = (
                content_sans_routing[:match.start()]
                + content_sans_routing[match.end():]
            )

    # What remains after extracting routing + session is candidate for
    # memory sections and user content.
    remaining = content_sans_session

    # Remove the old top-level heading and description line
    remaining = re.sub(
        r"^# Project Memory\s*\n"
        r"(?:\s*\n)*"
        r"(?:This file contains project-specific memory managed by the PACT framework\.\s*\n)?",
        "",
        remaining,
    )

    # Strip legacy template lines (e.g., stale orchestrator-loader line)
    remaining = _strip_legacy_lines(remaining)

    # Extract memory sections: Retrieved Context, Pinned Context, Working Memory
    memory_headings = ["## Retrieved Context", "## Pinned Context", "## Working Memory"]
    memory_parts = []
    user_parts = []

    lines = remaining.splitlines(keepends=True)
    current_section: list[str] = []
    in_memory_section = False
    # Length-tracked fence state, as in _strip_legacy_lines.
    fence_open_len = 0  # 0 = not inside a fence
    fence_char = ""     # "`" or "~" when inside a fence

    for line in lines:
        stripped = line.rstrip()
        lstripped = stripped.lstrip()
        if fence_open_len == 0:
            # Not inside a fence — check for fence open
            if lstripped.startswith("```"):
                run_len = len(lstripped) - len(lstripped.lstrip("`"))
                fence_open_len = run_len
                fence_char = "`"
                current_section.append(line)
                continue
            elif lstripped.startswith("~~~"):
                run_len = len(lstripped) - len(lstripped.lstrip("~"))
                fence_open_len = run_len
                fence_char = "~"
                current_section.append(line)
                continue
        else:
            # Inside a fence — check for fence close (same char, run >= open)
            if fence_char == "`" and lstripped.startswith("```"):
                run_len = len(lstripped) - len(lstripped.lstrip("`"))
                after_run = lstripped[run_len:].strip()
                if run_len >= fence_open_len and not after_run:
                    fence_open_len = 0
                    fence_char = ""
            elif fence_char == "~" and lstripped.startswith("~~~"):
                run_len = len(lstripped) - len(lstripped.lstrip("~"))
                after_run = lstripped[run_len:].strip()
                if run_len >= fence_open_len and not after_run:
                    fence_open_len = 0
                    fence_char = ""
            # Keep fence body verbatim regardless
            current_section.append(line)
            continue
        if any(stripped == h for h in memory_headings):
            if current_section and not in_memory_section:
                user_parts.extend(current_section)
                current_section = []
            elif current_section and in_memory_section:
                memory_parts.extend(current_section)
                current_section = []
            in_memory_section = True
            current_section.append(line)
        # Indent-tolerant on purpose (the exact match above is not): an indented heading becomes a
        # boundary, so user text leaves the managed region instead of being adopted and pruned.
        elif lstripped.startswith("## ") or lstripped.startswith("# "):
            if current_section:
                if in_memory_section:
                    memory_parts.extend(current_section)
                else:
                    user_parts.extend(current_section)
                current_section = []
            in_memory_section = False
            current_section.append(line)
        else:
            current_section.append(line)

    if current_section:
        if in_memory_section:
            memory_parts.extend(current_section)
        else:
            user_parts.extend(current_section)

    memory_text = "".join(memory_parts).strip()
    user_text = "".join(user_parts).strip()

    # Split memory into {heading: body} dict — always emit all 3 headings.
    memory_sections: dict[str, str] = {
        "## Retrieved Context": "",
        "## Pinned Context": "",
        "## Working Memory": "",
    }

    def _append_body(heading: str, new_body: str) -> None:
        existing = memory_sections[heading]
        if existing and new_body:
            memory_sections[heading] = existing + "\n" + new_body
        elif new_body:
            memory_sections[heading] = new_body

    if memory_text:
        current_heading: str | None = None
        current_body: list[str] = []
        for line in memory_text.splitlines(keepends=True):
            stripped_line = line.rstrip()
            if stripped_line in memory_sections:
                if current_heading is not None:
                    _append_body(current_heading, "".join(current_body).rstrip())
                current_heading = stripped_line
                current_body = []
            elif current_heading is not None:
                current_body.append(line)
        if current_heading is not None:
            _append_body(current_heading, "".join(current_body).rstrip())

    # Build the new structure — all content goes inside the managed block
    parts: list[str] = []
    parts.extend([MANAGED_START_MARKER, "\n", f"{MANAGED_TITLE}\n"])

    if session_block:
        parts.extend(["\n", session_block, "\n"])

    parts.extend(["\n", MEMORY_START_MARKER, "\n"])
    # Same comment constants as the creation template; Pinned Context has none.
    heading_comments = {
        "## Retrieved Context": RETRIEVED_CONTEXT_COMMENT,
        "## Working Memory": WORKING_MEMORY_COMMENT,
    }
    heading_chunks: list[str] = []
    for heading in ("## Retrieved Context", "## Pinned Context", "## Working Memory"):
        body = memory_sections[heading]
        comment = heading_comments.get(heading)
        # Add the comment only if absent anywhere in the body: a duplicate cannot be undone, a missing one is re-supplied.
        if comment and comment not in body:
            body = f"{comment}\n{body}" if body else comment
        if body:
            heading_chunks.append(f"{heading}\n{body}\n")
        else:
            heading_chunks.append(f"{heading}\n")
    parts.append("\n".join(heading_chunks))
    if not parts[-1].endswith("\n"):
        parts.append("\n")
    parts.extend([MEMORY_END_MARKER, "\n"])

    parts.extend(["\n", MANAGED_END_MARKER, "\n"])

    if user_text:
        parts.extend(["\n", user_text, "\n"])

    return "".join(parts)
