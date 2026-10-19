"""
Location: pact-plugin/hooks/shared/ops/accept.py
Summary: `repact accept`, the lead's one call to close a step. A
teachback waiting in two_phase mode is accepted. A HANDOFF is judged by facts: the evidence
check (ops.evidence.verdict: shared/evidence_check, which also runs the acceptance criteria
pinned at dispatch), an AC disposition per pinned criterion, and the amendment dispositions.
All pass: ONE write of status completed plus metadata.acceptance (the check's statuses, runs
and tree), then the task log, journal and workflow state. Any fails:
the same write path stores a rejection (shared/ops/reject.rejection) and accept exits 1.
Used by: bin/_repact_ops.py (run); shared/ops/submit.py (ac_problems, for its preview).

Waivers and park: `--waive "<why>"` over failing facts completes the task with
metadata.evidence_waiver only when attended. With unattended mode on (shared.unattended
controls_on) it never completes: ONE write of metadata.parked {why, inbox_ref, at, rev, problems},
the task stays in_progress, its bound task-log rows are parked (status blocked) and repact decide
lists the question; exit 0. The same park replaces the third rejection of an unattended task
(two rejections stored, counted from the ops log's `rejection` rows), so no
unwatched loop runs on.

A task dispatched with --feature also gets statuses.integrated (shared/ops/integrate.task_status):
"pass" when it is the feature's only task, else "waiting", and then its task-log rows stay open
until `repact accept --feature` passes on the merged tree.

A rerun after a crash finds its own op id in metadata.last_op and only finishes the side
writes, judged by what the task file already holds, so nothing is judged or written twice.
"""

from __future__ import annotations

from shared import amendments as AM, ref_dispositions as RD

from . import OpError, Result, frame, integrate, records, reject, sides
from . import evidence as EV

SHOWN = 3
REJECTION_LIMIT = 3         # the failing accept of an unattended task that parks it
WAIVE_MAX = 300
OP_NAMES = ("accept", "accept_park", "accept_waive")


def ac_problems(task: dict, handoff: dict, team: str = "") -> list:
    """Lines for the pinned acceptance criteria the HANDOFF gives no disposition in handoff.refs.
    Whether each AC's command passed on the current inputs is the evidence check's
    (checks_passed). No pin: nothing to judge."""
    pin = records.dispatched(task).get("acceptance_pin") or {}
    given = RD.ac_dispositions(handoff) if pin else {}
    return [f"{ac}: no disposition in handoff.refs" for ac in pin if ac not in given]


def judge(ctx: records.Context, task: dict, handoff: dict) -> tuple:
    """(problems, the verdict) for the HANDOFF on the task."""
    dispatched = records.dispatched(task)
    checked = EV.verdict(task, ctx.team, handoff, dispatched.get("worktree"), ctx.session_id)
    problems = list(checked["problems"]) + ac_problems(task, handoff)
    problems += [f"amendment {key} has no disposition in handoff.amendments ({text})"
                 for key, text in AM.missing_dispositions(AM.amendments(records.metadata(task)), handoff)]
    return problems, checked


def _teachback(ctx: records.Context, task_id: str, task: dict, dispatched: dict) -> Result:
    stored = records.metadata(task).get("teachback")
    if not isinstance(stored, dict) or not stored.get("submit"):
        raise OpError("ops.wrong_state", [f"#{task_id} has no teachback to accept and no HANDOFF yet"])
    args = records.digest(stored["submit"])

    def mutate(current: dict) -> None:
        current["metadata"]["teachback"]["accepted"] = {"by": "lead", "at": records.now()}

    op_id, _wrote = records.execute(ctx, "accept_teachback", task_id, args, mutate, [
        ("workflow", lambda: sides.workflow(ctx, task_id, dispatched.get("worktree"))),
    ])
    return Result(task_id=task_id, lines=[
        f"Teachback accepted on #{task_id} ({op_id}).",
        f"Next: SendMessage(to=\"{dispatched.get('name')}\", message=\"Teachback accepted on #{task_id}. "
        "Go on with the work.\")"])


def _record(op_id: str, handoff: dict, checked: dict) -> dict:
    """metadata.acceptance: the check's statuses, the runs on the accepted tree, and that tree."""
    return {"op_id": op_id, "at": records.now(), "by": "lead", "rev": AM.handoff_rev(handoff),
            "digest": records.digest(handoff), "statuses": checked.get("statuses") or {},
            "runs": list(checked.get("runs") or [])[:20], "tree": checked.get("tree"),
            "notes": [str(note)[:300] for note in checked.get("notes") or []][:20]}


def status_line(statuses: dict) -> str:
    """What accept found, in words: "verified" only when a run tested the accepted inputs."""
    if (statuses.get("inputs_unchanged") or {}).get("state") == "pass":
        return "a passing run tested the accepted inputs"
    if (statuses.get("inputs_changed") or {}).get("state") == "no":
        return "no input file changed since dispatch, nothing was verified"
    return "no evidence status recorded"


