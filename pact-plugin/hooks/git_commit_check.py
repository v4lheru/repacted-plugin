#!/usr/bin/env python3
"""PreToolUse hook (matcher Bash): checks a git commit for secrets and frontend credential use.

Enforces no credentials in committed files, no frontend credential exposure (backend proxy
pattern), and .env ignored by git. It scans what git will commit (the index, plus the working
tree for -a, a pathspec, -i/-o or -p), added lines only; a git read that fails denies. A
`git add` earlier in the same command is replayed on a temporary index copy first; a staging
step that cannot be replayed denies the combined form (stage and commit in two calls).
The command is read through shared/commit_routes.py (comments, here-documents, aliases, closed
option tables, a name or HEAD moved by an earlier step); one that names git with commit or a
history verb and cannot be read is denied. It also refuses a `git stash` that takes or drops an
entry from the stack every worktree shares (bare stash, an untagged push, save, pop, clear, a
drop that names no entry; rule git_commit_check.shared_stash), read the same way. Exit 2 blocks,
0 allows.

Module map and notes: the design notes.
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_deny BEFORE wrapped imports) ─
import sys
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from functools import partial
from dataclasses import dataclass, field
from typing import NoReturn, Optional

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _deny(reason: str) -> NoReturn:
    """Print a PreToolUse deny with `reason` and exit 2."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    print(reason, file=sys.stderr)
    sys.exit(2)


# The one fail-closed emitter (hooks/_fail_closed.py, stdlib only); bound to this check's name.
from _fail_closed import emit_load_failure_deny

_emit_load_failure_deny = partial(emit_load_failure_deny, "git_commit_check")


# ─── fail-closed wrapper on cross-package imports ──────────────────────────
try:
    from shared.error_output import hook_error_json  # noqa: F401  # re-export: tests import it here
    from shared.git_helpers import run_git
    from shared import commit_routes as routes
    from shared.log_values import logs_secret_value, logs_token_value  # noqa: F401
    from shell_words import PREFIX_WORDS, strip_prefix, tokens as shell_tokens
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)


def _load_detector() -> None:
    """Bind the shared secret detector (D25), which the push scan uses too, so commit and push
    agree on what a secret line is (ENV-GUARD-2 amend_2). Loaded on the commit path only: this
    hook runs before every Bash call. A failure here raises into main(), which denies."""
    if "secret_kind" in globals():
        return
    global ALLOWLIST_PRAGMA, pragma_covers, pragma_notice, scan_added_lines, secret_kind, _HUNK, \
        _diff_path
    from shared.scrub import (ALLOWLIST_PRAGMA, _HUNK, _diff_path, pragma_covers, pragma_notice,
                              scan_added_lines, secret_kind)


class Unverifiable(Exception):
    """What the commit takes could not be read: the commit is denied, never waved through."""


class NoRepository(Unverifiable):
    """Git finds no repository where a commit or a route of the command will run (none there,
    or a directory the command itself makes): denied with the form to use instead. There is no
    reading of what a later step of the same command will have made of that place."""


class Unread(Exception):
    """The command (or a script it nests) cannot be split into words and names git, then
    `commit` or a verb that brings history in: a shell may still run its complete lines (a
    quote left open further on), so it is denied. Comments and here-documents are read."""


class StageSeparately(Exception):
    """A staging step in the commit's command could not be replayed: the combined form is denied
    with a reason that asks for staging and committing in two calls."""


class SharedStash(Exception):
    """A command with no commit runs a `git stash` form shared_stash refuses: denied."""


@dataclass
class _StashStep:
    """A refused `git stash` call, kept in read_commit's `before` beside the other steps."""
    form: str


STASH_RULE = "git_commit_check.shared_stash"
_STASH_MESSAGE = re.compile(r"--message(?:=.*)?|-[A-Za-z]*m.*")   # -m, -mTAG, -um TAG, --message=


def shared_stash(args: list) -> Optional[str]:
    """The refused form of `git stash <args>` (`git stash pop`, ...), or None for one that passes.
    The stash stack is one per repository, shared by every worktree and session: a bare stash or
    an untagged push cannot be found again by its tag, and save (no tag to look up by), pop,
    clear and a drop with no entry act on whichever entry is on top."""
    sub, rest = (args[0], args[1:]) if args and not args[0].startswith("-") else ("push", args)
    if sub == "push":
        options = rest[:rest.index("--")] if "--" in rest else rest
        if any(_STASH_MESSAGE.fullmatch(word) for word in options):
            return None
        return "git stash push" if args and args[0] == "push" else "git stash"
    if sub in ("save", "pop", "clear"):
        return f"git stash {sub}"
    if sub == "drop" and not any(not word.startswith("-") for word in rest):
        return "git stash drop"
    return None


def _stash_refusal(form: str) -> str:
    return (f"PACT git_commit_check: `{form}` works on the one stash stack every worktree and "
            "session of this repository shares, so it can take, apply or drop another session's "
            "entry. Set work aside with a WIP commit, or with `git stash push -u -m <tag>` and "
            "later `git stash apply <sha>` (the sha from `git stash list --format='%H %gs'`). "
            f"[rule: {STASH_RULE}]")


