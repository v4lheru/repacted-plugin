"""session_init per-write role-suppression matrix (#877) — end-to-end.

The comprehensive complement to the coder's function-level verification in
test_session_init.py. Drives the REAL session_init.main() end-to-end via
synthetic stdin (under the established Path.home/tmp_path isolation) so the
actual ``frame_is_lead = is_lead(input_data)`` computation and the gating
branches execute — not a mock of them.

THE MATRIX: each of session_init's 4 Class-A lead-only writes ×
{lead / teammate / plain}:

    write                         | lead | teammate | plain
    ------------------------------|------|----------|------
    write_context disk-write      | runs | SUPPRESS | SUPPRESS
    append_event(session_start)   | runs | SUPPRESS | SUPPRESS
    update_session_info           | runs | SUPPRESS | SUPPRESS
    check_resume_state            | runs | SUPPRESS | SUPPRESS

CRUCIAL split-correctness property (the one non-mechanical site, #877):
``write_context`` populates the in-process ``_cache`` / ``_context_path``
UNCONDITIONALLY (every frame) so ``get_session_dir()`` / append_event's
path-resolution keep working — but gates ONLY the on-disk write on is_lead.
A teammate/plain frame must get the cache populated AND no disk file written.
``TestWriteContextSplit`` exercises the REAL write_context (not a mock) to pin
this.

Lead path is the coder's existing coverage (test_session_init.py
TestWriteContextIntegration etc.); this file is the SUPPRESSION-side
comprehensiveness add: teammate + plain rows for every write, plus the split.
"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import (
    lead_frame_qualified,
    plain_frame,
    teammate_frame,
)


_SESSION_ID = "aabb1122-0000-0000-0000-000000000000"
_PROJECT_DIR = "/Users/example/Sites/test-project"


def _stdin_for(frame: dict) -> str:
    """Build a SessionStart stdin payload carrying ``frame``'s role
    discriminator + a valid session_id (so the writes are reached and only
    the is_lead gate decides suppression)."""
    payload = {"session_id": _SESSION_ID, **frame}
    return json.dumps(payload)


def _run_main_with(stdin_data: str, monkeypatch, tmp_path):
    """Run session_init.main() end-to-end with the heavy collaborators
    patched out, returning the four gated-write mocks for assertion.

    Patches the SAME collaborator set the coder's integration tests use so we
    isolate the is_lead gating decision. Returns a dict of the 4 write mocks.
    """
    from session_init import main

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
         patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("shared.session_init_lead.restore_last_session", return_value=None), \
         patch("shared.session_init_lead.build_context_cache",
               return_value=(Path("/tmp/ctx.json"), {})) as mock_build_ctx, \
         patch("shared.session_init_lead.persist_context", return_value=None) as mock_persist, \
         patch("shared.session_init_lead.append_event") as mock_append, \
         patch("shared.session_init_lead.update_session_info", return_value=None) as mock_update, \
         patch("shared.session_init_lead.check_resume_state", return_value=None) as mock_paused, \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO):
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0
    return {
        # #878 SHAPE-2: the disk/cache seam is two functions. build_context_cache
        # runs for EVERY frame (cache always populated); persist_context (the disk
        # side-effect) runs ONLY for a lead frame.
        "build_context_cache": mock_build_ctx,
        "persist_context": mock_persist,
        "append_event": mock_append,
        "update_session_info": mock_update,
        "check_resume_state": mock_paused,
    }


def _session_start_calls(mock_append):
    """The session_start append_event calls (filtering out any other event
    types the hook may append)."""
    return [
        c for c in mock_append.call_args_list
        if c.args and c.args[0].get("type") == "session_start"
    ]


# ===========================================================================
# LEAD rows — every write RUNS (both spellings).
# ===========================================================================

class TestLeadRowsAllWritesRun:
    """For a lead frame (both spellings), all 4 Class-A writes fire."""

    @pytest.mark.parametrize("frame_builder", [
        lead_frame_qualified,], ids=["qualified",])
    def test_lead_runs_all_four_writes(self, frame_builder, monkeypatch, tmp_path):
        mocks = _run_main_with(_stdin_for(frame_builder()), monkeypatch, tmp_path)

        # #878 SHAPE-2: cache is built for every frame; the lead ALSO persists to
        # disk (persist_context fires).
        mocks["build_context_cache"].assert_called_once()
        mocks["persist_context"].assert_called_once()
        # session_start journal anchor appended.
        assert _session_start_calls(mocks["append_event"]), (
            "lead frame must append the session_start journal anchor"
        )
        # CLAUDE.md Current Session block written.
        mocks["update_session_info"].assert_called_once()
        # paused-state surface checked.
        mocks["check_resume_state"].assert_called_once()

    def test_lead_with_missing_session_id_suppresses_all_writes(
        self, monkeypatch, tmp_path
    ):
        """#9: a LEAD frame with NO session_id suppresses ALL Class-A writes —
        including update_session_info. The is_lead gate is necessary but not
        sufficient: the `session_id_was_missing` guard (R3) independently skips
        every persistence call so a malformed-stdin lead can't leak an
        unreapable `pact-sessions/.../unknown-xxxx/` dir or interpolate a junk
        session_id into the CLAUDE.md Current Session block. This pins that the
        two gates compose (lead AND valid-session_id), not lead alone."""
        # Lead frame (qualified) but OMIT session_id → session_id_was_missing.
        stdin = json.dumps({**lead_frame_qualified()})  # no session_id key
        mocks = _run_main_with(stdin, monkeypatch, tmp_path)

        # The whole `if not session_id_was_missing` block is skipped: neither
        # half of the build/persist seam, nor the journal anchor, runs.
        mocks["build_context_cache"].assert_not_called()
        mocks["persist_context"].assert_not_called()
        assert _session_start_calls(mocks["append_event"]) == [], (
            "missing-session_id lead must NOT append the session_start anchor"
        )
        # update_session_info is gated by `frame_is_lead AND not
        # _is_unknown_or_missing_session` — the missing-session_id arm suppresses
        # it even though the frame IS the lead.
        mocks["update_session_info"].assert_not_called()


# ===========================================================================
# TEAMMATE + PLAIN rows — every write is SUPPRESSED.
# ===========================================================================

class TestNonLeadRowsAllWritesSuppressed:
    """For a teammate or plain frame, all 4 Class-A writes are suppressed.

    build_context_cache is still CALLED (the cache must be populated — see
    TestWriteContextSplit) but persist_context (the disk write) is NOT called;
    the other 3 writes are not called at all.
    """

    @pytest.mark.parametrize("frame_builder, role", [
        (teammate_frame, "teammate"),])
    def test_non_lead_suppresses_disk_write_and_other_writes(
        self, frame_builder, role, monkeypatch, tmp_path
    ):
        mocks = _run_main_with(_stdin_for(frame_builder()), monkeypatch, tmp_path)

        # #878 SHAPE-2: build_context_cache IS called (cache population is
        # unconditional) but persist_context (the disk write) is NOT — the disk
        # side-effect is gated on is_lead.
        mocks["build_context_cache"].assert_called_once()
        mocks["persist_context"].assert_not_called()
        # The other 3 lead-only writes are NOT called at all.
        assert _session_start_calls(mocks["append_event"]) == [], (
            f"{role} frame must NOT append the session_start journal anchor"
        )
        mocks["update_session_info"].assert_not_called()
        mocks["check_resume_state"].assert_not_called()


# ===========================================================================
# write_context SPLIT — the load-bearing correctness property (REAL function).
# ===========================================================================

class TestWriteContextSplit:
    """#878 SHAPE-2 seam: build_context_cache populates _cache/_context_path
    UNCONDITIONALLY (NO disk I/O); persist_context is the separate is_lead-gated
    disk write. The #877 split's correctness property. Exercises the REAL
    functions (not mocks). The autouse _reset_pact_context_state fixture
    (conftest.py) gives each test a clean cache.
    """

    def test_non_lead_populates_cache_but_writes_no_disk_file(self, monkeypatch, tmp_path):
        """Non-lead = build_context_cache ONLY (no persist): _cache +
        _context_path are set (get_session_dir works) and NO on-disk
        pact-session-context.json is created."""
        import shared.pact_context as ctx

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Pre-state: autouse reset leaves both None.
        assert ctx._cache is None and ctx._context_path is None

        result = ctx.build_context_cache(
            "pact-aabb1122", _SESSION_ID, str(tmp_path / "proj"), "",
        )
        # (no persist_context call — this is the non-lead path)

        assert result is not None
        # Cache + path populated unconditionally by the builder.
        assert ctx._cache is not None, "non-lead frame must still populate _cache"
        assert ctx._cache["session_id"] == _SESSION_ID
        assert ctx._context_path is not None, "non-lead frame must set _context_path"
        # get_session_dir() (the downstream consumer) resolves off the cache.
        assert ctx.get_session_dir(), "get_session_dir must work for a non-lead frame"
        # But NO disk file was written (persist_context was never called).
        assert not ctx._context_path.exists(), (
            "skipping persist must NOT create the on-disk session-context file "
            "(the lead-only artifact a teammate frame must not clobber)"
        )


# ===========================================================================
# UNKNOWN-ROLE STARTUP WARNING — fires for unknown role ONLY.
# ===========================================================================

# The live plugin root (this file is tests/…, parent.parent is pact-plugin/,
# which carries the real agents/pact-*.md registry). #1 resolves the recognized-
# specialist set from CLAUDE_PLUGIN_ROOT at the 0c site, so the notice tests
# point that env at the live root — exercising the REAL specialist registry
# (the SSOT), not a hand-seeded fixture.
_REAL_PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)


class TestUnknownRoleStartupWarning:
    """#1 (#878): the unknown-role notice fires when a frame has NO recognized
    role — agent_type ABSENT, OR present-but-unrecognized (typo'd) — and NOT for
    a lead frame or a RECOGNIZED specialist. Recognized = the live
    agents/pact-*.md registry, resolved at 0c from CLAUDE_PLUGIN_ROOT (env), with
    a PACT:-strip for spelling-symmetry and is_lead checked FIRST.
    """

    def _run_capture_systemmessage(
        self, stdin_data, monkeypatch, tmp_path, plugin_root=_REAL_PLUGIN_ROOT,
    ):
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        # #1: the specialist-registry resolution at 0c reads CLAUDE_PLUGIN_ROOT
        # from env. Default to the live plugin root so a recognized specialist
        # type actually resolves; pass "" to exercise the unresolvable-registry
        # fail-OPEN residual.
        if plugin_root:
            monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", plugin_root)
        else:
            monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.persist_context", return_value=None), \
             patch("shared.session_init_lead.append_event", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit):
                main()
        out = json.loads(mock_stdout.getvalue())
        return json.dumps(out)  # whole output blob; notice may be systemMessage

    def _notice_text(self):
        import session_init
        return session_init._UNKNOWN_ROLE_NOTICE

    # ---- FIRES: no recognized role ----

    def test_absent_agent_type_fires(self, monkeypatch, tmp_path):
        """(d) plain frame — agent_type ABSENT → notice fires."""
        stdin = json.dumps({"session_id": _SESSION_ID, "source": "startup",
                            **plain_frame()})
        blob = self._run_capture_systemmessage(stdin, monkeypatch, tmp_path)
        assert self._notice_text() in blob, (
            "absent agent_type on startup must emit the unknown-role notice"
        )


    # ---- DOES NOT FIRE: recognized role ----


    # ---- ONE CHANNEL PER FRAME ----

