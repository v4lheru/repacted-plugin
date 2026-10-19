"""
Location: pact-plugin/hooks/shared/team_name.py
Summary: The team name a session's tasks and teams live under on disk: the minted
         "session-<first 8>" interactively, the bare session id under `claude -p`.
Used by: hooks/session_init.py (step 5).
"""
from __future__ import annotations

import os
from pathlib import Path

from shared.pact_context import generate_team_name, get_claude_config_dir, is_safe_path_component

HEADLESS_ENTRYPOINTS = ("sdk-",)   # CLAUDE_CODE_ENTRYPOINT: "cli" interactive, "sdk-cli" under -p, "sdk-py"/"sdk-ts"


def session_team_name(input_data: dict, teams_root: Path | None = None,
                      environ: dict | None = None, minted: str | None = None) -> str:
    """The team name the platform keys this session's tasks and teams by. Two names
    are possible: generate_team_name's "session-<first 8>" (interactive sessions) and the bare
    full session id (`claude -p` and SDK sessions: cfg/teams/<sid> and cfg/tasks/<sid>).
    The disk decides first: when exactly one of the two has a teams/ dir, that one. With
    neither (a fresh SessionStart, before the first TaskCreate provisions the team) a headless
    entrypoint gives the full id and anything else the minted name, so interactive sessions keep
    their name. `minted` is the caller's generate_team_name result. Never raises: any error gives
    the minted name."""
    minted = minted or generate_team_name(input_data)
    try:
        raw_id = input_data.get("session_id")
        session_id = str(raw_id) if raw_id else ""
        if not session_id or not is_safe_path_component(session_id) or session_id == minted:
            return minted
        root = Path(teams_root) if teams_root is not None else get_claude_config_dir()
        on_disk = [name for name in (minted, session_id)
                   if (root / "teams" / name).is_dir()]
        if len(on_disk) == 1:
            return on_disk[0]
        if on_disk:
            return minted
        entrypoint = (os.environ if environ is None else environ).get("CLAUDE_CODE_ENTRYPOINT", "")
        if isinstance(entrypoint, str) and entrypoint.startswith(HEADLESS_ENTRYPOINTS):
            return session_id
    except Exception:
        pass
    return minted
