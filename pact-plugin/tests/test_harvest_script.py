"""scripts/harvest.py: script-first harvest, the ledger move, the PR-9 fixes and the D6 archive.

Location: pact-plugin/tests/test_harvest_script.py.

Fixture sessions are built on disk (a config root with pact-sessions/<project>/<session>/ and
tasks/<team>/) and every verb runs against them. Nothing here touches the live config root.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import harvest  # plugin-level scripts/ is on sys.path via conftest
from shared.agent_handoff_marker import occupant_hash  # noqa: E402

TEAM = "session-aaaa1111"
SID = "aaaa1111-0000-0000-0000-000000000000"
HANDOFF = {"produced": ["x"], "decisions": ["d"], "reasoning_chain": "r", "uncertainty": [],
           "integration": ["i"], "open_questions": []}


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.session = root / "pact-sessions" / "proj" / SID
        self.session.mkdir(parents=True)
        self.tasks = root / "tasks" / TEAM
        self.tasks.mkdir(parents=True)
        self.old_ledger = root / "agent-memory" / "pact-secretary" / "session_processed_tasks.md"
        self.journal = self.session / "session-journal.jsonl"
        self.event({"type": "session_start", "ts": "2026-09-27T08:00:00Z", "source": "startup"})

    def event(self, event: dict, session: Path | None = None) -> None:
        path = (session or self.session) / "session-journal.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"v": 1, **event}) + "\n")

    def handoff_event(self, task_id, agent, subject, handoff, ts, session=None):
        self.event({"type": "agent_handoff", "agent": agent, "task_id": str(task_id),
                    "task_subject": subject, "handoff": handoff, "ts": ts}, session)

    def task(self, task_id, status="completed", owner="coder", subject="coder: work",
             metadata=None, blocked_by=None):
        data = {"id": str(task_id), "subject": subject, "status": status, "owner": owner,
                "blockedBy": blocked_by or [], "metadata": metadata or {}}
        (self.tasks / f"{task_id}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
        return data

    def candidates(self, include=()):
        return harvest.candidates(self.session, TEAM, self.tasks, self.old_ledger, include)


@pytest.fixture
def fx(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    return Fixture(tmp_path)


def _unit(out, task_id):
    return [u for u in out["units"] if u["task_id"] == str(task_id)]


# --- the population recovers every HANDOFF -----------------------------------------------------

def test_a_fixture_harvest_recovers_every_handoff(fx):
    fx.handoff_event(1, "coder", "coder: work", HANDOFF, "2026-09-27T08:01:00Z")      # journal only
    fx.task(2, metadata={"handoff": HANDOFF})                                          # task file only
    fx.handoff_event(3, "coder", "coder: work", {**HANDOFF, "decisions": ["journal"]},
                     "2026-09-27T08:02:00Z")
    fx.task(3, metadata={"handoff": {**HANDOFF, "decisions": ["disk"], "integration": []}})
    out = fx.candidates()
    assert out["population"] == ["1", "2", "3"]
    assert {u["task_id"] for u in out["units"] if u["handoff"]} == {"1", "2", "3"}
    merged = _unit(out, 3)[0]["handoff"]
    assert merged["decisions"] == ["disk"]            # the task file wins a conflict
    assert merged["integration"] == ["i"]             # except present-and-empty over content
    assert "handoff divergence check unavailable for task 1: task file absent" in out["reports"]


def test_a_missing_journal_still_recovers_every_handoff_from_task_files(fx):
    fx.journal.unlink()
    for tid in (4, 5, 6):
        fx.task(tid, metadata={"handoff": HANDOFF})
    out = fx.candidates()
    assert [u["task_id"] for u in out["units"]] == ["4", "5", "6"]
    assert all(u["carriers"] == ["task_file"] for u in out["units"])


def test_the_latest_emit_wins_and_an_equal_ts_keeps_the_later_line(fx):
    for n, ts in enumerate(("2026-09-27T08:01:00Z", "2026-09-27T08:05:00Z", "2026-09-27T08:05:00Z")):
        fx.handoff_event(7, "coder", "coder: work", {**HANDOFF, "produced": [f"v{n}"]}, ts)
    assert _unit(fx.candidates(), 7)[0]["handoff"]["produced"] == ["v2"]


def test_a_reused_task_id_yields_one_unit_per_identity(fx):
    fx.handoff_event(8, "architect", "architect: design", HANDOFF, "2026-09-27T08:01:00Z")
    fx.handoff_event(8, "coder", "coder: work", HANDOFF, "2026-09-27T08:02:00Z")
    fx.task(8, owner="coder", subject="coder: work", metadata={"handoff": HANDOFF})
    out = fx.candidates()
    assert sorted(u["agent"] for u in _unit(out, 8)) == ["architect", "coder"]
    assert ("handoff identity mismatch for task 8: task file present, no event group carries its "
            "identity") in out["reports"]


def test_a_key_case_mismatch_is_read_and_named(fx):
    fx.task(9, metadata={"Handoff": HANDOFF})
    out = fx.candidates()
    assert _unit(out, 9)[0]["handoff"] == HANDOFF
    assert any("key 'Handoff' (key-case mismatch)" in r for r in out["reports"])


def test_no_handoff_found_is_its_own_message_and_a_coverage_flag(fx):
    fx.task(10, owner="auditor", subject="auditor: observe",
            metadata={"audit_summary": "GREEN", "audit_summary_authored": "GREEN"})
    out = fx.candidates()
    row = [r for r in out["coverage"] if r["task_id"] == "10"][0]
    assert row["flag"] == "no HANDOFF carrier"
    assert row["other_keys"] == ["audit_summary", "audit_summary_authored"]
    assert any(r.startswith("task 10: no HANDOFF found") for r in out["reports"])
    assert not any("key-case mismatch" in r for r in out["reports"])


def test_coverage_names_each_carrier_including_a_snapshot(fx):
    """verify-then-close: a snapshot-only task is visible as a carrier."""
    fx.handoff_event(11, "coder", "coder: work", HANDOFF, "2026-09-27T08:01:00Z")
    fx.task(11, metadata={"handoff": HANDOFF})
    fx.event({"type": "task_metadata_snapshot", "task_id": "12", "subject": "coder: work",
              "occupant": occupant_hash("coder", "coder: work"),
              "metadata": {"post_completion_verification": "ok"}, "ts": "2026-09-27T08:03:00Z"})
    fx.task(12, metadata={"post_completion_verification": "ok"})
    rows = {r["task_id"]: r for r in fx.candidates()["coverage"]}
    assert rows["11"]["handoff_carriers"] == ["journal", "task_file"]
    assert "snapshot" in rows["12"]["handoff_carriers"] and rows["12"]["flag"]


def test_without_a_journal_an_unaccepted_handoff_is_still_recovered_and_held(fx):
    """test-rehearsal F1: the journal emits on the HANDOFF write, so the task-file census must
    count an in_progress task that carries a HANDOFF too, or the two carriers disagree."""
    fx.journal.unlink()
    fx.task(13, status="in_progress", metadata={"handoff": HANDOFF})
    fx.task(14, metadata={"handoff": HANDOFF})
    out = fx.candidates()
    assert out["population"] == ["13", "14"]
    assert all(u["handoff"] == HANDOFF for u in out["units"])  # every HANDOFF recovered
    _harvest_all(fx, [13, 14])
    arch = harvest.archive(fx.session, TEAM, fx.tasks, None)
    assert arch["deletable"] == ["14"]  # a candidate, but never archive-eligible until completed
    assert {u["task_id"]: u["status"] for u in out["units"]} == {"13": "in_progress", "14": "completed"}
    assert ("task 13: HANDOFF not yet accepted (status in_progress); hold it until the team-lead "
            "completes the task") in out["reports"]
    assert not any(r.startswith("task 14: HANDOFF not yet accepted") for r in out["reports"])


def test_the_journal_and_task_files_name_the_same_population(fx):
    fx.handoff_event(15, "coder", "coder: work", HANDOFF, "2026-09-27T08:01:00Z")
    fx.task(15, status="in_progress", metadata={"handoff": HANDOFF})
    with_journal = fx.candidates()["population"]
    fx.journal.unlink()
    assert fx.candidates()["population"] == with_journal == ["15"]


def test_a_teachback_only_task_is_not_flagged(fx):
    """test-rehearsal F2 / amend_10: a task whose metadata holds only teachback keys is a Task A,
    never a missing HANDOFF; one teachback key beside any other key is not exempt."""
    fx.task(16, subject="coder: TEACHBACK for work",
            metadata={"teachback_submit": {"understanding": "u"}, "intentional_wait": {}})
    fx.task(17, subject="auditor: TEACHBACK and more",
            metadata={"teachback_submit": {"understanding": "u"}, "audit_summary": "GREEN"})
    out = fx.candidates()
    rows = {r["task_id"]: r for r in out["coverage"]}
    assert rows["16"]["kind"] == "teachback" and "flag" not in rows["16"]
    assert not any(r.startswith("task 16:") for r in out["reports"])
    assert rows["17"]["flag"] == "no HANDOFF carrier"


def test_signal_tasks_are_listed_separately_not_flagged(fx):
    fx.task(18, owner="team-lead", subject="ALERT: SECURITY: something leaked",
            metadata={"variety": {"total": 4}})
    fx.task(19, status="pending", owner="team-lead", subject="BLOCKER: waiting on a token")
    out = fx.candidates()
    rows = {r["task_id"]: r for r in out["coverage"]}
    assert rows["18"]["kind"] == "signal" and "flag" not in rows["18"]
    assert [s["task_id"] for s in out["signals"]] == ["18", "19"]


# --- the ledger -----------------------------------------------------------------------------------

def test_recorded_ids_leave_the_population_and_only_for_this_session(fx):
    for tid in (1, 2):
        fx.task(tid, metadata={"handoff": HANDOFF})
    harvest.record(fx.session, TEAM, ["1"], "standard")
    other = fx.session.parent / "bbbb2222-0000-0000-0000-000000000000"
    harvest.record(fx.session, TEAM, ["2"], "orphan", origin_session_dir=other)
    assert fx.candidates()["population"] == ["2"]
    path = harvest.ledger_path(fx.session, TEAM)
    assert path == fx.root / "pact-sessions" / "proj" / "processed-tasks" / f"{TEAM}.jsonl"


def test_the_ledger_is_append_only_and_a_torn_line_hides_nothing(fx):
    harvest.record(fx.session, TEAM, ["1"], "standard")
    path = harvest.ledger_path(fx.session, TEAM)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"torn": \n')
    harvest.record(fx.session, TEAM, ["2"], "incremental")
    assert harvest.processed_ids(fx.session, TEAM, None) == {"1", "2"}
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3


def test_the_old_ledger_is_read_as_a_fallback_by_union(fx):
    fx.old_ledger.parent.mkdir(parents=True)
    fx.old_ledger.write_text(
        f"# log\n\n## team={TEAM} (proj, session {SID})\n\nProcessed task IDs (this team): 1, #2\n"
        "Last processed (this team): x\nProcessed task IDs (this team): 3\n\n"
        "## team=session-other (proj, session y)\n\nProcessed task IDs (this team): 4\n",
        encoding="utf-8")
    assert harvest.processed_ids(fx.session, TEAM, fx.old_ledger) == {"1", "2", "3"}


def test_a_duplicated_old_ledger_section_is_refused(fx):
    fx.old_ledger.parent.mkdir(parents=True)
    fx.old_ledger.write_text(f"## team={TEAM} (a)\nProcessed task IDs: 1\n## team={TEAM} (b)\n",
                             encoding="utf-8")
    with pytest.raises(harvest.HarvestError, match="2 '## team="):
        harvest.processed_ids(fx.session, TEAM, fx.old_ledger)


def test_a_forced_include_widens_the_population(fx):
    fx.task(1, metadata={"handoff": HANDOFF})
    harvest.record(fx.session, TEAM, ["1"], "standard")
    assert fx.candidates(include=["1"])["population"] == ["1"]


# --- D6: archive, then delete ---------------------------------------------------------------------

def _harvest_all(fx, ids):
    harvest.record(fx.session, TEAM, [str(i) for i in ids], "standard")


def test_the_archive_round_trips_byte_identically(fx):
    fx.task(20, metadata={"handoff": HANDOFF})
    _harvest_all(fx, [20])
    before = (fx.tasks / "20.json").read_bytes()
    out = harvest.archive(fx.session, TEAM, fx.tasks, None)
    assert out["archived"] == ["20"]
    assert (fx.session / "tasks-archive" / "20.json").read_bytes() == before
    assert out["sha256"]["20"] == hashlib.sha256(before).hexdigest()
    assert harvest.archive(fx.session, TEAM, fx.tasks, None)["archived"] == ["20"]  # idempotent


def test_task_81_survives_the_archive(fx):
    """The live team's pending ALERT #81 must never be archived or offered for deletion."""
    fx.task(81, status="pending", owner="team-lead",
            subject="ALERT: SECURITY: four Supabase access tokens exposed in process args and "
                    "copied into a transcript")
    _harvest_all(fx, [81])
    out = harvest.archive(fx.session, TEAM, fx.tasks, None, ids=["81"])
    assert out["archived"] == []
    assert out["skipped"] == [{"id": "81", "reason": "status pending"}]
    assert not (fx.session / "tasks-archive").exists()


