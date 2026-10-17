"""tests/test_task_migration.py: the resume migration (shared/task_migration.py) and the
TaskStore writers it uses.

The source store is disk state an earlier teammate could write, so most rows are hostile:
a file that is not what its name says, an id or team that is a path, edges that name
nothing, a `migrated_from` that lies, a run that fails half-way. Every store is a tmp dir.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from shared import task_migration as tm
from shared.task_store import TaskRef, TaskStore

OLD, NEW, NOW = "session-old00001", "session-new00002", "2026-10-11T10:00:00+00:00"


def task(task_id, status="in_progress", blocks=(), blocked_by=(), **extra) -> dict:
    return {"id": task_id, "subject": f"subject {task_id}", "description": "d", "status": status,
            "owner": "coder-a", "blocks": list(blocks), "blockedBy": list(blocked_by),
            "metadata": {}, **extra}


@pytest.fixture
def root(tmp_path) -> Path:
    base = tmp_path / "config" / "tasks"
    (base / OLD).mkdir(parents=True)
    (base / NEW).mkdir()
    return base


def put(root: Path, team: str, obj: dict, name: str | None = None) -> Path:
    path = root / team / f"{name or obj['id']}.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def listed(directory: Path) -> list:
    """The directory's entries but the id marker a run leaves in a store it created files in."""
    return [p for p in directory.iterdir() if p.name != ".highwatermark"]


def snapshot(root: Path) -> dict:
    """Every file under the store with its bytes; lock dirs and temp names would show."""
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


JOURNALS: dict = {}


def run(root, emit=None, source=OLD, dest=NEW, **kwargs):
    """One run against the root's recording journal: the rows a run emits are what a later
    run of the same root reads back as `copied`. Returns the result and this run's rows."""
    journal, rows = JOURNALS.setdefault(root, []), []

    def record(row):
        rows.append(row)
        journal.append(json.loads(json.dumps(row)))
        return True

    kwargs.setdefault("copied", lambda: list(journal))
    result = tm.migrate(TaskStore(root), source, dest, emit or record, NOW, **kwargs)
    return result, rows


# -- plan, pure -----------------------------------------------------------------------------------

def test_ids_are_kept_on_an_empty_destination_and_the_wait_is_stripped():
    wait = {"reason": "awaiting_lead_completion", "expected_resolver": "lead"}
    source = [task("3", blocked_by=["2"]), task("2", blocks=["3"], metadata={"intentional_wait": wait,
                                                                              "handoff": {"rev": 2}})]
    planned = tm.plan(source, set(), 0, OLD, NOW)
    assert [t["id"] for t in planned.tasks] == ["2", "3"]
    two, three = planned.tasks
    assert two["blocks"] == ["3"] and three["blockedBy"] == ["2"]
    assert "intentional_wait" not in two["metadata"] and two["metadata"]["handoff"] == {"rev": 2}
    assert two["metadata"]["migrated_from"] == {
        "team": OLD, "id": "2", "at": NOW, "status_was": "in_progress", "owner_was": "coder-a",
        "wait_was": wait, "dropped_edges": []}
    assert source[1]["metadata"]["intentional_wait"] == wait  # the input is not changed


def test_one_collision_remaps_that_task_only_and_rewrites_both_edge_directions():
    source = [task("1", blocks=["2"]), task("2", blocked_by=["1"], blocks=["3"]), task("3", blocked_by=["2"])]
    planned = tm.plan(source, {"2", "9"}, 0, OLD, NOW)
    assert planned.id_map == {"1": "1", "2": "10", "3": "3"}
    by_id = {t["id"]: t for t in planned.tasks}
    assert by_id["1"]["blocks"] == ["10"] and by_id["3"]["blockedBy"] == ["10"]
    assert by_id["10"]["blockedBy"] == ["1"] and by_id["10"]["blocks"] == ["3"]
    assert by_id["10"]["metadata"]["migrated_from"]["id"] == "2"


def test_a_highwatermark_above_a_source_id_forces_a_remap_with_no_file_present():
    planned = tm.plan([task("4"), task("12")], set(), 7, OLD, NOW)
    assert planned.id_map == {"4": "13", "12": "12"}


@pytest.mark.parametrize("highwater", [True, -3, 2.5, "9", None])
def test_a_highwatermark_that_is_not_a_positive_int_reads_as_zero(highwater):
    assert tm.plan([task("1")], set(), highwater, OLD, NOW).id_map == {"1": "1"}


def test_a_non_numeric_id_is_kept_when_free_and_numbered_when_not():
    assert tm.plan([task("alpha"), task("5")], set(), 0, OLD, NOW).id_map == {"alpha": "alpha", "5": "5"}
    assert tm.plan([task("alpha"), task("5")], {"ALPHA"}, 0, OLD, NOW).id_map == {"alpha": "6", "5": "5"}
    # A digit of another script is not a number: `int` would read it as 7 and collide.
    assert tm.plan([task("\uff17")], set(), 9, OLD, NOW).id_map == {"\uff17": "\uff17"}


def test_edges_to_a_completed_an_absent_and_a_self_id_are_dropped_and_recorded():
    source = [task("1", blocked_by=["1", "2", "404", "3", "3"]), task("2", status="completed"), task("3")]
    planned = tm.plan(source, set(), 0, OLD, NOW)
    one = planned.tasks[0]
    assert one["blockedBy"] == ["3"]
    assert one["metadata"]["migrated_from"]["dropped_edges"] == [
        {"field": "blockedBy", "id": "1"}, {"field": "blockedBy", "id": "2"}, {"field": "blockedBy", "id": "404"}]


