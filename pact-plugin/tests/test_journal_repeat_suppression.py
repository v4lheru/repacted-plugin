"""
Location: pact-plugin/tests/test_journal_repeat_suppression.py (repacted EVENTS, m8a)
Summary: session_journal's repeat suppression for variety_assessed, agent_dispatch and
         phase_transition. Between m8a and m8b a command block and hooks/journal_emitter.py
         may both write the same event; whichever runs second must write nothing, through
         BOTH writers (the CLI the blocks call, and append_event the hook calls). Keyed on
         payload, never a time window (lead ruling): a genuine re-stamp always writes, even
         one that returns to an earlier value.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from shared import session_journal as sj

WRITER = Path(sj.__file__)


def cli_write(session: Path, event_type: str, payload: dict) -> subprocess.CompletedProcess:
    """What a command block runs."""
    return subprocess.run([sys.executable, str(WRITER), "write", "--type", event_type,
                           "--session-dir", str(session), "--stdin"],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=30)


def hook_write(session: Path, event_type: str, payload: dict) -> bool:
    """What hooks/journal_emitter.py runs."""
    return sj.append_event(sj.make_event(event_type, **payload), session_dir=str(session))


def rows(session: Path, event_type: str | None = None) -> list[dict]:
    return sj.read_events_from(str(session), event_type)


DISPATCH = {"task_id": "7", "scope": "dispatch",
            "variety": {"novelty": 1, "scope": 2, "uncertainty": 2, "risk": 1, "total": 6}}


class TestHookAndBlockWriteOnce:
    @pytest.mark.parametrize("event_type,block,hook", [
        ("variety_assessed", DISPATCH,
         dict(DISPATCH, variety=dict(DISPATCH["variety"], novelty_rationale="shape"))),
        ("variety_assessed", {"task_id": "20", "variety": {"total": 7}},
         {"task_id": "20", "variety": {"total": 7}}),
        ("agent_dispatch", {"agent": "preparer", "task_id": "7", "phase": "PREPARE", "scope": []},
         {"agent": "preparer-core", "task_id": "7", "phase": "PREPARE"}),
        ("phase_transition", {"phase": "CODE", "status": "started", "skip_reason": "", "metadata": {}},
         {"phase": "CODE", "status": "started", "skip_reason": "", "metadata": {}}),
    ])
    def test_hook_first_then_block(self, tmp_path, event_type, block, hook):
        assert hook_write(tmp_path, event_type, hook)
        result = cli_write(tmp_path, event_type, block)
        assert result.returncode == 0
        assert "already recorded" in result.stderr
        assert len(rows(tmp_path, event_type)) == 1

    @pytest.mark.parametrize("event_type,payload", [
        ("variety_assessed", DISPATCH),
        ("agent_dispatch", {"agent": "architect", "task_id": "9", "phase": "ARCHITECT", "scope": []}),
        ("phase_transition", {"phase": "ARCHITECT", "status": "skipped",
                              "skip_reason": "structured_gate_passed", "metadata": {}}),
    ])
    def test_block_first_then_hook(self, tmp_path, event_type, payload):
        assert cli_write(tmp_path, event_type, payload).returncode == 0
        hook_payload = {k: v for k, v in payload.items() if k != "scope" or event_type != "agent_dispatch"}
        assert hook_write(tmp_path, event_type, hook_payload) is True    # recorded already: success
        assert len(rows(tmp_path, event_type)) == 1


class TestAgentDispatchNames:
    """architect ruling on #124: a real name is never lost to a block literal."""

    def test_a_real_name_after_the_block_literal_writes(self, tmp_path):
        assert cli_write(tmp_path, "agent_dispatch",
                         {"agent": "preparer", "task_id": "7", "phase": "PREPARE", "scope": []}).returncode == 0
        assert hook_write(tmp_path, "agent_dispatch", {"agent": "preparer-core", "task_id": "7", "phase": "PREPARE"})
        rows_ = rows(tmp_path, "agent_dispatch")
        assert [r["agent"] for r in rows_] == ["preparer", "preparer-core"]      # latest is the real name

    def test_the_block_literal_after_a_real_name_is_a_repeat(self, tmp_path):
        assert hook_write(tmp_path, "agent_dispatch", {"agent": "architect-2", "task_id": "9", "phase": "ARCHITECT"})
        assert cli_write(tmp_path, "agent_dispatch",
                         {"agent": "architect", "task_id": "9", "phase": "ARCHITECT", "scope": []}).returncode == 0
        assert [r["agent"] for r in rows(tmp_path, "agent_dispatch")] == ["architect-2"]

    def test_a_different_real_name_writes(self, tmp_path):
        assert hook_write(tmp_path, "agent_dispatch", {"agent": "coder-1", "task_id": "11", "phase": "CODE"})
        assert hook_write(tmp_path, "agent_dispatch", {"agent": "coder-2", "task_id": "11", "phase": "CODE"})
        assert len(rows(tmp_path, "agent_dispatch")) == 2

    def test_volatile_fields_never_decide(self, tmp_path):
        """ts (and v, and any unlisted field) differ between the two writes; still one row."""
        first = sj.make_event("phase_transition", phase="CODE", status="started", skip_reason="",
                              metadata={}, ts="2026-09-27T00:00:00Z")
        second = sj.make_event("phase_transition", phase="CODE", status="started", skip_reason="",
                               metadata={}, ts="2026-09-27T00:05:00Z", session_hint="volatile")
        assert sj.append_event(first, session_dir=str(tmp_path))
        assert sj.append_event(second, session_dir=str(tmp_path))
        assert len(rows(tmp_path, "phase_transition")) == 1


