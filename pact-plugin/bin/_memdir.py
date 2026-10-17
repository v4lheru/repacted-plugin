"""The orchestrator memory-dir rename core, used by repacted-memdir.

Location: pact-plugin/bin/_memdir.py (owned by launcher; see
the design notes section 1.2 A2 and 1.3). Used by: bin/repacted-memdir (the launcher's
swap and its crash recovery).
Stdlib only; import it by adding this file's directory to sys.path.

The platform derives the orchestrator's memory dir from the launch id with ':' written as '-':
the upstream PACT orchestrator launch uses agent-memory/PACT-pact-orchestrator/, and
`--agent repacted:pact-orchestrator` uses agent-memory/repacted-pact-orchestrator/
(MEASURED in the launcher live gate, #97). While the user alternates between the two
products, exactly ONE real dir exists and is renamed to match the running product. There is
no symlink and no copy: walkers such as memory_reachability and check-index --all would read
it twice.

Three operations, each refusing rather than guessing:
  ensure(side)   rename the one dir to that side's name; a no-op when it already has it.
                 Both names present is a SPLIT and is refused.
  merge_split()  fold the stray PACT-named dir into the repacted-named one, file by file,
                 never overwriting and never deleting a file: a colliding stray file lands
                 beside its twin as '<name>.split-<UTC timestamp>'. The merged dir then takes
                 the resting name PACT-pact-orchestrator.
  status()       report both names, the split flag and the *.split-* count.

Every move is checked: a count and a digest taken before must equal the ones taken after,
or the operation fails loudly (exit 1) with the undo command.
"""
from __future__ import annotations

import datetime
import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Optional

LEGACY_NAME = "PACT-pact-orchestrator"
DEFAULT_NAME = "repacted-pact-orchestrator"
CLI_PROCESS_NAME = "claude"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SPLIT_SUFFIX = re.compile(r"\.split-\d{8}T\d{6}Z(?:-\d+)?$")
EXIT_OK, EXIT_VERIFY, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2, 64
SIDES = ("pact", "repacted")

Fingerprint = Callable[[Path], "tuple[int, str]"]
Say = Callable[[str], None]


class Refusal(Exception):
    """A reason not to move anything."""


class VerifyFailed(Exception):
    """A post-move check failed; the message says what to inspect or how to undo."""


# ---------------------------------------------------------------------------
# Root and processes
# ---------------------------------------------------------------------------

def resolve_root(env: Optional[dict] = None) -> "tuple[Path, str]":
    """(config root, where it came from)."""
    env = os.environ if env is None else env
    configured = (env.get("CLAUDE_CONFIG_DIR") or "").strip()
    if configured:
        return Path(os.path.expanduser(configured)), "$CLAUDE_CONFIG_DIR"
    home = (env.get("HOME") or "").strip()
    if not home:
        raise Refusal("neither CLAUDE_CONFIG_DIR nor HOME is set; cannot locate the config root")
    return Path(home) / ".claude", "$HOME/.claude"


def claude_pids_from_ps(ps_output: str) -> "list[int]":
    """PIDs whose executable basename is exactly `claude`, from `ps -axo pid=,comm=`."""
    pids = []
    for line in ps_output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            if os.path.basename(parts[1].strip()) == CLI_PROCESS_NAME:
                pids.append(int(parts[0]))
    return pids


def running_claude_pids() -> "list[int]":
    """Live Claude Code CLI processes. Raises Refusal when the list cannot be read.

    Reads the executable name column only: never the argument column, which carries
    whatever secrets a process was started with.
    """
    try:
        result = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal(f"cannot read the process list ({exc.__class__.__name__})") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise Refusal("cannot read the process list (ps returned nothing)")
    return claude_pids_from_ps(result.stdout)


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------

def _entry_line(entry: Path) -> str:
    if entry.is_symlink():
        return f"L {os.readlink(entry)}"
    return f"F {hashlib.sha256(entry.read_bytes()).hexdigest()}"


def tree_fingerprint(root: Path) -> "tuple[int, str]":
    """(file count, digest) over every entry's relative path and content.

    Regular files contribute their bytes' sha256, symlinks their target text, and
    directories their presence, so an empty dir or a changed link changes the digest.
    Symlinks are never followed.
    """
    lines, files = [], 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in dirnames:
            entry = base / name
            rel = entry.relative_to(root).as_posix()
            lines.append(f"L {rel} -> {os.readlink(entry)}" if entry.is_symlink() else f"D {rel}")
        for name in filenames:
            entry = base / name
            rel = entry.relative_to(root).as_posix()
            if entry.is_symlink():
                lines.append(f"L {rel} -> {os.readlink(entry)}")
            else:
                lines.append(f"F {rel} {hashlib.sha256(entry.read_bytes()).hexdigest()}")
            files += 1
    digest = hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()
    return files, digest


def _leaf_entries(root: Path) -> "list[Path]":
    """Every regular file and symlink under root (a symlinked dir is a leaf), never followed."""
    leaves = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        leaves += [base / name for name in dirnames if (base / name).is_symlink()]
        leaves += [base / name for name in filenames]
    return leaves


