"""
Location: pact-plugin/hooks/shared/task_utils.py
Summary: Shared Task system integration utilities for PACT hooks.
Used by: PACT lifecycle/wake hooks (via get_task_list) and every reader of the
task analysis functions. The store itself (paths, reads, the locked write) is
shared/task_store.py; iter_team_task_jsons, read_task_json and update_task_file
are re-exported from here until 1.7.

This module provides common functions for reading and analyzing Tasks from
the Claude Task system. Tasks live under one of two storage topologies, keyed
on a structural signal (team_name): a TEAM session stores tasks at
~/.claude/tasks/{team_name}/*.json, a SOLO/no-team session at
~/.claude/tasks/{session_id}/*.json. They survive context compaction, making
them the primary state source for workflow recovery.

Functions:
    get_task_list: Read all tasks from the Task system
    find_feature_task: Identify the main Feature task
    find_current_phase: Find the currently active phase task
    find_active_agents: Find the tasks live agents are working or waiting on
    find_blockers: Find blocker/algedonic tasks
    build_post_compaction_checkpoint: Build [POST-COMPACTION CHECKPOINT] message from Task state
    is_teachback_subject: True iff a subject matches the canonical Teachback shape
"""

from __future__ import annotations

import os
import re
from typing import Any

from shared.pact_context import get_session_id, get_team_name
from shared.task_store import TaskStore
# shim: delete in 1.7. The readers and the writer live in task_store; the names stay
# importable from here for one release.
from shared.task_store import (  # noqa: F401  # re-export: shim until 1.7
    iter_team_task_jsons, read_task_json, update_task_file)


def get_task_list(tasks_base_dir: str | None = None) -> list[dict[str, Any]] | None:
    """
    Read TaskList from the Claude Task system.

    Two live storage topologies, keyed on a structural signal (team_name):
      - TEAM session -> tasks under ~/.claude/tasks/{team_name}/   (Agent Teams)
      - SOLO session -> tasks under ~/.claude/tasks/{session_id}/  (no team)
    team_name is mode-independent (resolves identically under in-process and
    tmux), so the team branch needs no teammateMode detection. Hooks cannot
    call Task tools, so the store is read from disk through TaskStore, which
    applies the one set of path rules (safe component, containment, symlink
    and dotfile skips, object-only parse) to both branches.

    Args:
        tasks_base_dir: Override for the ~/.claude/tasks base dir (testing only;
            production default None resolves the real home path).

    Returns:
        List of task dicts, or None if tasks unavailable
    """
    store = TaskStore(tasks_base_dir) if tasks_base_dir else TaskStore()
    # A team session with no tasks yet is still a team session: the return lives
    # inside the `if` so control never reaches the solo branch when team_name is set.
    team_name = get_team_name()
    if team_name:
        tasks = store.list(team_name)
        return tasks if tasks else None
    # SOLO: CLAUDE_CODE_TASK_LIST_ID (user-controlled, so it passes the same component
    # check as a team name inside the store) else the session id.
    task_list_id = os.environ.get("CLAUDE_CODE_TASK_LIST_ID", get_session_id())
    if not task_list_id:
        return None
    tasks = store.list(task_list_id)
    return tasks if tasks else None


# Subject prefixes of the lead's phase tasks: never the feature, never an agent's.
_PHASE_PREFIXES = ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:", "Review:")

# Agent-type subject prefixes, for a task dispatched with no owner set.
_AGENT_TYPE_PREFIXES = (
    "preparer:",
    "architect:",
    "backend-coder:",
    "frontend-coder:",
    "database-engineer:",
    "devops-engineer:",
    "test-engineer:",
    "security-engineer:",
    "qa-engineer:",
    "auditor:",
    "secretary:",
)

_SIGNAL_TASK_TYPES = ("blocker", "algedonic")
# A wait the lead resolves: awaiting_lead_completion, awaiting_lead_commit.
_WAITS_ON_LEAD_PREFIX = "awaiting_lead"
# The lead's owner name. Kept here, not imported from shared.amendments, so
# this module adds no helper to the import closure of the hooks that load it.
_LEAD_OWNER = "team-lead"
_AGENT_NAME_MAX_CHARS = 64
_WAIT_REASON_MAX_CHARS = 60
_SUBJECT_MAX_CHARS = 120
_TASK_ID_MAX_CHARS = 12
# The checkpoint lists this many agents, and this many tasks per agent, then "+N more".
_MAX_LISTED_AGENTS = 12
_MAX_LISTED_AGENT_TASKS = 6
_OPEN_STATUSES = ("in_progress", "pending")
_CHECKPOINT_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029]+")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _task_key(task: dict[str, Any]) -> str:
    """The task id as a string, or "" when it has none usable."""
    task_id = task.get("id")
    if isinstance(task_id, bool) or not isinstance(task_id, (str, int)):
        return ""
    return str(task_id)


