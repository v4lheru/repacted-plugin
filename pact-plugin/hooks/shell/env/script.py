"""
Location: pact-plugin/hooks/shell/env/script.py
Summary: env-get normalisation and the script reader: expansions, substitutions, heredocs,
         command steps and simple commands of a script body.
Used by: hooks/env_guard.py (the entry hook and façade) and the other shell.env
         modules per the imports below. Carved from env_guard.py at M2; every
         decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
Map: json_literal, _QUOTED_ENV_GET, _ASSIGNMENT, _env_get_variables, normalize_env_get, SHELLS, _BODY_RUNNERS, _INTERPRETER, _SCRIPT_OPTIONS, _STRING_LITERAL, _SCRIPT_PRINTS, _SCRIPT_RUNS, _SUBSTITUTION, _SUB_MARK, _SUB_KEY, _BODY, _BODY_KEY, _HEREDOC_OPERATOR, _State, _read, _script_prints_file, _STDIN_PRINTERS, _FD_COPY, _STDOUT_TO_FILE, _STDOUT_SHOWN, _heredoc_expansions, _sub_program, _pull_substitutions, _REDIRECT_IN, _REDIRECTS, _OPERATOR_PART, _REDIRECT_FD, _Step, _command_steps, _simple_commands, _LEADING_ASSIGNMENT, _RESPELLED, _VARIABLE_USE, _WORD_SAFE, _DYNAMIC_SETTERS, _computed, _sets_variables_unseen, _expand_line, _spell_rounds, _expand_once, _literal_rounds, expand_literal_variables, LOOP_WORDS, _LOOP_WORD, _OPERATORS, _KEEPS_COMMAND, _OPENS, _CLOSES, HOOK_PATHS, _loops, _loop_header, loop_bindings, bind_names, _IGNORE_WRITE, _UNSEEN_IGNORE.
"""

from __future__ import annotations

import json
import os
import re

from shell.words import HEREDOC_MARK
from shell.words import read_script
from shell.words import segments
from shell.words import strip_prefix
from shell.words import substitution_spans
from shell.words import token_spans as shell_token_spans
from shell.words import tokens as shell_tokens
from shell.inline_code import INTERPRETER as _INTERPRETER, SCRIPT_OPTIONS as _SCRIPT_OPTIONS  # noqa: F401  # re-export
from shell.env.names import (token_protected)
from shell.env.programs import (NAME_CONSUMERS, READERS)

# A quoted word in command position whose last path component is env-get
# ("/a b/bin/env-get"). Only at the start of a simple command, optionally after runners
# that execute their argument unchanged: inside `bash -c "..."` the quoted text is a
# script, and replacing it would hide what it runs.
_QUOTED_ENV_GET = re.compile(
    r"""(^|[;&|(\n])(\s*(?:(?:rtk|command|exec|time|nice(?:\s+-n\s*-?\d+)?)\s+)*)"""
    r"""(["'])[^"'\n;&|<>()$`\\]*/env-get\3""")
_ASSIGNMENT = re.compile(r"""(?<![\w$])([A-Za-z_]\w*)(\+?=)("[^"]*"|'[^']*'|[^\s;&|]*)""")


def _env_get_variables(command: str) -> set[str]:
    """Names this command assigns only to an env-get path, and never sets any other way
    (a reassignment, +=, or a bare mention such as `read NAME` disqualifies the name)."""
    values: dict[str, list[str]] = {}
    for match in _ASSIGNMENT.finditer(command):
        name, op, value = match.groups()
        values.setdefault(name, []).append("" if op == "+=" else value.strip("\"'"))
    names = set()
    for name, assigned in values.items():
        if not all(v.rsplit("/", 1)[-1] == "env-get" for v in assigned):
            continue
        uses = re.findall(rf"(?<![\w])(\$\{{{name}\}}|\${name}\b|{name}\+?=|\b{name}\b)", command)
        if all(u.startswith("$") or u.endswith("=") for u in uses):
            names.add(name)
    return names


def normalize_env_get(command: str) -> str:
    """Spell env-get reached by a quoted path or an env-get-only variable as bare
    `env-get`, so the quote-deleting tokenizer sees the program it is."""
    command = _QUOTED_ENV_GET.sub(r"\1\2env-get", command)
    for name in _env_get_variables(command):
        command = re.sub(rf"""(["']?)\$(?:\{{{name}\}}|{name}\b)\1""", "env-get", command)
        command = re.sub(rf"""(?<![\w$]){name}=("[^"]*"|'[^']*'|[^\s;&|]*)""",
                         f"{name}=env-get", command)
    return command


