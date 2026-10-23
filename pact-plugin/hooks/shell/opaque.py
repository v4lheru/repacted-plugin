"""
Location: pact-plugin/hooks/shell/opaque.py
Summary: The one rule for Bash commands whose words the guards cannot read before they run.
         opaque_reason() names the first such form: a command substitution as the program,
         a variable program the command itself sets to something other than one plain
         literal, text built at run time (decoded, transformed, expanded) piped or fed into a
         shell or interpreter, eval of such text, and xargs or parallel handing git, gh, glab
         or repact its subcommand. resolved_command() spells what the command itself fixes as
         plain text, so each guard reads the words that will run: a variable it assigns one
         literal is written as that literal, a `$(which NAME)` or `$(command -v NAME)`
         program as NAME, and a literal script it feeds a shell (echo '..' |
         sh, sh <<< '..', sh -c '"$0" ..' git) is added as a line of its own.
Used by: hooks/env_guard.py (refuses on opaque_reason, judges resolved_command),
         hooks/git_commit_check.py and hooks/ops_frame_guard.py (judge resolved_command).
         Imports only shell.words and shell.inline_code, so every guard may load it.

A name the command never assigns comes from the environment (`$PYTHON -m pytest`): it stays
allowed, a known limit, since the Bash tool's shell keeps no state between calls; so does
`${PYTHON:-python3}`, spelled as its plain default for the guards (a default of git, gh,
glab or repact stays refused). A program word with an unquoted glob (* ? [) is refused:
file names, not the typed word, pick what runs. A variable
that names only the directory part of the program (`$HOME/bin/tool`, `"$R"` with R a literal
path) leaves the program's name readable, so it passes. A file or a download run as a script
(`bash x.sh`, `cat x.sh | sh`, `curl URL | sh`) is the script-file limit every guard already
documents; only text the command builds itself is refused here.
"""

from __future__ import annotations

import re
from functools import lru_cache

from shell.inline_code import INTERPRETER, code_option
from shell.words import (Unreadable, expanding_text, is_double_quoted, is_single_quoted,
                         quoted_inner, read_script, runner_prefix, spell, substitution_spans,
                         token_spans, unquoted_text)

MAX_DEPTH = 8
# The marker env_guard's rule table (shell/env/programs.RULE_MARKS) keys env.opaque on.
RULE_MARK = "so no check can read what this command runs"
MESSAGE = ("Blocked: {form}, " + RULE_MARK + " before it runs; write the command out literally "
           "(the program and its subcommand as plain words, a script as a file or a quoted "
           "heredoc) and run it again.")
SUBSTITUTED = "the program is the output of a command substitution ($(...) or backticks)"
UNRESOLVED = ("the program is ${name}, which this command sets to something other than one "
              "plain value")
OPERATOR = "the program is a parameter expansion with an operator (${...})"
GLOB = "the program word holds a glob (* ? [), so file names pick the program"
POSITIONAL = "the program is a positional parameter that `set` or the shell fills in"
OUTER = "the program comes from an expansion the outer shell makes before `sh -c` reads it"
DECODED = "it decodes text ({program}) into a shell or interpreter"
BUILT = "it feeds text {program} builds at run time into a shell or interpreter"
EVAL = "eval runs text built at run time ($VAR or $(...))"
FED_WORDS = "{runner} adds {program}'s subcommand from its input"

SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
LOADERS = frozenset({"source", "."})
ARG_FEEDERS = frozenset({"xargs", "parallel"})
# Programs whose subcommand decides what a guard checks (commit, push, accept).
SUBCOMMAND_PROGRAMS = frozenset({"git", "gh", "glab", "repact", "_repact_ops.py"})
_GLOBAL_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                                   "--config-env", "-R", "--repo", "--session-dir", "--team"})
PRINTERS = frozenset({"echo", "printf", "print"})
# Programs that pass their stdin through when given no file operand.
PASS_THROUGH = frozenset({"cat", "tee", "head", "tail", "less", "more"})
FETCHERS = frozenset({"curl", "wget", "fetch"})
DECODERS = frozenset({"uudecode", "gunzip", "zcat", "gzcat", "bunzip2", "unxz", "unzstd",
                      "lzcat", "xzcat"})