# Each git read is bounded; a slower git denies the commit (it is never allowed unscanned).
GIT_TIMEOUT_S = 30
# Environment a commit's own assignments can point elsewhere: the scan reads the same repo and index.
_GIT_LOCATION_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                     "GIT_SEQUENCE_EDITOR")
_GIT_LOCATION_OPTIONS = ("--git-dir", "--work-tree")
_OPERATORS = ";&|()\n"
_ROUTE_WORDS = "merge|cherry-pick|revert|rebase|pull|am|commit-tree|apply"


def _git_then(verbs: str):
    """True for a text with the word git (also /usr/bin/git, GIT, rtk [proxy] git) and one of
    `verbs` as a whole word anywhere after it: two searches, each linear in the text."""
    git = re.compile(r"(?<![\w.-])git(?![\w-])", re.I)
    verb = re.compile(r"(?<![\w.-])(?:%s)(?![\w-])" % verbs, re.I)

    def search(text: str) -> bool:
        found = git.search(text)
        return bool(found and verb.search(text, found.end()))
    return search


_UNREAD_ROUTE, _UNREAD = _git_then(_ROUTE_WORDS), _git_then(_ROUTE_WORDS + "|commit")
MAX_NESTING = 8                                              # sh -c 'sh -c ...' levels read
# Staging before a commit: add/stage replay, _NO_NEW_CONTENT passes, the rest deny (note).
_STAGING = {"add", "stage"}
_NO_NEW_CONTENT = frozenset((
    "status diff log show branch checkout switch restore reset merge pull fetch rebase "
    "cherry-pick revert tag remote config init clone worktree mv rm grep blame describe "
    "rev-parse rev-list ls-files ls-tree cat-file show-ref symbolic-ref for-each-ref reflog "
    "shortlog check-ignore check-attr var version help push gc fsck notes clean bisect").split())
_ADD_INTERACTIVE = {"--patch", "--interactive", "--edit"}
_REDIRECT = re.compile(r"^\d*(?:[<>]|>>|>&|<&)")


@dataclass
class Commit:
    """A git commit the command runs: where (repo, git location options, environment) and its
    words after `commit` (a plain commit by default). args None: a commit the parser could not
    read, which scans widest. stagings: the `git add`/`git stage` calls the same command runs
    before it (Commits whose args are the add's words); stage_issue: why one could not be
    replayed (the combined form is then denied)."""
    repo: Optional[str] = None
    options: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    args: Optional[list] = field(default_factory=list)
    ranges: Optional[list] = None                            # _diff_ranges, read once
    stagings: list = field(default_factory=list)
    stage_issue: Optional[str] = None
    verb: str = "commit"                                     # "route": only routes to scan
    head: Optional[tuple] = None                             # routes.follow_head: where HEAD may move
    routes: list = field(default_factory=list)               # cherry-pick, merge, am, ... calls
    stash_issue: Optional[str] = None                        # a refused `git stash` form it runs

    def git(self, *args: str) -> list:
        return (["-C", self.repo] if self.repo else []) + self.options + list(args)

    def ask(self, *args: str):
        """git `args` where the commit runs: the CompletedProcess, or None on a timeout."""
        return run_git(self.git(*args), timeout=GIT_TIMEOUT_S,
                       env={**os.environ, **self.env} if self.env else None)


def _moved(path: Optional[str], to: str) -> str:
    return os.path.normpath(os.path.join(path or os.getcwd(), os.path.expanduser(to)))


def _assignment(word: str) -> Optional[tuple]:
    """(name, value) of an assignment that says where git works or what its config holds."""
    name, sep, value = word.partition("=")
    return (name, value) if sep and name.isidentifier() and (
        name in _GIT_LOCATION_ENV or name.startswith("GIT_CONFIG")) else None


def _is_git(word: str) -> bool:
    return os.path.basename(word).lower() == "git"       # GIT runs git on a case-blind disk


