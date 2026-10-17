"""
Tests for the IDLE_PREAMBLE addition to teammate_idle.py (Layer 3 of
hallucination detection safeguard).

Tests cover:
1. IDLE_PREAMBLE constant exists and has expected content
2. IDLE_PREAMBLE is prepended to systemMessage in main() output
3. Existing stall detection behavior unchanged
4. Existing idle cleanup behavior unchanged
5. hooks.json integration: new hooks registered and scripts exist
"""
import io
import json
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Tests for IDLE_PREAMBLE constant
# ---------------------------------------------------------------------------
class TestIdlePreambleConstant:
    """Verify IDLE_PREAMBLE exists with expected content."""

    def test_preamble_exists(self):
        from teammate_idle import IDLE_PREAMBLE

        assert isinstance(IDLE_PREAMBLE, str)
        assert len(IDLE_PREAMBLE) > 0

    def test_preamble_contains_idle_notification(self):
        from teammate_idle import IDLE_PREAMBLE

        assert "idle notification" in IDLE_PREAMBLE

    def test_preamble_contains_no_response_needed(self):
        from teammate_idle import IDLE_PREAMBLE

        assert "no response needed" in IDLE_PREAMBLE


# ---------------------------------------------------------------------------
# Tests for IDLE_PREAMBLE integration in main() output
# ---------------------------------------------------------------------------
class TestPreambleInMainOutput:
    """Verify IDLE_PREAMBLE is prepended to systemMessage output."""

    def _run_main_with_stall(self, teammate_name="coder-a", team_name="pact-test"):
        """Helper: run main() with a stall scenario and return captured output."""
        from teammate_idle import main

        input_data = json.dumps({
            "teammate_name": teammate_name,
            "team_name": team_name,
        })

        # Mock an in_progress task for the teammate (causes stall detection)
        mock_tasks = [
            {
                "id": "1",
                "subject": "CODE: auth",
                "status": "in_progress",
                "owner": teammate_name,
                "metadata": {},
            }
        ]

        with patch("teammate_idle.frame_team_and_name", return_value=(team_name, "")), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.iter_team_task_jsons", return_value=mock_tasks):
            # main() calls sys.exit(0) at the end
            import io as io_module
            captured_stdout = io_module.StringIO()
            with patch("sys.stdout", captured_stdout):
                with pytest.raises(SystemExit) as exc_info:
                    main()

            assert exc_info.value.code == 0
            return captured_stdout.getvalue()

    def test_in_progress_idle_does_not_emit_systemmessage(self):
        """Post-#538 C3: in_progress + idle no longer emits a systemMessage.
        The stall-nag surface was removed with detect_stall; suppressOutput
        is the expected behavior."""
        output_str = self._run_main_with_stall()
        assert output_str.strip(), "hook should still emit suppressOutput JSON"
        output = json.loads(output_str)
        assert "systemMessage" not in output

    def test_no_output_when_no_messages(self):
        """When no stall or idle cleanup, no output (no preamble either)."""
        from teammate_idle import main

        input_data = json.dumps({
            "teammate_name": "coder-a",
            "team_name": "pact-test",
        })

        # Mock: no tasks at all = no stall, no cleanup
        with patch("teammate_idle.frame_team_and_name", return_value=("pact-test", "")), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.iter_team_task_jsons", return_value=[]):
            import io as io_module
            captured_stdout = io_module.StringIO()
            with patch("sys.stdout", captured_stdout):
                with pytest.raises(SystemExit):
                    main()

            # Bare exit path: suppressOutput to prevent false "hook error"
            assert json.loads(captured_stdout.getvalue().strip()) == {"suppressOutput": True}


# ---------------------------------------------------------------------------
# Tests for hooks.json integration (new safeguard hooks registered)
# ---------------------------------------------------------------------------
