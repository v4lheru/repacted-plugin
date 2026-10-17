"""
Location: pact-plugin/hooks/shell/env/names.py
Summary: Protected-name recognition: is_protected_name, mentions_class, templates, file and
         path protection, glob and brace reasoning (token_protected).
Used by: hooks/env_guard.py (the entry hook and façade) and the other shell.env
         modules per the imports below. Carved from env_guard.py at M2; every
         decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
Map: CLASS_SAMPLES, GIT_TEMPLATE_SUFFIXES, _MENTION, _WRITE_REDIRECT, is_protected_name, mentions_class, _absolute, is_template_name, file_is_protected, path_is_protected, _candidates, _STAR, _POSIX_CLASSES, _SET_PART, _python_glob, _bracket, _glob_tokens, _token_matches, _glob_consumes, _glob_reaches_class, _BRACE_LIMIT, _brace_expansions, _BRACE_BUDGET, token_protected.
"""

from __future__ import annotations

import fnmatch
import glob as globbing
import os
import re

# Names a pattern must cover to exclude the whole class (hidden and non-hidden forms).
CLASS_SAMPLES = (".env", ".env.local", "app.env", ".envrc", ".netrc")
# An env template is made to be shared (the env-class files that repositories track are
# nearly always .env.example or .env.local.example), so the name alone is not protected,
# for reads or for staging; the push scan still catches a real value typed into one.
GIT_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")
_MENTION = re.compile(r"\.env|\.netrc|(?<![\w.-])(?:env|printenv)(?![\w.-])", re.IGNORECASE)
_WRITE_REDIRECT = re.compile(r"(?:\d|&)?>>?\|?\s*(['\"]?)([^\s;&|<>()'\"`]+)\1")


def is_protected_name(name: str) -> bool:
    name = name.casefold()
    return (name == ".env" or name.startswith(".env.") or name.endswith(".env")
            or name in (".envrc", ".netrc"))


def mentions_class(text: str) -> bool:
    return bool(_MENTION.search(text))


def _absolute(token: str, cwd: str | None) -> str | None:
    """The path a token names from `cwd`. None for a relative token when the directory the
    command runs in is not known (cwd None, see _Directory)."""
    token = os.path.expanduser(token)
    if os.path.isabs(token):
        return token
    return os.path.join(cwd, token) if cwd is not None else None


def is_template_name(name: str) -> bool:
    return name.casefold().endswith(GIT_TEMPLATE_SUFFIXES)


def file_is_protected(name: str, path: str) -> bool:
    """True when the file at `path`, written as `name`, is protected: by its own name or by
    the name it resolves to. A template name (.env.example) is not protected, unless the
    file is a symlink to a protected name or has a second hard link, which may be one."""
    real = os.path.basename(os.path.realpath(path))
    if any(is_protected_name(each) and not is_template_name(each) for each in (name, real)):
        return True
    if not is_protected_name(name):
        return False
    try:
        return os.stat(path).st_nlink > 1
    except OSError:
        return False


def path_is_protected(token: str, cwd: str | None) -> bool:
    """True for a protected name as written, or a path that resolves to one. A directory
    is never protected (a virtualenv named .env), nor is a token ending in a slash. Where
    the directory is not known a relative token is judged by its name alone: what it
    resolves to cannot be looked up."""
    if not token or token.endswith("/"):
        return False
    name = token.rsplit("/", 1)[-1]
    path = _absolute(token, cwd)
    if path is None:
        return is_protected_name(name) and not is_template_name(name)
    if os.path.isdir(path):
        return False
    return file_is_protected(name, path)


def _candidates(word: str) -> set[str]:
    """The paths a shell word can carry: itself, the value of an option=value, the file
    of a curl @file, and each side of a docker-style a:b pair."""
    found = {word}
    if "=" in word:
        found.add(word.split("=", 1)[1])
        found.add(word.rsplit("=", 1)[1])
    for part in list(found):
        if part.startswith("@"):
            found.add(part[1:])
        if ":" in part:
            found.update(part.split(":"))
    return {part for part in found if part}


_STAR = object()
# Bracket forms Python's glob does not have: the pattern is then judged by what it CAN match
# (see _bracket for where one of these opens a class).
_POSIX_CLASSES = ("[:", "[=", "[.")
_SET_PART = re.compile(r"\]|\[[:=.]")                   # what ends a set, or opens a class in it