# ── Bash: which commands print a protected file ───────────────────────────────────────
# Design rule: block only what would put a protected file's CONTENTS
# on stdout or stderr, or send the file off the machine; a path mentioned as data (a quoted
# argument, an option value, an assignment) and every other program pass.

# Heredocs. shell_words.read_script takes every body out of the command before anything
# else reads it, so a body is never read as commands by accident and never hides the lines
# after it. A quoted delimiter ('EOF', "EOF", \EOF) makes the body literal text; a bare one
# expands $(...) in it. Not a here-string (<<<).
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish"})
# Words on a heredoc's operator line that run its body as commands or read names from it.
_BODY_RUNNERS = SHELLS | {"eval", "source", ".", "ssh"} | NAME_CONSUMERS
_STRING_LITERAL = re.compile(r"""(['"])(.*?)\1""")
# An exit or an error carries its message to stderr: raise SystemExit(text), die, throw.
_SCRIPT_PRINTS = re.compile(r"\b(?:print|printf|puts|echo|say|pp|p|pprint|console\.\w+|"
                            r"sys\.std(?:out|err)|write|dumps?|display|cat|exit|SystemExit|"
                            r"raise|throw|die|warn|abort|fail|assert|readfile|fpassthru)\b")
# A script that hands a string to a shell or another program: what that prints is shown too.
_SCRIPT_RUNS = re.compile(r"\b(?:system2?|popen\w*|subprocess|spawn\w*|exec\w*|shell_exec|"
                          r"passthru|proc_open|get(?:status)?output|execute|qx|Open3|"
                          r"child_process)\b|do shell script")
_SUBSTITUTION = "__repact_sub_{}__"
_SUB_MARK = _SUBSTITUTION.split("{")[0]
_SUB_KEY = re.compile(_SUB_MARK + r"\d+__")
_BODY = "__repact_body_{}__"
_BODY_KEY = re.compile(r"__repact_body_\d+__")
_HEREDOC_OPERATOR = re.compile("<<" + re.escape(HEREDOC_MARK).replace(r"\{\}", r"(\d+)"))


class _State:
    """What one Bash command has done so far: values it loaded from a protected file
    (sourced, or read into named variables), and, shared with every state forked from it,
    the substitutions and heredoc bodies taken out of its text and the verdicts already
    reached on them."""
    def __init__(self, parent: "_State | None" = None):
        self.subs: dict[str, str] = parent.subs if parent else {}
        self.pulled: dict[str, str] = parent.pulled if parent else {}
        self.bodies: dict[str, tuple[bool, str]] = parent.bodies if parent else {}
        self.verdicts: dict[tuple, str | None] = parent.verdicts if parent else {}
        # How the script being judged spells its variables inside a bare heredoc body.
        self.spell = parent.spell if parent else (lambda body: body)
        self.env_get_written = parent.env_get_written if parent else False
        self.captured = False
        self.sourced = False                            # loaded keys unknown: any $VARIABLE
        self.loaded: set[str] = set()
        self.loaded_from = ""                           # the file named in the deny
        self.loop_read = False
        self.traced = False                             # set -x / -v: a load prints its lines

    @classmethod
    def any_variable(cls) -> "_State":
        state = cls()
        state.sourced = True
        return state


def _read(text: str, state: _State) -> str:
    """`text` as read_script reads it, its heredoc bodies kept in state.bodies under the
    keys that stand for them in the text returned. Raises shell_words.Unreadable."""
    flat, bodies = read_script(text)
    if not bodies:
        return flat
    keys = []
    for body in bodies:
        keys.append(_BODY.format(len(state.bodies)))
        state.bodies[keys[-1]] = body
    # Only the reader's own operators: typed text that looks like a mark has no `<<` it wrote.
    return _HEREDOC_OPERATOR.sub(
        lambda found: "<<" + keys[int(found.group(1))] if int(found.group(1)) < len(keys)
        else found.group(0), flat)


def _script_prints_file(script: str, cwd: str | None, prints: bool = False) -> str | None:
    """The protected path a script's string literal names, when the script can print or
    can run a command (os.system("head .env")). A glob counts only in a script that runs
    commands: there a shell may expand it, where glob.glob('.*') lists names."""
    runs = bool(_SCRIPT_RUNS.search(script))
    if not (prints or runs or _SCRIPT_PRINTS.search(script)):
        return None
    for literal in _STRING_LITERAL.finditer(script):
        for piece in literal.group(2).split():
            if (runs or not any(char in piece for char in "*?[")) and token_protected(piece, cwd):
                return piece
    return None