def read_commit(command: str, cwd: Optional[str], depth: int = 0,
                _before: Optional[list] = None) -> Optional[Commit]:
    """The first git commit `command` runs, carrying the other commit-creating calls (`routes`);
    a route-only Commit when it runs none; None when it runs neither or cannot be read. Read the
    way the shell splits it; how cd, -C, GIT_DIR, the stagings before it and nested scripts
    travel: the design notes. `_before` collects the calls in order."""
    base = os.path.abspath(os.path.expanduser(cwd)) if cwd else None
    words = routes.script_words(command, _OPERATORS)
    if words is None:
        if _UNREAD(command):
            raise Unread(depth)
        return None
    before = [set(), {}] if _before is None else _before     # the names it moves; where HEAD goes
    exported, dirs = {}, []
    segments, current = [], []
    for word in words + [";"]:
        if word and set(word) <= set(_OPERATORS):
            segments.append(current)
            current = []
        else:
            current.append(word)
    for segment in segments:
        while segment and segment[0] == "{":
            segment = segment[1:]
        command_words = strip_prefix(segment)
        while command_words and command_words[0] not in PREFIX_WORDS \
                and os.path.basename(command_words[0]).lower() in PREFIX_WORDS:
            # ENV, /usr/bin/env: the same runner, spelled as strip_prefix reads it
            segment = segment[:len(segment) - len(command_words)] + [
                os.path.basename(command_words[0]).lower()] + command_words[1:]
            command_words = strip_prefix(segment)
        env = dict(exported)
        env.update(filter(None, map(_assignment, segment[:len(segment) - len(command_words)])))
        if not command_words:
            continue
        head = command_words[0]
        base = routes.shell_step(command_words, base, dirs, before)
        if head == "export":
            exported.update(filter(None, map(_assignment, command_words[1:])))
        elif _is_git(head):
            sub, call = _git_call(command_words, base, env)
            if sub == "stash" and shared_stash(call.args):
                before.append(_StashStep(shared_stash(call.args)))
            if sub and sub[0] != "!":
                routes.follow_head(sub, call, before, call.ask)
            if sub and (sub == "commit" or sub in routes.ROUTE_VERBS):
                routes.refuse_moved(sub, call.args, before[0])
            elif sub and call.verb == "paths":                   # paths copied out of a tree
                before.append(call)
            elif sub:
                before[0] |= routes.moved_names(sub, call.args, call.repo)
            if sub and sub.startswith("!"):                      # an alias that runs a shell script
                if depth >= MAX_NESTING:
                    raise routes.Unreadable("a git alias runs scripts more than 8 deep")
                read_commit(sub[1:], call.repo, depth + 1, before)
            elif sub == "commit" or routes.continues(sub, call.args):
                before.append(call)
            elif sub in routes.ROUTE_VERBS:
                call.verb = sub
                before.append(call)
            elif sub in _STAGING and "xargs" in map(os.path.basename, segment[:-len(command_words)]):
                before.append("`xargs` adds words to `git add` at run time")
            elif sub in _STAGING:
                call.verb = "add"
                before.append(call)
            elif sub is not None and sub not in _NO_NEW_CONTENT:
                if routes.ALIASED in before[0]:
                    raise routes.Unreadable(f"`git {sub}` may be an alias an earlier step of the "
                                            "same command defines")
                before.append(f"`git {sub}` runs before the commit and what it stages cannot "
                              "be read ahead")
        elif len(command_words) == 1 and depth < MAX_NESTING and re.search(r"\s", head):
            mark = len(before)
            try:                                                 # the script of sh -c / eval
                read_commit(head.lstrip(routes.BODY), base, depth + 1, before)
            except Unread:
                if head[0] != routes.BODY or _UNREAD_ROUTE(head):
                    raise
                # a heredoc body that names only `git commit`: prose, or a script; scan widest
                before.append(Commit(repo=base, args=None))
            for step in before[mark:]:
                if isinstance(step, Commit):
                    step.env = {**env, **step.env}
        elif any(_is_git(word) for word in command_words[1:]):
            before.append(f"`{os.path.basename(head)}` runs git in a way that cannot be read ahead")
    calls = [step for step in before if isinstance(step, Commit) and step.verb != "add"]
    commits = [step for step in calls if step.verb == "commit"]
    calls = [step for step in calls if commits or step.verb != "paths"]  # read only for a commit
    stash = next((step.form for step in before if isinstance(step, _StashStep)), None)
    if not calls or _before is not None:
        if stash and _before is None:
            raise SharedStash(stash)
        return None
    if not commits:
        return Commit(calls[0].repo, calls[0].options, calls[0].env, verb="route", routes=calls,
                      stash_issue=stash)
    first, prior = commits[0], before[:before.index(commits[0])]
    first.stagings = [step for step in prior if isinstance(step, Commit) and step.verb == "add"]
    first.stage_issue = next((step for step in prior if isinstance(step, str)), None) or (
        "it runs a second commit, whose content cannot be read ahead" if commits[1:] else None)
    first.routes = [step for step in calls if step.verb != "commit"]
    first.stash_issue = stash
    return first


def _git_call(words: list, base: Optional[str], env: dict) -> tuple:
    """(subcommand or None, a Commit holding where it runs and the words after the subcommand)
    for a `git ...` word list. An alias is expanded as git expands it; one that runs a shell
    script comes back as `!script`. routes.Unreadable for an option of git not in its table."""
    for _ in range(MAX_NESTING):
        at, pairs = routes.git_globals(words)
        where, options, config = base, [], []
        for name, value in pairs:
            if name == "-C":
                where = _moved(where, value)
            elif name in _GIT_LOCATION_OPTIONS:
                options.append(f"{name}={_moved(where, value)}")
            elif name in ("-c", "--config-env"):
                config += [name, value]
        if at is None:
            return None, Commit(repo=where, options=options, env=env)
        sub, call = words[at], Commit(repo=where, options=options, env=env, args=words[at + 1:])
        if sub == "commit" or sub in routes.ROUTE_VERBS | _STAGING | _NO_NEW_CONTENT:
            return sub, call
        there = os.path.isdir(where or os.getcwd())             # else: a directory it makes
        asked = call if there else Commit(env=env)
        alias = routes.alias(sub, lambda *args: asked.ask(*config, *args))
        if alias is None:
            return sub, call
        if alias.startswith("!"):
            return alias, call
        words = words[:at] + (shell_tokens(alias) or [alias]) + words[at + 1:]
    raise routes.Unreadable("a git alias expands to aliases more than 8 deep")


def find_commit(command: str, cwd: Optional[str]) -> tuple:
    """(is_commit, repo): whether `command` runs a git commit, and the directory it runs in
    (the payload cwd, or None for this process's cwd, when it runs none)."""
    try:
        commit = read_commit(command, cwd)
    except (Unread, routes.Unreadable, SharedStash):
        commit = None
    if commit:
        return True, commit.repo
    return False, os.path.abspath(os.path.expanduser(cwd)) if cwd else None


