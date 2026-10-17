#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/file_tracker.py
Summary: PostToolUse hook matching Edit|Write that tracks which agent edits
         which files and warns on inter-agent conflicts.
Used by: hooks.json PostToolUse hook (matcher: Edit|Write)

Non-blocking (PostToolUse cannot block). Warns via additionalContext when
a different agent has already edited the same file.

Input: JSON from stdin with tool_input.file_path
Output: JSON with additionalContext warning if conflict detected
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import shared.pact_context as pact_context
from shared.background_work import frame_team_and_name
from shared.pact_context import get_session_id, resolve_agent_name
from shared.paths import get_claude_config_dir
from shared.task_store import TaskStore
from shared import state_file

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _normalize_path(file_path: str) -> str:
    """Normalize a file path for consistent comparison.

    Resolves symlinks and relative components so that './src/auth.ts',
    'src/auth.ts', and '/abs/path/src/auth.ts' all produce the same key
    in the tracking file.
    """
    return os.path.realpath(file_path)


def _state_root(tracking_file: Path, root: Path | None) -> Path:
    """The directory the edit-tracking file must stay under.

    None is for callers that own their path (tests); production entry points
    pass the config root, so containment applies to the path main() builds.
    """
    return root if root is not None else tracking_file.parent


# The label track_edit gives a lead frame. It is not a member name, so its
# absence from members[] does not mean the lead has stopped.
_LEAD_LABEL = "orchestrator"


def _advisory_exempt(file_path: str, input_data: dict) -> bool:
    """Edits that never draw the advisory: an agent-memory inbox (append-only
    by rule, and shared by every agent of one type) and the lead's own edits
    under the top-level docs/ of the project dir or of a worktree root (a
    directory holding a .git entry). A directory named docs deeper in the tree
    (src/docs/) is code like any other. The edit is still tracked."""
    path = Path(_normalize_path(file_path))
    if path.name == "MEMORY-inbox.md" and "agent-memory" in path.parts:
        return True
    if not pact_context.is_lead(input_data):
        return False
    project_dir = pact_context.get_project_dir()
    if not project_dir:
        return False
    project = Path(_normalize_path(project_dir))
    try:
        parents = path.relative_to(project).parts[:-1]
    except ValueError:
        return False
    return any(
        part == "docs" and (i == 0 or project.joinpath(*parents[:i], ".git").exists())
        for i, part in enumerate(parents)
    )


def _finished_editors(tracking_file: Path, names: set[str]) -> set[str]:
    """The editors among `names` whose work on the team is over: each owns at
    least one task and every task it owns is completed. A fixer that follows
    one of them on the same file is a handover, not an overlap.

    An editor that owns no task is unknown, so it is still working: a listed
    member between tasks, or a subagent with no member entry, tracked under
    its agent_type. Absence from members[] alone finishes nobody. The lead
    always is working. An unreadable or empty roster finishes nobody, so the
    advisory stays.
    """
    try:
        team = tracking_file.parent.name
        teams_dir = tracking_file.parent.parent
        roster = pact_context._iter_members(team, str(teams_dir))
        members = {m.get("name") for m in roster if isinstance(m.get("name"), str)}
        if not members:
            return set()
        leads = {_LEAD_LABEL} | {
            m.get("name") for m in roster if m.get("agentType") == "team-lead"
        }
        owned: dict[str, list] = {}
        for task in TaskStore.for_config(teams_dir.parent).iter_tasks(team):
            if isinstance(task.get("owner"), str):
                owned.setdefault(task["owner"], []).append(task.get("status"))
        return {
            name for name in names - leads
            if name in owned and all(s == "completed" for s in owned[name])
        }
    except (OSError, ValueError, TypeError):
        return set()


def _advised_keys(entry: dict) -> set[tuple[str, str]]:
    """The editor keys an entry's advisory named; empty for any other shape."""
    advised = entry.get("advised")
    if not isinstance(advised, list):
        return set()
    return {
        (key[0], key[1]) for key in advised
        if isinstance(key, list) and len(key) == 2
        and isinstance(key[0], str) and isinstance(key[1], str)
    }


