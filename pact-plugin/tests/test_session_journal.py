"""
Tests for shared/session_journal.py -- append-only JSONL event store
for GC-proof workflow state persistence.

Tests cover:

make_event():
1. Sets v=1 schema version
2. Sets ts to current UTC time (ISO 8601)
3. Passes through type-specific keyword fields
4. Timestamp is always last (ts set after user fields)

append_event():
5. Write event, read it back, fields match (roundtrip)
6. Creates directory when teams dir doesn't exist (mkdir -p)
7. Returns False when v field is missing
8. Returns False when v field is not an int
8a. Returns False when v is True (bool subclass of int)
8b. Returns False when v is False (bool subclass of int)
9. Returns False when type field is missing
10. Returns False when type field is empty string
11. Returns False when session_dir is empty
12. Returns False on write error (fail-open, no exception)
13. Auto-sets ts when missing from event
14. Creates file with 0o600 permissions
15. Creates directory with 0o700 permissions

read_events():
16. Returns all events when no type filter
17. Returns only matching events when type filter applied
18. Returns empty list when journal file missing
19. Skips malformed JSON lines
20. Skips empty lines
21. Returns empty list on outer exception (fail-open)
22. Returns events in chronological order
23a. Skips truncated/incomplete JSON line at end of file
23b. Returns empty list when filtering by nonexistent event_type

read_last_event():
23. Returns the most recent event of given type
24. Returns None when no matching events
25. Returns None when journal is missing

get_journal_path():
26. Returns correct absolute path string
27. Does not check file existence
27a. Returns empty string when session_dir unavailable

read_events_from() — Explicit API:
50. Reads events from explicit session directory path
51. Filters by event_type
52. Returns empty list for missing journal
53. Returns empty list for empty session_dir
54. Skips malformed lines
55. Skips empty lines
56. Returns empty for nonexistent event_type filter

read_last_event_from() — Explicit API:
57. Returns most recent matching event
58. Returns None when no match
59. Returns None for missing journal
60. Returns None for empty session_dir
61. Skips malformed lines

_get_session_dir():
62. Returns session dir from mocked pact_context
63. Returns empty string when unavailable

Concurrency (P1):
28. Multiple threads can append without interleaving
29. Large event (~3KB) writes and reads correctly

Integration:
30. _build_journal_resume produces correct markdown from events
31. _build_journal_resume returns None for empty journal
32. _build_journal_resume includes handoff decisions (truncated)
33. _build_journal_resume includes phase progress
34. _build_journal_resume includes session_end warnings
35. _check_journal_paused_state returns None when no paused events
36. _check_journal_paused_state returns formatted context for paused PR
37. _check_journal_paused_state includes consolidation warning
38. _check_journal_paused_state handles stale (>14 day) events
39. _check_journal_paused_state returns None when pr_number is None
40. _check_journal_paused_state handles MERGED/CLOSED PR
41. restore_last_session uses journal when prev_session_dir provided
42. check_paused_state uses journal when prev_session_dir provided
43. _extract_prev_session_dir returns None on IOError (fail-open)

Integration: pact_context.write_context() cache fix:
64. Full path chain: write_context -> append_event -> file at sessions/ path
65. write_context populates _cache for immediate get_session_dir() access

CLI (main()):
44. write subcommand creates event and exits 0
45. read subcommand outputs JSON array and exits 0
46. read-last subcommand outputs single event JSON and exits 0
47. write with invalid --data JSON exits 1
48. write with missing --type exits non-zero
49. read-last with no matching events outputs "null"
"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

# Module-level for the arc-scoping / read-contract fixtures (#972 — #963/#966).
from shared.session_journal import make_event  # noqa: E402


# Module-level worker for multi-process concurrency tests.
# Must be picklable (i.e. top-level) so multiprocessing.Process can spawn it
# on macOS where the default start method is "spawn" (not "fork").
def _concurrent_large_writer_worker(
    worker_id: int,
    num_events: int,
    payload_size: int,
    journal_path: str,
    hooks_path: str,
) -> None:
    import sys as _sys
    if hooks_path not in _sys.path:
        _sys.path.insert(0, hooks_path)
    from pathlib import Path as _Path
    from shared.session_journal import _atomic_write

    # Each event is a JSON-shaped line well above macOS PIPE_BUF (512 B).
    # Use a distinctive payload the reader can validate per-event.
    filler = "x" * max(0, payload_size - 200)
    for seq in range(num_events):
        event = {
            "v": 1,
            "type": "test",
            "ts": "2026-04-06T00:00:00Z",
            "worker": worker_id,
            "seq": seq,
            "filler": filler,
        }
        line = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
        # Guardrail: the test is only meaningful above PIPE_BUF. If the
        # filler calculation is ever tuned wrong, fail loudly in the worker.
        if len(line) <= 512:
            raise AssertionError(
                f"worker {worker_id}: line too small "
                f"({len(line)} bytes) — test would not exercise "
                f"the PIPE_BUF interleaving window"
            )
        if not _atomic_write(_Path(journal_path), line):
            raise RuntimeError(
                f"worker {worker_id} seq {seq}: _atomic_write returned False"
            )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def journal_home(tmp_path, monkeypatch):
    """Redirect Path.home() to tmp_path for filesystem isolation."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def session_dir(journal_home):
    """Standard test session directory path (string, not created on disk)."""
    return str(journal_home / ".claude" / "pact-sessions" / "test-project" / "test-session-id")


@pytest.fixture
def team_name():
    """Standard test team name (still used for non-journal tests)."""
    return "pact-test1234"


@pytest.fixture
def journal_file(session_dir):
    """Return the expected journal file path (does not create it)."""
    return Path(session_dir) / "session-journal.jsonl"


@pytest.fixture(autouse=True)
def mock_get_session_dir(monkeypatch, session_dir):
    """Mock _get_session_dir at the session_journal module level.

    Patches the internal _get_session_dir() function which is called by
    _journal_path() to derive the implicit path. This avoids import-path
    complications (shared.pact_context vs pact_context).
    """
    import shared.session_journal as sj
    monkeypatch.setattr(sj, "_get_session_dir", lambda: session_dir)


# ---------------------------------------------------------------------------
# make_event()
# ---------------------------------------------------------------------------


class TestMakeEvent:
    """Tests for make_event() -- event construction with defaults."""

    def test_sets_schema_version(self):
        from shared.session_journal import make_event

        event = make_event("session_start")
        assert event["v"] == 1


# ---------------------------------------------------------------------------
# append_event()
# ---------------------------------------------------------------------------


class TestAppendEvent:
    """Tests for append_event() -- atomic JSONL append."""

    def test_roundtrip_append_and_read(self, journal_home):
        """P0: Write event, read it back, all fields match."""
        from shared.session_journal import append_event, make_event, read_events

        event = make_event(
            "agent_handoff",
            agent="backend-coder",
            task_id="7",
            task_subject="CODE: implement auth",
            handoff={"produced": ["src/auth.ts"]},
        )

        result = append_event(event)
        assert result is True

        events = read_events()
        assert len(events) == 1

        read_back = events[0]
        assert read_back["v"] == 1
        assert read_back["type"] == "agent_handoff"
        assert read_back["agent"] == "backend-coder"
        assert read_back["task_id"] == "7"
        assert read_back["handoff"]["produced"] == ["src/auth.ts"]
        assert "ts" in read_back


# ---------------------------------------------------------------------------
# read_events()
# ---------------------------------------------------------------------------