def _commit_args(command: str) -> list:
    """The words after `commit` in the first git commit of `command` (quoting honoured, so a -m
    message is one word), or [] when there is none or it cannot be read."""
    try:
        commit = read_commit(command, None)
    except (Unread, routes.Unreadable, SharedStash):
        return []
    return (commit.args or []) if commit else []


def _run(commit: Commit, *args: str, text: bool = True):
    """git `args` in the commit's repo, index and environment; Unverifiable on a timeout, a
    missing git or a failing exit. The CompletedProcess otherwise."""
    env = {**os.environ, **commit.env} if commit.env else None
    result = run_git(commit.git(*args), timeout=GIT_TIMEOUT_S, text=text, env=env)
    if result is None:
        raise Unverifiable(f"git did not answer within {GIT_TIMEOUT_S} s (or could not be run)")
    if result.returncode != 0:
        raise Unverifiable(f"`git {args[0]}` exited {result.returncode}")
    return result


def _add_words(step: Commit) -> list:
    """The words of a `git add` to replay, redirections dropped; StageSeparately when one cannot
    be replayed as typed (the shell expands it, it is interactive, it reads stdin)."""
    words, skip, literal = [], False, False
    for word in step.args:
        if skip:
            skip = False
            continue
        if not literal and _REDIRECT.match(word):
            skip = bool(re.fullmatch(r"\d*(?:[<>]|>>|>&|<&)", word))   # its target is the next word
            continue
        if "$" in word or "`" in word or word.startswith("~"):
            raise StageSeparately(f"`{word}` in `git add` is expanded by the shell at run time")
        if not literal and word.startswith("-"):
            if word == "--":
                literal = True
            elif word in _ADD_INTERACTIVE or (not word.startswith("--") and set(word[1:]) & set("pie")):
                raise StageSeparately(f"`git add {word}` is interactive")
            elif word.startswith("--pathspec-from-file") and word.rpartition("=")[2] in ("-", word):
                raise StageSeparately("`git add --pathspec-from-file` reads its paths at run time")
        words.append(word)
    return words


@contextmanager
def replayed_stagings(commit: Commit):
    """Replay the command's `git add` steps on a temporary copy of the commit's index and point
    the commit's scan at it, so it reads what the commit will take. The copy is always removed.
    StageSeparately when a step cannot be replayed; Unverifiable when git does not answer."""
    if commit.stage_issue:
        raise StageSeparately(commit.stage_issue)
    if not commit.stagings:
        yield
        return
    for step in commit.stagings:
        if (step.repo, step.options, step.env) != (commit.repo, commit.options, commit.env):
            raise StageSeparately("`git add` runs in another directory, repository or index "
                                  "than the commit")
    env = {**os.environ, **commit.env}
    found = run_git(commit.git("rev-parse", "--git-path", "index"), timeout=GIT_TIMEOUT_S, env=env)
    if found is None:
        raise Unverifiable(f"git did not answer within {GIT_TIMEOUT_S} s (or could not be run)")
    if found.returncode != 0:
        raise StageSeparately("the repository it stages into does not exist yet")
    index = os.path.join(commit.repo or os.getcwd(), found.stdout.strip())
    scratch = tempfile.mkdtemp(prefix="pact-commit-index-")
    saved_env, saved_ranges = commit.env, commit.ranges
    try:
        copy = os.path.join(scratch, "index")
        if os.path.isfile(index):
            shutil.copyfile(index, copy)
        env["GIT_INDEX_FILE"] = copy
        for step in commit.stagings:
            result = run_git(commit.git("add", *_add_words(step)), timeout=GIT_TIMEOUT_S, env=env)
            if result is None:
                raise Unverifiable(f"git did not answer within {GIT_TIMEOUT_S} s (or could not be run)")
            if result.returncode != 0:
                first = (result.stderr or "").strip().splitlines()[:1]
                raise StageSeparately(f"`git add` fails when replayed ({first[0] if first else 'no message'})")
        commit.env, commit.ranges = {**commit.env, "GIT_INDEX_FILE": copy}, None
        yield
    finally:
        commit.env, commit.ranges = saved_env, saved_ranges
        shutil.rmtree(scratch, ignore_errors=True)


def _diff_ranges(commit: Commit) -> list:
    """routes.commit_ranges, read once: the index is always read, even where `-o`/`-a` would
    leave part of it out (a superset, so no misread option can drop staged content from the scan)."""
    if commit.ranges is None:
        commit.ranges = routes.commit_ranges(commit)
    if commit.ranges is None:
        raise Unverifiable(f"git did not answer within {GIT_TIMEOUT_S} s (or could not be run)")
    return commit.ranges


_NO_REPO = ("not a git repository", "cannot change to")