def _pass_sides(ctx, task_id: str, dispatched: dict, handoff: dict) -> list:
    worktree = dispatched.get("worktree")
    return [
        ("task_log", lambda: sides.task_log(ctx, task_id, ctx.task(task_id),
                                            {"acceptance": records.metadata(ctx.task(task_id)).get("acceptance")},
                                            ["accept"], worktree)),
        ("journal", lambda: sides.journal(ctx, "agent_handoff", agent=dispatched.get("name"), task_id=task_id,
                                          task_subject=str(ctx.task(task_id).get("subject") or ""),
                                          handoff=handoff)),
        ("workflow", lambda: sides.workflow(ctx, task_id, worktree)),
    ]


def _accepted(task_id: str, op_id: str, handoff: dict, record: dict) -> Result:
    lines = [f"Accepted #{task_id} rev {AM.handoff_rev(handoff)} ({op_id}): "
             f"{status_line(record.get('statuses') or {})}."]
    lines += [f"  note: {note[:160]}" for note in (record.get("notes") or [])[:SHOWN]]
    lines += [line for line in [_task_log_line(task_id)] if line]
    return Result(task_id=task_id, lines=lines + [f"Next: merge the branch, or dispatch the next step; "
                                                  f"`repact inspect {task_id}` shows the receipt."])


def _task_log_line(task_id: str):
    """What the task-log side write just did for this task (bridge.acceptance_line), or None."""
    from shared.task_log import bridge
    report = bridge.last_report
    return bridge.acceptance_line(report) if report.get("task_id") == task_id else None


def _rejected(task_id: str, dispatched: dict, record: dict) -> Result:
    shown = [f"  {line[:200]}" for line in record["corrections"][:SHOWN]]
    return Result(task_id=task_id, exit_code=1, lines=[
        f"Not accepted: #{task_id} rev {record['rev']} fails {len(record['corrections'])} check(s); "
        "stored as a rejection.", *shown,
        reject.resume_line(dispatched, task_id, "accept found problems.")])


def rejections(ctx: records.Context, task_id: str, meta: dict) -> int:
    """How many rejections the task holds: the distinct accept or reject ops of this task with a
    `rejection` side row in ops.jsonl, or revision_number - 1 when that is larger. Both are
    op-owned (the session dir is protected, revision_number reserved), so neither can be reset;
    the larger wins so a task resumed in a new session keeps its count."""
    log = records.rows(ctx)
    ops = {row.get("op_id") for row in log
           if row.get("task") == task_id and row.get("op") in ("accept", "reject")}
    counted = len({row.get("op_id") for row in log
                   if row.get("side") == reject.REJECTION_SIDE and row.get("op_id") in ops})
    revision = meta.get("revision_number")
    stored = revision - 1 if type(revision) is int else 0
    return max(counted, stored)


def unattended(ctx: records.Context) -> bool:
    """records.unattended: the op's session or the caller's own, sticky once on."""
    return records.unattended(ctx)


def _waiver(waive) -> str:
    """The waiver's reason, one line, or OpError when --waive was given without one."""
    from shared.task_log.close_gate import valid_waiver
    reason = valid_waiver(waive)
    if not reason:
        raise OpError("ops.invalid_input", [f"--waive needs a reason of at least one word (at most {WAIVE_MAX} "
                                            "characters)"])
    return reason


def _park_sides(ctx, task_id: str, dispatched: dict, question: str) -> list:
    def task_log() -> object:
        from shared.task_log import bridge, write as W
        worktree = dispatched.get("worktree")
        if not worktree:
            return None
        try:
            store = W.Store.locate(worktree, by=W.with_team("bridge", ctx.session_id or None))
        except W.NoLog:
            return "no-log"
        return bridge.park(store, ctx.team, task_id, question)

    return [("task_log", task_log), ("workflow", lambda: sides.workflow(ctx, task_id, dispatched.get("worktree")))]


def _question(task_id: str, why: str, problems: list) -> str:
    return (f"Accept #{task_id} without evidence? Reason given: {why}. Problems: "
            + "; ".join(str(problem)[:200] for problem in problems[:SHOWN]))


def _park(ctx, task_id: str, dispatched: dict, handoff: dict, op: str, args: str, why: str,
          problems: list) -> Result:
    record = {"why": why, "inbox_ref": f"ptask:{ctx.team}/{task_id}", "at": records.now(),
              "rev": AM.handoff_rev(handoff), "problems": [str(p)[:300] for p in problems[:20]],
              "question": _question(task_id, why, problems)[:600]}

    def mutate(current: dict) -> None:
        current["metadata"]["parked"] = record

    op_id, _wrote = records.execute(ctx, op, task_id, args, mutate,
                                    _park_sides(ctx, task_id, dispatched, record["question"]))
    return _parked(task_id, op_id, record)


def _parked(task_id: str, op_id: str, record: dict) -> Result:
    return Result(task_id=task_id, lines=[
        f"#{task_id} parked for a human ({op_id}): unattended, it stays open: {record['why'][:160]}.",
        f"  {record['question'][:200]}",
        "Next: dispatch the next task; the owner decides this one from repact decide."])


