# pact-plugin/tests/test_session_end.py
"""
Tests for session_end.py — SessionEnd hook for session lifecycle management.

session_end.py is purely observational — no destructive operations on project files.

Tests cover:
1. main() entry point: exit codes, error handling, journal event emission
2. check_unpaused_pr() — journal-based safety-net for unpaused PRs:
   - Reads session_paused events from journal (skip warning if paused)
   - Reads review_dispatch events from journal (primary PR detection)
   - Falls back to task metadata/handoff scanning for PR number
   - Writes warning to journal via append_event
3. cleanup_old_sessions() — stale session directory removal
"""
import io
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest


def _backdate(session_dir, stamp):
    """Set a session dir's mtime and every direct child's to ``stamp``.

    The session reaper ages a dir by the newer of its own mtime and its newest
    child's, so a fixture that backdates only the dir leaves it looking fresh.
    """
    import os as _os
    for child in Path(session_dir).iterdir():
        _os.utime(str(child), (stamp, stamp), follow_symlinks=False)
    _os.utime(str(session_dir), (stamp, stamp))


class TestGetProjectSlug:
    """Tests for session_end.get_project_slug() — reads via get_project_dir()."""

    def test_returns_basename_from_project_dir(self):
        from session_end import get_project_slug

        with patch("session_end.get_project_dir", return_value="/Users/example/Sites/my-project"):
            assert get_project_slug() == "my-project"


class TestMainEntryPoint:
    """Tests for session_end.main() exit behavior and call orchestration."""

    def _patch_main_deps(self, **overrides):
        """Return a combined context manager mocking main()'s dependencies.

        Default mocks: pact_context.init, get_project_dir,
        get_session_id, get_team_name, get_task_list, append_event,
        check_unpaused_pr, cleanup_old_sessions, cleanup_old_teams,
        cleanup_old_tasks, _prune_registry_dead_teams.

        Pass keyword overrides to replace defaults (e.g., get_task_list=...).
        """
        defaults = {
            "pact_context_init": patch("session_end.pact_context.init"),
            "get_project_dir": patch("session_end.get_project_dir",
                                     return_value="/Users/example/Sites/my-project"),
            "get_session_id": patch("session_end.get_session_id", return_value=""),
            "get_team_name": patch("session_end.get_team_name", return_value="pact-abc12345"),
            "get_task_list": patch("session_end.get_task_list", return_value=[]),
            "append_event": patch("session_end.append_event"),
            "check_unpaused_pr": patch("session_end.check_unpaused_pr"),
            "cleanup_old_sessions": patch("session_end.cleanup_old_sessions"),
            "cleanup_old_teams": patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            "cleanup_old_tasks": patch("session_end.cleanup_old_tasks", return_value=(0, 0)),
            # #1187 closer: main() calls _prune_registry_dead_teams() with NO
            # args, so the default registry_path resolves via
            # get_claude_config_dir() and would run the REAL reaper against the
            # operator's .teammate-registry.jsonl under the test. Patch it at
            # the module attr main() resolves (bare call → session_end global
            # lookup). return_value=0 matches the int-pruned contract; main()
            # does not consume it today. Together with conftest's CLAUDE_CONFIG_DIR
            # redirect, the unrooted registry write is unreachable by construction.
            "_prune_registry_dead_teams": patch("session_end._prune_registry_dead_teams", return_value=0),
        }
        defaults.update(overrides)
        return defaults

    def test_main_exits_0_on_success(self):
        from session_end import main

        patches = self._patch_main_deps()
        with patch("sys.stdin", io.StringIO("{}")):
            with ExitStack() as stack:
                for p in patches.values():
                    stack.enter_context(p)
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0


# =============================================================================
# check_unpaused_pr() Tests
# =============================================================================

class TestCheckUnpausedPr:
    """Tests for session_end.check_unpaused_pr() — journal-based safety-net.

    Detects open PRs that were NOT paused (no memory consolidation), returning
    a warning string that the caller attaches to the single session_end event.

    Key behavior:
    - Compares session_paused vs review_dispatch event timestamps:
      pause covers PR only when last_pause_ts >= last_review_ts.
    - Reads review_dispatch events (primary PR detection from journal)
    - Falls back to task metadata scanning (safety net for non-journal PRs)
    - Returns the warning string (or None) instead of writing the journal
      directly — the caller emits the single session_end event.

    #453 Fix A defensive-default: each test in this class runs with
    `session_end.check_pr_state` patched to return "OPEN" so the
    last-line-of-defense live gh call does NOT shell out to the real
    `gh pr view` (which would flake against arbitrary PR numbers used
    in fixtures). Individual tests that exercise the MERGED / CLOSED /
    fail-open branches patch check_pr_state explicitly with their own
    return_value (inner patch wins over the fixture).
    """

    @pytest.fixture(autouse=True)
    def _default_check_pr_state(self):
        with patch("session_end.check_pr_state", return_value="OPEN"):
            yield

    def _make_task_with_pr_number(self, pr_number):
        """Helper: task with pr_number in metadata."""
        return {
            "id": "1",
            "subject": "Review: auth feature",
            "status": "completed",
            "metadata": {"pr_number": pr_number},
        }

    def _make_task_with_pr_url(self, pr_url):
        """Helper: task with PR URL in handoff metadata."""
        return {
            "id": "2",
            "subject": "backend-coder: implement auth",
            "status": "completed",
            "metadata": {
                "handoff": {
                    "produced": ["src/auth.py"],
                    "decisions": ["Used JWT"],
                    "artifact": pr_url,
                }
            },
        }

    def test_detects_pr_number_in_task_metadata(self):
        """Should return warning string when pr_number found in task metadata."""
        from session_end import check_unpaused_pr

        tasks = [self._make_task_with_pr_number(288)]

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #288" in warning
        assert "pause-mode was not run" in warning


    # ========================================================================
    # M2 — pause-vs-review timestamp reconciliation tests
    # ========================================================================


    # ========================================================================
    # #453 Fix B — session_consolidated short-circuit tests
    # ========================================================================


    # ========================================================================
    # #453 Fix A — live PR-state defense-in-depth tests
    # ========================================================================


    # ========================================================================
    # #453 T19-T21 — fail-open at the real subprocess boundary
    #
    # Unlike the tests above (which patch session_end.check_pr_state directly
    # with a "" return value to simulate the fail-open sentinel), these tests
    # patch shared.gh_helpers.subprocess.run to raise each canonical error
    # type and let the REAL gh_helpers.check_pr_state code convert the
    # exception into the empty-string sentinel. This pins the full
    # plumbing path: subprocess raises → gh_helpers catches → returns
    # "" → session_end falls through to warn.
    # ========================================================================


# =============================================================================
# cleanup_old_sessions() Tests
# =============================================================================

