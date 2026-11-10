"""Consultant background records, through the real hooks.

Location: pact-plugin/tests/test_consultant_background_seam.py
Summary: a teammate in consultant mode — owning no in_progress task, only a
         completed one — launches background work through the real Layer 1
         hook, then idles through the real Layer 2 entry point.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

WHY THROUGH THE HOOKS. The predicates on this path are pinned on their own in
test_consultant_anchor_coverage.py, and every one of those arms stays green if
the writer stops marking a consultant's record, or if the idle hook stops
discharging records for an owner with no in_progress task. Both defects fail
closed: an unmarked record quietly expires against its own completed anchor,
and a discharge that never runs quietly leaves a record behind. Only arms that
assert what the real hooks WRITE and REMOVE can see either.

THE STORE IS REAL AND CONFINED TO tmp_path. The Layer 1 hook runs as a
subprocess with HOME, CLAUDE_CONFIG_DIR and CLAUDE_PROJECT_DIR built from
nothing and pointed into the test's tmp tree; the in-process Layer 2 calls use
the same root through monkeypatched env. Nothing touches the real config root.

NO FIXED DATES. The record is stamped by the real hook at write time. A
leftover wait is placed one hour BEFORE that stamp and a covering wait AT it,
both derived from the stamp the hook wrote, so no TTL boundary can fall between
the write and the read.

THE STDIN FRAMES ARE BUILT, NOT CAPTURED. The Layer 1 frame mirrors the seam
fixture in test_track_files_background_integration.py; the TeammateIdle frame
carries only the fields `teammate_idle.main()` reads.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from clock_shift.clock_shift_env import carry_clock_shift

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "track_files.py"
TEAM = "session-consultseam"
SESSION_ID = "consult-seam-session"
PROJECT_DIR = "/consult-seam/project"
CONSULTANT = "seam-consultant"
ANCHOR_ID = "20"
ADVISORY_FRAGMENT = "background work and have no flagged wait"


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _anchor_task(wait=None) -> dict:
    task = {"id": ANCHOR_ID, "status": "completed", "owner": CONSULTANT,
            "subject": "CODE: done, now consulting"}
    if wait is not None:
        task["metadata"] = {"intentional_wait": wait}
    return task


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A real config root for one consultant: team config, session context,
    and a task store holding only a COMPLETED task owned by the consultant."""
    from shared.pact_context import project_slug

    config = tmp_path / ".claude"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", PROJECT_DIR)
    _write(config / "teams" / TEAM / "config.json", {
        "leadSessionId": SESSION_ID,
        "members": [{"name": CONSULTANT,
                     "agentId": f"{CONSULTANT}@{TEAM}",
                     "agentType": "pact-backend-coder",
                     "backendType": "in-process"}],
    })
    _write(config / "pact-sessions" / project_slug(PROJECT_DIR) / SESSION_ID
           / "pact-session-context.json",
           {"session_id": SESSION_ID, "project_dir": PROJECT_DIR, "team_name": TEAM})
    _write(config / "tasks" / TEAM / f"{ANCHOR_ID}.json", _anchor_task())
    return config


def _launch(config: Path) -> None:
    """Fire the real Layer 1 hook as a subprocess, the way hooks.json runs it."""
    frame = {
        "hook_event_name": "PostToolUse",
        "session_id": SESSION_ID,
        "tool_name": "Bash",
        "agent_type": CONSULTANT,
        "agent_id": "0123456789abcdef",
        "tool_input": {"command": "sleep 5", "run_in_background": True},
    }
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(config.parent),
        "CLAUDE_CONFIG_DIR": str(config),
        "CLAUDE_PROJECT_DIR": PROJECT_DIR,
    }
    result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(frame),
                            capture_output=True, text=True, env=carry_clock_shift(env), timeout=30)
    assert result.returncode == 0, result.stderr


def _registry(config: Path) -> list:
    path = config / "teams" / TEAM / "background_work.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("records", [])


def _tasks(config: Path) -> list:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((config / "tasks" / TEAM).glob("*.json"))]


def _set_wait(config: Path, anchor: str, reason: str) -> None:
    _write(config / "tasks" / TEAM / f"{ANCHOR_ID}.json", _anchor_task({
        "reason": reason,
        "expected_resolver": "lead",
        "since": anchor,
        "covers_since": anchor,
    }))


def _idle(capsys) -> bool:
    """One TeammateIdle tick through `main()`. True if the advisory fired."""
    from teammate_idle import main

    frame = {"hook_event_name": "TeammateIdle",
             "session_id": SESSION_ID,
             "teammate_name": CONSULTANT}
    capsys.readouterr()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 0
    out = capsys.readouterr().out.strip()
    payload = json.loads(out) if out else {}
    return ADVISORY_FRAGMENT in payload.get("systemMessage", "")


class TestAConsultantLaunchThroughTheRealHooks:
    """An owner with no in_progress task is still recorded, still surfaced
    past a wait left over from before its launch, and still discharged when it
    flags a wait that covers the launch."""

    def test_the_launch_is_recorded_on_the_COMPLETED_anchor_and_marked_so(self, root):
        _launch(root)
        records = _registry(root)
        assert len(records) == 1, (
            "an owner holding only a completed task launched background work "
            "and no record was written: %r" % (records,)
        )
        assert records[0]["task_ids"] == [ANCHOR_ID]
        assert records[0].get("anchor_completed") is True, (
            "a record for an owner with no in_progress task must carry "
            "anchor_completed=True. Without it the record expires against its "
            "own completed anchor the moment anything reads it, so the launch "
            "is invisible to every layer. Got %r" % (records[0],)
        )


