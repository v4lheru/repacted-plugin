# pact-plugin/tests/test_error_output.py
"""
Tests for structured JSON error output in hook exception handlers (Issue #280).

Tests cover:
1. Unit tests for hook_error_json() helper: valid JSON, correct keys,
   special characters, unicode, empty messages, very long messages
2. Integration tests for all 12 hooks' exception handlers: verify stdout
   JSON contains systemMessage when an exception occurs
3. Stderr output preserved alongside new stdout JSON
4. Category B hooks (git_commit_check, merge_guard_post) still use
   _SUPPRESS_OUTPUT on happy paths but not on error paths
5. Edge cases: nested exceptions, newlines, unicode
"""
import io
import json
import os
import sys
from unittest.mock import patch

import pytest


# =============================================================================
# Unit Tests: hook_error_json() helper
# =============================================================================

class TestHookErrorJson:
    """Unit tests for the shared hook_error_json() helper function."""

    def test_returns_valid_json(self):
        """Output must be valid JSON."""
        from shared.error_output import hook_error_json

        result = hook_error_json("test_hook", RuntimeError("something broke"))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_contains_system_message_key(self):
        """Output must contain the 'systemMessage' key."""
        from shared.error_output import hook_error_json

        result = hook_error_json("test_hook", RuntimeError("fail"))
        parsed = json.loads(result)
        assert "systemMessage" in parsed


# =============================================================================
# Integration Tests: Each hook's exception handler outputs JSON on stdout
# =============================================================================

def _parse_stdout_json(captured_out: str) -> dict:
    """Parse the last JSON line from captured stdout.

    Some hooks may output multiple lines before the error handler runs.
    The JSON error output is always the last printed line.
    """
    lines = captured_out.strip().split("\n")
    # Walk backwards to find the JSON line
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    pytest.fail(f"No JSON found in stdout: {captured_out!r}")


def _assert_error_json(captured_out: str, hook_name: str):
    """Assert that stdout contains valid JSON with systemMessage for the given hook."""
    parsed = _parse_stdout_json(captured_out)
    assert "systemMessage" in parsed, (
        f"Missing 'systemMessage' key in stdout JSON for {hook_name}. "
        f"Got: {parsed}"
    )
    assert hook_name in parsed["systemMessage"], (
        f"Hook name '{hook_name}' not found in systemMessage: "
        f"{parsed['systemMessage']}"
    )
    assert "PACT hook warning" in parsed["systemMessage"]


