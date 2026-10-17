"""
Location: pact-plugin/hooks/shell/merge/binding.py
Summary: What one approval covers: the count of guarded operations a command holds (an
         approval covers exactly one) and the wrapper a wrapped operation is bound to.
Used by: hooks/shared/merge_guard_common.py (the façade); merge_guard_pre refuses a command
         that holds a second guarded operation and compares the wrapper at the token match;
         merge_guard_post refuses to mint for the first and writes the second into the token.
         Posture: honest-mistake prevention, not an adversarial sandbox. Threat model and
         design rules: the design notes.
Map: _MAX_DEPTH, _BLANK, _blank_body, _top_level_text, _leg_operation_count, guarded_operation_count, holds_second_operation, wrapper_binding, _guarded_leg, _without_redirects, _without_comment, _typed_words, typed_binding, _rewrites_remote, _holds_loop, unbindable_reason, approval_binding.
"""

from __future__ import annotations

import os
import re

from shell.merge import remotes as _remotes
from shell.merge.checkout import (_GIT_VALUE_GLOBALS, _PUSH_LONG_VALUE_FLAGS, _UNREADABLE, _git_call, _paired_legs, _ungroup)
from shell.merge.classify import (_OWN_ARM_PROGRAMS, _leg_payloads, _leg_words, _substitution_readings, _with_heredocs, detect_command_operation_type, is_dangerous_command)
from shell.merge.strip import (_split_into_legs)
from shell.merge.views import (_leg_program_tokens, _shell_tokenize)
from shell.words import (Unreadable, read_script, substitution_spans)
from shell.words import normalize_line_continuations as _normalize_line_continuations

# Payloads and substitutions are counted this deep; a deeper one counts once when dangerous.
_MAX_DEPTH = 3
_BLANK = "$_"


def _blank_body(bare: bool, body: str) -> str:
    """A heredoc body with each substitution the shell would run in it spelled `$_` (an
    unquoted delimiter only: a quoted one makes the body plain text)."""
    if not bare:
        return body
    try:
        spans = substitution_spans(body, quoting=False)
    except (Unreadable, IndexError, RecursionError):
        return body
    out, last = [], 0
    for start, end, _found in spans:
        out += [body[last:start], _BLANK]
        last = end
    return "".join(out) + body[last:]


def _top_level_text(command: str) -> "tuple[str, list[str]]":
    """(the command with every substitution spelled `$_`, heredoc bodies kept; the
    substitutions' commands). Each guarded operation then stands in exactly one of the two."""
    plain, heredocs, inner = _substitution_readings(command)
    return _with_heredocs(plain, [(bare, _blank_body(bare, body)) for bare, body in heredocs]), inner


def _leg_operation_count(leg: str, depth: int) -> int:
    """The guarded operations one leg holds: its own (one at most), or those of the scripts
    it runs (`sh -c S`, `eval S`, a here-string, an ssh string) when they hold more."""
    own = 1 if is_dangerous_command(leg) else 0
    tokens = _leg_words(leg)
    if not tokens or depth >= _MAX_DEPTH:
        return own
    return max(own, sum(guarded_operation_count(inner, depth + 1)
                        for inner, _wrapper in _leg_payloads(tokens)))


def guarded_operation_count(command: str, _depth: int = 0) -> int:
    """How many operations the guard gates `command` holds, wherever they stand: a leg, a
    script a leg runs, a `$(...)` or backtick substitution in an argument, a redirect target,
    a here-string or an unquoted heredoc body. A harmless substitution or redirect adds none.
    A dangerous command no single leg accounts for counts once."""
    text, inner = _top_level_text(command)
    total = sum(_leg_operation_count(leg, _depth) for leg in _split_into_legs(text))
    if total == 0 and is_dangerous_command(text):
        total = 1
    if _depth >= _MAX_DEPTH:
        return total + sum(1 for found in inner if is_dangerous_command(found))
    return total + sum(guarded_operation_count(found, _depth + 1) for found in inner)


def holds_second_operation(command: str) -> bool:
    """True when `command` holds more than the one guarded operation an approval can cover."""
    return guarded_operation_count(command) >= 2


def wrapper_binding(command: str) -> "str | None":
    """What an approval binds besides operation, target, flags and repository: None when a
    top-level leg runs the guarded operation as its own program (git, gh, glab, curl, wget:
    the repository binding reads its directory words), else the whole command as typed. A
    guarded operation inside a shell -c or eval string, a here-string, an ssh string, a
    substitution, a subshell or a compound statement is approved as that exact text, so
    another wrapper, host or directory inside it is another command."""
    command = _normalize_line_continuations(command).strip()
    text, inner = _top_level_text(command)
    if any(guarded_operation_count(found, 1) for found in inner):
        return command
    for leg in _split_into_legs(text):
        tokens = _leg_program_tokens(leg)
        own = bool(tokens) and os.path.basename(tokens[0]) in _OWN_ARM_PROGRAMS
        words = _leg_words(leg)
        if words and any(guarded_operation_count(script, 1) for script, _wrapper in _leg_payloads(words)):
            return command
        guarded = is_dangerous_command(leg) or detect_command_operation_type(leg) is not None
        if guarded and not own:
            return command
        # A leg of a subshell or a group (`(cd x && git branch -D y)`) is no top-level leg:
        # the directory words around the operation are part of the text approved.
        if _ungroup(leg) != leg.strip() and is_dangerous_command(text):
            return command
    return None


