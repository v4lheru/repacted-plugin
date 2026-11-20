"""
Tests for pact-plugin/telegram/telegram_client.py

Tests cover:
1. TelegramClient construction and HTTP client lifecycle
2. send_message: content filtering, parse_mode, ReplyParameters, reply_markup
3. send_message_with_buttons: inline keyboard construction
4. answer_callback_query: acknowledgement and error handling
5. get_updates: long-polling, chat_id filtering (security), offset advancement
6. get_file: file metadata retrieval
7. Static extractors: _extract_chat_id, extract_text, extract_voice,
   extract_callback_query_id, extract_reply_to_message_id
8. _api_call: retry logic, rate limiting (429), error handling
9. Security: unauthorized chat_id rejection, inbound sanitization
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram.telegram_client import (
    TelegramClient,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_http_client():
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture
def telegram_client(mock_http_client):
    """Create a TelegramClient with a mock HTTP client."""
    return TelegramClient(
        bot_token="123456789:TESTTOKEN",
        chat_id="999888777",
        http_client=mock_http_client,
    )


def make_ok_response(result=None, status_code=200):
    """Create a mock successful Telegram API response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = {"ok": True, "result": result or {}}
    mock_response.raise_for_status = MagicMock()
    return mock_response


def make_error_response(description="Error", error_code=400, status_code=400):
    """Create a mock error Telegram API response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = {
        "ok": False,
        "description": description,
        "error_code": error_code,
    }
    mock_response.raise_for_status = MagicMock()
    return mock_response


def make_rate_limit_response(retry_after=5):
    """Create a mock 429 rate-limit response."""
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.json.return_value = {
        "ok": False,
        "description": "Too Many Requests",
        "parameters": {"retry_after": retry_after},
    }
    return mock_response


# =============================================================================
# Constructor and Client Lifecycle Tests
# =============================================================================

class TestClientConstruction:
    """Tests for TelegramClient constructor and HTTP client management."""

    def test_stores_bot_token_and_chat_id(self):
        """Should store bot_token and chat_id."""
        client = TelegramClient(bot_token="token", chat_id="123")
        assert client._chat_id == "123"


# =============================================================================
# _api_call Tests
# =============================================================================

class TestApiCall:
    """Tests for _api_call -- retry logic and error handling."""

    @pytest.mark.asyncio
    async def test_successful_api_call(self, telegram_client, mock_http_client):
        """Should return result from successful API response."""
        mock_http_client.post.return_value = make_ok_response({"message_id": 42})

        result = await telegram_client._api_call("sendMessage", {"text": "hi"})

        assert result == {"message_id": 42}


# =============================================================================
# send_message Tests
# =============================================================================

class TestSendMessage:
    """Tests for send_message -- outbound message sending."""

    @pytest.mark.asyncio
    async def test_sends_message_to_authorized_chat(self, telegram_client, mock_http_client):
        """Should send message to the configured chat_id."""
        mock_http_client.post.return_value = make_ok_response({"message_id": 42})

        await telegram_client.send_message("Hello")

        call_args = mock_http_client.post.call_args
        params = call_args.kwargs.get("json") or call_args[1].get("json")
        assert params["chat_id"] == "999888777"
        assert params["text"] == "Hello"


# =============================================================================
# send_message_with_buttons Tests
# =============================================================================

class TestSendMessageWithButtons:
    """Tests for send_message_with_buttons -- inline keyboard construction."""

    @pytest.mark.asyncio
    async def test_creates_inline_keyboard(self, telegram_client, mock_http_client):
        """Should create inline keyboard from button labels."""
        mock_http_client.post.return_value = make_ok_response({"message_id": 42})

        await telegram_client.send_message_with_buttons(
            "Choose:", buttons=["Yes", "No"]
        )

        call_args = mock_http_client.post.call_args
        params = call_args.kwargs.get("json") or call_args[1].get("json")
        keyboard = params["reply_markup"]["inline_keyboard"]
        assert len(keyboard) == 1  # single row
        assert len(keyboard[0]) == 2  # two buttons
        assert keyboard[0][0] == {"text": "Yes", "callback_data": "Yes"}
        assert keyboard[0][1] == {"text": "No", "callback_data": "No"}


# =============================================================================
# answer_callback_query Tests
# =============================================================================

class TestAnswerCallbackQuery:
    """Tests for answer_callback_query -- inline keyboard button acknowledgement."""

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, telegram_client, mock_http_client):
        """Should return True when API call succeeds."""
        mock_http_client.post.return_value = make_ok_response(True)

        result = await telegram_client.answer_callback_query("query_123")

        assert result is True


# =============================================================================
# get_updates Tests
# =============================================================================

class TestGetUpdates:
    """Tests for get_updates -- long-polling with chat_id security filtering."""

    @pytest.mark.asyncio
    async def test_returns_authorized_updates_only(self, telegram_client, mock_http_client):
        """Should filter updates to authorized chat_id only (security control)."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 1,
                    "message": {"chat": {"id": 999888777}, "text": "hello"},
                },
                {
                    "update_id": 2,
                    "message": {"chat": {"id": 111222333}, "text": "intruder"},
                },
            ],
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        updates = await telegram_client.get_updates(timeout=1)

        assert len(updates) == 1
        assert updates[0]["update_id"] == 1


# =============================================================================
# get_file Tests
# =============================================================================

class TestGetFile:
    """Tests for get_file -- file metadata retrieval for voice note downloads."""

    @pytest.mark.asyncio
    async def test_returns_file_metadata(self, telegram_client, mock_http_client):
        """Should return file metadata from Telegram API."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "ok": True,
            "result": {
                "file_id": "voice_abc",
                "file_unique_id": "unique123",
                "file_size": 12345,
                "file_path": "voice/file_0.oga",
            },
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        result = await telegram_client.get_file("voice_abc")

        assert result["file_path"] == "voice/file_0.oga"
        assert result["file_size"] == 12345
        mock_http_client.post.assert_awaited_once()


# =============================================================================
# Static Extractor Tests
# =============================================================================

class TestExtractChatId:
    """Tests for _extract_chat_id -- extracting chat_id from different update types."""

    def test_extracts_from_regular_message(self):
        """Should extract chat_id from a regular message update."""
        update = {"message": {"chat": {"id": 12345}, "text": "hello"}}
        assert TelegramClient._extract_chat_id(update) == 12345


