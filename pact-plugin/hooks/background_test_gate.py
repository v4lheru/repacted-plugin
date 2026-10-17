#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/background_test_gate.py
Summary: PreToolUse(Bash) check that refuses a TEAMMATE's test run in the background. A
         finished background job does not reliably wake an in-process teammate, so it idles
         on the job until the lead notices (repeated stalls in field use, even after
         the brief said "foreground"). The refusal names the foreground route.
Used by: hooks/dispatch_registry.py (PreToolUse, matcher "Bash", after wait_filler_gate; the
         dispatcher calls dispatch_check()). Tested by tests/test_background_test_gate.py.

What it refuses: a Bash call with run_in_background set whose command runs a test runner, or
a command in which the shell backgrounds a test run with a bare `&`: the and-or list before
that `&` (back to `;`, a line break, an earlier `&` or the open of its subshell) runs one,
wherever the `&` stands (`pytest & echo started`, `(pytest &)`, `pytest & disown`), unless a
later `wait` waits for it. A test runner is what docker_lock_gate.detect reads as one (pytest,
python -m pytest|unittest, npm|pnpm|yarn|bun test, jest, vitest, go test, cargo test, dotnet
test, ... through nice, env, timeout, tcount, uv run and the like; setsid too), and tcount,
run-tests (also as `python3 path/run-tests`) and make|gmake test|tests|check or one of
_MAKE_SUITES (exact names: `make test-server` and `make testdata` are not test runs, so
`make test-foo` is missed, the cheaper error for a nudge). Comments, heredoc bodies, quoted
text and the `&` of `&&`, `|&`, `>&` and `&>` never count.
Only a teammate frame is refused (shared/frame.py role "teammate"). The lead is re-invoked
when its job ends and may background anything; a dev server or a watcher is no test run.

Fail direction: allow. A frame whose role is unknown is allowed with one line on stderr; an
internal error, odd stdin or a command that cannot be read allows the call.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

_hooks_dir = Path(__file__).resolve().parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

GATE_NAME = "background_test_gate"
_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})
_PYTHON = re.compile(r"python(\d(\.\d+)?)?")
_SCRIPTS = frozenset({"tcount", "run-tests"})
_MAKE_TARGETS = frozenset({"test", "tests", "check"})
_MAKE_SUITES = frozenset(f"{verb}{sep}{suite}" for verb in ("test", "tests", "check")
                         for sep in "-_" for suite in ("unit", "units", "integration", "e2e",
                                                       "all", "quick", "smoke", "ci"))
_OPERATOR_CHARS = "|;&()<>\n"
_SETSID = re.compile(r"(?<![\w./-])setsid(?:\s+-[-\w]+)*\s+")
_MAKE_VALUE_OPTIONS = frozenset({"-C", "-f", "--file", "--directory", "-I", "-o", "-W", "-j"})

REASON = (
    "Run test suites in the foreground as a teammate: a finished background job does not "
    "reliably wake you, so you would idle on it. Run the same command again without "
    "run_in_background (and without a trailing `&`), with the Bash timeout at 600000. Split a "
    "run that may pass 10 minutes into foreground calls by file group."
)


def _script_runner(words: list[str]) -> bool:
    """tcount, run-tests (directly or as a python script) or make test|tests|check."""
    if not words:
        return False
    base = os.path.basename(words[0])
    if base in _SCRIPTS:
        return True
    if _PYTHON.fullmatch(base):
        index = next((i for i, word in enumerate(words) if i and not word.startswith("-")), 0)
        return bool(index) and os.path.basename(words[index]) in _SCRIPTS and "-c" not in words[1:index]
    if base in ("make", "gmake"):
        i, targets = 1, []
        while i < len(words):
            word = words[i]
            if word in _MAKE_VALUE_OPTIONS:
                i += 2
                continue
            if not word.startswith("-") and "=" not in word:
                targets.append(word)
            i += 1
        return any(target in _MAKE_TARGETS or target in _MAKE_SUITES for target in targets)
    return False


