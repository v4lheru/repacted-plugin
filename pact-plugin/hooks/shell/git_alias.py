"""
Location: pact-plugin/hooks/shell/git_alias.py
Summary: What a git subcommand runs when it is an alias: `-c alias.<name>=...` on the command
         line, or `alias.<name>` in the repo's or the user's git config, asked of git itself
         where the call runs (`git <the call's global options> config --get alias.<name>`).
         An alias may name another alias; a `!` alias runs a shell script, returned as its
         body. Also reads the value a `git config ... alias.<name> <value>` call sets.
Used by: hooks/merge_guard_pre.py (judges the expansion with the merge check) and
         hooks/shell/env/git_egress.py (runs the push, commit and add scans on it).

Standard library and shell.words only. git ignores an alias that shadows one of its own
commands, so a subcommand in GIT_COMMANDS is never looked up (and no git runs for it).
Limits: a lookup git cannot answer in GIT_TIMEOUT_S counts as no alias; an include.path or
a config file the same command writes before the alias runs is read as it stands now.
"""

from __future__ import annotations

import os
import subprocess

from shell.words import tokens

GIT_TIMEOUT_S = 5
MAX_DEPTH = 8
# git's own commands (git --list-cmds=main,nohelpers, 2.54): never an alias.
GIT_COMMANDS = frozenset("""
add am annotate apply archive backfill bisect blame branch bugreport bundle cat-file check-attr
check-ignore check-mailmap check-ref-format checkout checkout-index cherry cherry-pick clean
clone column commit commit-graph commit-tree config count-objects credential credential-cache
credential-store daemon describe diagnose diff diff-files diff-index diff-pairs diff-tree
difftool fast-export fast-import fetch fetch-pack filter-branch fmt-merge-msg for-each-ref
for-each-repo format-patch fsck fsck-objects gc get-tar-commit-id grep hash-object help history
hook http-backend http-fetch http-push imap-send index-pack init init-db interpret-trailers
last-modified log ls-files ls-remote ls-tree mailinfo mailsplit maintenance merge merge-base
merge-file merge-index merge-octopus merge-one-file merge-ours merge-recursive
merge-recursive-ours merge-recursive-theirs merge-resolve merge-subtree merge-tree mergetool
mktag mktree multi-pack-index mv name-rev notes pack-objects pack-redundant pack-refs patch-id
prune prune-packed pull push quiltimport range-diff read-tree rebase receive-pack reflog refs
remote repack replace replay repo request-pull rerere reset restore rev-list rev-parse revert
rm send-email send-pack shell shortlog show show-branch show-index show-ref sparse-checkout
stage stash status stripspace submodule subtree switch symbolic-ref tag unpack-file
unpack-objects update-index update-ref update-server-info upload-archive upload-pack var
verify-commit verify-pack verify-tag version whatchanged worktree write-tree lfs
""".split())
# git's global options that take the next word as their value.
VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env",
                           "--super-prefix", "--exec-path"})


def subcommand_at(args: list[str]) -> int | None:
    """The index of the subcommand in the words after `git`, past its global options."""
    i = 0
    while i < len(args):
        if args[i] in VALUE_OPTIONS:
            i += 2
        elif args[i].startswith("-"):
            i += 1
        else:
            return i
    return None


def lookup(globals_: list[str], name: str, cwd: str | None) -> str | None:
    """What git says `alias.<name>` is where the call runs (its -C, -c, --git-dir words
    passed through), or None: no such alias, or git did not answer."""
    where = cwd if cwd and os.path.isdir(cwd) else None
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        done = subprocess.run(["git", *globals_, "config", "--get", f"alias.{name}"],
                              capture_output=True, text=True, timeout=GIT_TIMEOUT_S, cwd=where,
                              env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def expand(args: list[str], cwd: str | None, ask=lookup) -> tuple[list[str], str | None]:
    """(the words after `git` with each alias spelled out as git spells it, the shell script
    a `!` alias runs or None). Words that hold no alias come back unchanged. `ask(globals,
    name, cwd)` answers one alias (lookup by default)."""
    for _ in range(MAX_DEPTH):
        at = subcommand_at(args)
        if at is None:
            return args, None
        name = args[at]
        if name in GIT_COMMANDS or not name or any(c in name for c in "$`*?[ /"):
            return args, None
        value = ask(args[:at], name, cwd)
        if value is None:
            return args, None
        if value.startswith("!"):
            return args, value[1:]
        words = tokens(value) or value.split()
        args = args[:at] + list(words) + args[at + 1:]
    return args, None


def defined_values(args: list[str]) -> list[str]:
    """The alias values a `git config` call sets (`git config [--global] alias.x 'push -f'`,
    `git config set alias.x ...`, `--add`, `--replace-all`), from the words after `git`."""
    at = subcommand_at(args)
    if at is None or args[at] != "config":
        return []
    rest = args[at + 1:]
    return [rest[i + 1] for i, word in enumerate(rest[:-1])
            if word.lower().startswith("alias.") and not rest[i + 1].startswith("-")]
