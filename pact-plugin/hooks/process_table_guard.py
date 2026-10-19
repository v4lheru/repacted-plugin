#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/process_table_guard.py
Summary: PreToolUse(Bash) hook. Denies a process-table read that prints command lines
         (pgrep -f/-a, ps with no -o list or with a command column, pstree, top -c, procs,
         htop, btop, a read
         of /proc/<pid>/cmdline or environ, sysctl kern.procargs*), because a token passed as an argument to any running process
         then lands in the transcript. The deny message names the safe form, so the
         caller repairs in the same turn. A second rule (process.pty_wrap) denies a command
         that wraps a human-only KB step (`repact-distill promote`, `repact-distill team`,
         `vault-note drain/decide` or `kb-note drain/decide`) in a pseudo-terminal (script, expect, unbuffer, socat, a
         terminal multiplexer such as screen or tmux, a pty spawn), since that would pass the
         step's terminal check with no human at it.
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
                         runner_prefix, segments, strip_prefix, token_spans, tokens)

_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})
RULE_ID = "process.listing"     # hooks/shared/rules.py holds its sentence and way through
DENY_MESSAGE = (
    "Process listings that print command lines can expose tokens passed as arguments. "
    f"Use `pgrep -x <name>` or `ps -axo pid=,comm=`. [rule: {RULE_ID}]"
)
PTY_RULE_ID = "process.pty_wrap"
PTY_DENY_MESSAGE = (
    "A pseudo-terminal wrapper around a KB step that only a human at a terminal may take would "
    "pass its terminal check with no human there. Ask the user to run the step in their own "
    f"terminal (an agent may run `repact-distill promote --dry-run`). [rule: {PTY_RULE_ID}]"
)
# The wrapper must be a command (script, expect, unbuffer, socat, ... in command position, after any
# runner words such as sudo or nice), or a pty spawn in an interpreter string; the human step may
# be anywhere in the command (an expect -c body or a heredoc is where it hides). A terminal
# multiplexer (screen, tmux, ...) counts in any form, `tmux ls` included: send-keys or -X stuff
# types into a pty it already holds. A brake on habit, not a boundary, like the listing rule: a
# wrapper started from a script file or a name the shell assembles evades it, and so does a
# multiplexer driven across two separate commands, or a terminal from ssh -tt, asciinema rec -c,
# osascript's do script or an editor's terminal (Rule.why in hooks/shared/rules.py says so).
# The step is its program's basename and its verb, each a whole word, in any case (APFS opens
# Vault-Note for vault-note). The forms that never prompt for or record a decision are allowed:
# promote or team (stage and push) with --dry-run in the same simple command (bin/_distill/promote.py
# run and bin/_distill/team.py: no gate, and they return before writing or sending), and a drain with no deciding option anywhere after it, which is the
# list form and --blind-brief (bin/_kb/drain.py cmd_drain). Options are matched as argparse
# reads them, so an abbreviation (--decid, --acc) counts as the option it abbreviates.
_HUMAN_STEPS = (("repact-distill", ("promote", "team")), ("vault-note", ("drain", "decide")),
                ("kb-note", ("drain", "decide")))
_DRAIN_DECIDING = ("--decide", "--decisions", "--accept-all", "--batch", "--page")
_SIMPLE_COMMAND_END = ";&|\n()`"
_REDIRECTS = "<>"
PTY_WRAPPERS = frozenset({"script", "expect", "unbuffer", "socat", "zpty", "faketty", "empty",
                          "screen", "tmux", "byobu", "zellij", "dtach", "abduco"})
# A pty spawn inside an interpreter string, matched lower-cased with the whitespace removed
# (python pty/posix/pexpect and a dynamic import of pty, perl IO::Pty and IO::Tty, ruby PTY,
# node-pty).
# `openpty` and `forkpty` bare (from os import openpty), `pty.spawn` with no paren (ruby
# PTY.spawn "cmd").
_PTY_SPAWNS = ("pty.spawn", "pty.fork(", "openpty", "forkpty", "importpty",
               "importpexpect", "importptyprocess", "frompty", "frompexpect", "fromptyprocess",
               "import__('pty", 'import__("pty',
               "import_module('pty", 'import_module("pty', "io::pty", "io::tty", "node-pty")


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char in "_-"