# --- The guarded leg as typed: every word of it is part of what was approved ---
# Flags that change nothing about what is pushed or deleted; an approval covers the command
# with or without them. Closed lists: every other option is bound as typed.
_HARMLESS_FLAGS = {
    "push": frozenset({"-v", "--verbose", "-q", "--quiet", "--progress", "--no-progress", "-u",
                       "--set-upstream"}),
    "branch": frozenset({"-q", "--quiet"}),
}
_PUSH_VALUE_FLAGS = _PUSH_LONG_VALUE_FLAGS | {"-o"}
_GIT_ENV_RE = re.compile(r"^GIT_\w*\+?=")
_REDIRECT_RE = re.compile(r"^(?:\d*|&)(?:>>|>\||>&|<&|<<<|<>|>|<)(.*)$", re.DOTALL)
_EXPANSION_RE = re.compile(r"[$`]")
_LEGS_OR_EXPANSION_RE = re.compile(r"[$`;&|\n]")
_LOOP_WORDS = frozenset({"for", "while", "until", "select"})
_DECLARING_HEADS = frozenset({"export", "declare", "typeset", "readonly", "local"})
_REMOTE_REWRITES = frozenset({"add", "set-url", "rename", "remove", "rm"})
_CONFIG_REWRITES = ("remote.", "url.", "credential.", "core.sshcommand", "branch.", "push.")
_SEP = "\x1f"
_WHOLE_TEXT = "\x00text" + _SEP


def _guarded_leg(command: str) -> "tuple[int, list[tuple[str, str]]] | None":
    """(index of the one guarded leg, the (stripped, raw) leg pairs), or None when the legs
    hold no guarded operation or more than one. The leg is chosen as the token match chooses
    the leg it reads operation, target and flags from: the one dangerous leg, else the one
    leg with an operation type (an inert `gh pr close 5 --help` beside a merge is not it)."""
    pairs = _paired_legs(command)
    for guarded in (is_dangerous_command, detect_command_operation_type):
        found = [k for k, (stripped, _raw) in enumerate(pairs) if guarded(stripped.strip())]
        if len(found) == 1:
            return found[0], pairs
    return None


def _without_redirects(words: "list[str]") -> "list[str]":
    """`words` without redirect operators and their targets (`2>&1`, `> out`, `>out`)."""
    kept, skip = [], False
    for word in words:
        if skip:
            skip = False
            continue
        found = _REDIRECT_RE.match(word)
        if found:
            skip = not found.group(1)
            continue
        kept.append(word)
    return kept


def _without_comment(leg: str) -> str:
    """`leg` without its trailing comment, as read_script reads one (a quoted `#` and the
    `#` inside a word stay); the leg as typed when it cannot be read."""
    if "#" not in leg:
        return leg
    try:
        return read_script(leg)[0]
    except Unreadable:
        return leg


def _typed_words(raw_leg: str) -> "list[str] | None":
    """The words an approval of this git leg binds: its GIT_* assignment prefixes, then every
    word from the program on (git globals, subcommand, options, remote and refspecs as typed;
    runner words, redirects, a trailing comment and the harmless flags dropped). [] for a leg
    of another program (gh, glab, curl: bound by operation, target, privileged flags,
    repository and host, where an alias or a placeholder path names the same target). None
    when it does not tokenize."""
    leg = _without_comment(_ungroup(raw_leg))
    every, words = _shell_tokenize(leg), _leg_program_tokens(leg)
    if not every or not words:
        return None
    env = [word for word in every[:len(every) - len(words)] if _GIT_ENV_RE.match(word)]
    words = _without_redirects(words)
    if not words or os.path.basename(words[0]) != "git":
        return []
    at = 1
    while at < len(words) and words[at].startswith("-"):
        at += 2 if words[at] in _GIT_VALUE_GLOBALS else 1
    harmless = _HARMLESS_FLAGS.get(words[at] if at < len(words) else "", frozenset())
    kept, value_next = words[:at + 1], False
    for word in words[at + 1:]:
        if word in harmless and not value_next:
            continue
        kept.append(word)
        value_next = word in _PUSH_VALUE_FLAGS
    return env + kept


