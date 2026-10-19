"""
Location: pact-plugin/hooks/shared/run_place.py
Summary: Where a Bash call started its test run: run_dir reads the command in order (the shared
         shell reader, comments and heredoc bodies removed) and returns the directory in force at
         its runner segment, real-pathed, or None when the text does not settle it. settled_word
         is the text one shell word stands for when the command's own text settles it.
Used by: hooks/run_ledger.py (the run_dir field of a hook row, and its _literal), and the git
         head and branch of a hook row come from its run_dir. The evidence check places a run by
         tcount's own directory, never by a directory its call only names.
Map (file order): settled_word, run_dir, _simple_commands, _close, _cd_target.
"""
from __future__ import annotations

import os
import re

from shell.words import SHELL_KEYWORDS, Unreadable, read_script, token_spans

WORD_PIECE = re.compile(r"\"([^\"\n]*)\"|'([^'\n]*)'|([^\"']+)")
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
_OPERATORS = ";&|<>()\n"
_CD_WORDS = frozenset({"cd", "pushd", "popd", "chdir"})
_LEAD_WORDS = SHELL_KEYWORDS | {"{", "}", "time", "builtin", "command"}
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
_STACK_STEP = re.compile(r"[+-]\d+")
PLACE_MAX_BYTES = 400          # run_ledger.CALL_DIR_MAX_BYTES: one directory on a ledger line


def settled_word(word: str | None, values: dict[str, str]) -> str | None:
    """The text a shell word stands for when the command's own text settles it, else None:
    quotes removed, `$NAME` filled from `values`. A substitution, a glob, `~`, an escape or a
    name this command never set leaves it unsettled."""
    parts = []
    for double, single, bare in WORD_PIECE.findall(word or ""):
        if single:
            parts.append(single)
            continue
        text = _VARIABLE.sub(lambda match: values.get(match.group(1) or match.group(2), "$"), double or bare)
        unquoted = _VARIABLE.sub("", bare)
        if any(char in text for char in "$`\\") or any(char in unquoted for char in "*?[~{"):
            return None
        parts.append(text)
    return "".join(parts) or None


def run_dir(command: str, cwd: str | None) -> str | None:
    """The real path of the directory in force where the command starts its test run: the
    payload's cwd, changed by each `cd`/`pushd` before the runner segment in the order the shell
    runs them. A `cd` inside `( )` or `$( )` ends with its group, one in the background (`&`)
    changes nothing, and comments, heredoc bodies and quoted text are not commands. None, so the
    row has no place of its own, when the text does not settle it: a target that is a
    substitution or a name the command never set, `cd -`, `popd`, a bare `cd`, a `cd` in a
    pipeline, quoting the shared reader cannot read, no runner segment, or runner segments in
    two directories. Known limit: a runner told by its own options or an inner shell to work
    elsewhere (`make -C`, `bash -c "cd .."`) is placed where it was started."""
    from shared.runcounts import detect_runner

    try:
        steps = _simple_commands(read_script(command)[0])
    except Unreadable:
        return None
    if steps is None:
        return None
    base = os.path.normpath(cwd) if isinstance(cwd, str) and os.path.isabs(cwd) else None
    values: dict[str, str] = {}
    saved: list[tuple[str | None, dict[str, str]]] = []
    places: set[str | None] = set()
    for kind, words, raws in steps:
        if kind == "(":
            saved.append((base, dict(values)))
        elif kind == ")":
            if saved:
                base, values = saved.pop()
        else:
            base = _close(kind, words, raws, base, values, places, detect_runner)
    if len(places) != 1:
        return None
    place = places.pop()
    if place is None:
        return None
    real = os.path.realpath(place)
    return real if len(real.encode("utf-8", "surrogatepass")) <= PLACE_MAX_BYTES else None


def _simple_commands(script: str) -> list[tuple[str, list[str], list[str]]] | None:
    """The script as steps in order: ("(", [], []) and (")", [], []) for each unquoted bracket,
    else (how, words, raw words) per simple command, `how` being "|" for a part of a pipeline,
    "&" for one sent to the background, else ";". Redirections and their targets are dropped
    (`2>/dev/null cd x` is a cd). None when the shared reader cannot read the quoting."""
    found = token_spans(script, _OPERATORS)
    if found is None:
        return None
    steps: list[tuple[str, list[str], list[str]]] = []
    words: list[str] = []
    raws: list[str] = []
    piped = redirect = False
    last_end = -1

    def end(kind: str) -> None:
        nonlocal words, raws, piped
        if words:
            steps.append(("|" if piped or kind == "|" else kind, words, raws))
        words, raws, piped = [], [], kind == "|"

    for start, stop, token in found:
        if script[start] not in _OPERATORS:
            if redirect:
                redirect = False
            else:
                words.append(token)
                raws.append(script[start:stop])
            last_end = stop
            continue
        i = 0
        while i < len(token):
            pair, char = token[i:i + 2], token[i]
            if pair in ("&&", "||"):
                end(";")
                i += 2
                continue
            if pair == "|&":
                end("|")
                i += 2
                continue
            if pair in ("&>", ">&") or char in "<>":
                if i == 0 and words and last_end == start and raws[-1].isdigit():
                    words.pop()
                    raws.pop()                  # the descriptor of a redirection: `2>&1`
                redirect = True
                i += 2 if pair in ("&>", ">&") else 1
                continue
            redirect = False
            if char in ";\n":
                end(";")
            elif char in "|&":
                end(char)
            else:
                end(";")
                steps.append((char, [], []))
            i += 1
        last_end = stop
    end(";")
    return steps


def _close(how: str, words: list[str], raws: list[str], base: str | None, values: dict[str, str],
           places: set, detect_runner) -> str | None:
    """Apply one simple command to the walk; returns the directory in force after it. An
    assignment-only command sets a name; a `cd` moves the base (or makes it unknown); a test
    run records the base as one of the command's places."""
    while words and words[0] in _LEAD_WORDS:
        words, raws = words[1:], raws[1:]
    if words and words[0] == "export":
        words, raws = words[1:], raws[1:]
    lead = 0
    while lead < len(words) and _ASSIGNMENT.match(words[lead]):
        lead += 1
    if words and lead == len(words):
        for raw in raws:
            name, _, value = raw.partition("=")
            settled = settled_word(value, values) if value else ""
            values.pop(name, None)
            if settled is not None:
                values[name] = settled
        return base
    if not words:
        return base
    head = words[lead]
    if head in _CD_WORDS:
        if how == "&":
            return base                         # a background job's cd changes nothing here
        return None if how == "|" else _cd_target(head, raws[lead + 1:], base, values)
    if head != "." and base is not None and os.path.isdir(os.path.join(base, head)):
        return None                             # zsh AUTO_CD: a directory named as a command
    if detect_runner(" ".join(words)):
        places.add(base)
    return base


def _cd_target(head: str, args: list[str], base: str | None, values: dict[str, str]) -> str | None:
    """The directory a `cd`/`pushd` moves to, or None when the text does not settle it."""
    if head == "popd":
        return None
    while args and args[0].startswith("-") and args[0] not in ("-", "--") \
            and not (head == "pushd" and _STACK_STEP.fullmatch(args[0])):
        args = args[1:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        return None                             # a bare cd goes home, which is not read here
    target = settled_word(args[0], values)
    if target is None or target == "-" or (head == "pushd" and _STACK_STEP.fullmatch(target)):
        return None
    if not os.path.isabs(target):
        if base is None or (os.environ.get("CDPATH") and not target.startswith(("./", "../"))
                            and target not in (".", "..")):
            return None
        target = os.path.join(base, target)
    return os.path.normpath(target)
