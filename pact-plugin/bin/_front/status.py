"""
Location: pact-plugin/bin/_front/status.py
Summary: `repact inspect` with no task id (the status screen) and `repact resume` (formerly
bin/repact-resume). Hooks keep <session dir>/workflow-state.json on
every task event (hooks/shared/workflow_state.py); the screen prints it as at most 40 lines (phase
and feature, the cost line, worktrees, open tasks with owner and wait state, undisposed
amendments, the last accepted HANDOFFs, the next step), then one line per op a crash left
unfinished (<session dir>/ops.jsonl, shared/ops/records.unfinished) and per parked task.
`resume` prints the same screen; bin/_repact_ops.py then starts `claude --resume <session id>`
through the launcher.
The session is --session-dir, else --session-id or $CLAUDE_CODE_SESSION_ID, else (outside a
session) the newest session of this project that holds a workflow-state.json.
Used by: bin/_repact_ops.py (`repact inspect`, `repact resume`).
Exit codes: 0 printed; 1 no state file (rerun with --rebuild); 2 usage error or no session.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import _repact_common as common  # noqa: E402
from shared import workflow_state  # noqa: E402

REBUILD = "repact inspect --rebuild"
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def session_dir_of(session: Optional[str], session_dir: Optional[str]) -> Optional[Path]:
    """The session directory named; the newest pact-sessions/*/<id> for the session id; or with
    neither, the newest session dir of this project that holds a workflow-state.json."""
    if session_dir:
        path = Path(session_dir).expanduser()
        return path if path.is_dir() else None
    session_id = session or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    root = common.claude_config_dir() / "pact-sessions"
    if session_id:
        if not SESSION_ID.fullmatch(session_id):
            return None
        found = [p for p in root.glob(f"*/{session_id}") if p.is_dir()]
        return max(found, key=lambda p: p.stat().st_mtime) if found else None
    from shared.pact_context import project_slug
    slug = project_slug(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    states = [p for p in (root / slug).glob(f"*/{workflow_state.STATE_NAME}") if p.is_file()] if slug else []
    return max(states, key=lambda p: p.stat().st_mtime).parent if states else None


def identity(folder: Path) -> "tuple[str, str]":
    """(team_name, project_dir) the session recorded at its start; ("", "") when unknown."""
    try:
        context = json.loads((folder / "pact-session-context.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    if not isinstance(context, dict):
        return "", ""
    return str(context.get("team_name") or ""), str(context.get("project_dir") or "")


def extra_lines(folder: Path) -> List[str]:
    """Unfinished ops and parked tasks: what the stored state file does not hold."""
    out = []
    team, _ = identity(folder)
    try:
        from shared.ops import frame, records
        ctx = records.Context(None, team, folder, folder.name, frame.Caller(kind="lead"))
        for row in records.unfinished(ctx):
            task = f" #{row['task']}" if row.get("task") else ""
            out.append(f"Unfinished: {row.get('op')}{task} ({row.get('op_id')}): rerun the same "
                       f"`repact {row.get('op')}` command")
    except Exception as error:  # noqa: BLE001 - the screen still prints
        out.append(f"note: ops.jsonl not read ({type(error).__name__})")
    from .inbox import parked_rows
    parked, notes = parked_rows(str(folder), None, Path.cwd())
    out += [f"Parked: {item['task']}: {item['question']} -> repact decide" for item in parked]
    return out + notes


def screen(folder: Path, rebuild: bool = False, as_json: bool = False) -> Tuple[int, List[str]]:
    """(exit code, lines) of the status screen of the session in `folder`."""
    if rebuild:
        team, project_dir = identity(folder)
        if not team:
            return 2, [f"repact inspect: {folder} records no team; cannot rebuild"]
        state = workflow_state.rebuild(str(folder), team, project_dir)
        if not workflow_state.write(str(folder), state):
            return 2, ["repact inspect: the rebuilt state could not be written"]
    state = workflow_state.load(str(folder))
    if state is None:
        return 1, [f"repact inspect: no {workflow_state.STATE_NAME} in {folder} yet; run `{REBUILD}`"]
    if as_json:
        return 0, [json.dumps(state, sort_keys=True, indent=1)]
    fresh = workflow_state.freshness(str(folder), state)
    lines = workflow_state.render(state, fresh, str(folder)).splitlines() + extra_lines(folder)
    if fresh != "fresh":
        lines.append(f"(The file is {fresh}: the journal moved on without it. Run `{REBUILD}`.)")
    return 0, lines


def show(session: Optional[str], session_dir: Optional[str], rebuild: bool = False,
         as_json: bool = False) -> Tuple[int, Optional[Path]]:
    """Print the screen; (exit code, the session folder or None)."""
    folder = session_dir_of(session, session_dir)
    if folder is None:
        print("repact inspect: no session directory found (give --session-id or --session-dir)",
              file=sys.stderr)
        return 2, None
    code, lines = screen(folder, rebuild, as_json)
    print("\n".join(lines), file=sys.stdout if code == 0 else sys.stderr)
    return code, folder
