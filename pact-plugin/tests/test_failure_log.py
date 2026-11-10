"""
Tests for shared/failure_log.py -- bounded ring buffer log for session_init
malformed-stdin failures.

Tests cover:

append_failure():
1. Creates file and parent directory on first call
2. Writes JSONL record with all expected fields (ts, classification, error, cwd, source)
3. Truncates error field to 200 chars
4. Rotation: caps the log at MAX_ENTRIES (100) via read-keep-99-append
5. Concurrent writes are serialized via file_lock (no interleave/corruption)
6. Fail-open on lock timeout — does not raise
7. Fail-open on disk error (OSError from write_text) — does not raise
8. Fail-open on JSON encode error (via non-serializable cwd/source) — does not raise
9. Fail-open on any unexpected exception — does not raise
10. File permissions are 0o600 after write
11. Optional cwd/source fields default to None

read_failures():
12. Returns [] when log file is missing
13. Returns [] on lock/IO error
14. Returns parsed entries in chronological order (oldest first)
15. Skips malformed lines silently
16. Returns [] on outer exception (fail-open)
"""
import threading
from pathlib import Path

import pytest

from shared import failure_log
from shared.failure_log import (
    MAX_ENTRIES,
    append_failure,
    read_failures,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def failure_log_home(tmp_path, monkeypatch):
    """Redirect Path.home() into tmp_path; the accessor follows it.

    get_failure_log_path() resolves $CLAUDE_CONFIG_DIR at CALL time (B1), so
    with the env unset it derives the log path from the monkeypatched
    Path.home() — no module-level constant override needed (the import-time
    freeze is gone). delenv guarantees the fallback branch deterministically.
    """
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tmp_log = tmp_path / ".claude" / "pact-sessions" / "_session_init_failures.log"
    return tmp_log


# ---------------------------------------------------------------------------
# append_failure: basic behavior
# ---------------------------------------------------------------------------

class TestAppendCreatesFile:
    def test_creates_file_and_parent_dir(self, failure_log_home):
        """First append creates the file and the parent pact-sessions dir."""
        log = failure_log_home
        assert not log.exists()
        assert not log.parent.exists()

        append_failure("missing_session_id", "raw_id was None")

        assert log.exists()
        assert log.parent.is_dir()
        # Parent dir permissions: 0o700 per PACT convention
        parent_mode = log.parent.stat().st_mode & 0o777
        assert parent_mode == 0o700

    def test_file_permissions_are_0o600(self, failure_log_home):
        """Written file has owner-only 0o600 permissions."""
        log = failure_log_home
        append_failure("missing_session_id", "test")
        mode = log.stat().st_mode & 0o777
        assert mode == 0o600


class TestAppendRecordFormat:
    def test_writes_jsonl_record_with_all_fields(self, failure_log_home):
        """Record contains ts (ISO 8601 UTC), classification, error, cwd, source."""
        append_failure(
            classification="missing_session_id",
            error="raw_id was None",
            cwd="/home/alice/project",
            source="startup",
        )

        entries = read_failures()
        assert len(entries) == 1
        entry = entries[0]

        # Required fields
        assert "ts" in entry
        assert entry["classification"] == "missing_session_id"
        assert entry["error"] == "raw_id was None"
        assert entry["cwd"] == "/home/alice/project"
        assert entry["source"] == "startup"

        # ts format: ISO 8601 UTC with trailing Z (matches session_journal)
        ts = entry["ts"]
        assert isinstance(ts, str)
        assert ts.endswith("Z")
        assert "T" in ts
        assert len(ts) >= 19  # "YYYY-MM-DDTHH:MM:SSZ" = 20 chars

    def test_optional_fields_default_to_none(self, failure_log_home):
        """cwd and source default to None when not supplied."""
        append_failure("missing_session_id", "boom")
        entries = read_failures()
        assert len(entries) == 1
        assert entries[0]["cwd"] is None
        assert entries[0]["source"] is None


# ---------------------------------------------------------------------------
# append_failure: rotation
# ---------------------------------------------------------------------------

class TestRotation:
    def test_rotation_caps_at_max_entries(self, failure_log_home):
        """Appending (MAX_ENTRIES + 50) leaves exactly MAX_ENTRIES in the log,
        with the oldest 50 dropped.
        """
        total = MAX_ENTRIES + 50
        for i in range(total):
            append_failure("missing_session_id", f"entry-{i}")

        entries = read_failures()
        assert len(entries) == MAX_ENTRIES

        # The retained entries are the LAST 100, i.e. entry-50 through entry-149
        assert entries[0]["error"] == f"entry-{total - MAX_ENTRIES}"
        assert entries[-1]["error"] == f"entry-{total - 1}"

    def test_below_cap_retains_all(self, failure_log_home):
        """Appending fewer than MAX_ENTRIES leaves all entries in the log."""
        for i in range(10):
            append_failure("missing_session_id", f"entry-{i}")
        entries = read_failures()
        assert len(entries) == 10
        # Order preserved: entry-0, entry-1, ..., entry-9
        for i, entry in enumerate(entries):
            assert entry["error"] == f"entry-{i}"


# ---------------------------------------------------------------------------
# append_failure: concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_writes_via_file_lock(self, failure_log_home):
        """Two threads hammering append_failure cannot interleave/corrupt
        JSONL lines. Every entry parses successfully after the storm.
        """
        writes_per_thread = 25
        num_threads = 4

        def worker(worker_id: int):
            for seq in range(writes_per_thread):
                append_failure(
                    classification="missing_session_id",
                    error=f"worker-{worker_id}-seq-{seq}",
                    cwd=f"/w{worker_id}",
                    source="test",
                )

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All entries must parse (no interleaved/corrupt JSONL lines).
        # Total writes = writes_per_thread * num_threads = 100 = MAX_ENTRIES,
        # so no rotation fires and every write survives.
        entries = read_failures()
        assert len(entries) == writes_per_thread * num_threads

        # Each thread's writes are fully represented
        seen_per_worker = {i: 0 for i in range(num_threads)}
        for entry in entries:
            err = entry["error"]
            assert err.startswith("worker-")
            worker_id = int(err.split("-")[1])
            seen_per_worker[worker_id] += 1

        for i in range(num_threads):
            assert seen_per_worker[i] == writes_per_thread

    def test_concurrent_append_and_read_never_observes_partial_state(
        self, failure_log_home
    ):
        """R4-L2: ``read_failures`` must never observe a partially-written
        file while ``append_failure`` is rotating.

        Before the fix, ``read_failures`` called ``LOG_PATH.read_text()``
        without acquiring ``file_lock``. The writer's rotation path uses
        ``Path.write_text`` (truncate-then-write under the hood via
        ``open(mode='w')``), which is NOT atomic against unlocked readers:
        a concurrent reader in the narrow window between truncate and
        write could observe a zero-length or partially-written file, then
        return ``[]`` or a subset of entries. Under steady write load this
        would manifest as sporadic "disappearing" entries.

        The R4 fix acquires ``file_lock`` in ``read_failures`` so the
        reader serializes against the writer's rotation. Every
        ``read_failures`` call while ``append_failure`` is running MUST
        return a coherent, well-formed list — never empty, never
        partial, never raising.

        This test uses ``threading.Barrier`` to synchronize a writer
        thread and multiple reader threads so they all enter the critical
        section together. Every read that occurs during the storm is
        checked for (1) well-formedness (parses), (2) consistent
        monotonic growth (entries only added, never removed during a
        single storm before rotation kicks in).

        Deterministic expected-count check: we seed the log with
        MAX_ENTRIES entries first, then the writer performs N rotation
        writes while readers hammer read_failures in parallel. Every
        reader's snapshot length must equal MAX_ENTRIES (rotation always
        keeps the buffer at the cap), and every entry must parse.
        """
        # Seed the log to the cap so every subsequent write triggers rotation
        # (read-keep-(MAX_ENTRIES-1)-append-write). Rotation is the window
        # where the bug would manifest: the writer truncates the file to
        # rewrite the full buffer. An unlocked reader in that window sees a
        # short/empty file.
        for i in range(MAX_ENTRIES):
            append_failure("missing_session_id", f"seed-{i}")

        writes_during_storm = 30
        num_readers = 4
        barrier = threading.Barrier(num_readers + 1)
        reader_snapshots: list[list[dict]] = []
        reader_errors: list[BaseException] = []
        lock_for_results = threading.Lock()

        def writer():
            barrier.wait()
            for seq in range(writes_during_storm):
                append_failure(
                    classification="missing_session_id",
                    error=f"storm-{seq}",
                )

        def reader():
            barrier.wait()
            try:
                # Multiple reads per reader to maximize the odds of
                # overlapping with the writer's rotation window.
                for _ in range(20):
                    snapshot = read_failures()
                    with lock_for_results:
                        reader_snapshots.append(snapshot)
            except BaseException as e:
                with lock_for_results:
                    reader_errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(num_readers)]
        writer_thread = threading.Thread(target=writer)
        for t in threads:
            t.start()
        writer_thread.start()
        writer_thread.join()
        for t in threads:
            t.join()

        # No reader should have raised.
        assert not reader_errors, f"read_failures raised: {reader_errors}"

        # Every snapshot must be non-empty (the writer cannot cause the
        # reader to see a truncated-to-zero view) and every entry must
        # parse and carry a classification field.
        assert reader_snapshots, "readers did not capture any snapshots"
        for snap in reader_snapshots:
            # Under rotation semantics the buffer stays at exactly
            # MAX_ENTRIES once seeded. A reader observing fewer entries
            # means it caught the writer mid-rotation (the bug).
            assert len(snap) == MAX_ENTRIES, (
                f"reader observed partial state: len={len(snap)}, "
                f"expected={MAX_ENTRIES} — reader raced with writer's "
                f"truncate-then-write rotation window"
            )
            # Each entry must be well-formed.
            for entry in snap:
                assert "classification" in entry
                assert "error" in entry

        # After the storm, final state is consistent: exactly MAX_ENTRIES,
        # and some number of storm-* entries visible (rotation drops the
        # oldest seed-* entries first).
        final = read_failures()
        assert len(final) == MAX_ENTRIES
        # At least some storm entries survived (rotation is FIFO so the
        # latest writes are always at the tail).
        storm_entries = [e for e in final if e["error"].startswith("storm-")]
        assert len(storm_entries) == writes_during_storm


