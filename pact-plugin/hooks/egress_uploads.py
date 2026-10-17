"""The local files a network command uploads BY PATH, for egress_guard to open and scan.

The client reads these files itself, so their contents never appear in the command text:
curl/wget/httpie data and config files, gh/glab api inputs and uploads, scp/rsync local
sources, redirects, pipes and command substitutions a client consumes, through runner words
and inner scripts. Fail closed: a path that is missing, unreadable, built at run time or past
a budget raises Unreadable and the guard denies. `-` (stdin) and binary files are skipped.
Used by hooks/egress_guard.py; stdlib only plus the sibling shell_words.
Map, in file order: command reading and runners; per-client file options (curl, httpie,
wget, gh/glab, scp/rsync); redirects, readers and substitution reads; upload_paths and
_scannable; URL targets (url_targets); file_texts.
Doc: the design notes
"""

from __future__ import annotations

import fnmatch
import glob
import os
import re
import shlex
import sys
from pathlib import Path

import egress_code
from shell_words import (HEREDOC_MARK, RUNNER_COMMAND_OPTIONS, read_script, runner_prefix,
                         substitution_spans)
from shell_words import Unreadable as ShellUnreadable
from shell_words import tokens as shell_tokens

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TREE_FILES = 2000
MAX_TREE_BYTES = 64 * 1024 * 1024
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})
OPERATORS = frozenset({"|", "||", "&", "&&", ";", ";;", "(", ")", "\n"})

CURL_AT_FLAGS = frozenset({"-d", "--data", "--data-binary", "--data-ascii", "--json"})
# -H @f sends each line of f as a header; --url @f reads URLs from f (curl 8.13+).
CURL_HEADER_FLAGS = frozenset({"-H", "--header", "--proxy-header", "--url"})
# name@f / @f, as --data-urlencode reads it (a leading + on --url-query skips the encoding).
CURL_URLENCODE_FLAGS = frozenset({"--data-urlencode", "--url-query"})
CURL_FORM_FLAGS = frozenset({"-F", "--form"})
CURL_UPLOAD_FLAGS = frozenset({"-T", "--upload-file"})
CURL_CONFIG_FLAGS = frozenset({"-K", "--config"})
CURL_VARIABLE = "--variable"
MAX_CONFIG_LINES = 10_000
HEREDOC_WORD = HEREDOC_MARK.split("{", 1)[0]       # read_script's heredoc delimiter
WGET_FILE_FLAGS = frozenset({"--post-file", "--body-file"})
API_INPUT_FLAGS = frozenset({"--input"})
API_FIELD_FLAGS = frozenset({"-F", "--field"})
SCP_VALUE_FLAGS = frozenset({"-c", "-D", "-F", "-i", "-J", "-l", "-o", "-P", "-S", "-X"})
RSYNC_VALUE_FLAGS = frozenset({
    "-e", "-f", "-T", "-B", "-M", "--rsh", "--exclude", "--include", "--filter", "--files-from",
    "--exclude-from", "--include-from", "--password-file", "--port", "--temp-dir", "--log-file",
    "--backup-dir", "--partial-dir", "--compare-dest", "--copy-dest", "--link-dest", "--chmod",
    "--chown", "--timeout", "--bwlimit", "--max-size", "--min-size", "--out-format", "--suffix",
    "--rsync-path", "--remote-option", "--sockopts", "--address", "--usermap", "--groupmap",
    "--info", "--debug", "--skip-compress", "--modify-window", "--contimeout", "--block-size",
    "--max-delete", "--iconv", "--checksum-choice", "--compress-choice", "--compress-level",
})
SHELL_RUNNERS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish"})
# httpie and its look-alikes (xh, xhs) read request items the same way; curlie takes both
# curl options and httpie items.
HTTPIE_PROGRAMS = frozenset({"http", "https", "xh", "xhs"})
UPLOAD_CLIENTS = HTTPIE_PROGRAMS | {"curl", "curlie", "wget", "scp", "rsync"}
# Runner words and their options come from shell_words' one table; shells and eval are kept
# as the program, since their script is read by inner_scripts.
KEPT_RUNNERS = SHELL_RUNNERS | {"eval"}
SHELL_VALUE_FLAGS = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})
FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
MAX_RUNNER_DEPTH = 8
# gh/glab subcommands that send local files: (group, action) -> (flags taking a value,
# flags whose value is an uploaded file, the first positional that is a file (None: none)).
_RELEASE_FLAGS = frozenset({"-R", "--repo", "-n", "--notes", "--name", "-N", "-t", "--title",
                            "--target", "-r", "--ref", "-m", "--milestone", "-a", "--assets-links",
                            "--discussion-category", "--notes-start-tag", "-F", "--notes-file"})
_BODY_FILE = (frozenset({"-F", "--body-file"}), frozenset({"-F", "--body-file"}), None)
GH_UPLOADS = {
    ("gist", "create"): (frozenset({"-d", "--desc", "-f", "--filename"}), frozenset(), 0),
    ("gist", "edit"): (frozenset({"-a", "--add", "-f", "--filename", "-d", "--desc"}),
                       frozenset({"-a", "--add"}), None),
    ("release", "upload"): (_RELEASE_FLAGS, frozenset(), 1),
    ("release", "create"): (_RELEASE_FLAGS, frozenset({"-F", "--notes-file"}), 1),
    ("snippet", "create"): (frozenset({"-t", "--title", "-d", "--description", "-f", "--filename",
                                       "-v", "--visibility", "-R", "--repo"}), frozenset(), 0),
    **{(group, action): _BODY_FILE for group in ("pr", "issue")
       for action in ("create", "comment", "edit", "review")},
}
MAX_LOOP_EXPANSIONS = 64


class Unreadable(Exception):
    """A file the command uploads could not be read, so its contents cannot be checked."""

    def __init__(self, path: str, why: str):
        super().__init__(f"{path} ({why})")
        self.path, self.why = path, why


_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(?:.*?\n)??[ \t]*\2[ \t]*(?=\n|$)", re.S)