class TestReadEvents:
    """Tests for read_events() -- JSONL reading with type filtering."""

    def test_returns_all_events_without_filter(self, journal_home, team_name):
        """P0: Returns all events when no type filter."""
        from shared.session_journal import append_event, make_event, read_events

        append_event(
            make_event(
                "session_start",
                team="t1",
                session_id="sid1",
                project_dir="/tmp/p1",
            ),
        )
        append_event(
            make_event(
                "agent_handoff",
                agent="coder",
                task_id="1",
                task_subject="s",
                handoff={},
            ),
        )
        append_event(make_event("session_end"))

        events = read_events()
        assert len(events) == 3
        assert events[0]["type"] == "session_start"
        assert events[1]["type"] == "agent_handoff"
        assert events[2]["type"] == "session_end"


# ---------------------------------------------------------------------------
# read_last_event()
# ---------------------------------------------------------------------------


class TestReadLastEvent:
    """Tests for read_last_event() -- most recent event of a given type."""

    def test_returns_most_recent(self, journal_home, team_name):
        """P0: Returns the last matching event."""
        from shared.session_journal import append_event, make_event, read_last_event

        append_event(make_event("checkpoint", phase="PREPARE", data="old"))
        append_event(
            make_event(
                "agent_handoff",
                agent="coder",
                task_id="1",
                task_subject="s",
                handoff={},
            ),
        )
        append_event(make_event("checkpoint", phase="CODE", data="new"))

        last = read_last_event("checkpoint")
        assert last is not None
        assert last["data"] == "new"


class TestReadLastEventExcludeScope:
    """`exclude_scope` — the per-dispatch-mirror guard for read-last.

    Bugbot T1 (cycle 3): orchestrate's post-compaction recovery reads the
    feature's `variety_assessed` via read-last, and per-dispatch mirrors
    (`scope: "dispatch"`) land LATER in the journal than the feature-level
    assessment — so an unfiltered reverse scan returns a Task-B dispatch
    total as the feature assessment. The exclusion skips any event whose
    TOP-LEVEL `scope` equals the given value; events with no `scope` field
    or a different value are unaffected, and the exclusion threads BOTH
    scan paths (tail window and full-slurp fallback) so an excluded event
    found in the tail cannot be resurrected by the fallback.
    """

    def _write_journal(self, session_dir, events):
        from shared.session_journal import make_event

        path = Path(session_dir) / "session-journal.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(make_event(**spec)) for spec in events
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    FEATURE = {
        "event_type": "variety_assessed",
        "task_id": "5",
        "variety": {"total": 9},
        "ts": "2026-09-10T08:00:00Z",
    }
    DISPATCH_LATER = {
        "event_type": "variety_assessed",
        "task_id": "7",
        "scope": "dispatch",
        "variety": {"total": 4},
        "ts": "2026-09-10T09:00:00Z",
    }
    DISPATCH_LATEST = {
        "event_type": "variety_assessed",
        "task_id": "8",
        "scope": "dispatch",
        "variety": {"total": 5},
        "ts": "2026-09-10T10:00:00Z",
    }

    def test_unfiltered_read_returns_the_dispatch_mirror(self, session_dir):
        """The defect itself, as a control: without the exclusion the
        reverse scan returns the LATEST dispatch mirror."""
        from shared.session_journal import read_last_event_from

        self._write_journal(
            session_dir,
            [self.FEATURE, self.DISPATCH_LATER, self.DISPATCH_LATEST],
        )
        event = read_last_event_from(session_dir, "variety_assessed")
        assert event is not None
        assert event["task_id"] == "8"


# ---------------------------------------------------------------------------
# _read_last_event_at() — tail-streamed branch coverage
# ---------------------------------------------------------------------------


class TestReadLastEventTailStreamed:
    """Pin the four new branches introduced by the tail-streamed rewrite of
    `_read_last_event_at`: tail-hit happy path, tail-miss fallback, byte-
    boundary alignment edge case, and empty-file early-return.

    Existing TestReadLastEvent tests use small synthetic journals (3 events,
    < 1 KB) and exclusively exercise the `size <= _TAIL_WINDOW_BYTES` early-
    branch. These tests construct journals strictly LARGER than the tail
    window so the tail-seek / partial-line-discard / fallback control flow
    is actually exercised.

    Fixtures use `append_event(make_event("checkpoint", phase=..., data=...))`
    for normal padding (passes the writer's schema validator: checkpoint
    requires `phase: str`). The single target event is `session_consolidated` with
    a fixed `task_count` integer for unambiguous identification.

    Tests (c) and (d) bypass `append_event` and write directly to the
    journal file path because they require byte-precise placement or an
    empty-file fixture that `append_event` cannot produce.
    """

    @staticmethod
    def _filler_data_for_target_size(target_bytes):
        """Return a `data` payload string sized so that one checkpoint event
        is approximately `target_bytes` of journal text. The overhead of the
        JSON wrapper (v, type, ts, phase, data keys + quotes + braces +
        newline) is roughly 110 bytes; we subtract that and floor at 1.
        """
        return "x" * max(1, target_bytes - 110)

    def _pad_journal_to_size(self, target_size_bytes, event_size_bytes=512):
        """Append checkpoint events until the journal file is at least
        `target_size_bytes` in size. Each event contributes approximately
        `event_size_bytes` of journal text.
        """
        from shared.session_journal import (
            append_event, get_journal_path, make_event,
        )

        filler_data = self._filler_data_for_target_size(event_size_bytes)
        journal_path = Path(get_journal_path())
        while True:
            if journal_path.exists() and journal_path.stat().st_size >= target_size_bytes:
                break
            append_event(make_event(
                "checkpoint", phase="filler", data=filler_data,
            ))

    def test_tail_hit_large_journal_returns_target(
        self, journal_home, session_dir,
    ):
        """Test (a) — Tail-hit large-journal happy path.

        Fixture: pad journal beyond _TAIL_WINDOW_BYTES, then append the
        target `session_consolidated` event LAST so it lands in the tail window.
        Assert the tail-scan returns the target event's task_count.

        Falsifiability: counter-test-by-revert is performed against
        `_read_last_event_at` itself — replacing the `return match` after
        the tail scan with `return None` (force tail-miss-then-fallback
        even on real tail-hit) leaves the target STILL recoverable via
        the fallback, so this test stays GREEN — but in conjunction with
        test (b), the asymmetric mutation that breaks ONLY the tail-scan
        path (without breaking fallback) discriminates which branch is
        load-bearing for which test. The stronger discriminating mutation
        is "break the entire tail return + fallback" (full guard
        bypass) — that breaks BOTH (a) and (b). Test (a)'s primary
        contract is correctness, not branch-discrimination on its own.

        Performance: cost should be O(_TAIL_WINDOW_BYTES) on tail-hit,
        not O(file_size). Not asserted directly (timing is flaky);
        documented here as the load-bearing-claim the test guards.
        """
        from shared.session_journal import (
            _read_last_event_at, _TAIL_WINDOW_BYTES,
            append_event, get_journal_path, make_event,
        )

        # Pad beyond tail window. After padding, append the target event
        # so it is the LAST event on disk — guaranteed to fall within the
        # last _TAIL_WINDOW_BYTES bytes.
        self._pad_journal_to_size(_TAIL_WINDOW_BYTES + 4096)
        append_event(make_event("session_consolidated", task_count=20260516))

        journal_path = Path(get_journal_path())
        assert journal_path.stat().st_size > _TAIL_WINDOW_BYTES, (
            "Fixture invariant: journal must exceed tail window to "
            "exercise the tail-streamed branch."
        )

        result = _read_last_event_at(journal_path, "session_consolidated")
        assert result is not None
        assert result["task_count"] == 20260516