@pytest.mark.parametrize("status", ["pending", "in_progress"])
def test_an_open_task_is_never_archived(fx, status):
    fx.task(21, status=status)
    _harvest_all(fx, [21])
    assert harvest.archive(fx.session, TEAM, fx.tasks, None)["archived"] == []


def test_a_task_an_open_task_is_blocked_by_is_never_archived(fx):
    fx.task(22, metadata={"handoff": HANDOFF})
    fx.task(23, status="pending", blocked_by=["22"])
    _harvest_all(fx, [22])
    out = harvest.archive(fx.session, TEAM, fx.tasks, None)
    assert out["skipped"] == [{"id": "22", "reason": "named in an open task's blockedBy"}]


def _open_task_saying(fx, task_id, field, value):
    brief = fx.task(task_id, status="in_progress")
    brief[field] = value
    (fx.tasks / f"{task_id}.json").write_text(json.dumps(brief), encoding="utf-8")


@pytest.mark.parametrize("field, value", [
    ("description", f"Known limits: ~/.claude/tasks/{TEAM}/13.json"),
    ("description", "the list is in 13.json"),
    ("description", "Continue from #13."),
    ("description", "Read Task 13 first"),
    ("metadata", {"amend_1": {"text": "as ruled on\ntask #13"}}),
    ("blocks", ["13"]),
])
def test_a_task_an_open_task_cites_is_kept_out_of_the_deletable_list(fx, field, value):
    """A live brief pointed at a harvested task's file; the harvest deleted it and the path broke."""
    fx.task(13, metadata={"handoff": HANDOFF})
    fx.task(14, metadata={"handoff": HANDOFF})
    _open_task_saying(fx, 58, field, value)
    _harvest_all(fx, [13, 14])
    out = harvest.archive(fx.session, TEAM, fx.tasks, None)
    assert out["deletable"] == ["14"]
    assert out["skipped"] == [{"id": "13", "reason": "kept: cited by open task 58", "cited_by": ["58"]}]
    assert sorted(path.name for path in fx.tasks.iterdir()) == ["13.json", "14.json", "58.json"]


