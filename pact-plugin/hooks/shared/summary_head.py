"""
Location: pact-plugin/hooks/shared/summary_head.py
Summary: Writes compact-summary.head.txt, the capped read a post-compaction lead
         is pointed at, beside the full compact-summary.txt.
Used by: compaction_owner.py (write_head when a lead summary is promoted),
         postcompact_archive.py (discard before staging; write_head beside the
         root singleton) and session_init.py (discard when a stale summary is
         moved, and the head path in the compact directive).

The summary is the harness's compact_summary text, which PostCompact receives
on stdin; the plugin only stores it, at 22-44 KB a compaction. The head is at
most COMPACT_SUMMARY_HEAD_MAX_BYTES: a pointer block rebuilt from disk (open
tasks with owners, last HANDOFF revs and undisposed amend_<n> keys; worktrees
and branches), then the summary cut at a line boundary, then one line that
states the full file's size and path. The full file is never changed, so no
text is lost, and a summary that fits passes through whole with no last line.

The head mirrors the full file: it is written with it and discarded when a new
compaction is staged or the full file is moved, so a head never outlives the
compaction it describes. Every function here is fail-open.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .amendments import amendments, handoff_rev, missing_dispositions
from .constants import (
    COMPACT_SUMMARY_HEAD_MAX_BYTES,
    COMPACT_SUMMARY_HEAD_NAME,
    COMPACT_SUMMARY_NAME,
)

POINTER_MAX_BYTES = 3584
SUBJECT_CHARS = 70
FIELD_CHARS = 160
WORKTREES_MAX = 10

_OPEN_STATUSES = ("in_progress", "pending")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]+")


def write_head(folder: str | Path, summary: str) -> bool:
    """Write the head of the summary that sits at folder/compact-summary.txt.

    True when the head was written. Never raises: a failed head leaves the
    full summary as the only copy, which is the state before this module.
    """
    try:
        folder = Path(folder)
        full = folder / COMPACT_SUMMARY_NAME
        text = build_head(summary, full, pointer_block(folder))
        _write_atomic(folder / COMPACT_SUMMARY_HEAD_NAME, text)
        return True
    except Exception:
        return False


def discard(folder: str | Path) -> None:
    """Remove folder's head. It is derived from the full summary, never the only copy."""
    with contextlib.suppress(Exception):
        (Path(folder) / COMPACT_SUMMARY_HEAD_NAME).unlink()


def build_head(summary: str, full_path: Path, pointers: str) -> str:
    """The head text: pointers, then the summary whole or cut, within the cap."""
    total = len(summary.encode("utf-8"))
    head = f"{pointers}\n## Summary\n"
    budget = COMPACT_SUMMARY_HEAD_MAX_BYTES - len(head.encode("utf-8"))
    if total <= budget:
        return head + summary
    # The shown count never has more digits than the total, so a trailer
    # measured with the total twice is an upper bound on the real one.
    budget -= len(_trailer(total, total, full_path, True).encode("utf-8"))
    shown, at_line = _cut(summary, max(budget, 0))
    shown_bytes = len(shown.encode("utf-8"))
    if shown and not shown.endswith("\n"):
        shown += "\n"
    return head + shown + _trailer(shown_bytes, total, full_path, at_line)


def _cut(summary: str, budget: int) -> "tuple[str, bool]":
    """The longest run of whole lines within budget bytes; mid-line only when no line fits."""
    kept, used = [], 0
    for line in summary.splitlines(keepends=True):
        size = len(line.encode("utf-8"))
        if used + size > budget:
            break
        kept.append(line)
        used += size
    if kept:
        return "".join(kept), True
    # budget - 1 leaves room for the newline build_head adds after a mid-line cut.
    return summary.encode("utf-8")[: max(budget - 1, 0)].decode("utf-8", "ignore"), False