# ---------------------------------------------------------------------------
# get_journal_path()
# ---------------------------------------------------------------------------


class TestGetJournalPath:
    """Tests for get_journal_path() -- path string helper."""

    def test_returns_correct_path(self, journal_home, session_dir):
        from shared.session_journal import get_journal_path

        path = get_journal_path()
        expected = str(Path(session_dir) / "session-journal.jsonl")
        assert path == expected


# ---------------------------------------------------------------------------
# read_events_from() — Explicit API
# ---------------------------------------------------------------------------


class TestReadEventsFrom:
    """Tests for read_events_from() — explicit session_dir parameter."""

    def test_reads_events_from_explicit_path(self, tmp_path):
        """P0: Reads events from a specified session directory."""
        from shared.session_journal import read_events_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-abc")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"v":1,"type":"session_start","team":"t","ts":"2026-01-01T00:00:00Z"}\n'
            '{"v":1,"type":"agent_handoff","agent":"coder","ts":"2026-01-01T00:01:00Z"}\n'
        )

        events = read_events_from(session_dir)
        assert len(events) == 2
        assert events[0]["type"] == "session_start"
        assert events[1]["type"] == "agent_handoff"


# ---------------------------------------------------------------------------
# read_last_event_from() — Explicit API
# ---------------------------------------------------------------------------


class TestReadLastEventFrom:
    """Tests for read_last_event_from() — explicit session_dir parameter."""

    def test_returns_most_recent_matching_event(self, tmp_path):
        """P0: Returns the last event matching the type."""
        from shared.session_journal import read_last_event_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-abc")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"v":1,"type":"checkpoint","data":"old","ts":"2026-01-01T00:00:00Z"}\n'
            '{"v":1,"type":"agent_handoff","agent":"c","ts":"2026-01-01T00:01:00Z"}\n'
            '{"v":1,"type":"checkpoint","data":"new","ts":"2026-01-01T00:02:00Z"}\n'
        )

        result = read_last_event_from(session_dir, "checkpoint")
        assert result is not None
        assert result["data"] == "new"


# ---------------------------------------------------------------------------
# _get_session_dir() — Internal helper
# ---------------------------------------------------------------------------


class TestGetSessionDir:
    """Tests for _get_session_dir() — lazy import of pact_context."""

    def test_returns_session_dir_from_mock(self, session_dir):
        """Returns the session dir value from the monkeypatched function."""
        import shared.session_journal as sj

        result = sj._get_session_dir()
        assert result == session_dir


# ---------------------------------------------------------------------------
# Integration: pact_context.write_context() -> journal (Scenario 13)
# ---------------------------------------------------------------------------