_DECODE_FLAGS = {"base64": ("-d", "-D", "--decode"), "base32": ("-d", "--decode"),
                 "xxd": ("-r", "-revert"), "openssl": ("-d", "-base64", "base64"),
                 "gzip": ("-d", "-dc", "-cd", "--decompress"), "bzip2": ("-d", "--decompress"),
                 "xz": ("-d", "--decompress"), "zstd": ("-d", "--decompress"),
                 "basenc": ("-d", "--decode")}
DECLARERS = frozenset({"export", "declare", "typeset", "local", "readonly"})
SETTERS = frozenset({"read", "mapfile", "readarray", "getopts", "unset"})

_OPERATORS = "();<>|&\n"
_KEY = "__repact_opaque_{}__"
_KEY_MARK = re.compile(r"__repact_opaque_\d+__")
_UNKNOWN = "__repact_unknown__"
_HEREDOC = re.compile(r"__repact_heredoc_(\d+)__")
_EXPANSION = re.compile(r"\$(?:\{([^}]*)\}|([A-Za-z_]\w*)|([0-9@*#?$!-]))")
_PLAIN_NAME = re.compile(r"[A-Za-z_]\w*")
_SAFE_VALUE = re.compile(r"[^$`'\"\\\n;|&<>(){}]+")
# ${NAME:-word} / ${NAME-word} with a plain word: NAME's value when the command sets it to
# one literal, else (from the environment, as a bare $NAME) the word, spelled for the guards.
_DEFAULTED = re.compile(r"\$\{([A-Za-z_]\w*):?-([A-Za-z0-9_./+@%,:=~-]+)\}")
_GLOB_CHARS = frozenset("*?[")
_TEST_WORDS = frozenset({"[", "[["})
_REDIRECT_FD = re.compile(r"(?:^|(?<=[\s;&|(]))\d+(?=[<>])")
_SET = "#set"                       # the assignments key that records a `set` with operands

# Setup commands whose output the tool itself writes: `eval "$(X)"`, `source <(X)` and
# `. <(X)` are allowed when X is one of these, typed as plain words (no expansion, quote,
# redirect from a file or runner word). {program: [(leading words, what may follow)]}; what
# may follow: "" nothing, "shell" one shell name, "shell?" at most one, "options" shell
# names and plain --options. Every other text eval or a loader runs stays refused.
SHELL_NAMES = frozenset({"bash", "zsh", "fish", "sh", "ksh", "dash", "tcsh", "csh", "elvish",
                         "nu", "xonsh", "pwsh", "powershell"})
COMPLETION_TOOLS = frozenset({"kubectl", "kubeadm", "helm", "gh", "glab", "docker", "npm", "pnpm",
                              "rustup", "poetry", "minikube", "kind", "k3d", "kustomize", "flux",
                              "argocd", "stern", "hugo", "railway", "supabase", "deno"})
SETUP_FORMS = {
    "ssh-agent": [(("-s",), ""), (("-c",), "")],
    "brew": [(("shellenv",), "shell?")],
    "pyenv": [(("init", "-"), "shell?")],
    "rbenv": [(("init", "-"), "shell?")],
    "direnv": [(("export",), "shell"), (("hook",), "shell")],
    "fnm": [(("env",), "options")],
    "mise": [(("activate",), "options")],
    "conda": [((f"shell.{shell}", "hook"), "") for shell in sorted(SHELL_NAMES)],
    "starship": [(("init",), "options")],
    "zoxide": [(("init",), "options")],
    "opam": [(("env",), "options")],
    **{tool: [(("completion",), "shell")] for tool in COMPLETION_TOOLS},
}
_SETUP_OPTION = re.compile(r"--[a-z][a-z-]*(?:=[a-z0-9-]+)?")
_PLAIN_PROGRAM = re.compile(r"[A-Za-z0-9_./+-]+")
# `$(which NAME)` / `$(command -v NAME)` as the program prints where the program NAME is: the
# program stays readable, so it passes and is spelled as NAME for the guards to read.
_LOOKUP = re.compile(r"\s*(?:which|command\s+-v)\s+([A-Za-z0-9._-]+)\s*")


