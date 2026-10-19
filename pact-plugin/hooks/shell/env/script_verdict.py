"""
Location: pact-plugin/hooks/shell/env/script_verdict.py
Summary: judge, bash_verdict and the filters around the walk: listed names, quiet groups,
         procsub consumers, fed texts, path-variable expansion, literal expansion.
Used by: hooks/env_guard.py (the entry hook and façade) and the other shell.env
         modules per the imports below. Carved from env_guard.py at M2; every
         decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
Map: judge, _pattern_words, _find_filters, _listed_names_verdict, _quiet_group, _procsub_consumer, _fed_texts, _fed_text_verdict, _PATH_VARIABLE, _expand_path_variables, _spell_pairs, _script_verdict, _loose_verdict, _ENV_GET_WRITE, bash_verdict.
"""

from __future__ import annotations

import re
import sys

from shell.words import Unreadable
from shell.words import segments
from shell.words import strip_prefix
from shell.words import words as shell_words
from shell.env.git_egress import (Notice, git_egress_pass, git_egress_verdict)
from shell.env.judge import (_body_as_script, _heredoc_verdict, _printed_text, _prints_loaded, _program, _sub_verdict, command_verdict)
from shell.env.names import (GLOB_BUDGET_MESSAGE, _WRITE_REDIRECT, glob_budget_overspent,
                             mentions_class, reset_glob_budget, token_protected)
from shell.env.places import (_Directory, _PIPE, _merge, _names_only_search, _search_skip, resolve_path_substitutions)
from shell.env.programs import (GREP_PROGRAMS, NAME_CONSUMERS, PARSE_MESSAGE, PRINTERS, READERS, RG_PROGRAMS, SILENT_STDIN, SUGGESTION, UNREADABLE_MESSAGE, WALK_LIMIT)
from shell.env.script import (_BODY, _BODY_KEY, _SUB_KEY, _State, _Step, _command_steps, _literal_rounds, _pull_substitutions, _read, _spell_rounds, bind_names, loop_bindings, normalize_env_get)
from shell.env.search import (_TooLarge, _named_message, _search_roots, first_protected_file, recursive_search)
from shell.env import judge as _judge

# The judge hands sub-shell, heredoc and script bodies back up to this layer (judge._upper);
# registering here keeps the package's import direction downward.
_judge._UPPER = sys.modules[__name__]

def judge(command: str, cwd: str | None, state: _State, captured: bool = False) -> str | None:
    """Pull out substitutions and judge each as captured output (the command around it
    decides whether that output is printed), then judge the simple commands in order, each
    with the heredocs fed to it. Each is judged in every directory it may run in (see
    _Directory); a substitution runs where the commands carrying it do. `command` has been
    through _read."""
    command = _pull_substitutions(command, state)
    steps, exact = _command_steps(command)
    where = _Directory(cwd, exact)
    placed = [(step, where.step(step)) for step in steps]
    carriers: dict[str, list] = {}
    for step, places in placed:
        for word in step.words + step.stdin + step.strings:
            for key in _SUB_KEY.findall(word):
                carriers.setdefault(key, []).append(places)
    for key in dict.fromkeys(_SUB_KEY.findall(command)):
        for here in _merge(*carriers.get(key, ())) or (None,):
            reason = _sub_verdict(key, here, True, state)
            if reason:
                return reason
    state.captured = captured
    fed = {key for step in steps for key in step.bodies}
    for index, (step, places) in enumerate(placed):
        for here in places if not _quiet_group(steps, index) else ():
            reason = command_verdict(step.words, step.stdin, here, state, step.strings)
            if reason:
                return reason
        for key in step.bodies:
            reason = _heredoc_verdict(key, index, steps, command, places, state)
            if reason:
                return reason
        for text, consumer in _fed_texts(steps, index, state):
            reason = _fed_text_verdict(text, steps, consumer, places, state)
            if reason:
                return reason
    for key in dict.fromkeys(_BODY_KEY.findall(command)):  # the tokenizer lost its operator
        if key in state.bodies and key not in fed:
            bare, body = state.bodies[key]
            reason = _body_as_script(state.spell(body) if bare else body, (cwd, None), state)
            if reason:
                return reason
    programs = [(strip_prefix(step.words)[:1] or [""])[0].rsplit("/", 1)[-1] for step in steps]
    if state.loop_read and not captured and any(
            _prints_loaded(program, strip_prefix(step.words)[1:], _State.any_variable())
            for program, step in zip(programs, steps)):
        return ("Blocked: this loop would print lines it reads from a .env-class file into the "
                f"transcript; {SUGGESTION}.")
    if not captured and any(set(step.words) & NAME_CONSUMERS and program in READERS
                            | GREP_PROGRAMS | RG_PROGRAMS for program, step in zip(programs, steps)):
        # `ls .env | xargs cat`: the names one command lists, another reads.
        for step, places in placed:
            named = [w for here in places for w in step.words
                     if w not in _pattern_words(step.words) and token_protected(w, here)]
            if named and strip_prefix(step.words)[:1] != ["env-get"]:
                return _named_message(named[0])
        return _listed_names_verdict(placed)
    return None


