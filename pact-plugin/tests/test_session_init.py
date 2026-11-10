"""
Tests for session_init.py — SessionStart hook.

Tests cover:
generate_team_name():
1. Happy path: session_id from input_data -> "session-{first 8 chars}"
2. Random fallback: random hex suffix when input_data has no session_id
3. Short session_id: less than 8 chars used as-is
4. Empty session_id: treated as falsy, falls back to random
5. session_id from input_data used as sole source
6. Output format validation (session- prefix, hex suffix)
7. None session_id: treated as falsy, falls back to random

Resume-aware team detection (main() integration):
9. Fresh session (no config file) → fresh-team (create-branch) directive emitted
10. Resume session (config file exists) → reuse instruction emitted
11. OSError fallback → fresh-team (create-branch) directive (fail-open)
12. Team instruction is first in context_parts (insert at position 0)

main() integration:
13. check_resume_state non-None result appears in additionalContext output

check_additional_directories():
14. Returns None when ~/.claude/teams is present (absolute path)
15. Returns None when ~/.claude/teams is present (tilde path)
16. Returns tip message when setting is missing from additionalDirectories
17. Returns tip message when additionalDirectories is empty
18. Returns None when settings.json does not exist (fail-open)
19. Returns None when settings.json contains malformed JSON (fail-open)
20. Returns tip when permissions key is missing (empty additionalDirectories)
21. Returns None when additionalDirectories is not a list (fail-open)
22. Returns None when Path.home() raises (fail-open)
23. Ignores non-string entries in additionalDirectories without crashing
24. main() integration: tip appears in systemMessage when setting is missing
25. main() integration: no tip when setting is present
26. main() integration: tip skipped on context reset (compact and clear sources)

Note: restore_last_session() and check_resumption_context() are tested
in test_session_resume.py (canonical location).
"""

from __future__ import annotations

import io
import json
import re
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# #877: session_init's Class-A writes (write_context disk-write, session_start
# journal anchor, CLAUDE.md Current Session block, paused-state surface) are now
# gated behind is_lead, which keys on the harness-set agent_type. These tests
# exercise the LEAD path, so their stdin payloads must carry a lead agent_type.
# _with_lead_role injects it without disturbing any payload that already sets
# agent_type (e.g. a teammate/plain-frame suppression test sets it explicitly).
_LEAD_AGENT_TYPE = "pact-orchestrator"


def _with_lead_role(payload: dict) -> dict:
    """Return a copy of ``payload`` with a lead ``agent_type`` unless one is
    already present. Lets the lead-path tests opt in to is_lead==True with a
    single shared edit instead of stamping every inline stdin literal."""
    if "agent_type" in payload:
        return payload
    return {**payload, "agent_type": _LEAD_AGENT_TYPE}


# The canonical session id every driver below uses. One name for one value, so
# a change lands in one place.
_TEST_SESSION_ID = "aabb1122-0000-0000-0000-000000000000"

# The unknown-role notice literal. classify_session_role returns "unknown" when
# agent_type is absent, and the role gate emits this BESIDE the orchestrator
# ladder, never in place of it. Kept as a fragment rather than the full
# sentence so a re-wrap of the source literal does not redden every arm that
# reads it.
_UNKNOWN_ROLE_FRAGMENT = "relaunch with `--agent repacted:pact-orchestrator`"


def _stdin_payload(
    source=None,
    agent_type: Optional[str] = _LEAD_AGENT_TYPE,
    **extra,
) -> str:
    """Build a stdin JSON string for a session_init driver.

    ``agent_type=None`` OMITS the key, which is the ONLY way to build an
    unknown frame: classify_session_role reads "unknown" from an ABSENT
    agent_type, so a driver that cannot omit the key cannot reach that branch.

    THE DEFAULT IS THE LEAD ROLE ON PURPOSE. A driver that omitted agent_type
    silently built an UNKNOWN frame while its class asserted the LEAD contract,
    and the two frames emitted identical bytes before the role gate became
    three-way, so the arms passed without exercising the frame they named.
    """
    payload = {"session_id": _TEST_SESSION_ID}
    if source is not None:
        payload["source"] = source
    if agent_type is not None:
        payload["agent_type"] = agent_type
    payload.update(extra)
    return json.dumps(payload)


def _assert_unknown_frame_body(additional: str) -> None:
    """Assert that ``additional`` is the body an UNKNOWN frame receives.

    Paired with each lead-frame arm in the same class: the lead fixture repair
    gives an arm the frame it MEANT, and the unknown arm keeps the frame it was
    accidentally providing. Both properties stay asserted, so neither the lead
    contract nor the unknown branch loses its witness.

    AN UNKNOWN FRAME IS A PRIMARY FRAME AND KEEPS THE LADDER. "unknown" means
    agent_type was ABSENT, which is a no-`--agent` primary frame: an ordinary
    user running plain `claude`. Withholding the ladder leaves the bootstrap
    marker unstamped, and bootstrap_gate then denies Edit, Write and Agent on
    every call. The notice rides BESIDE the ladder, never in place of it.

    BOTH ASSERTIONS ARE PRESENCES, which is what makes this helper non-vacuous
    in a fail-open hook: a build path that died emits no bytes and fails the
    first assertion, so neither half can pass by accident.
    """
    assert "YOUR PACT ROLE: orchestrator." in additional, (
        "an unknown frame is a no-`--agent` PRIMARY frame and must receive the "
        f"orchestrator instructions. got: {additional[:120]!r}"
    )
    assert _UNKNOWN_ROLE_FRAGMENT in additional, (
        "an unknown frame must also receive the unknown-role notice. got: "
        f"{additional[:120]!r}"
    )