def track_edit(
    file_path: str,
    agent_name: str,
    tool_name: str,
    tracking_path: str,
    session_id: str = "",
    root: Path | None = None,
    advised: list | None = None,
) -> None:
    """Append a file edit record to the tracking file.

    NEW-1 (#878): the editor is identified by the COMPOSITE key
    ``(agent_name, session_id)``, not by ``agent_name`` alone. Under tmux,
    same-``agent_type`` siblings (e.g. two ``backend-coder`` instances)
    collapse to the same ``resolve_agent_name`` value, so an agent-name-only
    key cannot tell them apart and conflict detection false-negatives. The
    ``session_id`` supplies the per-instance uniqueness in BOTH modes:
    ``main()`` reads it from the PACT context, or from the frame's own
    ``session_id`` in a separate-process teammate's process, which has no
    context. ``agent_name`` is retained as the human-readable LABEL; for a
    separate-process teammate ``resolve_agent_name`` recovers the member name
    from its session-registry entry.

    ``advised`` is the list of editor keys this edit's advisory named (filled
    by ``check_conflict``); it is stored on the entry so each pair of editors
    is advised about a file once.
    """
    file_path = _normalize_path(file_path)
    tracking_file = Path(tracking_path)

    new_entry = {
        "file": file_path,
        "agent": agent_name,
        "session_id": session_id,
        "tool": tool_name,
        "ts": int(time.time()),
    }
    if advised:
        new_entry["advised"] = advised

    # One locked read-modify-write; the new list is swapped in whole, so a
    # failure part-way leaves the previous file intact.
    def _apply(content: str):
        try:
            entries = json.loads(content) if content.strip() else []
        except json.JSONDecodeError:
            entries = []
        entries.append(new_entry)
        return json.dumps(entries), True, None

    state_file.locked_update(tracking_file, _apply, _state_root(tracking_file, root))


def check_conflict(
    file_path: str,
    agent_name: str,
    tracking_path: str,
    session_id: str = "",
    root: Path | None = None,
    advised: list | None = None,
) -> str | None:
    """Check if another EDITOR INSTANCE has edited this file.

    NEW-1 (#878): an editor is the COMPOSITE ``(agent_name, session_id)`` — so
    a different *instance* of the same ``agent_type`` (same ``agent_name``,
    different ``session_id``) is correctly counted as a separate editor and a
    real cross-instance conflict is DETECTED under tmux (the prior
    agent-name-only key false-negatived here). The SAME instance editing twice
    (same composite) is NOT a conflict.

    The conflict message lists the human-readable ``agent`` LABEL of each other
    editor. When two other editors share an ``agent`` label but differ by
    ``session_id`` (same-type siblings under tmux), the label is disambiguated
    with a short session_id suffix so the message names two distinct editors
    rather than a confusing repeated name.

    Two kinds of other editor are left out: one this editor has already been
    paired with in an advisory for this file (in either direction), and one
    whose work is over (``_finished_editors``). The keys the message names are
    appended to ``advised`` for ``track_edit`` to store.
    """
    file_path = _normalize_path(file_path)
    if not agent_name:
        return None

    tracking_file = Path(tracking_path)
    try:
        entries = json.loads(
            state_file.read_text(tracking_file, _state_root(tracking_file, root))
        )
    except (json.JSONDecodeError, OSError):
        return None

    self_key = (agent_name, session_id)
    # Collect the distinct OTHER editor instances (composite key) and, per
    # agent label, the set of session_ids seen — so the label can be
    # disambiguated only when a name is genuinely shared across instances.
    other_keys: set[tuple[str, str]] = set()
    sessions_by_agent: dict[str, set[str]] = {}
    already_advised: set[tuple[str, str]] = set()
    for entry in entries:
        if entry.get("file") != file_path:
            continue
        entry_agent = entry.get("agent", "")
        entry_session = entry.get("session_id", "")
        named = _advised_keys(entry)
        if (entry_agent, entry_session) == self_key:
            already_advised |= named
            continue
        if self_key in named:
            already_advised.add((entry_agent, entry_session))
        other_keys.add((entry_agent, entry_session))
        sessions_by_agent.setdefault(entry_agent, set()).add(entry_session)

    other_keys -= already_advised
    finished = _finished_editors(tracking_file, {agent for agent, _ in other_keys})
    other_keys = {key for key in other_keys if key[0] not in finished}
    if not other_keys:
        return None
    if advised is not None:
        advised.extend([agent, session] for agent, session in sorted(other_keys))

    labels = []
    for entry_agent, entry_session in other_keys:
        # Disambiguate with a short session_id suffix only when this agent
        # name is shared by more than one instance (otherwise the bare name
        # is unambiguous and cleaner).
        if entry_session and len(sessions_by_agent.get(entry_agent, set())) > 1:
            labels.append(f"{entry_agent} (session {entry_session[:8]})")
        else:
            labels.append(entry_agent)
    others = ", ".join(sorted(labels))
    return (
        f"File conflict: {file_path} was also edited by {others}. "
        f"Consider coordinating via SendMessage to avoid merge conflicts."
    )


