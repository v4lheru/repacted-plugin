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
    ("..", "1"), ("team-a/../team-b", "1"), ("team-a", "../team-b/1"), ("team-a", "1\x00"),
    ("", "../x"), ("team a", "1"), ("team-a", ""),
])
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


def test_read_task_json_folds_the_team_and_falls_back_to_the_flat_layout(root):
    (root / "9.json").write_text(json.dumps({"id": "9"}))
    assert read_task_json("1", "TEAM-A", tasks_base_dir=str(root))["status"] == "pending"
    assert read_task_json("9", "team-a", tasks_base_dir=str(root)) == {"id": "9"}
    assert read_task_json("../9", "team-a", tasks_base_dir=str(root)) == {"id": "9"}
    assert read_task_json("x/../..", "team-a", tasks_base_dir=str(root)) == {}
    assert read_task_json("1", "../team-a", tasks_base_dir=str(root)) == {}


def test_a_missing_root_reads_as_empty(tmp_path):
    store = TaskStore(tmp_path / "nowhere")
    assert store.teams() == [] and store.list("t") == [] and store.teams_holding("1") == []
    assert store.team_mtime_ns("t") is None


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_a_non_json_number_is_refused_on_read_and_never_written(root, token):
    """Python's json accepts and emits these three tokens; a strict parser refuses the file."""
    path = root / "team-a" / "8.json"
    path.write_text('{"id": "8", "status": "pending", "metadata": {"x": %s}}' % token)
    store, ref = TaskStore(root), TaskRef("team-a", "8")
    assert store.get(ref) is None and "8" not in [t.get("id") for t in store.list("team-a")]
    assert store.update(ref, lambda task: True) is False and token in path.read_text()
    assert store.create(TaskRef("team-b", "8"), {"id": "8", "x": float(token)}) is False
    assert not (root / "team-b" / "8.json").exists()
    assert store.update(TaskRef("team-a", "1"), lambda task: task.update(x=float(token)) or True) is False
    assert json.loads((root / "team-a" / "1.json").read_text()) == {"id": "1", "status": "pending"}


@pytest.mark.parametrize("token", ["1e999", "-1e999", "1E400", "-0.5e+99999", "[1e999]"])
def test_an_overflowed_float_is_refused_like_infinity(root, token):
    """Regression: json parses an out-of-range literal to inf without calling parse_constant."""
    path = root / "team-a" / "8.json"
    path.write_text('{"id": "8", "metadata": {"rev": 1, "x": {"y": %s}}}' % token)
    store = TaskStore(root)
    assert store.get(TaskRef("team-a", "8")) is None and read_task_json("8", "team-a", str(root)) == {}


@pytest.mark.parametrize("token, value", [("1e-999", 0.0), ("-1e-999", -0.0), ("1.7976931348623157e308",
                                          1.7976931348623157e308), ("2.5", 2.5), ("1" * 4300, int("1" * 4300))])
def test_a_finite_number_still_reads(root, token, value):
    path = root / "team-a" / "8.json"
    path.write_text('{"id": "8", "x": %s}' % token)
    assert TaskStore(root).get(TaskRef("team-a", "8"))["x"] == value


def test_the_highwatermark_is_only_raised_and_created_only_on_request(root):
    store, marker = TaskStore(root), root / "team-a" / ".highwatermark"
    assert store.raise_highwatermark("team-a", 5) is False and not marker.exists()
    assert store.raise_highwatermark("team-a", 5, create=True) and marker.read_text() == "5"
    assert store.raise_highwatermark("team-a", 3) and marker.read_text() == "5"
    assert store.raise_highwatermark("team-a", 9) and marker.read_text() == "9"
    for bad in (0, -1, True, 2.5, "7", 10 ** 18):
        assert store.raise_highwatermark("team-a", bad, create=True) is False
    assert store.raise_highwatermark("../x", 5, create=True) is False
    assert sorted(p.name for p in (root / "team-a").iterdir() if p.name.endswith(".tmp")) == []
    marker.unlink()
    os.symlink(root / "elsewhere", marker)
    assert store.raise_highwatermark("team-a", 99, create=True) is False and not (root / "elsewhere").exists()


def test_retire_with_a_digest_renames_only_the_bytes_that_were_read(root):
    store = TaskStore(root)
    path = root / "team-a" / "50.json"
    path.write_text(json.dumps({"id": "50", "subject": "s"}))
    task, digest = store.get_digested(TaskRef("team-a", "50"))
    assert task == {"id": "50", "subject": "s"}
    path.write_text(json.dumps({"id": "50", "subject": "written since"}))
    assert store.retire(TaskRef("team-a", "50"), digest) is False and path.is_file()
    assert store.retire(TaskRef("team-a", "50"), store.get_digested(TaskRef("team-a", "50"))[1])
    assert not path.exists() and (root / "team-a" / "50.json.migrated").is_file()
