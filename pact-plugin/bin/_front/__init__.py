"""
Location: pact-plugin/bin/_front/__init__.py
Summary: The readers behind the front door:
`repact inspect` (status.py, payload.py, refusals.py, criteria.py), `repact decide` (inbox.py)
and `repact resume` (status.py); teams.py holds the Teams-only refusal. They were the bin
tools repact-resume, repact-payload, repact-explain, repact-accept and repact-inbox;
bin/_repact_ops.py parses the arguments and calls them. Each reader prints its own lines and returns an exit code.
Used by: bin/_repact_ops.py; bin/repact-autopilot (inbox readers); bin/repact-status and
bin/pact-which-team (teams.teams_only).
"""

from __future__ import annotations

import sys
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parent.parent
HOOKS_DIR = BIN_DIR.parent / "hooks"
for _path in (BIN_DIR, HOOKS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
