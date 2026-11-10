"""
Tests for pact-plugin/telegram/routing.py

Tests cover:
1. UpdateRouter ABC: interface contract verification
2. DirectRouter: pass-through behavior, single-session operation
3. FileBasedRouter: lock acquisition, session management, polling, routing,
   inbox fan-out, offset persistence, cleanup, crash recovery
4. count_active_sessions: session counting with stale cleanup
5. Session ID generation: config.py get_or_create_session_id
6. Edge cases: corrupted files, concurrent access, lock contention
"""

import fcntl
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram.routing import (
    DirectRouter,
    FileBasedRouter,
    register_session,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_client():
    """Create a mocked TelegramClient for router tests."""
    client = MagicMock()
    client.get_updates = AsyncMock(return_value=[])
    client.send_message = AsyncMock(return_value={"message_id": 1})
    client.close = AsyncMock()
    client.extract_reply_to_message_id = MagicMock(return_value=None)
    client._update_offset = 0
    return client


@pytest.fixture
def coordinator_dir(tmp_path):
    """Create a temporary coordinator directory structure."""
    coord = tmp_path / "coordinator"
    coord.mkdir()
    (coord / "sessions").mkdir()
    (coord / "updates").mkdir()
    return coord


@pytest.fixture
def make_session_file(coordinator_dir):
    """Factory to create session files for testing."""

    def _make(session_id, pid=None, registered_at=None, last_heartbeat=None):
        if pid is None:
            pid = os.getpid()
        if registered_at is None:
            registered_at = time.time()
        if last_heartbeat is None:
            last_heartbeat = time.time()

        data = {
            "pid": pid,
            "project": "/test/project",
            "registered_at": registered_at,
            "last_heartbeat": last_heartbeat,
        }
        session_file = coordinator_dir / "sessions" / f"{session_id}.json"
        session_file.write_text(json.dumps(data), encoding="utf-8")
        return session_file

    return _make


# =============================================================================
# DirectRouter Tests
# =============================================================================


class TestDirectRouter:
    """Tests for DirectRouter -- zero-overhead single-session pass-through."""

    @pytest.mark.asyncio
    async def test_start_stores_session_id(self, mock_client):
        """Should store session_id on start."""
        router = DirectRouter(mock_client)
        await router.start("session-abc")
        assert router._session_id == "session-abc"


# =============================================================================
# FileBasedRouter -- Lifecycle Tests
# =============================================================================


class TestFileBasedRouterLifecycle:
    """Tests for FileBasedRouter lifecycle: start, stop, directory setup."""

    @pytest.mark.asyncio
    async def test_start_creates_coordinator_dirs(self, mock_client, tmp_path):
        """Should create coordinator directory structure on start."""
        coord = tmp_path / "new_coordinator"
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coord
        )

        await router.start("sess-1")

        assert (coord / "sessions").exists()
        assert (coord / "updates").exists()
        assert router._running is True

        await router.stop()


# =============================================================================
# FileBasedRouter -- Lock Management Tests
# =============================================================================