def test_edge_entries_that_are_not_strings_are_dropped_without_their_value():
    source = [task("1", blocked_by=[7, None, {"id": "2"}, ["2"], "../2", "2"]), task("2")]
    one = tm.plan(source, set(), 0, OLD, NOW).tasks[0]
    assert one["blockedBy"] == ["2"]
    assert one["metadata"]["migrated_from"]["dropped_edges"] == [{"field": "blockedBy", "id": None}] * 5


def test_a_cycle_in_blocked_by_is_rewritten_and_does_not_loop():
    source = [task("1", blocked_by=["2"]), task("2", blocked_by=["3"]), task("3", blocked_by=["1"])]
    planned = tm.plan(source, {"1", "2", "3"}, 0, OLD, NOW)
    assert planned.id_map == {"1": "4", "2": "5", "3": "6"}
    assert [t["blockedBy"] for t in planned.tasks] == [["5"], ["6"], ["4"]]


@pytest.mark.parametrize("bad", [
    {"id": "../x"}, {"id": ""}, {"id": "x" * 300}, {"id": 7}, {"subject": None}, {"description": 3},
    {"blocks": "2"}, {"blockedBy": None}, {"metadata": ["handoff"]},
])
def test_an_open_task_of_the_wrong_shape_is_counted_and_not_planned(bad):
    planned = tm.plan([{**task("1"), **bad}, task("2")], set(), 0, OLD, NOW)
    assert [t["id"] for t in planned.tasks] == ["2"]
    assert (planned.open_count, planned.skipped) == (2, 1)


@pytest.mark.parametrize("status", ["completed", "deleted", None, ["pending"], "PENDING", 1])
def test_only_pending_and_in_progress_move(status):
    planned = tm.plan([task("1", status=status), "not a dict", None], set(), 0, OLD, NOW)
    assert planned.tasks == [] and planned.open_count == 0


def test_a_source_migrated_from_is_rebuilt_and_only_a_safe_origin_is_carried():
    lie = {"team": "session-other", "id": "40", "at": "1999", "wait_was": {"x": 1}, "planted": "claim"}
    unsafe = {"team": "../../etc", "id": "1", "origin": {"team": "a/b", "id": ".."}}
    hop2 = {"team": "session-mid", "id": "8", "origin": {"team": "session-first", "id": "2", "note": "x"}}
    source = [task("1", metadata={"migrated_from": lie}), task("2", metadata={"migrated_from": unsafe}),
              task("3", metadata={"migrated_from": hop2}), task("4", metadata={"migrated_from": "x"}),
              task("5", metadata={"intentional_wait": "forever"})]
    got = [t["metadata"]["migrated_from"] for t in tm.plan(source, set(), 0, OLD, NOW).tasks]
    assert all(m["team"] == OLD and m["at"] == NOW for m in got)
    assert got[0]["origin"] == {"team": "session-other", "id": "40"} and got[0]["wait_was"] is None
    assert "planted" not in json.dumps(got) and "1999" not in json.dumps(got)
    assert "origin" not in got[1] and "origin" not in got[3]
    assert got[2]["origin"] == {"team": "session-first", "id": "2"}
    assert got[4]["wait_was"] is None  # a wait that is not an object is removed, not kept


def test_unicode_in_subject_and_owner_survives_and_the_note_fields_are_cut():
    owner = "cod\u00e9r-\u202e`x`\n$(rm)" + "a" * 60
    source = [task("1", subject="t\u00e2che \U0001f600 \u202e", owner=owner)]
    planned = tm.plan(source, set(), 0, OLD, NOW)
    assert planned.tasks[0]["subject"] == source[0]["subject"] and planned.tasks[0]["owner"] == owner
    assert tm.shown_owner(owner) == ("codr-xrm" + "a" * 60)[:40]
    assert tm.shown_owner(None) == tm.shown_owner("\u202e") == "no owner"


@pytest.mark.parametrize("metadata, state", [
    ({"handoff": {"rev": 3}}, "HANDOFF rev 3 stored"), ({"handoff": {"rev": True}}, "HANDOFF stored"),
    ({"handoff": {"rev": "9; rm"}}, "HANDOFF stored"), ({"handoff": "x", "teachback_submit": {}}, "teachback stored"),
    ({}, "nothing stored"), (None, "nothing stored"),
])
def test_stored_state_prints_presence_and_a_bounded_rev_only(metadata, state):
    assert tm.stored_state(metadata) == state


def test_the_journal_row_holds_key_names_only_and_no_wait():
    source = task("1", metadata={"intentional_wait": {"reason": "MARKER-WAIT"}, "handoff": {"produced": "MARKER"},
                                 "amend_1": "MARKER", "bad key\n": 1})
    row = tm.journal_row(NEW, tm.plan([source], set(), 0, OLD, NOW).tasks[0], recovered=True)
    assert row["kind"] == "migrated" and row["team"] == NEW and row["task_id"] == "1" and row["recovered"] is True
    assert row["keys"] == ["amend_1", "handoff", "migrated_from"]
    assert "MARKER" not in json.dumps(row) and "wait_was" not in row["migrated_from"]


