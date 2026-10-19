"""
Location: pact-plugin/hooks/shell/env/judge.py
Summary: command_verdict: the per-command walk (printing words, env-get, printed text,
         substitutions, loads, traces, echoes) and the stdin / heredoc verdicts.
Used by: hooks/env_guard.py (the entry hook and façade) and the other shell.env
         modules per the imports below. Carved from env_guard.py at M2; every
         decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
Map: _printing_words, _is_env_get, _PRINTF_SPEC, _ESCAPES, _unescape, _printed_text, _sub_paths, _sub_verdict, _sub_reads, _sub_names, _load, _uses_loaded, _ENV_READ, _prints_loaded, _inner_script, _SSH_VALUE_OPTIONS, _group_words, _trace_switch, _LOADED, _COMPUTED, _echoes_request, _glued_unknown, _AWK, _AWK_VALUE_OPTIONS, _AWK_GLUED_OPTION, _awk_program_word, command_verdict, _program, _STDIN_ACTORS, _PAGERS, _DATA_CONSUMERS, _TEXT_COMMANDS, _NAMES_FROM_STDIN, _GIT_NAMES_FROM_STDIN, _BODY_WORD, _BODY_WORDS_READ, _COMPOUND_END, _STDIN_AS_FILE, _reads_script_from_stdin, _script_on_stdin, _stdin_role, _body_names, _body_as_script, _heredoc_verdict, _json_actor.
"""

from __future__ import annotations

import os
import re

from shell.words import segments
from shell.words import strip_prefix
from shell.inline_code import code_option, runs_inline_code
from shell.env.git_egress import (Notice, _git_split)
from shell.env.names import (token_protected)
from shell.env.places import (_PIPE, _names_only_search, _search_skip)
from shell.env.programs import (DUMP_MESSAGE, EGRESS_PROGRAMS, FIND_ACTIONS, GREP_PROGRAMS, LOADERS, OPERAND_READERS, PRINTERS, READERS, RG_PROGRAMS, SILENT_STDIN, SUGGESTION, UNREADABLE_MESSAGE, _GIT_VALUE_OPTIONS)
from shell.env.script import (json_literal, _sub_program, SHELLS, _BODY_RUNNERS, _INTERPRETER, _SCRIPT_OPTIONS, _SCRIPT_PRINTS, _STDIN_PRINTERS, _SUB_KEY, _SUB_MARK, _State, _Step, _command_steps, _heredoc_expansions, _pull_substitutions, _read, _script_prints_file)
from shell.env.search import (_cut_lists_names_only, _file_keys, _git_verdict, _named_message, _option_values, _positionals, _recurse_verdict, _SENDS, _sent_words, dumps_environment)

def _printing_words(words: list[str], state: "_State | None" = None) -> list[str]:
    """The words of a substitution's command that can name a file whose contents it prints.
    For `env-get FILE KEY -- cmd` these are cmd's alone: env-get never prints a value, and
    what cmd prints is what the standing ruling already lets env-get print when run directly."""
    command = strip_prefix(words)
    program = command[0].rsplit("/", 1)[-1] if command else ""
    if program in GREP_PROGRAMS | RG_PROGRAMS:          # not its pattern or --exclude='.env*'
        skip = _search_skip(program, command[1:])
        return [word for word in words if word not in skip]
    if len(command) > 1 and _INTERPRETER.fullmatch(program):
        command = command[1:]                           # python3 .../env-get is env-get
    if not command or not _is_env_get(command[0], state):
        return words
    return command[command.index("--") + 1:] if "--" in command else []


def _is_env_get(word: str, state: "_State | None" = None) -> bool:
    """env-get as the shell finds it on PATH, by an absolute path, or by a relative path
    unless the same command writes a file of that name (printf .. > env-get; ./env-get .env):
    there it is whatever that command put in it."""
    return word.rsplit("/", 1)[-1] == "env-get" and (
        word == "env-get" or os.path.isabs(os.path.expanduser(word))
        or not (state and state.env_get_written))


_PRINTF_SPEC = re.compile(r"%%|%[-+ #0-9.]*[a-zA-Z]")
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\"}


def _unescape(text: str) -> str:
    return re.sub(r"\\([nt\\])", lambda found: _ESCAPES[found.group(1)], text)


