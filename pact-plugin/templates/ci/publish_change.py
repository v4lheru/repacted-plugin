#!/usr/bin/env python3
"""publish_change: turn the patch an unattended run produced into a draft pull request.

Location: pact-plugin/templates/ci/publish_change.py. The `publish` job of
templates/ci/unattended-compact.yml runs it from the plugin checkout pinned by sha, never from
the repository the patch changes. The recipe is docs/recipes/unattended-ci.md.

The patch was written by a model in another job, so it is hostile input. This script
  1. refuses a patch that `git apply --check --cached` refuses, or larger than --max-bytes;
  2. applies it to the index only (`--cached`): nothing from the patch reaches the working tree,
     and nothing from the patched tree is ever run;
  3. reads the staged result back from git (`diff --cached --raw -z`, not the patch text) and
     refuses, at any depth and in any case, the CI, git, agent, editor and shell config listed
     in REFUSED_DIRS and REFUSED_FILES, plus a binary change, a symlink, a submodule and a
     mode change;
  4. commits with hooks off, pushes one NEW branch `repacted/issue-<n>-run-<id>` without
     force, and runs `gh pr create --draft` against the default branch, listing any other
     dot-path of the change at the top of the PR body.
It never merges, never force-pushes and never pushes the default branch.

Exit codes: 0 pull request opened, or no change to publish; 1 the patch is refused;
2 usage error; 3 a git or gh command failed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 1024 * 1024
BRANCH_PREFIX = "repacted/issue-"
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
NUMBER = re.compile(r"[1-9][0-9]{0,11}")
# Config something runs or trusts from the tree, matched at any depth and in any case: CI and
# git's own files, the agent and guard config a maintainer's next session loads, what an editor
# or a shell runs on opening the folder, attributes that fold a file out of the PR review, and
# the review routing itself.
REFUSED_DIRS = (".github", ".git", ".claude", ".repacted", ".vscode", ".devcontainer")
REFUSED_FILES = (".mcp.json", "claude.md", "claude.local.md", ".gitattributes", ".envrc",
                 ".gitmodules", "codeowners")
MAX_LISTED = 50
SYMLINK, GITLINK, ABSENT = "120000", "160000", "000000"
# Hooks and an fsmonitor command are the two ways a repository's config runs a program on a
# plain commit; both are switched off for every git call here.
SAFE_GIT = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
EXAMPLE = ('python3 publish_change.py --repo target --patch change.patch --issue 7 '
           '--run-id 123 --base-branch main')


class Refused(Exception):
    """The patch is not published; the message says why."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}\n\n  {EXAMPLE}", file=sys.stderr)
        sys.exit(2)


def number_arg(value: str) -> str:
    if not NUMBER.fullmatch(value):
        raise argparse.ArgumentTypeError("a positive whole number")
    return value


def build_parser() -> Parser:
    parser = Parser(prog="publish_change", description=__doc__.split("\n")[0],
                    epilog=f"example:\n  {EXAMPLE}",
                    formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="a fresh checkout of the base commit")
    parser.add_argument("--patch", required=True, help="the patch from the work job")
    parser.add_argument("--issue", required=True, type=number_arg, help="the issue number")
    parser.add_argument("--run-id", required=True, type=number_arg, help="github.run_id")
    parser.add_argument("--base-branch", required=True, help="the default branch, the PR's base")
    parser.add_argument("--max-bytes", type=int, default=MAX_BYTES, help="the largest patch taken")
    return parser


