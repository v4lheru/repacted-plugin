"""
Location: pact-plugin/hooks/shared/team_labels.py
Summary: Who a session is, as a label for records: teammate_label and label_from_agent_id
         (team, name) from the registry and the host task files, team_exists,
         teams_claiming_session, lead_label_for_session, current_task_id, and git_context
         (branch and short sha of a cwd, bounded).
Used by: hooks/run_ledger.py, bin/repact-wait, shared/task_files.py, (via the bin/_repact_common
         shim).
Map (file order): teammate_label, label_from_agent_id, team_exists, teams_claiming_session,
lead_label_for_session, current_task_id, GIT_TIMEOUT_SECONDS, git_context.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

from shared.plugin_paths import claude_config_dir, task_store


def teammate_label(session_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(agent_name, team) from PACT's labelling registry; (None, None) when unknown.

    The registry is self-asserted, so this is a label, never an authority. It cannot label
    IN-PROCESS teammates: they share the lead's session id and PACT skips their write
    (measured by wu10-shell). Prefer label_from_agent_id() on the hook payload first.
    """
    if not session_id:
        return None, None
    registry = claude_config_dir() / "pact-sessions" / ".teammate-registry.jsonl"
    label = None
    try:
        with open(registry, encoding="utf-8") as handle:
            for raw in handle:
                try:
                    item = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("session_id") == session_id:
                    label = item.get("value")
    except OSError:
        return None, None
    if not isinstance(label, str) or "@" not in label:
        return None, None
    name, team = label.rsplit("@", 1)
    return name or None, team or None


def label_from_agent_id(agent_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(agent_name, team) from a hook payload agent_id of the form "name@team".

    In-process teammates carry that form (team config members[].agentId); any other
    shape yields (None, None), so callers fall back to teammate_label().
    """
    if not isinstance(agent_id, str) or agent_id.count("@") != 1:
        return None, None
    name, team = agent_id.split("@")
    return (name, team) if name and team else (None, None)


def team_exists(team: Optional[str]) -> bool:
    """True when <config>/teams/<team>/ exists: a team name nobody created is a guess."""
    return bool(team) and (claude_config_dir() / "teams" / str(team)).is_dir()


def teams_claiming_session(session_id: Optional[str]) -> list[tuple[str, str]]:
    """[(lead name, team)] for every team config on disk whose leadSessionId is this session.

    The authoritative source is <config>/teams/*/config.json: its leadSessionId says which
    session leads the team, and its leadAgentId ("team-lead@<team>") names the lead. More than
    one entry means a stale config and a live one both claim the session (a resumed session).
    """
    if not session_id:
        return []
    claims = []
    for config_path in sorted((claude_config_dir() / "teams").glob("*/config.json")):
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(config, dict) or config.get("leadSessionId") != session_id:
            continue
        name, team = label_from_agent_id(config.get("leadAgentId"))
        claims.append((name or "team-lead", team or config.get("name") or config_path.parent.name))
    return claims


def lead_label_for_session(session_id: Optional[str]) -> Optional[tuple[str, str]]:
    """(name, team) of the LEAD when exactly ONE team config on disk names this session as its lead.

    In-process teammates share the lead's session_id, so the session registry cannot tell them
    apart; the team config can. None when no team claims the session, and None when MORE than
    one does: choosing one (say the first alphabetically) could file runs under a stale team's
    tasks, so an ambiguous session gets no team rather than a guessed one.
    """
    claims = teams_claiming_session(session_id)
    return claims[0] if len(claims) == 1 else None


def current_task_id(team: Optional[str], agent_name: Optional[str]) -> Optional[str]:
    """Id of the in_progress task owned by agent_name in the team (newest if several)."""
    if not team or not agent_name:
        return None
    best: tuple[float, Optional[str]] = (-1.0, None)
    for task_file in task_store().files(team):
        try:
            task = json.loads(task_file.read_text(encoding="utf-8"))
            mtime = task_file.stat().st_mtime
        except (OSError, ValueError):
            continue
        if isinstance(task, dict) and task.get("status") == "in_progress" \
                and task.get("owner") == agent_name and mtime > best[0]:
            best = (mtime, str(task.get("id") or task_file.stem))
    return best[1]


GIT_TIMEOUT_SECONDS = 2.0


def git_context(cwd: Optional[os.PathLike | str]) -> tuple[Optional[str], Optional[str]]:
    """(HEAD sha, branch) for a directory; (None, None) outside git or on any error."""
    def _git(*args: str) -> Optional[str]:
        try:
            result = subprocess.run(["git", *args], cwd=cwd or None, capture_output=True,
                                    text=True, timeout=GIT_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None if result.returncode == 0 else None

    return _git("rev-parse", "HEAD"), _git("rev-parse", "--abbrev-ref", "HEAD")