def _printed_text(step: _Step, state: _State) -> str | None:
    """The text one simple command writes to stdout when it only prints what it was given:
    echo and printf of their words (printf's format filled in), a stdin printer fed a
    heredoc or a here-string. None for any other command."""
    command = strip_prefix(step.words)
    program = command[0].rsplit("/", 1)[-1] if command else ""
    args = command[1:]
    if program in ("echo", "print"):
        while args[:1] and re.fullmatch(r"-[neE]+", args[0]):
            args = args[1:]
        return _unescape(" ".join(args))
    if program == "printf" and args and args[0] != "-v":
        rest, out = list(args[1:]), []
        while True:
            used = []
            out.append(_PRINTF_SPEC.sub(lambda spec: "%" if spec.group() == "%%" else (
                used.append(1) or (rest.pop(0) if rest else "")), args[0]))
            if not rest or not used:
                return _unescape("".join(out))
    if program in _STDIN_PRINTERS and not args and (step.bodies or step.strings):
        return "\n".join([state.bodies[key][1] for key in step.bodies if key in state.bodies]
                         + step.strings)
    return None


def _sub_paths(words: list[str], state: _State, cwd: str, shown: bool = False) -> list[str]:
    """The protected paths named by the substitutions these words carry ($(ls .env)), and
    with `shown` the names they print as text ($(printf '.e%s' nv) is .env)."""
    return [w for word in words for key in _SUB_KEY.findall(word) if key in state.subs
            for step in _command_steps(state.subs[key])[0]
            for w in _printing_words(step.words, state) + step.stdin
            + ((_printed_text(step, state) or "").split() if shown else [])
            if token_protected(w, cwd)]


def _sub_verdict(key: str, cwd: str | None, captured: bool, state: _State) -> str | None:
    """The verdict on one substitution run in one directory, reached once however many
    words carry it: judged again at every level, a nest of them took 2^depth steps."""
    slot = (key, cwd, captured)
    if key not in state.subs:                           # typed text that looks like a key
        return None
    if slot not in state.verdicts:
        state.verdicts[slot] = _upper().judge(state.subs[key], cwd, _State(state), captured=captured)
    return state.verdicts[slot]


def _sub_reads(words: list[str], state: _State, cwd: str | None) -> bool:
    """True when a substitution these words carry reads a protected file's contents."""
    return any(_sub_verdict(key, cwd, False, state)
               for word in words for key in _SUB_KEY.findall(word))


def _sub_names(word: str, state: _State, cwd: str | None) -> list[str]:
    """The protected paths a substitution's output can name when that output is run as a
    command: among the words of the substitutions `word` carries, the heredocs and
    here-strings fed to them, the text they print, and the substitutions inside in turn."""
    found = []
    for key in _SUB_KEY.findall(word):
        if key not in state.subs:
            continue
        for step in _command_steps(_pull_substitutions(state.subs[key], state))[0]:
            text = [piece for body in step.bodies if body in state.bodies
                    for piece in _BODY_WORD.findall(state.bodies[body][1])]
            text += _BODY_WORD.findall(" ".join(step.strings + [
                _printed_text(step, state) or ""]))
            for each in step.words + step.stdin + text:
                found += _sub_names(each, state, cwd) if _SUB_MARK in each \
                    else [each] if token_protected(each, cwd) else []
    return found


def _load(state: _State, path: str, cwd: str) -> None:
    """Note the keys a sourced file sets; if unreadable, any later $VARIABLE counts."""
    state.loaded_from = state.loaded_from or path
    keys = _file_keys(path, cwd)
    if keys is None:
        state.sourced = True
    else:
        state.loaded |= keys


def _uses_loaded(texts: list[str], state: _State) -> bool:
    """True when a text expands a loaded name: $KEY, ${KEY}, ${KEY:0:9}, ${KEY#x}."""
    if state.sourced:
        return any("$" in text for text in texts)
    return any(re.search(rf"\$\{{?{re.escape(name)}\b", text)
               for text in texts for name in state.loaded)


_ENV_READ = re.compile(r"environ|getenv|\bENV\b|process\.env|\$_ENV|%ENV|Deno\.env")


def _prints_loaded(program: str, args: list[str], state: _State,
                   strings: list[str] = ()) -> bool:
    """True when this command shows a value loaded earlier in the same command: printed as
    an argument or a here-string, printenv NAME, or a -c/-e script that reads it and prints."""
    if not (state.loaded or state.sourced):
        return False
    if program in PRINTERS and _uses_loaded(args, state) \
            or strings and program not in SILENT_STDIN and _uses_loaded(list(strings), state):
        return True
    if program == "printenv":
        return any(arg in state.loaded or state.sourced and not arg.startswith("-")
                   for arg in args)
    if _INTERPRETER.fullmatch(program):
        for script in _option_values(args, _SCRIPT_OPTIONS):
            names = set(re.findall(r"\w+", script))
            if (names & state.loaded or state.sourced) and _ENV_READ.search(script) and (
                    _SCRIPT_PRINTS.search(script) or "-p" in args or "--print" in args):
                return True
    return False


