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

    def test_redacts_aws_access_key(self):
        """Should redact AWS access key ID (AKIA prefix)."""
        text = "Key: AKIAIOSFODNN7EXAMPLE"
        result = filter_outbound(text)
        assert "AKIA" not in result
        assert "[REDACTED:aws_key]" in result

    # --- OpenAI Keys ---

    def test_redacts_openai_api_key(self):
        """Should redact OpenAI API keys (sk- prefix)."""
        text = "Using key sk-proj-abc123def456ghi789jkl012mno345"
        result = filter_outbound(text)
        assert "sk-" not in result
        assert "[REDACTED:openai_key]" in result

    def test_redacts_openai_key_in_env_format(self):
        """Should redact OpenAI key in .env assignment format."""
        text = "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz"
        result = filter_outbound(text)
        assert "sk-" not in result

    # --- Telegram Bot Tokens ---

    def test_redacts_telegram_bot_token(self):
        """Should redact Telegram bot tokens (numeric:alpha pattern)."""
        # Bot token regex: \d{8,10}:[A-Za-z0-9_-]{35}
        text = "Token: 123456789:ABCdefGHI-JKLmnoPQRstuVWXyz12345ABC"
        result = filter_outbound(text)
        assert "123456789:" not in result
        assert "[REDACTED:bot_token]" in result

    # --- GitHub Tokens ---

    def test_redacts_github_personal_access_token(self):
        """Should redact GitHub PAT (ghp_ prefix)."""
        text = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"  # pragma: allowlist secret
        result = filter_outbound(text)
        assert "ghp_" not in result
        assert "[REDACTED:github_token]" in result

    def test_redacts_github_oauth_token(self):
        """Should redact GitHub OAuth token (gho_ prefix)."""
        text = "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"  # pragma: allowlist secret
        result = filter_outbound(text)
        assert "gho_" not in result
        assert "[REDACTED:github_token]" in result

    # --- Generic API Keys ---

    def test_redacts_api_key_equals_format(self):
        """Should redact api_key=<value> patterns."""
        text = "api_key=abcdef1234567890abcdef"
        result = filter_outbound(text)
        assert "abcdef1234567890" not in result
        assert "[REDACTED" in result

    def test_redacts_api_key_colon_format(self):
        """Should redact api_key: <value> patterns."""
        text = "api_key: abcdef1234567890abcdef"
        result = filter_outbound(text)
        assert "abcdef1234567890" not in result

    # --- Bearer Tokens ---

    def test_redacts_bearer_token(self):
        """Should redact Bearer tokens in auth headers."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.long_token_value"
        result = filter_outbound(text)
        assert "Bearer eyJ" not in result
        assert "[REDACTED" in result

    # --- Passwords ---

    def test_redacts_password_equals(self):
        """Should redact password=<value> patterns."""
        text = "password=mysecretpassword123"
        result = filter_outbound(text)
        assert "mysecretpassword" not in result
        assert "[REDACTED:password]" in result

    def test_redacts_pwd_colon(self):
        """Should redact pwd: <value> patterns."""
        text = "pwd: super_secret_value_here"
        result = filter_outbound(text)
        assert "super_secret" not in result

    # --- Connection Strings ---

    def test_redacts_postgres_connection_string(self):
        """Should redact PostgreSQL connection strings with credentials."""
        text = "postgresql://admin:s3cr3t@localhost:5432/mydb"
        result = filter_outbound(text)
        assert "admin:s3cr3t@" not in result
        assert "[REDACTED:connection_string]" in result

    def test_redacts_mysql_connection_string(self):
        """Should redact MySQL connection strings with credentials."""
        text = "mysql://root:password@db.example.com:3306/app"
        result = filter_outbound(text)
        assert "root:password@" not in result

    def test_redacts_mongodb_connection_string(self):
        """Should redact MongoDB connection strings."""
        text = "mongodb://user:pass@host:27017/db"
        result = filter_outbound(text)
        assert "user:pass@" not in result

    # --- Private Keys ---

    def test_redacts_rsa_private_key_header(self):
        """Should redact RSA private key block headers."""
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA..."  # pragma: allowlist secret
        result = filter_outbound(text)
        assert "BEGIN RSA PRIVATE KEY" not in result
        assert "[REDACTED:private_key]" in result

    def test_redacts_ec_private_key_header(self):
        """Should redact EC private key block headers."""
        text = "-----BEGIN EC PRIVATE KEY-----"  # pragma: allowlist secret
        result = filter_outbound(text)
        assert "BEGIN EC PRIVATE KEY" not in result

    # --- .env Variable Patterns ---

    def test_redacts_env_var_telegram_bot_token(self):
        """Should redact TELEGRAM_BOT_TOKEN=... on its own line."""
        text = "Config:\nTELEGRAM_BOT_TOKEN=123456:ABCDEF\nDone."
        result = filter_outbound(text)
        assert "123456:ABCDEF" not in result

    def test_redacts_env_var_secret_key(self):
        """Should redact SECRET_KEY=... on its own line."""
        text = "SECRET_KEY=my_django_secret_key_here"
        result = filter_outbound(text)
        assert "my_django_secret" not in result

    # --- JWT Tokens ---

    def test_redacts_jwt_token(self):
        """Should redact JWT tokens (eyJ...eyJ...signature)."""
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc123def456"  # pragma: allowlist secret
        text = f"Token: {jwt}"
        result = filter_outbound(text)
        assert "eyJhbGci" not in result
        assert "[REDACTED:jwt]" in result

    # --- Multiple Patterns in One Message ---

    def test_redacts_multiple_credentials_in_one_message(self):
        """Should redact multiple different credential types in a single message."""
        text = (
            "Config dump:\n"
            "AWS: AKIAIOSFODNN7EXAMPLE\n"
            "OpenAI: sk-abcdefghijklmnopqrst\n"
            "password=hunter2hunter2\n"
        )
        result = filter_outbound(text)
        assert "AKIA" not in result
        assert "sk-" not in result
        assert "hunter2" not in result

    # --- Security Adversarial Tests ---

    def test_redacts_credential_with_surrounding_whitespace(self):
        """Should redact credentials even with leading/trailing whitespace."""
        text = "  sk-testkey123456789012345  "
        result = filter_outbound(text)
        assert "sk-testkey" not in result

    def test_redacts_credential_in_json(self):
        """Should redact credentials embedded in JSON."""
        text = '{"api_key": "sk-abcdefghijklmnopqrstuvwxyz"}'  # pragma: allowlist secret
        result = filter_outbound(text)
        assert "sk-" not in result

    def test_does_not_false_positive_on_short_strings(self):
        """Should not redact normal short words that happen to contain substrings."""
        text = "The skeleton key is in the drawer."
        result = filter_outbound(text)
        # "sk" appears in "skeleton" but it's not "sk-..." pattern
        assert "skeleton" in result

    def test_redacts_long_hex_string(self):
        """Should redact hex strings that look like secrets (48+ hex chars)."""
        hex_secret = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6"  # 48 hex chars
        text = f"Secret hash: {hex_secret}"
        result = filter_outbound(text)
        assert hex_secret not in result
        assert "[REDACTED:hex_secret]" in result

    def test_does_not_redact_git_sha(self):
        """Should not redact git commit hashes (40 hex chars)."""
        git_sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"  # 40 hex chars
        text = f"Commit: {git_sha}"
        result = filter_outbound(text)
        assert git_sha in result

    def test_does_not_redact_short_hex(self):
        """Should not redact hex strings shorter than 48 chars."""
        short_hex = "a1b2c3d4e5f6"  # 12 hex chars
        text = f"Short hash: {short_hex}"
        result = filter_outbound(text)
        assert short_hex in result

    # --- Zero-Width Character Bypass Tests ---

    def test_strips_zero_width_chars_before_scanning(self):
        """Should strip zero-width chars to prevent credential bypass."""
        # Insert zero-width spaces into an OpenAI key to try to bypass
        key_with_zwsp = "sk-\u200babcdefghijklmnopqrstuvwxyz"
        text = f"Key: {key_with_zwsp}"
        result = filter_outbound(text)
        assert "sk-" not in result
        assert "[REDACTED:openai_key]" in result

    def test_strips_zero_width_joiner_in_credential(self):
        """Should strip zero-width joiner that could break pattern matching."""
        # ZWNJ inside AWS key
        text = "AKIA\u200cIOSFODNN7EXAMPLE"
        result = filter_outbound(text)
        assert "AKIA" not in result

    def test_strips_bom_character(self):
        """Should strip BOM (byte order mark) character."""
        text = "\ufeffsk-abcdefghijklmnopqrstuvwxyz"
        result = filter_outbound(text)
        assert "sk-" not in result

    def test_normal_text_unaffected_by_zero_width_stripping(self):
        """Zero-width stripping should not affect normal text."""
        text = "Hello world, this is a normal message."
        result = filter_outbound(text)
        assert result == text


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

    def test_long_message_truncated(self):
        """Should truncate messages exceeding the limit."""
        text = "x" * (TELEGRAM_MAX_MESSAGE_LENGTH + 100)
        result = truncate_message(text)
        assert len(result) <= TELEGRAM_MAX_MESSAGE_LENGTH
        assert result.endswith("[...message truncated]")

    def test_empty_string_unchanged(self):
        """Should return empty string unchanged."""
        assert truncate_message("") == ""

    def test_custom_max_length(self):
        """Should respect custom max_length parameter."""
        text = "x" * 200
        result = truncate_message(text, max_length=100)
        assert len(result) <= 100
        assert result.endswith("[...message truncated]")

    def test_truncation_marker_fits_within_limit(self):
        """Truncated message including marker should not exceed max_length."""
        text = "x" * 5000
        result = truncate_message(text)
        assert len(result) <= TELEGRAM_MAX_MESSAGE_LENGTH


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

    def test_strips_null_bytes(self):
        """Should strip null bytes (\\x00)."""
        text = "hello\x00world"
        assert sanitize_inbound(text) == "helloworld"

    def test_strips_control_characters(self):
        """Should strip control characters like \\x01-\\x08, \\x0e-\\x1f."""
        text = "hello\x01\x02\x03\x0eworld"
        assert sanitize_inbound(text) == "helloworld"

    def test_preserves_newlines(self):
        """Should preserve \\n (newline) characters."""
        text = "line1\nline2"
        assert sanitize_inbound(text) == "line1\nline2"

    def test_preserves_tabs(self):
        """Should preserve \\t (tab) characters."""
        text = "col1\tcol2"
        assert sanitize_inbound(text) == "col1\tcol2"

    def test_preserves_carriage_return(self):
        """Should preserve \\r (carriage return) characters."""
        text = "line1\r\nline2"
        assert sanitize_inbound(text) == "line1\r\nline2"

    def test_enforces_length_limit(self):
        """Should truncate text exceeding MAX_INBOUND_LENGTH."""
        text = "x" * (MAX_INBOUND_LENGTH + 500)
        result = sanitize_inbound(text)
        assert len(result) <= MAX_INBOUND_LENGTH

    def test_strips_leading_trailing_whitespace(self):
        """Should strip leading/trailing whitespace after sanitization."""
        text = "  hello  "
        assert sanitize_inbound(text) == "hello"

    def test_preserves_unicode_text(self):
        """Should preserve Unicode characters (emoji, CJK, etc.)."""
        text = "Hello world"
        assert sanitize_inbound(text) == "Hello world"

    def test_strips_vertical_tab_and_form_feed(self):
        """Should strip \\x0b (vertical tab) and \\x0c (form feed)."""
        text = "hello\x0b\x0cworld"
        assert sanitize_inbound(text) == "helloworld"

    def test_strips_delete_character(self):
        """Should strip \\x7f (DEL) character."""
        text = "hello\x7fworld"
        assert sanitize_inbound(text) == "helloworld"

    def test_strips_zero_width_space(self):
        """Should strip zero-width space characters."""
        text = "hello\u200bworld"
        assert sanitize_inbound(text) == "helloworld"

    def test_strips_bidi_override_characters(self):
        """Should strip Unicode bidi override characters (security control)."""
        # LRE, RLE, PDF, LRO, RLO
        text = "hello\u202a\u202b\u202c\u202d\u202eworld"
        assert sanitize_inbound(text) == "helloworld"

    def test_strips_bidi_isolate_characters(self):
        """Should strip Unicode bidi isolate characters."""
        # LRI, RLI, FSI, PDI
        text = "hello\u2066\u2067\u2068\u2069world"
        assert sanitize_inbound(text) == "helloworld"

    def test_strips_bom_inbound(self):
        """Should strip BOM (byte order mark) from inbound text."""
        text = "\ufeffhello"
        assert sanitize_inbound(text) == "hello"


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

    def test_short_clean_message_unchanged(self):
        """Should pass through short, clean messages unchanged."""
        text = "Build completed."
        assert filter_and_truncate(text) == text

    def test_order_matters_filter_before_truncate(self):
        """Filter should run first so credentials near the end are still caught."""
        # Put credential near the start so it survives truncation
        token = "sk-abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret
        text = f"Start {token} " + "x" * 5000
        result = filter_and_truncate(text)
        assert token not in result


# =============================================================================
# The shared scrub (T-4dbe65b9): Telegram redacts what every other egress path redacts
# =============================================================================

GITLAB_FAKE = "glpat-" + "Fk9mQ2vLp7Rz4tXk9mQ2"          # a gitlab-token shape, fake
SLACK_FAKE = "xoxb-" + "123456789012-" + "Fk9mQ2vL"        # a slack-token shape, fake


class TestSharedScrub:
    def test_a_value_only_the_shared_scrub_knows_is_redacted(self):
        result = filter_outbound(f"deploy used {GITLAB_FAKE} and {SLACK_FAKE} today")
        assert GITLAB_FAKE not in result and SLACK_FAKE not in result
        assert "[REDACTED:gitlab-token]" in result and "[REDACTED:slack-token]" in result

    def test_denylist_literals_and_words_are_redacted(self, tmp_path):
        (tmp_path / "denylist.txt").write_text("# names\nAcmeFakeCorp\n", encoding="utf-8")
        (tmp_path / "words.txt").write_text("zorblat\n", encoding="utf-8")
        result = filter_outbound("Report for acmefakecorp: zorblat done, zorblatty kept")
        assert "acmefakecorp" not in result.lower() and " zorblat " not in result
        assert "zorblatty" in result

    def test_a_denylist_that_cannot_be_read_sends_nothing(self, tmp_path):
        (tmp_path / "denylist.txt").write_bytes(b"\xff\xfe not utf-8")
        with pytest.raises(OutboundFilterError):
            filter_and_truncate("an ordinary message")

    def test_a_missing_denylist_is_not_a_failure(self):
        assert filter_outbound("Build completed.") == "Build completed."

    def test_unicode_around_a_secret(self):
        result = filter_outbound(f"\u00e9t\u00e9 \u2713 \U0001f511 {GITLAB_FAKE} \u00fc\u200b")
        assert GITLAB_FAKE not in result and "\U0001f511" in result and "\u00e9t\u00e9" in result

    def test_a_huge_message_is_capped_before_the_scrub_and_stays_fast(self):
        """100 KB of "a." takes the shared scrub 4 s; the cap bounds it, and nothing past the cap
        is ever sent: a value placed beyond it never appears."""
        text = f"{GITLAB_FAKE} " + "a." * 500_000 + f" tail {SLACK_FAKE}"
        start = time.monotonic()
        result = filter_and_truncate(text)
        assert time.monotonic() - start < 5
        assert len(result) <= TELEGRAM_MAX_MESSAGE_LENGTH
        assert GITLAB_FAKE not in result and SLACK_FAKE not in result and "tail" not in result
        assert len(filter_outbound(text)) <= OUTBOUND_SCAN_CAP + 64

    def test_a_value_cut_by_the_cap_is_dropped_whole(self):
        pad = "x " * ((OUTBOUND_SCAN_CAP - 10) // 2)
        result = filter_outbound(pad + GITLAB_FAKE + " more")
        assert "glpat" not in result and GITLAB_FAKE[:8] not in result


# ── 1.5.0 leakfix L3: every Unicode format character is stripped before redaction ──────────
AWS_FAKE = "AKIA" + "QWERTYUIOPASDFGH"                       # an aws-key shape, fake


@pytest.mark.parametrize("char", ["\u00ad", "\u2060", "\u180e", "\u200b", "\u2062", "\u061c",
                                  "\U000e0041", "\U000e0001", "\U000e007f", "\ufeff", "\u202e"])
def test_leakfix_a_format_character_inside_a_key_does_not_hide_it(char):
    out = filter_outbound("key " + AWS_FAKE[:8] + char + AWS_FAKE[8:] + " end")
    assert AWS_FAKE[8:] not in out and "[REDACTED:aws_key]" in out


# fxa F9: invisible characters outside Cf, and fullwidth letters (NFKC folds them to ASCII).
@pytest.mark.parametrize("char", ["\u034f", "\ufe0f", "\ufe00", "\U000e0100", "\u180b", "\u180f",
                                  "\u3164", "\u1160", "\u115f", "\uffa0", "\u2800", "\u17b4"])
def test_fxa_an_invisible_non_format_character_inside_a_key_does_not_hide_it(char):
    out = filter_outbound("key " + AWS_FAKE[:8] + char + AWS_FAKE[8:] + " end")
    assert AWS_FAKE[8:] not in out and "[REDACTED:aws_key]" in out


@pytest.mark.parametrize("spell", [
    lambda key: "".join(chr(ord(c) + 0xFEE0) for c in key),               # fullwidth
    lambda key: "".join(chr(ord(c) + 0xFEE0) for c in key[:4]) + key[4:],  # mixed
    lambda key: "".join(chr(0x1D400 + ord(c) - 65) if c.isupper() else c for c in key),  # math bold
])
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