def test_a_bare_number_or_a_closed_task_is_not_a_citation(fx):
    fx.task(13, metadata={"handoff": HANDOFF})
    _open_task_saying(fx, 58, "description", "13 files, line 13, #130, task 131, 113.json, v13.json")
    closed = fx.task(59)
    closed["description"] = "see #13"
    (fx.tasks / "59.json").write_text(json.dumps(closed), encoding="utf-8")
    _harvest_all(fx, [13])
    assert harvest.archive(fx.session, TEAM, fx.tasks, None)["deletable"] == ["13"]


@pytest.mark.parametrize("text", [
    "read tasks 11 and 13 first", "upstream: tasks 11, 13", "tasks 11/13 hold the design",
    "tasks 10-13 hold the design", "tasks 12-15 hold the design", "tasks 12 to 14", "the upstream task id 13",
    "upstream task ID: 13", "taskId 13", "TaskGet(taskId=13)", "run TaskGet(13) for the brief", "see task-13",
    f"read ~/.claude/tasks/{TEAM}/13 for the design", f"read tasks/{TEAM}/13.JSON",
])
def test_a_list_a_range_an_id_form_or_the_path_without_its_suffix_cites_the_task(fx, text):
    """Second review, finding 7: only the first id of a list was a citation, and `task id 13`,
    `taskId=13` and the task file path without `.json` were none, so the file a live brief
    named was listed deletable."""
    fx.task(13, metadata={"handoff": HANDOFF})
    fx.task(99, metadata={"handoff": HANDOFF})
    _open_task_saying(fx, 58, "description", text)
    _harvest_all(fx, [13, 99])
    out = harvest.archive(fx.session, TEAM, fx.tasks, None)
    assert out["deletable"] == ["99"]
    assert out["skipped"] == [{"id": "13", "reason": "kept: cited by open task 58", "cited_by": ["58"]}]


