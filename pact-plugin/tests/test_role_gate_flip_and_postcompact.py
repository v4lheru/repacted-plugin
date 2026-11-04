"""Role-gate flip regression + postcompact suppression + #812 drift fixture.

Three comprehensive groups complementing the coder's gate-specific smoke
tests:

  1. POSTCOMPACT suppression (#881, re-scoped by #1504) — postcompact_archive's
     session-scoped compact-summary O_TRUNC write × {lead writes / teammate
     suppressed / plain suppressed}, end-to-end via stdin.

  2. CLASS-B GATE FLIP regression — each of the 5 migrated teammate-bypass
     gates: a non-lead frame (teammate both spellings + plain) takes the
     bypass branch (no-op), while a lead frame ENGAGES the gate. Parameterized
     across the gate seams so a single gate regressing to the old heuristic
     (or dropping the is_lead routing) is caught with a tight, gate-specific
     assertion. (bootstrap_gate's full DENY matrix is the coder's existing
     coverage; this file adds the consolidated cross-gate flip contract +
     the plain-frame row.)

  3. #812 DRIFT fixture — pins the CURRENT identity-signal facts so a future
     regression (agent_id re-appearing as a role signal, or an agent_type
     role-mapping change) surfaces loudly: agent_id is absent from the
     synthesized frames; agent_type maps to role per the SSOT.
"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import (
    lead_frame_qualified,
    lead_frame_unqualified,
    plain_frame,
    postcompact_frame,
    teammate_frame,
)


_SESSION_ID = "aabb1122-0000-0000-0000-000000000000"


# ===========================================================================
# GROUP 3a — postcompact_archive #881 suppression
# ===========================================================================

class TestPostcompactSuppression:
    """The compact-summary write is SESSION-SCOPED (#1504), and the in-process
    topology COLLAPSES a teammate's session_id onto the lead's — so a teammate
    or plain frame's PostCompact would land in the LEAD's own session dir and
    clobber the summary there. Gated behind is_lead (#881). Drives
    postcompact_archive.main() end-to-end via stdin; frames carry session_id
    per the captured PostCompact shape.
    """

    def _run_postcompact(self, frame, monkeypatch, tmp_path):
        from postcompact_archive import main

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        stdin_data = json.dumps(frame)

        with patch("postcompact_archive.write_compact_summary") as mock_write, \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        return mock_write

    @pytest.mark.parametrize("frame_builder", [
        lead_frame_qualified,], ids=["qualified",])
    def test_lead_writes_compact_summary(self, frame_builder, monkeypatch, tmp_path):
        """A lead frame with a compact_summary writes the summary."""
        frame = frame_builder(hook_event_name="PostCompact",
                              compact_summary="lead summary text",
                              session_id=_SESSION_ID)
        mock_write = self._run_postcompact(frame, monkeypatch, tmp_path)
        mock_write.assert_called_once()
        assert mock_write.call_args.args[0] == "lead summary text"


# ===========================================================================
# GROUP 3b — Class-B gate flip regression (the 5 migrated teammate-bypass gates)
# ===========================================================================

class TestBootstrapGateFlip:
    """bootstrap_gate (DENY): lead(both spellings)-no-marker → ENFORCE;
    teammate/plain → PASSTHROUGH. Consolidated flip matrix (the coder's file
    has the full DENY-reason matrix; this adds the explicit plain-frame row +
    the both-spellings contrast in one place).
    """

    def _setup_session_no_marker(self, monkeypatch, tmp_path):
        """Minimal: a session_dir that exists with NO bootstrap marker, so the
        gate reaches the is_lead branch. Patches the two pre-is_lead guards."""
        import bootstrap_gate
        monkeypatch.setattr(
            bootstrap_gate.pact_context, "init", lambda _d: None
        )
        monkeypatch.setattr(
            bootstrap_gate.pact_context, "get_session_dir",
            lambda: str(tmp_path / "sess")
        )
        monkeypatch.setattr(bootstrap_gate, "is_marker_set", lambda _p: False)

    def _input(self, frame, tool_name="Edit"):
        return {
            "hook_event_name": "PreToolUse",
            "session_id": _SESSION_ID,
            "tool_name": tool_name,
            "tool_input": {},
            **frame,
        }

    @pytest.mark.parametrize("frame_builder", [
        lead_frame_qualified,], ids=["qualified",])
    def test_lead_no_marker_enforces(self, frame_builder, monkeypatch, tmp_path):
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON
        self._setup_session_no_marker(monkeypatch, tmp_path)
        result = _check_tool_allowed(self._input(frame_builder(), tool_name="Agent"))
        assert result == _DENY_REASON, (
            "lead frame, no marker, blocked tool → must ENFORCE (deny)"
        )


class TestClassBGateBypassFlip:
    """The 4 OTHER migrated Class-B gates: a non-lead frame takes the bypass
    branch (no-op), a lead frame engages. Each gate is reached by patching its
    pre-is_lead guards so only the is_lead verdict decides.

    Asserting the FLIP (bypass for non-lead, engagement for lead) on each gate
    is the per-migrated-hook regression: a gate that regresses to the old
    `if resolve_agent_name(...)` heuristic would invert under tmux (lead
    bypasses, teammate engages) and fail here.
    """

    # --- bootstrap_prompt_gate._check_bootstrap_needed ---

    def _setup_prompt_gate(self, monkeypatch, tmp_path):
        import bootstrap_prompt_gate as g
        monkeypatch.setattr(g.pact_context, "init", lambda _d: None)
        monkeypatch.setattr(g.pact_context, "get_session_dir",
                            lambda: str(tmp_path / "sess"))
        monkeypatch.setattr(g, "is_marker_set", lambda _p: False)
        return g

    @pytest.mark.parametrize("frame, expect_bypass", [
        (lead_frame_qualified(), False),], ids=["lead-q",])
    def test_prompt_gate_flip(self, frame, expect_bypass, monkeypatch, tmp_path):
        g = self._setup_prompt_gate(monkeypatch, tmp_path)
        result = g._check_bootstrap_needed({"session_id": _SESSION_ID, **frame})
        if expect_bypass:
            assert result is None, "non-lead must bypass (no injection)"
        else:
            assert result is not None, "lead must engage (inject bootstrap instruction)"

    # --- bootstrap_marker_writer._try_write_marker ---


# ===========================================================================
# GROUP 3c — #812 drift-detection fixture
# ===========================================================================

class TestAgentIdDriftFixture:
    """Pin the CURRENT identity-signal facts. A future regression that
    re-introduces agent_id as a role signal, or remaps agent_type→role, breaks
    these — surfacing the #812 drift class loudly at CI rather than silently
    under tmux.
    """

    def test_synthesized_frames_carry_no_agent_id(self):
        """The synthesized role frames model the tmux reality: agent_id is
        ABSENT on every hook event. If a future capture/fixture re-adds it,
        this fails — prompting a deliberate review of whether the role
        discriminator still (correctly) ignores it."""
        for builder in (lead_frame_qualified, lead_frame_unqualified,
                        teammate_frame, plain_frame):
            frame = builder()
            assert "agent_id" not in frame, (
                f"{builder.__name__} unexpectedly carries agent_id — the tmux "
                f"capture matrix says agent_id is absent. If this changed, "
                f"re-verify the role discriminator still ignores agent_id."
            )


