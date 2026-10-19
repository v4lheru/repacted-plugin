"""
Location: pact-plugin/hooks/shared/ops/amend.py
Summary: `repact amend`: the lead's ruling, correction or scope
change after dispatch, as metadata.amend_<n> in ONE locked write (the number is taken under
the lock, so two amends never share one), then workflow state, the line that tells the coder and, under Agent Teams, the wake line. The coder disposes of it in handoff.amendments; submit and accept refuse until it does.
Used by: bin/_repact_ops.py.

The op id is digested from the text and kind, so an identical amendment sent twice is stored
once (a known limit: to repeat a ruling on purpose, word it differently).
"""

from __future__ import annotations

from shared import amendments as AM
from shared.payload_digest import payload_digest

from . import OpError, Result, frame, records, sides

KINDS = tuple(kind for kind in AM.KINDS if kind != AM.COMMIT)
DEFAULT_KIND = "ruling"
TEXT_MAX = AM.RELAY_TEXT_CHARS


def run(ctx: records.Context, task_id: str, text: str, kind: str = DEFAULT_KIND) -> Result:
    frame.check("amend", ctx.caller)
    problems = []
    if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
        problems.append(f"the amendment text must be 1-{TEXT_MAX} characters")
    if kind not in KINDS:
        problems.append(f"--kind must be one of {', '.join(KINDS)}")
    if problems:
        raise OpError("ops.invalid_input", problems)
    task = ctx.task(task_id)
    dispatched = records.dispatched(task)
    args = records.digest({"text": text.strip(), "kind": kind})
    op_id = records.op_id_of("amend", task_id, args)
    if records.metadata(task).get("last_op") != op_id and task.get("status") != "in_progress":
        raise OpError("ops.wrong_state", [f"task #{task_id} is {task.get('status')}; amend an open task"])
    numbered = []

    def mutate(current: dict) -> None:
        number = AM.next_number(current["metadata"])
        current["metadata"][f"{AM.AMEND_PREFIX}{number}"] = AM.make_amendment(text.strip(), kind, records.now(),
                                                                              author=AM.LEAD)
        numbered.append(number)

    records.execute(ctx, "amend", task_id, args, mutate, [
        ("workflow", lambda: sides.workflow(ctx, task_id, dispatched.get("worktree"))),
    ])
    stored = ctx.task(task_id)
    key = f"{AM.AMEND_PREFIX}{numbered[0]}" if numbered else _stored_key(stored, text.strip())
    digest = payload_digest(records.metadata(stored).get(key)) or "unavailable"
    return Result(task_id=task_id, lines=[
        f"Amended #{task_id}: {key} ({kind}, {op_id}).",
        f"Next: SendMessage(to=\"{dispatched.get('name')}\", message=\"#{task_id} {key} ({kind}): read it with "
        f"repact inspect {task_id} --key {key} and dispose of it in handoff.amendments.\")",
        f"Wake (Agent Teams): {AM.wake_line(dispatched.get('name') or '', task_id, 'amended', digest)}"])


def _stored_key(task: dict, text: str) -> str:
    """The amend_<n> holding `text` (a rerun after the write landed)."""
    for key, value in AM.amendments(records.metadata(task)).items():
        if value.get("text") == text:
            return key
    return f"{AM.AMEND_PREFIX}?"
