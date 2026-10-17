"""tests/test_task_store_probe.py: the host-store detector and the spawn deny it arms.

One row per behaviour: a present file is silent and clears; one miss is silent; the second
miss HALTs (systemMessage, task_store_absent row, halt marker); a miss on a task seen before
HALTs at once; a present but malformed file is a miss; dispatch_gate denies Agent while the marker holds; the next hit clears it.
Hostile: an id that cannot name a file (`../x`) probes nothing and counts nothing.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

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


def test_a_present_but_malformed_file_is_a_miss(world):
    _write(world, "1")
    _probe(world, _frame("TaskUpdate", "1"))
    (world["root"] / TEAM / "1.json").write_text("{not json")
    assert _probe(world, _frame("TaskUpdate", "1")) == {"team": TEAM, "task_id": "1", "misses": 1}


@pytest.mark.parametrize("task_id", ["../x", "a/b", "", "\x00"])
def test_an_id_that_names_no_file_probes_nothing(world, task_id):
    assert _probe(world, _frame("TaskUpdate", task_id)) is None
    assert read_probe_marker(world["session_dir"]) == {}
    assert not list(Path(world["session_dir"]).iterdir())


def test_the_dispatcher_entry_journals_and_returns_the_halt(world, monkeypatch):
    import shared.pact_context as ctx
    monkeypatch.setattr(ctx, "init", lambda payload: None)
    monkeypatch.setattr(ctx, "get_team_name", lambda: TEAM)
    monkeypatch.setattr(ctx, "get_session_dir", lambda: world["session_dir"])
    monkeypatch.setattr(probe_module, "TaskStore", lambda: world["store"])
    rows = []

    class Ctx:
        def journal(self, event_type, **fields):
            rows.append((event_type, fields))

    assert probe_module.dispatch_check(_frame("TaskCreate", "9"), Ctx()) is None
    result = probe_module.dispatch_check(_frame("TaskCreate", "8"), Ctx())
    assert result.system_message.startswith("⚠️ ALGEDONIC HALT") and "tasks/session-probe/8.json" in result.system_message
    assert result.journaled and result.rule == "dispatch.task_store_absent"
    assert rows == [("task_store_absent", {"team": TEAM, "task_id": "8", "misses": 2})]
    assert probe_module.dispatch_check({"tool_name": "Bash"}, Ctx()) is None


def test_dispatch_gate_denies_agent_spawns_while_the_marker_holds(tmp_path, monkeypatch, capsys):
    from test_dispatch_gate_smoke import _NAME, _make_input, _seed_plugin, _seed_team, _setup_session
    import shared.pact_context as ctx
    import dispatch_gate

    plugin_root = tmp_path / "plugin"
    _seed_plugin(plugin_root)
    _setup_session(monkeypatch, tmp_path, plugin_root)
    _seed_team(tmp_path, tasks=((_NAME, "pending"),))
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    monkeypatch.setattr(ctx, "get_session_dir", lambda: str(session_dir))
    (session_dir / "task-store-probe.json").write_text(json.dumps(
        {"misses": 2, "halt": True, "seen": [], "team": "t", "task_id": "4"}))

    with patch("sys.stdin", io.StringIO(json.dumps(_make_input()))):
        with pytest.raises(SystemExit) as stop:
            dispatch_gate.main()
    out = json.loads(capsys.readouterr().out.strip())
    assert stop.value.code == 2
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "tasks/t/4.json" in out["hookSpecificOutput"]["permissionDecisionReason"]

    (session_dir / "task-store-probe.json").write_text(json.dumps({"misses": 0, "halt": False}))
    with patch("sys.stdin", io.StringIO(json.dumps(_make_input()))):
        with pytest.raises(SystemExit) as stop:
            dispatch_gate.main()
    assert stop.value.code == 0
