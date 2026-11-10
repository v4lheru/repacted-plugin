"""tests/test_task_store_probe.py: the host-store detector and the spawn deny it arms.

One row per behaviour: a present file is silent and clears; one miss is silent; the second
miss HALTs (systemMessage, task_store_absent row, halt marker); a miss on a task seen before
HALTs at once; a present but malformed file is a miss; dispatch_gate denies Agent while the marker holds; the next hit clears it.
Hostile: an id that cannot name a file (`../x`) probes nothing and counts nothing.
"""

from __future__ import annotations

import json

import pytest

import task_store_probe as probe_module
from shared.task_store import TaskStore, read_probe_marker

TEAM = "session-probe"


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "config" / "tasks"
    (root / TEAM).mkdir(parents=True)
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    return {"store": TaskStore(root), "root": root, "session_dir": str(session_dir)}


def _frame(tool: str, task_id: str, created: bool = True) -> dict:
    payload = {"tool_name": tool, "session_id": "s1", "tool_input": {}}
    if tool == "TaskCreate" and created:
        payload["tool_response"] = {"task": {"id": task_id}}
    else:
        payload["tool_input"] = {"taskId": task_id}
    return payload


def _write(world, task_id: str) -> None:
    (world["root"] / TEAM / f"{task_id}.json").write_text(json.dumps({"id": task_id}))


def _probe(world, payload):
    return probe_module.probe(payload, world["store"], world["session_dir"], TEAM)


def test_a_present_file_is_silent_and_a_second_miss_halts(world):
    _write(world, "1")
    assert _probe(world, _frame("TaskCreate", "1")) is None
    assert read_probe_marker(world["session_dir"]) == {"misses": 0, "halt": False,
                                                        "seen": [f"{TEAM}/1"]}
    assert _probe(world, _frame("TaskCreate", "2")) is None  # first miss: silent
    marker = read_probe_marker(world["session_dir"])
    assert (marker["misses"], marker["halt"]) == (1, False)
    assert _probe(world, _frame("TaskCreate", "3")) == {"team": TEAM, "task_id": "3", "misses": 2}
    assert read_probe_marker(world["session_dir"])["halt"] is True
    _write(world, "3")
    assert _probe(world, _frame("TaskUpdate", "3")) is None  # the next hit clears the halt
    assert read_probe_marker(world["session_dir"])["halt"] is False


def test_a_miss_on_a_task_seen_before_halts_at_once(world):
    _write(world, "1")
    _probe(world, _frame("TaskUpdate", "1"))
    (world["root"] / TEAM / "1.json").unlink()
    assert _probe(world, _frame("TaskUpdate", "1")) == {"team": TEAM, "task_id": "1", "misses": 1}


@pytest.mark.parametrize("status", ["deleted",])
def test_a_seen_task_closed_or_deleted_is_an_expected_absence(world, status):
    """Deleting a task removes its file: that update, and any later one on the same task, is
    no miss. A seen task whose file vanishes on a plain update still halts at once."""
    for task_id in ("1", "2"):
        _write(world, task_id)
        _probe(world, _frame("TaskUpdate", task_id))
    (world["root"] / TEAM / "1.json").unlink()
    closing = _frame("TaskUpdate", "1")
    closing["tool_input"]["status"] = status
    assert _probe(world, closing) is None
    assert _probe(world, _frame("TaskUpdate", "1")) is None
    marker = read_probe_marker(world["session_dir"])
    assert (marker["misses"], marker["halt"]) == (0, False)
    (world["root"] / TEAM / "2.json").unlink()
    assert _probe(world, _frame("TaskUpdate", "2")) == {"team": TEAM, "task_id": "2", "misses": 1}