class TestWriteContextCacheIntegration:
    """Integration test: write_context() populates _cache so get_session_dir()
    works immediately after, enabling append_event() to find the journal path.

    This verifies the critical one-line fix in pact_context.py (Phase 2 of plan).
    """

    def test_full_path_chain_write_context_then_journal(self, tmp_path, monkeypatch):
        """P1: Real pact_context.write_context -> append_event -> file at sessions/ path."""
        import shared.pact_context as pc

        # Reset pact_context module state
        monkeypatch.setattr(pc, "_context_path", None)
        monkeypatch.setattr(pc, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        project_dir = str(tmp_path / "project")
        team_name = "pact-aaaaaaaa"

        # Write context — this should populate _cache
        pc.write_context(team_name, session_id, project_dir)

        # Verify _cache was populated (the fix)
        assert pc._cache is not None
        assert pc._cache["session_id"] == session_id

        # Verify get_session_dir returns the correct path
        sd = pc.get_session_dir()
        assert sd != ""
        assert "pact-sessions" in sd
        assert session_id in sd

        # Now use session_journal with the real get_session_dir
        import shared.session_journal as sj

        # Undo the autouse mock — use real _get_session_dir
        monkeypatch.setattr(sj, "_get_session_dir", pc.get_session_dir)

        from shared.session_journal import append_event, make_event, read_events

        event = make_event("integration_test", data="hello")
        result = append_event(event)
        assert result is True

        # Read back and verify
        events = read_events()
        assert len(events) == 1
        assert events[0]["type"] == "integration_test"
        assert events[0]["data"] == "hello"

        # Verify the file is at the expected sessions/ path
        expected_journal = (
            tmp_path / ".claude" / "pact-sessions" / "project" / session_id
            / "session-journal.jsonl"
        )
        assert expected_journal.exists()


# ---------------------------------------------------------------------------
# P1: Concurrency and Large Events
# ---------------------------------------------------------------------------


class TestConcurrentAppends:
    """P1: Thread-safety of concurrent append operations."""

    def test_concurrent_threads_no_interleaving(self, journal_home, team_name):
        """Multiple threads appending simultaneously produce valid JSONL."""
        from shared.session_journal import append_event, make_event, read_events

        num_threads = 10
        events_per_thread = 20
        errors = []

        def writer(thread_id):
            try:
                for seq in range(events_per_thread):
                    event = make_event("test", thread=thread_id, seq=seq)
                    result = append_event(event)
                    if not result:
                        errors.append(f"Thread {thread_id} seq {seq} failed")
            except Exception as e:
                errors.append(f"Thread {thread_id}: {e}")

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"

        events = read_events()
        assert len(events) == num_threads * events_per_thread

        # Verify each event is complete (no interleaved JSON)
        for event in events:
            assert "v" in event
            assert "type" in event
            assert "thread" in event
            assert "seq" in event


# ---------------------------------------------------------------------------
# Integration: _build_journal_resume()
# ---------------------------------------------------------------------------


class TestBuildJournalResume:
    """Integration tests for _build_journal_resume() in session_resume.py."""

    def test_returns_none_for_empty_journal(self, journal_home, session_dir):
        """Returns None when no events exist in the journal."""
        from shared.session_resume import _build_journal_resume

        result = _build_journal_resume(session_dir)
        assert result is None


# ---------------------------------------------------------------------------
# Integration: simulated-compaction full lifecycle
# ---------------------------------------------------------------------------


class TestJournalLifecycleSimulatedCompaction:
    """End-to-end lifecycle test for the session journal feature.

    Addresses the end-to-end validation gap noted in PR #350 merge-readiness
    review: every other test in this suite mocks either the write side
    (`sj._get_session_dir`) or the read side (by calling
    `_build_journal_resume(session_dir)` against a path seeded by hand). This
    class asserts the full chain survives an in-memory state reset:

        write_context -> pact_context.get_session_dir -> append_event
        -> [clear in-memory caches to simulate compaction]
        -> read from disk via _build_journal_resume

    What this test does NOT validate (be explicit about the limitations):

    1. **Real platform compaction** — Claude's compaction summarizes the
       model context window; it does not clear Python module state. This
       test substitutes "clear `_cache` / `_context_path` / reimport" for
       "model context was summarized" because the two share the same
       observable precondition for the journal: the in-memory state from
       the prior session is gone, and the only surviving artifact is the
       JSONL file on disk. It catches write/read contract drift; it does
       not catch bugs that require a real compaction event.
    2. **Hook behavior during recovery** — session_init's on-resume logic
       (reconstructing CLAUDE.md, re-invoking resolvers, spawning the
       secretary) is not exercised. See issue #364 for that scope.
    3. **Cross-session recovery** — this test writes and reads under a
       single session_id. A genuine "resume from previous session" flow
       would write events under session_id=A and read them while a new
       session_id=B is active. The journal file path derivation is
       session-scoped, so the cross-session case adds path-resolution
       concerns the single-session lifecycle does not.
    4. **Performance under large journals** — the test writes a handful
       of events. Scalability of `_build_journal_resume` against thousands
       of events is not measured here.

    The test is a coverage *improvement*, not a *complete validation*. Its
    value is catching write/read schema drift end-to-end — the class of
    bug where a writer and a reader agree in isolation (unit tests pass)
    but disagree on the on-disk representation.
    """

    def test_journal_state_survives_simulated_compaction(
        self, tmp_path, monkeypatch
    ):
        """P1: write → clear in-memory state → re-read recovers full journal.

        Uses REAL filesystem persistence via monkeypatched Path.home(), the
        public write API (`append_event` / `make_event`), and the public
        read path (`_build_journal_resume(session_dir)` — the same entry
        point session_init calls on resume). No mocking of the journal
        write or read code paths.

        Determinism: every `make_event(...)` call passes an explicit `ts`
        (monotonically increasing) so the test does not depend on wall
        clock resolution or ordering of same-second events. The
        `_build_journal_resume` active-phase logic uses `max(ts)` to pick
        the latest started phase, so the injected timestamps double as a
        verification that the tie-breaking contract is stable.
        """
        import shared.pact_context as pc
        import shared.session_journal as sj

        # --- Setup: isolate filesystem to tmp_path, reset module caches.
        # This is equivalent to "the previous session wrote these events,
        # then the process ended; now we are in a new process whose
        # in-memory state is empty but the on-disk journal remains."
        monkeypatch.setattr(pc, "_context_path", None)
        monkeypatch.setattr(pc, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Disable the autouse `mock_get_session_dir` fixture so the real
        # pact_context.get_session_dir() drives path derivation — without
        # this line, the autouse fixture pins _get_session_dir to a
        # hard-coded string and bypasses the write_context cache path
        # this test is trying to exercise.
        monkeypatch.setattr(sj, "_get_session_dir", pc.get_session_dir)

        session_id = "11111111-2222-3333-4444-555555555555"
        project_dir = str(tmp_path / "test_project")
        Path(project_dir).mkdir(parents=True)

        # --- Act (phase 1): bootstrap session context and write events.
        pc.write_context("pact-lifecycle", session_id, project_dir, "/plugin/root")

        # Sanity check: the context cache MUST be populated by write_context
        # (otherwise append_event's path derivation will silently fall
        # through and we would be testing a fail-open code path instead
        # of the lifecycle).
        assert pc._cache is not None
        assert pc.get_session_dir() != ""

        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        # Explicit, monotonically increasing timestamps — the
        # `_build_journal_resume` active-phase selector sorts by `ts`,
        # so deterministic stamps prove the selector works on the
        # persisted bytes, not on iteration order.
        events = [
            make_event(
                "session_start",
                team="pact-lifecycle",
                session_id=session_id,
                project_dir=project_dir,
                ts="2026-04-06T10:00:00Z",
            ),
            make_event(
                "phase_transition",
                phase="PREPARE",
                status="started",
                ts="2026-04-06T10:01:00Z",
            ),
            make_event(
                "phase_transition",
                phase="PREPARE",
                status="completed",
                ts="2026-04-06T10:02:00Z",
            ),
            make_event(
                "phase_transition",
                phase="CODE",
                status="started",
                ts="2026-04-06T10:03:00Z",
            ),
            make_event(
                "agent_handoff",
                agent="lifecycle-coder",
                task_id="61",
                task_subject="CODE: lifecycle integration test",
                handoff={"decisions": ["Wrote the lifecycle test end-to-end"]},
                ts="2026-04-06T10:04:00Z",
            ),
        ]
        for e in events:
            assert append_event(e) is True, f"append_event failed for {e['type']}"

        # Verify the journal file actually exists on disk before we
        # clear state — if this fails, the rest of the test is moot.
        expected_session_dir = (
            tmp_path / ".claude" / "pact-sessions" / "test_project" / session_id
        )
        expected_journal = expected_session_dir / "session-journal.jsonl"
        assert expected_journal.exists()
        on_disk_lines = expected_journal.read_text(encoding="utf-8").splitlines()
        assert len(on_disk_lines) == len(events)

        # --- Simulate compaction: drop ALL in-memory state that could
        # short-circuit a re-read. This is the closest analog to a
        # context-loss event available from inside a Python test —
        # the journal file on disk is now the ONLY record of the
        # session's prior work.
        pc._cache = None
        pc._context_path = None

        # --- Act (phase 2): reconstruct session_dir from known inputs
        # (slug + session_id) and ask session_resume to rebuild the
        # resume summary from disk. We deliberately pass the path
        # explicitly — _build_journal_resume is the public entry point
        # session_init calls with a previous-session dir, so this
        # mirrors the real recovery code path.
        session_dir_str = str(expected_session_dir)
        resume = _build_journal_resume(session_dir_str)

        # --- Assert: the resume string reflects every persisted fact.
        assert resume is not None, (
            "resume build returned None after simulated compaction — "
            "the on-disk journal was not recovered"
        )
        # Handoff survived
        assert "## Completed Work" in resume
        assert "lifecycle-coder" in resume
        assert "CODE: lifecycle integration test" in resume
        assert "Wrote the lifecycle test end-to-end" in resume
        # Phase progress survived
        assert "Completed phases: PREPARE" in resume
        # CODE was started but never completed — must be the active phase
        # picked by max(ts), not the earlier PREPARE start.
        assert "Last active phase: CODE" in resume
        # PREPARE must NOT appear as the active phase — its latest
        # transition was `completed`, so the started→completed transition
        # must correctly demote it out of the active-phase set.
        assert "Last active phase: PREPARE" not in resume


# ---------------------------------------------------------------------------
# Integration: _check_journal_paused_state()
# ---------------------------------------------------------------------------


class TestCheckJournalPausedState:
    """Integration tests for _check_journal_paused_state() in session_resume.py."""

    def test_returns_none_when_no_paused_events(self, journal_home, session_dir):
        """Returns None when no session_paused events exist."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        # Write some non-paused events
        append_event(make_event("session_start", team="pact-test1234"))

        result = _check_journal_paused_state(session_dir)
        assert result is None


# ---------------------------------------------------------------------------
# AdvF2 Approach 4: implicit-API stderr warnings on missing pact_context.init()
# ---------------------------------------------------------------------------


class TestImplicitApiUninitWarnings:
    """The implicit API (append_event/read_events/read_last_event) prints a
    stderr warning -- without changing return values -- when called before
    pact_context.init() AND the journal path cannot be derived. The warning
    only fires on the path-unavailable fail-open branch, so it doesn't add
    noise to in-process tests that monkeypatch _get_session_dir to a real
    path.

    Each test overrides the autouse mock_get_session_dir fixture by
    re-monkeypatching _get_session_dir to return "" inside the test body.
    """

    def test_append_event_warns_when_uninit(self, monkeypatch, capsys):
        import shared.session_journal as sj
        from shared.session_journal import append_event, make_event

        # Override autouse fixture: simulate "no session dir" so _journal_path
        # returns None and we land on the fail-open branch.
        monkeypatch.setattr(sj, "_get_session_dir", lambda: "")
        monkeypatch.setattr(sj, "_pact_context_is_initialized", lambda: False)

        result = append_event(
            make_event(
                "session_start",
                session_id="s1",
                project_dir="/tmp/p",
            ),
        )

        # Fail-open semantics preserved: returns False, no exception.
        assert result is False
        captured = capsys.readouterr()
        assert "append_event called before pact_context.init()" in captured.err
        assert "returning False" in captured.err


# ---------------------------------------------------------------------------
# Integration: restore_last_session() and check_paused_state() prefer journal
# ---------------------------------------------------------------------------


class TestJournalPreference:
    """Tests that restore_last_session and check_paused_state use journal."""

    def test_restore_uses_journal(self, journal_home, session_dir):
        """restore_last_session reads journal when prev_session_dir is provided."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import restore_last_session

        append_event(
            make_event(
                "agent_handoff",
                agent="coder",
                task_id="1",
                task_subject="CODE: feature",
                handoff={"decisions": ["Built feature X"]},
            ),
        )

        result = restore_last_session(prev_session_dir=session_dir)

        assert result is not None
        assert "journal" in result  # Journal-based resume header


# ---------------------------------------------------------------------------
# Integration: agent_handoff_emitter journal write (#538 replacement for
# prior handoff_gate coverage; handoff_gate removed in C2a)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Integration: session_init journal write
# ---------------------------------------------------------------------------


class TestSessionInitJournalWrite:
    """Tests that session_init.main() writes session_start event to journal."""

    @pytest.mark.parametrize(
        "stdin_source, expected_on_disk",
        [
            ("startup", "startup"),],
    )
    def test_writes_session_start_event(
        self, journal_home, monkeypatch, stdin_source, expected_on_disk
    ):
        """session_init.main() writes session_start event to journal."""
        # NOTE: The class-level `mock_get_session_dir` autouse fixture at
        # test_session_journal.py:188-197 patches `_get_session_dir()` on the
        # session_journal module, so this test bypasses the real
        # `pact_context`-backed session-dir derivation. What IS verified
        # end-to-end: the source-normalization branches in
        # session_init.main(), journal serialization to JSONL on disk, and
        # read_events() round-trip — including the non-string isinstance
        # guard via the (42, "unknown") case. What is NOT exercised:
        # `pact_context.init()` path resolution.
        import io

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(journal_home / "project"))
        (journal_home / "project").mkdir()
        (journal_home / "project" / "CLAUDE.md").write_text("# Project\n## Retrieved Context\n")

        # #877: the session_start journal anchor is a lead-only write (gated
        # behind is_lead), so a lead agent_type is required for it to be written.
        input_data = {
            "session_id": "abc12345-test",
            "source": stdin_source,
            "agent_type": "pact-orchestrator",
        }

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
             patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
             patch("session_init.get_task_list", return_value=[]), \
             patch("shared.session_init_lead.persist_context"), \
             patch("shared.session_init_lead.check_resumption_context", return_value=None), \
             patch("shared.session_init_lead.restore_last_session", return_value=None), \
             patch("shared.session_init_lead.check_resume_state", return_value=None):

            with pytest.raises(SystemExit) as exc:
                from session_init import main
                main()

            assert exc.value.code == 0

        # Verify session_start event was written to journal
        from shared.session_journal import read_events

        events = read_events("session_start")
        assert len(events) >= 1
        assert events[0]["team"] == "session-abc12345"
        assert events[0].get("source") == expected_on_disk


# ---------------------------------------------------------------------------
# Integration: session_end journal write
# ---------------------------------------------------------------------------


class TestSessionEndJournalWrite:
    """Tests that session_end.main() writes session_end event to journal."""

    def test_writes_session_end_event(self, journal_home, monkeypatch):
        """session_end.main() writes session_end event to journal."""
        import io

        # Pre-create journal with a session_start event
        from shared.session_journal import append_event, make_event
        append_event(make_event("session_start", team="pact-endtest12"))

        input_data = {}

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("session_end.pact_context") as mock_ctx, \
             patch("session_end.get_project_dir", return_value=str(journal_home / "project")), \
             patch("session_end.get_session_id", return_value="test-session-id"), \
             patch("session_end.get_team_name", return_value="pact-endtest12"), \
             patch("session_end.get_task_list", return_value=[]):

            with pytest.raises(SystemExit) as exc:
                from session_end import main
                main()

            assert exc.value.code == 0

        # Verify session_end event was written
        from shared.session_journal import read_events

        events = read_events("session_end")
        assert len(events) >= 1


# ---------------------------------------------------------------------------
# Integration: _extract_prev_session_dir
# ---------------------------------------------------------------------------


class TestExtractPrevSessionDir:
    """Tests for session_init._extract_prev_session_dir()."""

    def test_extracts_session_dir_from_claude_md(self, tmp_path):
        """Extracts session dir from Session dir line in Current Session block."""
        from session_init import _extract_prev_session_dir

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            "- Resume: `claude --resume abc123`\n"
            "- Team: `pact-abc12345`\n"
            "- Session dir: `~/.claude/pact-sessions/myproject/abc123`\n"
            "- Started: 2026-04-05\n"
            "<!-- SESSION_END -->\n"
        )
        named = Path.home() / ".claude" / "pact-sessions" / "myproject" / "abc123"
        assert str(named).startswith(str(tmp_path))
        named.mkdir(parents=True)

        result = _extract_prev_session_dir(str(tmp_path))
        assert result is not None
        assert result.endswith("pact-sessions/myproject/abc123")


