"""The lifecycle gate wires a teachback pair itself (1.5.0, D4).

On an owner write to Task A ("<name>: TEACHBACK for ...") or Task B ("<name>: ..."), the gate
writes A.blocks=[B] and B.blockedBy=[A] when the pair is unambiguous: one team, one owner, exactly
one open A and one open B, each existing edge pointing at the other task or empty. Anything else
writes nothing. The gate writes another task's file, so each refusal row checks the bytes on disk.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import task_lifecycle_gate as tlg

TEAM = "test-team"


@pytest.fixture
def team(tmp_path, monkeypatch, pact_context):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id="test-session")
    events = []
    monkeypatch.setattr(tlg, "append_event", lambda event: events.append(event) or True)
    root = tmp_path / ".claude" / "tasks"

    def seed(tid, subject, owner="wiring", status="pending", team_name=TEAM, **fields):
        d = root / team_name
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{tid}.json").write_text(json.dumps(
            {"id": tid, "subject": subject, "owner": owner, "status": status, **fields}),
            encoding="utf-8")

    def disk(tid, team_name=TEAM):
        return json.loads((root / team_name / f"{tid}.json").read_text(encoding="utf-8"))

    def write(tid, owner="wiring", **extra):
        return tlg.evaluate_lifecycle({"tool_name": "TaskUpdate",
                                       "tool_input": {"taskId": tid, "owner": owner, **extra},
                                       "tool_response": {}})

    seed.disk, seed.write, seed.events, seed.root = disk, write, events, root
    return seed


def rules(advisories):
    return [rule for rule, _ in advisories]


def test_b_owner_write_with_addblockedby_fills_a_blocks(team):
    team("1", "wiring: TEACHBACK for x", blocks=[])
    team("2", "wiring: build x", blockedBy=["1"])
    advisories = team.write("2", addBlockedBy=["1"])
    assert team.disk("1")["blocks"] == ["2"] and team.disk("2")["blockedBy"] == ["1"]
    assert "teachback_addblocks_missing" not in rules(advisories)
    assert [(e["type"], e["written"]) for e in team.events] == [("task_pair_wired", ["blocks"])]


def test_owner_writes_alone_wire_both_edges(team):
    team("1", "wiring: TEACHBACK for x")
    team("2", "wiring: build x")
    advisories = team.write("1") + team.write("2")
    assert "teachback_addblocks_missing" not in rules(advisories)
    assert team.disk("1")["blocks"] == ["2"] and team.disk("2")["blockedBy"] == ["1"]
    assert [e["written"] for e in team.events] == [["blocks", "blockedBy"]]


def test_a_owner_write_before_b_has_an_owner_is_silent(team):
    team("1", "wiring: TEACHBACK for x")
    team("2", "wiring: build x", owner="")
    advisories = team.write("1")
    assert "teachback_addblocks_missing" not in rules(advisories)
    assert "blocks" not in team.disk("1") and "blockedBy" not in team.disk("2")


def test_two_teachback_tasks_for_one_owner_write_nothing_and_warn(team):
    team("1", "wiring: TEACHBACK for x")
    team("3", "wiring: TEACHBACK for y")
    team("2", "wiring: build x")
    advisories = team.write("2")
    assert "teachback_addblocks_missing" in rules(advisories)
    assert all("blocks" not in team.disk(t) for t in ("1", "3"))
    assert "blockedBy" not in team.disk("2") and team.events == []


def test_different_owners_are_never_paired(team):
    team("1", "wiring: TEACHBACK for x")
    team("2", "wiring: build x", owner="other")
    advisories = team.write("2", owner="other")
    advisories += team.write("1")
    assert "teachback_addblocks_missing" not in rules(advisories)
    assert "blocks" not in team.disk("1") and "blockedBy" not in team.disk("2")


def test_another_teams_tasks_are_neither_counted_nor_written(team):
    team("1", "wiring: TEACHBACK for x")
    team("2", "wiring: build x")
    team("9", "wiring: build y", team_name="other-team")
    before = (team.root / "other-team" / "9.json").read_bytes()
    team.write("2")
    assert team.disk("1")["blocks"] == ["2"]
    assert (team.root / "other-team" / "9.json").read_bytes() == before
    # and a lone A here is not paired with the other team's B
    team("5", "solo: TEACHBACK for z", owner="solo")
    team("6", "solo: build z", owner="solo", team_name="other-team")
    team.write("5", owner="solo")
    assert "blocks" not in team.disk("5") and "blockedBy" not in team.disk("6", "other-team")


def test_a_completed_work_task_is_not_a_pair(team):
    team("1", "wiring: TEACHBACK for x")
    team("2", "wiring: build x", status="completed")
    advisories = team.write("1")
    assert "teachback_addblocks_missing" not in rules(advisories)
    assert "blocks" not in team.disk("1") and "blockedBy" not in team.disk("2")


@pytest.mark.parametrize("owner_b, subject_b", [
    ("wiring", "wіring: build x"),     # Cyrillic i in B's subject prefix
    ("wіring", "wiring: build x"),     # Cyrillic i in B's owner
    ("wiring", "wiring​: build x"),    # zero-width space before the colon
])
def test_unicode_look_alike_names_are_never_paired(team, owner_b, subject_b):
    team("1", "wiring: TEACHBACK for x")
    team("2", subject_b, owner=owner_b)
    team.write("2", owner=owner_b)
    team.write("1")
    assert "blocks" not in team.disk("1") and "blockedBy" not in team.disk("2")


def test_an_a_that_already_blocks_something_else_is_left_alone(team):
    team("1", "wiring: TEACHBACK for x", blocks=["77"])
    team("2", "wiring: build x")
    advisories = team.write("2")
    assert team.disk("1")["blocks"] == ["77"] and "blockedBy" not in team.disk("2")
    assert "teachback_addblocks_missing" not in rules(advisories)


def test_a_b_blocked_by_another_task_is_left_alone_and_warned(team):
    team("1", "wiring: TEACHBACK for x")
    team("2", "wiring: build x", blockedBy=["50"])
    advisories = team.write("2")
    assert "blocks" not in team.disk("1") and team.disk("2")["blockedBy"] == ["50"]
    assert "teachback_addblocks_missing" in rules(advisories)


def test_a_traversal_id_in_a_task_file_writes_nothing(team):
    team("1", "wiring: TEACHBACK for x")
    d = team.root / TEAM
    (d / "2.json").write_text(json.dumps({"id": "../escape", "subject": "wiring: build x",
                                          "owner": "wiring", "status": "pending"}), encoding="utf-8")
    team.write("1")
    assert not (team.root / "escape.json").exists()
    assert "blocks" not in team.disk("1")


def test_a_half_wired_pair_is_named_for_the_lead(team, monkeypatch):
    """rv4 MED-2: A.blocks landed, B's write timed out on a held host lock; the gate must say so
    (teachback_addblocks_missing cannot fire once A has blocks)."""
    from shared import state_file
    monkeypatch.setattr(state_file, "HOST_LOCK_WAIT", 0.1)
    team("1", "wiring: TEACHBACK for x")
    team("2", "wiring: build x")
    (team.root / TEAM / "2.json.lock").mkdir()
    advisories = team.write("1")
    assert team.disk("1")["blocks"] == ["2"] and "blockedBy" not in team.disk("2")
    message = dict(advisories)["teachback_pair_half_wired"]
    assert "Task 1 blocks 2" in message and "addBlockedBy" in message