class _Command:
    """One simple command: its words as typed and as read, the operator before it, its
    here-strings (as typed), heredoc indexes, `<` files, and whether a `<(...)` follows it."""
    __slots__ = ("typed", "words", "before", "strings", "said", "bodies", "files", "procsub")

    def __init__(self, before: str):
        self.typed: list[str] = []
        self.words: list[str] = []
        self.before = before
        self.strings: list[str] = []
        self.said: list[str] = []
        self.bodies: list[int] = []
        self.files: list[str] = []
        self.procsub = False


def _parse(text: str) -> tuple[list[_Command], dict, list]:
    """(simple commands, {placeholder: substitution text}, heredoc bodies) of `text`, with
    each outermost substitution replaced by a placeholder word. Raises Unreadable."""
    flat, bodies = read_script(text)
    subs, out, last = {}, [], 0
    for start, end, inner in substitution_spans(flat):
        key = _KEY.format(len(subs))
        subs[key] = inner
        out += [flat[last:start], key]
        last = end
    flat = _REDIRECT_FD.sub("", "".join(out) + flat[last:])
    spans = token_spans(flat, _OPERATORS)
    if spans is None:
        raise Unreadable("a quote left open", "close it")
    commands, current, pending = [], _Command(""), None
    for start, end, word in spans:
        typed = flat[start:end]
        if typed and all(char in _OPERATORS for char in typed):
            if "(" in typed and ("<" in typed or ">" in typed):   # <(..): runs apart, feeds it
                current.procsub = current.procsub or "<" in typed
                commands.append(current)
                current = _Command(typed)
            elif "<" in typed or ">" in typed:
                pending = typed
            else:
                commands.append(current)
                current = _Command(typed)
            continue
        if pending is not None:
            if pending.endswith("<<<"):
                current.strings.append(typed)
                current.said.append(word)
            elif pending.endswith("<<") and _HEREDOC.fullmatch(word):
                current.bodies.append(int(_HEREDOC.fullmatch(word).group(1)))
            elif pending.endswith("<") and not pending.endswith("<&"):
                current.files.append(word)
            pending = None
            continue
        current.typed.append(typed)
        current.words.append(word)
    commands.append(current)
    return commands, subs, bodies


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _assignment(typed: str, word: str) -> tuple[str, str | tuple | None] | None:
    """(name, literal value or None) for an assignment word, else None; a value typed as
    ${REF:-word} is (REF, word), which _values settles once every assignment is known."""
    name, equals, value = word.partition("=")
    if not equals:
        return None
    append = name.endswith("+")
    name = name.rstrip("+")
    if not _PLAIN_NAME.fullmatch(name):
        return None
    typed_value = typed.partition("=")[2]
    bare = typed_value[1:-1] if is_double_quoted(typed_value) else typed_value
    defaulted = _DEFAULTED.fullmatch(bare)
    if defaulted and not append:
        return name, defaulted.groups()
    built = append or "$" in expanding_text(typed_value) or "`" in typed_value \
        or _KEY_MARK.search(typed_value) or not _SAFE_VALUE.fullmatch(value)
    return name, None if built else value


def _assignments(commands: list[_Command], outer: dict) -> dict:
    """{name: candidate values} for every name the commands set (None: not one literal),
    over the outer level's. Union over the whole text: order does not narrow it."""
    found = {name: set(values) for name, values in outer.items()}

    def add(name: str, value: str | None) -> None:
        found.setdefault(name, set()).add(value)
    for command in commands:
        start = runner_prefix(command.words)[0]
        for typed, word in zip(command.typed[:start], command.words[:start]):
            pair = _assignment(typed, word)
            if pair:
                add(*pair)
        words, typed = command.words[start:], command.typed[start:]
        program = words[0] if words else ""
        pair = _assignment(typed[0], program) if "+=" in program[:program.find("=") + 1] else None
        if pair:                                       # W+=x: runner_prefix reads it as a program
            add(*pair)
        if program in DECLARERS:
            for each_typed, word in zip(typed[1:], words[1:]):
                pair = _assignment(each_typed, word)
                if pair:
                    add(*pair)
        elif program in SETTERS or program in ("for", "select"):
            for word in words[1:2] if program in ("for", "select") else words[1:]:
                if _PLAIN_NAME.fullmatch(word):
                    add(word, None)
        elif program == "printf" and "-v" in words:
            index = words.index("-v")
            if index + 1 < len(words):
                add(words[index + 1], None)
        elif program == "set" and any(not word.startswith("-") or word == "--"
                                      for word in words[1:]):
            add(_SET, None)
    return found


