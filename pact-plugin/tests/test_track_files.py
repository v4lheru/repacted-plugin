"""
Tests for track_files.py — PostToolUse hook that tracks files modified during
the session for the memory system's graph network.

Tests cover:
1. Timestamps use timezone-aware UTC (not naive datetime.utcnow())
2. Basic track/load/save cycle works
3. File locking is present in load/save (structural test via mock)
4. Update existing entry updates timestamp
5. main() entry point: stdin JSON parsing, exit codes
6. Atomic read-modify-write: track_file() holds single lock for full cycle
7. _update_data() pure function: new entry, update existing, no side effects
"""
import io
import json
import os
import sys
from unittest.mock import patch

import pytest


class TestTrackFilesTimestamps:
    """H4: Verify timestamps use timezone-aware UTC, not deprecated utcnow()."""

    def test_new_file_timestamps_are_timezone_aware(self, tmp_path):
        """first_seen and last_modified must contain timezone info ('+00:00')."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("/src/app.py", "Edit")

        data = json.loads(tracking_file.read_text())
        entry = data["files"][0]

        # Timezone-aware ISO format includes '+00:00' suffix
        assert "+00:00" in entry["first_seen"], (
            f"first_seen lacks timezone info: {entry['first_seen']}"
        )
        assert "+00:00" in entry["last_modified"], (
            f"last_modified lacks timezone info: {entry['last_modified']}"
        )

    def test_updated_file_timestamp_is_timezone_aware(self, tmp_path):
        """When updating an existing entry, last_modified must be timezone-aware."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("/src/app.py", "Edit")
            track_files.track_file("/src/app.py", "Write")

        data = json.loads(tracking_file.read_text())
        entry = data["files"][0]

        assert "+00:00" in entry["last_modified"], (
            f"Updated last_modified lacks timezone info: {entry['last_modified']}"
        )


class TestTrackFilesCycle:
    """Basic track/load/save cycle."""

    def test_track_new_file(self, tmp_path):
        """Tracking a new file creates an entry with correct fields."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("/src/auth.ts", "Edit")

        data = json.loads(tracking_file.read_text())
        assert len(data["files"]) == 1
        assert data["files"][0]["path"] == "/src/auth.ts"
        assert data["files"][0]["tool"] == "Edit"

    def test_track_updates_existing_file(self, tmp_path):
        """Tracking same file again updates timestamp, no duplicate entry."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("/src/auth.ts", "Edit")
            track_files.track_file("/src/auth.ts", "Write")

        data = json.loads(tracking_file.read_text())
        assert len(data["files"]) == 1
        assert data["files"][0]["tool"] == "Write"


class TestTrackFilesLocking:
    """H5: writes hold the state-file sidecar lock; reads take none."""

    @staticmethod
    def _spy_flock(monkeypatch):
        from shared import state_file

        calls = []
        real_flock = state_file.fcntl.flock

        def spy(fd, op):
            calls.append((op, os.fstat(fd).st_ino))
            return real_flock(fd, op)

        monkeypatch.setattr(state_file.fcntl, "flock", spy)
        return calls

    def test_save_uses_flock(self, tmp_path, monkeypatch):
        """save_tracked_files holds LOCK_EX on the sidecar and releases it."""
        import fcntl
        import track_files

        tracking_file = tmp_path / "test-session.json"
        calls = self._spy_flock(monkeypatch)
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.save_tracked_files({"files": [], "session_id": "test"})

        sidecar = (tmp_path / "test-session.json.lock").stat().st_ino
        assert [op for op, _ in calls] == [fcntl.LOCK_EX, fcntl.LOCK_UN]
        assert all(inode == sidecar for _, inode in calls)


class TestUpdateData:
    """Tests for _update_data() pure function."""

    def test_adds_new_entry(self):
        """New file path creates an entry with correct fields."""
        import track_files

        data = {"files": [], "session_id": "test"}
        result = track_files._update_data(data, "/src/app.py", "Edit")

        assert len(result["files"]) == 1
        assert result["files"][0]["path"] == "/src/app.py"
        assert result["files"][0]["tool"] == "Edit"
        assert "+00:00" in result["files"][0]["first_seen"]


class TestTrackFileAtomicity:
    """Verify track_file() does atomic read-modify-write under single lock."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="fcntl not available on Windows"
    )
    def test_lock_released_on_write_exception(self, tmp_path):
        """Lock must be released if an exception occurs during track_file write."""
        import fcntl
        import track_files

        tracking_file = tmp_path / "test-session.json"
        # An existing file, so the failing update runs under the sidecar lock.
        tracking_file.write_text(json.dumps({"files": [], "session_id": "test"}))

        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            with patch("track_files.json.dumps", side_effect=RuntimeError("boom")):
                try:
                    track_files.track_file("/src/app.py", "Edit")
                except (RuntimeError, IOError):
                    pass

        # If the sidecar lock was released, it can be taken without blocking
        sidecar = tmp_path / "test-session.json.lock"
        assert sidecar.exists()
        with open(sidecar, "r") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)


class TestTrackFilesMain:
    """Tests for main() entry point."""

    def test_main_tracks_edit_tool(self, tmp_path):
        """main() processes Edit tool input correctly."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        input_data = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/src/app.py"},
        })

        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                track_files.main()
            assert exc_info.value.code == 0

        data = json.loads(tracking_file.read_text())
        assert len(data["files"]) == 1