def _pattern_words(words: list[str]) -> set[str]:
    """The words of a listing that are name patterns, not files: a search's pattern and
    filter globs, find's -name values. What they let through is judged by walking the tree
    (see _listed_names_verdict)."""
    command = strip_prefix(words)
    program, args = (command[0].rsplit("/", 1)[-1], command[1:]) if command else ("", [])
    if program in GREP_PROGRAMS | RG_PROGRAMS:
        return _search_skip(program, args)
    if program == "find":
        return {args[i + 1] for i, word in enumerate(args[:-1])
                if word in ("-name", "-iname", "-path", "-ipath")}
    return set()


def _find_filters(args: list[str]) -> tuple[list[str], list[str], list[str]] | None:
    """(roots, excluded names, included names) of a find that lists files, from its -name
    and -iname tests (`! -name`, `-not -name` exclude); None when it lists directories only."""
    roots = []
    while len(roots) < len(args) and not args[len(roots)].startswith(("-", "(", "!")):
        roots.append(args[len(roots)])
    includes, excludes, negate = [], [], False
    for i in range(len(roots), len(args)):
        word = args[i]
        if word in ("!", "-not"):
            negate = True
            continue
        if word in ("-name", "-iname") and i + 1 < len(args):
            pattern = args[i + 1].casefold() if word == "-iname" else args[i + 1]
            (excludes if negate else includes).append(pattern)
        elif word == "-type" and args[i + 1:i + 2] == ["d"]:
            return None
        negate = False
    return roots or ["."], excludes, includes


def _listed_names_verdict(placed: list) -> str | None:
    """`find . -type f | xargs grep KEY`, `grep -rl KEY . | xargs grep -n KEY`: the names a
    listing hands to xargs are read by the program xargs runs, so the listing is checked as
    the recursive search it amounts to."""
    for index, (step, places) in enumerate(placed):
        words = step.words
        command = strip_prefix(words)
        program = command[0].rsplit("/", 1)[-1] if command else ""
        if not (index and set(words) & {"xargs", "parallel"} and _PIPE.search(step.before)
                and program in READERS | GREP_PROGRAMS | RG_PROGRAMS
                and not (program not in READERS and _names_only_search(command[1:]))):
            continue
        source, source_places = placed[index - 1]
        listing = strip_prefix(source.words)
        lister, args = (listing[0].rsplit("/", 1)[-1], listing[1:]) if listing else ("", [])
        if lister == "find":
            search = _find_filters(args)
            search = search and (*search, True)
        else:
            search = recursive_search(lister, args)
        if not search:
            continue
        roots, excludes, includes, hidden = search
        for here in source_places:
            try:
                known = _search_roots(roots, here)
                found = known and first_protected_file(known, here, excludes, hidden, includes)
            except _TooLarge:
                known = []
                found = f"one of more than {WALK_LIMIT} entries"
            if known is None:
                found = "a name from a path not known here"
            if found:
                return (f"Blocked: {lister} hands the names it lists to {program}, which prints "
                        f"each file, and it would list {found} (.env class); exclude the class "
                        f"from the listing (find: ! -name '.env*' ! -name '*.env' ! -name .envrc "
                        f"! -name .netrc; grep/rg: the --exclude or -g '!' forms), or "
                        f"{SUGGESTION}.")
    return None


def _quiet_group(steps: list[_Step], index: int) -> bool:
    """True when steps[index] only closes a group (`{ ..; } < f`, `( .. ) < f`) whose commands
    all read its stdin without printing it (read, mapfile, wc): the redirect feeds the
    group, and nothing in it prints."""
    if _program(steps[index].words) or not steps[index].stdin:
        return False
    programs = []
    for k in range(index - 1, -1, -1):
        programs.append(_program(steps[k].words))
        if steps[k].words[:1] == ["{"] or "(" in steps[k].before:
            return all(program in SILENT_STDIN for program in programs)
    return False