@pytest.mark.parametrize("text", [
    "upstream 13 has the contract", "13", "tasks 130-140", "tasks 1, 2 and 130", "tasks 14-12", "multitask 13",
    "taskforce 13", f"tasks/{TEAM}/130", f"tasks/{TEAM}/13.jsonl", f"tasks/{TEAM}/13/notes", "task 1 has 13 files",
    "tasks 1-99999",
])
def test_a_number_that_is_not_in_a_task_list_is_still_not_a_citation(fx, text):
    fx.task(13, metadata={"handoff": HANDOFF})
    _open_task_saying(fx, 58, "description", text)
    _harvest_all(fx, [13])
    assert harvest.archive(fx.session, TEAM, fx.tasks, None)["deletable"] == ["13"]


@pytest.mark.parametrize("real_status, plant", [
    ("completed", {"id": "58", "status": "completed"}),            # 58 is open and cites #13
    ("in_progress", {"id": "13", "status": "completed"}),          # 13 itself is open
], ids=["a-twin-of-the-citing-task", "a-twin-of-the-task-itself"])
def test_a_file_that_claims_another_tasks_id_makes_nothing_deletable(fx, real_status, plant):
    """Second review, finding 10: tasks were keyed by the id INSIDE the file, so a planted
    zz.json claiming an existing id replaced that task: the open task citing #13 read as
    completed, or the in_progress 13 itself did, and 13 was listed deletable."""
    fx.task(13, status=real_status, metadata={"handoff": HANDOFF})
    _open_task_saying(fx, 58, "description", "see #13")
    (fx.tasks / "zz.json").write_text(json.dumps(plant), encoding="utf-8")
    _harvest_all(fx, [13, "zz"])
    out = harvest.archive(fx.session, TEAM, fx.tasks, None)
    assert out["deletable"] == [] and out["archived"] == []
    assert out["id_mismatch"] == [{"file": "zz.json", "id": plant["id"]}]
    assert {"id": "zz", "reason": "file name and inner id differ"} in out["skipped"]
    assert sorted(harvest.load_tasks(fx.tasks)) == ["13", "58", "zz"]