def content_fingerprint(roots: Iterable[Path]) -> "tuple[int, str]":
    """(entry count, digest) over the MULTISET of contents under the roots, paths ignored.

    A merge renames colliding files, so paths change by design; what must not change is
    what is stored. Equal before and after proves no file was lost, duplicated or altered.
    """
    lines = [_entry_line(entry) for root in roots if root.is_dir() for entry in _leaf_entries(root)]
    return len(lines), hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The verified move
# ---------------------------------------------------------------------------

def move_verified(source: Path, target: Path, *, apply: bool, undo: str,
                  fingerprint: Fingerprint = tree_fingerprint, say: Say = print) -> int:
    """Rename source to target in one os.rename, with the count and digest checked around it."""
    count, digest = fingerprint(source)
    say(f"before: {count} files, digest {digest}")
    if not apply:
        say("dry run: nothing moved. Re-run with --confirm-live to apply.")
        return EXIT_OK
    if target.exists() or target.is_symlink():  # re-checked at the last moment: never overwrite
        raise Refusal(f"target {target} already exists; refusing to merge or overwrite")
    os.rename(source, target)
    after_count, after_digest = fingerprint(target)
    say(f"after:  {after_count} files, digest {after_digest}")
    if (after_count, after_digest) != (count, digest) or source.exists():
        raise VerifyFailed(
            f"VERIFY FAILED: the dir is now at {target}, but its content does not match what "
            f"was taken before the move. Do not start a session; inspect it, and undo with: {undo}")
    say(f"moved: {count} files, digest equal. {source.name} no longer exists.")
    return EXIT_OK



# ---------------------------------------------------------------------------
# ensure / status
# ---------------------------------------------------------------------------

def side_paths(memory_dir: Path, name: str) -> "dict[str, Path]":
    return {"pact": memory_dir / LEGACY_NAME, "repacted": memory_dir / name}