def committed_files(commit: Commit) -> Optional[list]:
    """The paths the commit takes, EXCLUDING deletions. Raises NoRepository where git finds no
    repository for the commit at hook time, Unverifiable when git cannot list them.

    `--diff-filter=d` excludes deletion-only stagings so a `git rm --cached <file>` remediation is
    not flagged: the deleted path has no content to scan and no new secret to leak."""
    files: list = []
    for diff in _diff_ranges(commit):
        try:
            result = _run(commit, "diff", "--name-only", "--diff-filter=d", *diff)
        except Unverifiable:
            probe = commit.ask("rev-parse", "--git-dir")
            if probe is not None and probe.returncode == 128 and any(
                    text in probe.stderr for text in _NO_REPO):
                raise NoRepository() from None
            raise
        files += [name for name in result.stdout.splitlines() if name not in files]
    return files


def commit_patch(commit: Commit) -> list:
    """The -U0 patches of what the commit takes, one per diff range, decoded from bytes (text mode
    would turn a lone \\r in a line into a line break). Raises Unverifiable when git cannot show it."""
    return [_run(commit, "diff", "-U0", "--no-color", "--no-ext-diff", "--diff-filter=d",
                 *diff, text=False).stdout.decode("utf-8", "replace")
            for diff in _diff_ranges(commit)]


def _patches(patches) -> list:
    return [patches] if isinstance(patches, str) else list(patches)


def require_repository(call: Commit) -> None:
    """NoRepository unless git, asked at hook time, finds a repository where `call` will run."""
    probe = call.ask("rev-parse", "--git-dir")
    if probe is None:
        raise Unverifiable(f"git did not answer within {GIT_TIMEOUT_S} s (or could not be run)")
    if probe.returncode != 0:
        raise NoRepository()


def added_lines(patches) -> dict:
    """{path: [(line number in the new file, text)]} for the ADDED lines of -U0 patches, read the
    way scan_added_lines reads them (a `+` line inside a hunk is content, /dev/null is no path).
    A line two patches both add (staged and in the working tree) is listed once."""
    _load_detector()
    added: dict = {}
    seen: set = set()
    for patch in _patches(patches):
        _read_added(patch, added, seen)
    return added


def _read_added(patch: str, added: dict, seen: set) -> None:
    path: Optional[str] = None
    number = new_left = old_left = 0
    for line in patch.split("\n"):
        if new_left > 0 or old_left > 0:
            if line.startswith("+"):
                new_left -= 1
                if path and (path, number, line) not in seen:
                    seen.add((path, number, line))
                    added.setdefault(path, []).append((number, line[1:]))
                number += 1
            elif line.startswith("-"):
                old_left -= 1
            elif line.startswith(" "):
                new_left, old_left, number = new_left - 1, old_left - 1, number + 1
            continue
        if line.startswith("+++ "):
            target = line[4:].rstrip("\r")
            path = None if target == "/dev/null" else _diff_path(target)
        elif line.startswith("@@"):
            hunk = _HUNK.match(line)
            if hunk:
                old_left = int(hunk.group(2) or 1)
                number, new_left = int(hunk.group(3)), int(hunk.group(4) or 1)
    return added


def commit_messages(command: str, repo: Optional[str] = None) -> list:
    """Every message text a git commit command carries: each -m/--message value (also attached,
    `--message=`, and the tail of a short cluster such as -am), and the file of -F/--file."""
    args, texts, files, i = _commit_args(command), [], [], 0
    while i < len(args):
        word = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if word in ("-m", "--message") and nxt is not None:
            texts.append(nxt)
            i += 2
            continue
        if word in ("-F", "--file") and nxt is not None:
            files.append(nxt)
            i += 2
            continue
        if word == "--trailer" and nxt is not None:
            texts.append(nxt)
            i += 1
        elif word.startswith("--message=") or word.startswith("--trailer="):
            texts.append(word.split("=", 1)[1])
        elif word.startswith("--file="):
            files.append(word.split("=", 1)[1])
        elif word.startswith("-") and not word.startswith("--") and len(word) > 2:
            for position, letter in enumerate(word[1:], 1):
                if letter in "mF":
                    value = word[position + 1:] or nxt
                    if value is not None:
                        (texts if letter == "m" else files).append(value)
                        if not word[position + 1:]:
                            i += 1
                    break
        i += 1
    for name in files:
        if name == "-":
            continue
        path = os.path.join(repo or os.getcwd(), os.path.expanduser(name))
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                texts.append(handle.read())
        except OSError:
            continue                                    # git will refuse a missing file itself
    return texts


def check_message(command: str, repo: Optional[str] = None) -> list:
    """A secret value in the commit message leaves with every push; the pragma never covers it."""
    _load_detector()
    texts = commit_messages(command, repo)
    for part, text in enumerate(texts, 1):
        for number, line in enumerate(text.splitlines(), 1):
            kind = secret_kind(line)
            if kind:
                where = f" part {part} of {len(texts)} (-m/-F values in order)" if len(texts) > 1 else ""
                return [f"SECRET: line {number} of the commit message{where} matches the {kind} secret "
                        "pattern; remove the value from the message before committing."]
    return []


