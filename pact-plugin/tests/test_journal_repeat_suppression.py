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
         dict(DISPATCH, variety=dict(DISPATCH["variety"], novelty_rationale="shape"))),])
    def test_hook_first_then_block(self, tmp_path, event_type, block, hook):
        assert hook_write(tmp_path, event_type, hook)
        result = cli_write(tmp_path, event_type, block)
        assert result.returncode == 0
        assert "already recorded" in result.stderr
        assert len(rows(tmp_path, event_type)) == 1

    @pytest.mark.parametrize("event_type,payload", [
        ("variety_assessed", DISPATCH),])
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


class TestGenuineRestampsAlwaysWrite:
    def test_two_different_restamps_close_together_both_write(self, tmp_path):
        first = dict(DISPATCH)
        second = dict(DISPATCH, variety=dict(DISPATCH["variety"], scope=3, total=7))
        assert hook_write(tmp_path, "variety_assessed", first)
        assert hook_write(tmp_path, "variety_assessed", second)
        assert [e["variety"]["total"] for e in rows(tmp_path, "variety_assessed")] == [6, 7]


class TestOtherTypesUntouched:
    def test_an_undeduped_type_still_double_writes(self, tmp_path):
        payload = {"phase": "CODE", "completed_phases": [], "active_agents": [], "variety": None,
                   "pending_phases": [], "safe_to_retry": True}
        assert cli_write(tmp_path, "checkpoint", payload).returncode == 0
        assert cli_write(tmp_path, "checkpoint", payload).returncode == 0
        assert len(rows(tmp_path, "checkpoint")) == 2


