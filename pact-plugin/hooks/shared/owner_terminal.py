"""
Location: pact-plugin/hooks/shared/owner_terminal.py
Summary: Who is calling: a Claude Code session (its host session id is in the environment) or
the owner at their own terminal. The owner's proof is the one the KB decide uses
(bin/_kb/decide.py terminal_confirms): no session id in the environment, stdin AND stderr are
interactive terminals, and the owner retypes a token. An agent's shell has no terminal; dropping
the session id alone (`env -u`) proves nothing, and a pseudo-terminal wrap is refused by a guard
rule (process.pty_wrap, a brake on habit, not a boundary).
Used by: shared/ops/records.py (host_session_id), shared/task_log/close_gate.py, bin/repact-task
(a row parked for the owner) and bin/repact-unattended (`off`).
Standard library only.

Example:
    if host_session_id():
        refuse("only the owner, at their own terminal, can do this")
    if not owner_confirms("off", "Type off to turn unattended mode off: "):
        refuse(...)
"""

from __future__ import annotations

import os
import sys
from typing import Mapping, Optional

SESSION_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")
TERMINAL_STREAMS = "with stdin and stderr on the terminal (no 2>log, no 2>&1 | tee)"


def host_session_id(environ: Optional[Mapping[str, str]] = None) -> str:
    """The calling Claude Code session's id from the environment, stripped, or ""."""
    env = os.environ if environ is None else environ
    for name in SESSION_ENV:
        value = env.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def owner_confirms(token: str, prompt: str, stdin=None, stderr=None,
                   environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only for the owner at their own terminal: no session id in the environment, stdin
    and stderr are interactive terminals, and the line typed is exactly `token`."""
    if host_session_id(environ):
        return False
    stdin = stdin or sys.stdin
    stderr = stderr or sys.stderr
    try:
        if not (stdin.isatty() and stderr.isatty()):
            return False
        print(prompt, end="", file=stderr, flush=True)
        return stdin.readline().strip() == token
    except (OSError, ValueError, AttributeError):
        return False