def get_environment_delta(
    since_ts: int,
    requesting_agent: str,
    tracking_path: str,
    root: Path | None = None,
) -> dict[str, str]:
    """Return files modified by OTHER agents since the given timestamp.

    Returns a dict of {file_path: agent_name} for files modified by agents
    other than requesting_agent after since_ts. Used by orchestrator to
    detect environment drift when dispatching or briefing agents.

    Note: Uses inclusive boundary (>=) — entries AT exactly since_ts are included.
    """
    tracking_file = Path(tracking_path)
    try:
        entries = json.loads(
            state_file.read_text(tracking_file, _state_root(tracking_file, root))
        )
    except (json.JSONDecodeError, OSError):
        return {}

    delta: dict[str, str] = {}
    for entry in entries:
        file_path = entry.get("file")
        agent = entry.get("agent")
        if not file_path or not agent:
            continue
        if entry.get("ts", 0) >= since_ts and agent != requesting_agent:
            delta[file_path] = agent

    return delta


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    pact_context.init(input_data)
    team_name, _member = frame_team_and_name(input_data)
    if not team_name:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    file_path = input_data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    agent_name = resolve_agent_name(input_data)
    tool_name = input_data.get("tool_name", "")
    # session_id is the per-instance uniqueness component of the composite
    # editor key. A separate-process teammate's own process has no PACT context,
    # so get_session_id() is empty there, and the frame's own session_id is that
    # teammate's session.
    frame_session = input_data.get("session_id")
    session_id = get_session_id() or (frame_session if isinstance(frame_session, str) else "")

    teams_root = get_claude_config_dir() / "teams"
    tracking_path = str(teams_root / team_name / "file-edits.json")

    # Check for conflict BEFORE recording this edit. Pass the same
    # (agent_name, session_id) composite so this instance's own prior edits are
    # excluded but a different instance's are detected.
    advised: list = []
    conflict = None
    if not _advisory_exempt(file_path, input_data):
        conflict = check_conflict(
            file_path, agent_name, tracking_path, session_id, root=teams_root,
            advised=advised,
        )

    # Record this edit. A refused or failed write skips tracking rather than
    # failing the hook: this PostToolUse hook must still exit 0.
    try:
        track_edit(
            file_path, agent_name or _LEAD_LABEL, tool_name, tracking_path,
            session_id, root=teams_root, advised=advised,
        )
    except OSError:
        pass

    # Warn if conflict
    if conflict:
        # hookEventName is required by the harness; missing it silently fails open
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": f"\u26a0\ufe0f {conflict}"
            }
        }
        print(json.dumps(output))
    else:
        # Unlike other hooks, this else-branch is new code (the original had
        # no explicit no-conflict path — it fell through to bare sys.exit(0))
        print(_SUPPRESS_OUTPUT)

    sys.exit(0)


if __name__ == "__main__":
    main()