class TestCleanupOldSessions:
    """Tests for session_end.cleanup_old_sessions() — stale session directory removal.

    CARRIER-GUARD ARMS A1 to A5 (`test_carrier_guard_*`) COME IN TWO PAIRS,
    AND A LATER EDITOR MUST NOT REMOVE ONE HALF:

    * A1 (un-harvested handoff survives) and A4 (unreadable journal
      survives) are the arms that a mutant returning ``False``
      unconditionally must redden. They pin the protection itself.
    * A3 (no handoff reaps) and A5 (no journal reaps) are the VACUITY arms,
      and they are not optional. Without them a mutant that refuses to reap
      anything passes the suite, and the reaper looks guarded while it has
      stopped working.

    A2 (handoff plus consolidation reaps) pins that a durable second carrier
    releases the directory.

    ARMS A6 TO A15 EACH REDDEN ON ONE FAULT THAT FEW OR NO OTHER ARMS HERE
    CATCH, and that is the property to keep when editing them:

    * A6 (payload mention reaps) is the only arm that separates a verdict
      taken from the parsed ``type`` field from one taken from the raw text
      of the line. A1, A2, A3 and A5 put the name in BOTH, so the two
      readings agree on all of them.
    * A7 (corrupt line) is the only arm where a line reaches ``json.loads``
      and fails. Every other arm writes well-formed JSON.
    * A8 (JSON array line) is the only arm that reaches the type check with
      a parsed value that has no ``.get`` method.
    * A9 (a handoff BELOW the consolidation) is the only arm that separates
      "a consolidation anywhere releases" from "a consolidation releases what
      came before it". A2 writes the handoff first, so it cannot.
    * A10 (JSON-escaped ``type`` value) is the only arm that reddens if the
      predicate decides from the RAW BYTES of a line rather than from the
      value the line parses to. A6 pins the same distinction from the other
      side, on a line that mentions the name and is not the event. A10 pins
      it on a line that IS the event and does not mention the name.
    * A11 (handoff, consolidation, handoff) carries work on the two sides of
      the consolidation, so it holds when a reader asks if the guard ignores
      order rather than reads it.
    * A12 (a handoff below 50 filler lines) is the only arm of which the
      deciding event sits away from the head of the file. Each other arm
      decides in its first three lines, so ALL of them are blind to a
      predicate that reads only the head. SEE THE NOTE BELOW.
    * A13 (a byte that is not valid UTF-8) is the only arm that reaches the
      decode layer. A7 covers a line that decodes and does not parse. A13
      covers bytes that do not reach the parser.
    * A14 (handoff, consolidation, handoff, consolidation) is the only arm
      that holds the RELEASE half of the high-water rule. A9 and A11 hold
      the keep half. MEASURED: a mutant of which only the FIRST
      consolidation resets agrees with the shipped predicate on each shape
      the other arms seed and diverges on A14 alone.
    * A15 (handoff, consolidation, one other event) is the only arm of
      which the consolidation is NOT the last line. It says that the reset
      applies where it is written. MEASURED: a mutant of which only a
      consolidation on the LAST event line resets diverges on A15 alone,
      and A14 is green against it.

    A14 AND A15 ARE TWO ARMS ON ONE RULE AND NEITHER COVERS THE OTHER. A14
    kills a one-shot reset and is blind to a last-line reset. A15 kills a
    last-line reset and is blind to a one-shot reset. Each was found after
    the rule looked complete, so treat a third arm on this rule as likely
    rather than as excess.

    A7, A8, A9, A10, A11, A12 and A13 are PROTECTION arms and belong with A1
    and A4: each reddens when the guard stops protecting a carrier. A6, A14
    and A15 are release arms and belong with A2, A3 and A5.

    🔴 COUNT THE INDEPENDENT CONDITIONS, NOT THE ARMS. Nine arms redden
    when the guard always permits removal (A1, A4, A7, A8, A9, A10, A11,
    A12, A13). THAT COUNT OVERSTATES THE PROTECTION, and two measurements
    say so. A mutant that renames the compared ``agent_handoff`` literal
    reddens EIGHT of the nine together, because each of them seeds that
    event and depends on the same recognition. A4 is the ONLY one of the
    nine independent of it, because its journal is unreadable and the
    verdict never reaches a type comparison. AND ALL NINE ASSERT THAT THE
    DIRECTORY SURVIVES, so a fault that stops an entry reaching the removal
    branch leaves ALL NINE GREEN: measured with a mutant that makes the age
    test never pass, the red set was A2, A3, A5, A6, A14 and A15, and no
    protection arm fired. RE-MEASURE THIS SET WHEN YOU ADD A RELEASE ARM.
    It moved from four to five to six as A14 and A15 landed, and a recorded
    set goes stale the moment a member joins it. THE RELEASE ARMS ARE WHAT CATCHES THAT, which is
    why they are not optional. Each arm adds one further condition beyond
    the shared one, and the mutants each arm alone kills are the honest
    measure of what this class detects.

    🔴 A7, A8 AND A13 ASSERT ON THE PREDICATE AS WELL AS END TO END, AND
    THE PREDICATE LEG IS THE ONE THAT DISCRIMINATES. The caller wraps each
    entry in ``except Exception: continue``, which is correct: it keeps the
    bytes and keeps the reaper loop alive. IT ALSO MAKES A RAISE LOOK
    EXACTLY LIKE CORRECT BEHAVIOUR from outside, because the directory
    survives either way. MEASURED on isolated copies: removing the parse
    guard, the ``isinstance`` guard, or ``errors="replace"`` each makes the
    predicate raise, and each left an end-to-end-only arm GREEN. A DEFECT
    CLASS THAT MAKES THE PREDICATE RAISE IS INVISIBLE FROM THE REAPER
    OUTCOME. Do not weaken those three arms to an end-to-end check.

    🔴 A12 GUARDS THE CLASS OF A DEFECT THIS FILE ONCE LOCKED IN. The
    predicate used to return "safe to remove" at the FIRST consolidation and
    stop, so a handoff written below it was unseen. That is an EARLY EXIT.
    A9 pins the specific shape, and A12 pins the class, because a suite that
    cannot detect an early exit cannot detect the return of that defect in a
    new form. Keep an arm of which the deciding event sits far from the head
    of the file.

    THESE ARMS COVER THE EXECUTABLE HALF ONLY. The step 4 instruction in
    ``skills/pact-handoff-harvest/SKILL.md`` is the non-executable half and
    is not covered here.

    A10 IS A REGRESSION PIN ON A DEFECT THAT WAS FIXED, NOT ON A DESIGN
    CHOICE. The predicate carried a cheap substring test above the parse. It
    read the RAW line, so a line of which the ``type`` value used a JSON
    escape parsed to the correct event name and was dropped BEFORE the
    parse. The guard then answered "no carrier" for a journal that held a
    handoff, and the reaper removed the only copy. THE FAIL-OPEN DIRECTION.
    A10 is red against that shape and green without it. Do not re-introduce
    a pre-parse test on the raw line to save the parse cost.
    """

    def _create_session_dir(self, slug_dir, session_id, age_days=0):
        """Helper: create a session directory with controlled mtime."""
        import time as _time
        session_dir = slug_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        # Write a file so the directory has content
        (session_dir / "pact-session-context.json").write_text("{}")
        if age_days > 0:
            old_time = _time.time() - (age_days * 86400)
            import os as _os
            _backdate(session_dir, old_time)
        return session_dir

    def _age_dir(self, session_dir, age_days):
        """Helper: backdate a directory's mtime.

        Separate from _create_session_dir because writing the journal into
        the directory bumps the parent mtime. The age must be applied AFTER
        every child write, or the entry looks fresh and the age test skips
        it before the carrier guard is ever reached.
        """
        import os as _os
        import time as _time
        old_time = _time.time() - (age_days * 86400)
        _backdate(session_dir, old_time)

    def _write_journal(self, session_dir, event_types):
        """Helper: write one journal line for each event type given.

        The event name goes in the ``type`` field, because that is where the
        journal writer puts it. A fixture seeding an ``event`` field matches
        no line, so every arm below would pass against a predicate that
        reads nothing at all.
        """
        import json as _json
        lines = [
            _json.dumps({"type": t, "ts": "2026-01-01T00:00:00Z"})
            for t in event_types
        ]
        (session_dir / "session-journal.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _write_journal_raw(self, session_dir, raw_lines):
        """Helper: write journal lines VERBATIM, with no JSON encode step.

        ``_write_journal`` can only produce well-formed objects that carry
        the event name in ``type``. The arms below need three shapes it
        cannot make: the name in a payload with a different ``type``, a line
        that fails to parse, and a line that parses to something other than
        an object. Each of those reaches a different branch of the predicate.
        """
        (session_dir / "session-journal.jsonl").write_text(
            "\n".join(raw_lines) + "\n", encoding="utf-8"
        )

    def test_removes_old_session_directories(self, tmp_path):
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        old_id = "11111111-2222-3333-4444-555555555555"

        self._create_session_dir(slug_dir, current_id, age_days=0)
        self._create_session_dir(slug_dir, old_id, age_days=10)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )

        assert (slug_dir / current_id).exists()
        assert not (slug_dir / old_id).exists()


    # -------------------------------------------------------------------
    # Carrier guard, arms A1 to A5. See the class docstring for the pairing
    # rule: A1 and A4 pin the protection, A3 and A5 are the vacuity arms.
    # -------------------------------------------------------------------


# =============================================================================
# cleanup_old_sessions() — Adversarial/Boundary Cases (Test Engineer)
# =============================================================================

