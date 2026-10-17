"""
Location: pact-plugin/hooks/egress_code.py
Summary: Reads the code a Python or Node command runs when its output reaches a network
         client (`curl ".../$(python3 parse.py data.json)"`, `node -e '...' | curl -d @-`).
         An interpreter is refused unless its code text can be read: a closed set of command
         forms (code_form), no token that reads the environment, runs code, lists a directory
         or sends by itself, imports from a closed set only (code_refusal), and no protected
         or credential file NAME (name_refusal). Everything else fails closed.
Used by: hooks/egress_uploads.py: _words_reads builds a CodeRead (code_form) and upload_paths
         hands it to code_uploads, which applies the text checks and returns every file the
         code or its arguments name, for the guard to scan. code_uploads takes the
         egress_uploads module as `base` (its word reader, path helpers and Unreadable), so
         this module imports nothing from it.
Limits:  a denylist over text is not a sandbox. Code that computes a path from pieces, or
         uses an allowed module in a way no token names, is not seen here.
Doc: the egress design notes, section 1.1
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass

PY, JS = "py", "js"
_PYTHON = re.compile(r"(?:python|pypy)[\d.]*")
NODE_PROGRAMS = frozenset({"node", "nodejs"})
PASS_FLAGS = {PY: frozenset({"-u", "-B", "-I", "-E", "-s", "-S"}), JS: frozenset()}
INLINE_FLAGS = {PY: frozenset({"-c"}), JS: frozenset({"-e", "--eval", "-p", "--print"})}
OUTPUT_REDIRECTS = frozenset({">", ">>", ">|", ">&", "&>", "&>>"})
RUN_TIME_MARKS = ("$", "`", "__repact_")       # a variable, a substitution, a heredoc placeholder
MAX_CODE_NAMES = 4096
_PATH_TOKEN = re.compile(r"[\w./~+@%-]+")
_STRING = re.compile(r"""(['"])((?:(?!\1)[^\\\n])+)\1""")
# An escape can spell a refused token or a file name this check would not recognise.
_ESCAPE = re.compile(r"\\(?:[xuUN]|[0-7])")

# Refused tokens by class. A family the language spells with suffixes (execvp, readdirSync,
# fwalk) is matched without a closing word boundary.
_REFUSED = {
    PY: (
        ("it reads the environment", r"\benvironb?\b|\bgetenvb?\b"),
        ("it runs code or commands",
         r"subprocess|\bsystem\b|os\.exec|\bexec[lv]p?e?\b|[Pp]open|spawn|\bexec\w*\s*\(|\beval\s*\("
         r"|\bcompile\s*\(|__import__|importlib|\brunpy\b|\bpickle\b|yaml\.load\s*\(|unsafe_load"
         r"|full_load"),
        ("it lists a directory", r"\b[ir]?glob\b|walk|listdir|scandir|iterdir"),
        ("it can send by itself", r"\bsocket\b|urllib\.request|urlopen|\brequests\b|\bhttp\b"),
    ),
    JS: (
        # `process` only as argv, the standard streams or exit: env, binding and the rest are out.
        ("it reads the environment or the process",
         r"\bprocess\b(?!\s*\.\s*(?:argv|stdout|stderr|stdin|exit|exitCode)\b)"),
        ("it runs code or commands",
         r"child_process|\beval\s*\(|\bFunction\s*\(|\bimport\s*\(|\bvm\b|\bconstructor\b"),
        ("it lists a directory", r"readdir|\bglob(?:Sync)?\b|opendir"),
        ("it can send by itself", r"\bfetch\s*\(|\bhttp\b|\bnet\b|\bdgram\b|\btls\b"),
    ),
}
_REFUSED = {lang: tuple((why, re.compile(pattern)) for why, pattern in rows)
            for lang, rows in _REFUSED.items()}
PY_MODULES = frozenset({"json", "sys", "re", "os", "pathlib", "csv", "base64", "datetime", "math",
                        "collections", "itertools", "textwrap", "hashlib", "argparse", "tomllib",
                        "yaml"})
PY_DOTTED_MODULES = frozenset({"urllib.parse"})
JS_MODULES = frozenset({"fs", "path", "os", "node:fs", "node:path", "node:os"})
_PY_IMPORT = re.compile(r"\b(?:from\s+(\S+)\s+)?import\b([^;\n]*)")
_PY_IMPORTED = re.compile(r"[A-Za-z_][\w.]*(?:\s+as\s+[A-Za-z_]\w*)?")
_JS_REQUIRE = re.compile(r"""\brequire\s*\(\s*(['"])([^'"]*)\1\s*\)""")
_JS_IMPORT = re.compile(r"""\bimport\b[^;\n'"]*?\bfrom\s*(['"])([^'"]*)\1""")
IMPORT_REFUSAL = "it imports a module outside the set this check allows"


@dataclass(frozen=True)
class CodeRead:
    """One interpreter command whose code text can be found: `code` is the inline code or the
    script path as written, `args` the words after it, `inputs` its `< file` redirects."""
    program: str
    lang: str
    inline: bool
    code: str
    args: tuple[str, ...]
    inputs: tuple[str, ...]


def language(program: str) -> str | None:
    """'py' or 'js' for an interpreter whose code this module reads; None for any other."""
    if _PYTHON.fullmatch(program):
        return PY
    return JS if program in NODE_PROGRAMS else None


def set_at_run_time(text: str) -> bool:
    return any(mark in text for mark in RUN_TIME_MARKS)


def code_form(program: str, words: list[str]) -> CodeRead | str:
    """The CodeRead of one interpreter command (words from the program on), or the reason its
    code cannot be found. Allowed forms only: pass flags, then one inline flag and its code,
    or a script path. A module run, stdin, a combined or attached flag and any other option
    are refused."""
    lang = language(program)
    if lang is None:
        return "only Python and Node code can be read"
    plain, inputs, i = [], [], 1
    while i < len(words):
        word = words[i]
        if word in OUTPUT_REDIRECTS:
            i += 2
        elif word == "<" and i + 1 < len(words) and words[i + 1] not in ("<", "("):
            inputs.append(words[i + 1])
            i += 2
        elif word and set(word) <= set("<>&"):
            return "it reads a heredoc or a redirect this check does not follow"
        else:
            plain.append(word)
            i += 1
    while plain and plain[0] in PASS_FLAGS[lang]:
        plain.pop(0)
    if not plain:
        return "it reads its code from stdin"
    if plain[0] in INLINE_FLAGS[lang]:
        if len(plain) < 2:
            return "its inline code is missing"
        if set_at_run_time(plain[1]):
            return "its inline code is built at run time"
        return CodeRead(program, lang, True, plain[1], tuple(plain[2:]), tuple(inputs))
    if not plain[0] or plain[0].startswith("-"):
        return "it is run with an option this check does not read"
    return CodeRead(program, lang, False, plain[0], tuple(plain[1:]), tuple(inputs))


def script_text(path: str, limit: int) -> str | None:
    """The text of the script at path; None unless it is a regular file within limit bytes
    that decodes as text."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > limit:
            return None
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return None
    if len(data) > limit or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _py_module_allowed(module: str) -> bool:
    return module in PY_DOTTED_MODULES or (
        module.split(".", 1)[0] in PY_MODULES and not module.startswith("."))


def py_imports(text: str) -> list[str] | None:
    """The modules the Python text imports, or None when an import cannot be read."""
    modules: list[str] = []
    for match in _PY_IMPORT.finditer(text):
        source, names = match.group(1), match.group(2).strip()
        if source is not None:
            modules.append(source)
            continue
        for part in names.split(","):
            if not _PY_IMPORTED.fullmatch(part.strip()):
                return None
            modules.append(part.split()[0])
    return modules


def _import_refusal(text: str, lang: str) -> str | None:
    if lang == PY:
        modules = py_imports(text)
        return None if modules is not None and all(map(_py_module_allowed, modules)) else IMPORT_REFUSAL
    for word, pattern in (("require", _JS_REQUIRE), ("import", _JS_IMPORT)):
        named = [match.group(2) for match in pattern.finditer(text)]
        if len(named) != len(re.findall(rf"\b{word}\b", text)) or not set(named) <= JS_MODULES:
            return IMPORT_REFUSAL
    return None


def code_refusal(text: str, lang: str) -> str | None:
    """Why the code text (comments included) is refused, or None. The text is NFKC-folded
    first: Python reads a full-width `ｅｖａｌ` as `eval`."""
    text = unicodedata.normalize("NFKC", text)
    if _ESCAPE.search(text):
        return "it holds an escape sequence this check does not decode"
    for why, pattern in _REFUSED[lang]:
        if pattern.search(text):
            return why
    return _import_refusal(text, lang)


def path_candidates(text: str) -> list[str] | None:
    """Every piece of the code text that may name a file: each path-shaped token and each
    quoted string (a name may hold spaces). None when there are too many to check."""
    found = dict.fromkeys(_PATH_TOKEN.findall(text))
    found.update(dict.fromkeys(match.group(2) for match in _STRING.finditer(text)))
    return list(found) if len(found) <= MAX_CODE_NAMES else None


def name_refusal(candidates: list[str]) -> str | None:
    """Refused when a candidate is a protected or credential file by NAME (an env file that is
    not a template, a private key, a registry credential), whether or not it exists."""
    from shell.env.git_egress import git_protected_kind   # noqa: PLC0415 - only for code reads
    for candidate in candidates:
        if git_protected_kind(os.path.expanduser(candidate).rstrip("/")):
            return "it names a protected or credential file"
    return None


def shadowed_import(text: str, directories: list[str]) -> bool:
    """True when a module the Python text imports has a same-named file or package in a
    directory Python searches before its own library (the script's, or the one it runs in)."""
    tops = {module.split(".", 1)[0] for module in py_imports(text) or []}
    return any(os.path.lexists(os.path.join(directory, top + suffix))
               for directory in directories for top in tops
               for suffix in (".py", os.sep + "__init__.py"))


def _commands_within(base, text: str, depth: int = 0) -> list[list[str]]:
    """The words of every simple command in text, those inside its substitutions included."""
    text = base._with_process_substitutions(text)
    spans = base.substitution_spans(text) if depth <= base.MAX_RUNNER_DEPTH else []
    found = [raw for _start, _end, inner in spans for raw in _commands_within(base, inner, depth + 1)]
    for start, end, _inner in reversed(spans):
        text = text[:start] + base.SUBSTITUTION_WORD.format(0) + text[end:]
    return found + [raw for _before, raw in base.simple_commands(text)]


def _named_elsewhere(base, script: str, command: str, variables: dict[str, str]) -> bool:
    """True when a program other than a Python or Node interpreter names the script: it may
    write it before the interpreter runs (cp, mv, sed -i, curl -o)."""
    name = os.path.basename(script)
    for words in map(base.program_words, _commands_within(base, base.read_script(command)[0])):
        if not words or language(words[0].rsplit("/", 1)[-1]) is not None:
            continue
        for word in words:
            word = base._expand(word, variables) or word
            if any(name == os.path.basename(token) for token in _PATH_TOKEN.findall(word)):
                return True
    return False


def code_uploads(base, entry: CodeRead, command: str, places: list[str], variables: dict[str, str],
                 written: set[str], fills: tuple[str, ...]) -> list[tuple[str, str, list[str]]]:
    """(path, directory, excludes) for what an interpreter command a client consumes may send:
    its script (the guard scans it whole), its `< file` inputs and every existing file its
    code text or its arguments name. Raises base.Unreadable unless the code text can be read
    and passes the checks above.
    places are the directories the command may run in, written the files it writes."""
    def refuse(why: str):
        raise base.Unreadable(f"$({entry.program} ...)",
                              f"{entry.program} runs code whose reads cannot be checked: {why}")

    values = []
    for word in (*entry.args, *entry.inputs, *(() if entry.inline else (entry.code,))):
        value = base._expand(word, variables)
        if value is None or set_at_run_time(value) or any(fill in word for fill in fills):
            refuse("its script or an argument is set at run time")
        values.append(value)
    args, inputs = values[:len(entry.args)], values[len(entry.args):len(entry.args) + len(entry.inputs)]
    found: list[tuple[str, str, list[str]]] = []
    for path in inputs:                                  # a `< file` is read whole: required
        homes = [place for place in places if os.path.lexists(base._resolve(path, place))]
        found += [(path, place, []) for place in homes or places[:1]]
    directories, texts = list(places), [entry.code]
    if not entry.inline:
        script, texts = values[-1], []
        if any(c in script for c in "*?["):
            refuse("its script path is a pattern")
        homes = [place for place in places if os.path.lexists(base._resolve(script, place))]
        if not homes:
            refuse("its script is missing")
        if _named_elsewhere(base, script, command, variables):
            refuse("this command names its script elsewhere and may change it")
        for place in homes:
            full = base._resolve(script, place)
            text = None if full in written else script_text(full, base.MAX_FILE_BYTES)
            if text is None:
                refuse("its script is not a readable text file, or this command writes it")
            texts.append(text)
            found.append((script, place, []))
            directories.append(os.path.dirname(full))
    if entry.lang == PY and any(path.endswith((".py", ".pth")) for path in written):
        refuse("this command writes a module the code may import")
    for text in texts:
        candidates = path_candidates(text)
        if candidates is None:
            refuse("its code names too many files to check")
        candidates += args
        why = code_refusal(text, entry.lang) or name_refusal(candidates + inputs)
        if why:
            refuse(why)
        if entry.lang == PY and shadowed_import(text, directories):
            refuse("a file beside the code shadows a module it imports")
        for candidate in candidates:
            for place in dict.fromkeys(directories):
                found += [(name, place, []) for name in base._scannable(candidate, place)
                          if base._resolve(name, place) not in written]
    return found
