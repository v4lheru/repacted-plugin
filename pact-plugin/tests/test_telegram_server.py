"""
Tests for pact-plugin/telegram/server.py

Tests cover:
1. create_server: MCP server creation with all tools registered
2. lifespan: startup/shutdown lifecycle, graceful no-op when unconfigured
3. _polling_loop: background polling, error backoff
4. _process_update: update routing, callback query handling, voice transcription
5. Integration: reply routing to pending Futures, fallback to first pending
"""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock the mcp module tree before importing server.py (mcp may not be installed)
if "mcp" not in sys.modules:
    _mcp = ModuleType("mcp")
    _mcp_server = ModuleType("mcp.server")
    _mcp_server_fastmcp = ModuleType("mcp.server.fastmcp")

    class _FakeFastMCP:
        """Minimal stub for FastMCP to allow server.py to load."""
        def __init__(self, name, **kwargs):
            self.name = name
            self._lifespan = kwargs.get("lifespan")
            self.instructions = kwargs.get("instructions")
        def tool(self, **kwargs):
            def decorator(fn):
                return fn
            return decorator
        def run(self, **kwargs):
            pass

    _mcp_server_fastmcp.FastMCP = _FakeFastMCP
    _mcp.server = _mcp_server
    _mcp_server.fastmcp = _mcp_server_fastmcp
    sys.modules["mcp"] = _mcp
    sys.modules["mcp.server"] = _mcp_server
    sys.modules["mcp.server.fastmcp"] = _mcp_server_fastmcp

from telegram.routing import DirectRouter
from telegram.server import (
    _process_update,
    _extract_message_id,
    _polling_loop,
    create_server,
    lifespan,
    ERROR_BACKOFF,
)
from telegram.tools import ToolContext
from telegram.voice import VoiceTranscriptionError


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def tool_context():
    """Create a ToolContext with mocked client and voice.

    The TelegramClient has both sync (static) and async methods.
    We use MagicMock for the base and override async methods explicitly.
    """
    ctx = ToolContext()
    ctx.configured = True
    ctx.config = {"mode": "passive", "warnings": []}

    # Use MagicMock for sync methods; override async methods explicitly
    client = MagicMock()
    client.get_updates = AsyncMock(return_value=[])
    client.send_message = AsyncMock(return_value={"message_id": 1})
    client.send_message_with_buttons = AsyncMock(return_value={"message_id": 1})
    client.answer_callback_query = AsyncMock(return_value=True)
    client.get_file = AsyncMock(return_value={})
    client.close = AsyncMock()
    ctx.client = client

    voice = MagicMock()
    voice.is_available.return_value = True
    voice.transcribe_voice_message = AsyncMock(return_value="Transcribed text")
    voice.close = AsyncMock()
    ctx.voice = voice
    return ctx


# =============================================================================
# create_server Tests
# =============================================================================

class TestCreateServer:
    """Tests for create_server -- MCP server setup."""

    def test_creates_server(self):
        """Should return a FastMCP server instance with instructions."""
        server = create_server()
        assert server is not None
        assert server.name == "pact-telegram"
        assert server.instructions is not None
        # All 4 tool names must appear in the instructions
        for tool_name in ("telegram_notify", "telegram_ask", "telegram_check_replies", "telegram_status"):
            assert tool_name in server.instructions, f"{tool_name} missing from instructions"


# =============================================================================
# _process_update Tests
# =============================================================================

