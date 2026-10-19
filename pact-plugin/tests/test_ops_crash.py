"""
Location: pact-plugin/tests/test_ops_crash.py
Summary: A crash after the task write, then a rerun, for every workflow operation: the op dies in its last side write (workflow state), the same call runs
again, and the task file was written exactly once while every side write ran exactly once.
Used by: pytest.
"""

from __future__ import annotations

import pytest

from fixtures.ops_env import World, handoff, ops_env, teachback  # noqa: F401  # fixture
from shared.ops import accept, amend, reject, sides, submit
from shared.ops import evidence as EV


class Crash(RuntimeError):
    pass


@pytest.fixture
def counted(ops_env: World, monkeypatch):
    """Counts landed task writes (create + update) and journal and task-log side calls, and,
    once armed by reset(), makes the next workflow side write raise."""
    counts = {"writes": 0, "journal": 0, "task_log": 0, "workflow_crashes": 0, "armed": True}
    store = ops_env.store
    real_update, real_create = store.update, store.create

    def update(ref, mutate):
        landed = real_update(ref, mutate)
        counts["writes"] += bool(landed)
        return landed

    def create(ref, task):
        landed = real_create(ref, task)
        counts["writes"] += bool(landed)
        return landed

    def spy(name, real):
        def call(*args, **kwargs):
            counts[name] += 1
            return real(*args, **kwargs)
        return call

    real_workflow = sides.workflow

    def workflow(*args, **kwargs):
        if counts["armed"] and counts["workflow_crashes"] == 0:
            counts["workflow_crashes"] += 1
            raise Crash("killed after the task write")
        return real_workflow(*args, **kwargs)

    monkeypatch.setattr(store, "update", update)
    monkeypatch.setattr(store, "create", create)
    monkeypatch.setattr(sides, "journal", spy("journal", sides.journal))
    monkeypatch.setattr(sides, "task_log", spy("task_log", sides.task_log))
    monkeypatch.setattr(sides, "workflow", workflow)

    def reset(armed: bool = True):
        counts.update(writes=0, journal=0, task_log=0, workflow_crashes=0, armed=armed)
    return counts, reset


def _ready(world: World, stage: str) -> str:
    """A task brought (with real, uncrashed calls) to just before the op under test."""
    task_id = world.dispatch()
    if stage in ("handoff", "accept", "reject"):
        submit.teachback(world.coder(), task_id, teachback())
    if stage in ("accept", "reject"):
        submit.handoff(world.coder(), task_id, handoff(world.worktree(task_id)))
    return task_id


def _crash_then_rerun(call, counts) -> None:
    with pytest.raises(Crash):
        call()
    call()
    assert counts["workflow_crashes"] == 1


def test_dispatch_rerun_creates_one_task_and_writes_each_side_once(ops_env: World, counted):
    counts, _reset = counted
    _crash_then_rerun(ops_env.dispatch, counts)
    tasks = [task for task in ops_env.store.iter_tasks("team-ops") if task["metadata"].get("dispatch")]
    assert len(tasks) == 1 and counts["writes"] == 1 and counts["journal"] == 1
    op_id = tasks[0]["metadata"]["dispatch"]["op_id"]
    assert all(ops_env.side_rows(op_id, side) == 1 for side in ("journal", "workflow"))


@pytest.mark.parametrize("op", ["teachback", "handoff", "accept", "accept_rejects", "reject", "amend"])
def test_rerun_after_a_crash_writes_the_task_once(ops_env: World, counted, monkeypatch, op):
    counts, reset = counted
    reset(armed=False)
    stage = {"teachback": "teachback", "handoff": "handoff", "accept_rejects": "accept"}.get(op, op)
    if op == "amend":
        stage = "teachback"
    task_id = _ready(ops_env, stage)
    if op == "accept_rejects":
        monkeypatch.setattr(EV, "verdict", lambda *a, **k: {"problems": ["no passing run"], "notes": []})
    call = {
        "teachback": lambda: submit.teachback(ops_env.coder(), task_id, teachback()),
        "handoff": lambda: submit.handoff(ops_env.coder(), task_id, handoff(ops_env.worktree(task_id))),
        "accept": lambda: accept.run(ops_env.ctx(), task_id),
        "accept_rejects": lambda: accept.run(ops_env.ctx(), task_id),
        "reject": lambda: reject.run(ops_env.ctx(), task_id, "The empty input still raises."),
        "amend": lambda: amend.run(ops_env.ctx(), task_id, "Keep the old flag."),
    }[op]
    reset()
    _crash_then_rerun(call, counts)
    meta = ops_env.task(task_id)["metadata"]
    op_id = meta["last_op"]
    assert counts["writes"] == 1
    assert ops_env.side_rows(op_id, "workflow") == 1
    assert sum(1 for row in ops_env.ops_rows() if row.get("op_id") == op_id and row.get("done")) == 1
    if op == "teachback":
        assert counts["journal"] == 1 and meta["teachback"]["accepted"]["by"] == "auto"
    if op == "accept":
        assert counts["journal"] == 1 and counts["task_log"] == 1
        assert ops_env.task(task_id)["status"] == "completed"
    if op in ("accept_rejects", "reject"):
        assert counts["task_log"] == 1 and meta["revision_number"] == 2
    if op == "amend":
        assert [key for key in meta if key.startswith("amend_")] == ["amend_1"]
