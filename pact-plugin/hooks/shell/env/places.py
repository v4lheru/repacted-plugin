"""
Location: pact-plugin/hooks/shell/env/places.py
Summary: Where a command runs: cd targets, movers, the per-place directory model and the
         names-only search of a place.
Used by: hooks/env_guard.py (the entry hook and façade) and the other shell.env
         modules per the imports below. Carved from env_guard.py at M2; every
         decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
Map: _placed_at, resolve_path_substitutions, _RESOLVED_NAME, _PLAIN_PATH, _MOVERS, _MOVER_WORD, _CDPATH_SET, _COMPOUND_OPEN, _COMPOUND_CLOSE, _PIPE, _BACKGROUND, _cd_target, MAX_PLACES, _LITERAL_ASSIGNMENT, _PLACE_NAMES, _FREEZERS, _CHDIR_OPTION, _fed_or_failing, _merge, _Directory, _names_only_search, _search_skip.
"""

from __future__ import annotations

import os
import re
import shutil

from shell.words import is_double_quoted, quoted_inner, strip_prefix, substitution_spans
from shell.env.names import (_absolute)
from shell.env.programs import (GREP_PROGRAMS, LOADERS, _FILTER_OPTIONS, _GREP_VALUE_OPTIONS, _RG_VALUE_OPTIONS)
from shell.env.script import (_SUB_MARK, _Step, _command_steps)
from shell.env.search import (_pattern_by_option, _positionals)

# ── where a command runs ──────────────────────────────────────────────────────────────
_MOVERS = frozenset({"cd", "pushd", "popd", "chdir"})
_MOVER_WORD = re.compile(r"(?<![\w.-])(?:cd|pushd|popd|chdir)(?![\w.-])")
# Assignments after which a followed move no longer says where a name leads: CDPATH makes
# cd look a name up elsewhere, PWD and OLDPWD stop naming the directory.
_CDPATH_SET = re.compile(r"(?:(?i:cdpath)|(?:OLD)?PWD)\+?=")
_COMPOUND_OPEN = frozenset({"for", "while", "until", "select", "if", "case"})
_COMPOUND_CLOSE = frozenset({"done", "fi", "esac"})
_PIPE = re.compile(r"(?<!\|)\|(?!\|)")
_BACKGROUND = re.compile(r"(?<![&|<>])&(?![&>])")


def _cd_target(args: list[str], current: str | None) -> str | None:
    """The directory `cd`/`pushd` moves to, or None when it cannot be known here: an
    option, more than one operand, `-`, a `$`, ~user, a CDPATH lookup, a missing directory."""
    if any(arg.startswith("-") and arg != "-" for arg in args) or len(args) > 1:
        return None
    if not args:
        return os.path.expanduser("~")
    target = args[0]
    if target == "-" or "$" in target or (
            target.startswith("~") and not target.startswith(("~/",)) and target != "~"):
        return None
    target = os.path.expanduser(target)
    if not os.path.isabs(target):
        if current is None or os.environ.get("CDPATH") and not target.startswith(("./", "../")) \
                and target not in (".", ".."):
            return None
        target = os.path.join(current, target)
    path = os.path.normpath(target)
    # `cd ..` out of a symlinked directory lands in one place in a logical shell and another
    # in a physical one (set -P, chase_links): followed only where the two agree.
    if os.path.realpath(path) != os.path.realpath(target):
        return None
    return path if os.path.isdir(path) else None