class TestCleanupOldSessionsBoundary:
    """Boundary and adversarial tests for cleanup_old_sessions()."""

    def _create_session_dir(self, slug_dir, session_id, age_days=0):
        """Helper: create a session directory with controlled mtime."""
        import os as _os
        import time as _time
        session_dir = slug_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "pact-session-context.json").write_text("{}")
        if age_days > 0:
            old_time = _time.time() - (age_days * 86400)
            _backdate(session_dir, old_time)
        return session_dir

    def test_exactly_at_boundary_not_deleted(self, tmp_path):
        """Directory at 6.9 days age should NOT be deleted.

        The code uses `age_days > max_age_days` (strictly greater than).
        We use 6.9 days (safely under 7) to avoid flakiness from time
        elapsing between utime() and the stat() call inside cleanup.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        boundary_id = "11111111-2222-3333-4444-555555555555"

        self._create_session_dir(slug_dir, current_id, age_days=0)
        boundary_dir = slug_dir / boundary_id
        boundary_dir.mkdir(parents=True, exist_ok=True)
        (boundary_dir / "context.json").write_text("{}")
        # Set to 6.9 days — safely under threshold
        under_time = _time.time() - (6.9 * 86400)
        _backdate(boundary_dir, under_time)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )

        # Under threshold — should survive
        assert boundary_dir.exists()


# =============================================================================
# main() Integration — Full SessionEnd Flow (Test Engineer)
# =============================================================================

class TestMainIntegrationCleanup:
    """Integration tests for main() exercising cleanup functions with session context.

    Verifies that main() correctly chains pact_context.init() ->
    cleanup_old_sessions() using the session context from stdin.
    """

    def test_main_calls_cleanup_old_sessions(self):
        """main() should call cleanup_old_sessions with session context."""
        from unittest.mock import patch, MagicMock
        import io

        input_data = json.dumps({"session_id": "test-session"})

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("session_end.pact_context") as mock_ctx, \
             patch("session_end.get_project_dir", return_value="/test/proj"), \
             patch("session_end.get_session_id", return_value="test-session"), \
             patch("session_end.get_task_list", return_value=[]), \
             patch("session_end.check_unpaused_pr"), \
             patch("session_end.cleanup_old_sessions") as mock_cleanup, \
             pytest.raises(SystemExit):
            mock_ctx.init = MagicMock()
            from session_end import main
            main()

        mock_cleanup.assert_called_once_with(
            project_slug="proj",
            current_session_id="test-session",
            old_slug="proj",
        )


# =============================================================================
# _is_paused_session() Tests
# =============================================================================

class TestIsPausedSession:
    """Tests for session_end._is_paused_session() — paused-session detection.

    Semantics: a session is "paused" iff its journal contains ANY
    session_paused event, regardless of later session_end events. This
    is a "has-ever-been-paused" predicate. The caller applies a longer
    TTL (180 days) to paused sessions to preserve in-progress work
    across the pause→quit→session_end race (AdvF1) and equal-timestamp
    ties (BugF2).
    """

    def _write_journal(self, session_dir, events):
        """Helper: write events to a session's journal file."""
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(e) + "\n" for e in events]
        journal.write_text("".join(lines))

    def test_returns_true_for_paused_only(self, tmp_path):
        """Session with session_paused but no session_end is paused."""
        from session_end import _is_paused_session

        session_dir = str(tmp_path / "sess-abc")
        self._write_journal(session_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 42, "ts": "2026-01-01T01:00:00Z"},
        ])

        assert _is_paused_session(session_dir) is True


# =============================================================================
# _is_checkpointed_session() Tests — paused OR refreshed existence predicate
# =============================================================================

class TestIsCheckpointedSession:
    """Existence-OR truth table for session_end._is_checkpointed_session().

    The predicate extends _is_paused_session to refresh checkpoints:
    True iff the journal holds ANY session_paused OR session_refreshed
    event. Pure existence — no ts comparison — so the refresh→quit race
    (session_end fires ~1s after the session_refreshed write) can never
    produce a wrong answer, mirroring the AdvF1/BugF2 pause precedent.
    """

    def _write_journal(self, session_dir, events):
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text("".join(json.dumps(e) + "\n" for e in events))

    @pytest.mark.parametrize(
        "event_types, expected",
        [
            (["session_paused"], True),],
        ids=["paused-only",],
    )
    def test_existence_or_truth_table(self, tmp_path, event_types, expected):
        from session_end import _is_checkpointed_session

        session_dir = str(tmp_path / "sess-ckpt")
        events = [
            {"v": 1, "type": t, "ts": f"2026-01-01T0{i}:00:00Z"}
            for i, t in enumerate(event_types)
        ]
        self._write_journal(session_dir, events)
        assert _is_checkpointed_session(session_dir) is expected


# =============================================================================
# cleanup_old_sessions() — Refreshed Session Preservation Tests
# =============================================================================

class TestCleanupRefreshedPreservation:
    """Dual-bound behavior for refreshed sessions in cleanup_old_sessions().

    The 180-day journal guard is the LONG-GAP bound: a
    refreshed-but-never-resumed journal survives the 30-day active TTL
    (30d = full-fidelity resume bound — tasks/teams reapers are
    journal-blind and still reap at 30d; the resume prompt's HALT
    cross-check surfaces the documented loud-mismatch warning past that)
    and still ages out past the 180-day checkpoint TTL.
    """

    def _create_session_dir(self, slug_dir, session_id):
        session_dir = slug_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "pact-session-context.json").write_text("{}")
        return session_dir

    def _set_age(self, session_dir, age_days):
        import os as _os
        import time as _time
        old_time = _time.time() - (age_days * 86400)
        _backdate(session_dir, old_time)

    def _write_journal(self, session_dir, events):
        journal = session_dir / "session-journal.jsonl"
        journal.write_text("".join(json.dumps(e) + "\n" for e in events))

    def test_refreshed_session_survives_beyond_30d(self, tmp_path):
        """A 35-day-old refreshed session survives the active TTL —
        WITHOUT the guard this journal would be reaped at 30 days."""
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        refreshed_id = "33333333-4444-5555-6666-777777777777"

        self._create_session_dir(slug_dir, current_id)
        refreshed_dir = self._create_session_dir(slug_dir, refreshed_id)
        self._write_journal(refreshed_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_refreshed",
             "consolidation_completed": True, "halt_active": False,
             "ts": "2026-01-01T01:00:00Z"},
            {"v": 1, "type": "session_end", "ts": "2026-01-01T01:00:01Z"},
        ])
        self._set_age(refreshed_dir, 35)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        assert refreshed_dir.exists()


# =============================================================================
# cleanup_old_sessions() — Paused Session Preservation Tests
# =============================================================================

class TestCleanupPausedPreservation:
    """Tests for paused-session preservation in cleanup_old_sessions().

    Dual-TTL semantics (AdvF1/BugF2 fix): any session that has ever
    recorded a session_paused event uses the extended paused TTL
    (_PAUSED_SESSION_MAX_AGE_DAYS, default 180 days). Active sessions
    use the standard TTL (_SESSION_MAX_AGE_DAYS, default 30 days).
    The presence of a later session_end does NOT downgrade a paused
    session to the active TTL — this closes the pause→quit race and
    the equal-timestamp tie that previously caused silent data loss.

    Note: _set_age() must be called AFTER writing journal files, because
    writing into a directory updates its mtime on Unix/macOS.
    """

    def _create_session_dir(self, slug_dir, session_id):
        """Helper: create a session directory (without setting age)."""
        session_dir = slug_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "pact-session-context.json").write_text("{}")
        return session_dir

    def _set_age(self, session_dir, age_days):
        """Set directory mtime to simulate age. Call AFTER writing all files."""
        import os as _os
        import time as _time
        old_time = _time.time() - (age_days * 86400)
        _backdate(session_dir, old_time)

    def _write_journal(self, session_dir, events):
        """Helper: write events to a session's journal."""
        journal = session_dir / "session-journal.jsonl"
        lines = [json.dumps(e) + "\n" for e in events]
        journal.write_text("".join(lines))

    def test_preserves_paused_session_beyond_ttl(self, tmp_path):
        """Scenario 9: Paused session (no session_end) survives cleanup."""
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        paused_id = "11111111-2222-3333-4444-555555555555"

        self._create_session_dir(slug_dir, current_id)
        paused_dir = self._create_session_dir(slug_dir, paused_id)
        self._write_journal(paused_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 42, "ts": "2026-01-01T01:00:00Z"},
        ])
        self._set_age(paused_dir, 35)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        # Paused session must survive despite being 35 days old
        assert paused_dir.exists()


