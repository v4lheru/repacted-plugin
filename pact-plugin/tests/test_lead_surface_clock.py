"""Fixed-time pins for the lead-side scan's injected clock.

Location: pact-plugin/tests/test_lead_surface_clock.py
Summary: pins that `run_surface(input_data, now=...)` measures every lead alarm
         against the injected time rather than the wall clock.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

The two fixed times sit years either side of the real clock, so a callee that
reads the wall clock instead of `now` reaches the opposite verdict. An arm
marked REVERT PROOF fails when its callee goes back to a direct clock read.
The one marked GUARD cannot, and its docstring says why. Every arm that
asserts silence also asserts the same fixture fires just past the threshold,
so an empty output cannot pass it by accident.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import missed_wake_scan as mw
from fixtures.role_frames import captured_lead_userpromptsubmit_qualified

SOURCE = Path(__file__).resolve().parents[1] / "hooks" / "missed_wake_scan.py"

NOW_AFTER = datetime(2031, 1, 1, tzinfo=timezone.utc)
NOW_BEFORE = datetime(2020, 1, 1, tzinfo=timezone.utc)
TEAM = "lead-clock-team"

MISSED_WAKE = "PACT missed-wake alarm"
MUTUAL = "POSSIBLE MUTUAL WAIT"
UNFLAGGED = "UNFLAGGED BACKGROUND WORK"
UNANCHORED = "BACKGROUND WORK DISCHARGED ON A FALLBACK ANCHOR"

# Every call in run_surface that measures time. Hand-written, not derived from
# the source, so a call removed from run_surface reddens the guard arm too.
CLOCK_TAKING_CALLS = frozenset({
    "find_stale_missed_wakes", "emit_forensic", "build_surface",
    "find_mutual_waits", "find_stale_unflagged_background",
    "emit_unflagged_forensic", "find_unanchored_waits",
})


def _task(task_id, owner, since, reason, resolver, anchor=None, wait=True):
    task = {"id": task_id, "owner": owner, "subject": "s", "status": "in_progress"}
    if wait:
        w = {"reason": reason, "expected_resolver": resolver, "since": since.isoformat()}
        if anchor is not None:
            w["covers_since"] = anchor.isoformat()
        task["metadata"] = {"intentional_wait": w}
    return task


def _record(task_id, owner, registered, idled=None):
    rec = {"agent_name": owner, "session_id": "s", "task_ids": [task_id],
           "registered_at": registered.isoformat(), "command": "./gate.sh &"}
    if idled is not None:
        rec["idled_at"] = idled.isoformat()
    return rec


@pytest.fixture
def lead(tmp_path, monkeypatch):
    """Run `run_surface` as a lead at an injected time.

    The config root is tmp_path, journal writes are captured rather than
    written, and a team name is set only once a test seeds a registry.
    """
    import importlib

    from shared import pact_context

    # background_work copies pact_context.get_team_name when it is first
    # imported. Load it before the patch below, so a copy taken while the
    # patch is active cannot outlive this test.
    importlib.import_module("shared.background_work")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = {"team": None, "events": []}
    monkeypatch.setattr(pact_context, "get_team_name", lambda: state["team"])
    monkeypatch.setattr(mw, "read_events", lambda event_type: [])
    monkeypatch.setattr(mw, "append_event", lambda event: state["events"].append(event) or True)
    monkeypatch.setattr(mw, "get_journal_path", lambda: str(tmp_path / "journal.jsonl"))

    class Lead:
        events = state["events"]

        def seed(self, records):
            state["team"] = TEAM
            path = tmp_path / "teams" / TEAM / "background_work.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"records": records}))

        def run(self, tasks, now):
            monkeypatch.setattr(mw, "get_task_list", lambda: tasks)
            return mw.run_surface(captured_lead_userpromptsubmit_qualified(), now=now) or ""

    return Lead()


class TestMissedWakeUsesTheInjectedClock:
    @staticmethod
    def _stuck(now, minutes):
        return _task("1", "alice", now - timedelta(minutes=minutes),
                     "awaiting_lead_completion", "lead")

    def test_missed_wake_fires_at_an_injected_time_past_the_threshold(self, lead):
        """REVERT PROOF. The wait is 31 minutes older than NOW_AFTER. Measured
        against the wall clock it is dated years ahead, its age is negative,
        and nothing fires."""
        assert MISSED_WAKE in lead.run([self._stuck(NOW_AFTER, 31)], NOW_AFTER)


class TestMutualWaitUsesTheInjectedClock:
    @staticmethod
    def _pair(now, minutes):
        at = now - timedelta(minutes=minutes)
        return [_task("2", "alice", at, "awaiting_peer_reply", "peer", anchor=at),
                _task("3", "bob", at, "awaiting_peer_reply", "peer", anchor=at)]

    def test_mutual_wait_fires_at_an_injected_time_past_the_threshold(self, lead):
        """REVERT PROOF. Both anchors are 31 minutes older than NOW_AFTER; the
        wall clock reads them as years ahead and not stale."""
        assert MUTUAL in lead.run(self._pair(NOW_AFTER, 31), NOW_AFTER)


class TestUnflaggedUsesTheInjectedClock:
    """Two clock reads: the 24h registry prune and `lead_stale`'s window."""

    @staticmethod
    def _launch(lead, now, registered_ago, idled_ago):
        lead.seed([_record("9", "zed", now - registered_ago, now - idled_ago)])
        return [_task("9", "zed", now, "", "", wait=False)]

    def test_unflagged_fires_at_an_injected_time_past_the_threshold(self, lead):
        """REVERT PROOF for `lead_stale`. Idled 11 minutes before NOW_AFTER; the
        wall clock reads that as years ahead and not stale."""
        tasks = self._launch(lead, NOW_AFTER, timedelta(hours=1), timedelta(minutes=11))
        assert UNFLAGGED in lead.run(tasks, NOW_AFTER)


class TestUnanchoredUsesTheInjectedClock:
    """One clock read only: the 24h registry prune. No staleness window."""

    @staticmethod
    def _covered(lead, now, registered_ago):
        lead.seed([_record("7", "alice", now - registered_ago)])
        # No covers_since, so coverage falls back to `since`, which is after
        # the launch: the wait covers the record and the anchor reads absent.
        return [_task("7", "alice", now - timedelta(minutes=30),
                      "awaiting_background_job", "external")]

    def test_unanchored_keeps_a_record_the_injected_clock_has_not_expired(self, lead):
        """REVERT PROOF. Registered an hour before NOW_BEFORE; the wall clock
        prunes it, so nothing surfaces."""
        tasks = self._covered(lead, NOW_BEFORE, timedelta(hours=1))
        assert UNANCHORED in lead.run(tasks, NOW_BEFORE)


class TestForensicEventsUseTheInjectedClock:
    def test_forensic_events_carry_the_injected_time(self, lead):
        """REVERT PROOF. Both journal events are stamped with NOW_AFTER. Without
        the injected time `make_event` stamps the wall clock."""
        lead.seed([_record("9", "zed", NOW_AFTER - timedelta(hours=1),
                           NOW_AFTER - timedelta(minutes=11))])
        tasks = [
            _task("1", "alice", NOW_AFTER - timedelta(minutes=31),
                  "awaiting_lead_completion", "lead"),
            _task("9", "zed", NOW_AFTER, "", "", wait=False),
        ]
        lead.run(tasks, NOW_AFTER)
        by_type = {e.get("type"): e.get("ts") for e in lead.events}
        assert set(by_type) == {"missed_wake", "unflagged_background_wait"}, by_type
        assert set(by_type.values()) == {"2031-01-01T00:00:00Z"}, by_type


