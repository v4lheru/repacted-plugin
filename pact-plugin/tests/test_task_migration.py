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
from shared.task_store import TaskStore

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


@pytest.mark.parametrize("highwater", [True,])
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
    {"id": "../x"},])
def test_an_open_task_of_the_wrong_shape_is_counted_and_not_planned(bad):
    planned = tm.plan([{**task("1"), **bad}, task("2")], set(), 0, OLD, NOW)
    assert [t["id"] for t in planned.tasks] == ["2"]
    assert (planned.open_count, planned.skipped) == (2, 1)


@pytest.mark.parametrize("status", ["completed",])
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
    ({"handoff": {"rev": 3}}, "HANDOFF rev 3 stored"),])
def test_stored_state_prints_presence_and_a_bounded_rev_only(metadata, state):
    assert tm.stored_state(metadata) == state


def test_the_ops_dispatch_and_teachback_cross_a_resume_and_read_as_a_stored_teachback():
    # 2.1.0: `repact dispatch` and `repact submit` store metadata.dispatch and
    # metadata.teachback.submit; a resume carries both and the note reads the nested teachback.
    dispatch = {"name": "coder-a", "teachback": "two_phase"}
    teachback = {"submit": {"understanding": "u"}, "at": NOW}
    planned = tm.plan([task("4", metadata={"dispatch": dispatch, "teachback": teachback})], set(), 0, OLD, NOW)
    carried = planned.tasks[0]["metadata"]
    assert carried["dispatch"] == dispatch and carried["teachback"] == teachback
    assert tm.stored_state(carried) == "teachback stored"


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
    ("..", NEW),])
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


# -- the TaskStore writers ------------------------------------------------------------------------------


# -- review fixes: proof of a copy, numbers the host cannot hold, the journal row -------------------


# -- the journal row is the only proof of a copy -----------------------------------------------------

REAL = {"handoff": {"rev": 3, "produced": ["the real work"]}}
FORGED_CLAIMS = {
    # every field _is_copy once checked, read from the source file a teammate can open
    "direct": {"team": OLD, "id": "5", "status_was": "in_progress", "owner_was": "coder-a",
               "wait_was": None},
    "origin": {"team": "session-nowhere1", "id": "1", "origin": {"team": OLD, "id": "5"}},
}


def _left_standing(root, monkeypatch):
    """An honest run that copied task 1 and could not rename its source."""
    put(root, OLD, task("1", metadata={"handoff": {"rev": 1},
                                       "handoff_rejection": {"reason": "r", "revision_number": 1}}))
    monkeypatch.setattr(TaskStore, "retire", lambda self, ref, digest=None: False)
    assert run(root)[0].retire_failed == 1
    monkeypatch.undo()
    return root / NEW / "1.json"


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


# -- the row is bound to the source bytes and to the copy's own file -------------------------------

def _subjects(root, team):
    return sorted(json.loads(p.read_text())["subject"] for p in (root / team).glob("*.json"))