def _inner_script(words: list[str], command: list[str]) -> str | None:
    """The script a runner prefix hands to a shell: `bash -c S`, `eval S`."""
    leading = words[:len(words) - len(command)]
    names = [word.rsplit("/", 1)[-1] for word in leading]
    if "eval" in names:                     # eval 'X=$(..)': its words, assignments included
        return " ".join(words[names.index("eval") + 1:])
    if command and any(name in SHELLS for name in names) and any(
            word.startswith("-") and not word.startswith("--") and "c" in word
            for word in leading):
        return command[0]
    return None


_SSH_VALUE_OPTIONS = frozenset({"-i", "-p", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-b", "-c",
                                "-E", "-e", "-m", "-O", "-Q", "-S", "-W", "-w", "-B", "-I"})


def _group_words(words: list[str]) -> list[str]:
    """The words of a simple command past what opens or closes a brace group or a function
    body: `{ cat .env; }`, `f() { cat .env; }` and `function f { cat .env; }` run cat."""
    if words[:1] == ["function"]:
        words = words[2:]
    while words[:1] in (["{"], ["}"]):
        words = words[1:]
    return words


def _trace_switch(command: list[str]) -> bool | None:
    """True when this command turns on the shell's trace or verbose mode (set -x, set -v,
    set -o xtrace, setopt verbose), which prints each line a later `source` runs; False when
    it turns them off; None when it does neither."""
    program = command[0].rsplit("/", 1)[-1] if command else ""
    args = [arg.casefold() for arg in command[1:]]
    if program == "setopt":
        return True if {"xtrace", "verbose"} & {arg.replace("_", "") for arg in args} else None
    if program != "set":
        return None
    switch = None
    for i, arg in enumerate(args):
        option = args[i + 1] if arg in ("-o", "+o") and i + 1 < len(args) else ""
        if re.fullmatch(r"[-+][a-z]*[xv][a-z]*", arg) or option in ("xtrace", "verbose"):
            switch = arg.startswith("-")
    return switch


_LOADED = ("Blocked: this would {what} a value the same command loaded from {path} (a .env-class "
           "file); " + SUGGESTION + ".")
_COMPUTED = ("print the contents of {path} (a name the shell computes that can be a .env-class "
             "file) into the transcript")


def _echoes_request(program: str, args: list[str]) -> bool:
    """Whether an HTTP client prints the request it sends, headers included: curl -v,
    --verbose or --trace/--trace-ascii to stdout or stderr, wget -d/--debug, and httpie or xh
    -v/--verbose/--offline or a --print/-p selecting request parts (H or B)."""
    def flagged(arg: str, flag: str, valued: str) -> bool:
        # -sSv holds -v; -ofilev does not (-o takes the rest of the word as its value).
        if not re.fullmatch(r"-[^-].*", arg):
            return False
        for char in arg[1:]:
            if char == flag:
                return True
            if char in valued:
                return False
        return False

    for index, arg in enumerate(args):
        value = args[index + 1] if index + 1 < len(args) else ""
        if program == "curl":
            if arg == "--verbose" or flagged(arg, "v", "oHduXAebcFTwxEKmrYyzCPQtUu"):
                return True
            if arg in ("--trace", "--trace-ascii") and value in ("-", "%"):
                return True
            if re.fullmatch(r"--trace(-ascii)?=[-%]", arg):
                return True
        elif program == "wget":
            if arg == "--debug" or flagged(arg, "d", "OoaeitTwQPUlDXIRAB"):
                return True
        elif program in ("http", "https", "xh"):
            if arg in ("--verbose", "--offline") or flagged(arg, "v", "paoAs"):
                return True
            printed = (value if arg in ("--print", "-p") else
                       arg.split("=", 1)[1] if arg.startswith("--print=") else "")
            if re.search(r"[HB]", printed):
                return True
    return False


def _glued_unknown(word: str, cwd: str | None) -> str | None:
    """The word with each part the shell computes (a substitution, $NAME, ${NAME}) as `*`,
    when such a part is glued to written text in the last path component and the result can
    be a protected name (`$(echo .en)v`, `${f}v`, `"$x.env"`); a computed part alone or in a
    directory (`"$f"`, `"$(pwd)/a.txt"`) is not judged here."""
    marked = re.sub(r"\$\{\w+\}|\$\w+|" + _SUB_MARK + r"\d+__", "\0", word)
    base = marked.rsplit("/", 1)[-1]
    if "\0" not in base or not base.replace("\0", ""):
        return None
    shown = marked.replace("\0", "*")
    return shown if token_protected(shown.rsplit("/", 1)[-1], None) else None