def runs_tests(command: object) -> bool:
    """True when the command runs a test runner anywhere in it."""
    if not isinstance(command, str) or not command.strip():
        return False
    from docker_lock_gate import detect
    from shell_words import segments, strip_prefix

    command = _SETSID.sub("", command)                 # setsid runs the next word, like nohup
    if detect(command).runner:
        return True
    return any(_script_runner(strip_prefix(words)) for words in segments(command))


def _background_bit(op: str, j: int) -> bool:
    """Is op[j], an `&` in a run of operator characters, the shell's background operator (not
    one half of `&&`, nor `|&`, `>&`, `<&` or `&>`)?"""
    before, after = op[j - 1:j], op[j + 1:j + 2]
    return before not in ("&", "|", ">", "<") and after not in ("&", ">")


def backgrounded(command: str) -> list[str]:
    """The text of each and-or list the shell runs in the background with a bare `&` and no
    later `wait`. [] when the script cannot be read (the gate then allows)."""
    from shell_words import Unreadable, read_script, segments, strip_prefix, token_spans

    try:
        script, _ = read_script(command)
    except (Unreadable, RecursionError):
        return []
    candidates, start, outer = [], 0, []
    for begin, _end, token in token_spans(script, _OPERATOR_CHARS) or []:
        if token.strip(_OPERATOR_CHARS):
            continue                                    # a word, not a run of operators
        for j, char in enumerate(token):
            if char == "(":
                outer.append(start)
                start = begin + j + 1
            elif char == ")":
                start = outer.pop() if outer else begin + j + 1   # `(a; b) &` is one unit
            elif char in ";\n":
                start = begin + j + 1
            elif char == "&" and _background_bit(token, j):
                candidates.append((script[start:begin + j], begin + j + 1))
                start = begin + j + 1
    # Is there a later `wait`? The text after each `&` splits exactly at the next `&` (a segment
    # separator), so walk the candidates backwards and read each stretch once: linear, where
    # re-reading the whole rest per `&` was quadratic (20,000 `&` took ~50 s).
    found, waits, end = [], False, len(script)
    for unit, after in reversed(candidates):
        waits = waits or any(strip_prefix(words)[:1] == ["wait"] for words in segments(script[after:end]))
        end = after
        if unit.strip() and not waits:
            found.append(unit)
    return found[::-1]


def _flagged(tool_input: dict) -> bool:
    from shared.background_launch import is_harness_background_bash

    return is_harness_background_bash({"tool_name": "Bash", "tool_input": tool_input}) is True


def check(payload: dict, ctx=None) -> Optional[str]:
    """The deny reason for one PreToolUse payload, or None to allow."""
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return None
    command = tool_input["command"]
    flag = tool_input.get("run_in_background")
    if flag not in (None, False, 0, "", "false") and _flagged(tool_input):
        if not runs_tests(command):
            return None
    elif "&" not in command or not any(runs_tests(unit) for unit in backgrounded(command)):
        return None                                     # most Bash calls stop at the `&` test
    from shared.frame import resolve

    role = resolve(payload).role
    if role == "unknown":
        print(f"{GATE_NAME}: frame role unknown, allowing a background test run", file=sys.stderr)
        return None
    return REASON if role == "teammate" else None


def dispatch_check(payload: dict, ctx=None):
    """check() in the dispatcher's Result shape (hooks/dispatch_registry.py)."""
    import dispatch_registry as registry

    try:
        reason = check(payload, ctx)
    except Exception as error:  # noqa: BLE001 - a check that cannot run decided nothing
        print(f"{GATE_NAME}: internal error, allowing: {type(error).__name__}", file=sys.stderr)
        return None
    if reason is None:
        return None
    return registry.Result(decision="deny", reason=reason, rule="teammate_background_test")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason = check(payload) if isinstance(payload, dict) else None
    except Exception as error:  # noqa: BLE001 - a check that cannot run decided nothing
        print(f"{GATE_NAME}: internal error, allowing: {type(error).__name__}", file=sys.stderr)
        return 0
    if reason is None:
        print(_ALLOW_OUTPUT)
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 2


if __name__ == "__main__":
    sys.exit(main())