# -- hostile source files ------------------------------------------------------------------------

def test_hostile_source_files_are_left_alone_and_counted(root, tmp_path):
    put(root, OLD, task("1"))
    put(root, OLD, task("9"), name="2")                       # id differs from the stem
    (root / OLD / "3.json").write_text("[1, 2]")              # JSON that is a list
    (root / OLD / "4.json").write_text('{"id": "4", "subj')   # truncated
    (root / OLD / "5.json").write_text(json.dumps(task("5", description="x" * (tm.MAX_TASK_BYTES + 1))))
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(task("6")))
    os.symlink(outside, root / OLD / "6.json")                # a symlinked task file
    (root / OLD / "7.json").write_text("[" * 100000)          # deeper than the parser goes
    before = snapshot(root)
    result, rows = run(root)
    assert [(m.old_id, m.new_id) for m in result.moved] == [("1", "1")]
    assert result.skipped == 5 and len(rows) == 1
    after = snapshot(root)
    assert after.pop(f"{NEW}/.highwatermark") == b"1"
    assert after.pop(f"{OLD}/1.json.migrated") == before.pop(f"{OLD}/1.json")
    assert json.loads(after.pop(f"{NEW}/1.json"))["metadata"]["migrated_from"]["id"] == "1"
    assert after == before and json.loads(outside.read_text())["id"] == "6"


@pytest.mark.parametrize("source, dest", [
    ("..", NEW), (OLD, "../x"), ("a/b", NEW), (OLD, "team.dotted"), ("", NEW), (OLD, OLD), (OLD, "team new"),
    (None, NEW), (OLD, "\u202eteam"),
])
def test_a_team_name_that_is_not_one_safe_segment_is_refused(root, source, dest):
    put(root, OLD, task("1"))
    before = snapshot(root)
    result = tm.migrate(TaskStore(root), source, dest, lambda row: True, NOW)
    assert (result.status, result.reason) == (tm.STATUS_REFUSED, tm.REASON_BAD_TEAM)
    assert snapshot(root) == before


def test_a_symlinked_source_or_destination_team_dir_moves_nothing(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "1.json").write_text(json.dumps(task("1")))
    os.symlink(outside, root / "session-link")
    for source, dest in (("session-link", NEW), (OLD, "session-link")):
        put(root, OLD, task("2"))
        result = tm.migrate(TaskStore(root), source, dest, lambda row: True, NOW)
        assert result.status == tm.STATUS_REFUSED and result.moved == []
        assert sorted(p.name for p in outside.iterdir()) == ["1.json"]
        assert (root / OLD / "2.json").is_file()


def test_a_huge_source_store_is_refused_before_it_is_read(root, monkeypatch):
    monkeypatch.setattr(tm, "MAX_SOURCE_FILES", 3)
    for n in range(4):
        put(root, OLD, task(str(n + 1)))
    result, rows = run(root)
    assert (result.status, result.reason, rows) == (tm.STATUS_REFUSED, tm.REASON_UNREADABLE, [])
    monkeypatch.setattr(tm, "MAX_SOURCE_FILES", 2000)
    monkeypatch.setattr(tm, "MAX_SOURCE_BYTES", 300)
    assert run(root)[0].reason == tm.REASON_UNREADABLE and not list(listed(root / NEW))


# -- I/O: all or nothing, idempotence ------------------------------------------------------------

def test_a_create_that_fails_on_the_third_of_five_leaves_both_stores_as_they_were(root, monkeypatch):
    for n in range(1, 6):
        put(root, OLD, task(str(n), blocks=[str(n + 1)] if n < 5 else []))
    before, real, calls = snapshot(root), TaskStore.create, []

    def failing(self, ref, obj):
        calls.append(ref.task_id)
        return False if len(calls) == 3 else real(self, ref, obj)

    monkeypatch.setattr(TaskStore, "create", failing)
    result, rows = run(root)
    assert (result.status, result.reason) == (tm.STATUS_REFUSED, tm.REASON_CREATE_FAILED)
    assert calls == ["1", "2", "3"] and rows == [] and result.moved == []
    assert snapshot(root) == before


def test_a_destination_file_planted_at_a_kept_id_is_never_overwritten_or_removed(root, monkeypatch):
    put(root, OLD, task("1"))
    put(root, OLD, task("2"))
    monkeypatch.setattr(tm, "_task_stems", lambda directory: set())  # the plan is blind to the plant
    planted = put(root, NEW, {"id": "2", "subject": "the host's own"})
    before = snapshot(root)
    result, _ = run(root)
    assert result.reason == tm.REASON_CREATE_FAILED and snapshot(root) == before
    assert json.loads(planted.read_text())["subject"] == "the host's own"


def test_a_second_and_a_third_run_after_a_full_success_do_nothing_and_emit_no_row(root):
    put(root, OLD, task("1", blocks=["2"]))
    put(root, OLD, task("2", blocked_by=["1"]))
    first, rows = run(root)
    assert first.status == tm.STATUS_MOVED and [r["task_id"] for r in rows] == ["1", "2"]
    after = snapshot(root)
    for _ in range(2):
        again, rows = run(root)
        assert (again.status, again.moved, again.recovered, rows) == (tm.STATUS_NOTHING, [], 0, [])
        assert snapshot(root) == after