_AWK = frozenset({"awk", "gawk", "mawk", "nawk"})
# The only awk options read past on the way to the program text; any other option (-f, -e,
# --source, an unknown one) leaves every word judged as a file operand.
_AWK_VALUE_OPTIONS = frozenset({"-F", "-v", "--field-separator", "--assign"})
_AWK_GLUED_OPTION = re.compile(r"-[Fv].+|--(?:field-separator|assign)=.*")


def _awk_program_word(program: str, args: list[str]) -> int | None:
    """The index in `args` of awk's program text (its first operand: `{print $6,$7}` is code
    whose `$6` is an awk field, not a file name the shell computes), or None when the
    program comes from elsewhere or an option is not one this reads past. Every later
    operand is a file and is judged as one."""
    if program not in _AWK:
        return None
    i = 0
    while i < len(args):
        word = args[i]
        if word in _AWK_VALUE_OPTIONS:
            i += 2
        elif _AWK_GLUED_OPTION.fullmatch(word):
            i += 1
        elif word == "--":
            return i + 1 if i + 1 < len(args) else None
        elif word.startswith("-") and word != "-":
            return None
        else:
            return i
    return None


def command_verdict(words: list[str], stdin: list[str], cwd: str | None,
                    state: _State, strings: list[str] = ()) -> str | None:
    """A deny reason for one simple command, or None. Inside a substitution whose output a
    variable captures (state.captured) a read prints nothing, so only egress is judged.
    `strings` are the command's here-strings."""
    if dumps_environment(words):
        return DUMP_MESSAGE
    words = _group_words(words)
    command = strip_prefix(words)
    leading = words[:len(words) - len(command)]
    for word in words:                                  # X=$(cat .env): X now holds values
        name = word.split("=", 1)[0]
        if "=" in word and name.isidentifier() and _SUB_MARK in word:
            # X=$(grep -rn K .): a capture refused when printed holds what it would print.
            found = _sub_paths([word], state, cwd)
            if found or _sub_reads([word], state, cwd):
                state.loaded.add(name)
                state.loaded_from = state.loaded_from or (found or ["a .env-class file"])[0]
        elif "=" in word and name.isidentifier() and _uses_loaded([word.split("=", 1)[1]], state):
            state.loaded.add(name)                      # Y=$X: a copy of a loaded value
    switch = _trace_switch(command)
    if switch is not None:
        state.traced = switch
        return None
    script = _inner_script(words, command)
    if script is not None:
        # bash -x -c '...': the inner shell traces what it runs.
        state.traced = state.traced or any(
            re.fullmatch(r"-[a-z]*[xv][a-z]*", word) for word in leading)
        return _upper().judge(_read(script, state), cwd, state, captured=state.captured)
    program = command[0].rsplit("/", 1)[-1] if command else ""
    args = command[1:]
    if command and _SUB_MARK in command[0]:
        # The output of a substitution is the program: it can be any reader, whether or
        # not its own output is captured, so a protected name among its words is a read.
        named = [w for w in args if token_protected(w, cwd)] + _sub_names(command[0], state, cwd)
        if named:
            return _named_message(named[0])
        if _sub_reads(command[:1], state, cwd):        # $(grep -rn K .): the output runs, and
            return _named_message("a .env-class file")  # "command not found" prints it on stderr
        spelled = _sub_program(command[0], state)       # $(echo grep) -rn K . is grep;
        if not state.captured and (spelled is None or spelled in GREP_PROGRAMS | RG_PROGRAMS):
            reason = _recurse_verdict(spelled or "grep", args, cwd)   # unspelled: read as grep
            if reason:
                return reason
    if _INTERPRETER.fullmatch(program) and args and _is_env_get(args[0], state):
        program, args = "env-get", args[1:]            # python3 .../env-get is env-get
    elif program == "env-get" and not _is_env_get(command[0], state):
        program = "./env-get"                           # a file of that name: any program
    if program == "env-get":
        return (command_verdict(args[args.index("--") + 1:], [], cwd, state)
                if "--" in args else None)
    if program == "ssh":
        sent = [path for path in stdin if token_protected(path, cwd)]
        if sent:                                        # ssh host 'cat > x' < .env
            return _named_message(sent[0], _SENDS)
        remote = _positionals(args, _SSH_VALUE_OPTIONS)[1:]
        return _upper().judge(_read(" ".join(remote), state), cwd, state) if remote else None
    if program == "export" and _sub_paths(args, state, cwd):
        for path in _sub_paths(args, state, cwd):       # export $(grep -v '^#' .env | xargs)
            _load(state, path, cwd)
        return None
    if not state.captured and _prints_loaded(program, args, state, strings):
        return _LOADED.format(what="print", path=state.loaded_from or "a .env-class file")
    # curl -H "Auth: $KEY" https://api sends the value where it belongs and passes (the
    # design rule); a client that echoes its own request (curl -v) prints it.
    if program in EGRESS_PROGRAMS and _echoes_request(program, args) \
            and _uses_loaded(args + list(strings), state):
        return _LOADED.format(what="print (the client echoes its request)",
                              path=state.loaded_from or "a .env-class file")
    reader = program in READERS | OPERAND_READERS | GREP_PROGRAMS | RG_PROGRAMS \
        or program.startswith("$") or _SUB_MARK in program or program == "./env-get"
    # ls "$(cat .env)": a value handed to any program as an argument or a here-string can
    # come back in its output or its error text. An assignment (X=$(cat .env)) is a load.
    assigns = program in ("local", "declare", "typeset", "readonly")
    carried = [word for word in args + list(strings) if _SUB_MARK in word
               and not (assigns and "=" in word and word.split("=", 1)[0].isidentifier())]
    if program not in SILENT_STDIN and carried and (
            program in EGRESS_PROGRAMS or not state.captured) and _sub_reads(carried, state, cwd):
        found = _sub_paths(carried, state, cwd)
        return _named_message(found[0] if found else "a .env-class file")
    if reader and not state.captured and _sub_paths(args, state, cwd, shown=True):
        return _named_message(_sub_paths(args, state, cwd, shown=True)[0])   # cat $(ls .env)
    if program in ("read", "mapfile", "readarray") and (
            _sub_paths(list(strings), state, cwd) or _sub_reads(list(strings), state, cwd)):
        state.loaded.update(arg for arg in args if not arg.startswith("-"))   # read X <<<"$(..)"
        state.loaded_from = state.loaded_from or "a .env-class file"
    reading = [path for path in stdin if token_protected(path, cwd)]
    if reading:
        if program in ("read", "mapfile", "readarray"):
            state.loaded.update(arg for arg in args if not arg.startswith("-"))
            state.loaded_from = state.loaded_from or reading[0]
        elif program == "done":
            state.loop_read = True                      # while read ...; done < .env
            state.loaded_from = state.loaded_from or reading[0]
        elif program in EGRESS_PROGRAMS:
            return _named_message(reading[0], _SENDS)
        elif not state.captured and program not in SILENT_STDIN and not (
                program in GREP_PROGRAMS | RG_PROGRAMS and _names_only_search(args)
                or program == "cut" and _cut_lists_names_only(args, cwd)):
            # A protected stdin reaches the transcript through any program that may print
            # it (tee, tr, a group, a script), not only through READERS.
            return _named_message(reading[0])
    if _INTERPRETER.fullmatch(program) and not state.captured:
        for script in _option_values(args, _SCRIPT_OPTIONS):
            named = _script_prints_file(script, cwd, prints="-p" in args or "--print" in args)
            if named:
                return _named_message(named)
    if "xargs" in {word.rsplit("/", 1)[-1] for word in leading} and not state.captured:
        # xargs -a .env / --arg-file=.env: the file's lines become the command's arguments.
        named = [word for word in leading if token_protected(word, cwd)]
        if named:
            return _named_message(named[0])
    skip = _search_skip(program, args) if program in GREP_PROGRAMS | RG_PROGRAMS else set()
    named = [w for w in command if w not in skip and token_protected(w, cwd)]
    if not named:
        if program in GREP_PROGRAMS | RG_PROGRAMS | {"git"} and not state.captured \
                and not _names_only_search(args):
            # git -C DIR grep searches from DIR, wherever the shell is.
            return _recurse_verdict(program, args,
                                    _git_split(args, cwd)[2] if program == "git" else cwd)
        if reader and not state.captured and not (
                program in GREP_PROGRAMS | RG_PROGRAMS and _names_only_search(args)):
            code = _awk_program_word(program, args)
            guessed = [shown for i, w in enumerate(args) if w not in skip and i != code
                       for shown in [_glued_unknown(w, cwd)] if shown]
            if guessed:
                return _named_message(guessed[0], _COMPUTED)
        return None
    if program in LOADERS:
        _load(state, named[0], cwd)                     # source .env: loads, prints nothing
        if state.traced and not state.captured:
            return _named_message(named[0], "print the lines of {path} (a .env-class file) "
                                            "through the shell's trace (set -x or -v)")
        return None
    if program in EGRESS_PROGRAMS:
        sent = _sent_words(program, args, cwd)
        return _named_message(sent[0], _SENDS) if sent else None
    if program == "git":
        if _git_verdict(args):
            return None
        sub = (_positionals(args, _GIT_VALUE_OPTIONS) or [""])[0]
        if sub in ("add", "stage"):
            return _named_message(named[0], "stage {path} (a .env-class file), one commit and "
                                            "one push from leaving the machine")
        return _named_message(named[0])
    if program in ("cp", "mv", "ln", "install"):
        # A copy or link of a protected SOURCE under an unprotected name is the next
        # command's plain read. A protected destination is a write into the file.
        paths = [arg for arg in args if not arg.startswith("-")]
        every_source = len(paths) < 2 or any(
            arg.startswith("--target-directory") or
            arg.startswith("-") and not arg.startswith("--") and "t" in arg for arg in args)
        sources = paths if every_source else paths[:-1]
        leaked = [path for path in sources if token_protected(path, cwd)]
        # GNU -b, --backup, -S, --suffix: the file written over is kept under another name.
        if not every_source and token_protected(paths[-1], cwd) and any(
                arg.startswith(("--backup", "--suffix")) or arg.startswith("-")
                and not arg.startswith("--") and set(arg[1:]) & set("bS") for arg in args):
            leaked = [paths[-1]]
        elif not leaked or not every_source and token_protected(paths[-1], cwd):
            return None
        return _named_message(leaked[0], "copy {path} (a .env-class file) to a name this guard "
                                         "does not protect")
    if state.captured:
        return None
    if program == "cut":
        allowed = _cut_lists_names_only(args, cwd)
    elif program == "find":
        allowed = not any(arg in FIND_ACTIONS for arg in args)
    elif program in GREP_PROGRAMS | RG_PROGRAMS:
        allowed = _names_only_search(args)
    elif program in ("perl", "ruby"):
        allowed = not any(word.startswith("-") and not word.startswith("--")
                          and set(word[1:]) & set("np") for word in args)   # perl -ne prints
    elif _INTERPRETER.fullmatch(program):
        # python3 -m base64 .env, -m json.tool: a module run on the file prints it.
        allowed = not any(word.startswith("-m") for word in args)
    else:
        allowed = not (reader or program == named[0].rsplit("/", 1)[-1])
    return None if allowed else _named_message(named[0])


