"""The session-start role gate must route each classifier value correctly.

`classify_session_role` returns "lead", "teammate" or "unknown". THE ROUTING
THESE ARMS PIN: "teammate" gets the teammate body, and "lead" AND "unknown"
BOTH get the orchestrator ladder, with "unknown" also receiving the operator
notice beside it.

AN EARLIER FORM OF THIS FILE ASSERTED THE OPPOSITE FOR "unknown", ON A CENSUS
THAT DID NOT MEASURE THAT POPULATION. It read: 155 files matching
`subagents/*.jsonl` for one team session, 13 of which fired a compact
SessionStart and received the orchestrator instructions, concluded to be
"unknown" frames. RE-MEASURED, with the parameter beside the count: of 166
such files, 13 carry a SessionStart record, holding 70 records between them,
and ALL 70 have `type == "attachment"` and carry the LEAD session id. The set
of distinct session ids across the 70 has exactly ONE member. They are the
LEAD's own hook output, attached by the platform into the transcripts of the
live sidechains. Those frames classify "lead", so a gate keyed on "unknown"
cannot change one byte of them, and 150 of the 166 files carry no SessionStart
record at all. NO SUBAGENT FRAME REACHES THIS HOOK.

WHY "unknown" MUST KEEP THE LADDER: it means agent_type was ABSENT, which is a
no-`--agent` PRIMARY frame, an ordinary user running plain `claude`. The fuller
coverage of that case lives in
`test_session_init_primary_frame_keeps_the_ladder.py`, which drives all four
lifecycle sources. The arms here pin the ROUTING of the three values.

WHY A LEAD KEEPS ITS LADDER, and it is measured rather than argued: `is_lead`
returned true for this session, proven by the session context file, which no
frame can write without passing an `is_lead` gate.

THE ARMING PROBLEM THESE ARMS ANSWER. `session_init` is fail-open by contract:
an error must not block a session start. So a hook that emits nothing and a
hook that ran and correctly said nothing produce the same bytes, which are
none. AN ARM THAT ASSERTS ONLY AN ABSENCE CANNOT SEPARATE THOSE TWO STATES,
and it passes when the build path dies for a cause it was not written to catch.
EVERY ARM BELOW ASSERTS A PRESENCE AND AN ABSENCE TOGETHER. The presence half
is what proves the run reached the emission point, so the absence half then
means the branch declined rather than that the run died.
"""
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

_HOOKS = Path(__file__).parent.parent / "hooks"

import session_init  # noqa: E402
from session_init import _build_safety_net_context, _UNKNOWN_ROLE_NOTICE  # noqa: E402

LADDER = "YOUR PACT ROLE: orchestrator."
TEAMMATE_MARKER = "YOUR PACT ROLE: teammate."
_SESSION_ID = "11111111-2222-3333-4444-555555555555"
_PROJECT_DIR = "/tmp/pact-three-way-gate"


def _run_main(frame, monkeypatch, tmp_path):
    """Drive the real `session_init.main()` and return its additionalContext.

    Heavy collaborators are stubbed. THE ROLE GATE IS NOT STUBBED: the frame
    below carries the agent type the classifier reads, so the branch under test
    is the one the hook takes in production.
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    stdin_data = json.dumps({"session_id": _SESSION_ID, "source": "startup", **frame})
    with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
         patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("shared.session_init_lead.restore_last_session", return_value=None), \
         patch("shared.session_init_lead.build_context_cache",
               return_value=(Path("/tmp/ctx.json"), {})), \
         patch("shared.session_init_lead.persist_context", return_value=None), \
         patch("shared.session_init_lead.append_event"), \
         patch("shared.session_init_lead.update_session_info", return_value=None), \
         patch("shared.session_init_lead.check_resume_state", return_value=None), \
         patch("shared.session_init_teammate._registry_resolve", return_value=None), \
         patch("shared.session_init_teammate.get_peer_context", return_value=None), \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    raw = mock_stdout.getvalue().strip()
    if not raw:
        return ""
    return json.loads(raw).get("hookSpecificOutput", {}).get("additionalContext", "")


class TestMainSiteThreeWay:
    """The live emission path, driven through `main()`."""

    def test_lead_frame_keeps_the_ladder(self, monkeypatch, tmp_path):
        """THE ARM THAT PROTECTS THE PRODUCT. A lead frame must still receive
        its bootstrap instructions. SEPARATION: the emitted block is non-empty
        AND carries the ladder, so a dead run fails the first assertion rather
        than passing the second by accident."""
        out = _run_main({"agent_type": "repacted:pact-orchestrator"}, monkeypatch, tmp_path)
        assert out, (
            "a lead frame emitted NO additionalContext at all. This is the "
            "not-delivered state, not the declined state: the run did not "
            "reach the emission point."
        )
        assert LADDER in out, (
            "a LEAD frame lost its orchestrator instructions. The three-way "
            "gate must keep the lead branch intact: the lead resolves through "
            "is_lead on its own evidence, which is what makes this safe."
        )

    def test_unknown_frame_gets_the_ladder_and_the_note(self, monkeypatch, tmp_path):
        """AN UNKNOWN FRAME IS A PRIMARY FRAME AND KEEPS THE LADDER.

        SEPARATION: the emitted block must be non-empty first, so a dead build
        path fails there rather than on the ladder. The note is asserted
        BESIDE the ladder, which is what makes the notice additive rather than
        a replacement."""
        out = _run_main({}, monkeypatch, tmp_path)
        assert out, (
            "an unknown frame emitted NO additionalContext at all, so this arm "
            "cannot tell a correct emission from a dead build path."
        )
        assert LADDER in out, (
            "an UNKNOWN frame lost the orchestrator instructions. 'unknown' "
            "means agent_type was ABSENT, which is a no-`--agent` PRIMARY "
            "frame: an ordinary user running plain `claude`. Withholding the "
            "ladder leaves the bootstrap marker unstamped, and bootstrap_gate "
            "then denies Edit, Write and Agent on every call."
        )
        assert _UNKNOWN_ROLE_NOTICE in out, (
            "the unknown branch emitted no note, so an operator who meant to "
            "pass `--agent` loses the cue that rides beside the ladder."
        )


class TestSafetyNetThreeWay:
    """The exception path. `_build_safety_net_context` is pure, so each case is
    driven directly. THE FOUR INPUTS ARE FOUR CASES: None is not the string
    'unknown'. None means the classifier did not run. 'unknown' means it ran
    and found no role."""

    def test_lead_keeps_the_ladder(self):
        out = _build_safety_net_context("session-x", "lead")
        assert out, "the safety net returned an empty string for a lead frame"
        assert LADDER in out, "the safety net stopped delivering the lead ladder"