def _procsub_consumer(steps: list[_Step], index: int) -> int | None:
    """The step whose `<(...)` holds steps[index], the first command inside it: the one that
    reads what the commands inside print."""
    if "<(" not in steps[index].before:
        return None
    level = 0
    for k in range(index - 1, -1, -1):
        for char in reversed(steps[k + 1].before):
            level += {")": 1, "(": -1}.get(char, 0)
        if level < 0:
            return k
    return None


def _fed_texts(steps: list[_Step], index: int, state: _State) -> list[tuple[str, int]]:
    """(text, index of the step that reads it) for what steps[index] hands to another
    program as input without a heredoc: its here-strings (to itself), what echo or printf
    prints into a pipe or a `>(...)`, and what the first command of a `<(...)` prints."""
    step = steps[index]
    # A here-string read into names (read -r a b <<< "$line") is never run.
    fed = [("\n".join(step.strings), index)] \
        if step.strings and _program(step.words) not in SILENT_STDIN - {"done"} else []
    printed = _printed_text(step, state)
    if printed is None or index + 1 >= len(steps) and "<(" not in step.before:
        return fed
    if _program(step.words) in PRINTERS and (_PIPE.search(step.after) or ">(" in step.after):
        fed.append((printed, index + 1))
    consumer = _procsub_consumer(steps, index)
    if consumer is not None:
        fed.append((printed, consumer))
    return fed


def _fed_text_verdict(text: str, steps: list[_Step], consumer: int, places: tuple,
                      state: _State) -> str | None:
    """The verdict on `text` reaching steps[consumer] on its input, read as a heredoc fed
    there is (see _heredoc_verdict): a shell or eval runs it, awk can run or open it."""
    key = _BODY.format(len(state.bodies))
    state.bodies[key] = (False, text)
    return _heredoc_verdict(key, 0, steps[consumer:], "", places, state)


_PATH_VARIABLE = re.compile(r"""(?<![\w$])([A-Za-z_]\w*)=("[^"]*"|'[^']*'|[^\s;&|]*)""")
# A loop sets its variable to each word of its list in turn: `for W in a b`, `select W in a b`
# and zsh's `for W (a b)`.
_LOOP_VARIABLE = re.compile(r"(?<![\w$.-])(?:for|select)[ \t]+([A-Za-z_]\w*)[ \t]*"
                            r"(?:[ \t]in[ \t]([^;&|\n()]*)|\(([^;&|\n()]*)\))")


def _expand_path_variables(command: str, cwd: str | None,
                           flags: bool) -> tuple[str, list[tuple[str, str]]]:
    """(`command` with $F spelled as its value where the command assigns F a protected path
    (F=.env; cat $F), the (name, value) pairs spelled). A value that holds several words
    with a flag among them reaches the program as ONE word under zsh, so the flags (an
    --exclude list) do not apply there, and as separate words under bash, where a file among
    them is read: it is left unspelled without `flags` and spelled with it, and the command
    is judged both ways (see _script_verdict)."""
    pairs = []
    for name, value in _PATH_VARIABLE.findall(command):
        value = value.strip("\"'")
        pieces = value.split()
        flagged = len(pieces) > 1 and any(piece.startswith("-") for piece in pieces)
        if flagged and not flags:
            continue
        if value and (token_protected(value, cwd) or flagged and any(
                token_protected(piece, cwd) for piece in pieces)):
            pairs.append((name, value))
    for name, listed, bracketed in _LOOP_VARIABLE.findall(command):
        listed = listed or bracketed
        for word in shell_words(listed) or listed.split():    # as the shell reads the list
            if token_protected(word, cwd):                    # one protected word decides the loop
                pairs.append((name, word))
                break
    return _spell_pairs(command, pairs), pairs


def _spell_pairs(text: str, pairs: list[tuple[str, str]]) -> str:
    for name, value in pairs:
        text = re.sub(rf"\$(?:\{{{name}\}}|{name}\b)", lambda _, value=value: value, text)
    return text