def _program(words: list[str]) -> str:
    command = strip_prefix(_group_words(words))
    return command[0].rsplit("/", 1)[-1] if command else ""


# Programs that can run a command, or open a file, that their stdin names: awk (system($0),
# getline < $0), sed (GNU `e`, -f -), yq (load(.path)), ack (-x: the files to search),
# sdiff (-o: its `e` command starts an editor that reads the rest).
_STDIN_ACTORS = frozenset({"awk", "gawk", "mawk", "nawk", "sed", "gsed", "yq", "ack", "ack-grep",
                           "sdiff"})
# A pager can be told to pass its input through a command first (LESSOPEN='|-CMD').
_PAGERS = frozenset({"less", "more", "most", "bat", "batcat"})
# Programs for which a heredoc is only data: they print it, count it or pass it on, and
# nothing on their stdin can make them run a command or open a file. A body fed to any
# other program may be a script to it (tcsh, make -f -, psql \!).
_DATA_CONSUMERS = (_STDIN_PRINTERS | GREP_PROGRAMS | RG_PROGRAMS | {"wc", "tr", "cut"}) \
    - _STDIN_ACTORS - _PAGERS
# git, gh and glab run whatever an alias names (git -c alias.x='!sh' x), and many of their
# own commands take paths on stdin. These take a message, a body or a value there and
# nothing else; none can be an alias, which may not shadow a built-in command.
_TEXT_COMMANDS = {
    "git": frozenset({"commit", "tag", "notes", "stripspace", "interpret-trailers"}),
    "gh": frozenset({"pr", "issue", "release", "api", "gist", "secret", "variable", "auth",
                     "repo", "label", "run", "workflow"}),
    "glab": frozenset({"mr", "issue", "release", "api", "snippet", "variable", "auth", "repo",
                       "label"}),
}
# Options that make an otherwise plain reader take file names from its stdin, or hand what
# it read to a program. GNU tools and git take any unambiguous start of a long option.
_NAMES_FROM_STDIN = ("--files0-from", "--files-from", "--compress-program")
_GIT_NAMES_FROM_STDIN = ("--pathspec-from-file", "--stdin-paths")
_BODY_WORD = re.compile(r"""[^\s'"`;|&()<>{},=]+""")
_BODY_WORDS_READ = 4000
# What a heredoc feeds when its operator follows a compound command or stands alone.
_COMPOUND_END = frozenset({"", "done", "fi", "esac", "}"})
_STDIN_AS_FILE = ("/dev/stdin", "/dev/fd/0", "/proc/self/fd/0")