def test_a_run_after_created_not_retired_retires_and_emits_recovered(root, monkeypatch):
    put(root, OLD, task("1"))
    put(root, OLD, task("2"))
    monkeypatch.setattr(TaskStore, "retire", lambda self, ref, digest=None: False)  # the crash before the rename
    half, _ = run(root)
    assert half.retire_failed == 2 and (root / OLD / "1.json").is_file()
    monkeypatch.undo()
    put(root, OLD, task("3", blocked_by=["1"]))                        # written after the crash
    result, rows = run(root)
    assert (result.recovered, [(m.old_id, m.new_id) for m in result.moved]) == (2, [("3", "3")])
    assert [(r["task_id"], r.get("recovered")) for r in rows] == [("1", True), ("2", True), ("3", None)]
    assert sorted(p.name for p in listed(root / OLD)) == ["1.json.migrated", "2.json.migrated", "3.json.migrated"]
    assert json.loads((root / NEW / "3.json").read_text())["blockedBy"] == ["1"]
    assert sorted(p.name for p in listed(root / NEW)) == ["1.json", "2.json", "3.json"]


def test_a_false_migrated_from_claim_in_the_destination_retires_nothing(root):
    put(root, OLD, task("1"))
    put(root, NEW, {**task("7"), "subject": "another task",
                    "metadata": {"migrated_from": {"team": OLD, "id": "1"}}})
    result, rows = run(root)
    assert result.recovered == 0 and [(m.old_id, m.new_id) for m in result.moved] == [("1", "1")]
    assert json.loads((root / NEW / "1.json").read_text())["subject"] == "subject 1"


@pytest.mark.parametrize("emit", [lambda row: False, lambda row: 1 / 0, lambda row: None])
def test_emit_failing_does_not_stop_the_retire_and_is_counted(root, emit):
    put(root, OLD, task("1"))
    result, _ = run(root, emit=emit)
    assert (result.status, result.emit_failed, result.retire_failed) == (tm.STATUS_MOVED, 1, 0)
    assert not (root / OLD / "1.json").exists() and (root / NEW / "1.json").is_file()


def test_origin_is_carried_across_two_hops(root):
    third = "session-third003"
    (root / third).mkdir()
    put(root, OLD, task("1"))
    run(root)
    assert tm.migrate(TaskStore(root), NEW, third, lambda row: True, NOW).status == tm.STATUS_MOVED
    moved = json.loads((root / third / "1.json").read_text())["metadata"]["migrated_from"]
    assert (moved["team"], moved["origin"]) == (NEW, {"team": OLD, "id": "1"})
    fourth = "session-fourth04"
    (root / fourth).mkdir()
    tm.migrate(TaskStore(root), third, fourth, lambda row: True, NOW)
    moved = json.loads((root / fourth / "1.json").read_text())["metadata"]["migrated_from"]
    assert (moved["team"], moved["origin"]) == (third, {"team": OLD, "id": "1"})


# -- refusals (RS-1) ---------------------------------------------------------------------------------

def _standing(root):
    config = root.parent / "teams" / OLD / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}")


def _dark(root) -> str:
    session_dir = root.parent / "session"
    session_dir.mkdir()
    (session_dir / "task-store-probe.json").write_text(json.dumps({"halt": True}))
    return str(session_dir)


def test_a_standing_config_moves_nothing_and_the_explicit_run_moves(root):
    put(root, OLD, task("1"))
    _standing(root)
    before = snapshot(root)
    result, rows = run(root)
    assert (result.status, result.reason, result.open_count) == (tm.STATUS_REFUSED, tm.REASON_STANDING_CONFIG, 1)
    assert rows == [] and snapshot(root) == before
    assert run(root, allow_standing_config=True)[0].status == tm.STATUS_MOVED


@pytest.mark.parametrize("case, reason", [
    ("peer", tm.REASON_PEER_SESSION), ("peer_raises", tm.REASON_PEER_SESSION),
    ("dark", tm.REASON_DARK_STORE), ("too_many", tm.REASON_TOO_MANY),
])
def test_the_other_refusals_hold_for_the_explicit_run_too(root, monkeypatch, case, reason):
    kwargs = {"allow_standing_config": True}
    if case == "peer":
        kwargs["peer_names_team"] = lambda team: team == OLD
    elif case == "peer_raises":
        kwargs["peer_names_team"] = lambda team: 1 / 0
    elif case == "dark":
        kwargs["session_dir"] = _dark(root)
    else:
        monkeypatch.setattr(tm, "MAX_OPEN_TASKS", 2)
    for n in range(3):
        put(root, OLD, task(str(n + 1)))
    before = snapshot(root)
    result, rows = run(root, **kwargs)
    assert (result.status, result.reason, result.open_count) == (tm.STATUS_REFUSED, reason, 3)
    assert rows == [] and {k: v for k, v in snapshot(root).items() if k.startswith(("session-",))} == {
        k: v for k, v in before.items() if k.startswith(("session-",))}


def test_201_open_tasks_move_nothing_and_200_move(root):
    for n in range(1, 202):
        put(root, OLD, task(str(n)))
    assert run(root)[0].reason == tm.REASON_TOO_MANY and not list(listed(root / NEW))
    (root / OLD / "201.json").unlink()
    assert len(run(root)[0].moved) == 200