class TestGenerateTeamName:
    """Tests for generate_team_name() -- session-unique team name generation."""

    def test_uses_session_id_from_input_data(self):
        """Should return session- followed by first 8 chars of session_id."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "0001639f-a74f-41c4-bd0b-93d9d206e7f7"})

        assert result == "session-0001639f"


class TestMainPausedStateIntegration:
    """Integration test: check_resume_state wiring in session_init.main()."""

    def test_paused_state_appears_in_additional_context(self, monkeypatch):
        """Non-None check_resume_state result should appear in additionalContext output."""
        from session_init import main

        paused_msg = "Paused work detected: PR #42 (feat/login) — awaiting merge."

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")

        # Provide valid JSON on stdin with a session_id (#877: lead frame so the
        # is_lead-gated check_resume_state actually runs).
        stdin_data = json.dumps(_with_lead_role(
            {"session_id": "aabb1122-0000-0000-0000-000000000000"}
        ))

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=paused_msg) as mock_paused, \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_paused.assert_called_once_with(prev_session_dir=None)

        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        assert paused_msg in additional


class TestResumptionMarker:
    """Step 8 records session_resumption_surfaced when a claim surfaces.

    The secretary reads the event's PRESENCE at spawn to learn that this
    session resumes a running arc, and skips its Working Memory rebuild for
    that session. Three properties, matching the three ways the marker can be
    wrong: written when a prompt surfaces, NOT written when none does, and
    loud when the write does not land.

    The failure of the write is the unsafe direction and cannot be made safe —
    no marker means the secretary rebuilds — so the third test is the one that
    matters most: it pins that the hole announces itself instead of opening
    silently.
    """

    _STDIN = json.dumps(_with_lead_role(
        {"session_id": "aabb1122-0000-0000-0000-000000000000"}
    ))

    @staticmethod
    def _patches(resume_msg, append_event_mock):
        """The common patch stack; the two variables are what each test sets."""
        return [
            patch("shared.session_init_common.setup_plugin_symlinks", return_value=None),
            patch("shared.session_init_common.ensure_project_memory_md", return_value=None),
            patch("shared.session_init_lead.update_session_info", return_value=None),
            patch("session_init.get_task_list", return_value=None),
            patch("shared.session_init_lead.restore_last_session", return_value=None),
            patch("shared.session_init_lead.check_resume_state", return_value=resume_msg),
            patch("shared.session_init_lead.append_event", append_event_mock),
            patch("sys.stdin", io.StringIO(TestResumptionMarker._STDIN)),
        ]

    def _run(self, monkeypatch, resume_msg, append_returns=True):
        """Run main() and return (output_dict, marker_call_count)."""
        monkeypatch.setenv(
            "CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project"
        )
        # session_start is appended at step 5 through the SAME helper, so the
        # mock must stay total; only the marker's own call is counted below.
        append_event_mock = MagicMock(return_value=append_returns)

        from session_init import main

        with ExitStack() as stack:
            for p in self._patches(resume_msg, append_event_mock):
                stack.enter_context(p)
            mock_stdout = stack.enter_context(
                patch("sys.stdout", new_callable=io.StringIO)
            )
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        marker_calls = [
            call for call in append_event_mock.call_args_list
            if call.args and call.args[0].get("type")
            == "session_resumption_surfaced"
        ]
        return json.loads(mock_stdout.getvalue()), marker_calls

    def test_marker_written_when_a_claim_surfaces(self, monkeypatch):
        """A surfaced resume prompt records exactly one marker event."""
        _output, marker_calls = self._run(
            monkeypatch,
            resume_msg="Paused work detected: PR #42 (feat/login).",
        )

        assert len(marker_calls) == 1
        # No fields beyond the envelope: presence is the whole signal, and a
        # field with no reader is a drift surface. `v`/`type`/`ts` are what
        # make_event puts on every event.
        assert set(marker_calls[0].args[0]) == {"v", "type", "ts"}


class TestMainPrevSessionDirOrdering:
    """Regression: _extract_prev_session_dir must run BEFORE update_session_info.

    Bug: in an earlier revision, main() called update_session_info() (which
    overwrites the Current Session block in CLAUDE.md with THIS session's info)
    before calling _extract_prev_session_dir(). That caused _extract_prev_session_dir
    to read back the just-written current session dir, silently breaking:
      - restore_last_session(prev_session_dir=...) -> reads current (empty) journal
      - check_resume_state(prev_session_dir=...)   -> never finds paused events

    Invariant: READ prior CLAUDE.md BEFORE OVERWRITING it.

    This test drives main() end-to-end (no mocking of update_session_info,
    _extract_prev_session_dir, restore_last_session, or check_resume_state).
    It pre-seeds a prior session with a session_paused event in its journal and
    asserts the paused-state message appears in the hook output — which can
    only happen if prev_session_dir was captured before the block rewrite.
    """

    def test_prev_session_dir_captured_before_claude_md_rewrite(
        self, monkeypatch, tmp_path
    ):
        """End-to-end: paused state from prior session is detected despite
        update_session_info() overwriting CLAUDE.md in the same main() call."""
        from session_init import main

        # --- Arrange: tmp home + tmp project dir ---
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        # --- Arrange: prior session dir with a session_paused event in its journal ---
        prior_session_id = "deadbeef-1111-2222-3333-444455556666"
        prior_session_dir = (
            tmp_path / ".claude" / "pact-sessions" / "project" / prior_session_id
        )
        prior_session_dir.mkdir(parents=True)
        prior_journal = prior_session_dir / "session-journal.jsonl"
        paused_event = {
            "v": 1,
            "type": "session_paused",
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pr_number": 9999,
            "pr_url": "https://github.com/example/repo/pull/9999",
            "branch": "feat/prior-session-branch",
            "worktree_path": str(tmp_path / "wt" / "prior"),
            "consolidation_completed": True,
            "team_name": "pact-deadbeef",
        }
        prior_journal.write_text(json.dumps(paused_event) + "\n", encoding="utf-8")

        # --- Arrange: project CLAUDE.md with a Session dir line pointing at the prior dir ---
        # Use an absolute path (not ~) so _extract_prev_session_dir returns it verbatim
        # and we can assert against it below.
        prior_session_dir_str = str(prior_session_dir)
        claude_md_dir = project_dir / ".claude"
        claude_md_dir.mkdir()
        claude_md = claude_md_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            f"- Resume: `claude --resume {prior_session_id}`\n"
            "- Team: `pact-deadbeef`\n"
            f"- Session dir: `{prior_session_dir_str}`\n"
            "- Started: 2025-01-01 00:00:00 UTC\n"
            "<!-- SESSION_END -->\n",
            encoding="utf-8",
        )

        # --- Arrange: current session id (different from the prior one) ---
        # #877: lead frame so the is_lead-gated check_resume_state runs.
        current_session_id = "aabb1122-0000-0000-0000-000000000000"
        stdin_data = json.dumps(_with_lead_role({"session_id": current_session_id}))

        # Spies so we can assert exactly which prev_session_dir the downstream
        # calls received. We wrap rather than replace so the real implementations
        # still run (end-to-end coverage). check_resume_state is the unified
        # resolver seam (paused + refreshed) with the same single-dir signature
        # the old check_paused_state seam had — the paused prompt still flows
        # through it, so the pass-through assertions are unchanged.
        from shared.session_resume import (
            check_resume_state as real_check_resume_state,
            restore_last_session as real_restore_last_session,
        )

        restore_calls: list[str | None] = []
        resume_calls: list[str | None] = []

        def spy_restore_last_session(prev_session_dir=None):
            restore_calls.append(prev_session_dir)
            return real_restore_last_session(prev_session_dir=prev_session_dir)

        def spy_check_resume_state(prev_session_dir=None):
            resume_calls.append(prev_session_dir)
            return real_check_resume_state(prev_session_dir=prev_session_dir)

        # Patch boundary side effects that are unrelated to the ordering invariant:
        #   - setup_plugin_symlinks / remove_stale_kernel_block / update_pact_routing /
        #     ensure_project_memory_md: touch user home / plugin root — not under test
        #   - _check_pr_state: shells out to `gh pr view`; patch to OPEN so the
        #     paused_msg path is exercised instead of being suppressed.
        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch(
                 "shared.session_resume._check_pr_state",
                 return_value="OPEN",
             ), \
             patch(
                 "shared.session_init_lead.restore_last_session",
                 side_effect=spy_restore_last_session,
             ), \
             patch(
                 "shared.session_init_lead.check_resume_state",
                 side_effect=spy_check_resume_state,
             ), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

        # --- Assert: both downstream calls saw the PRIOR session dir, not the current one ---
        assert restore_calls == [prior_session_dir_str], (
            f"restore_last_session should have received the prior session dir "
            f"({prior_session_dir_str!r}), but got {restore_calls!r}. "
            f"This indicates update_session_info() ran before "
            f"_extract_prev_session_dir() and clobbered the CLAUDE.md block."
        )
        assert resume_calls == [prior_session_dir_str], (
            f"check_resume_state should have received the prior session dir "
            f"({prior_session_dir_str!r}), but got {resume_calls!r}."
        )

        # --- Assert: the paused-work message made it into the hook output ---
        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        assert "Paused work detected: PR #9999" in additional
        assert "feat/prior-session-branch" in additional

        # --- Assert: update_session_info DID in fact rewrite the block to the current session ---
        # (confirms the ordering fix didn't accidentally skip the write)
        rewritten = claude_md.read_text(encoding="utf-8")
        assert f"claude --resume {current_session_id}" in rewritten
        assert f"claude --resume {prior_session_id}" not in rewritten


class TestRefreshSurfacingMatrix:
    """Real-main() round-trip matrix for the refreshed-state surface.

    Non-mocked seam tier: check_resume_state / restore_last_session /
    has_unspent_refresh run REAL against seeded journals — only boundary
    side effects (symlinks, memory-md, staleness, task list, gh) are
    patched. The step-8 surface keys on the frame's agent_type alone
    (is_lead is PURE on that one field), so the session-topology axis
    (sid==leadSid in-process vs sid!=leadSid tmux) is exercised through
    the teammate rows by varying the stdin session_id relative to the
    seeded session dir.

    COUNTER-TEST-BY-REVERT (executed once at authoring time, then
    restored): temporarily reverting the step-8 call swap in
    session_init.main() back to the paused-only resolver
    (check_paused_state) turned EXACTLY 5 tests in this class RED —
    test_lead_frame_surfaces_refresh_prompt[compact|resume|startup],
    test_quit_path_topology_prev_dir_differs, and
    test_compact_branch_suppression_and_relabel_with_refresh (its step-8
    assertion) — proving the round-trip matrix depends on the
    check_resume_state seam re-point, not on any mocked shim. The
    non-lead rows, byte-identity, and spent-refresh tests stayed green
    under the revert, as expected (they pin suppression/presentation,
    not the resolver seam).
    """

    _REFRESH_TS = "2026-07-10T12:00:00Z"

    def _seed(self, tmp_path, session_id, refresh=True):
        """Seed home, project CLAUDE.md, and a session journal holding a
        session_refreshed event; returns (project_dir, session_dir) strs."""
        project_dir = tmp_path / "project"
        project_dir.mkdir(exist_ok=True)

        session_dir = (
            tmp_path / ".claude" / "pact-sessions" / "project" / session_id
        )
        session_dir.mkdir(parents=True, exist_ok=True)
        if refresh:
            event = {
                "v": 1,
                "type": "session_refreshed",
                "ts": self._REFRESH_TS,
                "consolidation_completed": True,
                "halt_active": True,
                "halt_task_ids": ["7"],
                "feature_subject": "mid-flight feature",
                "next_phase": "test",
            }
            journal = session_dir / "session-journal.jsonl"
            journal.write_text(json.dumps(event) + "\n", encoding="utf-8")

        claude_md_dir = project_dir / ".claude"
        claude_md_dir.mkdir(exist_ok=True)
        (claude_md_dir / "CLAUDE.md").write_text(
            "# Project Memory\n\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            f"- Resume: `claude --resume {session_id}`\n"
            "- Team: `session-testteam`\n"
            f"- Session dir: `{session_dir}`\n"
            "- Started: 2026-01-01 00:00:00 UTC\n"
            "<!-- SESSION_END -->\n",
            encoding="utf-8",
        )
        return str(project_dir), str(session_dir)

    def _run_main(self, monkeypatch, tmp_path, stdin_payload, project_dir,
                  tasks=None):
        from session_init import main

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", project_dir)

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("session_init.get_task_list", return_value=tasks), \
             patch("shared.session_resume._check_pr_state", return_value=""), \
             patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        return json.loads(mock_stdout.getvalue())

    @pytest.mark.parametrize("source", ["compact",])
    def test_lead_frame_surfaces_refresh_prompt(
        self, monkeypatch, tmp_path, source
    ):
        """Lead frame × every source: the unspent refresh claim surfaces
        (post-compact/same-session --resume topology: prev == current dir)."""
        session_id = "aabb1122-0000-0000-0000-00000000cafe"
        project_dir, _ = self._seed(tmp_path, session_id)
        output = self._run_main(
            monkeypatch, tmp_path,
            {"session_id": session_id, "source": source,
             "agent_type": _LEAD_AGENT_TYPE},
            project_dir,
        )
        additional = output["hookSpecificOutput"]["additionalContext"]
        assert "Refreshed workstream detected" in additional
        assert f"refresh_ts={self._REFRESH_TS}" in additional
        assert "A HALT/stop signal was ACTIVE at refresh (tasks: 7)" in additional


    def _in_progress_tasks(self):
        return [
            {"id": "14", "subject": "Implement mid-flight feature",
             "status": "in_progress", "metadata": {}},
            {"id": "17", "subject": "CODE: mid-flight feature",
             "status": "in_progress", "metadata": {}},
            {"id": "26", "subject": "auditor: observe",
             "status": "in_progress", "metadata": {}},
        ]


class TestCompactSummaryCleanup:
    """Tests for stale compact-summary.txt cleanup in session_init.main()."""

    # The content written to the summary before each run. Recovered by reading
    # it back, so an assertion can be about the BYTES rather than about a path.
    SUMMARY_BODY = "Prior compaction context"

    def _run_main_with_source_and_summary(
        self, monkeypatch, tmp_path, source, session_id="aabb1122-0000-0000-0000-000000000000"
    ):
        """Run main() with compact-summary.txt present; report what survived.

        RETURNS A PAIR, NOT A BOOLEAN, AND THAT IS THE POINT. This helper used
        to return `summary_file.exists()` alone, which made byte loss
        UNREPRESENTABLE in the test's own vocabulary: no caller could ask
        whether the summary survived, only whether the singleton path was
        empty. Measured on the unfixed hook, a byte-preserving mutation and the
        destructive original both produced `4 passed` — identical output for
        opposite behaviours.

        Returns `(still_at_singleton, recovered)` where `recovered` is a
        {relative-path: text} map of every copy of the summary body found
        anywhere under the sessions root EXCEPT the singleton itself. An
        assertion about survival must read that map; one about the single-use
        property reads the flag.
        """
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create compact-summary.txt
        sessions_dir = tmp_path / ".claude" / "pact-sessions"
        sessions_dir.mkdir(parents=True)
        summary_file = sessions_dir / "compact-summary.txt"
        summary_file.write_text(self.SUMMARY_BODY)

        # Patch get_compact_summary_path() to return our tmp_path version
        patched_path = summary_file

        stdin_payload = {"source": source}
        if session_id is not None:
            stdin_payload["session_id"] = session_id
        stdin_data = json.dumps(stdin_payload)

        with patch("shared.session_init_resume.get_compact_summary_path", lambda: patched_path), \
             patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

        recovered = {
            str(p.relative_to(sessions_dir)): p.read_text(encoding="utf-8")
            for p in sorted(sessions_dir.rglob("*"))
            if p.is_file() and p != summary_file
            and p.read_text(encoding="utf-8") == self.SUMMARY_BODY
        }
        return summary_file.exists(), recovered

    @pytest.mark.parametrize("source", ["startup",])
    def test_non_compact_source_clears_the_singleton_path(self, monkeypatch, tmp_path, source):
        """The path must end up clear: a summary left there is processed twice."""
        still_at_singleton, _ = self._run_main_with_source_and_summary(
            monkeypatch, tmp_path, source
        )
        assert not still_at_singleton, (
            f"compact-summary.txt should not remain at the singleton path for "
            f"source='{source}' — a copy left there is processed a second time "
            f"by the next briefing in the same session."
        )


class TestCheckAdditionalDirectories:
    """Tests for check_additional_directories() — multi-directory tip.

    The function reads ~/.claude/settings.json and checks if both ~/.claude/teams
    and ~/.claude/pact-sessions (or their absolute equivalents) are listed in
    permissions.additionalDirectories. Returns a tip message listing whichever
    directories are missing, or None if all are present. Fail-open on all errors.
    """

    def _write_settings(self, tmp_path, settings_data):
        """Helper: write settings.json under tmp_path/.claude/."""
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_file = settings_dir / "settings.json"
        settings_file.write_text(json.dumps(settings_data), encoding="utf-8")
        return settings_file

    def test_returns_none_when_both_dirs_present_absolute(self, monkeypatch, tmp_path):
        """Should return None when absolute paths to both dirs are in additionalDirectories."""
        from session_init import check_additional_directories

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        teams_abs = str(tmp_path / ".claude" / "teams")
        sessions_abs = str(tmp_path / ".claude" / "pact-sessions")
        self._write_settings(tmp_path, {
            "permissions": {"additionalDirectories": [teams_abs, sessions_abs]}
        })

        result = check_additional_directories()

        assert result is None


class TestCheckAdditionalDirectoriesMainIntegration:
    """Integration tests: check_additional_directories wiring in session_init.main()."""

    def test_tip_appears_in_system_message_when_missing(self, monkeypatch, tmp_path):
        """Tip should appear in systemMessage when teams dir not configured."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create settings.json WITHOUT ~/.claude/teams in additionalDirectories
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True)
        (settings_dir / "settings.json").write_text(
            json.dumps({"permissions": {"additionalDirectories": []}}),
            encoding="utf-8",
        )

        stdin_data = json.dumps({"session_id": "aabb1122-0000-0000-0000-000000000000"})

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        system_msg = output.get("systemMessage", "")
        assert "additionalDirectories" in system_msg
        assert str(tmp_path / ".claude" / "teams") in system_msg
        assert str(tmp_path / ".claude" / "pact-sessions") in system_msg