def check_added_secrets(patches) -> tuple:
    """(errors, skipped) over the ADDED lines of every file in `patches`, whatever its type, on the
    shared detector. A line whose own pragma covers it (one value per pragma line) is skipped (and
    counted: per path, the most any one patch skipped, so a line both staged and in the working
    tree counts once)."""
    _load_detector()
    lines: dict = {}
    most: dict = {}
    for patch in _patches(patches):
        hits, skipped = scan_added_lines(patch)
        for path, kind, number in hits:
            lines.setdefault((path, kind), []).append(number)
        for path in set(skipped):
            most[path] = max(most.get(path, 0), skipped.count(path))
    skipped = [path for path, count in most.items() for _ in range(count)]
    errors = [f"SECRET: a {kind} value is added in {path}{_at_lines(numbers)} (the {kind} secret pattern); "
              f"remove it before committing. If you believe it is a test fixture or doc example, ask the "
              f"user to confirm the value is not real before adding `{ALLOWLIST_PRAGMA}` on that line "
              f"(one value per pragma line)."
              for (path, kind), numbers in lines.items()]
    return errors, skipped


def _at_lines(numbers) -> str:
    """' line 3' / ' lines 3, 9' for a message; a number the diff did not give (0) is left out.
    Only numbers: a message never carries the matched value."""
    known = sorted({n for n in numbers if n > 0})
    if not known:
        return ""
    return f" line{'s' if len(known) > 1 else ''} {', '.join(map(str, known))}"


PATTERNS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "git_commit_check_patterns.json")
_patterns_read: dict = {}


def patterns() -> dict:
    """The pattern lists (PATTERNS_FILE says what each is), read once, on the commit path only.
    A missing or malformed file raises into main(), which denies."""
    if not _patterns_read:
        with open(PATTERNS_FILE, encoding="utf-8") as handle:
            _patterns_read.update({k: tuple(map(tuple, v)) if k == "commit_only_patterns" else tuple(v)
                                   for k, v in json.load(handle).items() if not k.startswith("_")})
    return _patterns_read


def check_security(staged_files, added: Optional[dict] = None, *, repo: Optional[str]):
    """Check for basic security violations in what the commit takes. staged_files are paths
    in `repo` (None: unknown)."""
    from shell.env.names import file_is_protected, is_protected_name, is_template_name
    errors = []
    added = added or {}

    # 1. Check for .env files being committed. A template name (.env.example) may be: its
    #    content is scanned like any other file, and a link to a real env file is not one.
    for f in staged_files:
        name, path = os.path.basename(f), os.path.join(repo, f) if repo else None
        seen = path and os.path.exists(path)
        if is_protected_name(name) and is_template_name(name) and not seen:
            errors.append(f"SACROSANCT VIOLATION: Attempting to commit environment file: {f}. The "
                          "template could not be checked on disk: it may link to a real one.")
        elif file_is_protected(name, path or f):
            errors.append(
                f"SACROSANCT VIOLATION: Attempting to commit environment file: {f}. "
                "Only a template (.env.example, .env.sample, .env.template, .env.dist) "
                "that holds no real value may be committed."
            )

    # 2. Check for sensitive data in logs, on added lines only
    for f, lines in added.items():
        if not f.endswith(patterns()["log_scan_extensions"]):
            continue
        for pattern in patterns()["env_log_patterns"]:
            numbers = [n for n, line in lines if re.search(pattern, line, re.IGNORECASE)]
            if numbers:
                errors.append(
                    f"SECURITY: Potential secret exposure in log in {f}{_at_lines(numbers)}: "
                    f"matches pattern '{pattern}'"
                )
        numbers = [n for n, line in lines if logs_secret_value(line)]
        if numbers:
            errors.append(
                f"SECURITY: Potential secret exposure in log in {f}{_at_lines(numbers)}: "
                "a log call passes a value named by the password, secret, api key or token pattern"
            )

    return errors


def check_frontend_credentials(staged_files, added: dict):
    """SACROSANCT Rule 2: Check for credential exposure in frontend code."""
    errors = []

    credential_patterns = [
        r'VITE_[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)',
        r'REACT_APP_[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)',
        r'NEXT_PUBLIC_[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)',
        r'NUXT_PUBLIC_[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)',
        r'process\.env\.(VITE_|REACT_APP_|NEXT_PUBLIC_|NUXT_PUBLIC_)[A-Z_]*(?:KEY|SECRET|TOKEN)',
        r'import\.meta\.env\.(VITE_)[A-Z_]*(?:KEY|SECRET|TOKEN)',
    ]

    frontend_extensions = {'.jsx', '.tsx', '.vue', '.svelte'}
    frontend_dirs = {'src', 'components', 'pages', 'app', 'frontend', 'client', 'ui'}

    for f in staged_files:
        is_frontend_ext = any(f.endswith(ext) for ext in frontend_extensions)
        is_frontend_dir = any(
            f'/{d}/' in f or f.startswith(f'{d}/') for d in frontend_dirs
        )

        should_check = is_frontend_ext or (
            f.endswith(('.js', '.ts')) and is_frontend_dir
        )

        if should_check:
            content = "\n".join(line for _, line in added.get(f, []))
            for pattern in credential_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    errors.append(
                        f"SACROSANCT VIOLATION: Frontend credential exposure in {f}. "
                        f"Found: {matches[0]}. Credentials must NEVER be in frontend code. "
                        "Use backend proxy pattern instead."
                    )

    return errors