def test_no_source_store_and_no_open_task_are_nothing(root):
    put(root, OLD, task("1", status="completed"))
    assert run(root)[0].status == tm.STATUS_NOTHING
    assert tm.migrate(TaskStore(root), "session-absent", NEW, lambda row: True, NOW).status == tm.STATUS_NOTHING


# -- the TaskStore writers ------------------------------------------------------------------------------

def test_create_never_replaces_follows_no_symlink_and_leaves_no_temp_or_lock(root, tmp_path):
    store, obj = TaskStore(root), task("1")
    assert store.create(TaskRef(NEW, "1"), obj) is True
    assert store.get(TaskRef(NEW, "1")) == obj
    assert store.create(TaskRef(NEW, "1"), task("1", subject="second")) is False
    assert store.get(TaskRef(NEW, "1")) == obj
    outside = tmp_path / "outside.json"
    os.symlink(outside, root / NEW / "2.json")
    assert store.create(TaskRef(NEW, "2"), task("2")) is False and not outside.exists()
    for ref in (TaskRef("../x", "1"), TaskRef(NEW, "../3"), TaskRef("", "3"), TaskRef("session-absent", "3")):
        assert store.create(ref, task("3")) is False
    assert store.create(TaskRef(NEW, "4"), {"id": "4", "bad": {1, 2}}) is False
    assert sorted(p.name for p in listed(root / NEW)) == ["1.json", "2.json"]


def test_retire_renames_once_keeps_an_earlier_retired_file_and_refuses_a_symlink(root, tmp_path):
    store = TaskStore(root)
    put(root, OLD, task("1"))
    assert store.retire(TaskRef(OLD, "1")) is True and store.retire(TaskRef(OLD, "1")) is False
    put(root, OLD, task("1", subject="reissued"))
    assert store.retire(TaskRef(OLD, "1")) is True
    assert sorted(p.name for p in listed(root / OLD)) == ["1.json.migrated", "1.json.migrated.1"]
    assert json.loads((root / OLD / "1.json.migrated").read_text())["subject"] == "subject 1"
    assert store.list(OLD) == [] and store.teams_holding("1") == []
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    os.symlink(outside, root / OLD / "2.json")
    assert store.retire(TaskRef(OLD, "2")) is False and (root / OLD / "2.json").is_symlink()


def test_discard_removes_only_the_exact_object_it_is_given(root):
    store, obj = TaskStore(root), task("1")
    store.create(TaskRef(NEW, "1"), obj)
    assert store.discard(TaskRef(NEW, "1"), task("1", subject="other")) is False
    assert store.discard(TaskRef(NEW, "1"), obj) is True and not (root / NEW / "1.json").exists()
    assert store.discard(TaskRef(NEW, "1"), obj) is False


@pytest.mark.parametrize("text, value", [
    ("17", 17), ("17\n", 17), ("", 0), ("-4", 0), ("1e3", 0), ("\uff17", 0), ("9" * 40, 0), ("4 5", 0),
])
def test_highwatermark_reads_a_plain_number_or_zero(root, text, value):
    (root / NEW / ".highwatermark").write_text(text, encoding="utf-8")
    assert TaskStore(root).highwatermark(NEW) == value
    assert TaskStore(root).highwatermark("session-absent") == 0


# -- review fixes: proof of a copy, numbers the host cannot hold, the journal row -------------------

def test_a_claim_that_shares_subject_and_description_but_not_the_copys_fields_retires_nothing(root):
    """A destination task is this run's copy only when it holds what the plan would have
    written: `migrated_from` with a team and an id is a claim, not a copy."""
    put(root, OLD, task("5", metadata={"handoff": {"rev": 2}}))
    put(root, NEW, {**task("9"), "subject": "subject 5",
                    "metadata": {"migrated_from": {"team": OLD, "id": "5"}}})
    result, _ = run(root)
    assert result.recovered == 0 and [(m.old_id, m.new_id) for m in result.moved] == [("5", "5")]
    assert json.loads((root / NEW / "5.json").read_text())["metadata"]["handoff"] == {"rev": 2}
    assert sorted(p.name for p in listed(root / OLD)) == ["5.json.migrated"]


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_a_source_file_with_a_non_json_number_is_skipped_and_left_in_place(root, token):
    text = json.dumps(task("1")).replace('"metadata": {}', '"metadata": {"x": [%s]}' % token)
    (root / OLD / "1.json").write_text(text, encoding="utf-8")
    before = snapshot(root)
    result, rows = run(root)
    assert (result.status, result.skipped, rows) == (tm.STATUS_NOTHING, 1, [])
    assert snapshot(root) == before


def test_a_numeric_id_above_what_the_host_holds_exactly_is_counted_and_not_planned():
    exact = str(2 ** 53 - 1)
    got = tm.plan([task(str(2 ** 53)), task(exact), task("9" * 25), task("0" * 30 + "7")],
                  set(), 0, OLD, NOW)
    assert ([t["id"] for t in got.tasks], got.skipped) == ([exact, "0" * 30 + "7"], 2)


