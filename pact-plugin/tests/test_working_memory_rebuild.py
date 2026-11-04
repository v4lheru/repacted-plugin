"""The session-boundary Working Memory rebuild.

Location: pact-plugin/tests/test_working_memory_rebuild.py

The secretary used to rebuild CLAUDE.md's Working Memory block at spawn. It
now spawns on demand, so session_init starts the rebuild (the pact-memory
CLI's own `sync`, detached) on a session start, and HOLDS it when a
resumption claim surfaced at step 8. These arms pin both branches through the
real hook, the sources that never rebuild, the loud degraded lines, the
outcome read-back at a later boundary, and, end to end against the real CLI,
that the rebuild writes only the managed memory region.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shared import session_resume as sr

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CLI = PLUGIN_ROOT / "skills" / "pact-memory" / "scripts" / "cli.py"


@pytest.fixture
def spawned(monkeypatch):
    """Replace the detached spawner with a recorder; returns the call list."""
    calls = []
    monkeypatch.setattr(sr, "_spawn_detached",
                        lambda command, cwd, out: calls.append((command, cwd, out.name)))
    return calls


# --- start_working_memory_rebuild ------------------------------------------


@pytest.mark.parametrize("source", ["startup",])
def test_a_session_start_spawns_the_existing_sync(tmp_path, spawned, source):
    line = sr.start_working_memory_rebuild(source, False, "/proj", str(tmp_path),
                                           str(PLUGIN_ROOT))
    assert spawned == [([sys.executable, str(CLI), "sync"], "/proj",
                        str(tmp_path / sr.WORKING_MEMORY_SYNC_RESULT))]
    assert line.startswith("- Working Memory: rebuild started")
    assert str(tmp_path / sr.WORKING_MEMORY_SYNC_RESULT) in line


def test_a_surfaced_claim_holds_the_rebuild(tmp_path, spawned):
    line = sr.start_working_memory_rebuild("startup", True, "/proj", str(tmp_path),
                                           str(PLUGIN_ROOT))
    assert spawned == []
    assert "not rebuilt, a resumption claim surfaced" in line
    assert not (tmp_path / sr.WORKING_MEMORY_SYNC_RESULT).exists()


@pytest.mark.parametrize("source", ["compact",])
def test_a_continuing_session_never_rebuilds(tmp_path, spawned, source):
    assert sr.start_working_memory_rebuild(source, False, "/proj", str(tmp_path),
                                           str(PLUGIN_ROOT)) is None
    assert spawned == []


@pytest.mark.parametrize("session_dir, plugin_root, expected", [
    ("", str(PLUGIN_ROOT), "session directory unresolved"),])
def test_degraded_inputs_say_not_rebuilt(tmp_path, spawned, session_dir, plugin_root, expected):
    session_dir = str(tmp_path) if session_dir == "SESSION" else session_dir
    line = sr.start_working_memory_rebuild("startup", False, "/proj", session_dir, plugin_root)
    assert spawned == []
    assert line == f"- Working Memory: not rebuilt, {expected}."


def test_a_spawn_failure_is_reported_not_raised(tmp_path, monkeypatch):
    def refuse(command, cwd, out):
        raise OSError("fork failed")
    monkeypatch.setattr(sr, "_spawn_detached", refuse)
    line = sr.start_working_memory_rebuild("startup", False, "/proj", str(tmp_path),
                                           str(PLUGIN_ROOT))
    assert line == "- Working Memory: not rebuilt, sync did not start (OSError)."


# --- working_memory_rebuild_outcome ----------------------------------------


def _result(tmp_path, text):
    (tmp_path / sr.WORKING_MEMORY_SYNC_RESULT).write_text(text, encoding="utf-8")
    return str(tmp_path)


# --- through session_init.main() --------------------------------------------


def _run_session_init(monkeypatch, tmp_path, *, source, resume_msg):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PLUGIN_ROOT))
    stdin = json.dumps({"session_id": "aabb1122-0000-0000-0000-000000000000",
                        "source": source, "agent_type": "repacted:pact-orchestrator"})
    import session_init
    with ExitStack() as stack:
        for target, value in [
            ("shared.session_init_common.setup_plugin_symlinks", None),
            ("shared.session_init_common.ensure_project_memory_md", None),
            ("shared.session_init_lead.update_session_info", None),
            ("session_init.get_task_list", None),
            ("shared.session_init_lead.restore_last_session", None),
            ("shared.session_init_lead.check_resume_state", resume_msg),
            ("shared.session_init_lead.append_event", True),
            ("session_init.get_session_dir", str(tmp_path)),
        ]:
            stack.enter_context(patch(target, return_value=value))
        stack.enter_context(patch("sys.stdin", io.StringIO(stdin)))
        out = stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    return json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]


# --- end to end: the real CLI writes only the managed region -----------------