def typed_binding(command: str) -> "str | None":
    """The guarded git leg of an unwrapped command as typed (_typed_words), which the read
    side compares word for word: source and destination of every refspec, the remote, every
    option and git global. The whole command when its one guarded leg cannot be told apart;
    None for a wrapped operation (wrapper_binding already binds its text) and for a guarded
    leg of another program."""
    command = _normalize_line_continuations(command).strip()
    if wrapper_binding(command) is not None:
        return None
    found = _guarded_leg(command)
    words = _typed_words(found[1][found[0]][1]) if found else None
    if words is None:
        return _WHOLE_TEXT + command
    return _SEP.join(words) if words else None


def _rewrites_remote(stripped: str, raw: str) -> bool:
    """True when a leg changes where a later git leg's remote points: `git remote
    add|set-url|rename|remove`, `git config` on remote.*, url.*, credential.*, branch.*,
    push.* or core.sshCommand, or an assignment or export of a GIT_* variable."""
    tokens = _shell_tokenize(_ungroup(raw)) or _shell_tokenize(_ungroup(stripped)) or []
    if any(_GIT_ENV_RE.match(tok) for tok in tokens):
        return True
    if tokens and tokens[0] in _DECLARING_HEADS and any(tok.startswith("GIT_") for tok in tokens[1:]):
        return True
    call = _git_call(raw) or _git_call(stripped)
    if call is None:
        return False
    sub, args = call[0], [arg for arg in call[1] if not arg.startswith("-")]
    if sub == "remote":
        return bool(args) and args[0] in _REMOTE_REWRITES
    return sub == "config" and any(arg.lower().startswith(_CONFIG_REWRITES) for arg in args)


def _holds_loop(command: str, _depth: int = 0) -> bool:
    """True when `command`, or a script one of its legs runs, opens a for, while, until or
    select loop."""
    text, inner = _top_level_text(command)
    for leg in _split_into_legs(text):
        tokens = _shell_tokenize(_ungroup(leg)) or []
        if tokens and tokens[0] in _LOOP_WORDS:
            return True
        words = _leg_words(leg)
        if words and _depth < _MAX_DEPTH and any(
                _holds_loop(script, _depth + 1) for script, _wrapper in _leg_payloads(words)):
            return True
    return _depth < _MAX_DEPTH and any(_holds_loop(found, _depth + 1) for found in inner)


def unbindable_reason(command: str, bound: "dict | None" = None,
                      wrapped: "bool | None" = None) -> "str | None":
    """Why no approval can be bound to a guarded `command` (the mint refuses and the read
    side lets no token pass it), or None. `bound` is its command_repo_binding; `wrapped`
    says whether wrapper_binding binds it, for a caller that has read that. Two values
    that cannot be read are never equal, so each of these refuses on both sides: a directory,
    checked-out branch or remote that cannot be resolved; a word of the guarded git leg
    spelled with an expansion; an earlier leg that moves a remote or sets a GIT_* variable;
    a loop around the operation."""
    command = _normalize_line_continuations(command).strip()
    if bound is not None and any(_UNREADABLE in str(bound.get(axis) or "") for axis in ("repo", "remote")):
        return "a destructive command whose directory, branch or remote cannot be resolved"
    if wrapped is None:
        wrapped = wrapper_binding(command) is not None
    if wrapped:
        if _holds_loop(command):
            return "a destructive command inside a loop, which would run it more than once"
        return None
    # Without a leg separator there is no earlier leg, and without `$` or a backtick no
    # expansion: neither rule below can apply.
    if not _LEGS_OR_EXPANSION_RE.search(command):
        return None
    found = _guarded_leg(command)
    if found is None:
        return None
    index, pairs = found
    if any(_rewrites_remote(stripped, raw) for stripped, raw in pairs[:index]):
        return ("a destructive command behind a leg that changes where a remote points or "
                "sets a GIT_ variable")
    words = _typed_words(pairs[index][1]) or []
    program = next((word for word in words if not _GIT_ENV_RE.match(word)), "")
    if os.path.basename(program) == "git" and any(_EXPANSION_RE.search(word) for word in words):
        return ("a destructive command whose directory, branch, remote or option is spelled "
                "with a shell expansion")
    return None


def approval_binding(command: str) -> dict:
    """The keys an approval binds beside operation, target, flags and repository: `typed`
    (the guarded leg word for word) for an unwrapped operation; `wrapper` (the whole text) and
    `cwd` (the directory the hook ran in, which every relative path in the text starts from)
    for a wrapped one. Mint, read and retire all take them from here."""
    wrapper = wrapper_binding(command)
    if wrapper is None:
        return {"typed": typed_binding(command)}
    start = _remotes._INVOCATION_CWD or os.getcwd()
    return {"wrapper": wrapper, "cwd": os.path.realpath(start)}