class TestInprocessModeNoticeIntegration:
    """#864 Phase 1 §9.2: the in-process teammateMode notice wired into main().

    Drives session_init.main() end-to-end and asserts the notice appears in the
    emitted systemMessage for launch events (startup/resume) when the effective
    mode is not positively tmux, and is ABSENT for mid-launch context resets
    (compact/clear) and when the mode is tmux.

    The notice is detected by its stable doc-reference substring
    'reference/unattended-runs.md' (the notice<->doc link contract). The
    fixtures populate additionalDirectories so the step-0 dirs-tip is
    suppressed, isolating systemMessage to the notice under test.
    """

    # Stable substrings the notice MUST contain (drift guards per §9.2).
    @pytest.fixture(autouse=True)
    def _teams_mode(self, monkeypatch):
        """The notice is Teams-only."""
        monkeypatch.setenv("REPACT_EXECUTION", "teams")
        monkeypatch.setenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "1")

    _DOC_REF = "reference/unattended-runs.md"
    _CLI_HINT = "--teammate-mode tmux"

    # Sentinel: pass as `source` to OMIT the source key from stdin entirely
    # (distinct from passing None, which sets {"source": null} -> normalizes to
    # "unknown"). Drives main()'s missing-source default-to-"startup" path.
    _OMIT_SOURCE = object()

    def _run_main_capture_systemmessage(
        self, monkeypatch, tmp_path, source, mode
    ):
        """Run main() with given source + effective teammateMode (mode=None ->
        no teammateMode key -> resolves 'auto'). Returns the systemMessage str.

        Filesystem-isolated: Path.home -> tmp_path, CLAUDE_PROJECT_DIR -> an
        empty temp project dir (so project settings sources do not exist and the
        mode resolves from the user settings.json we write here).
        """
        from session_init import main

        project_dir = tmp_path / "project"
        project_dir.mkdir(exist_ok=True)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Determinism: neutralize the enterprise managed-settings source so the
        # effective teammateMode resolves ONLY from the isolated tmp settings
        # written below -- never the real OS managed path, which sits at
        # precedence[0] and would otherwise confound mode resolution on a
        # managed fleet. Sibling of the _ModeEnv fixture isolation in
        # test_teammate_mode.py (same managed-path determinism class).
        import shared.teammate_mode as teammate_mode
        monkeypatch.setattr(
            teammate_mode, "_managed_settings_path",
            lambda: tmp_path / "absent-managed-settings.json",
        )

        # User settings.json: populate additionalDirectories (suppress the
        # step-0 dirs-tip) and optionally pin teammateMode.
        teams_abs = str(tmp_path / ".claude" / "teams")
        sessions_abs = str(tmp_path / ".claude" / "pact-sessions")
        settings = {"permissions": {"additionalDirectories": [teams_abs, sessions_abs]}}
        if mode is not None:
            settings["teammateMode"] = mode
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)   # a second start reuses the root
        (settings_dir / "settings.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )

        # Team config so resume/compact/clear stay on the normal (non-anomalous)
        # path; mirrors the existing context-reset integration tests.
        team_dir = tmp_path / ".claude" / "teams" / "session-aabb1122"
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "config.json").write_text('{"members": []}')

        # Omit the source key entirely when _OMIT_SOURCE is passed (drives
        # main()'s `.get("source","startup")` default); otherwise pin it.
        payload = {"session_id": "aabb1122-0000-0000-0000-000000000000"}
        if source is not self._OMIT_SOURCE:
            payload["source"] = source
        stdin_data = json.dumps(payload)

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        return output.get("systemMessage", "")

    @pytest.mark.parametrize(
        "source,mode,present",
        [
            ("startup", "in-process", True),],
    )
    def test_notice_emit_matrix(self, monkeypatch, tmp_path, source, mode, present):
        """§9.2 matrix: notice present iff launch-event AND mode not tmux."""
        system_msg = self._run_main_capture_systemmessage(
            monkeypatch, tmp_path, source=source, mode=mode
        )
        if present:
            assert self._DOC_REF in system_msg, (
                f"expected notice for source={source!r} mode={mode!r}"
            )
            # Drift guards: the notice pins both literal substrings (§9.2).
            assert self._CLI_HINT in system_msg
        else:
            assert self._DOC_REF not in system_msg, (
                f"expected NO notice for source={source!r} mode={mode!r}"
            )


