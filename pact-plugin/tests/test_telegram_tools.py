"""
Tests for pact-plugin/telegram/tools.py

Tests cover:
1. ToolContext: initialization, resolve_reply, close, pending future lifecycle
2. tool_telegram_notify: configured/unconfigured behavior, API errors
3. tool_telegram_ask: question sending, Future-based reply waiting, timeout,
   button truncation, timeout clamping
4. tool_telegram_status: configured/unconfigured status, uptime, voice, warnings
5. Security: no credential leaks in tool responses
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from telegram.tools import (
    ToolContext,
    get_context,
    tool_telegram_notify,
    tool_telegram_ask,
    tool_telegram_status,
    _get_project_name,
    DEFAULT_ASK_TIMEOUT,
    NOTIFY_RATE_LIMIT,
    MAX_PENDING_REPLIES,
    STALE_FUTURE_BUFFER,
)


# =============================================================================
# ToolContext Tests
# =============================================================================

class TestToolContext:
    """Tests for ToolContext -- shared state for MCP tool handlers."""

    def test_initial_state(self):
        """Should start unconfigured with no client."""
        ctx = ToolContext()
        assert ctx.configured is False
        assert ctx.client is None
        assert ctx.voice is None
        assert ctx.pending_replies == {}


class TestGetContext:
    """Tests for get_context -- global context accessor."""

    def test_returns_tool_context_instance(self):
        """Should return the global ToolContext instance."""
        ctx = get_context()
        assert isinstance(ctx, ToolContext)


# =============================================================================
# Session Prefix Tests
# =============================================================================

class TestSessionPrefix:
    """Tests for _get_project_name and _prepend_session_prefix."""

    def test_get_project_name_from_env(self):
        """Should return basename of CLAUDE_PROJECT_DIR."""
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/home/user/my-project"}):
            assert _get_project_name() == "my-project"


# =============================================================================
# tool_telegram_notify Tests
# =============================================================================

class TestToolTelegramNotify:
    """Tests for tool_telegram_notify -- one-way notification tool."""

    @pytest.mark.asyncio
    async def test_returns_not_configured_message(self):
        """Should return 'not configured' when context is not initialized."""
        ctx = ToolContext()
        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_notify("hello")

        assert "not configured" in result


# =============================================================================
# tool_telegram_ask Tests
# =============================================================================

class TestToolTelegramAsk:
    """Tests for tool_telegram_ask -- blocking question with Future-based reply."""

    @pytest.mark.asyncio
    async def test_returns_not_configured_message(self):
        """Should return 'not configured' when context is not initialized."""
        ctx = ToolContext()
        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_ask("question?")

        assert "not configured" in result


# =============================================================================
# tool_telegram_status Tests
# =============================================================================

class TestToolTelegramStatus:
    """Tests for tool_telegram_status -- bridge health check."""

    @pytest.mark.asyncio
    async def test_unconfigured_status(self):
        """Should report NOT CONFIGURED when not initialized."""
        ctx = ToolContext()
        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_status()

        assert "NOT CONFIGURED" in result
        assert "init telegram" in result


# =============================================================================
# Rate Limiting Tests
# =============================================================================

class TestNotifyRateLimit:
    """Tests for notification rate limiting."""

    def test_allows_calls_within_limit(self):
        """Should allow calls under the rate limit."""
        ctx = ToolContext()
        for _ in range(NOTIFY_RATE_LIMIT):
            assert ctx.check_notify_rate_limit() is True


# =============================================================================
# Stale Future Cleanup Tests
# =============================================================================

class TestStaleFutureCleanup:
    """Tests for stale pending reply cleanup."""

    def test_removes_stale_futures(self):
        """Should remove futures that exceeded timeout + buffer."""
        ctx = ToolContext()
        loop = asyncio.new_event_loop()

        future = loop.create_future()
        ctx.pending_replies[42] = future
        # Set timestamp far enough in the past to be stale
        ctx._pending_timestamps[42] = time.time() - DEFAULT_ASK_TIMEOUT - STALE_FUTURE_BUFFER - 1

        removed = ctx.cleanup_stale_futures()

        assert removed == 1
        assert 42 not in ctx.pending_replies
        assert 42 not in ctx._pending_timestamps
        assert future.cancelled()
        loop.close()


# =============================================================================
# Max Pending Replies Tests
# =============================================================================

class TestMaxPendingReplies:
    """Tests for max concurrent pending questions limit."""

    @pytest.mark.asyncio
    async def test_rejects_when_at_max_pending(self):
        """Should reject new questions when at MAX_PENDING_REPLIES."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.client = AsyncMock()

        # Fill pending replies to capacity
        loop = asyncio.get_event_loop()
        for i in range(MAX_PENDING_REPLIES):
            ctx.pending_replies[i] = loop.create_future()
            ctx._pending_timestamps[i] = time.time()

        with patch("telegram.tools._ctx", ctx):
            result = await tool_telegram_ask("one more?")

        assert "Too many pending questions" in result
        ctx.client.send_message.assert_not_awaited()


# =============================================================================
# Reply Queue Tests
# =============================================================================

class TestReplyQueue:
    """Tests for reply queue infrastructure on ToolContext."""

    def test_initial_queue_is_empty(self):
        """Should start with an empty reply queue."""
        ctx = ToolContext()
        assert len(ctx.reply_queue) == 0


# =============================================================================
# Notification Tracking Tests
# =============================================================================


# =============================================================================
# tool_telegram_check_replies Tests
# =============================================================================


# =============================================================================
# telegram_status Reply Queue Depth Tests
# =============================================================================

