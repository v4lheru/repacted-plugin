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


def test_an_unknown_env_word_falls_through_and_is_named(work_tree):
    chosen = execution.decide({execution.ENV: "swarm", **FLAG_ON}, cwd=str(work_tree))
    assert chosen["mode"] == "subagents" and "'swarm'" in chosen["why"]


def test_teams_without_the_agent_teams_flag_runs_subagents(work_tree):
    chosen = execution.decide({execution.ENV: "teams"}, cwd=str(work_tree))
    assert chosen["mode"] == "subagents" and execution.TEAMS_FLAG in chosen["why"]


def test_the_record_is_decided_once_per_session(tmp_path, work_tree):
    first = execution.ensure(tmp_path, {execution.ENV: "teams", **FLAG_ON}, cwd=str(work_tree))
    later = execution.ensure(tmp_path, {execution.ENV: "subagents"}, cwd=str(work_tree))
    assert first["mode"] == later["mode"] == "teams"
    assert execution.current(tmp_path, {}, cwd=str(work_tree)) == "teams"


def test_a_bad_record_is_not_trusted(tmp_path, work_tree):
    (tmp_path / execution.FILE_NAME).write_text('{"mode": "everything"}')
    assert execution.read(tmp_path) is None
    assert execution.current(tmp_path, {}, cwd=str(work_tree)) == "subagents"


def test_teams_with_no_team_file_downgrades_once(tmp_path, monkeypatch, work_tree):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    env = {execution.ENV: "teams", **FLAG_ON}
    record, line = execution.settle_for_team(tmp_path, "session-ab12", env, cwd=str(work_tree))
    assert record["mode"] == "subagents" and record["downgraded_from"] == "teams"
    assert line and "subagents" in line and "teams/session-ab12/config.json" in line
    again, line = execution.settle_for_team(tmp_path, "session-ab12", env, cwd=str(work_tree))
    assert again["mode"] == "subagents" and line is None
    assert execution.read(tmp_path)["downgraded_from"] == "teams"


def test_teams_with_a_team_file_stays_teams(tmp_path, monkeypatch, work_tree):
    config = tmp_path / "config"
    (config / "teams" / "session-ab12").mkdir(parents=True)
    (config / "teams" / "session-ab12" / "config.json").write_text("{}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    record, line = execution.settle_for_team(tmp_path, "session-ab12",
                                             {execution.ENV: "teams", **FLAG_ON}, cwd=str(work_tree))
    assert record["mode"] == "teams" and line is None


def _dispatch_lines(world: World, name: str) -> list:
    from shared.ops import dispatch as dispatch_op
    return dispatch_op.run(world.ctx(), name=name, agent="pact-backend-coder", subject=name,
                           mission="Fix the empty-input case.", variety=[1, 1, 1, 1], kind="fix",
                           repo=str(world.repo), branch=f"fix/{name}").lines


def test_dispatch_downgrades_and_prints_the_line_once(ops_env: World, monkeypatch):
    monkeypatch.setenv(execution.ENV, "teams")
    monkeypatch.setenv(execution.TEAMS_FLAG, "1")
    first = _dispatch_lines(ops_env, "coder-one")
    assert sum("downgraded" in line for line in first) == 1
    assert execution.read(ops_env.session_dir)["downgraded_from"] == "teams"
    second = _dispatch_lines(ops_env, "coder-two")
    assert not any("downgraded" in line for line in second)
    assert all("team_name" not in line for line in first + second)
    tasks = list(ops_env.store.iter_tasks(TEAM))
    assert {t["metadata"]["dispatch"]["mode"] for t in tasks} == {"subagents"}


def test_dispatch_in_teams_mode_names_the_team(ops_env: World, monkeypatch):
    team_dir = Path(ops_env.root / "config" / "teams" / TEAM)
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text("{}")
    monkeypatch.setenv(execution.ENV, "teams")
    monkeypatch.setenv(execution.TEAMS_FLAG, "1")
    lines = _dispatch_lines(ops_env, "coder-one")
    assert any(f'team_name="{TEAM}"' in line for line in lines)