# ---------------------------------------------------------------------------
# CLI tests (main() via subprocess)
# ---------------------------------------------------------------------------

# Resolve absolute path to session_journal.py once.
_SJ_SCRIPT = str(
    Path(__file__).parent.parent / "hooks" / "shared" / "session_journal.py"
)


class TestCLI:
    """Tests for the CLI entry point (main) invoked via subprocess."""

    def test_write_creates_event_and_exits_0(self, journal_home, session_dir, journal_file):
        """44. write subcommand creates event and exits 0."""
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "test_event",
                "--session-dir", session_dir,
                "--data", '{"key": "value"}',
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 0, result.stderr

        # Verify the event was written
        assert journal_file.exists()
        event = json.loads(journal_file.read_text().strip())
        assert event["type"] == "test_event"
        assert event["key"] == "value"
        assert event["v"] == 1
        assert "ts" in event


# ---------------------------------------------------------------------------
# Per-type schema validation (BugF1 primary defense — validator at write time)
# ---------------------------------------------------------------------------


class TestValidateEventSchemaPerType:
    """Per-type schema validation tests for BugF1 fix.

    Every event type in _REQUIRED_FIELDS_BY_TYPE must pass validation when
    all required fields are present, and must be rejected when any single
    required field is missing. This class is the bulwark that prevents
    malformed events from reaching disk where _build_journal_resume would
    have to defend against them at read time.

    The dict _REQUIRED_FIELDS_BY_TYPE is the authoritative schema; tests
    here mirror it so adding a new event type requires a test to land with
    it (see the comment on _REQUIRED_FIELDS_BY_TYPE in session_journal.py).
    """

    # Known event types and one valid sample of required fields. Mirrors
    # _REQUIRED_FIELDS_BY_TYPE — when adding a new entry to the source dict,
    # add the matching sample here so the happy-path test and missing-field
    # test both cover it.
    _SAMPLES: dict = {
        "session_start": {
            "session_id": "test-session-id",
            "project_dir": "/tmp/proj",
        },
        "compaction_attributed": {"verdict": "teammate", "basis": "content"},
        "compaction_summary_dropped": {"cause": "no_free_stamp"},
        "variety_assessed": {
            "task_id": "42",
            "variety": {"novelty": 1, "scope": 1, "uncertainty": 1, "risk": 1, "total": 4},
        },
        "dispatch_variety": {
            "task_id": "42",
            "variety": {"novelty": 3, "scope": 3, "uncertainty": 3, "risk": 3, "total": 12},
        },
        # dispatch_site carries the coverage DENOMINATOR. task_id ALONE is
        # required, and `variety` is deliberately absent from this sample:
        # the harness treats every sample key as required (it asserts each
        # one's removal is rejected), so listing an optional field here would
        # be a false claim about the schema. That absence is also the point of
        # the event — an un-stamped dispatch is a legal dispatch_site with no
        # variety, and it is precisely the gap the metric exists to count.
        "dispatch_site": {
            "task_id": "42",
        },
        # journal_emit_skipped records a write that did NOT land. Both keys
        # here are required: `skipped_type` is what lets the liveness check
        # recognise a drop of its OWN witness rather than merely counting
        # losses, and `cause` keeps a writer defect distinct from a schema
        # rejection. `task_id` is optional (two of the causes have no task to
        # name) and so is deliberately absent — this harness treats every
        # sample key as required.
        "journal_emit_skipped": {
            "skipped_type": "dispatch_site",
            "cause": "returned_false",
        },
        "teachback_ack": {
            "task_id": "41",
            "rationale_articulates_this_dispatch": "yes",
        },
        # Required fields only — subject/owner/task_type/truncated/occupant
        # are optional (see TestValidateOptionalFieldTypes); the missing-field
        # test removes each sample key and expects rejection, which only
        # holds for required ones.
        "task_metadata_snapshot": {
            "task_id": "9",
            "metadata": {"teachback_submit": {"understanding": "x"}},
        },
        "phase_transition": {"phase": "CODE", "status": "started"},
        "checkpoint": {"phase": "CODE"},
        "agent_dispatch": {"agent": "coder", "task_id": "1", "phase": "CODE"},
        "agent_handoff": {
            "agent": "coder",
            "task_id": "1",
            "task_subject": "CODE: thing",
            "handoff": {"decisions": ["x"]},
        },
        "handoff_refusal_degraded": {
            "agent_type": "pact-backend-coder",
            "detail": "PACT Handoff Refusal: missing produced",
            "classes": ["missing_handoff"],
        },
        "missed_wake": {
            "agent": "devops",
            "task_id": "1",
            "since": "2026-01-01T00:00:00+00:00",
        },
        "prompt_kind": {
            "kind": "idle_notification",
            "chars": 120,
        },
        "idle_on_finished_run": {
            "agent": "devops",
            "task_id": "1",
            "log_name": "2026-01-01T000000Z-42-pytest.log",
        },
        "unflagged_background_wait": {
            "agent": "devops",
            "registered_at": "2026-01-01T00:00:00+00:00",
            # A LIST, not a scalar — a launch is recorded against every
            # in_progress task its launcher holds.
            "task_ids": ["1", "2"],
        },
        # Required fields only — `ids` and `cause` are optional (see
        # TestValidateOptionalFieldTypes), and this harness treats every
        # sample key as required.
        "background_stop_gate": {
            "role": "lead",
            "verdict": "block",
            "running": 1,
        },
        # The D10 bridge's lead-acceptance record (required fields only; `main`
        # is optional, see TestValidateOptionalFieldTypes).
        "project_task_acceptance": {
            "ptask": "session-x/7",
            "op": "accept",
            "rev": 1,
            "t": "T-1a2b3c4d",
        },
        "s2_state_seeded": {
            "worktree": "/tmp/wt",
            "agents": ["c1", "c2"],
            "boundaries": {"c1": ["a/"], "c2": ["b/"]},
        },
        "commit": {"sha": "abc123", "message": "fix: thing", "phase": "CODE"},
        "review_dispatch": {
            "pr_number": 42,
            "pr_url": "https://github.com/o/r/pull/42",
            "reviewers": ["r1"],
        },
        "review_finding": {
            "severity": "blocking",
            "finding": "missing X",
            "reviewer": "architect",
        },
        "remediation": {"cycle": 1, "items": ["F1"], "fixer": "coder"},
        "pr_ready": {
            "pr_number": 42,
            "pr_url": "https://github.com/o/r/pull/42",
            "commits": 7,
        },
        # Required fields only: pr_number and pr_url are optional (a pause
        # with no PR is valid).
        "session_paused": {
            "branch": "feat/x",
            "worktree_path": "/tmp/wt",
            "consolidation_completed": True,
        },
        # Required fields only — deliberately identical to the degenerate
        # no-workstream refresh event (the minimal-event oracle). The sample
        # must NOT carry optional fields: the missing-field test removes each
        # sample key and expects rejection, which only holds for required ones.
        "session_refreshed": {
            "consolidation_completed": True,
            "halt_active": False,
        },
        "session_refresh_consumed": {
            "refresh_ts": "2026-07-10T15:00:00Z",
        },
        "session_pause_consumed": {
            "pause_ts": "2026-07-10T15:00:00Z",
        },
        # No required fields BY DESIGN, not by omission: presence of the event
        # is the whole signal the secretary reads at spawn.
        "session_resumption_surfaced": {},
        "session_end": {},  # No required fields; baseline-only.
        "cleanup_summary": {},  # No required fields; optional-only (#412 Fix B).
        "session_consolidated": {},  # No required fields; optional-only (#453 Fix B).
        "artifact_paths": {
            "workflow": "prepare",
            "feature": "handoff-artifact-durability",
            "paths": ["/tmp/wt/docs/preparation/handoff-artifact-durability.md"],
        },
        "gate_refusal": {"hook": "unattended_gate", "verdict": "deny", "rule": "act_named"},
        "unattended_mode": {"state": "on", "cause": "command"},
        "unattended_default": {
            "ref": "0123456789ab",
            "question": "Which layout",
            "chose": "Two columns (Recommended)",
            "why": "matches the plan",
        },
        "unattended_halt": {"task_id": "35", "question": "Merge the branch"},
        "metadata_write": {"task_id": "93", "key": "handoff", "bytes": 1840},
        "task_store_absent": {"team": "session-ab12", "task_id": "8", "misses": 2},
    }

    def test_samples_mirror_required_fields_dict(self):
        """Meta-test: _SAMPLES must cover every key in _REQUIRED_FIELDS_BY_TYPE.

        If this test fails, a new event type was added to the source dict
        without adding a matching sample here — guards against the per-type
        check silently skipping coverage of new types.

        Also verifies each sample value has the expected Python type from
        the source dict — otherwise happy-path tests would accidentally
        exercise the validator with mis-typed samples.
        """
        from shared.session_journal import _REQUIRED_FIELDS_BY_TYPE

        assert set(self._SAMPLES.keys()) == set(_REQUIRED_FIELDS_BY_TYPE.keys())
        for event_type, field_types in _REQUIRED_FIELDS_BY_TYPE.items():
            sample = self._SAMPLES[event_type]
            for field, expected_type in field_types.items():
                assert field in sample, (
                    f"sample for {event_type} missing required field {field!r}"
                )
                value = sample[field]
                # Mirror the validator's bool-in-int rejection.
                assert not (expected_type is int and isinstance(value, bool)), (
                    f"sample for {event_type}.{field} is bool but schema says int"
                )
                assert isinstance(value, expected_type), (
                    f"sample for {event_type}.{field} is "
                    f"{type(value).__name__}, expected {expected_type.__name__}"
                )


    # -- Fix A (RA1): per-field type checks -------------------------------

    # One wrong-typed sample per required field for each event type. Each
    # tuple is (event_type, field, bad_value, expected_type_name,
    # got_type_name). Built from _REQUIRED_FIELDS_BY_TYPE so adding a new
    # schema entry forces a matching mismatch sample.
    _TYPE_MISMATCHES: list = [
        # str fields fed an int
        ("phase_transition", "phase", 42, "str", "int"),
        ("phase_transition", "status", 42, "str", "int"),
        ("checkpoint", "phase", 42, "str", "int"),
        ("agent_dispatch", "agent", 42, "str", "int"),
        ("agent_handoff", "task_subject", 42, "str", "int"),
        ("commit", "sha", 42, "str", "int"),
        ("review_dispatch", "pr_url", 42, "str", "int"),
        ("review_finding", "severity", 42, "str", "int"),
        ("remediation", "fixer", 42, "str", "int"),
        ("session_paused", "branch", 42, "str", "int"),
        ("session_start", "session_id", 42, "str", "int"),
        ("variety_assessed", "task_id", 42, "str", "int"),
        # int fields fed a str
        ("review_dispatch", "pr_number", "42", "int", "str"),
        ("remediation", "cycle", "1", "int", "str"),
        ("pr_ready", "commits", "7", "int", "str"),
        # dict fields fed a list
        ("variety_assessed", "variety", [1, 2], "dict", "list"),
        ("agent_handoff", "handoff", [1, 2], "dict", "list"),
        ("s2_state_seeded", "boundaries", [1, 2], "dict", "list"),
        # list fields fed a dict
        ("s2_state_seeded", "agents", {"k": "v"}, "list", "dict"),
        ("review_dispatch", "reviewers", {"k": "v"}, "list", "dict"),
        ("remediation", "items", {"k": "v"}, "list", "dict"),
        # bool fields fed non-bool values (consolidation_completed on both
        # lifecycle events; halt_active on session_refreshed)
        ("session_paused", "consolidation_completed", 1, "bool", "int"),
        ("session_refreshed", "consolidation_completed", 1, "bool", "int"),
        ("session_refreshed", "halt_active", "yes", "bool", "str"),
        # str field fed an int (the consumption claim id must be a ts string)
        ("session_refresh_consumed", "refresh_ts", 42, "str", "int"),
        # the D10 bridge's lead-acceptance record: the revision is an int, the rest str
        ("project_task_acceptance", "ptask", 7, "str", "int"),
        ("project_task_acceptance", "op", 1, "str", "int"),
        ("project_task_acceptance", "rev", "1", "int", "str"),
        ("project_task_acceptance", "rev", True, "int", "bool"),
        ("project_task_acceptance", "t", 1, "str", "int"),
    ]


    # -- Fix B (RG2): empty/whitespace-only strings rejected --------------


