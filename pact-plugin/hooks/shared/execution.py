"""
Location: pact-plugin/hooks/shared/execution.py
Summary: The session's execution mode: `subagents`, the default,
or `teams`, decided once per session and kept in <session dir>/execution.json.
Used by: hooks/dispatch.py (skips the Teams-only checks), hooks/session_init.py (writes the
record at SessionStart), hooks/dispatch_gate.py (rules 6-7), shared/ops/dispatch.py (the mode
of a dispatch and the downgrade) and bin/_doctor_host.py (the doctor line).

Precedence:
  1. the env REPACT_EXECUTION (`subagents` or `teams`). The user settings' env block lands
     here, which is where `/repacted:init --teams` writes it; autopilot, `repact work
     --unattended` and the CI template set `subagents` in the process env, so it wins.
  2. the team policy `"execution": "teams"` (.repacted/policy.json, shared/team_policy.py).
  3. the default, `subagents`.
`teams` also needs the host flag CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS; without it the mode is
`subagents` and the reason says why.

The downgrade: at a dispatch, a `teams` session whose team has no teams/<team>/config.json
(claude -p writes none) becomes `subagents`. The record says so, and the
dispatch prints one line, only on the write that downgrades.

Never raises: a record that cannot be read or written is decided again from the rules.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Tuple

SUBAGENTS = "subagents"
TEAMS = "teams"
MODES = (SUBAGENTS, TEAMS)
ENV = "REPACT_EXECUTION"
TEAMS_FLAG = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"
FILE_NAME = "execution.json"
MAX_BYTES = 4096
SHOWN_MAX = 40          # characters of an unknown env word echoed in the reason


def flag_on(value) -> bool:
    """The host's reading of a boolean env flag (bin/_doctor_host.py reads it the same way)."""
    return str(value).strip().lower() not in ("", "0", "false", "no", "off", "none")


def _policy_wants_teams(cwd: Optional[str]) -> bool:
    try:
        from shared import team_policy
        return team_policy.execution(cwd) == TEAMS
    except Exception:   # noqa: BLE001 - a policy that cannot be read asks for nothing
        return False


def decide(env: Optional[Mapping[str, str]] = None, cwd: Optional[str] = None) -> dict:
    """{"mode", "why"} from the precedence rules, with no record read or written."""
    env = os.environ if env is None else env
    raw = env.get(ENV)
    word = raw.strip().lower() if isinstance(raw, str) else ""
    note = f"; {ENV}={raw[:SHOWN_MAX]!r} is not a mode" if word and word not in MODES else ""
    if word in MODES:
        asked, source = word, f"{ENV}={word}"
    elif _policy_wants_teams(cwd):
        asked, source = TEAMS, "team policy" + note
    else:
        return {"mode": SUBAGENTS, "why": "default" + note}
    if asked == TEAMS and not flag_on(env.get(TEAMS_FLAG, "")):
        return {"mode": SUBAGENTS, "why": f"{source} asks for teams, but {TEAMS_FLAG} is off"}
    return {"mode": asked, "why": source}


def path(session_dir) -> Path:
    return Path(session_dir) / FILE_NAME


def read(session_dir) -> Optional[dict]:
    """The session's record, or None when there is none or it cannot be trusted (a link, too
    big, not JSON, or a mode that is not one of MODES)."""
    if not session_dir:
        return None
    target = path(session_dir)
    try:
        if target.is_symlink() or not target.is_file() or target.stat().st_size > MAX_BYTES:
            return None
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("mode") not in MODES:
        return None
    if not isinstance(record.get("why"), str):
        return None
    return record


def _write(session_dir, record: dict) -> bool:
    folder = Path(session_dir)
    try:
        if not folder.is_dir():
            return False
        fd, tmp = tempfile.mkstemp(dir=str(folder), prefix=".execution-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True)
        os.replace(tmp, path(folder))
        return True
    except OSError:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure(session_dir, env: Optional[Mapping[str, str]] = None,
           cwd: Optional[str] = None) -> dict:
    """The session's record: the one on disk, else a new decision written there."""
    record = read(session_dir)
    if record is not None:
        return record
    record = dict(decide(env, cwd), at=_now())
    _write(session_dir, record)
    return record


def current(session_dir, env: Optional[Mapping[str, str]] = None,
            cwd: Optional[str] = None) -> str:
    """The mode a hook acts on: the record's, else the rules' (a hook never writes it)."""
    record = read(session_dir)
    return (record or decide(env, cwd))["mode"]


def _team_file(team: str) -> Path:
    from shared.paths import get_claude_config_dir
    return get_claude_config_dir() / "teams" / team / "config.json"


def settle_for_team(session_dir, team: str, env: Optional[Mapping[str, str]] = None,
                    cwd: Optional[str] = None) -> Tuple[dict, Optional[str]]:
    """(the record, the one line to print or None). A `teams` record whose team file does not
    exist is rewritten as `subagents`, and only that write returns a line."""
    record = ensure(session_dir, env, cwd)
    if record["mode"] != TEAMS:
        return record, None
    try:
        exists = _team_file(team).is_file()
    except (OSError, ValueError):
        exists = False
    if exists:
        return record, None
    where = f"teams/{team}/config.json"
    record = {"mode": SUBAGENTS, "downgraded_from": TEAMS, "at": _now(),
              "why": f"teams asked ({record['why']}), but {where} does not exist"}
    _write(session_dir, record)
    return record, (f"Execution downgraded to subagents for this session: teams was asked, but "
                    f"{where} does not exist (claude -p writes none).")


def for_payload(payload: dict) -> str:
    """The mode for a hook payload: the session dir from CLAUDE_PROJECT_DIR and its session_id."""
    from shared.pact_context import reconstruct_session_dir
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    session_dir = ""
    if isinstance(session_id, str) and session_id and project_dir:
        session_dir = reconstruct_session_dir(project_dir, session_id)
    return current(session_dir)
