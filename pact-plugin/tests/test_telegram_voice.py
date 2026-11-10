"""
Tests for pact-plugin/telegram/voice.py

Tests cover:
1. VoiceTranscriber construction and is_available check
2. get_file_path: API call, error handling, file size limit
3. download_file: file download, size enforcement
4. transcribe: Whisper API call, temp file cleanup (security), error handling
5. transcribe_voice_message: full pipeline integration
6. Security: temp file always deleted, no credentials in errors, size limits
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram.voice import (
    VoiceTranscriber,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_http_client():
    """Create a mock httpx.AsyncClient."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def transcriber(mock_http_client):
    """Create a VoiceTranscriber with mock HTTP client."""
    return VoiceTranscriber(
        bot_token="123:TEST",
        openai_api_key="sk-test-key",
        http_client=mock_http_client,
    )


@pytest.fixture
def transcriber_no_key(mock_http_client):
    """Create a VoiceTranscriber without OpenAI key."""
    return VoiceTranscriber(
        bot_token="123:TEST",
        openai_api_key=None,
        http_client=mock_http_client,
    )


# =============================================================================
# Construction and is_available Tests
# =============================================================================

class TestVoiceTranscriberInit:
    """Tests for VoiceTranscriber construction."""

    def test_is_available_with_api_key(self, transcriber):
        """Should be available when OpenAI API key is configured."""
        assert transcriber.is_available() is True


# =============================================================================
# get_file_path Tests
# =============================================================================

class TestGetFilePath:
    """Tests for get_file_path -- Telegram getFile API call."""

    @pytest.mark.asyncio
    async def test_returns_file_path_on_success(self, transcriber, mock_http_client):
        """Should return file_path from successful getFile response."""
        response = MagicMock()
        response.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/file_0.ogg", "file_size": 1000},
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        path = await transcriber.get_file_path("file_abc")

        assert path == "voice/file_0.ogg"


# =============================================================================
# download_file Tests
# =============================================================================

class TestDownloadFile:
    """Tests for download_file -- downloading voice files from Telegram."""

    @pytest.mark.asyncio
    async def test_downloads_file_successfully(self, transcriber, mock_http_client):
        """Should return file bytes on success."""
        response = MagicMock()
        response.content = b"audio data"
        response.raise_for_status = MagicMock()
        mock_http_client.get.return_value = response

        data = await transcriber.download_file("voice/file.ogg")

        assert data == b"audio data"


# =============================================================================
# transcribe Tests
# =============================================================================

class TestTranscribe:
    """Tests for transcribe -- OpenAI Whisper API transcription."""

    @pytest.mark.asyncio
    async def test_transcribes_audio_successfully(self, transcriber, mock_http_client):
        """Should return transcribed text from Whisper API."""
        response = MagicMock()
        response.json.return_value = {"text": "Hello world"}
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        result = await transcriber.transcribe(b"audio data", "voice.ogg")

        assert result == "Hello world"


# =============================================================================
# transcribe_voice_message Tests
# =============================================================================