def test_a_copy_that_travelled_on_is_recognised_when_its_first_team_is_migrated_later(root, monkeypatch):
    """The first run could not rename the source; a later life moved the copy on. The note
    names the command (no resume retries it), and that command retires the source without
    creating a second file for the one task."""
    third = "session-third003"
    (root / third).mkdir()
    put(root, OLD, task("1", metadata={"handoff": {"rev": 1}}))
    monkeypatch.setattr(TaskStore, "retire", lambda self, ref, digest=None: False)
    half, _ = run(root)
    note = tm.resume_tasks_note(half)
    assert f"run `repact-migrate {OLD}` to rename" in note and "retries" not in note
    monkeypatch.undo()
    assert run(root, source=NEW, dest=third)[0].status == tm.STATUS_MOVED
    result, rows = run(root, dest=third)
    assert (result.status, result.recovered, result.moved) == (tm.STATUS_MOVED, 1, [])
    assert sorted(p.name for p in listed(root / third)) == ["1.json"]
    assert sorted(p.name for p in listed(root / OLD)) == ["1.json.migrated"]
    assert [(r["recovered"], r["migrated_from"]) for r in rows] == [(True, {"team": OLD, "id": "1"})]


def test_the_journal_row_bounds_the_owner_and_rebuilds_migrated_from_from_checked_fields():
    planned = tm.plan([task("1", owner="o" * 500 + "\n\x1b[2J")], set(), 0, OLD, NOW).tasks[0]
    fresh = tm.journal_row(NEW, planned)["migrated_from"]
    assert fresh == {"team": OLD, "id": "1", "at": NOW, "status_was": "in_progress",
                     "owner_was": "o" * 40, "dropped_edges": []}
    planned["metadata"]["migrated_from"] = {
        "team": "../x", "id": "a/b", "at": "A" * 900, "status_was": ["x"] * 99, "owner_was": {"k": "v"},
        "dropped_edges": [{"field": "blocks", "id": "../y", "more": "X" * 900}, "junk"] * 400,
        "origin": {"team": OLD, "id": "4", "note": "X" * 900}, "planted": "X" * 5000}
    row = tm.journal_row(NEW, planned, recovered=True)
    assert row["migrated_from"] == {
        "team": None, "id": None, "at": None, "status_was": None, "owner_was": None,
        "dropped_edges": [{"field": "blocks", "id": None}] * tm.MAX_DROPPED_EDGES,
        "origin": {"team": OLD, "id": "4"}}


# -- the journal row is the only proof of a copy -----------------------------------------------------

REAL = {"handoff": {"rev": 3, "produced": ["the real work"]}}
FORGED_CLAIMS = {
    # every field _is_copy once checked, read from the source file a teammate can open
    "direct": {"team": OLD, "id": "5", "status_was": "in_progress", "owner_was": "coder-a",
               "wait_was": None},
    "origin": {"team": "session-nowhere1", "id": "1", "origin": {"team": OLD, "id": "5"}},
}


@pytest.mark.parametrize("route", sorted(FORGED_CLAIMS))
def test_a_forged_claim_with_no_journal_row_never_retires_the_source_uncopied(root, route):
    put(root, OLD, task("5", metadata=REAL))
    put(root, NEW, {**task("9"), "subject": "subject 5", "owner": "other",
                    "metadata": {"handoff": {"rev": 1, "produced": ["forged"]},
                                 "migrated_from": FORGED_CLAIMS[route]}})
    result, rows = run(root)
    assert result.recovered == 0 and [(m.old_id, m.new_id) for m in result.moved] == [("5", "5")]
    assert json.loads((root / NEW / "5.json").read_text())["metadata"]["handoff"] == REAL["handoff"]
    assert [(r["task_id"], r.get("recovered")) for r in rows] == [("5", None)]


def _left_standing(root, monkeypatch):
    """An honest run that copied task 1 and could not rename its source."""
    put(root, OLD, task("1", metadata={"handoff": {"rev": 1},
                                       "handoff_rejection": {"reason": "r", "revision_number": 1}}))
    monkeypatch.setattr(TaskStore, "retire", lambda self, ref, digest=None: False)
    assert run(root)[0].retire_failed == 1
    monkeypatch.undo()
    return root / NEW / "1.json"


@pytest.mark.parametrize("edit", [
    lambda t: t.update(description=t["description"] + "\nAMENDMENT: x", subject="renamed"),
    lambda t: t["metadata"].pop("handoff_rejection"),
    lambda t: t["metadata"]["migrated_from"].update(at="later", status_was="pending", extra="x"),
])
def test_an_edited_copy_is_still_the_copy_when_the_row_names_it(root, monkeypatch, edit):
    copy_path = _left_standing(root, monkeypatch)
    copy = json.loads(copy_path.read_text())
    edit(copy)
    copy_path.write_text(json.dumps(copy), encoding="utf-8")
    result, rows = run(root)
    assert (result.status, result.recovered, result.moved) == (tm.STATUS_MOVED, 1, [])
    assert sorted(p.name for p in listed(root / NEW)) == ["1.json"]
    assert sorted(p.name for p in listed(root / OLD)) == ["1.json.migrated"]
    assert [(r["recovered"], r["migrated_from"]["id"]) for r in rows] == [(True, "1")]


def _sha(root, team=OLD, task_id="1") -> str:
    return hashlib.sha256((root / team / f"{task_id}.json").read_bytes()).hexdigest()


def _row(root, source_team=OLD, source_id="1", team=NEW, task_id="1", kind="migrated", **extra):
    """The row the honest run wrote for the standing source: alone, it proves the copy."""
    return {"kind": kind, "team": team, "task_id": task_id, "source_sha256": _sha(root),
            "migrated_from": {"team": source_team, "id": source_id}, **extra}