def _whole_words(text: str, word: str) -> list[int]:
    """The end offset of each place `word` stands in `text` as a whole word: no letter, digit,
    `_` or `-` on either side (kb-notes and --decide are not kb-note and decide)."""
    ends, at = [], text.find(word)
    while at >= 0:
        end = at + len(word)
        if (at == 0 or not _is_word_char(text[at - 1])) \
                and (end == len(text) or not _is_word_char(text[end])):
            ends.append(end)
        at = text.find(word, at + 1)
    return ends


def _options(text: str) -> list[str]:
    """Each `--name` option word in `text`, cut at `=` and at the first non-word character."""
    found = []
    for word in text.split():
        word = word.strip("'\"")
        if word.startswith("--") and len(word) > 2:
            name = word.split("=", 1)[0]
            cut = next((i for i, char in enumerate(name[2:], 2) if not _is_word_char(char)), None)
            found.append(name[:cut])
    return found


def _shell_words(text: str) -> list[str]:
    """The words the shell passes for `text`, up to a `#` comment (--dry-run
    inside a quoted value or a comment is not a dry run). A redirection operator and the word
    after it are not passed (`> --dry-run` names a file). [] when the quoting
    cannot be read, so the step stays refused."""
    words, target = [], False
    for start, _end, word in token_spans(text, _REDIRECTS) or ():
        if text[start] in _REDIRECTS:           # an unquoted operator: its next word is a target
            target = True
        elif text[start] == "#":
            break
        elif target:
            target = False
        else:
            words.append(word)
    return words


def _agent_allowed(verb: str, after: str) -> bool:
    """True when this run of the step can never prompt for or record a decision (see above).
    `after` is the lower-cased command text after the verb."""
    if verb in ("promote", "team"):
        cut = next((i for i, char in enumerate(after) if char in _SIMPLE_COMMAND_END), len(after))
        return "--dry-run" in _shell_words(after[:cut])
    if verb == "drain":
        return not any(flag.startswith(option) for option in _options(after)
                       for flag in _DRAIN_DECIDING)
    return False


def _names_human_step(command: str) -> bool:
    text = command.lower()
    for program, verbs in _HUMAN_STEPS:
        for end in _whole_words(text, program):
            rest = text[end:]
            for verb in verbs:
                named = _whole_words(rest, verb)
                if named and not _agent_allowed(verb, rest[named[0]:]):
                    return True
    return False


def _command_word(word: str) -> str:
    """A word's program name as the shell finds it: quotes and the directory dropped, lower case
    (a case-insensitive file system runs /usr/bin/TMUX)."""
    return word.strip("'\"").rsplit("/", 1)[-1].lower()


def _runs_pty_wrapper(command: str) -> bool:
    squeezed = "".join(command.split()).lower()
    if any(spawn in squeezed for spawn in _PTY_SPAWNS):
        return True
    for words in segments(command):
        # A runner given by path (/usr/bin/env tmux) is still a runner.
        words = [_command_word(word) if _command_word(word) in PREFIX_WORDS else word
                 for word in words]
        index, _ = runner_prefix(words, keep=PTY_WRAPPERS)
        if index < len(words) and _command_word(words[index]) in PTY_WRAPPERS:
            return True
    return False


def wraps_human_gate(command: str) -> bool:
    """True when a command names a human-only KB step and runs a pseudo-terminal wrapper."""
    return _names_human_step(command) and _runs_pty_wrapper(command)

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


# These print every process with its full command line by default (htop and btop on a
# terminal, procs on any output); only a version or help request prints none.
FULL_SCREEN_LISTERS = frozenset({"procs", "htop", "btop", "btm", "glances", "atop"})
_INFO_ONLY = frozenset({"--version", "-V", "--help", "-h"})


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
    if program in FULL_SCREEN_LISTERS:
        return not (len(args) == 1 and args[0] in _INFO_ONLY)
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
    if not isinstance(command, str):
        return None
    reason = DENY_MESSAGE if command_is_listing(command) else \
        PTY_DENY_MESSAGE if wraps_human_gate(command) else None
    if reason is None:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
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
    print(output["hookSpecificOutput"]["permissionDecisionReason"], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