def _reads_script_from_stdin(words: list[str]) -> bool:
    """jq -f /dev/stdin, sed -f -: a reader told to take its program from stdin."""
    return any(os.path.normpath(word).endswith(_STDIN_AS_FILE) or word == "-f-"
               or word == "-" and words[i - 1:i] == ["-f"] for i, word in enumerate(words))


def _script_on_stdin(args: list[str]) -> bool:
    """True when an interpreter with these arguments takes the heredoc as its script
    (python3 <<EOF, node - <<EOF): no code option and no script file before it. With
    either, the body is input to code this guard has not read."""
    for word in args:
        if not word.startswith("-") or word == "-":
            return word == "-"
        if code_option(word):
            return False
    return True


def _stdin_role(words: list[str]) -> str:
    """What the program of one pipeline step can do with a heredoc it is fed:
      data    nothing but print, count or pass it on (_DATA_CONSUMERS, _TEXT_COMMANDS)
      script  an interpreter that runs it as its script
      acts    it can run a command or open a file the body names (_STDIN_ACTORS, an
              interpreter running other code, a reader given its program or its file
              names on stdin, an alias of git, gh or glab)
      other   a program this guard does not know."""
    command = strip_prefix(words[1:] if words[:1] == ["{"] else words)
    program, args = (command[0].rsplit("/", 1)[-1], command[1:]) if command else ("", [])
    if _INTERPRETER.fullmatch(program):
        return "script" if _script_on_stdin(args) else "acts"
    options = _GIT_NAMES_FROM_STDIN if program in _TEXT_COMMANDS else _NAMES_FROM_STDIN
    names = [word.split("=", 1)[0] for word in args if word.startswith("--")]
    if program in _STDIN_ACTORS or _reads_script_from_stdin(args) \
            or any(len(name) > 2 and option.startswith(name)
                   for name in names for option in options):
        return "acts"
    if program in _TEXT_COMMANDS:
        sub = _positionals(args, _GIT_VALUE_OPTIONS if program == "git" else frozenset())
        return "data" if sub[:1] and sub[0] in _TEXT_COMMANDS[program] else "acts"
    if program in _DATA_CONSUMERS:
        return "data"
    # stdbuf -o0 awk ..: a runner strip_prefix does not know still runs the program it names.
    names = {word.rsplit("/", 1)[-1] for word in args}
    return "acts" if names & _STDIN_ACTORS or any(map(_INTERPRETER.fullmatch, names)) else "other"