def _python_glob(pattern: str) -> str:
    """The shell's `[^x]` is `[!x]` to Python's glob and fnmatch. A `[` after the last `]`
    opens nothing; written as `[[]` it costs fnmatch one step, not a scan to the end."""
    last = pattern.rfind("]") + 1
    return pattern[:last].replace("[^", "[!") + pattern[last:].replace("[", "[[]")


def _bracket(pattern: str, i: int) -> tuple:
    """(token, index after it, holds a class) for the `[` at pattern[i]. The token is a set
    as (negated, members), or the `[` itself where no `]` closes it. A set that holds a
    POSIX class, a collating symbol or an equivalence class (`[[:alpha:]]`, `[[.a.]]`,
    `[[=a=]]`) is not worked out: it is None, any one character, which is never less than
    the shell matches. One of those left open (`[[.a]`) is read differently by different
    shells, so the word as far as its last `]` becomes _STAR (to its end where no `]`
    follows). `[.a]` and `x[:1]` hold no class: the marker counts only inside a set
    already open."""
    negated = pattern[i + 1:i + 2] in ("!", "^")
    start = i + 1 + negated
    j, classed = start + (pattern[start:start + 1] == "]"), False
    while True:
        part = _SET_PART.search(pattern, j)
        if not part:
            return (_STAR, len(pattern), True) if classed else ("[", i + 1, False)
        if part.group() == "]":
            return (None if classed else (negated, pattern[start:part.start()])), part.end(), \
                classed
        close = pattern.find(part.group()[1] + "]", part.end())
        if close < 0:
            last = pattern.rfind("]") + 1
            return _STAR, last if last > i else len(pattern), True
        classed, j = True, close + 2


def _glob_tokens(pattern: str) -> tuple[list, bool]:
    """(a glob as a list: _STAR, None (any one character), a character, or a bracket set as
    (negated, members); whether a set in it holds a class, see _bracket)."""
    tokens, i, classed = [], 0, False
    last, opener = pattern.rfind("]"), max(pattern.rfind(mark) for mark in _POSIX_CLASSES)
    while i < len(pattern):
        char = pattern[i]
        if char == "[" and i > last:                        # no `]` follows: _bracket's answer
            if opener > i + (pattern[i + 1:i + 2] in ("!", "^")):
                return tokens + [_STAR], True
            tokens.append(char)
            i += 1
            continue
        if char == "[":
            token, i, held = _bracket(pattern, i)
            tokens.append(token)
            classed = classed or held
            continue
        if char != "*" or tokens[-1:] != [_STAR]:           # `**` matches what `*` does
            tokens.append(_STAR if char == "*" else None if char == "?" else char)
        i += 1
    return tokens, classed


def _token_matches(token, char: str) -> bool:
    if token is None or isinstance(token, str):
        return token is None or token == char
    negated, members = token
    hit, i = False, 0
    while i < len(members):
        if members[i + 1:i + 2] == "-" and i + 2 < len(members):
            hit, i = hit or members[i] <= char <= members[i + 2], i + 3
        else:
            hit, i = hit or members[i] == char, i + 1
    return hit != negated


def _glob_consumes(tokens: list, text: str, whole: bool = False) -> bool:
    """True when the tokens can match a string that starts with `text`, or `text` itself."""
    def closure(states: set[int]) -> set[int]:
        for i in sorted(states):
            while i < len(tokens) and tokens[i] is _STAR:
                i += 1
                states.add(i)
        return states
    states = closure({0})
    for char in text:
        states = closure({i if tokens[i] is _STAR else i + 1 for i in states
                          if i < len(tokens) and (tokens[i] is _STAR
                                                  or _token_matches(tokens[i], char))})
    return len(tokens) in states if whole else bool(states)


def _glob_reaches_class(base: str) -> bool:
    """True when a glob (the last component, lower case) can match a protected name that
    the five samples do not show: a name that begins `.env.` (the dot must be written, a
    wildcard does not match a leading dot) unless the glob ends in a template suffix, or a
    name that ends `.env`. A glob that ends in `*` can end any way at all, so there only one
    whose text stops inside `.env` counts (app.e*)."""
    tokens = _glob_tokens(base)[0]
    tail = len(tokens)
    while tail and isinstance(tokens[tail - 1], str):
        tail -= 1
    if tokens[:1] == ["."] and _glob_consumes(tokens, ".env.") \
            and not "".join(tokens[tail:]).endswith(GIT_TEMPLATE_SUFFIXES):
        return True
    if tokens and tokens[-1] is not _STAR:
        return _glob_consumes(tokens[::-1], "vne.")
    while tail and tokens[tail - 1] is _STAR:
        tail -= 1
    stem = tail
    while stem and isinstance(tokens[stem - 1], str):
        stem -= 1
    return "".join(tokens[stem:tail]).endswith((".e", ".en", ".env"))