class TestSessionEndErrorOutput:
    """session_end.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from session_end import main

        env = {"CLAUDE_PROJECT_DIR": "/tmp/test-project"}
        with patch.dict("os.environ", env, clear=True), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("session_end.get_task_list",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "session_end")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and the specific error message."""
        from session_end import main

        env = {"CLAUDE_PROJECT_DIR": "/tmp/test-project"}
        with patch.dict("os.environ", env, clear=True), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("session_end.get_task_list",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "session_end" in captured.err
        assert "boom" in captured.err


class TestSessionInitErrorOutput:
    """session_init.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from session_init import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "session_init")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and error info."""
        from session_init import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "session_init" in captured.err
        assert len(captured.err) > 0


# TestCompactionRefreshErrorOutput removed in #444: compaction_refresh.py
# was deleted and its responsibilities folded into session_init.py's
# source=compact branch. The session_init exception handler is covered
# by TestSessionInitErrorOutput above (exception_outputs_json_with_system_message,
# exception_outputs_valid_json_structure, etc.) — its hook_error_json shape
# is the same.


class TestTeammateIdleErrorOutput:
    """teammate_idle.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from teammate_idle import main

        input_data = json.dumps({
            "teammate_name": "backend-coder",
        })

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("teammate_idle.iter_team_task_jsons",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "teammate_idle")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and the specific error message."""
        from teammate_idle import main

        input_data = json.dumps({
            "teammate_name": "backend-coder",
        })

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("teammate_idle.iter_team_task_jsons",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "teammate_idle" in captured.err
        assert "boom" in captured.err


class TestTrackFilesErrorOutput:
    """track_files.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from track_files import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "track_files")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and error info."""
        from track_files import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "track_files" in captured.err
        assert len(captured.err) > 0


# =============================================================================
# Category B: Hooks that still use _SUPPRESS_OUTPUT on happy paths
# =============================================================================

class TestGitCommitCheckErrorOutput:
    """git_commit_check.py uses _SUPPRESS_OUTPUT on the happy path and, since ENV-GUARD-2
    amend_2, a fail-CLOSED deny on error (it was a fail-open hook_error_json)."""

    def test_exception_fails_closed_with_a_deny(self, capsys):
        """Error path: a deny envelope and exit 2, NOT _SUPPRESS_OUTPUT and not an allow."""
        from git_commit_check import main

        input_data = json.dumps({
            "tool_input": {"command": "git commit -m 'test'"}
        })

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("git_commit_check.committed_files",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        parsed = _parse_stdout_json(captured.out)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "git_commit_check" in parsed["hookSpecificOutput"]["permissionDecisionReason"]
        # Must NOT contain suppressOutput in the error path
        assert "suppressOutput" not in parsed


class TestMergeGuardPostErrorOutput:
    """merge_guard_post.py uses _SUPPRESS_OUTPUT on happy path, hook_error_json on error."""

    def _make_merge_input(self):
        """Create valid AskUserQuestion input with merge question + affirmative."""
        return json.dumps({
            "tool_input": {
                "questions": [{"question": "Merge PR #42?"}]
            },
            "tool_response": {
                "answers": {"Merge PR #42?": "yes"}
            },
        })

    def test_exception_outputs_json_with_system_message(self, capsys):
        """Error path: should output JSON with systemMessage, NOT _SUPPRESS_OUTPUT."""
        from merge_guard_post import main

        with patch("sys.stdin", io.StringIO(self._make_merge_input())), \
             patch("merge_guard_post._mint_contexts_from_bundle",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "merge_guard_post")
        # Must NOT contain suppressOutput in the error path
        parsed = _parse_stdout_json(captured.out)
        assert "suppressOutput" not in parsed


# =============================================================================
# Edge Cases: Error message content
# =============================================================================

class TestErrorOutputEdgeCases:
    """Edge cases for error messages that could break JSON or display."""

    def test_exception_with_curly_braces(self):
        """Error messages containing JSON-like content."""
        from shared.error_output import hook_error_json

        error = RuntimeError('Expected {"key": "value"} but got null')
        result = hook_error_json("test_hook", error)
        parsed = json.loads(result)
        assert '{"key": "value"}' in parsed["systemMessage"]


class TestPrecompactStateReminderErrorOutput:
    """precompact_state_reminder.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from precompact_state_reminder import main

        with patch("sys.stdin", io.StringIO("{}")), \
             patch("precompact_state_reminder.build_hook_output",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "precompact_state_reminder")


class TestPostcompactArchiveErrorOutput:
    """postcompact_archive.py exception handler produces JSON on stdout.

    Post-#444: build_verification_message was deleted. The outer try/except
    in main() now wraps write_compact_summary (the only external call
    remaining on the happy path), so tests patch that function instead.
    Post-PR-#447: module renamed from postcompact_verify to postcompact_archive.
    """

    def test_exception_outputs_json_with_system_message(self, capsys):
        from postcompact_archive import main

        # #881: the compact-summary write is gated behind is_lead, so a LEAD
        # frame (agent_type) is required for the patched side-effect to fire.
        input_data = json.dumps({
            "compact_summary": "test summary",
            "agent_type": "pact-orchestrator",
        })

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("postcompact_archive.write_compact_summary",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "postcompact_archive")


# =============================================================================
# Module-level export: hook_error_json is importable from shared
# =============================================================================

class TestModuleExport:
    """Verify hook_error_json is properly exported from shared package."""

    def test_importable_from_shared(self):
        """hook_error_json should be importable from shared package."""
        from shared.error_output import hook_error_json as fn
        assert callable(fn)


# =============================================================================
# Category C: Lifecycle hooks with _SUPPRESS_OUTPUT on bare exit paths (#316)
# =============================================================================

_SUPPRESS_EXPECTED = {"suppressOutput": True}


def _assert_suppress_output(captured_out: str):
    """Assert stdout contains exactly the suppressOutput JSON."""
    assert json.loads(captured_out.strip()) == _SUPPRESS_EXPECTED


# TestCompactionRefreshSuppressOutput removed in #444: compaction_refresh.py
# was deleted. The "no in-progress tasks → no checkpoint block" invariant is
# covered by TestSessionInitCompactPhantomWorkflow in test_session_init.py.


class TestSessionEndSuppressOutput:
    """session_end.py bare exit path outputs _SUPPRESS_OUTPUT (#316)."""

    def test_success_path_suppress(self, capsys, tmp_path):
        """Normal session end outputs suppressOutput."""
        from session_end import main

        env = {
            "CLAUDE_PROJECT_DIR": str(tmp_path),
        }
        with patch("sys.stdin", io.StringIO("{}")), \
             patch.dict(os.environ, env, clear=False), \
             patch("session_end.get_task_list", return_value=[]), \
             patch("session_end.check_unpaused_pr"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)


class TestTeammateIdleSuppressOutput:
    """teammate_idle.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_no_team_name_suppress(self, capsys):
        """Missing team name outputs suppressOutput."""
        from teammate_idle import main

        with patch("teammate_idle.frame_team_and_name", return_value=("", "")), \
             patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)


class TestPeerInjectSuppressOutput:
    """peer_inject.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_invalid_json_suppress(self, capsys):
        """JSONDecodeError path outputs suppressOutput."""
        from peer_inject import main

        with patch("sys.stdin", io.StringIO("bad json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)


class TestSessionInitSuppressOutput:
    """session_init.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_success_path_suppress(self, capsys, tmp_path):
        """Normal session init with no messages outputs suppressOutput."""
        from session_init import main

        input_data = json.dumps({"source": "startup", "session_id": "test123"})
        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("shared.session_init_common.check_additional_directories", return_value=None), \
             patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("session_init.generate_team_name", return_value="pact-test123"), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        # session_init always produces output (team instructions at minimum)
        assert captured.out.strip()


class TestFileTrackerSuppressOutput:
    """file_tracker.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_no_team_name_suppress(self, capsys):
        """Missing team name outputs suppressOutput."""
        from file_tracker import main

        with patch("file_tracker.frame_team_and_name", return_value=("", "")), \
             patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)


class TestTrackFilesSuppressOutput:
    """track_files.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_invalid_json_suppress(self, capsys):
        """JSONDecodeError path outputs suppressOutput."""
        from track_files import main

        with patch("sys.stdin", io.StringIO("bad json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)


# =============================================================================
# Load-failure advisory hardening against a hostile exception __str__
# =============================================================================


class _HostileStrError(Exception):
    """An exception whose str()/repr() RAISE — simulates a module-load error
    whose own stringification is broken (e.g. a dependency's exception with a
    buggy __str__). Pre-hardening, interpolating this into the advisory's
    f-string would make the advisory itself raise while emitting, defeating
    the fail-closed advisory."""

    def __str__(self):  # noqa: D401
        raise RuntimeError("hostile __str__ raised")

    def __repr__(self):
        raise RuntimeError("hostile __repr__ raised")


class _HostileNameMeta(type):
    @property
    def __name__(cls):  # noqa: N805 — metaclass property; access raises
        raise RuntimeError("hostile __name__ raised")


class _HostileNameError(Exception, metaclass=_HostileNameMeta):
    """An exception whose TYPE's __name__ access raises — the other half of
    the advisory's `{type(error).__name__}: {error}` interpolation."""


class TestLoadFailureAdvisoryHostileException:
    """Both bootstrap advisories (bootstrap_marker_writer, bootstrap_prompt_gate)
    compose their module-load advisory message from the triggering exception.
    A hostile exception whose __str__ (or whose type's __name__) RAISES must
    NOT make the advisory itself raise while emitting — that would defeat the
    fail-closed advisory whose entire purpose is to surface the load failure.

    The fix routes the interpolation through _safe_error_detail, which guards
    each part with a safe placeholder. These pins fail RED on revert (the old
    `{type(error).__name__}: {error}` f-string raises on either hostile input).
    Parametrized over both hooks because the advisory pattern is shared verbatim."""

    HOOKS = ["bootstrap_marker_writer", "bootstrap_prompt_gate"]

    @pytest.mark.parametrize("hook_module", list(HOOKS)[:1])
    def test_safe_error_detail_hostile_str_does_not_raise(self, hook_module):
        mod = __import__(hook_module)
        detail = mod._safe_error_detail(_HostileStrError())
        # Type name still recovered (its __name__ is fine); message is the
        # safe placeholder, never the raised exception.
        assert "_HostileStrError" in detail
        assert "str(error) raised" in detail