def git(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(["git", *SAFE_GIT, "-C", repo, *args], capture_output=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {done.stderr.decode(errors='replace').strip()}")
    return done


def valid_branch(repo: str, name: str) -> bool:
    if not name or name.startswith("-"):
        return False
    return git(repo, "check-ref-format", "--branch", name, check=False).returncode == 0


def shown(path: str) -> str:
    """A model-written path as one inert line: control and non-ASCII characters escaped."""
    return json.dumps(path)[1:-1].replace("`", "\\x60")


def staged_entries(repo: str) -> list[tuple[str, str, str, str]]:
    """(old mode, new mode, status, path) of every staged change, read back from git."""
    raw = git(repo, "diff", "--cached", "--raw", "-z", "--no-renames", "HEAD").stdout
    fields = raw.split(b"\0")
    entries = []
    for meta, path in zip(fields[0::2], fields[1::2]):
        parts = meta.decode("ascii", errors="replace").lstrip(":").split()
        if len(parts) != 5:
            raise Refused(f"unreadable staged entry {meta!r}")
        entries.append((parts[0], parts[1], parts[4], path.decode("utf-8", errors="replace")))
    return entries


def binary_paths(repo: str) -> list[str]:
    """Staged paths git counts as binary: numstat shows `-<TAB>-` for them."""
    raw = git(repo, "diff", "--cached", "--numstat", "-z", "--no-renames", "HEAD").stdout
    return [record[4:].decode("utf-8", errors="replace")
            for record in raw.split(b"\0") if record.startswith(b"-\t-\t")]


def refusal(old_mode: str, new_mode: str, status: str, path: str) -> str:
    """Why this staged entry is refused, or ''."""
    parts = [part.lower() for part in path.split("/")]
    path = shown(path)
    if any(part in REFUSED_DIRS for part in parts) or parts[-1] in REFUSED_FILES:
        return f"{path}: changes to {', '.join(REFUSED_DIRS + REFUSED_FILES)} are never published"
    if SYMLINK in (old_mode, new_mode):
        return f"{path}: a symlink"
    if GITLINK in (old_mode, new_mode):
        return f"{path}: a submodule"
    if status == "M" and old_mode != new_mode:
        return f"{path}: a mode change {old_mode} -> {new_mode}"
    if status not in ("A", "M", "D"):
        return f"{path}: status {status}"
    if status == "A" and old_mode != ABSENT:
        return f"{path}: an added file with an old mode"
    return ""


def stage(repo: str, patch: Path, max_bytes: int) -> list[str]:
    """Apply the patch to the index after the checks; the staged paths, [] for no change."""
    size = patch.stat().st_size
    if size > max_bytes:
        raise Refused(f"the patch is {size} bytes, more than {max_bytes}")
    if size == 0:
        return []
    checked = git(repo, "apply", "--check", "--cached", str(patch), check=False)
    if checked.returncode != 0:
        raise Refused("git apply --check refused it: "
                      + checked.stderr.decode(errors="replace").strip()[:500])
    git(repo, "apply", "--cached", str(patch))
    entries = staged_entries(repo)
    reasons = [reason for reason in (refusal(*entry) for entry in entries) if reason]
    reasons += [f"{shown(path)}: a binary change, which a reviewer cannot read"
                for path in binary_paths(repo)]
    if reasons:
        git(repo, "read-tree", "HEAD")   # leave the checkout as it was
        raise Refused("; ".join(reasons[:10]))
    return [entry[3] for entry in entries]


def dot_path_note(paths: list[str]) -> str:
    """The PR body's first paragraph when a path has a dot-named part, else ''."""
    dotted = [path for path in paths if any(part.startswith(".") for part in path.split("/"))]
    if not dotted:
        return ""
    listed = ", ".join(f"`{shown(path)}`" for path in dotted[:MAX_LISTED])
    more = f" and {len(dotted) - MAX_LISTED} more" if len(dotted) > MAX_LISTED else ""
    return f"Dot-paths in this change, read them first: {listed}{more}.\n\n"


def publish(args) -> int:
    repo = str(Path(args.repo).resolve())
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not REPOSITORY.fullmatch(repository):
        print("publish_change: GITHUB_REPOSITORY must be <owner>/<name>", file=sys.stderr)
        return 2
    branch = f"{BRANCH_PREFIX}{args.issue}-run-{args.run_id}"
    if not valid_branch(repo, args.base_branch) or args.base_branch == branch:
        print(f"publish_change: not a usable base branch: {args.base_branch!r}", file=sys.stderr)
        return 2
    paths = stage(repo, Path(args.patch), args.max_bytes)
    if not paths:
        print("publish_change: the run changed nothing; no pull request")
        return 0
    message = f"Unattended comPACT change for issue #{args.issue}"
    git(repo, "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
        "commit", "--no-verify", "--quiet", "-m", message)
    git(repo, "push", "--no-verify", "origin", f"HEAD:refs/heads/{branch}")
    body = dot_path_note(paths) + (f"Opened by an unattended run of the rePACTed workflow for #{args.issue} "
            f"(run {args.run_id}). A model wrote this change: review every line, start the "
            "required checks, and merge only after a human approval.")
    created = subprocess.run(
        ["gh", "pr", "create", "--repo", repository, "--draft", "--base", args.base_branch,
         "--head", branch, "--title", message, "--body", body], capture_output=True, text=True)
    if created.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {created.stderr.strip()}")
    print(f"publish_change: opened {created.stdout.strip()} from {branch}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return publish(args)
    except Refused as why:
        print(f"publish_change: refused, nothing pushed: {why}", file=sys.stderr)
        return 1
    except (RuntimeError, OSError) as error:
        print(f"publish_change: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