_BRACE_LIMIT = 64


def _brace_expansions(word: str) -> list[str]:
    """The words a brace list expands to (.env{,} is .env .env; .{env,x} is .env .x), as
    bash and zsh expand it, up to _BRACE_LIMIT words; [word] when it holds none. A `${` is a
    variable, not a list."""
    out, pending = [], [word]
    while pending and len(out) + len(pending) <= _BRACE_LIMIT:
        text = pending.pop()
        start = next((i for i, char in enumerate(text) if char == "{"
                      and text[i - 1:i] != "$"), -1)
        depth, commas, end = 0, [], -1
        for i in range(start, len(text)) if start >= 0 else ():
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 1 and text[i] == ",":
                commas.append(i)
            if depth == 0:
                end = i
                break
        if end < 0 or not commas:
            out.append(text)
            continue
        cuts = [start, *commas, end]
        pending += [text[:start] + text[a + 1:b] + text[end + 1:] for a, b in zip(cuts, cuts[1:])]
    return out + pending


# Words one token's brace lists may expand to in all, across every round of expansion.
_BRACE_BUDGET = 20000
# Distinct globs one command may have judged against the disk, across all its words. Past it
# no glob is judged at all, by name or by disk (a name that looks harmless can match a link
# to a protected file): the command is denied with GLOB_BUDGET_MESSAGE, so the cost of a
# command stops growing with its word count and the answer stays closed.
GLOB_BUDGET = 64
GLOB_BUDGET_MESSAGE = (f"Blocked: this command has more than {GLOB_BUDGET} glob patterns, more "
                       "than this check will expand against the disk, so it cannot tell whether "
                       "one of them reaches a .env-class file; split it into commands with fewer "
                       "patterns, then rerun.")
_globs_judged: dict = {}                        # (glob, cwd) -> protected, for one command
_globs_overspent = [False]


def reset_glob_budget() -> None:
    """A new command starts with a full budget (bash_verdict calls this)."""
    _globs_judged.clear()
    _globs_overspent[0] = False


def glob_budget_overspent() -> bool:
    """True once the command has shown more globs than GLOB_BUDGET: bash_verdict denies it."""
    return _globs_overspent[0]


def _glob_protected(candidate: str, cwd: str | None) -> bool:
    """One glob, judged once per command; past the budget it counts as protected, unjudged."""
    key = (candidate, cwd)
    if key not in _globs_judged:
        if len(_globs_judged) >= GLOB_BUDGET:
            _globs_overspent[0] = True
            return True
        _globs_judged[key] = _judge_glob(candidate, cwd)
    return _globs_judged[key]


def _judge_glob(candidate: str, cwd: str | None) -> bool:
    base = candidate.rsplit("/", 1)[-1].casefold()
    pattern = _absolute(candidate, cwd)
    # A glob that cannot be expanded here (directory not known, a bracket form Python's glob
    # lacks) counts when it CAN match a protected name; one that can be expanded counts for
    # what it matches.
    blind = pattern is None \
        or any(mark in candidate for mark in _POSIX_CLASSES) and _glob_tokens(candidate)[1]
    tokens, classed = _glob_tokens(base) if blind else ([], False)
    if ("env" in base or "netrc" in base or blind) and any(
            _glob_consumes(tokens, sample, True) if classed
            else fnmatch.fnmatchcase(sample, _python_glob(base))
            for sample in CLASS_SAMPLES):
        return True
    if blind and _glob_reaches_class(base):
        return True
    return pattern is not None and any(path_is_protected(match, cwd)
                                       for match in globbing.glob(_python_glob(pattern)))


def token_protected(word: str, cwd: str | None, budget: list[int] | None = None) -> bool:
    if "{" in word and "}" in word:
        expanded = _brace_expansions(word)
        if expanded != [word]:
            # Lists multiply ({a,b}{c,d}... doubles per list): past the budget the word is
            # not followed and counts as naming a protected file, so the answer stays
            # closed and comes at once.
            budget = [_BRACE_BUDGET] if budget is None else budget
            budget[0] -= len(expanded)
            if budget[0] < 0:
                return True
            return any(token_protected(each, cwd, budget) for each in expanded)
    for candidate in _candidates(word):
        if any(char in candidate for char in "*?["):
            if _glob_protected(candidate, cwd):
                return True
        elif path_is_protected(candidate, cwd):
            return True
    return False