# Programs that print their stdin: a heredoc handed to one reaches the transcript.
_STDIN_PRINTERS = READERS | {"tee"}
_FD_COPY = re.compile(r"\d*[<>]&[\d-]+")                # 2>&1, >&2: no file is written
_STDOUT_TO_FILE = re.compile(r"(?<![\d&>])1?>>?(?![>&])")
# A redirect that still ends on the terminal: a process substitution, a /dev or /proc stream.
_STDOUT_SHOWN = re.compile(r""">\s*(?:\(|["']?/(?:dev|proc)/)""")


def _heredoc_expansions(line: str, start: int, end: int, text: str) -> list[str]:
    """What a bare-delimiter heredoc body can run or reveal, as one command line: its
    substitutions, and the variables it expands when the program it feeds prints its stdin
    (a reader, tee) anywhere but into a file. `cat > f <<EOF` writes a file, so there the
    variables are only text. The rest of the body is text on stdin. `line` is the line of
    the command that holds the heredoc's operator, at line[start:end]."""
    spans = [text[a:b] for a, b, _ in substitution_spans(text, quoting=False)]
    head = re.split(r"[;&|()]", _FD_COPY.sub(" ", line[:start]))[-1]
    tail = re.split(r"([;&|()])", _FD_COPY.sub(" ", line[end:]), maxsplit=1)
    program = (strip_prefix(head.split())[:1] or [""])[0].rsplit("/", 1)[-1]
    piped = len(tail) > 1 and tail[1] == "|" and not tail[2].startswith("|")
    to_file = bool(_STDOUT_TO_FILE.search(head + " " + tail[0]))
    if _STDOUT_SHOWN.search(line) or program in _STDIN_PRINTERS and (piped or not to_file):
        return ["echo " + " ".join(spans + re.findall(r"\$\{?[A-Za-z_]\w*\}?", text))]
    return [": " + " ".join(spans)] if spans else []


def _sub_program(word: str, state: _State) -> str | None:
    """The program a program word made of one substitution names, when its command only
    spells a name: $(echo grep), $(which python3), $(command -v rg). None otherwise."""
    keys = _SUB_KEY.findall(word)
    if len(keys) != 1 or keys[0] != word or keys[0] not in state.subs:
        return None
    steps = _command_steps(state.subs[keys[0]])[0]
    words = strip_prefix(steps[0].words) if len(steps) == 1 else []
    if words[:1] in (["echo"], ["printf"], ["which"]) and len(words) == 2 \
            or words[:2] in (["command", "-v"], ["type", "-P"]) and len(words) == 3:
        name = words[-1].rsplit("/", 1)[-1]
        return name if re.fullmatch(r"[\w.+-]+", name) else None
    return None


def _pull_substitutions(command: str, state: _State) -> str:
    """Replace each $(...) and `...` outside single quotes with a placeholder word, keeping
    its text in state.subs, so `cat $(ls .env)` keeps the substitution beside its reader.
    The same text always gets the same placeholders, however often it is judged."""
    if command not in state.pulled:
        out, last = [], 0
        for start, end, body in substitution_spans(command):
            key = _SUBSTITUTION.format(len(state.subs))
            state.subs[key] = body
            out += [command[last:start], key]
            last = end
        state.pulled[command] = "".join(out) + command[last:]
    return state.pulled[command]


_REDIRECT_IN = frozenset({"<", "0<"})
_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", ">&", "<>", "<<", "<<<", "<&"}) | _REDIRECT_IN
# One operator of a run of them; `<(` and `>(` open a process substitution, not a redirect.
_OPERATOR_PART = re.compile(r"[<>]\(|&>>?|>>|>\||>&|<<<|<<|<>|<&|[\s\S]")
# The file-descriptor number a redirect starts with (`2>&1`, `2>/dev/null`) is not a word.
_REDIRECT_FD = re.compile(r"(?:^|(?<=[\s;&|(]))\d+(?=[<>])")


