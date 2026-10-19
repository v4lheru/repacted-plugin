"""
Location: pact-plugin/tests/test_ops_submit_inputs.py
Summary: Hostile input at `repact submit`, the one write a coder
controls: a bigint rev, NaN and Infinity, lookalike and control-character keys, a 1 MB field, a
`../` produced path and an absolute one outside the worktree. Each is refused in one message
with nothing written, or (rev) ignored; the CLI's strict reader refuses what JSON may spell.
Used by: pytest.
"""

from __future__ import annotations

import pytest

from fixtures.ops_env import World, handoff, ops_env, teachback  # noqa: F401  # fixture
from shared.ops import OpError, submit


@pytest.fixture
def open_task(ops_env: World) -> str:
    task_id = ops_env.dispatch()
    submit.teachback(ops_env.coder(), task_id, teachback())
    return task_id


def _refused(world: World, task_id: str, given) -> OpError:
    before = world.task(task_id)
    with pytest.raises(OpError) as refused:
        submit.handoff(world.coder(), task_id, given)
    assert refused.value.rule == "ops.invalid_input"
    assert world.task(task_id) == before
    return refused.value


def test_a_bigint_rev_is_ignored_and_the_cli_assigns_one(ops_env: World, open_task):
    given = handoff(ops_env.worktree(open_task), rev=10 ** 4000)
    submit.handoff(ops_env.coder(), open_task, given)
    assert ops_env.task(open_task)["metadata"]["handoff"]["rev"] == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_a_non_finite_number_is_refused(ops_env: World, open_task, value):
    _refused(ops_env, open_task, handoff(ops_env.worktree(open_task), decisions=[value]))


@pytest.mark.parametrize("key", ["\uff30roduced", "PRODUCED", "produced\u200b", "integra\ntion"])
def test_a_lookalike_or_control_key_is_refused(ops_env: World, open_task, key):
    given = handoff(ops_env.worktree(open_task))
    given[key] = given.pop("produced") if key.casefold().startswith("p") else "x"
    _refused(ops_env, open_task, given)


def test_a_one_megabyte_field_is_refused(ops_env: World, open_task):
    _refused(ops_env, open_task, handoff(ops_env.worktree(open_task), reasoning_chain="x" * (1024 * 1024)))


@pytest.mark.parametrize("path", ["../../../etc/passwd", "/etc/passwd", "~/.ssh/id_rsa"])
def test_a_produced_path_outside_the_worktree_is_refused(ops_env: World, open_task, path):
    error = _refused(ops_env, open_task, handoff(ops_env.worktree(open_task), produced=[f"{path}: x"]))
    assert any("outside the worktree" in line for line in error.problems)


def test_every_problem_is_listed_in_one_refusal(ops_env: World, open_task):
    given = handoff(ops_env.worktree(open_task), produced=["/etc/passwd: x"], uncertainty=["maybe broken"])
    del given["integration"]
    assert len(_refused(ops_env, open_task, given).problems) >= 3