def _is_setup(command: _Command) -> bool:
    """True when `command` is one SETUP_FORMS command typed as plain words."""
    if command.strings or command.bodies or command.files or command.procsub \
            or not command.words or runner_prefix(command.words)[0] \
            or command.typed != command.words or not _PLAIN_PROGRAM.fullmatch(command.words[0]) \
            or any("__repact_" in word for word in command.words):
        return False
    args = command.words[1:]
    for leading, tail in SETUP_FORMS.get(_base(command.words[0]), ()):
        rest = args[len(leading):]
        if tuple(args[:len(leading)]) != leading:
            continue
        if tail == "" and not rest or tail == "shell" and len(rest) == 1 \
                and rest[0] in SHELL_NAMES or tail == "shell?" and len(rest) <= 1 \
                and set(rest) <= SHELL_NAMES or tail == "options" \
                and all(word in SHELL_NAMES or _SETUP_OPTION.fullmatch(word) for word in rest):
            return True
    return False


def _values(assigned: dict) -> dict:
    """{name: value} for the names set to exactly one plain literal; ${REF:-word} counts
    as REF's literal when the command sets REF to one, else as the word."""
    def settle(value):
        if not isinstance(value, tuple):
            return value
        ref, word = value
        if ref not in assigned:
            return word
        refs = assigned[ref]
        return next(iter(refs)) if len(refs) == 1 and isinstance(next(iter(refs)), str) \
            else None
    settled = {name: {settle(value) for value in values} for name, values in assigned.items()
               if name != _SET}
    return {name: next(iter(values)) for name, values in settled.items()
            if len(values) == 1 and None not in values}


