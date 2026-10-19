#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/ops_frame_guard.py
Summary: PreToolUse(Bash) security check, both execution modes: a Bash call that runs one of
    the lead's `repact` operations (dispatch, accept, reject, amend) is refused unless the hook
    frame is the lead's. Who the session is comes from the host payload through
    shared/actor.Actor.runs_lead_ops, never from `--as` or REPACT_AGENT_NAME (a coder can set
    or drop those).
Used by: hooks/dispatch_registry.py (native check `ops_frame_guard:dispatch_check`, deny on
    timeout); `main` runs it alone when the dispatcher runs a deeply nested input apart.

A lead op counts only where the shell runs it: the program word of a simple command (after
runner words such as env, nice, sudo, xargs, `bash -c`, eval and assignments, in any segment,
substitution, `bash -c` or `eval` line, the command of `find -exec`, or a heredoc read by a
shell) is `repact`, a path or glob naming it, or any program given `_repact_ops.py` as a word,
with a lead verb among its later words. So `grep 'repact accept'`, `echo repact accept` and a
commit message that says it are not refused. Words are read by shell.words and
casefolded after NFKC (the default macOS file system is case-insensitive). Text that reader
cannot read is judged by the old quote-dropping scan over every segment, so it fails closed.
The script counts at program position too (`./bin/_repact_ops.py accept 5`, an absolute path,
`cd bin && ./_repact_ops.py ...`), not only as a later word.
In the lead's own frame (actor.is_lead) `repact submit` is refused: every task
submit acts on is dispatched to an agent, and only that agent hands in its work; `--as` cannot
make the lead the coder. The SubagentStop backstop relays a HANDOFF in process, not through
Bash, so it is not affected. A frame with no agent mark that nothing names (the known limit
below) is not the lead's for this rule, so an unmarked coder can still submit.
The launcher or the script run under xargs (or parallel, or find -exec) with no operation
written after its global options (`echo accept 5 | xargs repact`, `xargs -I{} repact {} 5`) is
refused in every frame (rule ops_frame_guard.verb_unseen): the runner adds
words the command does not show, so a coder frame could pass for the lead's.
Known limits: a route hidden in a variable, an alias, a copy of the script, an encoding or a
script file; the ops CLI run by a Python import or runpy inside `python -c` code; a program other than find that runs its arguments; a direct Python import of
shared/ops; and a coder whose host frame carries no agent mark (runs_lead_ops falls back to
the lead).
"""

from __future__ import annotations

import fnmatch
import json
import re
import sys
import unicodedata
from typing import Optional

from shell.words import Unreadable
from shell.words import read_script
from shell.words import runner_prefix
from shell.words import strip_prefix
from shell.words import substitution_spans
from shell.words import tokens

LEAD_VERBS = ("dispatch", "accept", "reject", "amend")
CODER_VERBS = ("submit",)
LAUNCHER = ("repact",)
SCRIPT = ("_repact_ops.py",)
RULE = "ops_frame_guard.lead_only"
SUBMIT_RULE = "ops_frame_guard.lead_submit"
UNSEEN_RULE = "ops_frame_guard.verb_unseen"
# What lead_op_of returns for the launcher run under xargs or find -exec with no verb in sight.
UNSEEN = "(verb unseen)"
# Runners that add words read from stdin or a file to the command they run.
ARG_FEEDERS = frozenset({"xargs", "parallel"})
# xargs' replace options, casefolded as lead_op_of reads them: `-I X` and `-i` both read `-i`.
REPLACE_OPTIONS = ("-i", "-j", "--replace")
_PLAIN_VERB = re.compile(r"[a-z][a-z0-9_-]*")
MAX_DEPTH = 8
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "source", "."})
FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_OPERATORS = "();<>|&\n"
_SEPARATORS = frozenset({";", "|", "&", "&&", "||", "|&", "\n", "(", ")", ";;"})
_REDIRECTS = frozenset({">", ">>", "<", "<<", "<<<", "&>", "&>>", ">&", "<&", ">|", "<>"})
_LINE_CHARS = frozenset(" \t\n;|&<>()`$")
_SEGMENT = re.compile(r"[;&|()\n\r`{}<>]")
_GLOB = re.compile(r"[*?\[]")


def _matches(word: str, names: tuple) -> bool:
    if word in names:
        return True
    return bool(_GLOB.search(word)) and any(fnmatch.fnmatchcase(name, word) for name in names)


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _verb_after(words: list, verbs: tuple = LEAD_VERBS) -> Optional[str]:
    for later in words:
        for verb in verbs:
            if _matches(later, (verb,)):
                return verb
    return None


def _lenient(text: str, verbs: tuple = LEAD_VERBS) -> Optional[str]:
    """The scan for text shell.words cannot read: quotes dropped, every word of every
    segment may be the route. Wider than the shell, so it fails closed."""
    text = text.replace("'", "").replace('"', "").replace("\\", "")
    for segment in _SEGMENT.split(text):
        words = segment.split()
        for index, word in enumerate(words):
            if _matches(_base(word).rsplit("=", 1)[-1], LAUNCHER + SCRIPT):
                verb = _verb_after(words[index + 1:], verbs)
                if verb:
                    return verb
    return None


def _simple_commands(text: str) -> list:
    """The word list of each simple command in `text` (read_script output), redirect
    targets left out. Raises Unreadable when the quoting cannot be read."""
    found = tokens(text, _OPERATORS)
    if found is None:
        raise Unreadable("a quote left open", "close it")
    commands, current, skip = [], [], False
    for word in found:
        if word and all(char in _OPERATORS for char in word):
            if word in _REDIRECTS or word.rstrip("&|").endswith((">", "<")) and \
                    word not in _SEPARATORS:
                skip = True
                continue
            commands.append(current)
            current, skip = [], False
            continue
        if skip:
            skip = False
            continue
        current.append(word)
    commands.append(current)
    return [command for command in commands if command]


def _fed_words(words: list, command: list, fed: Optional[frozenset]) -> tuple:
    """(replace strings, command) for the xargs-like runners in front of `words`: the replace
    strings are empty when such a runner adds words with no replace string, and `fed` as given
    (None: nothing adds words the command does not show) when there is no such runner. The
    text is casefolded, so a bare `-i` may be GNU's `-i` or `-I X`: both are taken, and X is
    skipped when the launcher follows it."""
    runners = [(option, value) for runner, option, value in runner_prefix(words)[1]
               if runner in ARG_FEEDERS]
    if not runners:
        return fed, command
    found = set(fed or ())
    for option, value in runners:
        if option in REPLACE_OPTIONS and value is None:
            found.add("{}")
            if len(command) > 1 and not _matches(_base(command[0]), LAUNCHER + SCRIPT) \
                    and _matches(_base(command[1]), LAUNCHER + SCRIPT):
                found.add(command[0])
                command = command[1:]
        elif option in REPLACE_OPTIONS:
            found.add(value)
        elif option[:2] in ("-i", "-j") and not option.startswith("--"):
            found.add(option[2:])                                 # -I{} glued
        elif option.startswith("--replace="):
            found.add(option.split("=", 1)[1] or "{}")
    return frozenset(found), command


def _verb_visible(args: list, fed: frozenset) -> bool:
    """Whether the ops verb is written out in `args`: the first word after the global
    options is a plain word that holds no replace string."""
    i = 0
    while i < len(args):
        word = args[i]
        if word.startswith("-"):
            i += 1 if "=" in word or word in ("-h", "--help", "--") else 2
            continue
        return bool(_PLAIN_VERB.fullmatch(word)) and not any(r and r in word for r in fed)
    return False


def _command_verb(words: list, depth: int, verbs: tuple = LEAD_VERBS,
                  fed: Optional[frozenset] = None) -> Optional[str]:
    """The verb of `verbs` this one simple command runs, UNSEEN when it runs the launcher
    under a runner that adds words it does not show and no verb is written, or None. `fed`
    holds the replace strings of a find -exec around it."""
    command = strip_prefix(words)
    if not command:
        return None
    fed, command = _fed_words(words, command, fed)
    program = _base(command[0])
    if _matches(program, LAUNCHER + SCRIPT):                     # repact, ./bin/_repact_ops.py
        if fed is not None and not _verb_visible(command[1:], fed):
            return UNSEEN
        verb = _verb_after(command[1:], verbs)
        if verb:
            return verb
    for index, word in enumerate(command[1:], start=1):
        if _matches(_base(word), SCRIPT):
            if fed is not None and not _verb_visible(command[index + 1:], fed):
                return UNSEEN
            verb = _verb_after(command[index + 1:], verbs)
            if verb:
                return verb
    if any(char in _LINE_CHARS for char in command[0]):          # bash -c 'LINE', eval LINE
        verb = _script_verb(" ".join(command), depth + 1, verbs)
        if verb:
            return verb
    if program == "find":
        for index, word in enumerate(command):
            if word in FIND_EXEC:
                verb = _command_verb(command[index + 1:], depth + 1, verbs, frozenset({"{}"}))
                if verb:
                    return verb
    return None


def _script_verb(text: str, depth: int, verbs: tuple = LEAD_VERBS) -> Optional[str]:
    if depth > MAX_DEPTH:
        raise Unreadable("a command nested too deep to read", "flatten it")
    flat, bodies = read_script(text)
    for _start, _end, inner in substitution_spans(flat):
        verb = _script_verb(inner, depth + 1, verbs)
        if verb:
            return verb
    runs_body = False
    for words in _simple_commands(flat):
        verb = _command_verb(words, depth, verbs)
        if verb:
            return verb
        command = strip_prefix(words)
        runs_body = runs_body or bool(command) and _base(command[0]) in SHELLS \
            or any(_base(word) in SHELLS for word in words[:len(words) - len(command)])
    if runs_body:
        for _bare, body in bodies:
            verb = _script_verb(body, depth + 1, verbs)
            if verb:
                return verb
    return None


def lead_op_of(command: str, verbs: tuple = LEAD_VERBS) -> Optional[str]:
    """The verb of `verbs` (default: the lead's) `command` runs through a repact route at
    command position, or None."""
    text = unicodedata.normalize("NFKC", command).casefold()
    try:
        return _script_verb(text, 0, verbs)
    except Unreadable:
        return _lenient(text, verbs)


def _submit_reason(command: str, actor) -> Optional[str]:
    """The deny reason for a `repact submit` in the lead's own frame, or None."""
    if not getattr(actor, "is_lead", False) or lead_op_of(command, CODER_VERBS) is None:
        return None
    return ("ops_frame_guard: `repact submit` hands in a coder's teachback or HANDOFF, and this Bash "
            "call runs in the lead's own frame, so it was refused whatever `--as` says: the lead "
            "accepts, rejects or amends instead. A HANDOFF a subagent returned in its final message "
            f"is relayed by the SubagentStop backstop. [rule: {SUBMIT_RULE}]")


def decide(payload: dict, actor) -> Optional[str]:
    """The deny reason, or None. `actor` is the frame's shared.actor.Actor."""
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return None
    verb = lead_op_of(command)
    if verb is None:
        return _submit_reason(command, actor)
    if verb == UNSEEN:
        return ("ops_frame_guard: this Bash call runs `repact` (or _repact_ops.py) under xargs or "
                "find -exec, which add words the command does not show, and no operation is "
                "written after it, so it was refused in every frame: which operation runs cannot "
                f"be checked. Type the operation out: `repact accept 5`. [rule: {UNSEEN_RULE}]")
    if actor.runs_lead_ops():
        return None
    who = actor.name or "an agent"
    return (f"ops_frame_guard: `repact {verb}` is the lead's command, and this Bash call runs "
            f"in {who}'s frame (the host names it an agent), so it was refused whatever "
            f"`--as` or REPACT_AGENT_NAME say. Hand in your own work with `repact submit <id>`; "
            f"ask the lead to {verb}. [rule: {RULE}]")


def dispatch_check(payload: dict, ctx=None):
    """The dispatcher entry: a block with the rule, or None. An error raises, and the
    registry denies (`on_timeout="deny"`)."""
    import dispatch_registry as registry

    if ctx is not None:
        actor = ctx.actor
    else:
        from shared.actor import resolve_actor

        actor = resolve_actor(payload)
    reason = decide(payload, actor)
    if not reason:
        return None
    return registry.Result(decision="block", reason=reason,
                           rule=next((rule for rule in (SUBMIT_RULE, UNSEEN_RULE)
                                      if f"[rule: {rule}]" in reason), RULE))


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except (ValueError, RecursionError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        from shared.actor import resolve_actor

        reason = decide(payload, resolve_actor(payload))
    except Exception as error:  # noqa: BLE001 - a security check that cannot run refuses
        reason = (f"ops_frame_guard: check failed ({type(error).__name__}); refused (fail closed) "
                  "[rule: ops_frame_guard.error]")
    if not reason:
        print(json.dumps({"suppressOutput": True}))
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
