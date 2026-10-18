#!/usr/bin/env python3
"""collect_change: turn what an unattended model left in its checkout into a patch.

Location: pact-plugin/templates/ci/collect_change.py. The `work` job of
templates/ci/unattended-compact.yml runs it twice, from the plugin checkout pinned by sha.
The recipe is docs/recipes/unattended-ci.md.

The model's checkout, its .git included, is model-written. Git reads config, attributes and
hooks from a repository, so the runner's git never opens it. Two halves:

  export   runs in a container with no key and no network, on the model's side. It picks the
           result: the one worktree under .worktrees/ whose tree differs from the base, or the
           main checkout when none does (two or more are refused, not merged). It snapshots
           that checkout through a scratch index, so committed and uncommitted work both
           count, and writes it out as plain files with `git archive`. The top-level
           .claude/ stays as at the base: the plugin writes its session notes there.
  collect  runs on the runner. It treats the export as hostile files, never as git data: a
           symlink, a FIFO or any other special file, a `.git` at any depth and anything
           under `.worktrees/` are refused. It lays the regular files over a fresh checkout of
           the base sha (made by actions/checkout, never cloned from the model's .git) and
           diffs there, with hooks, fsmonitor, transports, external diff and textconv off.
The patch then goes to publish_change.py, which applies its own refusals.

Exit codes: 0 done; 1 refused; 2 usage error; 3 a git command failed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

WORKTREES = ".worktrees"
PLUGIN_STATE = ".claude"
SAFE_GIT = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
            "-c", "protocol.allow=never")
EXAMPLES = ("python3 collect_change.py export --repo target --base <sha> --out export\n"
            "  python3 collect_change.py collect --export export --pristine pristine "
            "--base <sha> --patch change.patch")


class Refused(Exception):
    """Nothing is collected; the message says why."""


class GitFailed(Exception):
    pass


def git(repo: Path, *args: str, env: dict | None = None, out=subprocess.PIPE) -> str:
    done = subprocess.run(["git", *SAFE_GIT, "-C", str(repo), *args], stdout=out,
                          stderr=subprocess.PIPE, env=env)
    if done.returncode != 0:
        raise GitFailed(f"git {args[0]} in {repo}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout.decode().strip() if out is subprocess.PIPE else ""


# --- export (model side, in a container) -------------------------------------------------

def snapshot(checkout: Path, scratch: Path, base: str) -> str:
    """The tree of HEAD plus every uncommitted change, without touching the real index.

    The top-level .claude/ is kept as it is at the base: the plugin's SessionStart hook writes
    its session notes to .claude/CLAUDE.md there, and publish refuses .claude/ in any case.
    """
    env = {**os.environ, "GIT_INDEX_FILE": str(scratch / f"index-{os.urandom(4).hex()}")}
    git(checkout, "read-tree", "HEAD", env=env)
    git(checkout, "add", "-A", "--", ".", f":(exclude){WORKTREES}", env=env)
    git(checkout, "reset", "-q", base, "--", PLUGIN_STATE, env=env)
    return git(checkout, "write-tree", env=env)


def linked_worktrees(repo: Path) -> list[Path]:
    listing = git(repo, "worktree", "list", "--porcelain")
    inside = (repo / WORKTREES).resolve()
    found = []
    for line in listing.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):]).resolve()
            if inside in path.parents and path.is_dir():
                found.append(path)
    return found


def export(repo: Path, base: str, out: Path) -> str:
    repo = repo.resolve()
    base_tree = git(repo, "rev-parse", f"{base}^{{tree}}")
    with tempfile.TemporaryDirectory() as scratch:
        changed = []
        for tree_path in linked_worktrees(repo):
            tree = snapshot(tree_path, Path(scratch), base)
            if tree != base_tree:
                changed.append((tree_path, tree))
        if len(changed) > 1:
            names = ", ".join(str(path.relative_to(repo)) for path, _ in changed)
            raise Refused(f"{len(changed)} worktrees hold a change ({names}); "
                          "leave the result in one")
        source, tree = changed[0] if changed else (repo, snapshot(repo, Path(scratch), base))
    out.mkdir(parents=True, exist_ok=True)
    archive = subprocess.Popen(["git", *SAFE_GIT, "-C", str(source), "archive", "--format=tar",
                                tree], stdout=subprocess.PIPE)
    unpack = subprocess.run(["tar", "-x", "-C", str(out)], stdin=archive.stdout)
    archive.stdout.close()
    if archive.wait() != 0 or unpack.returncode != 0:
        raise GitFailed("git archive | tar -x failed")
    where = "the main checkout" if source == repo else str(source.relative_to(repo))
    return f"exported {where}, tree {tree}"


# --- collect (runner side) ---------------------------------------------------------------

def hostile_files(root: Path) -> list[tuple[str, int]]:
    """Every regular file of the export as (relative path, mode), or Refused."""
    files = []

    def walk(folder: Path, parts: tuple[str, ...]) -> None:
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                rel = parts + (entry.name,)
                shown = repr("/".join(rel))
                if entry.name.lower() == ".git":
                    raise Refused(f"{shown} is git data, not a file of the change")
                if rel[0].lower() == WORKTREES:
                    raise Refused(f"{shown} is under {WORKTREES}/")
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    walk(Path(entry.path), rel)
                elif stat.S_ISREG(info.st_mode):
                    files.append(("/".join(rel), info.st_mode))
                else:
                    kind = "a symlink" if stat.S_ISLNK(info.st_mode) else "not a regular file"
                    raise Refused(f"{shown} is {kind}")

    walk(root, ())
    return files


def copy_regular(source: Path, dest: Path, mode: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise Refused(f"{str(source)!r} changed under the copy")
        with os.fdopen(handle, "rb", closefd=False) as reader, open(dest, "wb") as writer:
            shutil.copyfileobj(reader, writer)
    finally:
        os.close(handle)
    os.chmod(dest, 0o755 if mode & 0o111 else 0o644)


def collect(export_dir: Path, pristine: Path, base: str, patch: Path) -> str:
    files = hostile_files(export_dir)
    if git(pristine, "rev-parse", "HEAD") != git(pristine, "rev-parse", f"{base}^{{commit}}"):
        raise Refused("the pristine checkout is not at the base sha")
    for entry in pristine.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    for rel, mode in files:
        copy_regular(export_dir / rel, pristine / rel, mode)
    git(pristine, "add", "-A")
    patch.parent.mkdir(parents=True, exist_ok=True)
    with open(patch, "wb") as handle:
        git(pristine, "diff", "--cached", "--binary", "--no-renames", "--no-ext-diff",
            "--no-textconv", base, out=handle)
    return f"collected {len(files)} files, patch {patch.stat().st_size} bytes"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collect_change", description=__doc__.split("\n")[0],
                                     epilog=f"examples:\n  {EXAMPLES}",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("export", help="model side: write the result tree as plain files")
    one.add_argument("--repo", required=True, type=Path)
    one.add_argument("--base", required=True)
    one.add_argument("--out", required=True, type=Path)
    two = sub.add_parser("collect", help="runner side: diff the export in a fresh checkout")
    two.add_argument("--export", required=True, type=Path)
    two.add_argument("--pristine", required=True, type=Path)
    two.add_argument("--base", required=True)
    two.add_argument("--patch", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            print(export(args.repo, args.base, args.out))
        else:
            print(collect(args.export, args.pristine, args.base, args.patch))
    except Refused as error:
        print(f"::error::refused: {error}", file=sys.stderr)
        return 1
    except GitFailed as error:
        print(f"::error::{error}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
