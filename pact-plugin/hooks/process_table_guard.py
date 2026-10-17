#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/process_table_guard.py
Summary: PreToolUse(Bash) hook. Denies a process-table read that prints command lines
         (pgrep -f/-a, ps with no -o list or with a command column, pstree, top -c, a read
         of /proc/<pid>/cmdline or environ, sysctl kern.procargs*), because a token passed as an argument to any running process
         then lands in the transcript. The deny message names the safe form, so the
         caller repairs in the same turn.
Used by: hooks.json PreToolUse entry (matcher "Bash"), registered by EVENTS
         (the design notes section D1). Tested by
         tests/test_process_table_guard.py.

Nearly importless by design: json, sys and the import-free sibling shell_words (the
tokenizer it shares with env_guard), never the `shared` package, because it runs before
every Bash call. Fail-open: a payload it cannot parse, or any error while
deciding, allows with exit 0; a file that cannot even load exits 1, which the platform
treats as a non-blocking hook error. A guard that cannot run has decided nothing.

Honest limit: string matching. eval, a command assembled in a variable, or a script
file evade it. It is a brake on habit, not a boundary. Quoted text is read as the commands
it may run (a shell's -c string), except the quoted arguments of grep itself, which are a
pattern or a file name, and the body of a quoted-delimiter heredoc that `cat` or `tee` only
writes to a file the rest of the command does not run (file_only_heredocs).

Input: JSON on stdin; reads tool_name and tool_input.command.
Output: deny = the PreToolUse deny envelope on stdout, the reason on stderr, exit 2;
        allow = {"suppressOutput": true}, exit 0.
"""

from __future__ import annotations

import json
import sys

from shell_words import (HEREDOC_MARK, PREFIX_WORDS, Unreadable, quoted_words, read_script,
                         segments, strip_prefix, tokens)

_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})
RULE_ID = "process.listing"     # hooks/shared/rules.py holds its sentence and way through
DENY_MESSAGE = (
    "Process listings that print command lines can expose tokens passed as arguments. "
    f"Use `pgrep -x <name>` or `ps -axo pid=,comm=`. [rule: {RULE_ID}]"
)

# ps -o keywords that print no command line. Anything else is denied (allowlist).
PS_SAFE_KEYWORDS = frozenset({
    "pid", "ppid", "pgid", "comm", "ucomm", "ucmd", "stat", "state", "etime", "etimes",
    "time", "cputime", "rss", "vsz", "%cpu", "%mem", "pcpu", "pmem", "user", "ruser",
    "uid", "ruid", "gid", "rgid", "tty", "tt", "tdev", "lstart", "start", "stime",
    "pri", "nice", "ni", "flags", "wchan", "sess", "tpgid", "nlwp", "psr",
})
# Dashed ps options that take a value; -o/-O are the output lists.
_PS_VALUE_LETTERS = frozenset("oOpgGMNtuUCqsk")
# Format flags that add a command column even alongside -o (dashed, then BSD style).
_PS_FORMAT_DASHED = frozenset("fFjlv")
_PS_FORMAT_BSD = frozenset("ujlvsX")
_PS_LONG_LISTS = ("--format", "-format")

# pgrep/pkill flags that print command lines. pgrep -l alone prints pid + name, so it is
# allowed (lead ruling on task 103); -l with -f still carries the f.
_PGREP_DENY = frozenset("fa")
_PKILL_DENY = frozenset("la")   # pkill -f matches on args but prints nothing
_PGREP_VALUE_LETTERS = frozenset("dFgGJMNPstuU")
_PGREP_DENY_LONG = frozenset({"--full", "--list-full"})


def ps_prints_command_lines(args: list[str]) -> bool:
    """True unless every output list is on the safe keyword allowlist and no format
    flag adds a command column. -O keeps the default columns, CMD included."""
    lists: list[str] = []
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if word.startswith("--"):
            name, _, value = word.partition("=")
            if name in _PS_LONG_LISTS:
                if not value and i < len(args):
                    value, i = args[i], i + 1
                lists.append(value)
            continue
        dashed = word.startswith("-")
        cluster = word[1:] if dashed else word
        if not dashed and not cluster.isalpha():
            continue                                    # a pid or other positional value
        for position, letter in enumerate(cluster):
            if letter == "O":
                return True
            if dashed and letter in _PS_FORMAT_DASHED or not dashed and letter in _PS_FORMAT_BSD:
                return True
            if letter in _PS_VALUE_LETTERS and (dashed or letter in "otpU"):
                value = cluster[position + 1:]
                if not value and i < len(args):
                    value, i = args[i], i + 1
                if letter == "o":
                    lists.append(value)
                break
    if not lists:
        return True
    for listing in lists:
        for keyword in listing.replace(",", " ").split():
            if keyword.split("=", 1)[0].split(":", 1)[0] not in PS_SAFE_KEYWORDS:
                return True
        if not listing.strip():
            return True
    return False


def pgrep_prints_command_lines(args: list[str], deny_letters: frozenset,
                               deny_long: frozenset) -> bool:
    """True when a flag cluster carries a deny letter before any value-taking letter."""
    for word in args:
        if word.startswith("--"):
            if word.split("=", 1)[0] in deny_long:
                return True
            continue
        if not word.startswith("-") or word == "-":
            continue
        for letter in word[1:]:
            if letter in deny_letters:
                return True
            if letter in _PGREP_VALUE_LETTERS:
                break
    return False


def reads_process_argv_file(word: str) -> bool:
    """/proc/<pid>/cmdline or /proc/<pid>/environ (any pid, self, or a glob): the same argv
    or environment a listing would print. Matched on any word, so `tr < /proc/1/cmdline` and
    `xargs -0 < /proc/$pid/environ` count as well as `cat /proc/1/cmdline`."""
    parts = word.split("/")
    return len(parts) == 4 and parts[0] == "" and parts[1] == "proc" and parts[2] != "" \
        and parts[3] in ("cmdline", "environ")


def top_shows_command_lines(args: list[str]) -> bool:
    """Linux top -c (also inside a cluster such as -bc) shows full command lines."""
    return any(word.startswith("-") and not word.startswith("--") and "c" in word[1:]
               for word in args)


def segment_is_listing(words: list[str]) -> bool:
    if any(reads_process_argv_file(word) for word in words):
        return True
    words = strip_prefix(words)
    if not words:
        return False
    program = words[0].rsplit("/", 1)[-1]
    args = words[1:]
    if program == "ps":
        return ps_prints_command_lines(args)
    if program == "pgrep":
        return pgrep_prints_command_lines(args, _PGREP_DENY, _PGREP_DENY_LONG)
    if program == "pkill":
        return pgrep_prints_command_lines(args, _PKILL_DENY, frozenset())
    if program == "pstree":
        return True                                     # the Homebrew build prints args
    if program == "top":
        return top_shows_command_lines(args)
    if program == "sysctl":
        return any(word.startswith("kern.procargs") for word in args)
    return False


# A quoted argument of these programs is data (a pattern, a file name): none of them runs
# an argument as a command. Not rg (its --pre value is a command).
_DATA_PROGRAMS = frozenset({"grep", "egrep", "fgrep"})
# Runner words that hand a later argument to a shell to parse again, or change who runs it.
_REPARSING_PREFIXES = frozenset({"eval", "sh", "bash", "zsh", "watch", "xargs", "sudo", "env",
                                 "exec"})


def _runs_data_program(piece: list[tuple[str, bool]]) -> bool:
    """True when the simple command is grep itself: its program, and every runner word
    before it, written unquoted and ahead of the first quoted word."""
    head: list[str] = []
    for word, quoted in piece:
        if quoted:
            break
        head.append(word)
    command = strip_prefix(head)
    if not command or command[0].rsplit("/", 1)[-1] not in _DATA_PROGRAMS:
        return False
    return not any(word in _REPARSING_PREFIXES or word not in PREFIX_WORDS
                   and not word.startswith("-") and not word[:1].isdigit() and "=" not in word
                   for word in head[:len(head) - len(command)])


def piece_is_listing(piece: list[tuple[str, bool]]) -> bool:
    words = [word for word, _ in piece]
    if _runs_data_program(piece):
        return any(reads_process_argv_file(word) for word in words)
    # Any other program may run its quoted text: read it with the quotes deleted.
    return any(segment_is_listing(each) for each in segments(" ".join(words)))


# The heredoc writers whose body is data, and the programs that may name the written file
# afterwards without running it (as a plain operand only, never piped onward).
_FILE_WRITERS = frozenset({"cat", "tee"})
_TEE_FLAGS = frozenset({"-a", "--append"})
_FILE_READERS = frozenset({"git", "ls", "wc", "head", "tail", "diff", "grep", "stat"})
_SCRIPT_OPERATORS = "|;&()<>\n`"
# Only a target with one of these extensions can hold a data body: a Makefile, conftest.py or
# git hook is run by a program that never names it. Config files that a tool discovers by
# name and runs entries from (npm scripts, pre-commit hooks) are not data either.
_DATA_EXTENSIONS = (".md", ".txt", ".json", ".jsonl", ".log", ".csv", ".yaml", ".yml", ".toml")
_RUNNER_CONFIGS = frozenset({
    "package.json", "composer.json", "deno.json", "pyproject.toml", "cargo.toml",
    "makefile.toml", ".pre-commit-config.yaml", ".pre-commit-config.yml", "lefthook.yml",
    "lefthook.yaml", "taskfile.yml", "taskfile.yaml", "compose.yml", "compose.yaml",
    "docker-compose.yml", "docker-compose.yaml", ".gitlab-ci.yml",
})
_GLOB_CHARACTERS = "*?[{"
_MARK_HEAD, _MARK_TAIL = HEREDOC_MARK.split("{}")


def _mark_index(word: str) -> int | None:
    middle = word[len(_MARK_HEAD):-len(_MARK_TAIL)]
    return int(middle) if word.startswith(_MARK_HEAD) and word.endswith(_MARK_TAIL) \
        and middle.isdigit() else None


def _simple_commands(text: str) -> list[tuple[list[str], bool]] | None:
    """(tokens, piped onward) per simple command of read_script's text, cut at every
    operator run but a pure redirection (`>`, `>>`, `<<`); None when the quoting is
    unreadable. A redirection run stays in the command as its own token."""
    found = tokens(text, _SCRIPT_OPERATORS)
    if found is None:
        return None
    commands: list[tuple[list[str], bool]] = [([], False)]
    for word in found:
        if word and set(word) <= set(_SCRIPT_OPERATORS) and not set(word) <= set("<>"):
            piped = "|" in word.replace("||", "")
            commands[-1] = (commands[-1][0], piped)
            commands.append(([], False))
        else:
            commands[-1][0].append(word)
    return [command for command in commands if command[0]]


def _writer(words: list[str]) -> tuple[int, list[str]] | None:
    """(heredoc index, target files) when the simple command is exactly `cat` or `tee`
    feeding one heredoc to plain files: cat takes no operands, tee only -a and file names,
    every redirection is `>` or `>>` to one plain path. Anything else is None."""
    if not words or words[0].rsplit("/", 1)[-1] not in _FILE_WRITERS:
        return None
    tee = words[0].rsplit("/", 1)[-1] == "tee"
    index, targets, i = None, [], 1
    while i < len(words):
        word, following = words[i], words[i + 1] if i + 1 < len(words) else ""
        if word == "<<" and index is None and _mark_index(following) is not None:
            index, i = _mark_index(following), i + 2
        elif word in (">", ">>") and _plain_path(following):
            targets.append(following)
            i += 2
        elif tee and word in _TEE_FLAGS:
            i += 1
        elif tee and _plain_path(word):
            targets.append(word)
            i += 1
        else:
            return None
    return (index, targets) if index is not None and targets else None


def _plain_path(word: str) -> bool:
    """A file name the shell takes as typed: no expansion, glob or device (but /dev/null)."""
    if word == "/dev/null":
        return True
    return bool(word) and not word.startswith(("-", "/dev/", "/proc/", "~")) \
        and not set(word) & set(_SCRIPT_OPERATORS + "$*?[]{}\\'\" \t")


def _data_target(target: str) -> bool:
    """/dev/null, or a file with a data extension that no tool runs by name, outside .git/."""
    if target == "/dev/null":
        return True
    parts = target.casefold().split("/")
    return ".git" not in parts and parts[-1].endswith(_DATA_EXTENSIONS) \
        and parts[-1] not in _RUNNER_CONFIGS


def file_only_heredocs(text: str, heredocs: list[tuple[bool, str]]) -> set[int]:
    """Indexes of the heredoc bodies that are only written to a file: a quoted delimiter
    (no expansion runs inside the body), fed to a writer (_writer) that is not piped onward,
    every target a data file (_data_target), and no other command of the script or other
    heredoc body mentions a written file except as a plain operand of a reader (git add
    notes.md). `&& bash notes.md`, `; . notes.md`, `./notes.md`, `bash n?tes.md` or a body
    that runs it keep the body a command. Honest limit: a file renamed first or named
    through a variable is not followed."""
    commands = _simple_commands(text)
    if commands is None:
        return set()
    exempt: set[int] = set()
    for position, (words, piped) in enumerate(commands):
        written = _writer(words)
        if written is None or piped:
            continue
        index, targets = written
        if index >= len(heredocs) or heredocs[index][0]:
            continue                                    # a bare delimiter expands $(...)
        if not all(_data_target(target) for target in targets):
            continue
        files = [target.casefold() for target in targets if target != "/dev/null"]
        names = {target.rsplit("/", 1)[-1] for target in files}
        others = [command for at, command in enumerate(commands) if at != position]
        bodies = [body.casefold() for at, (_, body) in enumerate(heredocs) if at != index]
        if any(name in body for name in names for body in bodies):
            continue
        if any(_runs_a_written_file(other, other_piped, files)
               for other, other_piped in others):
            continue
        exempt.add(index)
    return exempt


def _runs_a_written_file(words: list[str], piped: bool, files: list[str]) -> bool:
    """True when the simple command names a written file (casefolded paths in `files`)
    other than as a plain operand of a reader that is not piped onward. Case is folded,
    as macOS opens RUN.SH for run.sh. A word with a glob or brace character names every
    written file: the shell may expand it to one (matching is not modelled, so the
    hook stays importless)."""
    words = [word.casefold() for word in words]
    names = {file.rsplit("/", 1)[-1] for file in files}
    if any(set(word) & set(_GLOB_CHARACTERS) for word in words):
        return True
    mentions = [word for word in words if any(name in word for name in names)]
    if not mentions:
        return False
    if piped or words[0].rsplit("/", 1)[-1] not in _FILE_READERS:
        return True
    return any(word.rsplit("/", 1)[-1] not in names for word in mentions) \
        or words[0] in mentions


def command_is_listing(command: str) -> bool:
    """Comments and heredoc bodies are taken out first (read_script), so a quote character
    in one cannot pair with a later one and hide the commands between them. A heredoc body
    may be run by the program it feeds, and text read_script cannot read may be anything:
    both are read with the quotes deleted, which shows every command they could hold. The
    one exception is a body only written to a file (file_only_heredocs)."""
    try:
        text, heredocs = read_script(command)
    except Unreadable:
        return any(segment_is_listing(words) for words in segments(command))
    data = file_only_heredocs(text, heredocs)
    if any(segment_is_listing(words) for index, (_, body) in enumerate(heredocs)
           if index not in data for words in segments(body)):
        return True
    pieces = quoted_words(text)
    if pieces is None:
        return any(segment_is_listing(words) for words in segments(text))
    return any(piece_is_listing(piece) for piece in pieces)


def decide(payload: dict) -> dict | None:
    """The hook's output for one payload: a deny object, or None to allow."""
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command_is_listing(command):
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": DENY_MESSAGE,
    }}


def main() -> int:
    """Deny: the JSON envelope on stdout, the reason on stderr, exit 2 (the plugin's
    PreToolUse deny convention). Allow: suppressOutput, exit 0. Any error: exit 0."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        output = decide(payload) if isinstance(payload, dict) else None
    except Exception as error:   # noqa: BLE001 - fail-open: a guard that cannot run decided nothing
        print(f"process_table_guard: internal error, allowing (fail-open): "
              f"{type(error).__name__}", file=sys.stderr)
        return 0
    if not output:
        print(_ALLOW_OUTPUT)
        return 0
    print(json.dumps(output))
    print(DENY_MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
