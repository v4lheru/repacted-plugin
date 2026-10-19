"""
Location: pact-plugin/hooks/shared/ops/reject.py
Summary: `repact reject`, the lead's judgement rejection of a stored
HANDOFF, and `rejection`, the one record shape accept's fact rejection shares: ONE task write
of metadata.handoff_rejection {rev, by, op_id, reason, corrections, at, since, revision_number}
with metadata.revision_number + 1, then the task-log reject and workflow state, then the line
that resumes the coder.
Used by: bin/_repact_ops.py (run), shared/ops/accept.py (rejection, resume_line).

The record keeps the fields today's readers check (amendments.rejection_shape_problem, the
bridge's rejection_rev, repact-wait's open-rejection test). A teachback is not rejected here:
the lead sends a correction with `repact amend --kind correction` and the coder resubmits.
The rejection limit and the park live in accept; attended, a rejection clears a park.
Unattended, a parked task is refused: the park is the owner's question, so the
lead cannot answer it by rejecting; the owner turns the mode off at their terminal (or decides
the parked row from repact decide) and the lead rejects then. Every stored
rejection (this op's, and accept's fact rejection) lands a `rejection` side row in ops.jsonl:
the third-rejection park counts those rows (accept.rejections), not metadata.revision_number.
"""

from __future__ import annotations

from shared import amendments as AM

from . import OpError, Result, frame, records, sides

REASON_MAX = AM.REJECTION_REASON_MAX
CORRECTIONS_MAX = AM.REJECTION_CORRECTIONS_MAX
CORRECTION_MAX = AM.REJECTION_CORRECTION_MAX
REJECTION_SIDE = "rejection"


def stored_handoff(task: dict) -> dict:
    """The task's HANDOFF with its rev, or OpError ops.wrong_state."""
    handoff = records.metadata(task).get("handoff")
    if not isinstance(handoff, dict) or AM.handoff_rev(handoff) is None:
        raise OpError("ops.wrong_state", [f"task #{task.get('id')} holds no submitted HANDOFF"])
    return handoff


def rejection(meta: dict, rev: int, by: str, op_id: str, reason: str, corrections: list) -> dict:
    revision = meta.get("revision_number")
    revision = revision if type(revision) is int and revision >= 1 else 1
    at = records.now()
    return {"rev": rev, "by": by, "op_id": op_id, "reason": reason[:REASON_MAX],
            "corrections": [line[:CORRECTION_MAX] for line in corrections[:CORRECTIONS_MAX]] or [reason[:200]],
            "at": at, "since": at, "revision_number": revision}


def apply_rejection(current: dict, record: dict) -> None:
    current["metadata"]["handoff_rejection"] = record
    current["metadata"].pop("parked", None)     # the owner decided: the inbox question is answered
    current["metadata"]["revision_number"] = record["revision_number"] + 1


def resume_line(dispatched: dict, task_id: str, text: str) -> str:
    return (f"Next: SendMessage(to=\"{dispatched.get('name')}\", message=\"#{task_id}: {text} "
            f"Read it with repact inspect {task_id} --key rejection, fix, then repact submit.\")")


def rejection_sides(ctx: records.Context, task_id: str, dispatched: dict, record: dict) -> list:
    worktree = dispatched.get("worktree")
    return [
        ("task_log", lambda: sides.task_log(ctx, task_id, ctx.task(task_id), {"handoff_rejection": record},
                                            ["reject"], worktree)),
        ("workflow", lambda: sides.workflow(ctx, task_id, worktree)),
        (REJECTION_SIDE, lambda: None),           # the count's record: its row is the write
    ]


def run(ctx: records.Context, task_id: str, reason: str) -> Result:
    frame.check("reject", ctx.caller)
    if not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_MAX:
        raise OpError("ops.invalid_input", [f"--reason must be 1-{REASON_MAX} characters"])
    task = ctx.task(task_id)
    dispatched = records.dispatched(task)
    handoff = stored_handoff(task)
    rev = AM.handoff_rev(handoff)
    op_id = records.op_id_of("reject", task_id, records.digest({"reason": reason, "rev": rev}))
    meta = records.metadata(task)
    if meta.get("last_op") != op_id:
        if task.get("status") != "in_progress":
            raise OpError("ops.wrong_state", [f"task #{task_id} is {task.get('status')}, not in_progress"])
        if (meta.get("handoff_rejection") or {}).get("rev") == rev:
            raise OpError("ops.wrong_state", [f"rev {rev} on #{task_id} is already rejected; wait for a "
                                              "new submit"])
        if meta.get("parked") and records.unattended(ctx):
            raise OpError("ops.wrong_state", [f"#{task_id} is parked for the owner and this session is "
                                              "unattended, so a rejection cannot answer the owner's question; "
                                              "the owner decides it from repact decide"])
    record = rejection(meta, rev, "lead", op_id, reason.strip(), [reason.strip()])
    records.execute(ctx, "reject", task_id, records.digest({"reason": reason, "rev": rev}),
                    lambda current: apply_rejection(current, record),
                    rejection_sides(ctx, task_id, dispatched, record))
    return Result(task_id=task_id, lines=[f"Rejected #{task_id} rev {rev} ({op_id}).",
                                          resume_line(dispatched, task_id, "your HANDOFF was rejected.")])