# ---------------------------------------------------------------------------
# Per-type optional-field validation (BR-M2)
# ---------------------------------------------------------------------------


class TestValidateOptionalFieldTypes:
    """Per-type optional-field schema validation tests for BR-M2.

    The validator enforces type on optional fields declared in
    _OPTIONAL_FIELDS_BY_TYPE. A field is optional iff absent is OK; when
    present, it must match the declared Python type. This is the schema
    contract counterpart to runtime clamps (e.g. the `source` isinstance
    guard in session_init.py) — a future writer that bypasses the clamp
    and emits the wrong type directly to `make_event` is rejected at
    validate time instead of landing a bad type on disk.
    """

    def test_optional_fields_dict_shape(self):
        """_OPTIONAL_FIELDS_BY_TYPE maps event_type → {field: type}.

        Meta-test: validates the declaration shape so a malformed entry
        fails here rather than surfacing as a cryptic TypeError inside
        the validator.
        """
        from shared.session_journal import _OPTIONAL_FIELDS_BY_TYPE

        assert isinstance(_OPTIONAL_FIELDS_BY_TYPE, dict)
        for event_type, field_types in _OPTIONAL_FIELDS_BY_TYPE.items():
            assert isinstance(event_type, str) and event_type.strip()
            assert isinstance(field_types, dict)
            for field, expected_type in field_types.items():
                assert isinstance(field, str) and field.strip()
                assert isinstance(expected_type, type)


    # ---------------------------------------------------------------------
    # session_consolidated schema activation + optional-field tests (#453)
    # ---------------------------------------------------------------------


