"""
Tests for pact-plugin/telegram/content_filter.py

Tests cover:
1. filter_outbound: credential pattern redaction (AWS, OpenAI, Telegram, GitHub,
   API keys, bearer tokens, passwords, connection strings, private keys, JWTs, hex secrets)
2. truncate_message: Telegram 4096-char limit with truncation marker
3. sanitize_inbound: control character stripping, length enforcement
4. filter_and_truncate: combined filter + truncate pipeline
5. Security adversarial: crafted inputs designed to bypass redaction
6. Edge cases: empty strings, None-like, very long inputs
"""


import time

import pytest

from telegram import content_filter
from telegram.content_filter import (
    OUTBOUND_SCAN_CAP,
    TELEGRAM_MAX_MESSAGE_LENGTH,
    MAX_INBOUND_LENGTH,
    OutboundFilterError,
    filter_outbound,
    truncate_message,
    sanitize_inbound,
    filter_and_truncate,
)


@pytest.fixture(autouse=True)
def _own_denylists(tmp_path, monkeypatch):
    """Every row reads temp denylist files (absent unless a row writes them), never the owner's."""
    monkeypatch.setenv("REPACT_SCRUB_DENYLIST", str(tmp_path / "denylist.txt"))
    monkeypatch.setenv("REPACT_SCRUB_DENYLIST_WORDS", str(tmp_path / "words.txt"))
    content_filter._denylists.cache_clear()
    yield
    content_filter._denylists.cache_clear()


# =============================================================================
# filter_outbound Tests
# =============================================================================

class TestFilterOutbound:
    """Tests for filter_outbound -- credential redaction in outbound messages."""

    def test_empty_string_passthrough(self):
        """Should return empty string unchanged."""
        assert filter_outbound("") == ""

    def test_safe_text_unchanged(self):
        """Should not modify text without credential patterns."""
        text = "Build completed successfully. 5 tests passed."
        assert filter_outbound(text) == text

    # --- AWS Keys ---


    # --- OpenAI Keys ---


    # --- Telegram Bot Tokens ---


    # --- GitHub Tokens ---


    # --- Generic API Keys ---


    # --- Bearer Tokens ---


    # --- Passwords ---


    # --- Connection Strings ---


    # --- Private Keys ---


    # --- .env Variable Patterns ---


    # --- JWT Tokens ---


    # --- Multiple Patterns in One Message ---


    # --- Security Adversarial Tests ---


    # --- Zero-Width Character Bypass Tests ---


# =============================================================================
# truncate_message Tests
# =============================================================================

class TestTruncateMessage:
    """Tests for truncate_message -- Telegram message length enforcement."""

    def test_short_message_unchanged(self):
        """Should not modify messages within the length limit."""
        text = "Hello world"
        assert truncate_message(text) == text

    def test_exact_limit_unchanged(self):
        """Should not truncate message exactly at the limit."""
        text = "x" * TELEGRAM_MAX_MESSAGE_LENGTH
        assert truncate_message(text) == text


# =============================================================================
# sanitize_inbound Tests
# =============================================================================

class TestSanitizeInbound:
    """Tests for sanitize_inbound -- inbound Telegram text sanitization."""

    def test_normal_text_unchanged(self):
        """Should not modify normal text."""
        text = "Hello, how are you?"
        assert sanitize_inbound(text) == text

    def test_empty_string_returns_empty(self):
        """Should return empty string for empty input."""
        assert sanitize_inbound("") == ""


# =============================================================================
# filter_and_truncate Tests
# =============================================================================

class TestFilterAndTruncate:
    """Tests for filter_and_truncate -- combined filtering + truncation pipeline."""

    def test_filters_then_truncates(self):
        """Should apply filter before truncation."""
        # Credential that would be redacted, in a long message
        token = "sk-abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret
        text = f"Key: {token} " + "x" * 5000
        result = filter_and_truncate(text)

        # Credential should be redacted
        assert "sk-" not in result
        # Should be truncated
        assert len(result) <= TELEGRAM_MAX_MESSAGE_LENGTH


# =============================================================================
# The shared scrub: Telegram redacts what every other egress path redacts
# =============================================================================

GITLAB_FAKE = "glpat-" + "Fk9mQ2vLp7Rz4tXk9mQ2"          # a gitlab-token shape, fake
SLACK_FAKE = "xoxb-" + "123456789012-" + "Fk9mQ2vL"        # a slack-token shape, fake


class TestSharedScrub:
    def test_a_value_only_the_shared_scrub_knows_is_redacted(self):
        result = filter_outbound(f"deploy used {GITLAB_FAKE} and {SLACK_FAKE} today")
        assert GITLAB_FAKE not in result and SLACK_FAKE not in result
        assert "[REDACTED:gitlab-token]" in result and "[REDACTED:slack-token]" in result


# ── 1.5.0 leakfix L3: every Unicode format character is stripped before redaction ──────────
AWS_FAKE = "AKIA" + "QWERTYUIOPASDFGH"                       # an aws-key shape, fake


@pytest.mark.parametrize("char", ["\u00ad", "\u2060",])
def test_leakfix_a_format_character_inside_a_key_does_not_hide_it(char):
    out = filter_outbound("key " + AWS_FAKE[:8] + char + AWS_FAKE[8:] + " end")
    assert AWS_FAKE[8:] not in out and "[REDACTED:aws_key]" in out


# fxa F9: invisible characters outside Cf, and fullwidth letters (NFKC folds them to ASCII).
@pytest.mark.parametrize("char", ["\u034f", "\ufe0f",])
def test_fxa_an_invisible_non_format_character_inside_a_key_does_not_hide_it(char):
    out = filter_outbound("key " + AWS_FAKE[:8] + char + AWS_FAKE[8:] + " end")
    assert AWS_FAKE[8:] not in out and "[REDACTED:aws_key]" in out


@pytest.mark.parametrize("spell", [
    lambda key: "".join(chr(ord(c) + 0xFEE0) for c in key),               # fullwidth
    lambda key: "".join(chr(ord(c) + 0xFEE0) for c in key[:4]) + key[4:],])
def test_fxa_a_fullwidth_key_is_redacted(spell):
    text = spell(AWS_FAKE)
    out = filter_outbound("x " + text + " y")
    assert text not in out and "[REDACTED:aws_key]" in out


def test_fxa_emoji_and_accents_survive_the_invisible_strip():
    assert filter_outbound("ship it 👍🏽 café") == "ship it 👍🏽 café"


def test_leakfix_many_format_characters_do_not_hide_it():
    out = filter_outbound("\u2060".join(AWS_FAKE))
    assert out == "[REDACTED:aws_key]"


def test_leakfix_visible_unicode_is_kept():
    text = "café ✓ 日本語 🙂 ok"
    assert filter_outbound(text) == text


def test_leakfix_a_large_non_ascii_message_is_filtered_in_time():
    started = time.monotonic()
    filter_outbound(("\u00e9\u00ad" * 100_000) + AWS_FAKE)
    assert time.monotonic() - started < 5