class _Step:
    """One simple command: its words, the files on its stdin, the operators before and
    after it, the keys of the heredocs fed to it, the text of its here-strings, the targets of
    its other redirects. A redirect that stands alone (`> f`, `(..) <<EOF`) is a step with no
    words."""
    __slots__ = ("words", "stdin", "before", "after", "bodies", "strings", "redirects")

    def __init__(self, words: list[str], before: str = ""):
        self.words, self.stdin, self.before, self.after = words, [], before, ""
        self.bodies: list[str] = []
        self.strings: list[str] = []
        self.redirects: list[str] = []


def _command_steps(command: str) -> tuple[list[_Step], bool]:
    """(one _Step per simple command, exact). `command` has been through read_script: no
    comment is left in it and each heredoc is a `<<` and a key. Quoting is honoured (a
    commit message or a --text value stays one word); commands split at shell operators and
    newlines, which are kept as the text between two commands. A redirect's target is not a
    word. `exact` is False when the command could not be tokenized and the quote-deleting
    segments() stood in: its words may be cut at a space inside a quoted path."""
    command = _REDIRECT_FD.sub("", command)
    found = shell_tokens(command, "();<>|&\n")      # read_script took the comments out
    if found is None:
        return [_Step(words) for words in segments(command)], False
    steps: list[_Step] = []
    current, expect, pending = None, "", ""
    for token in found:
        if token and all(char in "();<>|&\n" for char in token):
            for part in _OPERATOR_PART.findall(token):
                if part in _REDIRECTS:
                    expect = part
                    continue
                if current is not None:
                    steps.append(current)
                current, expect = None, ""
                pending += part
            continue
        if current is None:
            if steps:
                steps[-1].after = pending
            current, pending = _Step([], pending), ""
        if expect in _REDIRECT_IN:
            current.stdin.append(token)
        elif expect == "<<" and _BODY_KEY.fullmatch(token):   # not the shift in $((1<<4))
            current.bodies.append(token)
        elif expect == "<<<":
            current.strings.append(token)
        elif expect:
            current.redirects.append(token)
        else:
            current.words.append(token)
        expect = ""
    if current is not None:
        steps.append(current)
    elif steps:
        steps[-1].after = pending
    return steps, True


def _simple_commands(command: str) -> list[tuple[list[str], list[str]]]:
    """(words, stdin files) per simple command: _command_steps without the operators."""
    return [(step.words, step.stdin) for step in _command_steps(command)[0]]


# `export W=literal` sets W in this shell as `W=literal` does.
_LEADING_ASSIGNMENT = re.compile(
    r"""\s*(?:export[ \t]+)?([A-Za-z_]\w*)=(?:"([^"'$`\\\n]+)"|'([^"'$`\\\n]+)'|([\w./+:@%,-]+))[ \t]*(?:;|&&|\n)""")
# The name written again where the shell could set it: a bare word (W=..., read W, unset W,
# for W in, ((W++))) or a ${W...} form other than the plain ${W}.
_RESPELLED = r"""(?:^|[\s;&|(){{}}!"']){name}(?:$|[\s;&|)=+\-"'\[])|\$\{{{name}[^}}]"""
_VARIABLE_USE = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*)(?![\w\[:]))")
_WORD_SAFE = re.compile(r"[\w./+:@%,-]+")
# Programs that can set a variable whose name the command never spells.
_DYNAMIC_SETTERS = frozenset({"eval", "source", ".", "unset", "declare", "typeset", "local",
                              "readonly", "getopts", "mapfile", "readarray", "let"})


def _computed(word: str) -> bool:
    return "$" in word or "`" in word or _SUB_MARK in word


def _sets_variables_unseen(command: str) -> bool:
    """True when a simple command of `command` can set a variable by a name it computes
    (eval, source, declare -n, printf -v, a changed IFS, `read "$x"`, `export "$x=.."`), or
    the command cannot be read."""
    steps, exact = _command_steps(_pull_substitutions(command, _State()))
    for step in steps:
        words = step.words
        bare = words[1:] if words[:1] == ["{"] else words
        rest = strip_prefix(bare)
        program = rest[0].rsplit("/", 1)[-1] if rest else ""
        if program in _DYNAMIC_SETTERS or "eval" in bare[:len(bare) - len(rest)] \
                or program == "printf" and "-v" in rest \
                or program == "read" and any(_computed(word) for word in rest[1:]) \
                or program == "export" and any(_computed(word.split("=", 1)[0])
                                               for word in rest[1:]) \
                or any(word.startswith("IFS=") for word in words):
            return True
    return not exact