# ===========================================================================
# Arc-scoping: --since / _ts_ge (epic #972, child #963)
#
# The wrap-up Q5/Q6 retrospective aggregates over the session journal, which
# in a resumed/multi-feature session accumulates PRIOR arcs. The --since arc
# boundary (the current feature's latest variety_assessed.ts) scopes the
# reads to the current arc. _ts_ge is the parse-not-compare filter that
# backs it. All fixtures are synthetic journal events — GC-immune (no
# task-store dependence).
# ===========================================================================


def _write_journal(journal_file, events):
    """Write pre-built event dicts in the on-disk journal format (one JSON
    object per line). GC-immune fixture helper for the arc-scoping +
    read-contract tests below."""
    journal_file.parent.mkdir(parents=True, exist_ok=True)
    journal_file.write_text(
        "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in events)
    )


class TestTsGeArcFilter:
    """_ts_ge — the parse-not-compare arc-scope timestamp filter (#963)."""

    def test_equal_instant_cross_format_is_included(self):
        """The arc-start boundary event must be INCLUDED (inclusive >=) even
        when the event ts and the `since` differ in zone format (Z vs
        +00:00). make_event stamps `...Z`; canonical_since() emits
        `...+00:00`."""
        from shared.session_journal import _ts_ge

        # mission-named direction: since in +00:00, event ts in Z
        assert _ts_ge("2026-06-14T00:00:00Z", "2026-06-14T00:00:00+00:00") is True


class TestArcScopedDenominator:
    """End-to-end --since arc-scoping over a RESUMED 2-arc journal.

    Two arcs written into one session journal. Non-vacuity hinge: the prior
    and current arcs REUSE task ids 8/10/12 — the collision is the whole
    point. A fixture with unique ids would pass vacuously, because keying the
    arc boundary on task_id would collide; keying on ts (via --since/_ts_ge)
    is what makes arc-scoping correct. Unscoped denominator 9 → arc-scoped 4.

    RE-EXPRESSED for the one-event topology: the denominator moved from the
    three variety-independent markers to the `dispatch_site` stream, so the
    subject of these tests changed. The PROPERTY did not — a dispatch_site
    stream is equally arc-scoped and equally subject to cross-arc task_id
    reuse, so the arc boundary is exactly as load-bearing as before. This is
    why the property was re-expressed rather than deleted with the helper.
    """

    ARC1_TS = "2026-06-13T12:00:00Z"
    ARC2_START = "2026-06-14T09:00:00Z"  # current feature's variety_assessed.ts
    ARC2_TS = "2026-06-14T12:00:00Z"

    def _ds(self, task_id, ts, stamped=True):
        """A `dispatch_site` event — the denominator's unit. `stamped=False`
        omits `variety`, which is what an un-stamped dispatch looks like and
        must stay legal: absence is the coverage gap itself."""
        fields = {"task_id": task_id, "ts": ts}
        if stamped:
            fields["variety"] = {
                "novelty": 2, "scope": 2, "uncertainty": 2, "risk": 2, "total": 8
            }
        return make_event("dispatch_site", **fields)

    def _resumed_two_arc_journal(self, journal_file):
        events = [
            # --- prior arc (5 dispatches) ---
            self._ds("8", self.ARC1_TS),
            self._ds("10", self.ARC1_TS),
            self._ds("12", self.ARC1_TS),
            self._ds("13", self.ARC1_TS),  # prior-arc-only
            self._ds("16", self.ARC1_TS),
            # --- current arc boundary: the feature's variety_assessed ---
            make_event(
                "variety_assessed",
                task_id="100",
                variety={
                    "novelty": 2,
                    "scope": 2,
                    "uncertainty": 2,
                    "risk": 2,
                    "total": 8,
                },
                ts=self.ARC2_START,
            ),
            # --- current arc (4 dispatches) — REUSES ids 8/10/12 ---
            self._ds("8", self.ARC2_TS),
            self._ds("10", self.ARC2_TS),
            self._ds("12", self.ARC2_TS),
            self._ds("14", self.ARC2_TS),
        ]
        _write_journal(journal_file, events)

    def test_unscoped_denominator_conflates_both_arcs(
        self, journal_home, session_dir, journal_file
    ):
        """Baseline (the bug): without --since the denominator counts BOTH
        arcs = 9 (the #960 observed value)."""
        from shared.session_journal import read_events_from

        self._resumed_two_arc_journal(journal_file)
        sites = read_events_from(session_dir, event_type="dispatch_site")
        assert len(sites) == 9