def _waived(ctx, task_id: str, dispatched: dict, handoff: dict, args: str, why: str, checked: dict) -> Result:
    op_id = records.op_id_of("accept_waive", task_id, args)
    record = dict(_record(op_id, handoff, checked), waived=True)
    waiver = {"why": why, "by": "lead", "at": records.now(), "attended": True}

    def mutate(current: dict) -> None:
        current["status"] = "completed"
        current["metadata"]["acceptance"] = record
        current["metadata"]["evidence_waiver"] = waiver

    records.execute(ctx, "accept_waive", task_id, args, mutate, _pass_sides(ctx, task_id, dispatched, handoff))
    result = _accepted(task_id, op_id, handoff, record)
    result.lines.insert(0, f"WAIVED: {why[:200]}")
    return result


def _finish_crashed(ctx, task_id: str, task: dict, dispatched: dict, handoff: dict, op_id: str) -> Result:
    meta = records.metadata(task)
    rejected = meta.get("handoff_rejection") or {}
    if rejected.get("op_id") == op_id:
        records.finish(ctx, op_id, reject.rejection_sides(ctx, task_id, dispatched, rejected))
        return _rejected(task_id, dispatched, rejected)
    parked = meta.get("parked") or {}
    if task.get("status") != "completed" and parked:
        records.finish(ctx, op_id, _park_sides(ctx, task_id, dispatched, parked.get("question", "")))
        return _parked(task_id, op_id, parked)
    records.finish(ctx, op_id, _pass_sides(ctx, task_id, dispatched, handoff))
    return _accepted(task_id, op_id, handoff, meta.get("acceptance") or {})


def run(ctx: records.Context, task_id: str, waive=None) -> Result:
    """Accept the waiting teachback, or judge the HANDOFF. `waive` (a reason) over failing facts
    completes with a waiver when attended and parks the task when unattended."""
    frame.check("accept", ctx.caller)
    why = _waiver(waive) if waive is not None else None
    task = ctx.task(task_id)
    dispatched = records.dispatched(task)
    meta = records.metadata(task)
    if not ((meta.get("teachback") or {}).get("accepted")):
        if task.get("status") != "in_progress":
            raise OpError("ops.wrong_state", [f"task #{task_id} is {task.get('status')}, not in_progress"])
        return _teachback(ctx, task_id, task, dispatched)
    handoff = reject.stored_handoff(task)
    alone = unattended(ctx)
    base = {"rev": AM.handoff_rev(handoff), "handoff": records.digest(handoff)}
    args = records.digest(dict(base, waive=why, unattended=alone) if why else base)
    if meta.get("last_op") in [records.op_id_of(name, task_id, args) for name in OP_NAMES]:
        return _finish_crashed(ctx, task_id, task, dispatched, handoff, meta["last_op"])
    if task.get("status") != "in_progress":
        raise OpError("ops.wrong_state", [f"task #{task_id} is {task.get('status')}, not in_progress"])
    if (meta.get("handoff_rejection") or {}).get("rev") == AM.handoff_rev(handoff):
        raise OpError("ops.wrong_state", [f"rev {AM.handoff_rev(handoff)} on #{task_id} is already rejected; "
                                          "wait for a new submit"])
    if alone and (meta.get("parked") or {}).get("rev") == AM.handoff_rev(handoff):
        raise OpError("ops.wrong_state", [f"rev {AM.handoff_rev(handoff)} on #{task_id} is parked for a human; "
                                          "the owner decides it from repact decide"])
    problems, checked = judge(ctx, task, handoff)
    checked = dict(checked, statuses=dict(checked.get("statuses") or {}, **integrate.task_status(ctx, dispatched)))
    if problems and why and alone:
        return _park(ctx, task_id, dispatched, handoff, "accept_park", args, why, problems)
    if problems and why:
        return _waived(ctx, task_id, dispatched, handoff, args, why, checked)
    count = rejections(ctx, task_id, meta) if problems and alone else 0
    if count >= REJECTION_LIMIT - 1:
        limit = f"rejection limit: {count} rejections, and rev {AM.handoff_rev(handoff)} fails again"
        return _park(ctx, task_id, dispatched, handoff, "accept_park", args, limit, problems)
    op_id = records.op_id_of("accept", task_id, args)
    if problems:
        record = reject.rejection(meta, AM.handoff_rev(handoff), "accept", op_id,
                                  "accept found problems: " + "; ".join(problems)[:1500], problems)
        records.execute(ctx, "accept", task_id, args, lambda current: reject.apply_rejection(current, record),
                        reject.rejection_sides(ctx, task_id, dispatched, record))
        return _rejected(task_id, dispatched, record)
    record = _record(op_id, handoff, checked)

    def mutate(current: dict) -> None:
        current["status"] = "completed"
        current["metadata"]["acceptance"] = record

    records.execute(ctx, "accept", task_id, args, mutate, _pass_sides(ctx, task_id, dispatched, handoff))
    return _accepted(task_id, op_id, handoff, record)