def _script_verdict(text: str, cwd: str | None, state: _State,
                    captured: bool = False) -> str | None:
    """A deny reason (or a Notice) for one script run in `cwd`: the command as typed, or a
    heredoc body a shell runs. Raises shell_words.Unreadable for text that cannot be read."""
    command = _read(text, state)
    original = command
    command = normalize_env_get(command)
    if shell_words(command) is None and mentions_class(command):
        return PARSE_MESSAGE
    # An escaped parenthesis is a literal argument (find's grouping), not a subshell.
    command = command.replace("\\(", " ").replace("\\)", " ")
    # A write redirect into a protected file is a write, not a read: drop its target.
    command = _WRITE_REDIRECT.sub(
        lambda m: " " if token_protected(m.group(2), cwd) else m.group(0), command)
    command, rounds = _literal_rounds(command)
    outer = state.spell
    try:
        for binding in loop_bindings(command):      # once per loop word
            bound, judged = bind_names(command, binding), None
            for flags in (False, True):
                variant, pairs = _expand_path_variables(bound, cwd, flags)
                if variant == judged:
                    break
                judged = variant
                state.spell = lambda body, pairs=pairs: _spell_pairs(_spell_rounds(body, rounds),
                                                                     pairs)
                reason = judge(variant, cwd, state, captured)
                if reason:
                    return reason
        notice = None
        for binding in loop_bindings(original):
            reason = git_egress_pass(bind_names(original, binding), cwd)
            if reason and not isinstance(reason, Notice):
                return reason
            notice = notice or reason
        return notice
    finally:
        state.spell = outer


def _loose_verdict(text: str, places: tuple, state: _State) -> str | None:
    """A deny reason for text this guard cannot read as a script: every run of words in it
    is taken as a command, quotes deleted, in each of `places`. That shows every command
    the text could hold, and some it does not."""
    for words in segments(text):
        for here in places if words else ():
            try:
                reason = command_verdict(words, [], here, state)
            except Unreadable as odd:
                reason = UNREADABLE_MESSAGE.format(what=odd.what, instead=odd.instead)
            if reason:
                return reason
        command = strip_prefix(words)
        if command and command[0].rsplit("/", 1)[-1] == "git":
            reason = git_egress_verdict(command[1:], None)     # where it runs: not known
            if reason and not isinstance(reason, Notice):
                return reason
    return None


# A file named env-get that the command itself writes, links or copies into place: the
# target of a redirect, or a word after a writer in the same simple command. Two anchored
# searches per simple command, so a long line costs its length (one pattern spanning
# "writer ... target" costs its square, inside one call no timer can interrupt).
_ENV_GET_TARGET = r"""['"]?(?:[^\s;&|'"]*/)?env-get\b"""
_ENV_GET_REDIRECT = re.compile(r">\s*" + _ENV_GET_TARGET)
_ENV_GET_OPERAND = re.compile(r"\s" + _ENV_GET_TARGET)
_WRITER = re.compile(r"\b(?:tee|cp|mv|ln|install|curl|wget)\b")
_COMMAND_END = re.compile(r"[;&|\n]")


def _writes_env_get(command: str) -> bool:
    if "env-get" not in command:
        return False
    if _ENV_GET_REDIRECT.search(command):
        return True
    for chunk in _COMMAND_END.split(command):
        writer = _WRITER.search(chunk)
        if writer and _ENV_GET_OPERAND.search(chunk, writer.end()):
            return True
    return False


def bash_verdict(command: str, cwd: str) -> str | None:
    """Why `command` is blocked, or None. A command with more globs than names.GLOB_BUDGET is
    blocked whatever else it holds: past the budget no glob is judged."""
    verdict = _bash_verdict(command, cwd)
    return GLOB_BUDGET_MESSAGE if glob_budget_overspent() else verdict


def _bash_verdict(command: str, cwd: str) -> str | None:
    state = _State()
    state.env_get_written = _writes_env_get(command)
    reset_glob_budget()
    try:
        command = resolve_path_substitutions(command, cwd)
        return _script_verdict(command, cwd, state)
    except Unreadable as odd:
        if not odd.broken:
            return UNREADABLE_MESSAGE.format(what=odd.what, instead=odd.instead)
        # Something is left open: the shell stops there too, after the commands before it.
        if mentions_class(command):
            return PARSE_MESSAGE
        return _loose_verdict(command, (cwd, None), _State())
    except RecursionError:
        return UNREADABLE_MESSAGE.format(what="commands nested too deep to follow",
                                         instead="run the inner command on its own")
