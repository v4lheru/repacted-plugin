"""
Location: pact-plugin/hooks/shared/ops/sides.py
Summary: The side writes the ops share : a session-journal
row, the workflow-state refresh and the project task-log bridge. records.run_sides runs each
once per op id; each one here is also harmless twice.
Used by: shared/ops/{dispatch,submit,accept,reject,amend}.py.
"""

from __future__ import annotations

from typing import Optional

from .records import Context


def main_checkout(worktree: Optional[str]) -> Optional[str]:
    """The main checkout holding `worktree` (where the task log and workflow state live)."""
    if not worktree:
        return None
    from shared.project_tasks import find_main_checkout

    main = find_main_checkout(worktree)
    return str(main) if main else None


def journal(ctx: Context, event_type: str, **fields) -> bool:
    """One journal row in the session dir; False when the journal refuses it (fail-open)."""
    from shared import session_journal as sj

    return sj.append_event(sj.make_event(event_type, **fields), session_dir=str(ctx.session_dir))


def workflow(ctx: Context, task_id: str, worktree: Optional[str]) -> None:
    """Refresh the resume screen's state for the task (derived, so a repeat is a no-op)."""
    from shared import workflow_state

    project = main_checkout(worktree) or ""
    workflow_state.update(str(ctx.session_dir), ctx.team, project, [task_id])


def task_log(ctx: Context, task_id: str, task: dict, written: dict, actions: list,
             worktree: Optional[str]) -> Optional[str]:
    """The task-log bridge (link, reject or accept) for the lead's own op. A repo with no task
    log does nothing. Returns what the bridge did."""
    from shared.task_log import bridge, write as W

    if not worktree:
        return None
    try:
        store = W.Store.locate(worktree, by=W.with_team("bridge", ctx.session_id or None))
    except W.NoLog:
        return "no-log"
    return bridge.apply(store, ctx.team, task_id, task, written, actions, ctx.session_id or None)