def _through(root, team="session-mid00003", task_id="1"):
    """Two rows that lead from the standing source to its copy through a middle hop. The copy
    names the middle hop, so the chain proves the copy unless a row is dropped as unsafe."""
    copy = json.loads((root / NEW / "1.json").read_text())
    copy["metadata"]["migrated_from"].update(team=team, id=task_id)
    put(root, NEW, copy)
    return [_row(root, team=team, task_id=task_id), _row(root, source_team=team, source_id=task_id)]


HOSTILE_JOURNALS = {
    "no reader": lambda root: None,
    "raises": lambda root: (lambda: 1 / 0),
    "empty": lambda root: (lambda: []),
    "not rows": lambda root: (lambda: ["junk", None, 7, {"kind": "migrated"},
                                       {**_row(root), "migrated_from": "x"}]),
    "another kind": lambda root: (lambda: [_row(root, kind="handoff")]),
    "another source team": lambda root: (lambda: [_row(root, source_team="session-foreign1")]),
    "another destination team": lambda root: (lambda: [_row(root, team="session-foreign2")]),
    "another source id": lambda root: (lambda: [_row(root, source_id="2")]),
    "a destination id with no file": lambda root: (lambda: [_row(root, task_id="404")]),
    "no digest": lambda root: (lambda: [{k: v for k, v in _row(root).items() if k != "source_sha256"}]),
    "a digest that is not a sha256": lambda root: (lambda: [_row(root, source_sha256="0" * 63),
                                                            _row(root, source_sha256=["a" * 64])]),
    "the digest of other bytes": lambda root: (lambda: [_row(root, source_sha256="0" * 64)]),
    # Each chain below proves the copy but for the one validation of _hops it names.
    "an unsafe team": lambda root: (lambda rows=_through(root, team="../" + OLD): rows),
    "an unsafe id": lambda root: (lambda rows=_through(root, task_id="a/b"): rows),
    "a team of 65": lambda root: (lambda rows=_through(root, team="t" * (tm.MAX_TEAM_CHARS + 1)): rows),
}


@pytest.mark.parametrize("case", sorted(HOSTILE_JOURNALS))
def test_without_a_matching_row_a_standing_source_is_copied_again_never_just_retired(
        root, monkeypatch, case):
    _left_standing(root, monkeypatch)
    result, _ = run(root, copied=HOSTILE_JOURNALS[case](root))
    assert result.recovered == 0 and [(m.old_id, m.new_id) for m in result.moved] == [("1", "2")]
    assert json.loads((root / NEW / "2.json").read_text())["metadata"]["handoff"] == {"rev": 1}
    assert sorted(p.name for p in listed(root / OLD)) == ["1.json.migrated"]


def test_the_hostile_journal_rows_are_one_change_away_from_proof(root, monkeypatch):
    """The control for the rows above: the plain row, and a chain through a safe middle hop,
    do prove the copy, so each hostile case fails on the one thing it changes."""
    _left_standing(root, monkeypatch)
    assert run(root, copied=lambda: [_row(root)])[0].recovered == 1
    os.rename(root / OLD / "1.json.migrated", root / OLD / "1.json")
    rows = _through(root)
    result, _ = run(root, copied=lambda: rows)
    assert (result.recovered, result.moved) == (1, [])


def test_every_row_is_emitted_before_any_source_is_renamed(root):
    put(root, OLD, task("1"))
    put(root, OLD, task("2"))
    standing = []
    run(root, emit=lambda row: standing.append(sorted(p.name for p in listed(root / OLD))) or True)
    assert standing == [["1.json", "2.json"]] * 2
    assert sorted(p.name for p in listed(root / OLD)) == ["1.json.migrated", "2.json.migrated"]


@pytest.mark.parametrize("length, carried", [(tm.MAX_TEAM_CHARS, True), (tm.MAX_TEAM_CHARS + 1, False),
                                             (5000, False)])
def test_an_origin_team_over_the_bound_is_refused_in_the_task_and_in_the_row(length, carried):
    team = "t" * length
    source = task("1", metadata={"migrated_from": {"team": "session-mid00003", "id": "7",
                                                   "origin": {"team": team, "id": "4"}}})
    planned = tm.plan([source], set(), 0, OLD, NOW).tasks[0]
    kept = {"team": team, "id": "4"} if carried else {"team": "session-mid00003", "id": "7"}
    assert planned["metadata"]["migrated_from"]["origin"] == kept
    planned["metadata"]["migrated_from"]["origin"] = {"team": team, "id": "4"}   # written after the copy
    row = tm.journal_row(NEW, planned, source=(OLD, "1"))
    assert row["migrated_from"].get("origin") == ({"team": team, "id": "4"} if carried else None)
    assert team not in json.dumps([planned["metadata"]["migrated_from"]["team"], row]) or carried


# -- the row is bound to the source bytes and to the copy's own file -------------------------------

def _subjects(root, team):
    return sorted(json.loads(p.read_text())["subject"] for p in (root / team).glob("*.json"))