def _expand_line(line: str, names: dict[str, str], quote: str,
                 body: bool = False) -> tuple[str, str]:
    """(`line` with each known $NAME spelled out, the quote state it ends in). In a heredoc
    body quote characters are text and nothing is word-split."""
    out, i = [], 0
    while i < len(line):
        char = line[i]
        if char == "\\" and quote != "'":
            out.append(line[i:i + 2])
            i += 2
            continue
        if not body and char in "'\"" and quote in ("", char):
            quote = "" if quote else char
        elif char == "$" and quote != "'":
            use = _VARIABLE_USE.match(line, i)
            name = use and (use.group(1) or use.group(2))
            if name in names and (quote or body or _WORD_SAFE.fullmatch(names[name])):
                out.append(names[name])
                i = use.end()
                continue
        out.append(char)
        i += 1
    return "".join(out), quote


def _spell_rounds(body: str, rounds: list[dict[str, str]]) -> str:
    """A bare-delimiter heredoc body with the names of each round spelled out, as the
    shell expands them before the body reaches its program. Nothing in it is word-split."""
    for names in rounds:
        body = "\n".join(_expand_line(line, names, "", True)[0] for line in body.split("\n"))
    return body


def _expand_once(command: str) -> tuple[str, dict[str, str]]:
    """(`command` with the names its opening assignments set spelled out, those names)."""
    values: dict[str, str | None] = {}
    position = 0
    while True:
        match = _LEADING_ASSIGNMENT.match(command, position)
        if not match:
            break
        name = match.group(1)
        value = next(group for group in match.groups()[1:] if group is not None)
        values[name] = None if name in values else value
        position = match.end()
    rest = command[position:]
    names = {name: value for name, value in values.items() if value is not None
             and not re.search(_RESPELLED.format(name=name), rest)}
    if not names or _sets_variables_unseen(command):
        return command, {}
    out, quote = [], ""
    for line in rest.split("\n"):
        expanded, quote = _expand_line(line, names, quote)
        out.append(expanded)
    return command[:position] + "\n".join(out), names


def _literal_rounds(command: str) -> tuple[str, list[dict[str, str]]]:
    """(`command` expanded to a fixed point, the names spelled in each round): the rounds
    are applied again to each bare heredoc body, which is kept apart from the command."""
    rounds = []
    for _ in range(4):
        expanded, names = _expand_once(command)
        if not names:
            break
        rounds.append(names)
        if expanded == command:
            break
        command = expanded
    return command, rounds


def expand_literal_variables(command: str) -> str:
    """Spell $W, ${W} and "$W" as W's value when the command OPENS with `W=literal;` or
    `export W=literal;` (or `&&`, or a newline): an assignment that nothing precedes is unconditional and top-level.
    Left unexpanded, so the word stays unknown to the directory rules: a name the command
    spells again as a bare word (reassigned, unset, read, a loop variable); a value holding
    a `$`, backtick, quote or backslash; an unquoted use of a value with whitespace or a glob
    character (the shells split it differently); `$W[..]` and `$W:x` (zsh subscripts and
    modifiers); any command with eval, source, IFS or another dynamic setter. Runs to a fixed
    point, so `A=/x; B="$A/y"; cd "$B"` resolves. `command` has been through read_script."""
    return _literal_rounds(command)[0]


# ── loop words and the hook's own paths ──────────────────────────────
# `for W in a b; do BODY; done` over plain words runs BODY once per word, so the command is
# judged once per binding with $W spelled as the word inside BODY only. Up to LOOP_WORDS
# bindings in all (one loop of 16 words, or two nested loops of 4 and 4).
LOOP_WORDS = 16
_LOOP_WORD = re.compile(r"[A-Za-z0-9_./+:@%,-]+")      # ASCII only: no lookalike is bound
_OPERATORS = "();<>|&\n"
_KEEPS_COMMAND = frozenset({"do", "then", "else", "elif", "{", "!", "time"})
_OPENS = frozenset({"for", "while", "until", "select", "if", "case"})
_CLOSES = frozenset({"done", "fi", "esac"})
# Paths the hook's environment holds and the Bash tool's shell shares ($PWD moves with cd:
# the search-root rule spells it per directory).
HOOK_PATHS = ("HOME", "CLAUDE_PROJECT_DIR", "CLAUDE_CONFIG_DIR")