def _trailer(shown: int, total: int, full_path: Path, at_line: bool) -> str:
    where = "at a line boundary" if at_line else "mid-line (no line break fits the cap)"
    return (
        f"[Summary cut {where}: this head shows {shown} of {total} bytes. "
        f"Full text: {full_path} (once processed, archived beside it as "
        f"compact-summary-<timestamp>.txt).]\n"
    )


def pointer_block(folder: Path) -> str:
    """Open tasks and worktrees read from disk now, at most POINTER_MAX_BYTES."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"## Pointers rebuilt from disk at {stamp} (disk is authoritative; re-read task files)"]
    team, project_dir = _session_identity(folder)
    tasks = _open_tasks(team) if team else []
    lines.append(f"Team: {_clean(team) or 'unknown'}. Open tasks: {len(tasks)}.")
    tail = [f"Project: {_clean(project_dir)}"] if project_dir else []
    tail += [f"- worktree {path} [{branch}]" for path, branch in _worktrees(project_dir)]
    used = sum(len(line.encode("utf-8")) + 1 for line in lines + tail)
    for index, task in enumerate(tasks):
        line = _task_line(task)
        size = len(line.encode("utf-8")) + 1
        if used + size > POINTER_MAX_BYTES - 80:
            lines.append(f"- ... {len(tasks) - index} more open tasks: run TaskList")
            break
        lines.append(line)
        used += size
    return "\n".join(lines + tail) + "\n"


def _session_identity(folder: Path) -> "tuple[str, str]":
    """(team_name, project_dir) for the session folder; ("", "") when unknown."""
    team, project_dir = "", ""
    with contextlib.suppress(Exception):
        context = json.loads((folder / "pact-session-context.json").read_text(encoding="utf-8"))
        if isinstance(context, dict):
            team = str(context.get("team_name") or "")
            project_dir = str(context.get("project_dir") or "")
    # The persisted team name can trail the platform's; pact_context aligns it
    # when this process's context is the folder's own session.
    with contextlib.suppress(Exception):
        from . import pact_context

        if pact_context.get_session_dir() == str(folder):
            team = pact_context.get_team_name() or team
            project_dir = pact_context.get_project_dir() or project_dir
    return team, project_dir


def _open_tasks(team: str) -> list:
    from .task_utils import iter_team_task_jsons

    tasks = [t for t in iter_team_task_jsons(team) if t.get("status") in _OPEN_STATUSES]
    return sorted(tasks, key=lambda t: (len(str(t.get("id", ""))), str(t.get("id", ""))))


def _task_line(task: dict) -> str:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    handoff = metadata.get("handoff")
    parts = [f"- #{_clean(task.get('id'))} {_clean(task.get('status'))}",
             f"owner={_clean(task.get('owner')) or 'none'}"]
    rev = handoff_rev(handoff)
    if rev is not None:
        parts.append(f"handoff rev {rev}")
    open_amends = [key for key, _ in missing_dispositions(amendments(metadata), handoff or {})]
    if open_amends:
        parts.append("undisposed " + ",".join(open_amends))
    return " ".join(parts)[:FIELD_CHARS] + f" | {_clean(task.get('subject'), SUBJECT_CHARS)}"


def _worktrees(project_dir: str) -> "list[tuple[str, str]]":
    if not project_dir or not Path(project_dir).is_dir():
        return []
    from .git_helpers import run_git

    result = run_git(["-C", project_dir, "worktree", "list", "--porcelain"], timeout=3)
    if result is None or result.returncode != 0:
        return []
    found, path = [], None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
            found.append([path, "detached"])
        elif line.startswith("branch ") and found:
            found[-1][1] = line[len("branch "):].removeprefix("refs/heads/")
    return [(_clean(p), _clean(b, 80)) for p, b in found[:WORKTREES_MAX]]


def _clean(value, limit: int = FIELD_CHARS) -> str:
    """One line of agent-written text, at most limit characters: control
    characters and line breaks become a space, so a field cannot forge a line."""
    return _CONTROL.sub(" ", str(value)).strip()[:limit] if value is not None else ""


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="compact-summary.tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
