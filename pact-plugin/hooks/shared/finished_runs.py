"""
Location: pact-plugin/hooks/shared/finished_runs.py
Summary: A teammate idle on a finished unattended test run: the run ledger's tcount rows
    with no hook row joining their log (foreground runs always have one), matched to an
    in_progress task's worktree (its `Worktree:` line, else the linked worktrees its owner's
    own calls were made in) and ending at or after the task's wait anchor.
Used by: hooks/missed_wake_scan.py (find_idle_on_finished_runs, read_run_ledger,
    run_summary). Moved out of that module in the refactor core lane: a ledger read, not a
    scan of waits. Pure apart from the ledger read; never raises on a malformed row.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from shared.background_work import wait_scope_anchor
from shared.intentional_wait import validate_wait

# The ledger window this alarm reads. Wider than run_ledger's 256 KB tail: a run that
# ended hours ago, under many later runs, is the case this exists for (U3 idled ~5 h).
RUN_TAIL_BYTES = 2 * 1024 * 1024
# A foreground run's hook row lands moments after tcount's own row; a run that ended
# less than this long ago may still be its caller's, so it is not judged yet.
RUN_SETTLE_SECONDS = 60
# The dispatch line naming the task's worktree: `Worktree: /abs/path (branch ...)`, also
# spelled `YOUR WORKTREE: ...` (both measured in this session's dispatches).
_WORKTREE_LINE = re.compile(r"^[ \t]*(?:your[ \t]+)?worktree:[ \t]*(/[^\n]*?)(?=[ \t]+\(|[ \t]*[.,]?[ \t]*$)",
                            re.MULTILINE | re.IGNORECASE)
_TREE_DEPTH_MAX = 64


def task_worktree(task: dict) -> "str | None":
    """The absolute worktree path the task's description names on a `Worktree:` line, or
    None (then the owner's own calls name it: owner_worktrees)."""
    description = task.get("description")
    if not isinstance(description, str):
        return None
    match = _WORKTREE_LINE.search(description)
    return os.path.normpath(match.group(1)) if match else None


def _linked_worktree(path: str, cache: dict) -> "str | None":
    """The linked worktree holding `path` (the nearest directory whose `.git` is a FILE), or
    None for a path in a main checkout (`.git` is a directory) or in no repository. The main
    checkout is left out on purpose: the lead's own runs are made there."""
    if path in cache:
        return cache[path]
    found, here = None, path
    for _ in range(_TREE_DEPTH_MAX):
        marker = os.path.join(here, ".git")
        if os.path.isfile(marker):
            found = here
            break
        if os.path.isdir(marker) or os.path.dirname(here) == here:
            break
        here = os.path.dirname(here)
    cache[path] = found
    return found


def owner_worktrees(rows: list) -> dict:
    """Agent name -> the linked worktrees its own Bash calls were made in, from the run
    ledger's hook rows (agent_name and call_dirs label every test-run call)."""
    cache: dict = {}
    trees: dict = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("source") != "hook":
            continue
        name, dirs = row.get("agent_name"), row.get("call_dirs")
        if not isinstance(name, str) or not name or not isinstance(dirs, list):
            continue
        for path in dirs:
            if isinstance(path, str) and os.path.isabs(path):
                tree = _linked_worktree(os.path.normpath(path), cache)
                if tree:
                    trees.setdefault(name, set()).add(tree)
    return trees


def read_run_ledger() -> list:
    """The run ledger's last RUN_TAIL_BYTES as rows, oldest first; [] on any error."""
    try:
        from shared.ledger import ledger_path, parse_ledger_lines

        with open(ledger_path(create_dir=False), "rb") as handle:
            handle.seek(0, 2)
            start = max(0, handle.tell() - RUN_TAIL_BYTES)
            handle.seek(start)
            data = handle.read()
        if start:
            data = data.partition(b"\n")[2]      # the line the window starts inside is cut
        return parse_ledger_lines(data)[0]
    except Exception:
        return []


def _row_time(row: dict) -> "datetime | None":
    """A ledger row's ts (written when the run ended) as a tz-aware time, or None."""
    try:
        stamp = datetime.fromisoformat(str(row.get("ts")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _under(path: str, roots: set) -> bool:
    return any(path == root or path.startswith(root.rstrip(os.sep) + os.sep) for root in roots)


def find_idle_on_finished_runs(tasks: list, rows: list, now: "datetime | None" = None) -> list:
    """(task, ledger row) per in_progress task whose teammate waits past a finished unattended
    run. Any well-formed wait; the task's worktree; a tcount row from under that tree ending
    at or after the wait anchor, with no hook row joining its log. Newest per task; runs
    younger than RUN_SETTLE_SECONDS wait for the next prompt."""
    now = now or datetime.now(timezone.utc)
    hooked = {row.get("log_path") for row in rows if isinstance(row, dict) and row.get("source") == "hook"}
    runs = []
    for row in rows:
        if not isinstance(row, dict) or row.get("source") != "tcount":
            continue
        log_path, cwd, ended = row.get("log_path"), row.get("cwd"), _row_time(row)
        if not isinstance(log_path, str) or not log_path or log_path in hooked:
            continue
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or ended is None:
            continue
        if (now - ended).total_seconds() < RUN_SETTLE_SECONDS:
            continue
        runs.append((os.path.normpath(cwd), ended, row))
    found = []
    by_owner = None
    for task in tasks or []:
        if not isinstance(task, dict) or task.get("status") != "in_progress":
            continue
        metadata = task.get("metadata")
        wait = metadata.get("intentional_wait") if isinstance(metadata, dict) else None
        if not validate_wait(wait) or not isinstance(task.get("owner"), str) or not task["owner"]:
            continue
        anchor, _ = wait_scope_anchor(wait)
        if anchor is None:
            continue
        worktree = task_worktree(task)
        if worktree is not None:
            trees = {worktree}
        else:
            by_owner = owner_worktrees(rows) if by_owner is None else by_owner
            trees = by_owner.get(task["owner"], set())
        roots = trees | {os.path.realpath(tree) for tree in trees}
        if not roots:
            continue
        mine = [(ended, row) for cwd, ended, row in runs if ended >= anchor and _under(cwd, roots)]
        if mine:
            found.append((task, max(mine, key=lambda pair: pair[0])[1]))
    return found


def run_summary(row: dict) -> str:
    """The counts line for a ledger row: `N passed, N failed, N errors, exit N`."""
    exit_code = row.get("exit_code")
    shown_exit = exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else "?"
    if row.get("count_parsed") is not True:
        return f"no count parsed, exit {shown_exit}"
    counts = []
    for key in ("passed", "failed", "errors"):
        value = row.get(key)
        counts.append(f"{value if isinstance(value, int) and not isinstance(value, bool) else '?'} {key}")
    return ", ".join(counts) + f", exit {shown_exit}"
