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


# --- D6: archive, then delete ---------------------------------------------------------------------

def _harvest_all(fx, ids):
    harvest.record(fx.session, TEAM, [str(i) for i in ids], "standard")


def _open_task_saying(fx, task_id, field, value):
    brief = fx.task(task_id, status="in_progress")
    brief[field] = value
    (fx.tasks / f"{task_id}.json").write_text(json.dumps(brief), encoding="utf-8")


# --- compact summary, per session -----------------------------------------------------------------


# --- orphans: the active session is included -----------------------------------------


# --- nothing deletes a journal ---------------------------------------------------------


# --- the CLI -------------------------------------------------------------------------------------------


