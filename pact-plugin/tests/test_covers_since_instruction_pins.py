"""What the lead is told about a wait that lacks the `covers_since` field.

Location: pact-plugin/tests/test_covers_since_instruction_pins.py
Summary: pins that the lead-facing surface for a missing anchor names every
         route to it and asserts none.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

The wording pins of each template site were dropped in 1.5.0; git has them.

A CORRECT FIRST SET HAS NOTHING TO REPORT. A wait written as instructed carries
an anchor, so the lead-side scan stays silent about it. A wait without one can
come from routes the scan cannot tell apart, so its surface names them all.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import missed_wake_scan as mw
from fixtures.role_frames import captured_lead_userpromptsubmit_qualified


def _phrase(text: str) -> str:
    return " ".join(text.replace("`", "").split())


LEAD_TEAM = "covers-since-team"


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


class TestTheMissingAnchorSurfaceAssertsNoRoute:
    """A wait with no anchor may predate the field, may come from an older
    instruction or a template that omits it, or may have lost the field on a
    re-SET. The scan sees the same absence in every case, so the lead is told
    all of them and none is asserted."""

    def test_the_ABSENT_explanation_names_every_route_and_asserts_none(self):
        task = {"id": "7", "owner": "alice", "status": "in_progress",
                "metadata": {"intentional_wait": {"reason": "awaiting_blocker_resolution",
                                                  "expected_resolver": "peer",
                                                  "since": _ago(90)}}}
        surface = _phrase(mw.build_unanchored_surface([(task, "absent")]) or "")
        for route in ("predates the field",
                      "was written under the earlier instruction or from a template that omits it",
                      "an agent dropped it on a re-SET"):
            assert route in surface, (
                f"the missing-anchor surface no longer names the route {route!r}; "
                f"a lead told fewer routes than exist will act on the wrong one: {surface!r}"
            )
        assert "the scan cannot tell which" in surface, surface
        assert "no teammate is at fault" not in surface, (
            "the surface asserts that no teammate is at fault. A wait missing its "
            "anchor can be an agent dropping the field on a re-SET, so that claim "
            f"is one the scan cannot make: {surface!r}"
        )


@pytest.fixture
def lead_scan(tmp_path, monkeypatch):
    """Run `run_surface` as a lead over one wait, with a real registry record
    launched 100 minutes ago that the wait, SET 90 minutes ago, covers."""
    import importlib

    from shared import pact_context

    # background_work copies pact_context.get_team_name when it is first
    # imported. Load it before the patch below, so a copy taken while the
    # patch is active cannot outlive this test.
    importlib.import_module("shared.background_work")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    team_dir = tmp_path / "teams" / LEAD_TEAM
    team_dir.mkdir(parents=True)
    (team_dir / "background_work.json").write_text(json.dumps({"records": [{
        "agent_name": "alice", "session_id": "s", "task_ids": ["7"],
        "registered_at": _ago(100)}]}), encoding="utf-8")
    monkeypatch.setattr(pact_context, "get_team_name", lambda: LEAD_TEAM)
    monkeypatch.setattr(mw, "read_events", lambda et: [])
    monkeypatch.setattr(mw, "append_event", lambda e: True)
    monkeypatch.setattr(mw, "get_journal_path", lambda: str(tmp_path / "journal.jsonl"))

    def run(anchored: bool) -> str:
        since = _ago(90)
        wait = {"reason": "awaiting_blocker_resolution", "expected_resolver": "peer",
                "since": since}
        if anchored:
            wait["covers_since"] = since
        task = {"id": "7", "owner": "alice", "subject": "s", "status": "in_progress",
                "metadata": {"intentional_wait": wait}}
        monkeypatch.setattr(mw, "get_task_list", lambda: [task])
        return mw.run_surface(captured_lead_userpromptsubmit_qualified()) or ""

    return run


class TestAFirstSETWrittenAsInstructedIsNotReported:

    def test_a_first_SET_carrying_covers_since_equal_to_since_raises_no_missing_anchor_surface(
        self, lead_scan
    ):
        out = lead_scan(anchored=True)
        assert "FALLBACK ANCHOR" not in out, (
            "a wait SET exactly as instructed, with `covers_since` equal to `since`, "
            f"was reported to the lead as missing its anchor: {out!r}"
        )

    def test_the_same_wait_WITHOUT_covers_since_is_reported(self, lead_scan):
        """The control for the arm above: the same setup reaches the surface, so
        that arm's silence comes from the anchor and not from a setup that
        could never report anything."""
        out = lead_scan(anchored=False)
        assert "FALLBACK ANCHOR" in out, (
            f"a covered wait with no `covers_since` did not reach the lead: {out!r}"
        )