def _id_list(value: Any) -> list[str]:
    """A blocks / blockedBy value as id strings; anything else gives []."""
    if not isinstance(value, list):
        return []
    return [
        str(i) for i in value
        if isinstance(i, (str, int)) and not isinstance(i, bool) and str(i)
    ]


def _metadata(task: dict[str, Any]) -> dict[str, Any]:
    metadata = task.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def task_agent_name(task: dict[str, Any]) -> str:
    """The teammate that owns this task, or "" when no owner is set or the
    lead owns it. The owner is the agent's NAME; its subject prefix may be an
    agent type, a name or neither."""
    owner = _text(task.get("owner")).strip()
    return "" if owner == _LEAD_OWNER else owner


def task_wait_reason(task: dict[str, Any]) -> str:
    """The reason of the task's intentional_wait, or ""."""
    wait = _metadata(task).get("intentional_wait")
    return _text(wait.get("reason")).strip() if isinstance(wait, dict) else ""


def _is_agent_task(task: dict[str, Any]) -> bool:
    if task_agent_name(task):
        return True
    return _text(task.get("subject")).lower().startswith(_AGENT_TYPE_PREFIXES)


def _is_feature_candidate(task: dict[str, Any]) -> bool:
    """An open task that is the lead's own and neither a phase nor a signal."""
    if not _task_key(task) or task.get("status") not in _OPEN_STATUSES:
        return False
    if _is_agent_task(task) or _text(task.get("subject")).startswith(_PHASE_PREFIXES):
        return False
    return _metadata(task).get("type") not in _SIGNAL_TASK_TYPES


def _blocked_tasks(tasks: list[dict[str, Any]]) -> dict[str, set]:
    """id -> ids of the tasks it blocks, from both `blocks` and `blockedBy`.

    A completed blocker stays in `blockedBy` on disk, so an edge says the two
    tasks belong together, not that one is still waiting on the other."""
    edges: dict[str, set] = {}
    for task in tasks:
        key = _task_key(task)
        if not key:
            continue
        edges.setdefault(key, set()).update(_id_list(task.get("blocks")))
        for blocker in _id_list(task.get("blockedBy")):
            edges.setdefault(blocker, set()).add(key)
    return edges


