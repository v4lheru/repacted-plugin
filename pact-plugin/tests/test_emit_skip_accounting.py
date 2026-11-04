"""Emit-skip accounting: the journal-emit seam.

`append_event_checked` was promoted from a PRIVATE helper in
task_lifecycle_gate to shared, so dispatch_gate can reach it. It had only
INCIDENTAL coverage through its callers, which vanishes silently when those
callers change; these are its direct tests. It is what turns a lost emit into
a `journal_emit_skipped` record instead of silence. An emit lost without
leaving that record is invisible here too.
"""
import sys

import pytest

from shared.session_journal import (  # noqa: E402
    SKIP_CAUSE_RAISED,
    SKIP_CAUSE_RETURNED_FALSE,
    append_event_checked,
)


def skip(skipped_type, cause=SKIP_CAUSE_RETURNED_FALSE):
    return {"type": "journal_emit_skipped", "skipped_type": skipped_type,
            "cause": cause}


# =============================================================================
# append_event_checked — the promoted seam's FIRST direct coverage
# =============================================================================
class TestPromotedSeam:
    @pytest.fixture
    def spy(self, monkeypatch):
        """Capture what reaches the journal, without touching a real one."""
        import shared.session_journal as sj
        written: list[dict] = []

        def fake(event):
            written.append(event)
            return not event.get("_fail")

        monkeypatch.setattr(sj, "append_event", fake)
        return written

    def test_success_returns_true_and_records_no_skip(self, spy):
        assert append_event_checked({"v": 1, "type": "x"}, "x") is True
        assert [e for e in spy if e.get("type") == "journal_emit_skipped"] == []

    def test_returned_false_records_a_skip_with_that_cause(self, spy):
        assert append_event_checked({"v": 1, "type": "x", "_fail": True}, "x") is False
        skips = [e for e in spy if e.get("type") == "journal_emit_skipped"]
        assert len(skips) == 1
        assert skips[0]["skipped_type"] == "x"
        assert skips[0]["cause"] == SKIP_CAUSE_RETURNED_FALSE

    def test_raise_records_a_DISTINCT_cause(self, monkeypatch):
        """RAISED must not collapse into RETURNED-FALSE. A writer defect and a
        schema rejection have different remedies; one 'failed' bucket rebuilds
        the ambiguity capturing the bool removed."""
        import shared.session_journal as sj
        written: list[dict] = []
        calls = {"n": 0}

        def fake(event):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("journal exploded")
            written.append(event)
            return True

        monkeypatch.setattr(sj, "append_event", fake)
        assert append_event_checked({"v": 1, "type": "x"}, "x") is False
        assert written[0]["cause"] == SKIP_CAUSE_RAISED
        assert written[0]["cause"] != SKIP_CAUSE_RETURNED_FALSE