def test_an_unharvested_task_is_never_archived(fx):
    fx.task(24, metadata={"handoff": HANDOFF})
    out = harvest.archive(fx.session, TEAM, fx.tasks, None, ids=["24"])
    assert out["skipped"] == [{"id": "24", "reason": "not harvested in this session"}]


def test_a_dry_run_writes_nothing(fx):
    fx.task(25, metadata={"handoff": HANDOFF})
    _harvest_all(fx, [25])
    out = harvest.archive(fx.session, TEAM, fx.tasks, None, dry_run=True)
    assert out["would_archive"] == ["25"]
    assert out["archived"] == [] and out["deletable"] == []
    assert not (fx.session / "tasks-archive").exists()


def test_the_rendered_reminder_shrinks_to_the_open_set(fx):
    """After the archive, deleting the printed ids leaves only the open tasks to list."""
    for tid in range(30, 37):
        fx.task(tid, metadata={"handoff": HANDOFF})
    open_ids = {"37", "38"}
    fx.task(37, status="in_progress")
    fx.task(38, status="pending", subject="BLOCKER: waiting")
    _harvest_all(fx, list(range(30, 39)))
    out = harvest.archive(fx.session, TEAM, fx.tasks, None)
    for tid in out["deletable"]:          # what TaskUpdate(status="deleted") does to the list
        (fx.tasks / f"{tid}.json").unlink()
    assert set(harvest.load_tasks(fx.tasks)) == open_ids


def test_a_skipped_id_never_appears_in_deletable(fx):
    fx.task(26, metadata={"handoff": HANDOFF})
    fx.task(27, status="in_progress", metadata={"handoff": HANDOFF})
    fx.task(28, metadata={"handoff": HANDOFF})
    fx.task(29, status="pending", blocked_by=["28"])
    _harvest_all(fx, [26, 27, 28])
    out = harvest.archive(fx.session, TEAM, fx.tasks, None, ids=["26", "27", "28", "30"])
    assert out["deletable"] == out["archived"] == ["26"]
    assert {s["id"] for s in out["skipped"]} == {"27", "28", "30"}
    assert not {s["id"] for s in out["skipped"]} & set(out["deletable"])