def _inline_code(words: list[str]) -> bool:
    """True when the step is an interpreter given its code on the command line, by any code
    option, clustered or not (`perl -lne`, `python3 -Ic`): shell.inline_code's rule."""
    return runs_inline_code(strip_prefix(words[1:] if words[:1] == ["{"] else words))


def _json_actor(words: list[str]) -> bool:
    """True when the step can act on a JSON literal it is fed: an interpreter given inline
    code, or any step whose role is `acts` but an interpreter running a script file (the
    declared inert row `| python3 tool.py`). A runner this guard does not strip (stdbuf awk)
    reads as `acts` with a program that is not an interpreter, so it counts."""
    if _inline_code(words):
        return True
    return _stdin_role(words) == "acts" and not _INTERPRETER.fullmatch(_program(words))


def _body_names(body: str, places: tuple) -> str | None:
    """The deny reason for a body that names a protected file to a program that can open
    what its input names (sed -f - with `r FILE`, awk reading `getline < $0`)."""
    words = list(dict.fromkeys(_BODY_WORD.findall(body)))
    if len(words) > _BODY_WORDS_READ:
        return UNREADABLE_MESSAGE.format(
            what=f"a heredoc of more than {_BODY_WORDS_READ} different words fed to a program "
                 "that can open the files its input names",
            instead="write the text to a file and pass the file")
    for word in words:
        if any(token_protected(word, here) for here in places):
            return _named_message(word)
    return None