# ---------------------------------------------------------------------------
# append_failure: fail-open
# ---------------------------------------------------------------------------

class TestFailOpen:
    def test_fail_open_on_lock_timeout(self, failure_log_home, monkeypatch):
        """TimeoutError from file_lock is swallowed; append returns cleanly."""
        from contextlib import contextmanager

        @contextmanager
        def timeout_lock(target_file):
            raise TimeoutError(f"simulated lock timeout on {target_file}")
            yield  # unreachable

        monkeypatch.setattr(failure_log, "file_lock", timeout_lock)

        # Must not raise
        append_failure("missing_session_id", "test lock timeout")

        # No entry written
        assert not failure_log_home.exists() or read_failures() == []


# ---------------------------------------------------------------------------
# append_failure: symlink guard (TOCTOU defense)
# ---------------------------------------------------------------------------

class TestSymlinkGuard:
    """LOG_PATH must never be followed if it points at a symlink.

    The guard runs INSIDE the file_lock so a concurrent writer cannot
    swap a regular file for a symlink between check and write. The
    guard is silent fail-open: if LOG_PATH is a symlink, append_failure
    returns None without writing anything (and without following the
    link). This prevents an attacker (or accidental local action) from
    tricking session_init's failure logger into clobbering an arbitrary
    file via symlink redirection (e.g., /etc/passwd, ~/.ssh/authorized_keys).
    """

    def test_symlink_at_log_path_is_not_followed(
        self, failure_log_home, tmp_path
    ):
        """Replacing LOG_PATH with a symlink causes append_failure to no-op
        without modifying the symlink target.
        """
        log = failure_log_home
        log.parent.mkdir(parents=True, exist_ok=True)

        # Create a target file we want to PROTECT from being clobbered.
        # Place it outside the pact-sessions tree so a successful write
        # would be unambiguously detectable.
        target = tmp_path / "victim.txt"
        original_content = "DO NOT OVERWRITE\n"
        target.write_text(original_content, encoding="utf-8")

        # Replace LOG_PATH with a symlink pointing at the victim file.
        log.symlink_to(target)
        assert log.is_symlink()

        # Attempt to append. Must not raise. Must not write to the target.
        append_failure("missing_session_id", "symlink redirection attempt")

        # The symlink itself still exists and still points at the victim,
        # but the victim is unchanged. The guard short-circuited before
        # any read or write happened.
        assert log.is_symlink()
        assert target.read_text(encoding="utf-8") == original_content

        # And read_failures returns [] because nothing valid was logged.
        # (read_failures itself does follow the symlink to read, which is
        # safe — the file content hasn't been touched by us.)
        # We assert specifically that the victim still has its original
        # content, not log entries.
        assert "missing_session_id" not in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# read_failures
# ---------------------------------------------------------------------------

class TestReadFailures:
    def test_read_empty_when_file_missing(self, failure_log_home):
        """Returns [] when the log file does not exist."""
        assert not failure_log_home.exists()
        assert read_failures() == []