def check_direct_api_calls(staged_files, added: dict):
    """SACROSANCT Rule 2: Warn about potential direct API calls from frontend."""
    warnings = []

    direct_api_patterns = [
        (r'fetch\s*\(\s*[\'"`]https?://api\.', 'fetch to external API'),
        (r'axios\.[a-z]+\s*\(\s*[\'"`]https?://api\.', 'axios to external API'),
        (r'fetch\s*\(\s*[\'"`]https?://[^/]*\.stripe\.com', 'direct Stripe API call'),
        (r'fetch\s*\(\s*[\'"`]https?://[^/]*\.openai\.com', 'direct OpenAI API call'),
        (r'fetch\s*\(\s*[\'"`]https?://[^/]*\.anthropic\.com', 'direct Anthropic API call'),
        (r'fetch\s*\(\s*[\'"`]https?://[^/]*\.github\.com/(?!repos/[^/]+/[^/]+$)', 'direct GitHub API call'),
        (r'fetch\s*\(\s*[\'"`]https?://[^/]*\.googleapis\.com', 'direct Google API call'),
    ]

    frontend_extensions = {'.jsx', '.tsx', '.vue', '.svelte', '.js', '.ts'}
    frontend_dirs = {'src', 'components', 'pages', 'app', 'frontend', 'client', 'ui'}
    # Backend directories to exclude
    backend_dirs = {'server', 'api', 'backend', 'lib', 'services', 'handlers'}

    for f in staged_files:
        is_frontend_ext = any(f.endswith(ext) for ext in frontend_extensions)
        is_frontend_dir = any(
            f'/{d}/' in f or f.startswith(f'{d}/') for d in frontend_dirs
        )
        is_backend = any(
            f'/{d}/' in f or f.startswith(f'{d}/') for d in backend_dirs
        )

        if is_frontend_ext and is_frontend_dir and not is_backend:
            content = "\n".join(line for _, line in added.get(f, []))
            for pattern, description in direct_api_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    warnings.append(
                        f"SACROSANCT Warning: Potential {description} in {f}. "
                        "Verify backend proxy pattern is used."
                    )
                    break  # One warning per file

    return warnings


def check_env_file_in_gitignore(commit: Optional[Commit] = None):
    """(is_protected, message or None): .env ignored through git's full ignore chain
    (`git check-ignore -q .env`); a git failure is a non-blocking WARNING (see the notes)."""
    commit = commit or Commit()
    env = {**os.environ, **commit.env} if commit.env else None
    result = run_git(commit.git("check-ignore", "-q", ".env"), env=env)
    if result is None:
        # run_git returns None on timeout and on a missing git; both get the same warning.
        return False, (
            "SACROSANCT WARNING: 'git check-ignore' could not be invoked "
            "(timeout or git binary missing); cannot verify .env protection."
        )

    if result.returncode == 0:
        return True, None
    if result.returncode == 1:
        return False, (
            "SACROSANCT VIOLATION: .env is not ignored by git. "
            "Add '.env' to .gitignore (repo), ~/.config/git/ignore (global), "
            "or .git/info/exclude (per-repo private). "
            "If .env already appears in .gitignore, you may have tracked it "
            "previously; run 'git rm --cached .env' to untrack."
        )
    if result.returncode == 128:
        return False, (
            "SACROSANCT WARNING: 'git check-ignore' reports not in a git repo "
            "(exit 128); cannot verify .env protection."
        )
    return False, (
        f"SACROSANCT WARNING: 'git check-ignore' exited {result.returncode}; "
        "cannot verify .env protection."
    )


def _shape_spans(line: str, shapes) -> list:
    """(start, end) of each commit-only shape match on a line that carries the pragma."""
    if ALLOWLIST_PRAGMA not in line:
        return []
    return [m.span() for pattern, _ in shapes for m in re.finditer(pattern, line, re.IGNORECASE)
            if m.end() > m.start()]


def check_hardcoded_secrets(added: dict):
    """Check the added lines of code files for the secret shapes only this check knows."""
    errors = []
    _load_detector()

    for f, added_in_file in added.items():
        if f.endswith(patterns()["secret_scan_extensions"]):
            # A line whose pragma covers it is skipped here too, by the same one-value rule
            # (these shapes count as values); the path and kind are reported, never the value.
            shapes = patterns()["commit_only_patterns"]
            lines = [(n, line) for n, line in added_in_file
                     if not pragma_covers(line, extra_spans=_shape_spans(line, shapes))]
            for pattern, description in shapes:
                numbers = [n for n, line in lines if re.search(pattern, line, re.IGNORECASE)]
                if numbers:
                    errors.append(f"SACROSANCT VIOLATION: Potential {description} in {f}{_at_lines(numbers)}")

    return errors


def main():
    try:
        # Read input from stdin. Unparsable input fails closed only when it mentions a commit
        # (this hook sees every Bash call; one it cannot read has decided nothing else).
        raw = sys.stdin.read()
        try:
            input_data = json.loads(raw or "{}")
        except ValueError as error:
            if re.search(r'\bcommit\b', raw):
                _emit_load_failure_deny("input", error)
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")
        cwd = input_data.get("cwd")

        # Unread denies; read but no commit while naming `git commit` scans widest (note).
        named = re.search(r'\bgit\s+commit\b', command, re.I)
        try:
            commit = read_commit(command, cwd)
        except Unread:
            _deny("PACT git_commit_check: this command names git and could not be read (a quote "
                  "or a substitution left open), so what it commits or merges cannot be scanned "
                  "first. Run the git step as its own plain command.")
        except routes.Unreadable as error:
            _deny(f"PACT git_commit_check: {error}, so what it commits or merges cannot be "
                  "scanned first.")
        except SharedStash as error:
            _deny(_stash_refusal(str(error)))
        if commit is None or (commit.verb == "route" and named):
            if not named:
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)  # Not a commit command, allow it
            commit = Commit(repo=os.path.abspath(os.path.expanduser(cwd)) if cwd else None, args=None,
                            routes=commit.routes if commit else [],
                            stash_issue=commit.stash_issue if commit else None)
            if re.search(r'\bgit\b', command[:named.start()], re.I):
                commit.stage_issue = "the command could not be read, so what it stages first is unknown"

        with replayed_stagings_or_deny(commit):
            _check_and_exit(command, commit)

    except Exception as e:  # noqa: BLE001 - FAIL CLOSED: a check that cannot run blocks the commit
        _emit_load_failure_deny("runtime", e)


