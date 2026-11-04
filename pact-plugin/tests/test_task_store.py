"""tests/test_task_store.py: the TaskStore adapter's path rules and its one writer.

Adversarial rows at the boundary every caller used to guard itself: a traversal segment,
a symlinked team dir or task file, a non-object file, a missing file on update, and the
watch key moving on a write.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from shared.task_store import TaskRef, TaskStore, read_task_json


@pytest.fixture
def root(tmp_path) -> Path:
    base = tmp_path / "config" / "tasks"
    (base / "team-a").mkdir(parents=True)
    (base / "team-a" / "1.json").write_text(json.dumps({"id": "1", "status": "pending"}))
    (base / "team-a" / "2.json").write_text(json.dumps({"id": "2", "status": "completed"}))
    (base / "team-a" / ".hidden.json").write_text(json.dumps({"id": "99"}))
    (base / "team-a" / "list.json").write_text("[1, 2, 3]")
    (base / "team-b").mkdir()
    (base / "team-b" / "1.json").write_text(json.dumps({"id": "1", "status": "in_progress"}))
    return base


def test_list_get_exists_and_teams_holding(root):
    store = TaskStore(root)
    assert store.teams() == ["team-a", "team-b"]
    assert [t["id"] for t in store.list("team-a")] == ["1", "2"]  # no dotfile, no non-object
    assert store.get(TaskRef("team-a", "2")) == {"id": "2", "status": "completed"}
    assert store.get(TaskRef("team-a", "list")) is None
    assert store.exists(TaskRef("team-a", "1")) and not store.exists(TaskRef("team-a", "7"))
    assert store.teams_holding("1") == ["team-a", "team-b"]
    assert store.teams_holding("2") == ["team-a"]


@pytest.mark.parametrize("team, task_id", [
    ("..", "1"),])
def test_an_unsafe_segment_builds_no_path(root, team, task_id):
    store = TaskStore(root)
    ref = TaskRef(team, task_id)
    assert store.path(ref) is None
    assert store.get(ref) is None and not store.exists(ref) and store.watch_key(ref) is None
    assert store.update(ref, lambda task: True) is False


def test_a_symlinked_team_dir_and_task_file_are_skipped(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "5.json").write_text(json.dumps({"id": "5"}))
    os.symlink(outside, root / "linked")
    os.symlink(outside / "5.json", root / "team-a" / "5.json")
    store = TaskStore(root)
    assert "linked" not in store.teams()
    assert store.team_dir("linked") is None and store.list("linked") == []
    assert store.get(TaskRef("team-a", "5")) is None
    assert not store.exists(TaskRef("team-a", "5"))
    assert store.update(TaskRef("team-a", "5"), lambda task: True) is False
    assert (outside / "5.json").read_text() == json.dumps({"id": "5"})


def test_update_writes_in_place_never_creates_and_moves_the_watch_key(root):
    store = TaskStore(root)
    ref = TaskRef("team-a", "1")
    before = store.watch_key(ref)

    def mutate(task):
        task["status"] = "in_progress"
        return True

    assert store.update(ref, mutate) is True
    assert store.get(ref)["status"] == "in_progress"
    assert store.watch_key(ref) != before
    assert store.update(TaskRef("team-a", "7"), mutate) is False
    assert not (root / "team-a" / "7.json").exists()
    assert store.update(TaskRef("team-a", "list"), mutate) is False  # not an object
    assert store.update(ref, lambda task: False) is False  # mutate declined