MAX_PLACES = 6
# An assignment that cannot fail: a literal value, nothing computed (see _Directory.sure).
_LITERAL_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*\+?=[^$`]*")
# Words that name where the shell is or where cd looks: set by read, for or export as well
# as by an assignment.
_PLACE_NAMES = frozenset({"CDPATH", "cdpath", "PWD", "OLDPWD"})
_FREEZERS = frozenset({"readonly", "declare", "typeset", "local"})
_CHDIR_OPTION = re.compile(r"-[A-Za-z]*C|--chdir(?:=.*)?")       # env -C DIR runs elsewhere


def _fed_or_failing(step: _Step) -> bool:
    """True when the command is fed its input (a file, a heredoc, a here-string) or has a
    redirect that can fail, so it may read commands unseen or not run at all. A copy of a
    file descriptor and /dev/null are neither."""
    return bool(step.stdin or step.bodies or step.strings) or any(
        not (target.isdigit() or target in ("-", "/dev/null")) for target in step.redirects)


def _merge(*groups) -> tuple:
    """The places in the groups, each once, the unknown one (None) first, at most MAX_PLACES
    (the latest are kept)."""
    seen: list = []
    for group in groups:
        for place in group or ():
            if place not in seen:
                seen.append(place)
    known = [place for place in seen if place is not None]
    return ((None,) if None in seen or len(known) >= MAX_PLACES else ()) \
        + tuple(known[1 - MAX_PLACES:])


class _Directory:
    """The directories each simple command of one Bash command may run in: one place when
    that is known, else None (not known) followed by the places the shell may still be in.
    A command is judged in every one of them. Only a positively recognised move is followed:
    a bare `cd DIR` or `pushd DIR` (see _cd_target) that is the whole simple command, outside
    any loop or conditional, not in a pipeline, not in the background, not after `||`, with
    no redirect that can fail. A parenthesis opens a scope that its closing one restores.
    Every other way to move leaves the directory unknown: popd, a prefixed or braced cd, a
    cd among the words of another command (noglob cd, repeat 1 cd, coproc cd), an eval or
    trap that names cd or holds anything computed, a program word that is computed (`$C
    dir`), a `.` fed from a redirect, a lone word that names a directory (autocd), `cd
    dir(:h)`. So does a `cd` that ran only if an earlier command of its `&&` chain
    succeeded, once that chain ends; a literal assignment and a followed cd are the only
    commands taken to succeed. After a function or alias definition, CDPATH or PWD set in
    any way, or such a trap, no move is followed at all. A sourced or executed script is not
    read, here or anywhere in this guard: what it does is its own."""

    def __init__(self, cwd: str | None, exact: bool = True):
        self.places: tuple = (cwd,)
        self.exact = exact
        self.scopes: list[tuple] = []
        self.depth = 0                                  # open for/while/if/case constructs
        self.held: tuple | None = None                  # places before a cd its chain may skip
        self.sure = True                                # this && chain so far cannot fail
        self.frozen = False                             # a readonly: an assignment can fail

    def _lose(self) -> None:
        self.places = _merge((None,), self.held, self.places)
        self.held, self.sure = None, False

    def _is_directory(self, word: str) -> bool:
        paths = [_absolute(word, place) for place in self.places if place is not None]
        return any(path is not None and os.path.isdir(path) for path in paths)

    def _move(self, step: _Step, first: str, chained: bool) -> None:
        plain = (self.exact and first in ("cd", "pushd") and self.depth == 0
                 and not _fed_or_failing(step)
                 and "||" not in step.before and not _PIPE.search(step.before)
                 and not _PIPE.search(step.after) and not _BACKGROUND.search(step.after)
                 and not step.after.startswith("(")      # zsh: cd dir(:h)
                 and not (first == "pushd" and len(step.words) == 1))
        current = self.places[0] if len(self.places) == 1 else None
        target = _cd_target(step.words[1:], current) if plain else None
        if target is None:
            self._lose()
        elif chained and not self.sure:
            self.held, self.places = _merge(self.held, self.places), (target,)
        else:
            self.places = (target,)

    def step(self, step: _Step) -> tuple:
        """The places `step` may run in; then apply the move it makes, if any."""
        words, before, after = step.words, step.before, step.after
        opened = closed = False
        for char in before:
            if char == "(":
                self.scopes.append((self.places, self.held, self.sure))
                self.held, self.sure, opened = None, True, True
            elif char == ")":
                self.places, self.held, self.sure = \
                    self.scopes.pop() if self.scopes else ((None,), None, False)
                closed, opened = True, False
        chained = "&&" in before and not _PIPE.search(before) and not opened
        if not chained:                                 # a new list: the && chain is over
            if self.held is not None:
                self.places, self.held = _merge((None,), self.held, self.places), None
            self.sure = True
        elif closed:                                    # (..) && cd: the brackets may fail
            self.sure = False
        here = self.places
        first = words[0] if words else ""
        if first in _COMPOUND_OPEN:
            self.depth += 1
        elif first in _COMPOUND_CLOSE:
            self.depth = max(self.depth - 1, 0)
        bare = words[1:] if first == "{" else words
        command = strip_prefix(bare)
        leading = bare[:len(bare) - len(command)]
        program = command[0].rsplit("/", 1)[-1] if command else ""
        text = " ".join(bare)
        computed = "$" in text or "`" in text or _SUB_MARK in text
        redirected = bool(step.stdin or step.bodies or step.strings or step.redirects)
        if "()" in after or first in ("function", "alias") \
                or any(_CDPATH_SET.match(word) or word in _PLACE_NAMES for word in words) \
                or program == "trap" and (computed or _MOVER_WORD.search(text)):
            self._lose()                                # cd may be redefined, or run unseen
            self.exact = False
        elif "eval" in leading:
            if computed or _MOVER_WORD.search(text):    # eval "cd DIR": a move this cannot read
                self._lose()
            self.sure = False
        elif program in _MOVERS:
            self._move(step, first, chained)
        elif any(word in _MOVERS for word in bare) \
                or command and "/" not in command[0] and (
                    "$" in command[0] or "`" in command[0] or _SUB_MARK in command[0]) \
                or program in LOADERS and (_fed_or_failing(step) or any(
                    word.startswith(("/dev/", "/proc/")) for word in command[1:])) \
                or len(bare) == 1 and not redirected and self._is_directory(bare[0]):
            self._lose()
        elif not (words and not redirected and not self.frozen and all(
                _LITERAL_ASSIGNMENT.fullmatch(word) and _SUB_MARK not in word for word in words)):
            self.sure = False
        if program in _FREEZERS:
            self.frozen = True
        if any(_CHDIR_OPTION.fullmatch(word) for word in leading):
            return _merge((None,), here)
        return here


def _names_only_search(args: list[str]) -> bool:
    """grep/rg run to list names, count or test (-l, -L, -c, -q, rg --files): no line of
    a file reaches the output."""
    long = {"--files-with-matches", "--files-without-match", "--count", "--quiet", "--silent",
            "--files", "--count-matches"}
    for word in args:
        if word in long:
            return True
        if word.startswith("-") and not word.startswith("--"):
            for letter in word[1:]:
                if letter in "lLcq":
                    return True
                if letter in "efmABCdDgtTM":
                    break
    return False


def _search_skip(program: str, args: list[str]) -> set[str]:
    """The words of a search that are not paths: its pattern and its filter globs."""
    skip = set()
    if not _pattern_by_option(args, "efmABCdD" if program in GREP_PROGRAMS else "efgtTmABCMjdrE"):
        pattern = _positionals(args, _GREP_VALUE_OPTIONS if program in GREP_PROGRAMS
                               else _RG_VALUE_OPTIONS)
        skip = {pattern[0]} if pattern else set()
    for i, word in enumerate(args):
        if word.split("=", 1)[0] in _FILTER_OPTIONS:
            skip.add(word)
            if "=" not in word and i + 1 < len(args):
                skip.add(args[i + 1])
    return skip


# ── path-valued substitutions (refactor §3.7 part 1) ───────────────────────────────────
# A closed list of substitutions that name one path on this machine: spelled as that path
# before the command is judged, so a search root or a git directory written as `$(pwd)` is
# a known place and not an unknown one. Anything else stays a substitution (unknown).
_RESOLVED_NAME = re.compile(r"[A-Za-z0-9._-]+")
_PLAIN_PATH = re.compile(r"[A-Za-z0-9_./+,:@%=-]+")      # a path the shell reads as one word
_PATH_SET = re.compile(r"(?<![\w.-])PATH=")


def _resolved_path(body: str, cwd: str | None) -> str | None:
    """The path `$(body)` prints, for the three forms: `pwd` (the frame's directory),
    `git rev-parse --show-toplevel` (the nearest ancestor holding .git) and `command -v
    NAME` / `which NAME` (a bare name looked up on PATH)."""
    words = body.split()
    if words in (["pwd"], ["pwd", "-L"], ["pwd", "-P"]):
        if cwd is None or not os.path.isabs(cwd):
            return None
        return os.path.realpath(cwd) if words[-1] == "-P" else cwd
    if words == ["git", "rev-parse", "--show-toplevel"]:
        if cwd is None or not os.path.isabs(cwd):
            return None
        here = os.path.realpath(cwd)
        while True:
            if os.path.lexists(os.path.join(here, ".git")):
                return here
            parent = os.path.dirname(here)
            if parent == here:
                return None
            here = parent
    if (len(words) == 3 and words[:2] == ["command", "-v"]) or (len(words) == 2 and words[0] == "which"):
        name = words[-1]
        if "/" in name or not _RESOLVED_NAME.fullmatch(name):
            return None
        return shutil.which(name)
    return None


# The marker carries a nonce drawn per call, so a word the command spells (even through
# quotes, `__repact_"here"_0__`) can never be read as the guard's own.
_HERE_WORD = "__repact_here_{}_{}__"
_HERE_INDEX = re.compile(r"__repact_here_")


def _placed_at(command: str, spans: list[tuple[int, int]], cwd: str | None) -> set[int]:
    """The indexes of `spans` (in order, not overlapping) whose simple command _Directory
    still places at `cwd` alone: an earlier lone directory word (zsh autocd), an eval of a
    computed value or a function or alias definition loses the place, as a move does. A span
    the steps do not carry as a word (a redirect target) is not placed. One pass over the
    command with each span replaced by a marker word. A command that already holds a marker
    word places nothing: the word would be counted as the guard's own."""
    if _HERE_INDEX.search(command):
        return set()
    nonce = os.urandom(6).hex()
    marked, last = [], 0
    for index, (start, end) in enumerate(spans):
        marked += [command[last:start], _HERE_WORD.format(nonce, index)]
        last = end
    try:
        steps, exact = _command_steps("".join(marked) + command[last:])
    except ValueError:                       # Unreadable: not known
        return set()
    where, placed = _Directory(cwd, exact), set()
    for step in steps:
        here = where.step(step)
        if here == (cwd,):
            words = " ".join(step.words + step.stdin + step.strings)
            placed |= {int(index) for index in re.findall(_HERE_WORD.format(nonce, r"(\d+)"), words)}
    return placed


def resolve_path_substitutions(command: str, cwd: str | None) -> str:
    """`command` with each `$(pwd)`, `$(git rev-parse --show-toplevel)`, `$(command -v
    NAME)` and `$(which NAME)` spelled as the one path it names here (_resolved_path), so
    the directory and search-root rules judge a real place. Left as written: a span after a
    `cd`/`pushd`/`popd` word or, but for a `command -v`/`which` lookup, where _Directory no
    longer knows the one place (the directory may have moved), a lookup after `PATH=`, a
    path the shell would split or glob unless the span is exactly `"$(...)"`, and a path
    holding a quote, `$`, backtick or backslash."""
    try:
        spans = substitution_spans(command)
    except ValueError:                       # Unreadable: the judge reports it
        return command
    found = []
    for start, end, body in spans:
        before = command[:start]
        lookup = body.split()[:1] in (["command"], ["which"])
        if _MOVER_WORD.search(before) or (lookup and _PATH_SET.search(before)):
            continue
        path = _resolved_path(body, cwd)
        if path and not any(char in path for char in "\"'\\$`\n"):
            found.append((start, end, path, lookup))
    placed = _placed_at(command, [(start, end) for start, end, _, _ in found], cwd)
    out = command
    for index in reversed(range(len(found))):
        start, end, path, lookup = found[index]
        if not lookup and index not in placed:
            continue
        wrapped = command[max(start - 1, 0):end + 1]
        quoted = start > 0 and is_double_quoted(wrapped) and quoted_inner(wrapped) == command[start:end]
        if quoted:
            out = out[:start - 1] + wrapped[0] + path + wrapped[-1] + out[end + 1:]
        elif _PLAIN_PATH.fullmatch(path):
            out = out[:start] + path + out[end:]
    return out