class TestFileBasedRouterLock:
    """Tests for FileBasedRouter lock acquisition and release."""

    @pytest.mark.asyncio
    async def test_first_session_becomes_poller(self, mock_client, coordinator_dir):
        """Should acquire lock and become poller when no other session holds it."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        assert router.is_poller is True
        assert router._lock_fd is not None

        await router.stop()


# =============================================================================
# FileBasedRouter -- Polling and Routing Tests
# =============================================================================


class TestFileBasedRouterPolling:
    """Tests for FileBasedRouter poll_and_route behavior."""

    @pytest.mark.asyncio
    async def test_poller_gets_updates_from_client(
        self, mock_client, coordinator_dir
    ):
        """Should call client.get_updates when acting as poller."""
        mock_client.get_updates.return_value = [
            {"update_id": 1, "message": {"text": "hello"}}
        ]
        mock_client._update_offset = 2

        # Register session first (as server.py would) so routing can find it
        register_session("sess-1", coordinator_dir=coordinator_dir)

        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")
        assert router.is_poller

        updates = await router.get_updates(timeout=30)

        mock_client.get_updates.assert_awaited_once_with(timeout=30)
        assert len(updates) == 1

        await router.stop()


# =============================================================================
# FileBasedRouter -- Reader Behavior Tests
# =============================================================================


class TestFileBasedRouterReader:
    """Tests for FileBasedRouter reader (non-poller) behavior."""

    @pytest.mark.asyncio
    async def test_reader_reads_from_inbox(self, mock_client, coordinator_dir):
        """Should read updates from inbox file when acting as reader."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()
        router._session_id = "sess-1"
        router._is_poller = False
        router._running = True

        # Write updates to inbox
        inbox = coordinator_dir / "updates" / "sess-1.jsonl"
        updates_data = [
            {"update_id": 1, "message": {"text": "msg1"}},
            {"update_id": 2, "message": {"text": "msg2"}},
        ]
        content = "\n".join(json.dumps(u) for u in updates_data) + "\n"
        inbox.write_text(content)

        # Hold lock externally to prevent promotion
        lock_path = coordinator_dir / "poll.lock"
        external_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(external_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            updates = await router.get_updates(timeout=30)

            assert len(updates) == 2
            assert updates[0]["message"]["text"] == "msg1"
            assert updates[1]["message"]["text"] == "msg2"

            # Inbox should be cleared after reading
            assert not inbox.exists()
        finally:
            fcntl.flock(external_fd, fcntl.LOCK_UN)
            os.close(external_fd)


# =============================================================================
# FileBasedRouter -- Register Message Tests
# =============================================================================


class TestFileBasedRouterRegisterMessage:
    """Tests for routing table management via register_message."""

    @pytest.mark.asyncio
    async def test_register_message_adds_to_routing_table(
        self, mock_client, coordinator_dir
    ):
        """Should add message_id -> session_id mapping to routing table."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        await router.start("sess-1")

        await router.register_message(42)

        routing_path = coordinator_dir / "routing-table.json"
        assert routing_path.exists()
        table = json.loads(routing_path.read_text())
        assert table["42"] == "sess-1"

        await router.stop()


# =============================================================================
# FileBasedRouter -- Session Management Tests
# =============================================================================


class TestFileBasedRouterSessions:
    """Tests for session file management and stale session cleanup."""

    def test_cleanup_removes_dead_pid_sessions(
        self, mock_client, coordinator_dir, make_session_file
    ):
        """Should remove session files for processes that no longer exist."""
        # Create a session file with a non-existent PID
        make_session_file("dead-sess", pid=99999999, last_heartbeat=time.time())

        router = FileBasedRouter(
            mock_client, session_id="my-sess", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()

        router._cleanup_stale_sessions()

        dead_file = coordinator_dir / "sessions" / "dead-sess.json"
        assert not dead_file.exists()


# =============================================================================
# FileBasedRouter -- Heartbeat Tests
# =============================================================================


class TestFileBasedRouterHeartbeat:
    """Tests for session heartbeat mechanism."""

    def test_update_heartbeat_refreshes_timestamp(
        self, mock_client, coordinator_dir
    ):
        """Should update the last_heartbeat in session file."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )
        router._ensure_coordinator_dirs()
        router._session_id = "sess-1"

        # Write initial session file with old heartbeat
        initial_time = time.time() - 100
        session_file = coordinator_dir / "sessions" / "sess-1.json"
        session_file.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "project": "/test",
                    "registered_at": initial_time,
                    "last_heartbeat": initial_time,
                }
            )
        )

        router._update_heartbeat()

        data = json.loads(session_file.read_text())
        assert data["last_heartbeat"] > initial_time


# =============================================================================
# FileBasedRouter -- Offset Management Tests
# =============================================================================


class TestFileBasedRouterOffset:
    """Tests for shared polling offset persistence."""

    def test_read_offset_returns_zero_when_no_file(
        self, mock_client, coordinator_dir
    ):
        """Should return 0 when offset file doesn't exist."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        assert router._read_offset() == 0


# =============================================================================
# FileBasedRouter -- Routing Table Tests
# =============================================================================


class TestFileBasedRouterRoutingTable:
    """Tests for shared routing table I/O."""

    def test_read_routing_table_returns_empty_when_no_file(
        self, mock_client, coordinator_dir
    ):
        """Should return empty dict when routing table file doesn't exist."""
        router = FileBasedRouter(
            mock_client, session_id="sess-1", coordinator_dir=coordinator_dir
        )

        assert router._read_routing_table() == {}


# =============================================================================
# FileBasedRouter -- Inbox Append Tests
# =============================================================================


# =============================================================================
# FileBasedRouter -- Atomic Write Tests
# =============================================================================


# =============================================================================
# count_active_sessions Tests
# =============================================================================


# =============================================================================
# Session ID Generation Tests
# =============================================================================


# =============================================================================
# FileBasedRouter -- Route Update Logic Tests
# =============================================================================


# =============================================================================
# Single-Session Fast-Path Tests
# =============================================================================


# =============================================================================
# FileBasedRouter -- Directory Permissions Tests
# =============================================================================


# =============================================================================
# FileBasedRouter -- Path Property Tests
# =============================================================================