# =============================================================================
# TTL Constant Verification — 30 Days Default
# =============================================================================

class TestTTLDefault:
    """Verify the 30-day default TTL constant (Scenario 8)."""

    def test_session_max_age_days_is_30(self):
        """The default TTL constant should be 30 days (changed from 7)."""
        from session_end import _SESSION_MAX_AGE_DAYS

        assert _SESSION_MAX_AGE_DAYS == 30


# =============================================================================
# cleanup_old_teams() Tests — #412 Fix B
# =============================================================================


def _make_team_dir(parent, name, age_days=0):
    """Create a team directory under parent with controlled mtime.

    Writes config.json (typical team fixture) and sets mtime on BOTH the
    config.json child AND the parent dir to `age_days` old. The teams
    reaper now uses max-child-mtime via `_dir_max_child_mtime(glob="*")`
    (cycle-4 fix for POSIX in-place-overwrite semantics: parent-dir
    mtime is NOT bumped when config.json is rewritten in place, only on
    create/unlink/rename). Honestly aging the child makes the fixture
    model the new reaper's actual probe. Parent-dir mtime is kept aged
    too as belt-and-suspenders — if a future reaper reverts to parent
    probe, tests still represent the intended age.
    """
    import os as _os
    import time as _time
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = d / "config.json"
    cfg.write_text("{}")
    if age_days > 0:
        old = _time.time() - (age_days * 86400)
        _os.utime(str(cfg), (old, old))
        _os.utime(str(d), (old, old))
    return d


def _make_task_dir(parent, name, child_ages_days=None, parent_age_days=None):
    """Create a tasks/{name}/ directory with per-child *.json mtimes.

    child_ages_days: list of ages (days) for child .json files; one file
    per entry (1.json, 2.json, ...). Pass [] for an empty dir.
    parent_age_days: if set, force parent dir mtime AFTER children are
    written (writing a child refreshes the parent on Unix).
    """
    import os as _os
    import time as _time
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    if child_ages_days:
        for idx, age in enumerate(child_ages_days, start=1):
            f = d / f"{idx}.json"
            f.write_text("{}")
            if age > 0:
                old = _time.time() - (age * 86400)
                _os.utime(str(f), (old, old))
    if parent_age_days is not None:
        old = _time.time() - (parent_age_days * 86400)
        _os.utime(str(d), (old, old))
    return d


