"""
Location: pact-plugin/bin/_front/teams.py
Summary: The Teams-only refusal for the two readers that only make sense under Agent Teams.
It lives apart from the package __init__ so that __init__ imports nothing from the package
(tests/contract/test_import_layers.py forbids the cycle).
Used by: bin/repact-status and bin/pact-which-team.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from . import BIN_DIR  # noqa: F401  (sets the import path)


def teams_only(prog: str, session_dir: Optional[str] = None) -> Optional[int]:
    """None when the session runs Agent Teams; else print one plain line and return 2.

    The mode is the session's record (execution.json in `session_dir`, else in the session dir
    of $CLAUDE_CODE_SESSION_ID), else the precedence rules of shared/execution.py."""
    from shared import execution
    if session_dir is None and os.environ.get("CLAUDE_CODE_SESSION_ID"):
        from .status import session_dir_of
        found = session_dir_of(None, None)
        session_dir = str(found) if found else None
    if execution.current(session_dir, os.environ, os.getcwd()) == execution.TEAMS:
        return None
    print(f"{prog}: Agent Teams only, and this session runs subagents; `repact inspect` shows "
          "the open tasks and what waits.", file=sys.stderr)
    return 2