class _Level:
    """The analysis of one script level: the first opaque form, the variables spelled, and
    the extra lines (resolved literal scripts) the guards should read as well."""

    def __init__(self, text: str, depth: int, outer: dict, positional: dict | None):
        if depth > MAX_DEPTH:
            raise Unreadable("commands nested too deep to read", "flatten them")
        self.text, self.depth, self.positional = text, depth, positional
        self.commands, self.subs, self.bodies = _parse(text)
        self.assigned = _assignments(self.commands, outer)
        self.values = _values(self.assigned)
        self.values.update({key: value for key, value in (positional or {}).items()
                            if value is not None and _SAFE_VALUE.fullmatch(value)})
        self.form: str | None = None
        self.extras: list[str] = []
        self.lookups: dict[str, str] = {}               # {substitution text: the name it finds}
        for inner in self.subs.values():
            self._nested(inner, None)
        for index, command in enumerate(self.commands):
            self._command(index, command)

    # ── nested scripts ─────────────────────────────────────────────────────────────────
    def _nested(self, text: str, positional: dict | None, add: bool = False,
                checked: str | None = None) -> None:
        """Analyse a script this level runs (`checked`, when given, is the text judged for
        its form: the script with what the outer shell expands marked unknown); keep its
        form, and its resolved text when it differs from `text` (or always, with `add`)."""
        if checked is not None and checked != text:
            marked = _Level(checked, self.depth + 1, self.assigned, positional)
            self.form = self.form or marked.form
        level = _Level(text, self.depth + 1, self.assigned, positional)
        self.form = self.form or level.form
        resolved = level.resolved()
        if (add or resolved != text) and not _KEY_MARK.search(resolved):
            self.extras.append(resolved)

    def resolved(self) -> str:
        spelled = _DEFAULTED.sub(self._defaulted, spell(self.text, self.values))
        for inner, name in self.lookups.items():
            spelled = spelled.replace(f"$({inner})", name).replace(f"`{inner}`", name)
        return "\n".join([spelled, *self.extras])

    def _defaulted(self, found) -> str:
        """What ${REF:-word} runs as: REF's literal, the word when the command leaves REF
        unset, else the expansion as typed."""
        ref, word = found.groups()
        if ref not in self.assigned:
            return word
        return self.values.get(ref, found.group())

    def _set(self, form: str) -> None:
        self.form = self.form or form

    # ── one simple command ─────────────────────────────────────────────────────────────
    def _command(self, index: int, command: _Command) -> None:
        start, seen = runner_prefix(command.words)
        leading = [runner for runner, option, _ in seen if not option]
        words, typed = command.words[start:], command.typed[start:]
        if words[:1] == ["{"]:
            words, typed = words[1:], typed[1:]
        if "eval" in leading and self._eval(command):
            return                                      # eval "$(brew shellenv)": nothing more
        shell_options = [option for runner, option, _ in seen if runner in SHELLS and option]
        dash_c = any(option.startswith("-") and not option.startswith("--") and "c" in option
                     for option in shell_options)
        if dash_c and words:
            self._dash_c(words, typed, leading, seen)
        elif words:
            self._program(typed[0])
        if leading and set(leading) & ARG_FEEDERS and words:
            self._fed_words(leading, seen, words)
        if self._reads_script(command, words, leading, shell_options, dash_c):
            shell = bool(set(leading) & SHELLS) or bool(words) and _base(words[0]) in LOADERS
            self._script_input(index, command, shell)

    def _program(self, typed: str) -> None:
        base = _base(typed)
        if _KEY_MARK.search(base):
            key = _KEY_MARK.search(base).group()
            bare = quoted_inner(typed) if is_double_quoted(typed) else typed
            found = _LOOKUP.fullmatch(self.subs.get(key, "")) if bare == key else None
            if found:
                self.lookups[self.subs[key]] = found.group(1)
                return None
            return self._set(SUBSTITUTED)
        if _UNKNOWN in base:
            return self._set(OUTER)
        if typed not in _TEST_WORDS and _GLOB_CHARS & set(unquoted_text(typed)):
            return self._set(GLOB)
        for found in _EXPANSION.finditer(expanding_text(base)):
            braced, name, special = found.groups()
            if braced is not None:
                defaulted = _DEFAULTED.fullmatch("${" + braced + "}")
                if _PLAIN_NAME.fullmatch(braced):
                    name = braced
                elif braced.isdigit() or braced in ("@", "*"):
                    special = braced
                elif defaulted and _base(defaulted.group(2)) not in SUBCOMMAND_PROGRAMS:
                    name = defaulted.group(1)   # ${PYTHON:-python3}; ${GIT:-git} stays refused
                else:
                    return self._set(OPERATOR)
            if name is not None:
                if name in self.assigned and name not in self.values:
                    return self._set(UNRESOLVED.format(name=name))
            elif special is not None and (special.isdigit() or special in "@*"):
                if self.positional is not None:
                    if self.positional.get(special) is None:
                        return self._set(POSITIONAL)
                elif _SET in self.assigned:
                    return self._set(POSITIONAL)
        return None

    def _eval(self, command: _Command) -> bool:
        """Judge what eval runs; True when it runs one SETUP_FORMS substitution."""
        at = command.words.index("eval") if "eval" in command.words else -1
        body_typed = command.typed[at + 1:]
        if len(body_typed) == 1 and self._setup_sub(command.words[at + 1]):
            return True
        if any(self._built(typed) for typed in body_typed):
            self._set(EVAL)
            return False
        self._nested(" ".join(command.words[at + 1:]), self.positional)
        return False

    def _built(self, typed: str) -> bool:
        """True when a typed word holds a substitution or an expansion of a name this
        command does not set to one literal."""
        if any(not self._literal_sub(key) for key in _KEY_MARK.findall(typed)):
            return True
        for found in _EXPANSION.finditer(expanding_text(typed)):
            braced, name, special = found.groups()
            if braced is not None and _PLAIN_NAME.fullmatch(braced):
                name = braced
            if name is None or name not in self.values:
                return True
        return False

    def _sub_level(self, key: str) -> _Level | None:
        try:
            return _Level(self.subs[key], self.depth + 1, self.assigned, None)
        except (KeyError, Unreadable):
            return None

    def _setup_sub(self, word: str) -> bool:
        """True when `word` (as read) is one substitution running one SETUP_FORMS command."""
        level = self._sub_level(word) if _KEY_MARK.fullmatch(word) else None
        return level is not None and level.form is None and len(level.commands) == 1 \
            and _is_setup(level.commands[0])

    def _printed_sub(self, key: str) -> str | None:
        """What a substitution prints when it only prints literal words ($(echo ls -la)),
        else None."""
        level = self._sub_level(key)
        if level is None:
            return None
        words = [command for command in level.commands if command.words]
        if len(words) != 1:
            return None
        kind, text = level._source(level.commands.index(words[0]))
        return text if kind in ("literal", "heredoc") else None

    def _literal_sub(self, key: str) -> bool:
        """True when a substitution only prints literal words: $(echo ls -la)."""
        return self._printed_sub(key) is not None

    def _dash_c(self, words: list, typed: list, leading: list, seen: list) -> None:
        """`sh -c BODY a0 a1`: the outer shell expands BODY first; what it cannot spell
        becomes an unknown word, an opaque program wherever it stands as one."""
        body = checked = words[0]
        spelled_sub = False
        if not is_single_quoted(typed[0]):
            body = _EXPANSION.sub(lambda found: self._spelled(found) or found.group(), body)
            printed = _KEY_MARK.sub(lambda key: _or(self._printed_sub(key.group()), key.group()),
                                    body)
            body, spelled_sub = printed, printed != body
            checked = _KEY_MARK.sub(lambda key: _or(self._printed_sub(key.group()), _UNKNOWN),
                                    _EXPANSION.sub(
                                        lambda found: self._spelled(found) or _UNKNOWN, words[0]))
        replaces = {value or "{}" for runner, option, value in seen
                    if runner in ARG_FEEDERS and option in ("-I", "-i", "-J", "--replace")}
        if set(leading) & ARG_FEEDERS and any(mark in body for mark in replaces | {"{}"}):
            return self._set(FED_WORDS.format(runner="xargs", program="the shell"))
        positional = {}
        for number, (each_typed, word) in enumerate(zip(typed[1:], words[1:])):
            positional[str(number)] = None if self._built(each_typed) else word
        rest = [positional[key] for key in sorted(positional, key=int) if key != "0"]
        positional["@"] = positional["*"] = None if None in rest else " ".join(rest)
        self._nested(body, positional, add=bool(words[1:]) or spelled_sub, checked=checked)

    def _spelled(self, found) -> str | None:
        braced, name, _special = found.groups()
        name = braced if braced is not None and _PLAIN_NAME.fullmatch(braced) else name
        return self.values.get(name) if name else None

    def _fed_words(self, leading: list, seen: list, words: list) -> None:
        """xargs git, xargs -I{} git {}: the runner writes the subcommand from its input."""
        program = _base(words[0])
        if program not in SUBCOMMAND_PROGRAMS:
            return
        replaces = {value or "{}" for runner, option, value in seen
                    if runner in ARG_FEEDERS and option in ("-I", "-i", "-J", "--replace")}
        i = 1
        while i < len(words) and words[i].startswith("-"):
            i += 2 if words[i] in _GLOBAL_VALUE_OPTIONS else 1
        sub = words[i] if i < len(words) else None
        if sub is None or any(mark and mark in sub for mark in replaces):
            runner = next(runner for runner in leading if runner in ARG_FEEDERS)
            self._set(FED_WORDS.format(runner=runner, program=program))

    # ── a script read from stdin, a here-string or a <(...) ────────────────────────────
    def _reads_script(self, command: _Command, words: list, leading: list,
                      shell_options: list, dash_c: bool) -> bool:
        if dash_c:
            return False
        if set(leading) & SHELLS:
            if not words or "-s" in shell_options:
                return True
            return words[0] in ("-", "/dev/stdin", "/dev/fd/0") or command.procsub
        if not words:
            return False
        program = _base(words[0])
        if program in LOADERS:
            return command.procsub or words[1:2] in (["/dev/stdin"], ["/dev/fd/0"], ["-"])
        if INTERPRETER.fullmatch(program):
            for word in words[1:]:
                if not word.startswith("-") or word == "-":
                    return word == "-" or command.procsub and word.startswith("/dev/fd")
                if code_option(word):
                    return False
            return True
        return False

    def _script_input(self, index: int, command: _Command, shell: bool) -> None:
        """Refuse a script the command builds itself; a literal one a shell reads is read
        as part of the command (an interpreter's is its own language: not read here). A
        heredoc body is added only when spelling changes it: the guards read the body
        themselves, and a second copy would count its commit twice."""
        if command.procsub and index + 1 < len(self.commands):
            words = command.words[runner_prefix(command.words)[0]:]
            closed = index + 2 < len(self.commands) \
                and self.commands[index + 2].before.startswith(")")
            if words and _base(words[0]) in LOADERS and closed \
                    and _is_setup(self.commands[index + 1]):     # source <(brew shellenv)
                return None
            kind, text = self._source(index + 1)
        else:
            kind, text = self._stdin(index)
        if kind == "decoded":
            return self._set(DECODED.format(program=text))
        if kind == "built":
            return self._set(BUILT.format(program=text))
        if kind in ("literal", "heredoc") and text and shell:
            self._nested(text, None, add=kind == "literal")
        return None

    def _stdin(self, index: int) -> tuple[str, str | None]:
        """What steps[index] reads on stdin: (kind, text). Kinds: literal (with its text),
        heredoc (with its body), file, fetched, none; refused: decoded, built (text: the program)."""
        command = self.commands[index]
        if command.strings:
            typed = command.strings[-1]
            if self._built(typed):
                return "built", "a here-string"
            return "literal", command.said[-1]
        if command.bodies:
            body = command.bodies[-1]
            return ("heredoc", self.bodies[body][1]) if body < len(self.bodies) else ("none", None)
        if command.files:
            return "file", None
        if index and "|" in command.before and "||" not in command.before:
            return self._source(index - 1)
        return "none", None

    def _source(self, index: int) -> tuple[str, str | None]:
        """What steps[index] writes to its stdout, as _stdin reports it."""
        command = self.commands[index]
        start = runner_prefix(command.words)[0]
        words, typed = command.words[start:], command.typed[start:]
        program = _base(words[0]) if words else ""
        args = words[1:]
        if program in PRINTERS:
            if any(self._built(each) for each in typed[1:]):
                return "built", program
            return "literal", _printed(program, args)
        if program in FETCHERS:
            return "fetched", None
        if program in DECODERS or any(flag in args for flag in _DECODE_FLAGS.get(program, ())):
            return "decoded", program
        if program in PASS_THROUGH:
            if program != "tee" and any(not word.startswith("-") for word in args):
                return "file", None
            return self._stdin(index)
        return "built", program or "a command group"