class TestGenuineRestampsAlwaysWrite:
    def test_two_different_restamps_close_together_both_write(self, tmp_path):
        first = dict(DISPATCH)
        second = dict(DISPATCH, variety=dict(DISPATCH["variety"], scope=3, total=7))
        assert hook_write(tmp_path, "variety_assessed", first)
        assert hook_write(tmp_path, "variety_assessed", second)
        assert [e["variety"]["total"] for e in rows(tmp_path, "variety_assessed")] == [6, 7]

    def test_a_restamp_back_to_an_earlier_value_writes(self, tmp_path):
        first = dict(DISPATCH)
        second = dict(DISPATCH, variety=dict(DISPATCH["variety"], total=7))
        for payload in (first, second, first):
            assert hook_write(tmp_path, "variety_assessed", payload)
        assert [e["variety"]["total"] for e in rows(tmp_path, "variety_assessed")] == [6, 7, 6]

    def test_the_same_phase_restarting_after_completion_writes(self, tmp_path):
        for status in ("started", "completed", "started"):
            assert hook_write(tmp_path, "phase_transition",
                              {"phase": "CODE", "status": status, "skip_reason": "", "metadata": {}})
        assert [e["status"] for e in rows(tmp_path, "phase_transition")] == ["started", "completed", "started"]

    def test_feature_and_dispatch_stamps_of_one_task_are_different_keys(self, tmp_path):
        assert hook_write(tmp_path, "variety_assessed", {"task_id": "7", "variety": {"total": 6}})
        assert hook_write(tmp_path, "variety_assessed", dict(DISPATCH, variety={"total": 6}))
        assert len(rows(tmp_path, "variety_assessed")) == 2

    def test_a_different_task_or_phase_is_never_a_repeat(self, tmp_path):
        assert hook_write(tmp_path, "agent_dispatch", {"agent": "a", "task_id": "7", "phase": "CODE"})
        assert hook_write(tmp_path, "agent_dispatch", {"agent": "a", "task_id": "8", "phase": "CODE"})
        assert hook_write(tmp_path, "agent_dispatch", {"agent": "a", "task_id": "7", "phase": "TEST"})
        assert len(rows(tmp_path, "agent_dispatch")) == 3


class TestOtherTypesUntouched:
    def test_an_undeduped_type_still_double_writes(self, tmp_path):
        payload = {"phase": "CODE", "completed_phases": [], "active_agents": [], "variety": None,
                   "pending_phases": [], "safe_to_retry": True}
        assert cli_write(tmp_path, "checkpoint", payload).returncode == 0
        assert cli_write(tmp_path, "checkpoint", payload).returncode == 0
        assert len(rows(tmp_path, "checkpoint")) == 2

    def test_the_deduped_set_is_exactly_the_three_hook_types(self):
        assert set(sj.DEDUPED_EVENT_KEYS) == {"variety_assessed", "agent_dispatch", "phase_transition"}

    def test_an_unreadable_journal_answers_not_a_repeat(self, tmp_path):
        journal = tmp_path / "session-journal.jsonl"
        journal.write_bytes(b"\xff\xfe not json\n")
        assert sj.repeats_latest_event(journal, sj.make_event("phase_transition", phase="CODE",
                                                              status="started")) is False