@contextmanager
def replayed_stagings_or_deny(commit: Commit):
    """replayed_stagings, with its failures turned into denies."""
    try:
        with replayed_stagings(commit):
            yield
    except StageSeparately as error:
        _deny(f"PACT git_commit_check: this command stages files and commits in one call, and "
              f"{error}, so what the commit takes cannot be scanned first. Stage in one call "
              "(git add ...), then commit in a second call.")
    except NoRepository:
        _deny("PACT git_commit_check: git finds no repository where this commit or merge would "
              "run (none there yet, or one the same command makes), so what it brings cannot be "
              "scanned first. Run the commit or merge as its own command, from inside the "
              "repository or with `git -C <repo>`, once the repository or worktree exists.")
    except Unverifiable as error:
        _deny(f"PACT git_commit_check: {error}, so the commit was not scanned and is blocked. "
              "Retry the commit; if git stays slow, check the repository (a lock, a cold disk).")
    except routes.Unreadable as error:
        _deny(f"PACT git_commit_check: {error}, so what it commits cannot be scanned first. "
              "Name the patch files or commits in the command.")


def _refuse_stash(commit: Commit) -> None:
    """Deny a command whose scan passed but which also runs a refused `git stash` form; a scan
    that denies first keeps its own reason (a stash before a commit is a staging step)."""
    if commit.stash_issue:
        _deny(_stash_refusal(commit.stash_issue))


def _check_and_exit(command: str, commit: Commit) -> NoReturn:
    """Scan what `commit` takes and exit: 2 (blocked) on a security error, 0 otherwise."""
    # The message first: an amend with nothing staged still sends it.
    security_errors = check_message(command, commit.repo)
    warnings = []
    skipped = []

    for call in ([commit] if commit.verb == "commit" else []) + commit.routes:
        require_repository(call)
    staged_files = (committed_files(commit) or []) if commit.verb == "commit" else []
    patches = _patches(commit_patch(commit)) if staged_files else []
    brought = [text for call in commit.routes for text in routes.patches(call, _run)]
    staged_files += [path for path in added_lines(brought) if path not in staged_files]
    patches += brought
    added = {}
    if staged_files:
        added = added_lines(patches)
        added_errors, skipped = check_added_secrets(patches)
        security_errors.extend(added_errors)

    # If nothing is committed, let git handle the error
    if not staged_files and not security_errors:
        _refuse_stash(commit)
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # --- SACROSANCT Security Checks ---

    # Basic security check (env files, logging secrets)
    security_errors.extend(check_security(staged_files, added, repo=commit.repo))

    # SACROSANCT Rule 1: the secret shapes only this check knows
    security_errors.extend(check_hardcoded_secrets(added))

    # SACROSANCT Rule 2: Frontend credential exposure
    security_errors.extend(check_frontend_credentials(staged_files, added))

    # SACROSANCT Rule 2: Direct API call warnings (non-blocking)
    warnings.extend(check_direct_api_calls(staged_files, added))

    # Check .gitignore protection for .env files
    env_protected, env_error = check_env_file_in_gitignore(commit)
    if env_error:
        if "VIOLATION" in env_error:
            security_errors.append(env_error)
        else:
            warnings.append(env_error)

    # --- Output Warnings (non-blocking) ---
    if warnings:
        print("PACT Security Warnings:", file=sys.stderr)
        print("-" * 30, file=sys.stderr)
        for w in warnings:
            print(f"  * {w}", file=sys.stderr)
        print("-" * 30, file=sys.stderr)
        print("Review these warnings before deployment.", file=sys.stderr)
        print("", file=sys.stderr)

    # --- Block on Security Errors ---
    if security_errors:
        print("Error: PACT Security Violation", file=sys.stderr)
        print("=" * 40, file=sys.stderr)
        for err in security_errors:
            print(f"* {err}", file=sys.stderr)
        print("=" * 40, file=sys.stderr)
        print("Please fix security issues before committing.", file=sys.stderr)
        print("See SACROSANCT rules in CLAUDE.md for guidance.", file=sys.stderr)
        sys.exit(2)  # Block the tool execution

    _refuse_stash(commit)
    notice = pragma_notice(skipped)
    if notice:
        print(json.dumps({"systemMessage": notice, "hookSpecificOutput": {
            "hookEventName": "PreToolUse", "additionalContext": notice}}))
    else:
        print(_SUPPRESS_OUTPUT)
    sys.exit(0)  # Allow the commit


if __name__ == "__main__":
    main()
