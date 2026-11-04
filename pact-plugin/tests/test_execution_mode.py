"""
Location: pact-plugin/tests/test_execution_mode.py
Summary: The execution mode: one test per precedence row (the env,
the team policy, the default), the Agent Teams flag that teams needs, the record kept once per
session, and the downgrade at the first dispatch when no team file exists (said once).
Used by: pytest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixtures.ops_env import TEAM, World, ops_env  # noqa: F401  # fixture
from shared import execution, team_policy

FLAG_ON = {execution.TEAMS_FLAG: "1"}


@pytest.fixture
def work_tree(tmp_path):
    root = tmp_path / "tree"
    (root / ".git").mkdir(parents=True)
    team_policy.reset()
    yield root
    team_policy.reset()


def _policy(root: Path, document: dict) -> None:
    (root / ".repacted").mkdir(exist_ok=True)
    (root / ".repacted" / "policy.json").write_text(json.dumps(document))
    team_policy.reset()


def test_row_one_env_wins_over_the_policy(work_tree):
    _policy(work_tree, {"execution": "teams"})
    chosen = execution.decide({execution.ENV: "subagents", **FLAG_ON}, cwd=str(work_tree))
    assert chosen["mode"] == "subagents" and execution.ENV in chosen["why"]
    chosen = execution.decide({execution.ENV: "teams", **FLAG_ON}, cwd=str(work_tree))
    assert chosen["mode"] == "teams" and execution.ENV in chosen["why"]


def test_row_two_team_policy_asks_for_teams(work_tree):
    _policy(work_tree, {"execution": "teams"})
    chosen = execution.decide(dict(FLAG_ON), cwd=str(work_tree))
    assert chosen == {"mode": "teams", "why": "team policy"}


def test_row_two_policy_accepts_only_the_literal_teams(work_tree):
    _policy(work_tree, {"execution": "Teams "})
    assert execution.decide(dict(FLAG_ON), cwd=str(work_tree))["mode"] == "subagents"
    assert any("execution" in line for line in team_policy.load(str(work_tree)).ignored)


def test_row_three_the_default_is_subagents(work_tree):
    assert execution.decide(dict(FLAG_ON), cwd=str(work_tree)) == {"mode": "subagents",
                                                                    "why": "default"}


def _dispatch_lines(world: World, name: str) -> list:
    from shared.ops import dispatch as dispatch_op
    return dispatch_op.run(world.ctx(), name=name, agent="pact-backend-coder", subject=name,
                           mission="Fix the empty-input case.", variety=[1, 1, 1, 1], kind="fix",
                           repo=str(world.repo), branch=f"fix/{name}").lines