def test_an_unparseable_task_file_refuses_the_archive(fx):
    """amend_11 D6-3: a malformed open task blocked on X could be what protects X, so X must not
    be archived; the archive refuses outright instead of guessing."""
    fx.task(31, metadata={"handoff": HANDOFF})
    (fx.tasks / "32.json").write_text('{"id": "32", "status": "pending", "blockedBy": ["31"], ',
                                      encoding="utf-8")
    _harvest_all(fx, [31])
    with pytest.raises(harvest.HarvestError, match="32.json"):
        harvest.archive(fx.session, TEAM, fx.tasks, None)
    assert not (fx.session / "tasks-archive").exists()
    assert any("32.json does not parse" in r for r in fx.candidates()["reports"])


# --- compact summary, per session -----------------------------------------------------------------

def test_the_session_compact_summary_is_moved_never_deleted(fx):
    (fx.session / "compact-summary.txt").write_text("summary", encoding="utf-8")
    assert harvest.compact_summary(fx.session, fx.root, False)["origin"] == "session"
    out = harvest.compact_summary(fx.session, fx.root, True)
    assert not (fx.session / "compact-summary.txt").exists()
    archived = Path(out["archived"])
    assert archived.parent == fx.session and archived.name.startswith("compact-summary-")
    assert archived.read_text(encoding="utf-8") == "summary"


def test_the_root_singleton_is_moved_into_the_session(fx):
    (fx.root / "pact-sessions" / "compact-summary.txt").write_text("root", encoding="utf-8")
    out = harvest.compact_summary(fx.session, fx.root, True)
    assert out["origin"] == "root" and Path(out["archived"]).parent == fx.session


def test_no_compact_summary_is_a_plain_none(fx):
    assert harvest.compact_summary(fx.session, fx.root, True) == {
        "path": None, "origin": None, "archived": None}


# --- orphans: the active session is included -----------------------------------------

def test_orphan_recovery_includes_the_active_session(fx):
    fx.handoff_event(40, "coder", "coder: work", HANDOFF, "2026-09-27T08:01:00Z")
    prior = fx.session.parent / "cccc3333-0000-0000-0000-000000000000"
    prior.mkdir()
    fx.handoff_event(5, "coder", "coder: work", HANDOFF, "2026-09-26T08:01:00Z", session=prior)
    found = {Path(o["session_dir"]).name: o for o in harvest.orphans(fx.root, TEAM, fx.session, None)}
    assert found[SID]["active"] is True and found[SID]["unprocessed"] == ["40"]
    assert found[prior.name]["unprocessed"] == ["5"]
    harvest.record(fx.session, TEAM, ["5"], "orphan", origin_session_dir=prior)
    assert prior.name not in {Path(o["session_dir"]).name
                              for o in harvest.orphans(fx.root, TEAM, fx.session, None)}


# --- nothing deletes a journal ---------------------------------------------------------

def test_no_verb_removes_the_sole_surviving_journal(fx):
    fx.handoff_event(50, "coder", "coder: work", HANDOFF, "2026-09-27T08:01:00Z")
    fx.task(50, metadata={"handoff": HANDOFF})
    before = fx.journal.read_bytes()
    fx.candidates()
    harvest.record(fx.session, TEAM, ["50"], "consolidation")
    harvest.archive(fx.session, TEAM, fx.tasks, None)
    harvest.orphans(fx.root, TEAM, fx.session, None)
    harvest.compact_summary(fx.session, fx.root, True)
    assert fx.journal.read_bytes() == before


# --- the CLI -------------------------------------------------------------------------------------------

def test_the_cli_refuses_a_relative_session_dir(capsys):
    assert harvest.main(["candidates", "--session-dir", "rel", "--team", TEAM]) == 2
    assert "must be absolute" in capsys.readouterr().err


def test_the_cli_prints_json_for_candidates(fx, capsys):
    fx.task(70, metadata={"handoff": HANDOFF})
    assert harvest.main(["candidates", "--session-dir", str(fx.session), "--team", TEAM,
                         "--tasks-dir", str(fx.tasks), "--old-ledger", str(fx.old_ledger)]) == 0
    assert json.loads(capsys.readouterr().out)["population"] == ["70"]