def test_a_source_written_after_the_copy_is_copied_again_not_retired(root, monkeypatch):
    """The earlier team's owner stored a HANDOFF on the source the first run could not rename:
    the row names that id, but not those bytes."""
    _left_standing(root, monkeypatch)
    put(root, OLD, task("1", metadata={"handoff": {"rev": 2, "produced": ["late work"]}}))
    result, _ = run(root)
    assert result.recovered == 0 and [(m.old_id, m.new_id) for m in result.moved] == [("1", "2")]
    assert json.loads((root / NEW / "2.json").read_text())["metadata"]["handoff"]["produced"] == ["late work"]


def test_a_retired_id_that_holds_another_task_later_is_copied_not_retired(root):
    put(root, OLD, {**task("1"), "subject": "task A"})
    assert run(root)[0].status == tm.STATUS_MOVED
    put(root, OLD, {**task("1"), "subject": "task B"})
    result, _ = run(root)
    assert result.recovered == 0 and [(m.old_id, m.new_id) for m in result.moved] == [("1", "2")]
    assert _subjects(root, NEW) == ["task A", "task B"]


def test_a_copy_whose_own_migrated_from_no_longer_names_the_source_is_not_proof(root, monkeypatch):
    """Also the state where the copy was deleted and the host gave its id to another task:
    the file at the row's id is readable, but it is not the copy."""
    copy_path = _left_standing(root, monkeypatch)
    copy_path.write_text(json.dumps({**task("1"), "subject": "unrelated task"}), encoding="utf-8")
    result, _ = run(root)
    assert result.recovered == 0 and [(m.old_id, m.new_id) for m in result.moved] == [("1", "2")]
    assert _subjects(root, NEW) == ["subject 1", "unrelated task"]


def test_a_deleted_copy_is_copied_again_under_an_id_the_store_never_issued(root, monkeypatch):
    copy_path = _left_standing(root, monkeypatch)
    assert (root / NEW / ".highwatermark").read_text() == "1"
    copy_path.unlink()
    result, _ = run(root)
    assert result.recovered == 0 and [(m.old_id, m.new_id) for m in result.moved] == [("1", "2")]
    assert (root / NEW / ".highwatermark").read_text() == "2"


@pytest.mark.parametrize("damage", [
    lambda path: path.write_text(path.read_text()[:40]),                       # cut-off JSON
    lambda path: path.write_text(json.dumps({**json.loads(path.read_text()), "id": "7"})),
], ids=["unparseable", "another id inside"])
def test_a_copy_that_does_not_read_as_a_task_leaves_the_source_in_place_and_is_named(
        root, monkeypatch, damage):
    """Neither proof nor a reason to copy. The second case fails when _proven_copy stops
    comparing the file's own id with the row's."""
    damage(_left_standing(root, monkeypatch))
    before = snapshot(root)
    result, rows = run(root)
    assert (result.status, result.recovered, result.moved, rows) == (tm.STATUS_NOTHING, 0, [], [])
    assert result.unread_copies == ["1"] and snapshot(root) == before
    note = tm.resume_tasks_note(result)
    assert f"1 task(s) of team `{OLD}` left in place" in note and "(#1)" in note


def test_a_source_written_between_the_read_and_the_rename_is_not_renamed(root, monkeypatch):
    put(root, OLD, task("1"))
    real = TaskStore.raise_highwatermark

    def write_then_raise(self, team, number, create=False):       # runs after the creates
        put(root, OLD, task("1", metadata={"handoff": {"rev": 1}}))
        return real(self, team, number, create)

    monkeypatch.setattr(TaskStore, "raise_highwatermark", write_then_raise)
    result, _ = run(root)
    assert result.retire_failed == 1 and sorted(p.name for p in listed(root / OLD)) == ["1.json"]
    monkeypatch.undo()
    again, _ = run(root)
    assert again.recovered == 0 and [(m.old_id, m.new_id) for m in again.moved] == [("1", "2")]
    assert json.loads((root / NEW / "2.json").read_text())["metadata"]["handoff"] == {"rev": 1}


def test_retired_ids_stay_reserved_in_a_store_that_becomes_a_destination(root):
    third = "session-third003"
    (root / third).mkdir()
    (root / OLD / ".highwatermark").write_text("1")
    put(root, OLD, task("2"))
    assert run(root)[0].status == tm.STATUS_MOVED
    assert (root / OLD / ".highwatermark").read_text() == "2"      # raised where the store has one
    assert not (root / third / ".highwatermark").exists()
    (root / OLD / ".highwatermark").unlink()                       # the retired name alone reserves it
    put(root, third, task("2"))
    result, _ = run(root, source=third, dest=OLD)
    assert [(m.old_id, m.new_id) for m in result.moved] == [("2", "3")]
    assert not (root / third / ".highwatermark").exists()          # never created in a source store


@pytest.mark.parametrize("source, dest", [("o" * 65, NEW), (OLD, "n" * 65)])
def test_a_team_name_over_the_bound_is_refused_with_a_reason_and_a_note(root, source, dest):
    for team in (source, dest):
        (root / team).mkdir(exist_ok=True)
    put(root, source, task("1"))
    before = snapshot(root)
    result, rows = run(root, source=source, dest=dest)
    assert (result.status, result.reason, rows) == (tm.STATUS_REFUSED, tm.REASON_LONG_TEAM, [])
    note = tm.resume_tasks_note(result)
    assert "nothing was moved" in note and "over 64 characters" in note and "o" * 65 not in note
    assert snapshot(root) == before