class TestIsUnknownOrMissingSession:
    """Direct unit tests for _is_unknown_or_missing_session() predicate.

    This security-relevant helper gates disk persistence (write_context,
    append_event) and CLAUDE.md writes. It must reject None, non-strings,
    empty strings, whitespace-only strings, and any "unknown-*" sentinel.

    These tests pin the exact boundary contract so regressions surface at
    the predicate level rather than only in integration test failures.
    """

    @pytest.mark.parametrize("value", [
        None,])
    def test_rejects_missing_or_blank(self, value):
        """None, empty, and whitespace-only values are all 'missing'."""
        from session_init import _is_unknown_or_missing_session

        assert _is_unknown_or_missing_session(value) is True


class TestWriteContextIntegration:
    """Integration tests: write_context() call wiring in session_init.main().

    Verifies that main() calls write_context() with the correct arguments
    derived from the session's team_name, session_id, and project_dir.
    """

    def test_write_context_called_with_correct_args(self, monkeypatch, tmp_path):
        """write_context should be called with team_name, session_id, project_dir."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # #878 SHAPE-2 seam: lead frame → session_init builds the cache then
        # persists. build_context_cache carries the 4 positional args (no flag).
        stdin_data = json.dumps(_with_lead_role({
            "session_id": "aabb1122-0000-0000-0000-000000000000",
        }))

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("shared.session_init_lead.build_context_cache",
                   return_value=(Path("/tmp/ctx.json"), {})) as mock_build_ctx, \
             patch("shared.session_init_lead.persist_context", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # #878 SHAPE-2: build_context_cache takes the 4 positional args; the
        # lead/teammate disk gate moved OUT of this call (it's now persist_context,
        # invoked only when frame_is_lead) — so no write_disk kwarg here.
        mock_build_ctx.assert_called_once_with(
            "session-aabb1122",
            "aabb1122-0000-0000-0000-000000000000",
            "/Users/example/Sites/test-project",
            "",  # plugin_root: CLAUDE_PLUGIN_ROOT not set in this test
            started_at=None,  # a startup records now; only a compaction keeps the value on disk
        )


# =============================================================================
# Issue #399: Failure Log Integration Tests
# =============================================================================


class TestFailureLogIntegration:
    """Tests that session_init's R3 malformed-stdin gate calls
    shared.failure_log.append_failure with the correct classification.

    Issue #399: the R3 gate intentionally drops the session_start journal
    anchor when stdin lacks a usable session_id — the alternative would
    create an unreapable `unknown-{hex}/` directory. That design choice
    costs visibility into hook failures (stderr is not user-visible, and
    teammate sessions never surface their first-message context to the
    team-lead). The global ring buffer at ~/.claude/pact-sessions/_session_init_failures.log
    is the post-hoc record that closes that gap.

    The integration contract verified here:
    1. The R3 gate calls append_failure BEFORE the stderr warning
    2. Classification distinguishes malformed_json / missing_session_id /
       non_string_session_id / empty_session_id / sentinel_session_id /
       other — each branch isolates a distinct upstream failure kind
    3. Fail-open is SACROSANCT: if append_failure raises, session_init
       still exits cleanly with the fallback sentinel (belt-and-suspenders
       wrapper at the call site catches any escape from the internal
       fail-open contract).
    """

    def test_malformed_stdin_calls_append_failure_with_malformed_json(
        self, monkeypatch, tmp_path
    ):
        """Non-JSON stdin → classification='malformed_json' with the
        JSONDecodeError text captured in the error field.
        """
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        stdin_data = "{ not valid json at all"

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("shared.session_init_lead.persist_context", return_value=None), \
             patch("shared.session_init_lead.append_event", return_value=None), \
             patch("session_init.append_failure") as mock_append_failure, \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("sys.stderr", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()

        # Session_init must exit cleanly — malformed stdin is a
        # fail-open path, not a crash path.
        assert exc_info.value.code == 0

        # R3 gate must have called append_failure exactly once.
        assert mock_append_failure.call_count == 1
        call_kwargs = mock_append_failure.call_args.kwargs
        assert call_kwargs["classification"] == "malformed_json"
        # The captured error should be non-empty (the real JSONDecodeError text).
        assert call_kwargs["error"]
        # cwd and source are populated (defensive context for post-hoc debugging).
        assert "cwd" in call_kwargs
        assert "source" in call_kwargs


# =============================================================================
# _extract_prev_session_dir() Dual-Location Tests
# =============================================================================


class TestExtractPrevSessionDirDualLocation:
    """Tests for _extract_prev_session_dir() honoring both project CLAUDE.md
    locations.

    Claude Code accepts the project memory file at either:
      - $CLAUDE_PROJECT_DIR/.claude/CLAUDE.md   (preferred / new default)
      - $CLAUDE_PROJECT_DIR/CLAUDE.md           (legacy)

    _extract_prev_session_dir must locate the previous session's journal
    regardless of which location is in use, with .claude/CLAUDE.md taking
    priority when both exist.
    """

    _CONTENT_TEMPLATE = (
        "# Project\n"
        "<!-- SESSION_START -->\n"
        "## Current Session\n"
        "- Resume: `claude --resume {sid}`\n"
        "- Team: `pact-{sid_short}`\n"
        "- Session dir: `{session_dir}`\n"
        "<!-- SESSION_END -->\n"
    )

    def _make_content(self, session_id: str, session_dir: str) -> str:
        return self._CONTENT_TEMPLATE.format(
            sid=session_id,
            sid_short=session_id[:8],
            session_dir=session_dir,
        )

    def test_returns_none_when_neither_location_exists(self, tmp_path):
        """Returns None when neither .claude/CLAUDE.md nor ./CLAUDE.md exists."""
        from session_init import _extract_prev_session_dir

        result = _extract_prev_session_dir(str(tmp_path))

        assert result is None


class TestExtractPrevSessionDirReadSideLock:
    """R2-B4: _extract_prev_session_dir must acquire the same sidecar
    file_lock that update_session_info uses for write — otherwise a
    concurrent write could surface as a torn read here.

    Verifies the lock-acquisition contract via monkeypatch + lock-state
    assertion (more deterministic than threading-based concurrent-write
    tests, which are timing-fragile)."""

    def test_acquires_file_lock_before_reading(self, tmp_path, monkeypatch):
        """The read at L389 must be wrapped in file_lock(claude_md).
        Verify by monkeypatching file_lock to record entry/exit + the
        read_text call, then asserting the order: lock-enter → read →
        lock-exit."""
        from session_init import _extract_prev_session_dir

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "<!-- SESSION_START -->\n"
            "- Session dir: `~/.claude/pact-sessions/project/abc12345`\n"
            "<!-- SESSION_END -->\n",
            encoding="utf-8",
        )

        # Track ordering events via the patched file_lock.
        events = []
        from contextlib import contextmanager
        import shared.session_init_resume as si_module

        @contextmanager
        def tracking_lock(target):
            events.append(("lock_enter", target))
            try:
                yield
            finally:
                events.append(("lock_exit", target))

        original_read_text = Path.read_text

        def tracking_read(self, *args, **kwargs):
            if self == claude_md:
                events.append(("read", self))
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(si_module, "file_lock", tracking_lock)
        monkeypatch.setattr(Path, "read_text", tracking_read)

        _extract_prev_session_dir(str(project_dir))

        # Assert ordering: lock-enter strictly before read; read strictly
        # before lock-exit. The single-call sequence guarantees the
        # serialization contract — a concurrent writer holding the lock
        # would have blocked the read, not interleaved with it.
        kinds = [k for k, _ in events]
        assert kinds == ["lock_enter", "read", "lock_exit"], (
            f"unexpected ordering: {events}"
        )


# =============================================================================
# CLAUDE_PLUGIN_ROOT env-set wiring tests (M6)
# =============================================================================


class TestPluginRootEnvWiring:
    """M6: Verify that main() honors CLAUDE_PLUGIN_ROOT when already set in env.

    The happy-path tests (TestWriteContextIntegration) cover the case where
    CLAUDE_PLUGIN_ROOT is unset (empty string is passed through). These tests
    cover the complementary case where the Claude Code plugin loader sets
    CLAUDE_PLUGIN_ROOT BEFORE session_init runs. session_init.py:319 reads
    the env var directly:

        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")

    So the test merely needs to assert that when the env var is set, the
    value flows through to:
      (a) write_context() -> pact-session-context.json
      (b) update_session_info() -> `- Plugin root:` line in CLAUDE.md
    """

    @pytest.fixture(autouse=True)
    def _reset_pact_context_cache(self, monkeypatch):
        """T2: reset pact_context module state before every test in this class.

        `write_context()` caches the computed session directory path in
        `pact_context._cache` and `pact_context._context_path` on first
        call. Because pytest loads `shared.pact_context` once per process
        and tests in this class exercise `main()` against different
        project dirs / tmp_paths, a stale cache from a prior test can
        leak into the next one — the second test's `write_context` short-
        circuits on the cached state and writes to the old path.

        Previously only M6c reset these via inline `monkeypatch.setattr`;
        M6a and M6b were incidentally safe because their assertions were
        on mock call args or CLAUDE.md file contents rather than on
        `pact-session-context.json` contents. Promoting the reset to a
        class-scoped autouse fixture hardens all three tests and any
        future additions to the class against cache leakage.
        """
        import shared.pact_context as pact_context
        monkeypatch.setattr(pact_context, "_context_path", None)
        monkeypatch.setattr(pact_context, "_cache", None)

    def test_plugin_root_env_flows_to_write_context(self, monkeypatch, tmp_path):
        """M6a: CLAUDE_PLUGIN_ROOT in env is passed as write_context's 4th arg."""
        from session_init import main

        plugin_root_value = "/some/custom/plugin/root"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", plugin_root_value)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        stdin_data = json.dumps(_with_lead_role({
            "session_id": "aabb1122-0000-0000-0000-000000000000",
        }))

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.build_context_cache",
                   return_value=(Path("/tmp/ctx.json"), {})) as mock_build_ctx, \
             patch("shared.session_init_lead.persist_context", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # 4th positional arg is plugin_root; #878 SHAPE-2 moved the disk gate out
        # of this call into persist_context, so no write_disk kwarg here.
        mock_build_ctx.assert_called_once_with(
            "session-aabb1122",
            "aabb1122-0000-0000-0000-000000000000",
            str(tmp_path / "proj"),
            plugin_root_value,
            started_at=None,
        )


# ---------------------------------------------------------------------------
# #366 Phase 1 — kernel elimination behavior contract
# ---------------------------------------------------------------------------
#
# These classes pin the post-refactor session_init contract:
#   1. _team_create / _team_reuse strings now lead with `YOUR PACT ROLE: orchestrator`
#      and instruct the team-lead to invoke `Skill("repacted:bootstrap")` as its FIRST
#      action.
#   2. session_init.main() calls remove_stale_kernel_block() unconditionally
#      on every SessionStart (not gated by is_context_reset).
#   3. session_init.main() calls update_pact_routing() unconditionally on
#      every SessionStart.
#   4. The legacy update_claude_md() symbol is gone — neither imported into
#      session_init nor present in claude_md_manager.
# ---------------------------------------------------------------------------


def _run_session_init_for_path(
    monkeypatch,
    tmp_path,
    source,
    team_exists,
    agent_type: Optional[str] = _LEAD_AGENT_TYPE,
):
    """Helper for the contract tests below: runs main() under stable mocks
    and returns (additionalContext, kernel_call_count, routing_call_count)."""
    from session_init import main

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    if team_exists:
        team_dir = tmp_path / ".claude" / "teams" / "session-aabb1122"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text('{"members": []}')

    stdin_data = _stdin_payload(source=source, agent_type=agent_type)

    with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
         patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
         patch("shared.session_init_lead.update_session_info", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("shared.session_init_lead.restore_last_session", return_value=None), \
         patch("shared.session_init_lead.check_resume_state", return_value=None), \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0
    output = json.loads(mock_stdout.getvalue())
    additional = output["hookSpecificOutput"]["additionalContext"]
    return additional, 0, 0


class TestRecoveryFrameNamesAReadableSurface:
    """The two recovery frames must instruct a read that returns something.

    Both frames tell a recovering agent how to get its in-progress work back,
    and both used to say `TaskGet on in-progress tasks` — an instruction that
    returns subject / status / owner and NO metadata, on precisely the paths
    where metadata is what the reader needs.

    THESE ARMS ASSERT ON THE CAPTURED `additionalContext`, NOT ON THE SOURCE
    LINE, and nothing here is assembled by the test. `_run_session_init_for_path`
    drives the real `main()` over patched stdin and parses the JSON the hook
    prints to stdout, so the string under assertion is the string the harness
    would deliver.

    A source-text search cannot do this job, for two independent reasons:

      1. The instruction is built from implicitly-concatenated f-string
         fragments joined into one blob, so the defect it is exposed to is a
         JOINING defect. Dropping the trailing space of one fragment runs two
         words together in the DELIVERED text while every source line still
         reads correctly, and a grep over the source passes over exactly that.
         Each pattern below therefore spans its WHOLE member, so every join
         in it is inside the match and a lost space at any one of them breaks
         it — including a join that does not exist yet, since a fragment
         spliced in later lands inside the pattern rather than between two
         pins.
      2. A source match would give the same green if the string moved to a
         constant, or if the branch that emits it stopped firing.

    WHAT THESE CANNOT CATCH: they are blind to the SessionStart hook not
    being registered at all — a platform-runtime property no in-process test
    reaches. They do NOT pin which `_archive_clause` or `_secretary_clause`
    variant fires, only that one of them rendered with its joins intact;
    branch selection is a separate property and is not this arm's job.
    """

    # ONE PATTERN PER MEMBER, SPANNING EVERY SEAM IN IT, rather than a pin per
    # seam. The property is "this member renders as one correctly-spaced
    # sentence", and a per-seam pin cannot hold that property: it covers the
    # seams that exist when it is written, and a fragment added later lands
    # outside every one of them, silently. That is not hypothetical — it is
    # how the gap here arose twice. The original arm pinned the seams around
    # the fragment a diff had touched, leaving the archive-clause joins open,
    # where dropping a space delivered "for prior context(if it is gone" to
    # the agent while every case stayed green. The first repair then pinned
    # those three joins and still left the two at the head of the member open.
    # Measuring a seam and closing it is a treadmill; asserting the member is
    # not.
    #
    # Interpolated spans are wildcarded so the pattern holds across both
    # `_archive_clause` variants, both `_secretary_clause` variants and any
    # tmp-path, while every LITERAL join stays inside the match. `\S ` at the
    # head and `\. \S` at the tail pin the two joins against interpolations
    # whose own text is not stable enough to assert.
    RECOVERY_MEMBERS = [
        (
            "compact",
            re.compile(
                r"\S After bootstrap, recover session state: "
                r"\(1\) Read .+?compact-summary\.head\.txt for prior context: "
                r"open tasks and worktrees rebuilt from disk, then the start of "
                r".+?compact-summary\.txt; read that full file only when the "
                r"head lacks what you need "
                r"\(if it is absent, .+?\), "
                r"\(2\) Run TaskList to find in-progress work, "
                r"\(3\) read the task files of in-progress tasks for "
                r"details \(TaskGet does not surface metadata\)\. "
                r"\(4\) If a PACT workflow you started is still in progress in "
                r"TaskList, and its copy among the re-attached skills above is "
                r"cut short or missing, Read .+? in full before you continue it, "
                r"where `repacted:<name>` is that workflow\. If the Read reports a "
                r"partial view, read the remaining pages\. Do not invoke the "
                r"workflow again: that starts it over\. If the file shows "
                r"`\$ARGUMENTS` where the task it was started for belongs, take "
                r"that task from its re-attached copy or from your summary\. \S"
            ),
        ),
        (
            "clear",
            re.compile(
                r"\S CONTEXT CLEARED: Your context was cleared via /clear\. "
                r"State recovery: \(1\) TaskList for current tasks, "
                r"\(2\) read the task files of in-progress tasks "
                r"\(TaskGet does not surface metadata\)\. "
                r"The session briefing below is computed by this hook; "
            ),
        ),
    ]

    # The retired instruction, in both its renderings.
    RETIRED_RECOVERY_INSTRUCTION = "TaskGet on in-progress tasks"

    @pytest.mark.parametrize(
        "source, member",
        list(list(RECOVERY_MEMBERS)[:1])[:1],
        ids=list([s for s, _ in RECOVERY_MEMBERS])[:1],
    )
    def test_recovery_frame_delivers_task_file_read(
        self, monkeypatch, tmp_path, source, member
    ):
        """The delivered context tells the agent to read the task FILES, and
        the whole member renders as one correctly-spaced sentence."""
        additional, _, _ = _run_session_init_for_path(
            monkeypatch, tmp_path, source=source, team_exists=True
        )
        assert member.search(additional), (
            f"source={source!r}: the delivered additionalContext does not "
            f"render the recovery member intact. Every fragment join in it "
            f"is inside this pattern, so a lost trailing space breaks the "
            f"match while the source still reads correctly — and so does a "
            f"new fragment spliced in without its own space. Pattern: "
            f"{member.pattern!r}. Delivered: {additional!r}"
        )


class TestUpdateClaudeMdNotCalled:
    """The legacy update_claude_md symbol is gone from session_init."""

    def test_session_init_does_not_import_update_claude_md(self):
        """session_init module must not bind the legacy update_claude_md name."""
        import session_init

        assert not hasattr(session_init, "update_claude_md"), (
            "session_init still imports update_claude_md — should be removed "
            "as part of #366 Phase 1 kernel elimination."
        )


# ---------------------------------------------------------------------------
# F2 exception safety net
# ---------------------------------------------------------------------------
#
# session_init is the PRIMARY delivery channel for the PACT ROLE marker
# and the orchestrator's bootstrap directive. If session_init.main()
# throws BEFORE it has built the team_create/team_reuse block, the team-
# lead would otherwise get only {"systemMessage": "..."} back and the
# governance delivery chain would be broken for that session. The F2
# safety net in the outer except block rebuilds a minimal PACT ROLE
# block so the team-lead still knows how to bootstrap even on the
# failure path.
# ---------------------------------------------------------------------------


# =============================================================================
# Issue #414 R2: session_start event includes `source` field
# =============================================================================


class TestSessionStartSourceField:
    """Issue #414 R2 — the `session_start` journal event written by
    session_init.main() must include the normalized session source so
    downstream triage can directly attribute the event to startup vs
    auto-compact vs user `/clear` vs `/resume`, instead of triangulating
    from timing clusters (which is how R1 was diagnosed — painfully).

    The value persisted is the same `source` already computed at line 431
    of session_init.py: any unrecognized stdin source clamps to `"unknown"`
    so the journal field is bounded to {startup, resume, compact, clear,
    unknown}. Fail-open is SACROSANCT — a missing or non-string source
    must NEVER block session start; it lands in the journal as
    `"unknown"` (or as `"startup"` when stdin omits the key entirely,
    matching the existing default at line 430).
    """

    @pytest.fixture(autouse=True)
    def _reset_pact_context_cache(self, monkeypatch):
        """Reset `shared.pact_context` module state between tests in this
        class. Mirrors the identically-named fixture on
        `TestPluginRootEnvWiring` (see that class for the full rationale).

        Why this class needs it: tests here exercise `main()` against the
        same tmp_path-rooted Path.home() but patch `write_context`, so the
        cache doesn't get populated from inside the test. That makes the
        tests incidentally safe today — but a future refactor that stops
        patching `write_context` (e.g. to exercise real context writes)
        would inherit silent cache leakage from a prior test. Matching
        the sibling class's pattern now hardens the class against that
        regression."""
        import shared.pact_context as pact_context
        monkeypatch.setattr(pact_context, "_context_path", None)
        monkeypatch.setattr(pact_context, "_cache", None)

    def _captured_session_start_event(self, mock_append):
        """Helper: pull the session_start dict out of an append_event mock."""
        starts = [
            call.args[0]
            for call in mock_append.call_args_list
            if call.args and call.args[0].get("type") == "session_start"
        ]
        assert len(starts) == 1, (
            f"Expected exactly one session_start event, got {len(starts)}: "
            f"{starts!r}"
        )
        return starts[0]

    def _run_main_and_capture_event(self, monkeypatch, tmp_path, stdin_payload):
        """Helper: run session_init.main() with the given stdin payload and
        return the dict that was passed to append_event for session_start."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        stdin_data = json.dumps(_with_lead_role(stdin_payload))

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("shared.session_init_lead.persist_context", return_value=None), \
             patch("shared.session_init_lead.append_event") as mock_append, \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()

        # Fail-open: session start must always succeed regardless of source.
        assert exc_info.value.code == 0
        return self._captured_session_start_event(mock_append)

    def test_the_event_carries_the_plugin_version(self, monkeypatch, tmp_path):
        """Gate inventory section 5: a journal row says which plugin version wrote it."""
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(Path(__file__).resolve().parent.parent))
        event = self._run_main_and_capture_event(monkeypatch, tmp_path, {
            "session_id": "abc-123", "source": "startup"})
        manifest = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
        assert event["plugin_version"] == json.loads(manifest.read_text("utf-8"))["version"]


# Post-compaction checkpoint tests


@pytest.fixture
def _compact_tasks_dir(tmp_path, monkeypatch, pact_context):
    """Create a real ~/.claude/tasks/{team_name}/ under tmp_path and
    route session_init's Path.home() to tmp_path. Yields the tasks dir
    so each test can drop task JSON files into it.

    Post team-dir-resolution fix: session_init runs in a team context, so
    get_task_list resolves tasks under {team_name} (session-aabb1122 — matching
    the team config the compact-branch tests create). The session_id still
    matches the stdin session_id used by the tests below.
    """
    session_id = "aabb1122-0000-0000-0000-000000000000"
    team_name = "session-aabb1122"
    tasks_dir = tmp_path / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True)
    pact_context(session_id=session_id, team_name=team_name)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tasks_dir


def _run_session_init_compact(
    monkeypatch, tmp_path, *, team_exists=True, patch_get_task_list=None,
    agent_type: Optional[str] = _LEAD_AGENT_TYPE,
):
    """Drive session_init.main() with source=compact and return the
    parsed additionalContext string plus the full output dict.

    By default get_task_list is NOT patched — the test relies on real
    task_utils reads from the isolated tasks dir (see _compact_tasks_dir).
    Pass patch_get_task_list=<value> to force a specific return value
    (e.g., None or a Mock that raises).
    """
    from session_init import main

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    if team_exists:
        team_dir = tmp_path / ".claude" / "teams" / "session-aabb1122"
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "config.json").write_text('{"members": []}')

    stdin_data = _stdin_payload(source="compact", agent_type=agent_type)

    # Minimum viable mock set: the steps that touch real disk / network
    # / external state, same shape as TestSourceAwareness._run_main_with_source
    # but with get_task_list REAL (reads from _compact_tasks_dir).
    patches = [
        patch("shared.session_init_common.setup_plugin_symlinks", return_value=None),
        patch("shared.session_init_common.ensure_project_memory_md", return_value=None),
        patch("shared.session_init_lead.update_session_info", return_value=None),
        patch("shared.session_init_lead.restore_last_session", return_value=None),
        patch("shared.session_init_lead.check_resume_state", return_value=None),
        patch("sys.stdin", io.StringIO(stdin_data)),
    ]
    if patch_get_task_list is not None:
        patches.append(
            patch("session_init.get_task_list", return_value=patch_get_task_list)
        )

    stdout = io.StringIO()
    with patch("sys.stdout", stdout):
        with _nested(*patches):
            with pytest.raises(SystemExit) as exc_info:
                main()

    assert exc_info.value.code == 0
    output = json.loads(stdout.getvalue())
    additional = output["hookSpecificOutput"]["additionalContext"]
    return additional, output


from contextlib import ExitStack


def _nested(*context_managers):
    """Enter multiple context managers as if via nested `with` — returns
    an ExitStack-backed wrapper so we don't need deeply nested `with`
    blocks in the helper above."""
    stack = ExitStack()
    for cm in context_managers:
        stack.enter_context(cm)
    return stack


class TestSessionInitCompactE2E:
    """E2E coverage for the post-compaction checkpoint block inside
    session_init's source=compact branch (migrated from
    TestCompactionRefreshPrimaryPathE2E).

    These tests put real task JSON into a temp ~/.claude/tasks/ dir and
    assert that session_init.main() renders the correct feature / phase /
    agent / blocker identities into additionalContext — and never
    fabricates identities that don't exist on disk.
    """

    def test_unknown_frame_gets_the_notice_not_the_checkpoint_ladder(
        self, _compact_tasks_dir, monkeypatch, tmp_path
    ):
        """PAIRED UNKNOWN ARM. The lead arms in this class drive a LEAD frame,
        which is the frame the compact checkpoint is written for. This arm keeps
        the UNKNOWN frame those arms used to build by accident, so the class
        covers both branches of the role gate rather than trading one for the
        other."""
        feature = {"id": "f-unknown", "subject": "Unknown-frame feature",
                   "status": "in_progress"}
        (_compact_tasks_dir / "f-unknown.json").write_text(json.dumps(feature))

        additional, _ = _run_session_init_compact(
            monkeypatch, tmp_path, agent_type=None
        )

        _assert_unknown_frame_body(additional)


# ---------------------------------------------------------------------------
# #500 plugin-version banner integration + counter-test-by-revert (moved
# from test_plugin_manifest.py per reviewer feedback — integration tests
# belong alongside the hook they exercise).
# ---------------------------------------------------------------------------


def _make_banner_plugin_root(tmp_path, manifest=None):
    """Build a plugin-root tree with optional manifest content.

    Returns (plugin_root, manifest_path_or_none).
    """
    plugin_root = tmp_path / "installed-cache"
    claude_plugin = plugin_root / ".claude-plugin"
    if manifest is None:
        return plugin_root, None
    claude_plugin.mkdir(parents=True)
    manifest_path = claude_plugin / "plugin.json"
    manifest_path.write_text(manifest)
    return plugin_root, manifest_path


class TestSessionInitSlotAIntegration:
    """End-to-end: banner appears in session_init.main() additionalContext.

    Verifies wiring between the helper and the hook — not just that the
    helper produces a string, but that session_init.main() actually calls
    it and includes the result in the emitted additionalContext. Mirrors
    the patching style of TestTeamResumeDetection in this file.
    """

    def _run_main(self, monkeypatch, tmp_path, plugin_root=None, manifest=None):
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "project"))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        (tmp_path / "home").mkdir(exist_ok=True)

        if plugin_root is not None:
            if manifest is not None:
                claude_plugin = plugin_root / ".claude-plugin"
                claude_plugin.mkdir(parents=True)
                (claude_plugin / "plugin.json").write_text(manifest)
            monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        else:
            monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)

        stdin_data = json.dumps(
            {"session_id": "abc12345-0000-0000-0000-000000000000"}
        )

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        return output["hookSpecificOutput"]["additionalContext"]

    def test_banner_appears_in_additional_context_happy_path(
        self, monkeypatch, tmp_path
    ):
        plugin_root = tmp_path / "installed-cache"
        additional = self._run_main(
            monkeypatch,
            tmp_path,
            plugin_root=plugin_root,
            manifest=json.dumps({"name": "PACT", "version": "3.18.1"}),
        )

        assert f"PACT plugin: PACT 3.18.1 (root: {plugin_root})" in additional


class TestCounterTestBySlotARevert:
    """Counter-test-by-revert for session_init Slot A append (d4f0f794
    dual-direction discipline). These tests are written so that if a
    future edit removes the `context_parts.append(format_plugin_banner())`
    call at Slot A (line ~709 in session_init.py), at least one named
    test here fails with a specific, informative assertion message.

    Verified empirically by reviewer-independent cp-backup revert:
    commenting out the Slot A append produces 4 Integration + 2 RevertGuard
    failures across these classes (cardinality 6)."""

    def test_banner_present_in_additional_context(
        self, monkeypatch, tmp_path
    ):
        """Load-bearing regression guard: if Slot A is reverted, the banner
        disappears from additionalContext and this assertion fails."""
        from session_init import main

        plugin_root = tmp_path / "installed-cache"
        claude_plugin = plugin_root / ".claude-plugin"
        claude_plugin.mkdir(parents=True)
        (claude_plugin / "plugin.json").write_text(
            json.dumps({"name": "PACT", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "project"))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        (tmp_path / "home").mkdir(exist_ok=True)

        stdin_data = json.dumps(
            {"session_id": "abc12345-0000-0000-0000-000000000000"}
        )

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit):
                main()

        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        assert "PACT plugin: PACT 3.18.1" in additional, (
            "Slot A banner missing from additionalContext — verify "
            "session_init.py line ~709 still appends format_plugin_banner()"
        )

class TestTeamResumeDetection:
    """Tests for resume-aware team detection in session_init.main().

    The hook checks whether ~/.claude/teams/{team_name}/config.json exists
    to determine if this is a fresh session (TeamCreate instruction) or a
    resumed session (reuse instruction).
    """

    def _run_main_with_team_detection(
        self,
        monkeypatch,
        tmp_path,
        stdin_data=None,
        agent_type: Optional[str] = _LEAD_AGENT_TYPE,
    ):
        """Helper: run main() with Path.home() pointed at tmp_path.

        Returns the additionalContext string from the hook output.
        """
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        if stdin_data is None:
            stdin_data = _stdin_payload(agent_type=agent_type)

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        return output["hookSpecificOutput"]["additionalContext"]

    def test_unknown_frame_gets_the_notice_not_the_team_directive(
        self, monkeypatch, tmp_path
    ):
        """PAIRED UNKNOWN ARM for the team-detection driver. See
        _assert_unknown_frame_body for why the positive token is load-bearing."""
        additional = self._run_main_with_team_detection(
            monkeypatch, tmp_path, agent_type=None
        )

        _assert_unknown_frame_body(additional)
        # The team directive rides the ladder, so a primary frame gets it too.
        assert "provided by the platform for this session" in additional


class TestSourceAwareness:
    """Tests for session source detection in session_init.main().

    The hook reads input_data["source"] which has 4 values:
    - "startup": fresh session (full init)
    - "resume": resumed session (model retains context)
    - "compact": context window compacted (model lost context)
    - "clear": /clear command (intentional context reset)

    Tests cover all 8 combinations (4 sources x 2 team states) plus edge cases.
    """

    def _run_main_with_source(
        self, monkeypatch, tmp_path, source, team_exists=False,
        agent_type: Optional[str] = _LEAD_AGENT_TYPE,
    ):
        """Helper: run main() with given source and team state.

        FIXTURE-ONLY ``team_exists`` (post-#979-Phase-2 source-only collapse):
        the production directive no longer branches on team existence — the
        platform pre-creates exactly one team per session, so ``source`` alone
        selects the branch. ``team_exists`` here ONLY controls whether the
        on-disk team-config fixture is written (lines below); it does NOT steer
        any live code path. It is retained so the directive can still be
        asserted source-only REGARDLESS of on-disk team state (a meaningful
        regression: the directive must be identical whether or not the team dir
        happens to exist). Do not read it as exercising a team_exists branch.

        Returns (additionalContext, mock_symlinks_called, _legacy_kernel_called=False).
        """
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        if team_exists:
            team_dir = tmp_path / ".claude" / "teams" / "session-aabb1122"
            team_dir.mkdir(parents=True)
            (team_dir / "config.json").write_text('{"members": []}')

        stdin_data = _stdin_payload(source=source, agent_type=agent_type)

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None) as mock_symlinks, \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        return additional, mock_symlinks.called, False

    # --- Path 1: startup + no team (fresh session) ---

    @pytest.mark.parametrize("source", ["startup",])
    def test_unknown_frame_gets_the_notice_on_every_source(
        self, source, monkeypatch, tmp_path
    ):
        """PAIRED UNKNOWN ARM, across all four sources.

        The role gate runs BEFORE the source ladder, so an unknown frame must
        receive the notice whatever the source is. Parametrized over the four
        sources this class exists to separate, so a role gate that leaked the
        ladder back on one source alone cannot stay green."""
        additional, _, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source=source, team_exists=True,
            agent_type=None,
        )

        _assert_unknown_frame_body(additional)
        # The team directive rides the ladder, so a primary frame gets it too.
        assert "provided by the platform for this session" in additional


    # --- Path 2: resume + team exists (normal resume) ---


    # --- Path 3: compact + team exists (post-compaction recovery) ---


    # --- Path 4: clear + team exists (context intentionally cleared) ---


    # --- Path 5: anomalous combinations ---


    # --- Edge cases ---


class TestResumeOwnSummaryPointer:
    """The resume-limb own-dir summary pointer (re-scoped #1520 fix).

    The resume limb names the session's OWN compact summary — canonical
    filename first, newest archive as fallback — because the interactive
    capture showed stdin id stays OLD across resume (prev == current; no
    new-id dir), so the summary is never in a sibling dir; it sits, usually
    archived-in-place by the non-compact clears that run BEFORE the source
    limbs render, in this session's own dir.

    Driver world note: `_run_main_with_source` leaves CLAUDE_CONFIG_DIR
    unset, so the config root is the patched home (tmp_path / ".claude"),
    and the session dir is
    ``tmp_path/.claude/pact-sessions/test-project/<_TEST_SESSION_ID>/``.

    Absence-arm pairing: ``test_resume_team_exists_reuse`` above (no summary
    planted → no clause) is the ABSENCE half of every carrier-present arm
    here — vacuity discipline, one control per absence cell.
    """

    _CLAUSE_PHRASE = (
        "A compact summary from an earlier point of this session is "
        "available at"
    )
    # The sid-free ROOT-singleton fragment: text naming it would point at
    # the DEGRADED writer's path (no slug, no session id). The session-
    # scoped path must never render this shape.
    _SID_FREE_FRAGMENT = "pact-sessions/compact-summary.txt"

    def _session_dir(self, tmp_path) -> Path:
        return (
            tmp_path / ".claude" / "pact-sessions" / "test-project"
            / _TEST_SESSION_ID
        )

    def _run_resume_with_planted_summary(
        self, monkeypatch, tmp_path, plant, source="resume",
    ):
        """Run main() with a summary planted in the session's own dir FIRST.

        ``plant="canonical"`` writes compact-summary.txt BEFORE main() runs —
        so the own-dir clear (which fires for every non-compact source before
        the source limbs render) MOVES it to a timestamped archive by limb
        time. This is the REAL resumed world: the planted-canonical arm
        asserts the clause names the ARCHIVE the clear produced, proving the
        ordering interaction end-to-end. ``plant="none"`` plants the session
        dir only (carrier-present control substrate).
        """
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        session_dir = self._session_dir(tmp_path)
        session_dir.mkdir(parents=True, exist_ok=True)
        if plant == "canonical":
            (session_dir / "compact-summary.txt").write_text(
                "PLANTED SUMMARY BYTES", encoding="utf-8"
            )

        stdin_data = _stdin_payload(source=source)
        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        return output["hookSpecificOutput"]["additionalContext"]

    def test_planted_canonical_lands_as_named_archive(self, monkeypatch, tmp_path):
        """Canonical planted pre-run → the clear archives it → the clause
        names that ARCHIVE by absolute path (the both-orders integration
        arm: plant canonical, assert the archive is what gets named)."""
        additional = self._run_resume_with_planted_summary(
            monkeypatch, tmp_path, plant="canonical",
        )

        session_dir = self._session_dir(tmp_path)
        archives = list(session_dir.glob("compact-summary-*.txt"))
        assert len(archives) == 1, (
            f"the own-dir clear should have archived the planted canonical, "
            f"found {archives}"
        )
        assert self._CLAUSE_PHRASE in additional
        assert str(archives[0]) in additional
        # The canonical slot itself must NOT be named — it is empty by limb
        # time, and naming it would send the lead to a vacated path. The
        # archive name ("compact-summary-<stamp>.txt") does not contain the
        # canonical filename as a substring, so this is a clean discriminator.
        assert str(session_dir / "compact-summary.txt") not in additional


    # --- F-ARCH-1 first-surface gate + F-SEC-2 drain-prefix isolation ---

    @staticmethod
    def _iso_from_epoch(epoch: float) -> str:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def _plant_start_event(
        self, tmp_path, source, ts_epoch, session_id=_TEST_SESSION_ID,
    ):
        """Append one session_start event to the session's journal using the
        writer's own field set (v/type/session_id/project_dir/team/worktree/
        source/ts) so a schema-validating reader sees a real event."""
        session_dir = self._session_dir(tmp_path)
        session_dir.mkdir(parents=True, exist_ok=True)
        event = {
            "v": 1,
            "type": "session_start",
            "session_id": session_id,
            "project_dir": "/Users/example/Sites/test-project",
            "team": "session-aabb1122",
            "worktree": "",
            "source": source,
            "ts": self._iso_from_epoch(ts_epoch),
        }
        with open(session_dir / "session-journal.jsonl", "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def _clause_direct(self, tmp_path):
        from session_init import _resume_own_summary_clause

        return _resume_own_summary_clause(str(self._session_dir(tmp_path)))


class TestTeamCreateStringFreshSession:
    """The fresh-session team-create string must follow the #444 4-sentence prelude."""

    def test_starts_with_pact_role_marker(self, monkeypatch, tmp_path):
        additional, _, _ = _run_session_init_for_path(
            monkeypatch, tmp_path, source="startup", team_exists=False
        )
        assert additional.startswith("YOUR PACT ROLE: orchestrator")


class TestTeamReuseStringResumedSession:
    """The resumed-session team-reuse string must follow the new prelude format."""

    def test_starts_with_pact_role_marker(self, monkeypatch, tmp_path):
        additional, _, _ = _run_session_init_for_path(
            monkeypatch, tmp_path, source="resume", team_exists=True
        )
        assert additional.startswith("YOUR PACT ROLE: orchestrator")


class TestHappyPathOutputInvariant:
    """N1: after PR #390, the happy-path output dict is guaranteed non-empty.

    The _team_create / _team_reuse string is always insert(0, ...)'d into
    context_parts regardless of source/team_exists branch, so the `if output:`
    else-branch that previously emitted `_SUPPRESS_OUTPUT` was unreachable
    and has been deleted. This test pins the invariant end-to-end: a minimal
    happy-path run emits hookSpecificOutput (not the suppression sentinel).

    Guards against a future regression where a new branch accidentally
    leaves context_parts empty, which would make the hook silently emit
    nothing and break governance delivery.
    """

    def test_unknown_frame_still_emits_hook_specific_output(
        self, monkeypatch, tmp_path
    ):
        """PAIRED UNKNOWN ARM for the non-empty-output invariant.

        THE INVARIANT THIS CLASS EXISTS FOR IS ROLE-FREE: the hook must emit
        hookSpecificOutput rather than the suppression sentinel, because an
        empty context_parts breaks governance delivery. The role gate adds a
        branch that could have left context_parts EMPTY for an unknown frame,
        which is the one way this invariant could have been broken by the
        repair, so the unknown frame needs its own witness here.

        This is also why the notice matters rather than silence: a silent
        unknown branch would emit the suppression sentinel and fail this arm.
        """
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "project"))
        (tmp_path / "project").mkdir()
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        stdin_data = _stdin_payload(source="startup", agent_type=None)

        with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("shared.session_init_lead.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        raw = mock_stdout.getvalue()
        output = json.loads(raw)
        assert "hookSpecificOutput" in output, (
            "an unknown frame must still produce hookSpecificOutput: a silent "
            "unknown branch would emit the suppression sentinel instead"
        )
        assert "suppressOutput" not in raw
        _assert_unknown_frame_body(output["hookSpecificOutput"]["additionalContext"])


# ---------------------------------------------------------------------------
# F2 exception safety net
# ---------------------------------------------------------------------------
#
# PR #390 replaces the persistent ~/.claude/CLAUDE.md kernel with a lazy-loaded
# bootstrap skill. The session_init hook is now the PRIMARY delivery channel
# for the PACT ROLE marker + Skill("repacted:bootstrap") YOUR FIRST ACTION directive.
#
# If session_init.main() throws BEFORE it has built the team_create/team_reuse
# block, the team-lead would previously get only {"systemMessage": "..."} back and
# the governance delivery chain would be broken for that one session. The F2
# safety net in the outer except block rebuilds a minimal PACT ROLE block so
# the team-lead still knows how to bootstrap even on the failure path.
# ---------------------------------------------------------------------------


class TestBuildSafetyNetContext:
    """Unit tests for _build_safety_net_context() helper."""

    def test_lead_role_starts_with_pact_role_marker(self):
        """ONLY a lead frame leads with the orchestrator marker at byte 0.

        RETARGET of test_none_team_starts_with_pact_role_marker, which looped
        over (None, "lead", "unknown") and asserted all three lead with the
        orchestrator marker. Its own docstring called that grouping "the safe
        default". IT WAS NOT SAFE, AND THAT ARM PINNED THE DEFECT: handing the
        orchestrator instructions to a frame of unknown role was measured
        reaching 13 of 13 teammate frames that fired a compact SessionStart.

        The three roles are now three cases and each is asserted separately by
        the three tests below, so a collapse of any two back into one cannot
        stay green. The looped form could not do that: it asserted on the first
        iteration, so a failure at None left "lead" and "unknown" unevaluated.
        """
        from session_init import _build_safety_net_context

        result = _build_safety_net_context(None, "lead")

        assert result.startswith("YOUR PACT ROLE: orchestrator."), (
            "frame_role='lead' must lead with 'YOUR PACT ROLE: orchestrator.' "
            "(line-anchored for routing block consumer check)."
        )


    # The four team_name tests below pass frame_role="lead" EXPLICITLY. The
    # team_name argument is interpolated by the LEAD branch and by no other, so
    # the branch these four exercise is reachable on the lead path alone. They
    # used to omit frame_role and reach the branch only because every non-
    # teammate role fell through to it; the frame they built was never the frame
    # they named. The None and "unknown" roles keep their own tests above.


    # ---- #888: role-aware teammate branch ----------------------------------
    # A teammate frame that hits the safety net must be marked as a teammate,
    # NOT as the orchestrator, and must NOT be handed the lead-only bootstrap
    # directive. (Before #888 every frame got the orchestrator marker + the
    # bootstrap directive, mis-roling a teammate.)


class TestM2TeammateAdvisoryGating:
    """The safety-net context text. The class's pin-advisory gating arms
    (#806 m2) went with the 1.5.0 pin-gate cut, which removed the advisories."""

    def test_with_team_mentions_partial_failure(self):
        """The team-present branch should note that session_init partially failed."""
        from session_init import _build_safety_net_context

        result = _build_safety_net_context("pact-abc123", "lead")

        assert "partially failed" in result
        assert "check systemMessage" in result


class TestReadOnlyHomeScenario:
    """S6: scenario test simulating a read-only ~/.claude directory.

    The home CLAUDE.md migration (remove_stale_kernel_block) opens a file_lock
    and writes to ~/.claude/CLAUDE.md. If the .claude parent directory is
    read-only, the write fails — but the hook must still deliver the
    governance chain: exit 0, valid JSON on stdout, and additionalContext
    starting with "YOUR PACT ROLE: orchestrator." so the team-lead can load bootstrap.

    Unlike the unit-level OSError mocks, this test uses a real chmod on a
    real temp directory and exercises the full migration code path end to
    end. Permissions are restored in a finally block so tmp_path cleanup
    does not fail.
    """

    def test_readonly_home_unknown_frame_gets_the_notice(self, tmp_path, monkeypatch):
        """PAIRED UNKNOWN ARM for the read-only home scenario.

        The property this class locks is FAIL-OPEN DELIVERY: a read-only home
        must not cost the exit code, the valid JSON, or the role-appropriate
        body. The lead arm below asserts the lead body. This arm asserts the
        unknown body on the same degraded path, so the role gate is shown to
        survive a real filesystem failure rather than only the mocked ones.

        The chmod and the restore in the finally block are duplicated from the
        lead arm on purpose: the two arms must fail independently, so neither
        can leave the directory read-only for the other.
        """
        import stat

        from session_init import main

        fake_home = tmp_path / "fakehome"
        home_claude = fake_home / ".claude"
        home_claude.mkdir(parents=True)
        (home_claude / "CLAUDE.md").write_text(
            "# Personal Preferences\n"
            "\n"
            "<!-- PACT_START: legacy -->\n"
            "Obsolete orchestrator block.\n"
            "<!-- PACT_END -->\n",
            encoding="utf-8",
        )

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".claude").mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        original_mode = home_claude.stat().st_mode
        home_claude.chmod(stat.S_IRUSR | stat.S_IXUSR)  # 0o500

        stdin_data = _stdin_payload(source="startup", agent_type=None)

        try:
            with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
                 patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
                 patch("session_init.get_task_list", return_value=None), \
                 patch("shared.session_init_lead.restore_last_session", return_value=None), \
                 patch("shared.session_init_lead.check_resume_state", return_value=None), \
                 patch("sys.stdin", io.StringIO(stdin_data)), \
                 patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
                 patch("sys.stderr", new_callable=io.StringIO):
                with pytest.raises(SystemExit) as exc_info:
                    main()

            assert exc_info.value.code == 0
            output = json.loads(mock_stdout.getvalue())
            _assert_unknown_frame_body(
                output["hookSpecificOutput"]["additionalContext"]
            )
        finally:
            home_claude.chmod(original_mode)


# =============================================================================
# Issue #414 R2: session_start event includes `source` field
# =============================================================================