class TestReadLastSinceParity:
    """read-last --since parity (#963 — devops uncertainty #3, no production
    caller yet but parity-complete with the read path)."""

    def test_current_arc_match_returned(
        self, journal_home, session_dir, journal_file
    ):
        from shared.session_journal import read_last_event_from

        events = [
            make_event("checkpoint", phase="PRIOR_ARC", ts="2026-06-13T12:00:00Z"),
            make_event("checkpoint", phase="CURRENT_ARC", ts="2026-06-14T12:00:00Z"),
        ]
        _write_journal(journal_file, events)
        ev = read_last_event_from(
            session_dir, "checkpoint", since="2026-06-14T09:00:00Z"
        )
        assert ev is not None
        assert ev["phase"] == "CURRENT_ARC"


class TestReadJsonArrayParseContract:
    """The `read` CLI emits a SINGLE JSON array (#966). A line-by-line
    (JSONL) parse misreads it — the masked false-absence the #966 guidance
    fixes. Demonstrates why `events = json.loads(output)` (iterate the list)
    is required and a per-line parse is wrong."""

    def test_read_emits_single_json_array_iterable_as_list(
        self, journal_home, session_dir, journal_file
    ):
        events = [
            make_event(
                "dispatch_variety",
                task_id="8",
                variety={
                    "novelty": 1,
                    "scope": 2,
                    "uncertainty": 1,
                    "risk": 2,
                    "total": 6,
                },
                ts="2026-06-15T10:00:00Z",
            ),
            make_event(
                "dispatch_variety",
                task_id="11",
                variety={
                    "novelty": 2,
                    "scope": 2,
                    "uncertainty": 2,
                    "risk": 2,
                    "total": 8,
                },
                ts="2026-06-15T11:00:00Z",
            ),
        ]
        _write_journal(journal_file, events)

        result = subprocess.run(
            [
                sys.executable,
                _SJ_SCRIPT,
                "read",
                "--session-dir",
                session_dir,
                "--type",
                "dispatch_variety",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 0, result.stderr

        # CORRECT parse: a single JSON array of N event dicts.
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert [e["variety"]["total"] for e in parsed] == [6, 8]


class TestArcScopingMalformedResilience:
    """F2: read --since over a journal MIXING a malformed line + a missing-ts
    event + valid events — the composition the unit tests cover only
    separately. Confirms malformed lines are skipped UPSTREAM of the _ts_ge
    arc-filter, a missing-ts event is fail-open INCLUDED (never silently
    dropped from a scoped read), and valid events are ts-scoped correctly."""

    def test_since_over_malformed_and_missing_ts_journal(
        self, journal_home, session_dir, journal_file
    ):
        from shared.session_journal import read_events_from

        arc_start = "2026-06-14T09:00:00Z"
        valid_prior = make_event(
            "agent_dispatch", agent="a", task_id="1", phase="CODE",
            ts="2026-06-13T12:00:00Z",  # before arc_start → excluded
        )
        valid_cur1 = make_event(
            "agent_dispatch", agent="b", task_id="2", phase="CODE",
            ts="2026-06-14T12:00:00Z",  # in arc → included
        )
        valid_cur2 = make_event(
            "agent_dispatch", agent="c", task_id="3", phase="TEST",
            ts="2026-06-14T13:00:00Z",  # in arc → included
        )
        # raw dict with NO ts → _ts_ge fail-open INCLUDES it (make_event would
        # auto-stamp a ts, so the missing-ts case is built by hand)
        missing_ts = {
            "v": 1, "type": "agent_dispatch", "agent": "d",
            "task_id": "4", "phase": "CODE",
        }
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        journal_file.write_text(
            "\n".join(
                [
                    json.dumps(valid_prior, separators=(",", ":")),
                    "this is not json {{{",  # malformed → skipped
                    json.dumps(missing_ts, separators=(",", ":")),
                    json.dumps(valid_cur1, separators=(",", ":")),
                    json.dumps(valid_cur2, separators=(",", ":")),
                ]
            )
            + "\n"
        )

        events = read_events_from(
            session_dir, event_type="agent_dispatch", since=arc_start
        )
        task_ids = sorted((e["task_id"] for e in events), key=int)
        # prior(1) excluded by ts; malformed skipped; missing-ts(4) fail-open
        # included; current(2,3) ts-scoped in
        assert task_ids == ["2", "3", "4"]


class TestJournalTeachExamples:
    # Full example lines, content-pinned. prog is the invoked script path
    # (_SJ_SCRIPT), so each rendered example is the exact pasteable string:
    # interpreter-prefixed, quoted script path, concrete flags. The epilog is
    # unwrapped (RawDescriptionHelpFormatter), so a full-line assertion is
    # width-safe at any COLUMNS.
    _EXAMPLES = {
        "write": (
            f'python3 "{_SJ_SCRIPT}" write'
            " --type decision --session-dir /abs/session --stdin"
        ),
        "read": f'python3 "{_SJ_SCRIPT}" read --session-dir /abs/session',
        "read-last": (
            f'python3 "{_SJ_SCRIPT}" read-last'
            " --type phase_transition --session-dir /abs/session"
        ),
    }

    def test_top_level_help_lists_every_example_line(self):
        result = subprocess.run(
            [sys.executable, _SJ_SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        for line in self._EXAMPLES.values():
            assert line in result.stdout, line


# resolve_arc_start: the --since arc-boundary resolver (moved with the divergence module's retirement)
from shared.session_journal import resolve_arc_start  # noqa: E402


class TestResolveArcStart:
    """resolve_arc_start(variety_assessed_events, feature_task_id): the LATEST
    variety_assessed.ts matching feature_task_id (the arc-start for --since),
    parse-not-lexical, fail-open None. Pure function, journal-event inputs."""

    def _va(self, task_id, ts):
        return make_event(
            "variety_assessed",
            task_id=task_id,
            variety={
                "novelty": 2,
                "scope": 2,
                "uncertainty": 2,
                "risk": 2,
                "total": 8,
            },
            ts=ts,
        )

    def test_returns_latest_ts_for_matching_feature_across_arcs(self):
        """The platform reuses task_ids across arcs, so the feature's id can
        match a PRIOR arc's variety_assessed too; the LATEST-ts match is the
        current arc. (Non-vacuity: a latest→earliest mutation returns the
        prior-arc ts and fails this.)"""
        events = [
            self._va("100", "2026-06-13T12:00:00Z"),  # prior arc
            self._va("100", "2026-06-14T12:00:00Z"),  # current arc
        ]
        assert resolve_arc_start(events, "100") == "2026-06-14T12:00:00Z"