def _or(value: str | None, default: str) -> str:
    return default if value is None else value


_PRINTF_SPEC = re.compile(r"%[-#0 +]*\d*(?:\.\d+)?[sbqdiouxXcfeEgG%]")


def _printed(program: str, args: list) -> str:
    """What echo or printf prints for these (literal) words, escapes filled in."""
    if program == "printf" and args:
        rest, out = list(args[1:]), []
        for _ in range(64):
            used = []
            out.append(_PRINTF_SPEC.sub(lambda spec: "%" if spec.group() == "%%" else (
                used.append(1) or (rest.pop(0) if rest else "")), args[0]))
            if not rest or not used:
                break
        text = "".join(out)
    else:
        while args[:1] and re.fullmatch(r"-[neE]+", args[0]):
            args = args[1:]
        text = " ".join(args)
    return text.replace("\\n", "\n").replace("\\t", "\t")


@lru_cache(maxsize=16)
def _analyse(command: str) -> tuple[str | None, str]:
    """(the deny reason or None, the resolved command); a command this module cannot read
    is left to the guards' own readers, which refuse what they cannot read."""
    try:
        level = _Level(command, 0, {}, None)
    except (Unreadable, RecursionError):
        return None, command
    reason = MESSAGE.format(form=level.form) if level.form else None
    return reason, level.resolved()


def opaque_reason(command: str) -> str | None:
    """Why `command` runs words no guard can read before it runs, or None."""
    return _analyse(command)[0] if isinstance(command, str) else None


def resolved_command(command: str) -> str:
    """`command` with what it fixes itself spelled out (see the module docstring); the
    command unchanged when nothing is."""
    return _analyse(command)[1] if isinstance(command, str) else command