def _loops(command: str) -> list[list]:
    """[NAME, words, body start, body end] for each `for NAME in WORDS; do ... done` whose
    `for`, `do` and `done` stand in command position, by offsets into `command`; the words
    are as typed and the end is None when no matching `done` was found."""
    spans = shell_token_spans(command, _OPERATORS) or []
    found, open_loops, at_command, i = [], [], True, 0
    while i < len(spans):
        typed = command[spans[i][0]:spans[i][1]]
        if all(char in _OPERATORS for char in typed):
            at_command, i = True, i + 1
            continue
        keyword = typed if at_command else ""
        at_command = keyword in _KEEPS_COMMAND
        if keyword in _CLOSES and open_loops:
            loop = open_loops.pop()
            if loop is not None:
                loop[3] = spans[i][0]
        elif keyword in _OPENS:
            loop, i = _loop_header(command, spans, i) if keyword == "for" else (None, i)
            if loop is not None:
                found.append(loop)
                at_command = True
            open_loops.append(loop)
        i += 1
    return found


def _loop_header(command: str, spans: list, i: int) -> tuple:
    """([NAME, words, body start, None], index of `do`) for the loop header at spans[i], or
    (None, i) when it is not `for NAME in words` ended by `;` or a newline, then `do`."""
    typed = [command[start:end] for start, end, _ in spans[i:i + LOOP_WORDS + 6]]
    if len(typed) < 4 or not re.fullmatch(r"[A-Za-z_]\w*", typed[1]) or typed[2] != "in":
        return None, i
    end = next((k for k in range(3, len(typed)) if all(c in _OPERATORS for c in typed[k])), 0)
    do = end
    while 0 < do < len(typed) and typed[do] and not typed[do].strip(";\n"):
        do += 1                                     # `;`, newlines, or both, then `do`
    if not end or do == end or do >= len(typed) or typed[do] != "do":
        return None, i
    return [typed[1], typed[3:end], spans[i + do][1], None], i + do


def loop_bindings(command: str) -> list[list[tuple[str, str]]]:
    """Each way the loops of `command` bind their names, as [(NAME, word), ...]: [[]] when
    none is bound. A loop is bound only when every word is plain ASCII path text, its
    `done` was found, and the command spells NAME nowhere else (assigned, read, a second
    loop, ${NAME:-x}) and sets no name unseen (eval, source, declare, IFS)."""
    if "for" not in command or _sets_variables_unseen(command):
        return [[]]
    usable = [(name, words) for name, words, _, end in _loops(command)
              if end is not None and words and all(_LOOP_WORD.fullmatch(w) for w in words)
              and len(re.findall(_RESPELLED.format(name=re.escape(name)), command)) == 1]
    if not usable:
        return [[]]
    bindings = [[]]
    for name, words in usable:
        bindings = [binding + [(name, word)] for binding in bindings for word in words]
        if len(bindings) > LOOP_WORDS:
            return [[]]
    return bindings


def bind_names(command: str, binding: list[tuple[str, str]]) -> str:
    """`command` with each loop's $NAME spelled as its bound word inside that loop's body,
    and $HOME, $CLAUDE_PROJECT_DIR and $CLAUDE_CONFIG_DIR as the hook's own values where the
    command never spells the name itself (see _expand_line for the quoting kept)."""
    for name, word in binding:
        loop = next((loop for loop in _loops(command) if loop[0] == name), None)
        if loop is None or loop[3] is None:
            continue
        body = _expand_line(command[loop[2]:loop[3]], {name: word}, "")[0]
        command = command[:loop[2]] + body + command[loop[3]:]
    names = {name: os.environ[name] for name in HOOK_PATHS
             if os.path.isabs(os.environ.get(name, "")) and name in command
             and not re.search(r"[\"'\\$`\n]", os.environ[name])
             and not re.search(_RESPELLED.format(name=name), command)}
    if not names or _sets_variables_unseen(command):
        return command
    out, quote = [], ""
    for line in command.split("\n"):
        expanded, quote = _expand_line(line, names, quote)
        out.append(expanded)
    return "\n".join(out)


_IGNORE_WRITE = re.compile(r""">>?\s*["']?[^\s;&|]*(?:\.gitignore|info/exclude)""")
_UNSEEN_IGNORE = (" An ignore rule this same command writes is not seen here (the tree is "
                  "read before the command runs): write it in its own command, then stage.")


def json_literal(text: str) -> bool:
    """True when `text` is a JSON object or array: data to every program but a shell (a
    shell, eval or source fed one still runs it as a script; see _heredoc_verdict)."""
    text = text.strip()
    if not text or text[0] not in "[{":
        return False
    try:
        return isinstance(json.loads(text), (dict, list))
    except ValueError:
        return False