class TestCleanupOldTeams:
    """Tests for session_end.cleanup_old_teams() — #412 Fix B."""

    def test_reaps_old_team_dirs_excluding_current(self, tmp_path):
        """Old PACT-shaped sibling team dirs reap; current team_name entry
        preserved even when older than TTL; non-PACT-shaped names
        preserved by `_TEAM_NAME_PATTERN` gate (cycle-4 defense layer)."""
        from session_end import cleanup_old_teams

        current = "pact-abcd1234"
        _make_team_dir(tmp_path, current, age_days=60)  # old but current
        _make_team_dir(tmp_path, "pact-deadbeef", age_days=40)  # REAPED (PACT-shaped)
        # UUID-shaped name: cycle-4 `_TEAM_NAME_PATTERN = ^pact-[a-f0-9-]+$`
        # rejects this even when old. Pre-cycle-4 this was reaped; the new
        # defense layer treats ~/.claude/teams/ as shared space.
        _make_team_dir(tmp_path, "43a2f95a-1111-2222-3333-444444444444", age_days=40)

        reaped, skipped = cleanup_old_teams(
            current_team_name=current,
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1, "only the PACT-shaped old dir should reap"
        assert skipped == 0
        assert (tmp_path / current).exists()
        assert not (tmp_path / "pact-deadbeef").exists()
        # Non-PACT-shaped UUID dir is preserved by the pattern gate even
        # though its mtime is older than TTL. Preservation is the *point*
        # of the gate: teams/ is shared space, not PACT-owned space.
        assert (tmp_path / "43a2f95a-1111-2222-3333-444444444444").exists()


# =============================================================================
# cleanup_old_tasks() Tests — #412 Fix B
# =============================================================================


class TestCleanupOldTasks:
    """Tests for session_end.cleanup_old_tasks() — #412 Fix B."""

    def test_reaps_via_max_child_mtime(self, tmp_path):
        """Dir with all-old children reaps; dir with one fresh child preserved.

        Pins the asymmetric probe — platform TaskUpdate rewrites child .json
        without touching parent dir's mtime, so max-child is the tight bound.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_tasks

        # Old dir: force parent mtime fresh but children all old — max-child
        # must win over parent mtime here.
        old = _make_task_dir(tmp_path, "pact-old", child_ages_days=[40, 45])
        # Refresh parent mtime to "now" so test proves children drive decision.
        _os.utime(str(old), (_time.time(), _time.time()))

        # Mixed dir: one old child + one fresh — max-child is fresh → preserved.
        mixed = _make_task_dir(tmp_path, "pact-mixed", child_ages_days=[40, 0])
        # Force old parent mtime to prove children override parent freshness.
        old_parent = _time.time() - (50 * 86400)
        _os.utime(str(mixed), (old_parent, old_parent))

        reaped, skipped = cleanup_old_tasks(
            skip_names={"pact-current"},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1
        assert skipped == 0
        assert not (tmp_path / "pact-old").exists()
        assert (tmp_path / "pact-mixed").exists()


# =============================================================================
# cleanup_summary journal event — #412 Fix B
# =============================================================================


class TestCleanupSummaryEvent:
    """main()-level integration: cleanup_summary journal event shape & emission."""

    def _run_main_with_reapers(self, *, team_return, env_task_list_id=""):
        """Helper: run main() with real reapers patched to return (r, s) tuples.

        Returns list of append_event call args for inspection.
        """
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io

        captured = []

        def record(event):
            captured.append(event)

        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch.dict("os.environ", {"CLAUDE_CODE_TASK_LIST_ID": env_task_list_id}, clear=False),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_id", return_value="sess-id"),
            patch("session_end.get_team_name", return_value=team_return),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(3, 1)),
            patch("session_end.cleanup_old_tasks", return_value=(2, 0)),
            patch("session_end.append_event", side_effect=record),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            from session_end import main
            with pytest.raises(SystemExit):
                main()
        return captured

    def test_cleanup_summary_event_shape_when_reaper_runs(self):
        """main() emits cleanup_summary with all 5 fields populated from reaper returns."""
        events = self._run_main_with_reapers(team_return="pact-current")

        summaries = [e for e in events if e["type"] == "cleanup_summary"]
        assert len(summaries) == 1
        s = summaries[0]
        assert s["teams_reaped"] == 3
        assert s["teams_skipped"] == 1
        assert s["tasks_reaped"] == 2
        assert s["tasks_skipped"] == 0
        # Cycle-8 split: single `ttl_days` replaced by per-reaper fields.
        assert s["teams_ttl_days"] == 30
        assert s["tasks_ttl_days"] == 30


# =============================================================================
# _assemble_tasks_skip_set — cycle-8 extracted helper (direct unit test)
# =============================================================================


class TestAssembleTasksSkipSet:
    """Cycle-8 Test 2 — direct unit test of the module-level helper.

    Extracted from `main()` in cycle-8 (Architect M3). Takes only
    primitives, returns a deterministic `set[str]` — no session-context
    mocking needed. Coverage: happy path (all 3 channels pass), one
    channel rejected by allowlist, all rejected, empty-string inputs
    pruned.

    No counter-test-by-revert needed: the helper IS the system under
    test; behavioral contract is the assertion target.
    """

    def test_happy_path_all_three_channels_pass(self):
        """All 3 non-empty allowlist-safe inputs populate skip_names."""
        from session_end import _assemble_tasks_skip_set

        result = _assemble_tasks_skip_set(
            team_name="pact-0001639f",
            task_list_id="task-list-abc",
            session_id="5ddd5636-d408-4892",
        )

        assert result == {"pact-0001639f", "task-list-abc", "5ddd5636-d408-4892"}


# =============================================================================
# main() wiring for reapers — #412 Fix B
# =============================================================================


class TestMainReaperWiring:
    """main()-level wiring guards for the new reapers."""

    def _base_patches(self, *, team_return="pact-current", session_id="sess-id", env=None):
        from unittest.mock import patch
        import io as _io
        env = env or {}
        return [
            patch("sys.stdin", _io.StringIO("{}")),
            patch.dict("os.environ", env, clear=False),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_id", return_value=session_id),
            patch("session_end.get_team_name", return_value=team_return),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.append_event"),
        ]

    def test_main_skips_team_reaper_on_empty_team_name(self):
        """Callsite short-circuit: empty team_name → cleanup_old_teams NOT invoked.

        Belt-and-suspenders layer around the internal guard; this test
        pins the short-circuit specifically (the internal guard already
        returns (0,0) but we must not even call it when we know better).
        """
        from unittest.mock import patch
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in self._base_patches(team_return=""):
                stack.enter_context(p)
            mock_teams = stack.enter_context(
                patch("session_end.cleanup_old_teams", return_value=(0, 0))
            )
            mock_tasks = stack.enter_context(
                patch("session_end.cleanup_old_tasks", return_value=(0, 0))
            )
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        mock_teams.assert_not_called()
        # tasks reaper still runs because session_id alone is a valid skip member
        mock_tasks.assert_called_once()


# =============================================================================
# Reaper outer-try regression — #412 Fix B
# =============================================================================


class TestReaperOuterTryExcept:
    """Outer try/except in each reaper must absorb catastrophic OSError."""

    def test_cleanup_old_teams_iterdir_oserror_absorbed(self, tmp_path):
        """iterdir() raising OSError at outer level → return current counts, no raise."""
        from unittest.mock import patch as _patch
        from pathlib import Path as _Path
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)

        real_iterdir = _Path.iterdir

        def flaky_iterdir(self):
            if str(self) == str(tmp_path):
                raise OSError("EACCES on base")
            return real_iterdir(self)

        with _patch.object(_Path, "iterdir", flaky_iterdir):
            reaped, skipped = cleanup_old_teams(
                current_team_name="pact-current",
                teams_base_dir=str(tmp_path),
                max_age_days=30,
            )

        assert (reaped, skipped) == (0, 0)


# =============================================================================
# Regression guard — cleanup_old_sessions unchanged — #412 Fix B
# =============================================================================


class TestCleanupOldSessionsUnchangedRegression:
    """Delta guard: adding the new reapers must not alter cleanup_old_sessions.

    Catches accidental edits to the parent reaper while the sibling
    reapers are being added.
    """

    def test_parent_reaper_still_reaps_uuid_sibling(self, tmp_path):
        """Smoke regression: basic UUID reap behavior intact."""
        import os as _os
        import time as _time
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "proj"
        current = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        old = "11111111-2222-3333-4444-555555555555"
        for sid in (current, old):
            d = slug_dir / sid
            d.mkdir(parents=True)
            (d / "ctx.json").write_text("{}")
        old_time = _time.time() - (40 * 86400)
        _backdate(slug_dir / old, old_time)

        cleanup_old_sessions(
            project_slug="proj",
            current_session_id=current,
            sessions_dir=str(tmp_path),
            max_age_days=30,
        )

        assert (slug_dir / current).exists()
        assert not (slug_dir / old).exists()


# =============================================================================
# Cycle-1 remediation pins (No-Narration/Raw-Read-Metadata/Emit-Nothing-If-Empty/G6/G7) — #412 Fix B
# =============================================================================


class TestReaperBehaviorPins:
    """Pin load-bearing behaviors that prior coverage left implicit.

    Each test protects an invariant against silent-refactor drift. Every
    test docstring names the specific refactor it would catch.
    """

    # No-Narration — TTL boundary semantics
    def test_ttl_boundary_29d_survives_30d_reaps(self, tmp_path):
        """29d-aged survives; 30d-aged reaps — pins effective `>= ~30d reaps`.

        Source uses `age_days > max_age_days` (strict). Because
        `time.time()` advances between utime() and the reaper's own
        wall-clock read, a dir utime'd to `now - 30*86400` has effective
        age 30.0000...ns > 30 and reaps. Two asymmetric assertions pin
        the practical boundary; flipping `>` to `>=` would pass the `30d
        reaps` half and fail the `29d survives` half — and vice versa for
        regressions that relax the comparison. Catches any future drift.
        """
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        # 29d survives
        _make_team_dir(tmp_path, "pact-29d", age_days=29)
        # 30d reaps (practical: time.time() advances → age > 30)
        _make_team_dir(tmp_path, "pact-30d", age_days=30)

        reaped, skipped = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert skipped == 0
        assert (tmp_path / "pact-29d").exists(), "29d must survive (age < TTL)"
        assert not (tmp_path / "pact-30d").exists(), (
            "30d must reap (effective age is 30.0 + epsilon due to "
            "time.time() drift between utime and reaper read)"
        )
        assert reaped == 1

    # Raw-Read-Metadata — _task_dir_mtime glob scope

    # Emit-Nothing-If-Empty — main() reaper → cleanup_summary emission ordering

    # G6 — skip-set exact-match semantics

    # G7 — hostile/pathological team names survive traversal


# =============================================================================
# Cycle-2 remediation: M3 CLAUDE_CODE_TASK_LIST_ID allowlist rejection pin
# =============================================================================


class TestTaskListIdAllowlistRejection:
    """Pins `re.fullmatch(r"[A-Za-z0-9_-]+", ...)` guard at session_end.py:642-644.

    The cycle-1 happy-path test (`test_main_assembles_union_skip_set`)
    covers a well-formed `task-C` passing through to skip_names. But it
    doesn't cover the REJECTION path — if someone deletes the allowlist
    line, hostile env values would silently enter skip_names and could
    shield malicious entries in `~/.claude/tasks/` from reaping.

    This class provides the counter-test pin: each hostile value is
    asserted NOT to appear in the skip_names passed to cleanup_old_tasks.
    Counter-test-by-revert confirmed: removing the allowlist line makes
    these tests fail (hostile values leak into skip_names).
    """

    @pytest.mark.parametrize("hostile_value", [
        "../etc",])
    def test_hostile_task_list_id_excluded_from_skip_names(self, hostile_value):
        """Each hostile CLAUDE_CODE_TASK_LIST_ID must be filtered out.

        Invokes main() with a hostile env value. Asserts the skip_names
        kwarg passed to cleanup_old_tasks does NOT contain the hostile
        string. The other skip members (team_name, session_id) are
        fixed non-empty so cleanup_old_tasks always runs.

        Uses `patch("os.environ.get", ...)` rather than
        `patch.dict("os.environ", ...)` because `os.environ` rejects
        embedded null bytes at the OS API layer — but the in-process
        allowlist is the defense-in-depth layer we're pinning here, so
        we simulate the env read directly.
        """
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io
        import os as _os

        real_env_get = _os.environ.get

        def fake_env_get(key, default=None):
            if key == "CLAUDE_CODE_TASK_LIST_ID":
                return hostile_value
            return real_env_get(key, default)

        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch("session_end.os.environ.get", side_effect=fake_env_get),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_id", return_value="sess-B"),
            patch("session_end.get_team_name", return_value="team-A"),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            patch("session_end.append_event"),
        ]
        mock_tasks_ref = {}
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            mock_tasks_ref["m"] = stack.enter_context(
                patch("session_end.cleanup_old_tasks", return_value=(0, 0))
            )
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        mock_tasks = mock_tasks_ref["m"]
        mock_tasks.assert_called_once()
        skip_names = mock_tasks.call_args.kwargs["skip_names"]

        assert hostile_value not in skip_names, (
            f"Hostile CLAUDE_CODE_TASK_LIST_ID={hostile_value!r} leaked into "
            f"skip_names={skip_names!r}. Regex allowlist at "
            f"session_end.py:642-644 must be filtering it."
        )
        # Sanity: the other two skip members still pass through.
        assert "team-A" in skip_names
        assert "sess-B" in skip_names


# =============================================================================
# Cycle-3 remediation: F2 inner symlink mtime oracle pin — #412 Fix B
# =============================================================================


class TestTaskDirMtimeInnerSymlink:
    """Pins `child.stat(follow_symlinks=False)` at session_end.py:384.

    `_task_dir_mtime` iterates each `*.json` child to compute the tight
    max-mtime used as the dir's reap decision. If `stat()` FOLLOWED
    symlinks, an attacker who plants `tasks/{real-dir}/x.json` as a
    symlink to an external file could force the reaper's decision to
    reflect the TARGET's mtime rather than the LINK's — an oracle leak
    (probe "is /etc/passwd fresh?") and a potential reap-suppression
    vector (point at a file that's always "touched" to keep the dir
    alive past its TTL).

    The `follow_symlinks=False` flag makes `stat()` use lstat semantics
    so the LINK's own mtime drives the decision, not the target's.
    These two tests pin that invariant asymmetrically: removing the
    flag flips both tests to fail (counter-test-by-revert verified).
    """

    def test_old_link_with_fresh_target_causes_reap(self, tmp_path):
        """Oracle-suppression scenario: old link, fresh target → dir reaps.

        With `follow_symlinks=False`, probe sees link lstat (old) →
        max-child is old → dir reaped. Without the flag, probe follows
        to target (fresh) → max-child is fresh → dir preserved
        (attacker wins: the planted symlink suppresses reap).
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_tasks

        # External target with FRESH mtime (attacker-chosen probe target).
        target = tmp_path / "external-target.json"
        target.write_text("{}")
        _os.utime(str(target), (_time.time(), _time.time()))

        # tasks subdir with single symlinked `.json` child whose LINK
        # mtime is OLD (40d). lstat must win.
        d = tmp_path / "pact-planted"
        d.mkdir()
        link = d / "1.json"
        link.symlink_to(target)
        old = _time.time() - (40 * 86400)
        _os.utime(str(link), (old, old), follow_symlinks=False)
        # Force parent dir mtime old too so fallback wouldn't mask the
        # intended decision path.
        _os.utime(str(d), (old, old))

        reaped, _ = cleanup_old_tasks(
            skip_names={"pact-current"},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        # Link-mtime (40d) > TTL → reap. If target (fresh) were used
        # instead, this would return 0 and the test would fail.
        assert reaped == 1, (
            "Old-link-with-fresh-target scenario must reap — link lstat "
            "mtime (40d) should win over target stat mtime (fresh). "
            "If this fails, `follow_symlinks=False` was likely removed "
            "from session_end.py:384."
        )
        assert not d.exists()
        # Target must survive (rmtree on the parent dir unlinks the
        # symlink entry but doesn't recurse through it).
        assert target.exists()


# =============================================================================
# Cycle-1 remediation: symlink pinning (②) — #412 Fix B
# =============================================================================


class TestReaperSymlinkHandling:
    """Symlinks under the reaper base-dirs must be SKIPPED entirely.

    Counter-test-by-revert target: remove the `if entry.is_symlink():
    continue` guard from any of the three reapers and exactly one of
    these tests fails. See HANDOFF evidence.
    """

    def _aged_target(self, tmp_path, name, age_days=60):
        """Create an aged-target directory OUTSIDE the reaper base-dir.

        Placed as a sibling of tmp_path so the reaper's iterdir scan
        cannot see the target itself — only the symlink that points to
        it. Uses `tmp_path.parent / (tmp_path.name + suffix)` so the
        pytest fixture still cleans it up.

        Ages ALL children AND the parent dir to `age_days` old. Both a
        `*.json` child (load-bearing for the tasks-reaper path, which
        globs `*.json`) AND a non-json `precious.txt` (for the teams
        path, which globs `*`) are aged. Aging the children is load-
        bearing for guard-sensitivity (PR #433 cycle-7 F2 remediation):
        without it, `_dir_max_child_mtime` sees a fresh child (write_text
        stamps current time) and the symlink guard's removal is masked —
        the dir would be preserved for the WRONG reason (fresh child,
        not guard).
        """
        import os as _os
        import time as _time
        victim = tmp_path.parent / f"{tmp_path.name}-victim-{name}"
        victim.mkdir(exist_ok=True)
        precious = victim / "precious.txt"
        precious.write_text("user data that must survive")
        # Also drop an aged `*.json` child so the tasks-reaper probe
        # (glob="*.json") returns the aged mtime rather than falling
        # back to parent lstat on the symlink (which is fresh).
        json_child = victim / "payload.json"
        json_child.write_text("{}")
        old = _time.time() - (age_days * 86400)
        _os.utime(str(precious), (old, old))
        _os.utime(str(json_child), (old, old))
        _os.utime(str(victim), (old, old))
        return victim

    def test_cleanup_old_teams_skips_symlinks(self, tmp_path):
        """Symlink in ~/.claude/teams/ is SKIPPED — target preserved, link preserved.

        Without the is_symlink guard, is_dir() follows the link, stat
        reads the target's (ancient) mtime, and rmtree unlinks the link
        entry. The guard short-circuits BEFORE is_dir so the symlink
        isn't even considered a candidate.
        """
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        # Hex-only name — cycle-4 pattern gate `^pact-[a-f0-9-]+$` rejects
        # letters outside [a-f]. BOTH the real old dir AND the symlink
        # name must be hex-valid, otherwise the pattern gate filters them
        # before either the is_symlink guard or the TTL probe runs —
        # which would mask the guard's sensitivity (PR #433 cycle-7 F2).
        _make_team_dir(tmp_path, "pact-0dd0eaf1", age_days=40)
        victim = self._aged_target(tmp_path, "teams")
        # "deadbeef" is all-hex so the pattern gate admits the symlink.
        link = tmp_path / "pact-deadbeef"
        link.symlink_to(victim)

        try:
            reaped, skipped = cleanup_old_teams(
                current_team_name="pact-current",
                teams_base_dir=str(tmp_path),
                max_age_days=30,
            )

            # Real old dir reaped; symlink NOT counted as reaped (guard skipped it).
            assert reaped == 1, f"expected 1 real reap, got {reaped}"
            assert skipped == 0
            assert link.is_symlink(), "symlink itself must survive (not rmtree'd)"
            assert victim.exists(), "target must survive"
            assert (victim / "precious.txt").exists(), "target contents must survive"
            assert not (tmp_path / "pact-0dd0eaf1").exists(), "real old dir reaped"
        finally:
            import shutil as _shutil
            if link.is_symlink():
                link.unlink()
            _shutil.rmtree(victim, ignore_errors=True)


# =============================================================================
# Cycle-1 remediation: teams_ran/tasks_ran bools — #412 Fix B M6-gap closure
# =============================================================================


class TestCleanupSummaryReaperRan:
    """`teams_ran`/`tasks_ran` discriminate "ran, found nothing" from
    "short-circuited" per side.

    Without these bools, a (0,0,0,0) counts row is ambiguous: did both
    reapers run and find nothing, or did either side short-circuit at
    the callsite guard? Cycle-8 splits the single `reaper_ran` bool
    into per-reaper bools so an auditor can tell WHICH side ran.
    """

    def _run_with(self, *, team_return, session_id="sess-id", env=None):
        """Run main() with real reapers no-op'd; record cleanup_summary event."""
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io

        env = env or {}
        captured = []

        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch.dict("os.environ", env, clear=False),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_id", return_value=session_id),
            patch("session_end.get_team_name", return_value=team_return),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            patch("session_end.cleanup_old_tasks", return_value=(0, 0)),
            patch("session_end.append_event", side_effect=lambda e: captured.append(e)),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        summaries = [e for e in captured if e["type"] == "cleanup_summary"]
        assert len(summaries) == 1, f"expected 1 cleanup_summary, got {len(summaries)}"
        return summaries[0]

    def test_only_teams_ran_when_team_name_only_skip_channel(self):
        """team_name set (non-allowlist-safe) → teams runs, tasks short-circuits.

        Cycle-8 un-skips the previously-unreachable "only teams ran"
        row. With the new `_assemble_tasks_skip_set` helper that runs
        `is_safe_path_component` on team_name, a deliberately-unsafe
        team_name drops from skip_names — so skip_names = empty (all
        three channels rejected) → tasks reaper short-circuits on
        `if skip_names:`. Meanwhile the teams-reaper callsite check is
        `if current_team_name:` (truthy-only, no allowlist) so it still
        runs. This is the reachable "only teams ran" row.

        In practice this requires a team_name that fails the allowlist.
        Construct with `pact/weird` which has a slash.
        """
        ev = self._run_with(
            team_return="pact/weird",  # fails is_safe_path_component → drops from skip_names
            session_id="",
            env={"CLAUDE_CODE_TASK_LIST_ID": ""},
        )
        assert ev["teams_ran"] is True
        assert ev["tasks_ran"] is False


# =============================================================================
# Cycle-4 remediation: teams child-mtime + name-shape gate pins — #412 Fix B
# =============================================================================


def _touch_child(child_path, age_days):
    """Set mtime on a file (or symlink via lstat) to `age_days` ago."""
    import os as _os
    import time as _time
    old = _time.time() - (age_days * 86400)
    _os.utime(str(child_path), (old, old))


class TestTeamsChildMtimeProbe:
    """Cycle-4 Test 1 — teams reaper walks child mtimes, not parent mtime.

    Post-cycle-4, cleanup_old_teams invokes `_dir_max_child_mtime(entry,
    glob="*")`. This pins the invariant that a fresh child inside an
    old-parent team dir preserves the dir (because the child-walk
    dominates the parent's mtime).

    Why load-bearing: POSIX in-place overwrite of `config.json` does NOT
    bump the parent dir's mtime. A reaper that read parent-dir mtime
    alone would false-reap live teams whose config.json was recently
    rewritten without rename/unlink. Max-child-mtime is the tight upper
    bound.
    """

    def test_fresh_child_preserves_old_parent_team_dir(self, tmp_path):
        """Old parent mtime + fresh child → dir PRESERVED (child walk wins).

        Construction mirrors the platform shape: parent dir mtime 40d old
        (simulates a team dir that hasn't had members added/removed
        recently), but `config.json` inside is fresh (simulates a recent
        in-place rewrite). The reaper must read the child and preserve.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        current = "pact-current"
        _make_team_dir(tmp_path, current, age_days=0)

        # Stale-parent-but-fresh-child team dir.
        # Name must match _TEAM_NAME_PATTERN=^pact-[a-f0-9-]+$ (hex only).
        target = tmp_path / "pact-abcd1234"
        target.mkdir()
        config = target / "config.json"
        config.write_text('{"members": []}')
        # Force fresh child (just in case utime defaults differ).
        _os.utime(str(config), (_time.time(), _time.time()))
        # Force aged parent dir mtime AFTER the child write.
        old = _time.time() - (40 * 86400)
        _os.utime(str(target), (old, old))

        reaped, skipped = cleanup_old_teams(
            current_team_name=current,
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert skipped == 0
        assert reaped == 0, (
            "Fresh child inside aged-parent team dir must PRESERVE the dir. "
            "If this test shows reaped == 1, the reaper is probing parent "
            "mtime instead of max-child mtime — likely regression of "
            "_dir_max_child_mtime back to direct entry.stat()."
        )
        assert target.exists()


class TestTeamNameShapeGate:
    """Cycle-4 Test 2 — _TEAM_NAME_PATTERN gate preserves non-PACT dirs.

    Post-cycle-4, `cleanup_old_teams` filters entries through
    `_TEAM_NAME_PATTERN = r"^pact-[a-f0-9-]+$"` before considering them
    for age-check. This treats `~/.claude/teams/` as shared space —
    non-PACT tooling that creates team dirs under that path is protected
    from reaping.
    """

    def test_non_pact_name_preserved_even_when_old(self, tmp_path):
        """Old `foo-bar/` and `pact-XYZ/` (uppercase) dirs PRESERVED.

        The gate rejects on the POSITIVE allowlist: only `^pact-[a-f0-9-]+$`
        passes. Uppercase hex, leading-capital names, and missing prefix
        all filtered out. Load-bearing: without the gate, a third-party
        tool's ~/.claude/teams/myapp/ would be reaped.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)

        non_pact_names = [
            "foo-bar",                    # no pact- prefix
            "pact-UPPERCASE",              # uppercase (non-hex)
            "PACT-lowerhex",               # uppercase prefix
            "myapp",                       # bare name
            "pact",                        # prefix-without-hyphen
            "pact_underscore",             # underscore, not hyphen
        ]
        for name in non_pact_names:
            d = tmp_path / name
            d.mkdir()
            (d / "config.json").write_text("{}")
            old = _time.time() - (40 * 86400)
            _os.utime(str(d / "config.json"), (old, old))
            _os.utime(str(d), (old, old))

        reaped, _ = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0, (
            f"Non-PACT-shaped team dirs must be preserved by the "
            f"_TEAM_NAME_PATTERN gate. If this shows reaped > 0, the "
            f"gate was likely removed or loosened."
        )
        for name in non_pact_names:
            assert (tmp_path / name).exists(), f"{name} should survive"


class TestTeamNameRegexStrictAnchor:
    """Cycle-7 N2 — `_TEAM_NAME_PATTERN` uses `\\Z` (strict end-of-string).

    Python `re` treats `$` as end-of-string OR immediately before a
    trailing newline. Without `\\Z`, a crafted team dir name like
    `pact-deadbeef\\n` would PASS the gate and land in the skip / reap
    eligibility path.  `\\Z` anchors strictly and rejects trailing
    newlines. Bounded today because `generate_team_name` never produces
    such a name, but a same-user attacker or a filesystem tool that
    creates a dir like this could bypass the invariant.

    POSIX permits `\\n` in filenames. macOS APFS was empirically verified
    to accept `mkdir("pact-deadbeef\\n")` and round-trip the literal name
    through `iterdir()`.
    """

    def test_trailing_newline_name_rejected_by_strict_anchor(self, tmp_path):
        """Dir named `pact-deadbeef\\n` is PRESERVED by the `\\Z` gate.

        COUNTER-TEST BY REVERT target: switching `\\Z` back to `$` in
        `_TEAM_NAME_PATTERN` flips this test — the newline-suffixed name
        matches under `$` and the dir gets reaped. Evidence (documented
        in HANDOFF):
          - With `\\Z`: `pact-deadbeef\\n` rejected by gate → preserved.
          - With `$`:   `pact-deadbeef\\n` matches → dir reaped.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)

        # Trailing-newline name. POSIX allows; macOS APFS verified.
        hostile = "pact-deadbeef\n"
        d = tmp_path / hostile
        try:
            d.mkdir()
        except OSError as e:
            pytest.skip(
                f"Filesystem rejects `\\n` in directory names ({e!r}); "
                f"strict-anchor pin cannot be exercised here"
            )
        (d / "config.json").write_text("{}")
        old = _time.time() - (40 * 86400)
        _os.utime(str(d / "config.json"), (old, old))
        _os.utime(str(d), (old, old))

        reaped, skipped = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0, (
            "Trailing-newline name must be REJECTED by `\\Z` strict "
            "anchor. If reaped == 1, the regex was likely reverted from "
            "`\\Z` to `$` (which matches end-of-string OR immediately "
            "before a trailing newline)."
        )
        assert d.exists(), "hostile-named dir must survive the pattern gate"


class TestTaskDirMtimeLstatPortability:
    """Cycle-4 Test 3 — child.lstat() matches prior stat(follow_symlinks=False).

    Cycle-4 changed `child.stat(follow_symlinks=False)` to `child.lstat()`
    for Python pre-3.10 portability. Semantics are identical: both
    return the link's own mtime without dereferencing. This pin confirms
    the lstat form preserves the oracle-suppression defense.

    Existing TestTaskDirMtimeInnerSymlink (cycle-3) already pins the
    defense behaviorally — if those tests still pass after the cycle-4
    rename, the contract holds. This class adds ONE symmetric test
    directly against the generalized helper to pin the lstat call-form.
    """

    def test_lstat_returns_link_mtime_not_target_mtime(self, tmp_path):
        """Direct probe: _dir_max_child_mtime on a dir with old-link-fresh-target.

        Invokes `_dir_max_child_mtime` directly (cycle-8 removed the
        `_task_dir_mtime` back-compat wrapper; callers now use the
        generalized helper with an explicit glob). The probe MUST return
        the link's lstat mtime (old), not the target's stat mtime
        (fresh). If `lstat()` were reverted to `stat()`, the returned
        value would be fresh and this test fails.
        """
        import os as _os
        import time as _time
        from session_end import _dir_max_child_mtime

        # Fresh external target
        target = tmp_path / "external-target.json"
        target.write_text("{}")
        _os.utime(str(target), (_time.time(), _time.time()))

        # tasks dir with OLD symlink-child pointing to FRESH target
        d = tmp_path / "pact-probe"
        d.mkdir()
        link = d / "1.json"
        link.symlink_to(target)
        old = _time.time() - (40 * 86400)
        _os.utime(str(link), (old, old), follow_symlinks=False)

        result = _dir_max_child_mtime(d, glob="*.json")

        # Must be ~40d old (link lstat mtime), NOT fresh (target stat mtime).
        age_days = (_time.time() - result) / 86400
        assert age_days > 30, (
            f"_dir_max_child_mtime returned {age_days:.1f}d old — expected >30d "
            f"(link lstat mtime). If this test fails with a fresh (<1d) "
            f"result, lstat() was reverted to stat() (follow_symlinks=True)."
        )


# =============================================================================
# Cycle-5 defensive-hardening pins — #412 Fix B (cycle-5 contract)
# =============================================================================


class TestTeamsCaseInsensitiveSkip:
    """Cycle-5 Test 1 — `cleanup_old_teams` skip uses case-insensitive compare.

    `pact_context.get_team_name()` returns a lowercased name and the
    `generate_team_name` INVARIANT pins lowercase, so byte-exact compare
    is correct-by-coincidence today. Cycle-5 changed the comparison to
    `entry.name.lower() == current_team_name.lower()` as belt-and-
    suspenders against future drift in either producer.
    """

    def test_mixed_case_team_dir_preserved_when_caller_passes_lowercase(self, tmp_path):
        """Mixed-case dir on disk + lowercase current_team_name → PRESERVED.

        Construct a team dir whose on-disk name is `pact-AABB1122` (mixed
        case, all hex-valid). Pass `pact-aabb1122` (lowercase) as
        current_team_name. Under case-insensitive compare, the dir is
        SKIPPED (preserved). Under byte-exact compare, the dir would be
        treated as a sibling and reaped.

        Note: the directory name must still pass `_TEAM_NAME_PATTERN`
        which is lowercase-only `^pact-[a-f0-9-]+$`. Mixed-case
        normally wouldn't match — but on case-insensitive filesystems
        (macOS HFS+/APFS-default), `mkdir("pact-AABB1122")` creates a
        directory whose `iterdir()` may return either the literal
        `"pact-AABB1122"` or the canonical lowercased form. We force
        the test fixture to write a directory whose name preserves
        case. If iterdir returns lowercase, the pattern gate accepts;
        if mixed case, the pattern gate rejects regardless of compare.
        Either way, this test pins the COMPARE semantic; the pattern
        gate is orthogonal.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        # Use ALL-LOWERCASE on-disk name (so pattern gate accepts) but
        # pass a different-case current_team_name. The semantic we're
        # pinning: skip predicate is case-insensitive in the COMPARE
        # direction (caller-provided value can differ in case).
        ondisk = "pact-aabb1122"
        d = tmp_path / ondisk
        d.mkdir()
        (d / "config.json").write_text("{}")
        old = _time.time() - (40 * 86400)
        _os.utime(str(d / "config.json"), (old, old))
        _os.utime(str(d), (old, old))

        # Pass mixed-case current_team_name. With .lower()==.lower(),
        # this must skip the on-disk dir.
        reaped, skipped = cleanup_old_teams(
            current_team_name="PACT-AABB1122",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0, (
            "Mixed-case current_team_name must skip lowercase on-disk "
            "match via .lower() compare. If reaped == 1, the compare "
            "regressed to byte-exact (==)."
        )
        assert d.exists()


class TestTasksByteExactSkip:
    """Cycle-8 Test 6 — `cleanup_old_tasks` skip uses byte-exact (in)
    membership, NOT case-insensitive compare.

    Gap identified in PR #433 blind review (MED Read-Filesystem-Only): cycle-5 added
    `.lower()==.lower()` to teams-reaper skip (TestTeamsCaseInsensitiveSkip
    pins it). tasks-reaper intentionally uses byte-exact `entry.name in
    skip_names`. Without a pin on the tasks side, a future reviewer could
    add `.lower()` "for consistency" and no test would catch the
    semantic change.

    Asymmetric partner shape: a mixed-case on-disk dir + lowercase
    skip_names. Under byte-exact, they do NOT match → dir reaps. Under
    `.lower()` (the regression we're guarding against), they WOULD match
    → dir spuriously shielded.
    """

    def test_mixed_case_task_dir_reaps_despite_lowercase_skip_name(self, tmp_path):
        """On-disk `Pact-AABB1122` is REAPED when skip_names={`pact-aabb1122`}.

        COUNTER-TEST BY REVERT target: adding `.lower()` symmetry to the
        tasks-side compare (e.g. `entry.name.lower() in
        {s.lower() for s in skip_names}`) flips this — the mixed-case
        on-disk dir would be treated as a skip match and preserved.
        Byte-exact semantic REAPS it (the dir name literally differs
        byte-for-byte from the skip key).
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_tasks

        # Mixed-case on-disk name. The tasks reaper has no pattern gate
        # (tasks/ allows arbitrary id shapes — uuid, hex, mixed-case),
        # so this name is ADMITTED for TTL consideration.
        ondisk = "Pact-AABB1122"
        d = tmp_path / ondisk
        d.mkdir()
        (d / "1.json").write_text("{}")
        old = _time.time() - (40 * 86400)
        _os.utime(str(d / "1.json"), (old, old))
        _os.utime(str(d), (old, old))

        # Lowercase skip_name. Under byte-exact `in`, "Pact-AABB1122" is
        # NOT in {"pact-aabb1122"} → dir is NOT skipped → reap path.
        reaped, skipped = cleanup_old_tasks(
            skip_names={"pact-aabb1122"},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1, (
            "Mixed-case on-disk name must REAP despite lowercase skip. "
            "If reaped == 0, the tasks-side skip was likely refactored to "
            "case-insensitive (e.g. `entry.name.lower() in "
            "{s.lower() for s in skip_names}`) — the byte-exact semantic "
            "is LOAD-BEARING (preserves asymmetric-trust model with "
            "teams-side case-insensitive defense)."
        )
        assert not d.exists()


class TestDirMaxChildMtimeFallbackLstat:
    """Cycle-5 Test 2 — `_dir_max_child_mtime` parent fallback uses `lstat()`.

    Cycle-5 changed the empty-dir fallback from `entry.stat().st_mtime`
    to `entry.lstat().st_mtime`. The caller (`cleanup_old_teams`,
    `cleanup_old_tasks`) already has an outer `is_symlink()` guard, but
    the helper-in-isolation should not follow symlinks — defensive against
    future callers that forget the guard. Same pattern as cycle-2's F2 fix.
    """

    def test_fallback_uses_lstat_not_stat_on_dir_symlink(self, tmp_path):
        """Empty-dir fallback path: probe a symlink-dir, expect link's own mtime.

        The fallback branch is only reached when the dir has no children
        matched by the glob (`saw_any_child=False`). Construct an empty
        dir AND a symlink pointing at a fresh external dir; probe the
        symlink directly. Under `entry.lstat()`, returns the link's
        (old) mtime. Under `entry.stat()`, would dereference and return
        the target's (fresh) mtime.

        Note: this directly probes `_dir_max_child_mtime` rather than
        going through `cleanup_old_tasks`, because the caller's
        `is_symlink()` guard would short-circuit the symlink before the
        helper runs in normal flow. We're pinning the helper's defense-
        in-isolation behavior.
        """
        import os as _os
        import time as _time
        from session_end import _dir_max_child_mtime

        # Fresh external target dir
        target = tmp_path / "external-fresh-dir"
        target.mkdir()
        _os.utime(str(target), (_time.time(), _time.time()))

        # Symlink with OLD lstat mtime pointing at fresh target
        link = tmp_path / "old-symlink-dir"
        link.symlink_to(target)
        old = _time.time() - (40 * 86400)
        _os.utime(str(link), (old, old), follow_symlinks=False)

        # Probe the symlink with default glob (no children match → fallback).
        result = _dir_max_child_mtime(link, glob="*.json")

        assert result is not None, (
            "Empty-dir fallback should return a float, not sentinel"
        )
        age_days = (_time.time() - result) / 86400
        assert age_days > 30, (
            f"_dir_max_child_mtime fallback returned {age_days:.1f}d old — "
            f"expected >30d (link lstat). If returned ~0d (target stat), "
            f"the fallback was reverted from entry.lstat() to entry.stat()."
        )