def _body_as_script(body: str, places: tuple, state: _State) -> str | None:
    for here in places:
        reason = _upper()._script_verdict(body, here, state, captured=state.captured)
        if reason and not isinstance(reason, Notice):
            return reason
    return None


def _heredoc_verdict(key: str, index: int, steps: list[_Step], command: str, places: tuple,
                     state: _State) -> str | None:
    """A deny reason for the heredoc `key` fed to steps[index], or None. Who gets the body
    decides how it is read, and each class is closed: nothing falls through unread.
      run     a shell, eval, source, ssh or a name consumer (xargs, read) anywhere in the
              pipeline or on the operator line, or a compound command (`done <<EOF`): the
              body is a script, judged whole in a shell of its own.
      script  an interpreter (python3 <<EOF, cat <<EOF | node): denied when it names a
              protected file and can print or run a command (see _script_prints_file).
      data    only programs that cannot act on what they read: nothing to judge.
      acts    a program that can run or open what its stdin names (awk, sed, an alias of
              git, python3 -c CODE): read as `other`, and denied when a word of the body
              is a protected file.
      other   any other program: it may run the body in a language this guard does not
              read, so every word of it is read as a command, quotes deleted.
    A bare delimiter leaves the body's expansions live in every class: they are judged as
    well (see _heredoc_expansions). The class is the worst of every program the body
    reaches down its pipeline, each named past its runner words (see _stdin_role)."""
    if key not in state.bodies:                         # typed text that looks like a key
        return None
    bare, body = state.bodies[key]
    if bare:
        body = state.spell(body)
    first = last = index
    while first and _PIPE.search(steps[first].before):
        first -= 1
    while last + 1 < len(steps) and _PIPE.search(steps[last].after):
        last += 1
    at = command.find("<<" + key)
    start = command.rfind("\n", 0, at) + 1
    end = command.find("\n", at)
    line = command[start:len(command) if end < 0 else end] if at >= 0 else ""
    names = {word.rsplit("/", 1)[-1] for step in steps[first:last + 1] for word in step.words}
    names |= {word.rsplit("/", 1)[-1] for words in segments(line) for word in words}
    programs = [_program(step.words) for step in steps[index:last + 1]]
    interpreted = [bool(_INTERPRETER.fullmatch(program)) for program in programs]
    roles = {_stdin_role(step.words) for step in steps[index:last + 1]}
    # A JSON literal is data to a program (refactor §3.7), unless a consumer's own code is in
    # the command (`python3 -c ...`) or the consumer acts on its input (awk `getline < $0`,
    # yq load(), a git alias): that code can open what the literal names.
    data = json_literal(body) and not any(_json_actor(step.words) for step in steps[index:last + 1])
    # `> >(sh)`: what stands inside a process substitution gets the body too.
    aside = any(">(" in step.before + step.after for step in steps[first:last + 1])
    if aside or names & _BODY_RUNNERS or programs[0] in _COMPOUND_END:
        reason = _body_as_script(body, places, state)
        if reason:
            return reason
        # A body that is harmless as a script can still name a file to an actor beside the
        # runner (`yq '.[] | load(.)'`: the `.` read as source).
        reason = "acts" in roles and not data and _body_names(body, places)
        if reason:
            return reason
    elif roles & {"acts", "other"} and not data:
        reason = _upper()._loose_verdict(body, places, state) \
            or ("acts" in roles and _body_names(body, places)) or None
        if reason:
            return reason
    for here in places if any(interpreted) and not data else ():
        named = _script_prints_file(body, here)
        if named:
            return _named_message(named)
    for text in _heredoc_expansions(line, at - start, at - start + 2 + len(key), body) \
            if bare else ():
        flat = _read(text, state)
        for here in places:
            reason = _upper().judge(flat, here, state, captured=state.captured)
            if reason:
                return reason
    return None


# The script layer (shell.env.script_verdict) registers itself here when it loads.
_UPPER = None


def _upper():
    """The script-level verdicts (judge, _script_verdict, _loose_verdict): a sub-shell,
    heredoc or script body inside a command is judged by the layer above this one, which
    imports this module and registers itself in _UPPER, so this module imports nothing
    above it (tests/contract/test_shell_package.py C5). Reached without that layer loaded
    (never through the entry), the verdict raises rather than guesses."""
    if _UPPER is None:
        raise RuntimeError("shell.env.script_verdict is not loaded; import it (or env_guard) first")
    return _UPPER
