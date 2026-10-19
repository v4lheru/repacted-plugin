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
from shared.ops import OpError, accept, amend, brief, frame, reject, submit
from shared.ops import evidence as EV


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


def test_accept_closes_the_task_on_passing_facts(ops_env: World):
    task_id = _submitted(ops_env)
    result = accept.run(ops_env.ctx(), task_id)
    task = ops_env.task(task_id)
    assert result.exit_code == 0 and task["status"] == "completed"
    assert task["metadata"]["acceptance"]["rev"] == 1


def test_accept_turns_failing_facts_into_a_rejection(ops_env: World, monkeypatch):
    task_id = _submitted(ops_env)
    monkeypatch.setattr(EV, "verdict", lambda *a, **k: {"problems": ["no passing tcount run"], "notes": []})
    result = accept.run(ops_env.ctx(), task_id)
    task = ops_env.task(task_id)
    assert result.exit_code == 1 and task["status"] == "in_progress"
    assert task["metadata"]["handoff_rejection"]["by"] == "accept"
    assert task["metadata"]["revision_number"] == 2


def test_accept_judges_with_the_real_evidence_check(ops_env: World, monkeypatch):
    """No stub: evidence_check sees no tcount run for the task and accept refuses the close."""
    monkeypatch.setattr(EV, "verdict", REAL_VERDICT)
    task_id = ops_env.dispatch()
    submit.teachback(ops_env.coder(), task_id, teachback())
    worktree = Path(ops_env.worktree(task_id))
    (worktree / "parser.py").write_text("def parse(text):\n    return text or ''\n")
    submit.handoff(ops_env.coder(), task_id, handoff(str(worktree)))
    result = accept.run(ops_env.ctx(), task_id)
    assert result.exit_code == 1
    assert ops_env.task(task_id)["status"] == "in_progress"


def test_reject_stores_the_reason_and_bumps_the_revision(ops_env: World):
    task_id = _submitted(ops_env)
    reject.run(ops_env.ctx(), task_id, "The empty input still raises.")
    meta = ops_env.task(task_id)["metadata"]
    assert meta["handoff_rejection"]["reason"] == "The empty input still raises."
    assert meta["handoff_rejection"]["rev"] == 1 and meta["revision_number"] == 2
    with pytest.raises(OpError) as again:
        reject.run(ops_env.ctx(), task_id, "A second reason for the same rev.")
    assert again.value.rule == "ops.wrong_state"


def test_amend_numbers_and_submit_requires_its_disposition(ops_env: World):
    task_id = ops_env.dispatch()
    submit.teachback(ops_env.coder(), task_id, teachback())
    amend.run(ops_env.ctx(), task_id, "Keep the old flag as an alias.")
    amend.run(ops_env.ctx(), task_id, "Log the empty input once.", "scope")
    meta = ops_env.task(task_id)["metadata"]
    assert meta["amend_1"]["text"] == "Keep the old flag as an alias." and meta["amend_2"]["kind"] == "scope"
    with pytest.raises(OpError) as refused:
        submit.handoff(ops_env.coder(), task_id, handoff(ops_env.worktree(task_id)))
    assert any("amend_1" in line for line in refused.value.problems)
    given = handoff(ops_env.worktree(task_id), amendments={"amend_1": "applied: parser.py", "amend_2": "ack"})
    assert submit.handoff(ops_env.coder(), task_id, given).exit_code == 0


def test_submit_refuses_a_handoff_that_leaves_a_carried_ref_undisposed(ops_env: World):
    task_id = ops_env.dispatch(refs=["T-0123abcd"])
    submit.teachback(ops_env.coder(), task_id, teachback())
    with pytest.raises(OpError) as refused:
        submit.handoff(ops_env.coder(), task_id, handoff(ops_env.worktree(task_id)))
    assert any("T-0123abcd" in line for line in refused.value.problems)
    given = handoff(ops_env.worktree(task_id), refs={"T-0123abcd": "done: parser.py"})
    assert submit.handoff(ops_env.coder(), task_id, given).exit_code == 0


@pytest.mark.parametrize("op", ["accept", "reject", "amend"])
def test_a_coder_cannot_run_the_leads_verbs(ops_env: World, op):
    task_id = _submitted(ops_env)
    before = ops_env.task(task_id)
    run = {"accept": lambda ctx: accept.run(ctx, task_id),
           "reject": lambda ctx: reject.run(ctx, task_id, "no"),
           "amend": lambda ctx: amend.run(ctx, task_id, "a ruling")}[op]
    with pytest.raises(OpError) as refused:
        run(ops_env.coder())
    assert refused.value.rule == "ops.lead_only"
    assert ops_env.task(task_id) == before


def test_only_the_dispatched_coder_may_submit(ops_env: World):
    task_id = ops_env.dispatch()
    for caller in (frame.Caller(frame.LEAD), frame.Caller(frame.CODER, "coder-2")):
        with pytest.raises(OpError) as refused:
            submit.teachback(ops_env.ctx(caller), task_id, teachback())
        assert refused.value.rule == "ops.not_owner"
    assert "teachback" not in ops_env.task(task_id)["metadata"]