def _reachable(start: str, edges: dict[str, set]) -> set:
    seen: set = set()
    stack = [start]
    while stack:
        for nxt in edges.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def find_feature_task(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Find the main Feature task from the task list.

    Among the lead's open tasks, in this order:
    1. the in_progress one a live agent task leads to through blocks /
       blockedBy (through phase tasks and other agent tasks);
    2. else the one in_progress task, linked or not: with more than one, None;
    3. else, with none in progress, the pending one a live agent task leads to;
    4. else None: the list does not say which task is the feature.

    An in_progress task outranks a pending one, so a task waiting on the
    feature (a release blockedBy it) or an older one never started is not
    named over the task the lead is working. A feature is normally blockedBy
    its agent tasks, so being blocked never rules a task out. Phase tasks,
    agent tasks and blocker / algedonic signal tasks are never the feature.

    Args:
        tasks: List of all tasks

    Returns:
        Feature task dict, or None if it cannot be told
    """
    tasks = [t for t in tasks if isinstance(t, dict)]
    candidates = {_task_key(t): t for t in tasks if _is_feature_candidate(t)}

    edges = _blocked_tasks(tasks)
    linked: set = set()
    for agent_task in find_active_agents(tasks):
        linked |= _reachable(_task_key(agent_task), edges) & set(candidates)
    in_progress = {
        key for key, task in candidates.items() if task.get("status") == "in_progress"
    }

    def chain_end(keys: set) -> dict[str, Any] | None:
        # With a chain of lead tasks, the feature is the one at the end of it.
        ends = [key for key in keys if not (_reachable(key, edges) & keys)]
        return candidates[ends[0]] if len(ends) == 1 else None

    feature = chain_end(linked & in_progress)
    if feature is not None:
        return feature
    if in_progress:
        return candidates[next(iter(in_progress))] if len(in_progress) == 1 else None
    return chain_end(linked)


def find_current_phase(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Find the currently active phase task.

    Phase tasks follow the pattern: "{PHASE}: {feature-slug}"
    The current phase is the one with status "in_progress".

    Args:
        tasks: List of all tasks

    Returns:
        Phase task dict, or None if not found
    """
    phase_prefixes = ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:")

    for task in tasks:
        if not isinstance(task, dict):
            continue
        subject = _text(task.get("subject"))
        if any(subject.startswith(p) for p in phase_prefixes):
            if task.get("status") == "in_progress":
                return task

    return None


def find_active_agents(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find the tasks live agents are working or waiting on.

    An agent task is in_progress and either owned by a teammate (any name;
    the owner is the agent) or, with no owner set, has an agent-type subject
    prefix ("{agent-type}: {work-description}"). An agent idle on an
    intentional_wait (awaiting_lead_completion, awaiting_lead_commit) still
    holds its task in_progress, so it is listed.

    Args:
        tasks: List of all tasks

    Returns:
        List of agent task dicts, in id order
    """
    active = [
        task for task in tasks
        if isinstance(task, dict)
        and task.get("status") == "in_progress"
        and _is_agent_task(task)
    ]
    return sorted(active, key=lambda t: (len(_task_key(t)), _task_key(t)))


def find_blockers(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find any blocker or algedonic tasks.

    These are signal tasks created by agents when they hit blockers
    or detect viability threats.

    Args:
        tasks: List of all tasks

    Returns:
        List of blocker/algedonic task dicts
    """
    blockers = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_type = _metadata(task).get("type", "")
        if task_type in _SIGNAL_TASK_TYPES:
            if task.get("status") != "completed":
                blockers.append(task)

    return blockers


def _checkpoint_field(value: Any, limit: int) -> str:
    """Task-authored text for one checkpoint line: control characters and line
    breaks become spaces, and the length is bounded."""
    cleaned = " ".join(_CHECKPOINT_CONTROL_CHARS.sub(" ", _text(value)).split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 3] + "..."


def _describe_agents(agents: list[dict[str, Any]]) -> "tuple[list[str], list[str]]":
    """(one entry per live agent, the names of those waiting on the lead).

    An agent is named by its task owner, with each task it holds and the
    intentional_wait reason where it has one. A task with no owner is listed
    by its subject."""
    by_name: dict[str, list[str]] = {}
    unowned: list[str] = []
    waiting_on_lead: list[str] = []
    for task in agents:
        if not isinstance(task, dict):
            continue
        name = _checkpoint_field(task_agent_name(task), _AGENT_NAME_MAX_CHARS)
        if not name:
            unowned.append(_checkpoint_field(task.get("subject"), _SUBJECT_MAX_CHARS) or "unknown")
            continue
        reason = _checkpoint_field(task_wait_reason(task), _WAIT_REASON_MAX_CHARS)
        state = f"waiting: {reason}" if reason else "in progress"
        by_name.setdefault(name, []).append(
            f"#{_checkpoint_field(_task_key(task), _TASK_ID_MAX_CHARS)} {state}"
        )
        if reason.startswith(_WAITS_ON_LEAD_PREFIX) and name not in waiting_on_lead:
            waiting_on_lead.append(name)
    entries = [
        f"{name} ({'; '.join(_capped(states, _MAX_LISTED_AGENT_TASKS))})"
        for name, states in by_name.items()
    ]
    return entries + unowned, waiting_on_lead


def _capped(items: list[str], limit: int) -> list[str]:
    """The first `limit` items, then "+N more" for the rest."""
    extra = len(items) - limit
    return items[:limit] + [f"+{extra} more"] if extra > 0 else items


def build_post_compaction_checkpoint(
    feature: dict[str, Any] | None,
    phase: dict[str, Any] | None,
    agents: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> str:
    """Build [POST-COMPACTION CHECKPOINT] message from Task state.

    Generates a concise message describing the workflow state for
    the orchestrator to resume from.

    Args:
        feature: Feature task dict or None
        phase: Current phase task dict or None
        agents: List of active agent tasks (find_active_agents)
        blockers: List of active blocker tasks

    Returns:
        Formatted refresh message string
    """
    lines = ["[POST-COMPACTION CHECKPOINT]"]
    lines.append("Prior conversation auto-compacted. Resume from Task state below:")

    # Feature context
    if feature:
        feature_subject = (
            _checkpoint_field(feature.get("subject"), _SUBJECT_MAX_CHARS) or "unknown feature"
        )
        feature_id = feature.get("id") and _checkpoint_field(
            _task_key(feature), _TASK_ID_MAX_CHARS
        )
        if feature_id:
            lines.append(f"Feature: {feature_subject} (id: {feature_id})")
        else:
            lines.append(f"Feature: {feature_subject}")
    else:
        lines.append(
            "Feature: cannot tell which task is the feature from the task list"
        )

    # Phase context
    if phase:
        phase_subject = (
            _checkpoint_field(phase.get("subject"), _SUBJECT_MAX_CHARS) or "unknown phase"
        )
        lines.append(f"Current Phase: {phase_subject}")
    else:
        lines.append("Current Phase: None detected")

    # Active agents
    agent_entries, waiting_on_lead = _describe_agents(agents)
    if agent_entries:
        listed = _capped(agent_entries, _MAX_LISTED_AGENTS)
        lines.append(f"Active Agents ({len(agent_entries)}): {', '.join(listed)}")
    else:
        lines.append("Active Agents: None")

    # Blockers (critical info)
    if blockers:
        lines.append("")
        lines.append("**BLOCKERS DETECTED:**")
        for blocker in blockers:
            subj = (
                _checkpoint_field(blocker.get("subject"), _SUBJECT_MAX_CHARS) or "unknown blocker"
            )
            level = _checkpoint_field(_metadata(blocker).get("level"), _WAIT_REASON_MAX_CHARS)
            if level:
                lines.append(f"  - {level}: {subj}")
            else:
                lines.append(f"  - {subj}")

    # Next step guidance
    lines.append("")
    if blockers:
        lines.append("Next Step: **Address blockers before proceeding.**")
    elif waiting_on_lead:
        lines.append(
            f"Next Step: {', '.join(_capped(waiting_on_lead, _MAX_LISTED_AGENTS))} "
            "waiting on you. Read the "
            "task files and act on each wait first, then monitor the other "
            "agents via TaskList."
        )
    elif agent_entries:
        lines.append("Next Step: Monitor active agents via TaskList, then proceed.")
    elif phase:
        lines.append("Next Step: Continue current phase or check agent completion.")
    else:
        lines.append("Next Step: **Check TaskList and ask user how to proceed.**")

    return "\n".join(lines)


# Canonical Teachback Task subject pattern: `<teammate-name>: TEACHBACK
# <mission descriptor>`, normally `TEACHBACK for …`. The leading
# `[a-z0-9-]+:` is the canonical teammate-prefix shape used across the
# plugin (matches names like `backend-coder-2`, `secretary`,
# `architect-1`); `TEACHBACK` is the canonical mission-framing marker per
# protocols/completion.md.
#
# WHY a structural match instead of substring `"teachback" in subject`:
# the substring form fires on ANY task subject containing the word —
# including planning/discussion subjects like `"Plan: wake-lifecycle
# teachback re-arm fix"` — and produces benign-but-noisy false-positive
# advisories. The structural match pins the pattern to the canonical
# Teachback-Gated Dispatch shape so only actual teachback tasks trip
# the rules.
#
# HOISTED here (from task_lifecycle_gate.py) so a second hook surface — the
# handoff_ordering_gate PreToolUse WARN — can reuse the same predicate without
# importing the sibling PostToolUse gate module (a coupling smell + import-time
# side-effect risk). SINGLE definition: the lifecycle gate re-imports it from
# here, so the regex is never duplicated (duplication would reintroduce the
# drift class the structural match was introduced to close).
#
# The tail is a WORD BOUNDARY, not the literal `TEACHBACK for `. The stricter
# trailing-space form silently missed real gates that qualify the marker before
# the mission — `preparer: TEACHBACK (respawn) for …` escaped the carve-out
# entirely. Widening keeps every subject the strict form matched (it is a
# superset) and admits qualified spellings; a work task titled
# `<name>: TEACHBACK…` now reads as a gate, which drops it from the dispatch
# population — the benign direction, and the reason a lossy-but-safe rule is
# preferred here over a precise one that can fail harmfully. `\b` still refuses
# `TEACHBACKS`/`TEACHBACKING`, so the marker must stand as its own word.
_TEACHBACK_SUBJECT_PATTERN = re.compile(r"^[a-z0-9-]+: TEACHBACK\b")


def is_teachback_subject(subject: str) -> bool:
    """Return True iff `subject` matches the canonical Teachback Task
    shape (`<teammate-name>: TEACHBACK <mission>`, normally
    `TEACHBACK for <mission>`; a qualifier between the marker and the
    mission — `TEACHBACK (respawn) for …` — still matches).

    Pure function; never raises. Returns False on non-string input or
    any subject that does not match the anchored pattern. Replaces the
    legacy `"TEACHBACK" in subject_upper` substring check across the
    gate's TaskCreate and TaskUpdate rule paths.
    """
    if not isinstance(subject, str):
        return False
    return _TEACHBACK_SUBJECT_PATTERN.match(subject) is not None