class TestProcessUpdate:
    """Tests for _process_update -- routing individual updates."""

    @pytest.mark.asyncio
    async def test_resolves_reply_by_reply_to_id(self, tool_context):
        """Should resolve pending Future when reply_to_message_id matches."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[42] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 42
        tool_context.client.extract_text.return_value = "user reply"
        tool_context.client.extract_voice.return_value = None

        update = {"message": {"text": "user reply", "reply_to_message": {"message_id": 42}}}

        await _process_update(tool_context, update)

        assert future.result() == "user reply"


# =============================================================================
# _polling_loop Tests
# =============================================================================

class TestPollingLoop:
    """Tests for _polling_loop -- background polling with error handling."""

    @pytest.mark.asyncio
    async def test_polls_and_processes_updates(self, tool_context):
        """Should poll for updates and process each one."""
        call_count = 0

        async def fake_get_updates(timeout=30):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"update_id": 1, "message": {"text": "hi"}}]
            # Stop the loop after first iteration
            raise asyncio.CancelledError()

        tool_context.client.get_updates = fake_get_updates
        router = DirectRouter(tool_context.client)

        with patch("telegram.server._process_update", new_callable=AsyncMock) as mock_process:
            with pytest.raises(asyncio.CancelledError):
                await _polling_loop(tool_context, router)

        mock_process.assert_awaited_once()


# =============================================================================
# lifespan Tests
# =============================================================================

class TestLifespan:
    """Tests for lifespan -- MCP server startup/shutdown lifecycle."""

    @pytest.mark.asyncio
    async def test_lifespan_configured_starts_polling(self):
        """Should start polling task when config is available."""
        fake_config = {
            "bot_token": "123:ABC",
            "chat_id": "456",
            "mode": "passive",
            "openai_api_key": None,
        }

        mock_router = AsyncMock()

        with patch("telegram.server.load_config_safe", return_value=fake_config), \
             patch("telegram.server.get_context") as mock_get_ctx, \
             patch("telegram.server._polling_loop", new_callable=AsyncMock) as mock_poll, \
             patch("telegram.server.get_or_create_session_id", return_value="test-session"), \
             patch("telegram.server.register_session"), \
             patch("telegram.server.count_active_sessions", return_value=1), \
             patch("telegram.server.FileBasedRouter", return_value=mock_router), \
             patch("telegram.server.unregister_session"):
            ctx = MagicMock()
            ctx.close = AsyncMock()
            mock_get_ctx.return_value = ctx

            server = MagicMock()
            async with lifespan(server) as state:
                assert state == {}
                ctx.initialize.assert_called_once_with(fake_config)


# =============================================================================
# Additional _process_update Edge Cases
# =============================================================================

class TestProcessUpdateEdgeCases:
    """Additional edge case tests for _process_update."""

    @pytest.fixture
    def tool_context(self):
        """Create a ToolContext with mocked client and voice."""
        ctx = ToolContext()
        ctx.configured = True
        ctx.config = {"mode": "passive", "warnings": []}

        client = MagicMock()
        client.get_updates = AsyncMock(return_value=[])
        client.send_message = AsyncMock(return_value={"message_id": 1})
        client.answer_callback_query = AsyncMock(return_value=True)
        client.close = AsyncMock()
        ctx.client = client

        voice = MagicMock()
        voice.is_available.return_value = True
        voice.transcribe_voice_message = AsyncMock(return_value="Transcribed text")
        voice.close = AsyncMock()
        ctx.voice = voice
        return ctx

    @pytest.mark.asyncio
    async def test_voice_without_file_id_skips_transcription(self, tool_context):
        """Should skip transcription when voice note has no file_id."""
        future = asyncio.get_event_loop().create_future()
        tool_context.pending_replies[60] = future

        tool_context.client.extract_callback_query_id.return_value = None
        tool_context.client.extract_reply_to_message_id.return_value = 60
        tool_context.client.extract_text.return_value = None
        # Voice metadata without file_id
        tool_context.client.extract_voice.return_value = {
            "duration": 5,
        }

        update = {
            "message": {
                "voice": {"duration": 5},
                "reply_to_message": {"message_id": 60},
            }
        }

        await _process_update(tool_context, update)

        # Transcription should not be attempted (no file_id)
        tool_context.voice.transcribe_voice_message.assert_not_awaited()
        # Future should not be resolved (no content extracted)
        assert not future.done()


# =============================================================================
# _extract_message_id Tests
# =============================================================================


# =============================================================================
# Reply Queue Routing Tests (server-side)
# =============================================================================

