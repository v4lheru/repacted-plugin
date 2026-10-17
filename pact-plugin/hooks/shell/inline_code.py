"""
Location: pact-plugin/hooks/shell/inline_code.py
Summary: The one rule for "this step runs code written on its command line": an interpreter
         program (INTERPRETER) given any code option (code_option), an exact long option or
         a short option word holding c, e, E, m, p or r (python3 -Ic, perl -lne, ruby -we,
         node -pe). Such a step can run what it is handed, so neither guard may treat a JSON
         literal or a heredoc body it reads as data.
Used by: hooks/shell/env/script.py (re-exports INTERPRETER and SCRIPT_OPTIONS under their old
         names), hooks/shell/env/judge.py (_script_on_stdin, _inline_code) and
         hooks/shell/merge/strip.py (carriers 11-12). Imports nothing but the stdlib, so both
         guards may import it (tests/contract/test_import_layers.py).
"""

from __future__ import annotations

import re

INTERPRETER = re.compile(r"python[\d.]*|pypy[\d.]*|node|nodejs|deno|bun|ruby[\d.]*|irb|perl[\d.]*|php|"
                         r"lua|osascript|Rscript|tsx|ts-node|swift")
SCRIPT_OPTIONS = ("-c", "-e", "-E", "--eval", "-p", "--print", "-r")
_CODE_LETTERS = frozenset("cemprE")


def code_option(word: str) -> bool:
    """True when `word` is an option that gives an interpreter its code: a long option of
    SCRIPT_OPTIONS or --command (`--eval=..` too), or a short option word whose letters
    include one of _CODE_LETTERS (`-c`, `-Ic`, `-lne`)."""
    if word.startswith("--"):
        return word.split("=", 1)[0] in SCRIPT_OPTIONS + ("--command",)
    return word.startswith("-") and bool(set(word[1:]) & _CODE_LETTERS)


def runs_inline_code(words: list[str]) -> bool:
    """True when `words` (one step, the program first) is an interpreter with a code option
    anywhere among its arguments. Fail closed: a word after the script name counts too, so
    `python3 tool.py -p 80` reads as inline code; `python3 tool.py '{..}'` does not."""
    return bool(words) and bool(INTERPRETER.fullmatch(words[0].rsplit("/", 1)[-1])) \
        and any(map(code_option, words[1:]))
