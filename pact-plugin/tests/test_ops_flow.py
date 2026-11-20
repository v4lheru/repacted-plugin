"""
Location: pact-plugin/tests/test_ops_flow.py
Summary: One scenario per workflow operation on a temp repo and
task store: dispatch makes the task, worktree and brief; submit stores the teachback (auto or
two-phase) and the HANDOFF; accept closes on passing facts and rejects on failing ones; reject
and amend store their records; and the frame check keeps a coder off the lead's verbs.
Used by: pytest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixtures.ops_env import CODER, REAL_VERDICT, World, handoff, ops_env, teachback  # noqa: F401  # fixture
from shared.ops import OpError, accept, brief, submit


def _submitted(world: World, **extra) -> str:
    task_id = world.dispatch()
    submit.teachback(world.coder(), task_id, teachback())
    submit.handoff(world.coder(), task_id, handoff(world.worktree(task_id), **extra))
    return task_id


def test_dispatch_makes_the_task_worktree_and_brief(ops_env: World):
    result_id = ops_env.dispatch()
    task = ops_env.task(result_id)
    meta = task["metadata"]["dispatch"]
    assert task["status"] == "in_progress" and task["owner"] == CODER
    assert meta["teachback"] == "auto" and meta["variety"]["total"] == 4
    assert Path(meta["worktree"]).is_dir() and meta["branch"] == "fix/parser"
    assert task["metadata"]["last_op"] == meta["op_id"]
    paths = brief.paths(ops_env.session_dir, result_id)
    assert all(path.is_file() for path in paths.values())
    assert f"{brief.launcher()} submit {result_id}" in paths["brief"].read_text()
    assert json.loads(paths["handoff"].read_text())["produced"]


def test_dispatch_at_variety_ten_is_two_phase(ops_env: World):
    task_id = ops_env.dispatch(variety=(3, 3, 2, 2))
    assert ops_env.task(task_id)["metadata"]["dispatch"]["teachback"] == "two_phase"


def test_auto_teachback_is_accepted_in_the_same_write(ops_env: World):
    task_id = ops_env.dispatch()
    result = submit.teachback(ops_env.coder(), task_id, teachback())
    assert ops_env.task(task_id)["metadata"]["teachback"]["accepted"]["by"] == "auto"
    assert "Accepted automatically" in result.lines[0]


def test_two_phase_teachback_waits_for_the_lead(ops_env: World):
    task_id = ops_env.dispatch(variety=(3, 3, 2, 2))
    given = dict(teachback(), reasoning_reconstruction={
        "decision_attribution": "the lead chose the alias", "assumption_trace": "callers use the flag",
        "contingency_clause": "if not, drop the alias"})
    result = submit.teachback(ops_env.coder(), task_id, given)
    assert ops_env.task(task_id)["metadata"]["teachback"]["accepted"] is None
    assert "TEACHBACK SUBMITTED" in result.lines[-1]
    with pytest.raises(OpError) as refused:
        submit.handoff(ops_env.coder(), task_id, handoff(ops_env.worktree(task_id)))
    assert refused.value.rule == "ops.wrong_state"
    accept.run(ops_env.ctx(), task_id)
    assert ops_env.task(task_id)["metadata"]["teachback"]["accepted"]["by"] == "lead"


def test_submit_handoff_stores_rev_one_and_repairs_a_relative_path(ops_env: World):
    task_id = ops_env.dispatch()
    submit.teachback(ops_env.coder(), task_id, teachback())
    given = handoff(ops_env.worktree(task_id), produced=["parser.py: handle the empty input"], rev=99)
    result = submit.handoff(ops_env.coder(), task_id, given)
    stored = ops_env.task(task_id)["metadata"]["handoff"]
    assert stored["rev"] == 1 and result.exit_code == 0
    assert stored["produced"][0].startswith(str(Path(ops_env.worktree(task_id)).resolve()))




def test_a_rejection_prints_the_verdict_notes(ops_env: World, monkeypatch):
    """Why each run was skipped reaches the lead's screen on a rejection too, not only on a pass."""
    task_id = _submitted(ops_env)
    notes = [f"run 2026-11-17T10000{n}Z-1 does not count: why {n}" for n in range(5)]
    monkeypatch.setattr(EV, "verdict", lambda *a, **k: {"problems": ["no passing run [rule: evidence.no_run]"],
                                                        "notes": list(notes)})
    result = accept.run(ops_env.ctx(), task_id)
    assert result.exit_code == 1 and result.lines[0].startswith("Not accepted")
    shown = [line for line in result.lines if line.startswith("  note: ")]
    assert shown == [f"  note: {note}" for note in notes[:accept.SHOWN]], result.lines
    stored = ops_env.task(task_id)["metadata"]["handoff_rejection"]
    assert stored["notes"] == notes