def _normalised(command: str) -> str:
    """The command with heredoc bodies removed (their text is scanned as command text, and is
    not commands), `#` comments dropped, and unquoted newlines turned into `;`."""
    command = _HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0], command)
    out, quote, i = [], None, 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                out.append(command[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "\\" and i + 1 < len(command):
            out.append(command[i:i + 2] if command[i + 1] != "\n" else "")   # a line continuation joins
            i += 2
            continue
        elif ch == "#" and (i == 0 or command[i - 1] in " \t\n;&|("):
            while i < len(command) and command[i] != "\n":
                i += 1
            continue
        elif ch == "\n":
            ch = " ; "
        out.append(ch)
        i += 1
    return "".join(out)


def simple_commands(command: str) -> list[tuple[str, list[str]]]:
    """(the operator before it, its words) for each simple command, with quotes parsed (a path
    may hold spaces). Redirections stay as words ('<', '>', '>>'). Falls back to whitespace
    splitting when the quotes do not balance."""
    tokens = shell_tokens(_normalised(command), ";&|()<>")
    if tokens is None:
        tokens = command.replace("|", " | ").replace(";", " ; ").split()
    commands, current, before = [], [], ""
    for token in tokens:
        if token in OPERATORS or token and set(token) <= set(";&|()"):   # "" is a word
            if current:
                commands.append((before, current))
            current, before = [], token
        else:
            current.append(token)
    if current:
        commands.append((before, current))
    return commands


def program_words(words: list[str]) -> list[str]:
    """The words from the program on: leading VAR=value assignments and runner words dropped."""
    return _program(words)[0]


class _Runners:
    """What the runner words before a program do to it. fed: None when no xargs runs it, the
    replacement string under `xargs -I R` (`-i` and --replace mean `{}`), and "" when xargs
    or GNU parallel fills its input into the command. arg_files: the files xargs -a or
    parallel -a reads those arguments from. chdir: the directory env -C / sudo -D moves to,
    or None. script: True when the program word is a shell command line (flock -c, script
    -c), with `command` set when the option carried it attached (--command=...)."""

    def __init__(self, seen: list[tuple[str, str, str | None]]):
        self.fed, self.arg_files, self.chdir = None, [], None
        self.script, self.command = False, None
        for runner, name, value in seen:
            if name in RUNNER_COMMAND_OPTIONS.get(runner, ()):
                self.script, self.command = True, value
            if runner == "parallel":
                self.fed = ""
                if name in ("-a", "--arg-file") and value:
                    self.arg_files.append(value)
            elif runner == "xargs":
                if not name:
                    self.fed = ""
                elif name in ("-I", "-J"):
                    self.fed = value or ""
                elif name.startswith("--replace"):
                    self.fed = value if value is not None else "{}"
                elif name.startswith("-i") and value is None:
                    self.fed = name[2:] or "{}"
                elif name in ("-a", "--arg-file") and value:
                    self.arg_files.append(value)
            elif name in ("-C", "--chdir") and runner == "env" or name in ("-D", "--chdir") and runner == "sudo":
                self.chdir = value


def _program(words: list[str]) -> tuple[list[str], _Runners]:
    """(the words from the program on, what the runner words before it do)."""
    i, seen = runner_prefix(words, KEPT_RUNNERS)
    return words[i:], _Runners(seen)


def _shell_script(words: list[str]) -> str | None:
    """The script a shell runs from its arguments: the -c string, or a here-string. None when
    it runs a script file or reads stdin."""
    i, inline = 1, False
    while i < len(words) and words[i][:1] in "-+" and len(words[i]) > 1 and words[i] != "--":
        word = words[i]
        if word in SHELL_VALUE_FLAGS:
            i += 2
            continue
        inline = inline or (not word.startswith("--") and "c" in word[1:])
        i += 1
    i += words[i:i + 1] == ["--"]
    if inline:
        return words[i] if i < len(words) else ""
    if "<<<" in words[:-1]:
        return words[words.index("<<<") + 1]
    return None


def inner_scripts(words: list[str]) -> list[tuple[str, tuple[str, ...]]]:
    """(script, replacement strings it is filled with) for every command this simple command
    runs as text: a shell's -c script or here-string, eval's words, each find -exec command
    (filled with `{}`). Empty when it runs none."""
    program = words[0].rsplit("/", 1)[-1] if words else ""
    if program in SHELL_RUNNERS:
        script = _shell_script(words)
        return [] if script is None else [(script, ())]
    if program == "eval":
        return [(" ".join(words[1:]), ())]
    found = []
    if program == "find":
        for i, word in enumerate(words):
            if word in FIND_EXEC:
                end = next((j for j in range(i + 1, len(words)) if words[j] in (";", "+")), len(words))
                found.append((shlex.join(words[i + 1:end]), ("{}",)))
    return found


def shell_heredocs(command: str) -> list[str]:
    """The body of every heredoc a shell reads as its script (`bash <<'EOF' ... EOF`)."""
    bodies = []
    for match in _HEREDOC.finditer(command):
        line = command[command.rfind("\n", 0, match.start()) + 1:match.start()]
        parts = simple_commands(line)
        head = program_words(parts[-1][1]) if parts else []
        if head and head[0].rsplit("/", 1)[-1] in SHELL_RUNNERS and _shell_script(head) is None:
            bodies.append("\n".join(match.group(0).split("\n")[1:-1]))
    return bodies


def _flag_value(words: list[str], i: int, flags: frozenset, short_attached: bool = True):
    """(value, next index) when words[i] is one of flags: `-d v`, `--data=v`, `-dv`."""
    word = words[i]
    if word in flags:
        return (words[i + 1] if i + 1 < len(words) else None), i + 2
    if word.startswith("--") and "=" in word and word.split("=", 1)[0] in flags:
        return word.split("=", 1)[1], i + 1
    if short_attached and len(word) > 2 and word[:2] in flags and not word.startswith("--"):
        return word[2:], i + 1
    return None, None


def _curl_files(words: list[str]) -> list[str]:
    found, i = [], 1

    def add(path: str, nxt: int) -> None:
        # `@<(cmd)`: the lexer splits the process substitution off; its reads are a
        # substitution's (substitution_uploads), not a file named "".
        if path or words[nxt:nxt + 1] != ["<("]:
            found.append(path)

    while i < len(words):
        value, nxt = _flag_value(words, i, CURL_AT_FLAGS | CURL_HEADER_FLAGS)
        if nxt is not None:
            if value and value.startswith("@"):
                add(value[1:], nxt)
            i = nxt
            continue
        value, nxt = _flag_value(words, i, CURL_URLENCODE_FLAGS, short_attached=False)
        if nxt is not None:
            value = (value or "").lstrip("+")
            if "@" in value and "=" not in value.split("@", 1)[0]:
                add(value.split("@", 1)[1], nxt)
            i = nxt
            continue
        value, nxt = _flag_value(words, i, CURL_FORM_FLAGS)
        if nxt is not None:
            if value and "=" in value:
                field = value.split("=", 1)[1]
                if field[:1] in ("@", "<"):
                    add(field[1:].split(";", 1)[0], nxt)
            i = nxt
            continue
        value, nxt = _flag_value(words, i, CURL_UPLOAD_FLAGS)
        if nxt is None:
            value, nxt = _flag_value(words, i, CURL_CONFIG_FLAGS)
        if nxt is not None:
            if value:
                found.append(value)
            i = nxt
            continue
        value, nxt = _flag_value(words, i, frozenset({CURL_VARIABLE}), short_attached=False)
        if nxt is not None:
            # --variable name@file (name=content and %ENV read no file)
            if value and value[:1] != "%" and "@" in value and "=" not in value.split("@", 1)[0]:
                found.append(value.split("@", 1)[1])
            i = nxt
            continue
        i += 1
    return found


def _curl_configs(words: list[str]) -> list[str]:
    """The config files (-K/--config) a curl command reads its options from."""
    found, i = [], 1
    while i < len(words):
        value, nxt = _flag_value(words, i, CURL_CONFIG_FLAGS)
        if nxt is None:
            i += 1
            continue
        found += [value] if value else []
        i = nxt
    return found


_CURL_CONFIG_LINE = re.compile(r"[ \t]*(-{0,2}[A-Za-z0-9][\w-]*)(?:[ \t]*[=:][ \t]*|[ \t]+)(.*)")


def _curl_config_words(text: str) -> list[str]:
    """The options a curl config file gives, as command words: `data = "@f"`, `-d @f`,
    `form "f=@x"`. A name without dashes is a long option; a quoted value loses its quotes
    and backslash escapes, an unquoted one ends at a blank."""
    words: list[str] = []
    for line in text.splitlines()[:MAX_CONFIG_LINES]:
        match = _CURL_CONFIG_LINE.match(line)
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip()
        if value.startswith('"'):
            chars, i = [], 1
            while i < len(value) and value[i] != '"':
                i += value[i] == "\\" and i + 1 < len(value)
                chars.append(value[i])
                i += 1
            value = "".join(chars)
        else:
            value = (value.split() or [""])[0]
        words += [name if name.startswith("-") else "--" + name, value]
    return words


def _stdin_config_uploads(words: list[str], stage_before: list[str] | None, here: str,
                          variables: dict[str, str]) -> list[str]:
    """The files a curl config read from stdin (`-K -`) names: from a here-string, a `< f`
    redirect, an echo/printf or a cat piped into curl. Any other stdin fails closed."""
    if "<<<" in words[:-1]:
        text = words[words.index("<<<") + 1]
    elif "<" in words[:-1]:
        return _config_uploads(words[words.index("<") + 1], here, variables)
    elif stage_before and program_words(stage_before)[:1] in (["echo"], ["printf"]):
        text = "\n".join(program_words(stage_before)[1:]).replace("\\n", "\n")
    elif stage_before and program_words(stage_before)[:1] == ["cat"]:
        return [path for config in _cat_inputs(program_words(stage_before))
                for path in _config_uploads(config, here, variables)]
    else:
        raise Unreadable("curl -K -", "its options come from a stdin this check cannot read")
    return _curl_files(["curl", *_curl_config_words(text)])


def _config_uploads(config: str, here: str, variables: dict[str, str]) -> list[str]:
    """The files a curl config file names for upload, one level deep (a config it names in
    turn is scanned as a file, not read for options). Empty when the config cannot be read:
    the config itself is an uploaded path, so reading it fails closed there."""
    resolved = _expand(config, variables)
    if resolved is None or resolved == "-":
        return []
    try:
        path = Path(_resolve(resolved, here))
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        text = path.read_bytes().decode("utf-8", errors="ignore")
    except OSError:
        return []
    return _curl_files(["curl", *_curl_config_words(text)])


# httpie request items that read a file: name@file (a form upload), name=@file (a field's
# text), name:=@file (a field's JSON), Header:@file, and a bare @file.
_HTTPIE_FILE_ITEM = re.compile(r"[^=:@]*(?::=|=|:)?@(.+)")


def _httpie_files(words: list[str]) -> list[str]:
    items = _positionals(words, HTTPIE_VALUE)[0]
    if len(items) > 1 and items[0].isalpha() and items[0].isupper():
        items = items[1:]                                # [METHOD] URL [request items]
    return [match.group(1) for match in map(_HTTPIE_FILE_ITEM.fullmatch, items[1:]) if match]


def _wget_files(words: list[str]) -> list[str]:
    found, i = [], 1
    while i < len(words):
        value, nxt = _flag_value(words, i, WGET_FILE_FLAGS, short_attached=False)
        if nxt is not None:
            if value:
                found.append(value)
            i = nxt
        else:
            i += 1
    return found


def _gh_files(words: list[str]) -> list[str]:
    """Files a gh/glab command sends: `api` inputs and fields, and the GH_UPLOADS subcommands."""
    rule = GH_UPLOADS.get(tuple(words[1:3]))
    if rule is None:
        return _api_files(words)
    value_flags, file_flags, first = rule
    found, i = [], 3
    while i < len(words):
        value, nxt = _flag_value(words, i, file_flags, short_attached=False)
        if nxt is not None:
            found += [value] if value else []
            i = nxt
            continue
        i += 1
    if first is not None:
        positionals = _positionals(words[2:], value_flags)[0]
        found += [arg.split("#", 1)[0] if words[1] == "release" else arg for arg in positionals[first:]]
    return found


def gh_uploads(words: list[str]) -> bool:
    """True when this gh/glab command may send local files (or its subcommand is not named)."""
    return len(words) < 3 or words[1] == "api" or tuple(words[1:3]) in GH_UPLOADS


def _api_files(words: list[str]) -> list[str]:
    if "api" not in words[1:3]:
        return []
    found, i = [], 1
    while i < len(words):
        value, nxt = _flag_value(words, i, API_INPUT_FLAGS, short_attached=False)
        if nxt is not None:
            if value:
                found.append(value)
            i = nxt
            continue
        value, nxt = _flag_value(words, i, API_FIELD_FLAGS)
        if nxt is not None:
            if value and "=@" in value:
                found.append(value.split("=@", 1)[1])
            i = nxt
            continue
        i += 1
    return found


def _is_remote(arg: str) -> bool:
    """scp/rsync: [user@]host:path, host::module or a scp:// / rsync:// URL. A path that starts
    with /, ./, ../ or ~ is local even when it holds a colon."""
    if "://" in arg:
        return True
    if arg.startswith(("/", "./", "../", "~")):
        return False
    head = arg.split("/", 1)[0]
    return ":" in head


def _positionals(words: list[str], value_flags: frozenset) -> tuple[list[str], list[str]]:
    """(positional arguments, rsync --exclude patterns) after the program word."""
    args, excludes, i = [], [], 1
    while i < len(words):
        word = words[i]
        if word == "--":
            args += words[i + 1:]
            break
        if word.startswith("--") and "=" in word:
            name, value = word.split("=", 1)
            if name == "--exclude":
                excludes.append(value)
            i += 1
        elif word in value_flags:
            if word == "--exclude" and i + 1 < len(words):
                excludes.append(words[i + 1])
            i += 2
        elif word.startswith("-") and len(word) > 1:
            i += 1
        else:
            args.append(word)
            i += 1
    return args, excludes


def _copy_sources(words: list[str], value_flags: frozenset) -> tuple[list[str], list[str]]:
    args, excludes = _positionals(words, value_flags)
    if len(args) < 2 or not _is_remote(args[-1]):
        return [], []
    return [arg for arg in args[:-1] if not _is_remote(arg)], excludes


def _redirected_files(words: list[str]) -> list[str]:
    found = []
    for i, word in enumerate(words[:-1]):
        if word == "<" and words[i + 1] not in ("<", "("):
            found.append(words[i + 1])
    return found


REDIRECTS = frozenset({"<", "<<", "<<<", ">", ">>", ">|", "<>"})


def _cat_inputs(words: list[str]) -> list[str]:
    """The files `cat` reads: its operands and a `< f` redirect, never a heredoc word or an
    output target."""
    found, i = [], 1
    while i < len(words):
        word = words[i]
        if word in REDIRECTS:
            if word == "<" and i + 1 < len(words):
                found.append(words[i + 1])
            i += 2
            continue
        if not word.startswith("-") or word == "-":
            found.append(word)
        i += 1
    return found


# Programs whose output inside a substitution is the text of the files they read.
SUBSTITUTION_READERS = frozenset({"cat", "tac", "nl", "head", "tail", "base64", "xxd", "od",
                                  "strings", "rev"})
READER_VALUE_FLAGS = frozenset({"-n", "-c", "-b", "-l", "-s", "-g", "-j", "-N", "-t", "-A", "-w",
                                "-o", "--output", "--lines", "--bytes"})
READER_INPUT_FLAGS = frozenset({"-i", "--input"})             # base64 -i FILE


def _reader_inputs(words: list[str]) -> list[str]:
    """The files a reader reads: its operands, `-i F` and a `< f` redirect; never a heredoc
    (its body is part of the command text) or an output target."""
    found, i = [], 1
    while i < len(words):
        word = words[i]
        if word in REDIRECTS or word in READER_VALUE_FLAGS or word in READER_INPUT_FLAGS:
            if word in ("<", *READER_INPUT_FLAGS) and i + 1 < len(words):
                found.append(words[i + 1])
            i += 2
            continue
        if word.startswith("--input="):
            found.append(word.split("=", 1)[1])
        elif (not word.startswith("-") or word == "-") and not word.startswith(HEREDOC_WORD):
            found.append(word)
        i += 1
    return found


# A read is (path, required). A required path is one a reader or a `<` names: it fails closed
# when it cannot be read. Any other word of a substitution's command that names an existing
# file (`sed -n p F`, `jq . F`, `dd if=F`, an awk program's `getline < "F"`) is scanned too,
# but only when it is a regular file within the scan limit: a word naming nothing is no read.
# An Unreadable in a list of reads is raised when a client consumes it, never before. An
# egress_code.CodeRead among them is an interpreter command: upload_paths reads its code.
Read = tuple[str, bool]
# Programs that run code: what they read cannot be told from their words, so one inside a
# substitution a client consumes fails closed (a shell's -c script is read instead).
INTERPRETERS = re.compile(r"(?:python|pypy|perl|ruby|php|lua|luajit|tclsh)[\d.]*|node|nodejs|deno"
                          r"|bun|osascript|Rscript|pwsh|powershell|ts-node|tsx")
AWK_PROGRAMS = frozenset({"awk", "gawk", "mawk", "nawk", "busybox-awk"})
_PATH_TOKEN = re.compile(r"[\w./~+@%${}-]+")
MAX_NAMED_TOKENS = 256


def _named_files(words: list[str]) -> list[str]:
    """Every word, and every path-shaped piece of one, that may name a file: `if=F` gives F,
    `getline < "F"` gives F. Option words are not files."""
    found: list[str] = []
    for word in words:
        for candidate in (word, *_PATH_TOKEN.findall(word)):
            candidate = candidate.lstrip("@")
            if candidate and not candidate.startswith("-") and candidate not in found:
                found.append(candidate)
                if len(found) >= MAX_NAMED_TOKENS:
                    return found
    return found


def _with_process_substitutions(text: str) -> str:
    """text with each unquoted `<(cmd)` spelled `$(cmd)`: the client reads cmd's output as a
    file (`curl -d @<(cat F)`), which is what a substitution hands it."""
    out, quote = [], ""
    for i, char in enumerate(text):
        if char in "'\"" and (not quote or quote == char) and (i == 0 or text[i - 1] != "\\"):
            quote = "" if quote else char
        elif not quote and char == "<" and text.startswith("<(", i) and not text.startswith("<<(", i - 1):
            char = "$"
        out.append(char)
    return "".join(out)


def substitution_reads(text: str, quoting: bool = True, depth: int = 0) -> list:
    """The reads of the command substitutions in `text`, handed on as text: `$(cat F)`,
    `$(<F)`, `` `head -c 99 F` ``, `$(base64 < F)`, `$(sed -n p F)`, nested ones too. text is
    read_script output (quoting on) or a heredoc body whose substitutions expand."""
    found: list = []
    for _start, _end, inner in substitution_spans(text, quoting):
        found += _lazy_reads(inner, depth + 1)
    return found


def _lazy(reader, *args) -> list:
    """reader(*args), or [the Unreadable it raised]: raised later, if a client consumes it."""
    try:
        return reader(*args)
    except Unreadable as error:
        return [error]


def _lazy_reads(inner: str, depth: int) -> list:
    return _lazy(_command_reads, inner, depth)


def _command_reads(inner: str, depth: int) -> list[Read]:
    """The reads of one substitution's command: what it may put into its output."""
    if depth > MAX_RUNNER_DEPTH:
        raise Unreadable("a substitution", "substitutions nested too deep to read")
    inner = _with_process_substitutions(inner)
    found: list[Read] = []
    for _before, raw in simple_commands(inner):
        found += _words_reads(raw, depth)
    for _start, _end, nested in substitution_spans(inner):
        found += _command_reads(nested, depth + 1)
    return found


def _words_reads(raw: list[str], depth: int) -> list[Read]:
    """The reads of one simple command inside a substitution (see Read)."""
    words = program_words(raw)
    program = words[0].rsplit("/", 1)[-1] if words else ""
    if INTERPRETERS.fullmatch(program):
        # Python or Node whose code text can be found is read by egress_code.code_uploads.
        form = (egress_code.code_form(program, words) if raw[:1] == words[:1]
                else "it runs under a runner or a changed environment")
        if isinstance(form, str):
            raise Unreadable(f"$({program} ...)",
                             f"{program} runs code whose reads cannot be checked: {form}")
        return [form]
    if program in SHELL_RUNNERS:
        script = _shell_script(words)
        if script is None:
            raise Unreadable(f"$({program} ...)", "a shell runs a script whose reads cannot be checked")
        return _command_reads(script, depth + 1)
    if program == "env-get":
        # Its key file is the sanctioned source of the value it hands on (the documented
        # `-H @<(env-get FILE KEY -- sh -c 'printf ...')`); the command it runs is read.
        rest = words[words.index("--") + 1:] if "--" in words else []
        return _words_reads(rest, depth + 1) if rest else []
    if program in AWK_PROGRAMS and any("system" in word or "|" in word for word in words[1:]):
        raise Unreadable(f"$({program} ...)", "an awk program runs commands whose reads cannot be checked")
    required = _reader_inputs(words) if program in SUBSTITUTION_READERS else _redirected_files(raw)
    named = [path for path in _named_files(words[1:]) if path not in required]
    return [(path, True) for path in required] + [(path, False) for path in named]


def _consumed(entries: list) -> list[Read]:
    """The reads a client consumes; an Unreadable among them is raised now."""
    for entry in entries:
        if isinstance(entry, Unreadable):
            raise entry
    return entries


SUBSTITUTION_WORD = "__repact_substitution_{}__"
_SUBSTITUTION_REF = re.compile(r"__repact_substitution_(\d+)__")
_HEREDOC_REF = re.compile(re.escape(HEREDOC_WORD) + r"(\d+)__")
DECLARERS = frozenset({"export", "local", "declare", "typeset", "readonly"})
# Every use of a variable's value: $T, ${T}, ${T:0:40}, ${T//a/b}, ${#T}, ${T[0]}.
_VARIABLE_USE = re.compile(r"\$(?:\{[#!]?([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*))")
READERS_INTO_VARIABLES = frozenset({"read", "mapfile", "readarray", "printf"})
# read/mapfile options that take a value (read -a NAME is the name itself).
_READ_VALUE_OPTIONS = {"read": frozenset({"-d", "-i", "-n", "-N", "-p", "-t", "-u", "-k"}),
                       "mapfile": frozenset({"-d", "-n", "-O", "-s", "-u", "-C", "-c"})}


def _variable_refs(text: str) -> list[str]:
    return [match.group(1) or match.group(2) for match in _VARIABLE_USE.finditer(text)]


def _read_names(words: list[str]) -> list[str]:
    """The variables `read [-r] A B`, `read -a ARR`, `mapfile -t ARR` or `printf -v T` set."""
    program, names, i = words[0], [], 1
    while i < len(words):
        word = words[i]
        if word in REDIRECTS:
            i += 2
            continue
        if program == "printf":
            if word == "-v" and i + 1 < len(words):
                return [words[i + 1]]
            if word.startswith("-v") and word[2:].isidentifier():
                return [word[2:]]
            if not word.startswith("-"):
                return []
        elif word in _READ_VALUE_OPTIONS["read" if program == "read" else "mapfile"]:
            i += 1
        elif word.startswith("-"):
            pass
        elif word.isidentifier():
            names.append(word)
        i += 1
    if program == "printf":
        return []
    return names or (["REPLY"] if program == "read" else ["MAPFILE"])


def substitution_uploads(command: str, clients: frozenset) -> list[Read]:
    """The files a substitution reads into what a network client sends: one written in a
    pipeline that names a client (an argument, a URL, a header, a heredoc it reads, a stage
    piped into it, a `<(cmd)` file it reads), or assigned to a variable such a pipeline uses
    (`T=$(cat F); curl -H "X: $T"`, `read T < F`, `printf -v T`, `for T in $(cat F)`). A
    substitution whose value no client sees (`T=$(cat F); curl URL`) is not an upload. Text
    the shared reader cannot read fails closed."""
    try:
        flat, bodies = read_script(command)
    except ShellUnreadable as odd:
        raise Unreadable("this command", f"{odd.what}; {odd.instead}") from None
    names = re.compile(r"(?<![\w.-])(?:%s)(?![\w.:-])" % "|".join(map(re.escape, sorted(clients))))
    return _text_uploads(flat, bodies, names, {}, 0)


def _text_uploads(flat: str, bodies: list[tuple[bool, str]], names: re.Pattern,
                  assigned: dict[str, list], depth: int) -> list[Read]:
    if depth > MAX_RUNNER_DEPTH:
        raise Unreadable("a substitution", "substitutions nested too deep to read")
    flat = _with_process_substitutions(flat)
    spans = substitution_spans(flat)
    reads = [_lazy_reads(inner, depth + 1) for _start, _end, inner in spans]
    inner_refs = [_variable_refs(inner) for _start, _end, inner in spans]
    found: list[Read] = []
    for _start, _end, inner in spans:                    # a client run inside a substitution
        found += _text_uploads(inner, bodies, names, dict(assigned), depth + 1)
    for index, (start, end, _inner) in reversed(list(enumerate(spans))):
        flat = flat[:start] + SUBSTITUTION_WORD.format(index) + flat[end:]
    pipelines: list[list[list[str]]] = []
    loop_inputs: list[Read] = []                         # `done < F` feeds the loop's reads
    for before, raw in simple_commands(flat):
        if raw[:1] == ["done"]:
            loop_inputs += [(path, True) for path in _redirected_files(raw)]
        if before == "|" and pipelines:
            pipelines[-1].append(raw)
        else:
            pipelines.append([raw])

    def flows(text: str) -> list:
        """The reads a word's value carries: its substitutions and the variables it uses."""
        entries = [entry for n in _SUBSTITUTION_REF.findall(text) for entry in reads[int(n)]]
        refs = _variable_refs(text) + [ref for n in _SUBSTITUTION_REF.findall(text) for ref in inner_refs[int(n)]]
        return entries + [entry for ref in refs for entry in assigned.get(ref, [])]

    for pipeline in pipelines:
        text = " ".join(word for raw in pipeline for word in raw)
        if names.search(text):
            found += _consumed(flows(text))
            # A stage piped towards a client hands it what it reads (`grep x F | curl -d @-`,
            # `jq . F | sort | ssh h`), whether or not the client names its stdin.
            client_at = max(k for k, raw in enumerate(pipeline) if names.search(" ".join(raw)))
            for raw in pipeline[:client_at]:
                found += _consumed(_lazy(_words_reads, raw, depth + 1))
            for n in _HEREDOC_REF.findall(text):
                bare, body = bodies[int(n)] if int(n) < len(bodies) else (False, "")
                if bare:
                    found += _consumed(substitution_reads(body, quoting=False, depth=depth))
                    found += _consumed([entry for ref in _variable_refs(body) for entry in assigned.get(ref, [])])
            continue
        for k, raw in enumerate(pipeline):
            words = program_words(raw)
            program = words[0].rsplit("/", 1)[-1] if words else ""
            targets = _read_names([program, *words[1:]]) if program in READERS_INTO_VARIABLES else []
            if targets:
                sources = flows(" ".join(raw))
                if program != "printf":
                    sources += [(path, True) for path in _redirected_files(raw)]
                    for earlier in pipeline[:k]:         # `grep x F | sort | while read T`
                        sources += _lazy(_words_reads, earlier, depth + 1)
                    if not k and "<" not in raw:
                        sources += loop_inputs
                for name in targets:
                    assigned[name] = assigned.get(name, []) + sources
                continue
            if program in ("for", "select") and len(words) > 2 and words[2] == "in":
                assigned[words[1]] = flows(" ".join(words[3:]))
                continue
            for word in raw[1:] if raw[:1] and raw[0] in DECLARERS else raw:
                name, sep, value = word.partition("=")
                append = name.endswith("+")
                name = name[:-1] if append else name
                if sep and name.isidentifier():
                    assigned[name] = (assigned.get(name, []) if append else []) + flows(value)
    return found


def _written_files(words: list[str]) -> list[str]:
    """Files this simple command writes: `> f`, `>> f`, `tee [-a] f...`."""
    found = [words[i + 1] for i, word in enumerate(words[:-1]) if word in (">", ">>", ">|")]
    if words and words[0].rsplit("/", 1)[-1] == "tee":
        found += [word for word in words[1:] if not word.startswith("-")]
    return found


_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
ENVIRONMENT_NAMES = ("HOME", "TMPDIR", "USER")


def _assignments(words: list[str], variables: dict[str, str]) -> None:
    """Record literal `NAME=value` words (leading, or after export) into variables."""
    for word in words[1:] if words[:1] == ["export"] else words:
        name, sep, value = word.partition("=")
        if not sep or not name.isidentifier():
            if words[:1] != ["export"]:
                break
            continue
        resolved = _expand(value, variables)
        if resolved is None:
            variables.pop(name, None)
        else:
            variables[name] = resolved


def _expand(text: str, variables: dict[str, str]):
    """text with $NAME / ${NAME} replaced from variables (or HOME/TMPDIR/USER from the
    environment); None when it holds anything else built at run time."""
    if "$(" in text or "`" in text or text == "$":
        return None

    def value(match):
        name = match.group(1) or match.group(2)
        if name in variables:
            return variables[name]
        if name in ENVIRONMENT_NAMES and name in os.environ:
            return os.environ[name]
        raise KeyError(name)
    try:
        expanded = _VARIABLE.sub(value, text)
    except KeyError:
        return None
    return None if "$" in expanded else expanded


def _loop_expansions(text: str, loops: dict[str, list[str]]) -> list[str]:
    """text once per value of each `for NAME in a b c` loop variable it uses (literal lists only;
    a list holding a glob or a substitution is left for _expand to refuse)."""
    expansions = [text]
    for name, values in loops.items():
        pattern = re.compile(r"\$(?:\{%s\}|%s(?![A-Za-z0-9_]))" % (name, name))
        if not pattern.search(text):
            continue
        if not values or any(c in value for value in values for c in "$`*?["):
            return []
        expansions = [pattern.sub(value.replace("\\", "\\\\"), item) for item in expansions for value in values]
        if len(expansions) > MAX_LOOP_EXPANSIONS:
            return []
    return expansions if expansions != [text] else []


def _resolve(path: str, here: str) -> str:
    expanded = os.path.expanduser(path)
    return os.path.normpath(expanded if os.path.isabs(expanded) else os.path.join(here, expanded))


def upload_paths(command: str, cwd: str, clients: frozenset, depth: int = 0,
                 fills: tuple[str, ...] = ()) -> list[tuple[str, str, list[str]]]:
    """(path, directory it resolves against, rsync excludes) for every local file a network
    client in the command uploads by path. Variables assigned a literal earlier in the command
    are expanded; a path still built at run time is returned as written, so reading it fails
    closed. A file an earlier part of the same command writes is skipped: its text comes from
    the command, which the guard scans as text. A script a runner executes is read the same
    way, at depth + 1; fills are the replacement strings (xargs -I, find {}) that stand for a
    value only known at run time."""
    if depth > MAX_RUNNER_DEPTH:
        raise Unreadable("the nested command", "runners nested too deep to read")
    found: list[tuple[str, str, list[str]]] = []
    here = cwd
    variables: dict[str, str] = {}
    loops: dict[str, list[str]] = {}
    written: set[str] = set()
    piped_cat: list[tuple[str, str]] = []
    places = [cwd]                         # every directory a part of the command runs in
    for body in shell_heredocs(command):
        found += upload_paths(body, here, clients, depth + 1, fills)
    last: list[str] | None = None
    for before, raw in simple_commands(command):
        stage_before, last = (last if before == "|" else None), raw
        _assignments(raw, variables)
        words, runners = _program(raw)
        fed = runners.fed
        cat_files, piped_cat = (piped_cat if before == "|" else []), []
        filled = fills + ((fed,) if fed else ())
        if runners.script:                       # flock -c 'curl ...', script -c '...'
            script = runners.command if runners.command is not None else (words or [""])[0]
            found += upload_paths(script, here, clients, depth + 1, filled)
            continue
        if not words:
            continue
        program = words[0].rsplit("/", 1)[-1]
        scripts = inner_scripts(words)
        for script, more in scripts:
            found += upload_paths(script, here, clients, depth + 1, filled + more)
        if program not in clients and not scripts and program not in ("cat", "cd", "for"):
            # A client run by a word this reader does not know (strace -f curl ...,
            # proxychains curl ...) is read from the client word on.
            k = next((k for k, word in enumerate(words[1:], 1) if word.rsplit("/", 1)[-1] in clients), 0)
            words, program = words[k:], words[k].rsplit("/", 1)[-1]
        if fed == "" and (program in UPLOAD_CLIENTS or program in ("gh", "glab") and gh_uploads(words)):
            raise Unreadable(f"{program} under xargs or parallel",
                             "its arguments are filled in at run time")
        for target in _written_files(words):
            resolved = _expand(target, variables)
            if resolved is not None:
                written.add(_resolve(resolved, here))
        if program == "for" and len(words) >= 3 and words[2] == "in" and words[1].isidentifier():
            loops[words[1]] = [value for value in words[3:] if value != "do"]
            continue
        if program == "cd" and len(words) == 2:
            target = _expand(words[1], variables)
            if target is not None and not any(c in target for c in "*?"):
                here = _resolve(target, here)
                places.append(here)
            continue
        if program == "cat":
            piped_cat = [(word, here) for word in _cat_inputs(words)]
            continue
        if program not in clients:
            continue
        where = here
        if runners.chdir is not None:            # env -C DIR, sudo -D DIR
            target = _expand(runners.chdir, variables)
            if target is None:
                raise Unreadable(runners.chdir, "the client runs in a directory set at run time")
            where = _resolve(target, here)
            places.append(where)
        paths: list[str] = list(runners.arg_files)
        excludes: list[str] = []
        if program in ("curl", "curlie"):
            paths += _curl_files(words)
            for config in _curl_configs(words):
                if config == "-":
                    paths += _stdin_config_uploads(words, stage_before, where, variables)
                else:
                    paths += _config_uploads(config, where, variables)
        if program in HTTPIE_PROGRAMS or program == "curlie":
            paths += _httpie_files(words)
        elif program == "wget":
            paths += _wget_files(words)
        elif program in ("gh", "glab"):
            paths += _gh_files(words)
        elif program in ("scp", "rsync"):
            if "$" in words:                         # `$(...)`: the lexer split it off
                paths += ["$(...)"]
            else:
                sources, excludes = _copy_sources(
                    words, SCP_VALUE_FLAGS if program == "scp" else RSYNC_VALUE_FLAGS)
                paths += sources
        paths += _redirected_files(words)
        for path, where in [(path, where) for path in paths] + cat_files:
            if path == "-":
                continue
            if any(fill in path for fill in filled):
                raise Unreadable(path, "filled in at run time")
            expansions = _loop_expansions(path, loops)
            for candidate in expansions or [path]:
                resolved = _expand(candidate, variables)
                if resolved is None:
                    found.append((path, where, excludes))
                    break
                if _resolve(resolved, where) not in written:
                    found.append((resolved, where, excludes))
    # A file a substitution reads into what a client sends is uploaded like a file it names:
    # resolved where the command runs (each directory it visits that holds it).
    # A word that only may name a file is read where it does name one (see Read).
    for entry in substitution_uploads(command, clients):
        if isinstance(entry, egress_code.CodeRead):
            found += [each for each in egress_code.code_uploads(
                sys.modules[__name__], entry, command, places, variables, written, fills)
                if each not in found]
            continue
        path, required = entry
        if path == "-":
            continue
        if any(fill in path for fill in fills):
            if not required:
                continue
            raise Unreadable(path, "filled in at run time")
        resolved = _expand(path, variables)
        if resolved is None:
            if required:
                found.append((path, cwd, []))
            continue
        if required:
            homes = [place for place in places if os.path.lexists(_resolve(resolved, place))] or [cwd]
            targets = [(resolved, place) for place in homes]
        else:
            targets = [(name, place) for place in places for name in _scannable(resolved, place)]
        for name, place in dict.fromkeys(targets):
            # `cat F | curl` is also read above (piped_cat): one report per file and place.
            if _resolve(name, place) not in written and (name, place, []) not in found:
                found.append((name, place, []))
    return found


def _scannable(path: str, place: str) -> list[str]:
    """The regular files within the scan limit that path names in place (a glob may name
    several); a directory, a missing name or a larger file names none."""
    base = _resolve(path, place)
    names = sorted(glob.glob(base)) if any(c in path for c in "*?[") else [base]
    found = []
    for name in names:
        try:
            if os.path.isfile(name) and os.path.getsize(name) <= MAX_FILE_BYTES:
                found.append(path if name == base else name)
        except OSError:
            continue
    return found[:MAX_NAMED_TOKENS]


# What a URL client is aimed at. Options in *_ELSEWHERE send the request somewhere the target
# words do not name (a proxy, a connect-to or resolve override, a config or URL-list file), so
# the targets are unknown; *_VALUE options take a value that is not a target.
CURL_ELSEWHERE = frozenset({"-x", "--proxy", "--preproxy", "--socks4", "--socks4a", "--socks5",
                            "--socks5-hostname", "--proxy1.0", "--connect-to", "--resolve", "-K",
                            "--config", "--doh-url", "-:", "--next"})
CURL_VALUE = frozenset({
    "-H", "--header", "-d", "--data", "--data-binary", "--data-raw", "--data-ascii",
    "--data-urlencode", "--json", "-F", "--form", "--form-string", "-X", "--request", "-o",
    "--output", "-u", "--user", "-A", "--user-agent", "-e", "--referer", "-b", "--cookie", "-c",
    "--cookie-jar", "-T", "--upload-file", "-m", "--max-time", "--connect-timeout", "-w",
    "--write-out", "-U", "--proxy-user", "-E", "--cert", "--key", "--cacert", "--capath", "-r",
    "--range", "-C", "--continue-at", "-Y", "--speed-limit", "-y", "--speed-time", "-z",
    "--time-cond", "--retry", "--retry-delay", "--retry-max-time", "--limit-rate", "--max-redirs",
    "--max-filesize", "--output-dir", "--trace", "--trace-ascii", "--stderr", "-D",
    "--dump-header", "--interface", "--local-port", "-Q", "--quote", "--oauth2-bearer",
    "--aws-sigv4", "--unix-socket", "--abstract-unix-socket", "-t", "--telnet-option",
    "--variable", "--cert-type", "--key-type", "--pass", "--ciphers", "--tls-max", "--url-query",
    "--expect100-timeout", "--keepalive-time", "--happy-eyeballs-timeout-ms", "--mail-from",
    "--mail-rcpt", "--mail-auth", "--login-options", "--sasl-authzid", "--service-name",
})
CURL_SHORT_VALUE = frozenset("HdFXouAebcTmwUErCYyzDQtKx:")
WGET_ELSEWHERE = frozenset({"-i", "--input-file", "-e", "--execute", "--config", "-B", "--base",
                            "--input-metalink"})
WGET_VALUE = frozenset({
    "-O", "--output-document", "-o", "--output-file", "-a", "--append-output", "-t", "--tries",
    "-T", "--timeout", "-w", "--wait", "-U", "--user-agent", "-P", "--directory-prefix", "-Q",
    "--quota", "-l", "--level", "-A", "--accept", "-R", "--reject", "-D", "--domains", "-I",
    "--include-directories", "-X", "--exclude-directories", "--header", "--post-data",
    "--post-file", "--body-data", "--body-file", "--method", "--user", "--password",
    "--http-user", "--http-password", "--referer", "--load-cookies", "--save-cookies",
    "--ca-certificate", "--certificate", "--private-key", "--limit-rate", "--bind-address",
})
WGET_SHORT_VALUE = frozenset("OoatTwUPQlARDIXieB")
HTTPIE_ELSEWHERE = frozenset({"--proxy"})
HTTPIE_VALUE = frozenset({"-a", "--auth", "-A", "--auth-type", "--session", "--session-read-only",
                          "-o", "--output", "--verify", "--cert", "--cert-key", "-s", "--style",
                          "-p", "--print", "--pretty", "--format-options", "--timeout",
                          "--max-redirects", "--boundary", "--ssl", "--ciphers",
                          "--response-charset", "--response-mime", "--default-scheme"})
_PROXY_ASSIGNMENT = re.compile(r"(?i)(?:^|[^A-Za-z0-9_])[A-Za-z_]*proxy=")


def _client_targets(words: list[str]) -> list[str] | None:
    """The target words of one curl/wget/httpie command, or None when the request may go
    somewhere they do not name."""
    program = words[0].rsplit("/", 1)[-1]
    if program in HTTPIE_PROGRAMS:
        elsewhere, value, short_value = HTTPIE_ELSEWHERE, HTTPIE_VALUE, frozenset("aAosp")
    elif program == "wget":
        elsewhere, value, short_value = WGET_ELSEWHERE, WGET_VALUE, WGET_SHORT_VALUE
    else:
        elsewhere, value, short_value = CURL_ELSEWHERE, CURL_VALUE, CURL_SHORT_VALUE
    targets, i = [], 1
    while i < len(words):
        word = words[i]
        name = word.split("=", 1)[0] if word.startswith("--") else word
        if name == "--url":
            targets.append(word.split("=", 1)[1] if "=" in word else " ".join(words[i + 1:i + 2]))
            i += 1 if "=" in word else 2
        elif word.startswith("--"):
            # wget and httpie take an unambiguous prefix of a long option (--input=urls.txt).
            if len(name) > 2 and any(option.startswith(name) for option in elsewhere):
                return None
            i += 2 if word in value else 1
        elif word.startswith("-") and len(word) > 1:
            # A cluster: flags, then at most one option taking the rest (or the next word).
            letters = word[1:]
            taking = next((k for k, c in enumerate(letters) if c in short_value), None)
            if taking is not None and "-" + letters[taking] in elsewhere:
                return None
            i += 2 if taking == len(letters) - 1 else 1
        else:
            targets.append(word)
            i += 1
    if program in HTTPIE_PROGRAMS:                      # httpie: [METHOD] URL [request items]
        if len(targets) > 1 and targets[0].isupper() and targets[0].isalpha():
            targets = targets[1:]
        targets = targets[:1]
    return targets or None


def url_targets(command: str, clients: frozenset, depth: int = 0) -> list[str] | None:
    """Every target word a URL client in the command is aimed at, as the shell passes it
    (quotes and escapes resolved), runners followed as upload_paths follows them. None when a
    target is unknown: no client found, a proxy or redirecting option or variable, xargs
    feeding the client, a client with no target, or runners nested too deep."""
    if depth > MAX_RUNNER_DEPTH or _PROXY_ASSIGNMENT.search(command):
        return None
    targets: list[str] = []
    for body in shell_heredocs(command):
        inner = url_targets(body, clients, depth + 1)
        if inner is None:
            return None
        targets += inner
    for _, raw in simple_commands(command):
        words, runners = _program(raw)
        fed = runners.fed
        if not words:
            continue
        scripts = inner_scripts(words)
        for script, _fills in scripts:
            inner = url_targets(script, clients, depth + 1)
            if inner is None:
                return None
            targets += inner
        if words[0].rsplit("/", 1)[-1] not in clients:
            # A client word under a runner this reader does not know (stdbuf -o0 curl ...).
            if not scripts and any(word.rsplit("/", 1)[-1] in clients for word in words[1:]):
                return None
            continue
        found = _client_targets(words)
        if fed is not None or found is None:
            return None
        targets += found
    return targets or None


def _excluded(relative: str, excludes: list[str]) -> bool:
    parts = relative.split(os.sep)
    for pattern in excludes:
        pattern = pattern.strip("/")
        if fnmatch.fnmatch(relative, pattern) or any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def _read_text(path: Path, shown: str) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise Unreadable(shown, "larger than the scan limit")
        data = path.read_bytes()
    except OSError as error:
        raise Unreadable(shown, type(error).__name__) from None
    if b"\x00" in data[:8192]:
        return ""                                    # binary: the detector reads text
    return data.decode("utf-8", errors="ignore")


def _tree_texts(root: Path, shown: str, excludes: list[str]) -> list[str]:
    texts, files, total = [], 0, 0
    for directory, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not _excluded(os.path.relpath(os.path.join(directory, d), root), excludes)]
        for name in names:
            full = Path(directory, name)
            if _excluded(os.path.relpath(full, root), excludes) or not full.is_file():
                continue
            files += 1
            try:
                total += full.stat().st_size
            except OSError:
                raise Unreadable(f"{shown}/{os.path.relpath(full, root)}", "unreadable") from None
            if files > MAX_TREE_FILES or total > MAX_TREE_BYTES:
                raise Unreadable(shown, "directory larger than the scan budget")
            texts.append(_read_text(full, str(full)))
    return texts


def file_texts(path: str, cwd: str, excludes: list[str]) -> list[str]:
    """The text of every file behind one uploaded path (a file, a glob or a directory)."""
    if any(marker in path for marker in ("$", "`")):
        raise Unreadable(path, "built at run time")
    expanded = os.path.expanduser(path)
    base = expanded if os.path.isabs(expanded) else os.path.join(cwd, expanded)
    if any(c in expanded for c in "*?["):
        matches = sorted(glob.glob(base))
        if not matches:
            raise Unreadable(path, "no file matches")
    else:
        matches = [base]
    texts: list[str] = []
    for match in matches:
        full = Path(match)
        if full.is_dir():
            texts += _tree_texts(full, path, excludes)
        elif full.is_file():
            texts.append(_read_text(full, path))
        else:
            raise Unreadable(path, "no such file")
    return texts