def _state(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "dir"
    return "other" if path.exists() else "absent"


def _refuse_unless_real_dirs(paths: Iterable[Path]) -> None:
    for path in paths:
        state = _state(path)
        if state == "symlink":
            raise Refusal(f"{path} is a symlink; this tool moves a real dir only")
        if state == "other":
            raise Refusal(f"{path} exists but is not a directory")


def split_message(memory_dir: Path, name: str) -> str:
    return (f"both {LEGACY_NAME} and {name} exist in {memory_dir}: a SPLIT (a session ran "
            f"while the dir had the other name). Nothing was moved. Merge them with: "
            f"repacted-memdir --merge-split --confirm-live")


def ensure(memory_dir: Path, side: str, name: str = DEFAULT_NAME, *, apply: bool,
           fingerprint: Fingerprint = tree_fingerprint, say: Say = print) -> int:
    """Give the one orchestrator dir the name of `side`. Idempotent; refuses a split."""
    paths = side_paths(memory_dir, name)
    target = paths[side]
    other = "repacted" if side == "pact" else "pact"
    source = paths[other]
    _refuse_unless_real_dirs(paths.values())
    if target.is_dir() and source.is_dir():
        raise Refusal(split_message(memory_dir, name))
    if target.is_dir():
        say(f"ensure {side}: already {target.name}; nothing to do.")
        return EXIT_OK
    if not source.is_dir():
        say(f"ensure {side}: no orchestrator memory dir yet (neither name exists); nothing to do.")
        return EXIT_OK
    say(f"ensure {side}: {source.name} -> {target.name} in {memory_dir}")
    return move_verified(source, target, apply=apply, fingerprint=fingerprint, say=say,
                         undo=f"repacted-memdir --ensure {other} --confirm-live")


def split_files(memory_dir: Path, name: str = DEFAULT_NAME) -> "list[Path]":
    """Every *.split-<ts> entry left by a merge, in whichever orchestrator dir exists."""
    found = []
    for root in side_paths(memory_dir, name).values():
        if root.is_dir() and not root.is_symlink():
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                found += [Path(dirpath) / n for n in dirnames + filenames if SPLIT_SUFFIX.search(n)]
    return sorted(found)


def split_reminder(memory_dir: Path, name: str = DEFAULT_NAME) -> Optional[str]:
    count = len(split_files(memory_dir, name))
    if not count:
        return None
    return (f"repact: {count} *.split-* file(s) from a merged split wait in the orchestrator "
            f"memory dir; reconcile each with its twin, then delete it "
            f"(list them: repacted-memdir --status).")


def status(memory_dir: Path, name: str = DEFAULT_NAME) -> "list[str]":
    paths = side_paths(memory_dir, name)
    lines = [f"memory dir: {memory_dir}"]
    lines += [f"  {path.name}: {_state(path)}" for path in paths.values()]
    both = all(_state(path) == "dir" for path in paths.values())
    lines.append(f"  split: {'YES (repacted-memdir --merge-split --confirm-live)' if both else 'no'}")
    splits = split_files(memory_dir, name)
    lines.append(f"  *.split-* files: {len(splits)}")
    lines += [f"    {path}" for path in splits]
    return lines


# ---------------------------------------------------------------------------
# merge_split
# ---------------------------------------------------------------------------

def utc_stamp(now: Optional[datetime.datetime] = None) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class _Placer:
    """Maps a stray relative path to a free destination under the canonical dir.

    A component collides when the canonical path already holds it (as a leaf, or as a
    non-directory where the stray has a directory). The first colliding component gets the
    '.split-<ts>' suffix (plus '-2', '-3' while that too is taken); everything below it
    follows it there. One mapping per component, so siblings land together.
    """

    def __init__(self, canonical: Path, stamp: str):
        self.canonical, self.stamp = canonical, stamp
        self.renamed: "dict[tuple, str]" = {}
        self.taken: "set[Path]" = set()  # destinations already planned in this run

    def _occupied(self, path: Path) -> bool:
        return path in self.taken or path.exists() or path.is_symlink()

    def _free(self, parent: Path, name: str) -> str:
        candidate, counter = f"{name}.split-{self.stamp}", 1
        while self._occupied(parent / candidate):
            counter += 1
            candidate = f"{name}.split-{self.stamp}-{counter}"
        return candidate

    def place(self, parts: "tuple[str, ...]", is_leaf: bool) -> "tuple[Path, bool]":
        """(destination, collided) for the stray entry at `parts`."""
        dest, collided = self.canonical, False
        for index, part in enumerate(parts):
            key = parts[: index + 1]
            if key in self.renamed:
                dest, collided = dest / self.renamed[key], True
                continue
            here = dest / part
            last = index == len(parts) - 1
            occupied = self._occupied(here)
            blocks = occupied and (last and is_leaf or not here.is_dir() or here.is_symlink())
            if blocks and not collided:
                self.renamed[key] = self._free(dest, part)
                dest, collided = dest / self.renamed[key], True
            else:
                dest = here
        if is_leaf:
            self.taken.add(dest)
        return dest, collided


def _move_no_clobber(source: Path, dest: Path) -> None:
    """Move one leaf; raises FileExistsError instead of ever replacing dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        os.symlink(os.readlink(source), dest)
    else:
        os.link(source, dest, follow_symlinks=False)
    if _entry_line(source) != _entry_line(dest):
        dest.unlink()
        raise VerifyFailed(f"VERIFY FAILED: {dest} does not match {source} after the move")
    source.unlink()


def _remove_empty_tree(root: Path) -> None:
    """rmdir the now-empty stray tree bottom-up; never removes a file."""
    for dirpath, dirnames, _ in os.walk(root, topdown=False, followlinks=False):
        for name in dirnames:
            entry = Path(dirpath) / name
            if not entry.is_symlink():
                os.rmdir(entry)
    os.rmdir(root)


def merge_split(memory_dir: Path, name: str = DEFAULT_NAME, *, apply: bool,
                now: Optional[datetime.datetime] = None, say: Say = print) -> int:
    """Fold the stray PACT-named dir into the repacted-named one; never overwrite or delete."""
    paths = side_paths(memory_dir, name)
    stray, canonical = paths["pact"], paths["repacted"]
    _refuse_unless_real_dirs(paths.values())
    if not (stray.is_dir() and canonical.is_dir()):
        say("merge-split: no split (at most one orchestrator dir exists); nothing to do.")
        return EXIT_OK
    before = content_fingerprint([stray, canonical])
    placer = _Placer(canonical, utc_stamp(now))
    dirs = [Path(p) for p, _, _ in os.walk(stray, followlinks=False)][1:]
    plan = [(leaf, *placer.place(leaf.relative_to(stray).parts, True)) for leaf in _leaf_entries(stray)]
    plan_dirs = [placer.place(d.relative_to(stray).parts, False)[0] for d in dirs]
    collisions = [(leaf, dest) for leaf, dest, collided in plan if collided]
    say(f"merge-split: {stray.name} (stray) -> {canonical.name}; before: {before[0]} files, "
        f"digest {before[1]}")
    say(f"  {len(plan) - len(collisions)} to move, {len(collisions)} colliding")
    for leaf, dest in collisions:
        say(f"  collision: {leaf.relative_to(stray)} -> {dest.relative_to(canonical)}")
    if not apply:
        say("dry run: nothing moved. Re-run with --confirm-live to apply.")
        return EXIT_OK
    for dest in plan_dirs:
        dest.mkdir(parents=True, exist_ok=True)
    for leaf, dest, _ in plan:
        _move_no_clobber(leaf, dest)
    _remove_empty_tree(stray)
    after = content_fingerprint([canonical])
    if after != before or stray.exists():
        raise VerifyFailed(
            f"VERIFY FAILED: after the merge {canonical} holds {after[0]} files (digest "
            f"{after[1]}), not the {before[0]} taken before. Do not start a session; inspect "
            f"{canonical} and {stray}.")
    move_verified(canonical, stray, apply=True, say=lambda _line: None,
                  undo=f"repacted-memdir --ensure repacted --confirm-live")
    say(f"merged the split: {len(plan) - len(collisions)} file(s) moved, {len(collisions)} "
        f"collision(s) kept as *.split-{placer.stamp}; {before[0]} files, digest equal; the dir "
        f"is now {LEGACY_NAME}.")
    return EXIT_OK
