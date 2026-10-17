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


@pytest.mark.parametrize("source", ["startup", "resume"])
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


@pytest.mark.parametrize("source", ["compact", "clear", "unknown"])
def test_a_continuing_session_never_rebuilds(tmp_path, spawned, source):
    assert sr.start_working_memory_rebuild(source, False, "/proj", str(tmp_path),
                                           str(PLUGIN_ROOT)) is None
    assert spawned == []


@pytest.mark.parametrize("session_dir, plugin_root, expected", [
    ("", str(PLUGIN_ROOT), "session directory unresolved"),
    ("SESSION", "", "pact-memory CLI not found"),
    ("SESSION", "/nonexistent-plugin-root", "pact-memory CLI not found"),
])
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


def test_the_real_spawner_refuses_under_pytest(tmp_path, monkeypatch):
    """~50 test modules drive session_init.main(); none may start a real child."""
    popen = MagicMock()
    monkeypatch.setattr(sr.subprocess, "Popen", popen)
    line = sr.start_working_memory_rebuild("startup", False, "/proj", str(tmp_path),
                                           str(PLUGIN_ROOT))
    assert popen.call_count == 0
    assert "disabled under pytest" in line


# --- working_memory_rebuild_outcome ----------------------------------------


def _result(tmp_path, text):
    (tmp_path / sr.WORKING_MEMORY_SYNC_RESULT).write_text(text, encoding="utf-8")
    return str(tmp_path)


def test_outcome_reports_the_cli_envelope(tmp_path):
    envelope = {"ok": True, "result": {"sync_status": "wrote", "projected": 3,
                                       "memory_ids": [], "project_id": "proj"}}
    session = _result(tmp_path, "warning: noise\n" + json.dumps(envelope, indent=2))
    assert sr.working_memory_rebuild_outcome(session) == (
        "- Working Memory: session-start rebuild wrote, 3 projected (project proj).")


def test_outcome_reports_a_refusal(tmp_path):
    session = _result(tmp_path, json.dumps(
        {"ok": False, "error": "SCOPE_DISAGREEMENT", "message": "env disagrees"}))
    assert sr.working_memory_rebuild_outcome(session) == (
        "- Working Memory: session-start rebuild failed: SCOPE_DISAGREEMENT: env disagrees")


def test_outcome_without_an_envelope_says_so(tmp_path):
    session = _result(tmp_path, "Traceback (most recent call last):\n  boom\n")
    assert "left no result" in sr.working_memory_rebuild_outcome(session)


def test_no_outcome_when_no_rebuild_started(tmp_path):
    assert sr.working_memory_rebuild_outcome(str(tmp_path)) is None
    assert sr.working_memory_rebuild_outcome("") is None


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


def test_session_init_rebuilds_a_session_that_resumes_nothing(monkeypatch, tmp_path, spawned):
    context = _run_session_init(monkeypatch, tmp_path, source="startup", resume_msg=None)
    assert [call[0][-1] for call in spawned] == ["sync"]
    assert "- Working Memory: rebuild started" in context


def test_session_init_holds_when_a_claim_surfaced(monkeypatch, tmp_path, spawned):
    context = _run_session_init(monkeypatch, tmp_path, source="startup",
                                resume_msg="Paused work detected: PR #42 (feat/login).")
    assert spawned == []
    assert "- Working Memory: not rebuilt, a resumption claim surfaced" in context


def test_a_later_compaction_reports_the_start_outcome(monkeypatch, tmp_path, spawned):
    _result(tmp_path, json.dumps({"ok": True, "result": {
        "sync_status": "empty", "projected": 0, "memory_ids": [], "project_id": "test-project"}}))
    context = _run_session_init(monkeypatch, tmp_path, source="compact", resume_msg=None)
    assert spawned == []
    assert "- Working Memory: session-start rebuild empty, 0 projected" in context


# --- end to end: the real CLI writes only the managed region -----------------


@pytest.mark.requires_embedding_backend
def test_the_real_sync_rewrites_only_the_memory_region(tmp_path, monkeypatch):
    from scripts.database import create_memory, ensure_initialized, get_connection
    from shared.claude_md_manager import ensure_project_memory_md

    project = tmp_path / "wmproj"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(store))
    ensure_project_memory_md()
    claude_md = project / ".claude" / "CLAUDE.md"
    before = claude_md.read_text(encoding="utf-8")
    conn = get_connection(store / "memory.db")
    ensure_initialized(conn)
    create_memory(conn, {"goal": "rebuilt by the session-start sync", "project_id": "wmproj"})
    conn.commit()
    conn.close()

    # The child runs as it would from a real hook: not under pytest (the CLI
    # refuses an unscoped store there), and with no session id that would
    # locate the LIVE session's record and disagree with this project.
    child_env = {k: v for k, v in os.environ.items()
                 if k not in ("PYTEST_CURRENT_TEST", "CLAUDE_CODE_SESSION_ID")}

    def run_now(command, cwd, out):
        subprocess.run(command, cwd=cwd, env=child_env, stdin=subprocess.DEVNULL,
                       stdout=out, stderr=subprocess.STDOUT, timeout=120, check=False)
    monkeypatch.setattr(sr, "_spawn_detached", run_now)
    session = tmp_path / "session"
    session.mkdir()
    sr.start_working_memory_rebuild("startup", False, str(project), str(session),
                                    str(PLUGIN_ROOT))

    assert sr.working_memory_rebuild_outcome(str(session)).startswith(
        "- Working Memory: session-start rebuild wrote, 1 projected (project wmproj)")
    after = claude_md.read_text(encoding="utf-8")
    start, end = "<!-- PACT_MEMORY_START -->", "<!-- PACT_MEMORY_END -->"
    assert "rebuilt by the session-start sync" in after.split(start)[1].split(end)[0]
    assert after.split(start)[0] == before.split(start)[0]
    assert after.split(end)[1] == before.split(end)[1]
