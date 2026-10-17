"""
Location: pact-plugin/telegram/content_filter.py
Summary: Outbound message sanitization with regex-based credential redaction.
Used by: telegram_client.py (applied to every outbound message before sending).

Security control: This is a MUST-HAVE per the plan's security checklist.
Every message sent to Telegram passes through filter_outbound() which
redacts patterns that look like credentials, API keys, tokens, passwords,
and other sensitive data.

Design decisions:
- Patterns are intentionally broad (better to over-redact than leak)
- Redacted values show the pattern type for debugging (e.g., "[REDACTED:api_key]")
- Filter is applied at the transport layer (telegram_client.py), not at the
  tool level, so even unexpected credential leaks are caught
- Inbound sanitization (strip_control_chars) is also provided for Telegram
  input validation
- After its own patterns, every message also runs the plugin's shared scrub
  (bin/_repact_common.scrub_text, every secret class plus the denylist files),
  so Telegram redacts what every other egress path redacts. A
  denylist file that exists but cannot be read raises OutboundFilterError:
  nothing is sent.
"""

from __future__ import annotations

import functools
import re
import sys
import unicodedata
from pathlib import Path

_BIN = str(Path(__file__).resolve().parent.parent / "bin")
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
from _repact_common import denylist_paths, load_denylist, scrub_text  # noqa: E402

# Maximum message length for Telegram API (4096 chars)
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
# Only this much of a message is scanned, and nothing past it is ever sent: the shared scrub is
# quadratic on some repetitive text (100 KB of "a." takes 4 s), and Telegram shows 4096 at most.
OUTBOUND_SCAN_CAP = 4 * TELEGRAM_MAX_MESSAGE_LENGTH
CAP_MARKER = "\n\n[...message truncated]"


class OutboundFilterError(Exception):
    """The outbound filter could not run in full; the message must not be sent."""

# Credential patterns to redact in outbound messages.
# Each tuple is (compiled_regex, replacement_label).
# Patterns are ordered from most specific to most general.
_CREDENTIAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # AWS access keys (AKIA...)
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws_key]"),

    # OpenAI API keys (sk-...)
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "[REDACTED:openai_key]"),

    # Telegram bot tokens (123456:ABC-DEF...)
    (re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}"), "[REDACTED:bot_token]"),

    # GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_)
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"), "[REDACTED:github_token]"),

    # Generic API keys in common formats (api_key=..., apikey=..., api-key: ...)
    (re.compile(
        r"(?i)(?:api[_-]?key|apikey|api[_-]?secret|api[_-]?token)"
        r"[\s]*[=:]\s*['\"]?([A-Za-z0-9_\-/.+=]{16,})['\"]?"
    ), "[REDACTED:api_key]"),

    # Bearer tokens
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-/.+=]{16,}"), "[REDACTED:bearer_token]"),

    # Authorization headers
    (re.compile(r"(?i)authorization[\s]*[=:]\s*['\"]?[A-Za-z0-9_\-/.+=\s]{16,}['\"]?"), "[REDACTED:auth_header]"),

    # Password patterns (password=..., passwd=..., pwd=...)
    (re.compile(
        r"(?i)(?:password|passwd|pwd|secret)"
        r"[\s]*[=:]\s*['\"]?([^\s'\"]{8,})['\"]?"
    ), "[REDACTED:password]"),

    # Connection strings with credentials
    (re.compile(
        r"(?i)(?:postgres|mysql|mongodb|redis|amqp)(?:ql)?://"
        r"[^:]+:[^@]+@"
    ), "[REDACTED:connection_string]://"),

    # Private key blocks
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"), "[REDACTED:private_key]"),

    # .env file content patterns (KEY=value on its own line)
    (re.compile(
        r"(?i)^(?:TELEGRAM_BOT_TOKEN|OPENAI_API_KEY|AWS_SECRET|DB_PASSWORD|"
        r"SECRET_KEY|PRIVATE_KEY|AUTH_TOKEN)"
        r"\s*=\s*.+$",
        re.MULTILINE,
    ), "[REDACTED:env_var]"),

    # JWT tokens (three base64 segments separated by dots)
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_\-+=]+"), "[REDACTED:jwt]"),

    # Hex strings that look like secrets (48+ hex chars to avoid matching git SHAs)
    (re.compile(r"(?<![A-Fa-f0-9])[0-9a-f]{48,}(?![A-Fa-f0-9])"), "[REDACTED:hex_secret]"),
]


# Invisible Unicode characters are stripped before credential scanning: inserted into a key,
# they would split it past the regex-based redaction.
# Default-ignorable and blank characters outside category Cf: combining grapheme joiner,
# Hangul fillers, Khmer inherent vowels, Mongolian variation selectors, variation selectors
# (and their supplement), halfwidth Hangul filler, braille blank.
_INVISIBLE = re.compile("[\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u2800\u3164"
                        "\ufe00-\ufe0f\uffa0\U000e0100-\U000e01ef]")


def _without_format_chars(text: str) -> str:
    """text without the characters that render as nothing (Unicode format characters,
    category Cf: zero-width space and joiners, BOM, soft hyphen, word joiner, bidi controls,
    the tag block; and _INVISIBLE), then NFKC-normalised, so a key split by an invisible
    character or spelled in fullwidth letters reads as the key. Each of these can hide a
    key from the regex-based redaction."""
    if text.isascii():
        return text
    visible = "".join(char for char in text if unicodedata.category(char) != "Cf")
    return unicodedata.normalize("NFKC", _INVISIBLE.sub("", visible))


# Control characters to strip from inbound Telegram text.
# Keeps printable ASCII, common whitespace, and Unicode text.
# Also strips Unicode bidi overrides and zero-width chars that could be
# used for injection or terminal rendering attacks.
_CONTROL_CHAR_PATTERN = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
    "|[\u200b-\u200f\ufeff]"
    "|[\u202a-\u202e]"
    "|[\u2066-\u2069]"
)

# Maximum inbound message length (prevent abuse)
MAX_INBOUND_LENGTH = 4096


def filter_outbound(message: str) -> str:
    """
    Filter an outbound message to redact credential patterns.

    Applied to every message before sending to Telegram. Scans for patterns
    that look like API keys, tokens, passwords, and other sensitive data,
    replacing them with labeled redaction markers.

    Args:
        message: The raw message text to filter.

    Returns:
        The filtered message with credential patterns redacted.

    Raises:
        OutboundFilterError: a denylist file exists but cannot be read.
    """
    if not message:
        return message
    literals, words = _denylists()

    # Strip invisible format characters before credential scanning so attackers
    # cannot insert them to bypass redaction patterns.
    filtered = _cap(_without_format_chars(message))

    for pattern, replacement in _CREDENTIAL_PATTERNS:
        filtered = pattern.sub(replacement, filtered)

    return scrub_text(filtered, literals, words)


@functools.lru_cache(maxsize=1)
def _denylists() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(literals, whole words) from the scrub denylist files, read once per process. A missing
    file is an empty list; one that exists but cannot be read raises OutboundFilterError
    (and is not cached, so the next message tries again)."""
    try:
        return tuple(tuple(load_denylist(path, strict=True)) for path in denylist_paths())
    except (OSError, UnicodeDecodeError) as error:
        raise OutboundFilterError(
            f"the scrub denylist could not be read ({type(error).__name__}); nothing was sent") from error


def _cap(message: str) -> str:
    """The first OUTBOUND_SCAN_CAP characters, cut back to the last whitespace in the final 256 so
    a value split by the cut is dropped whole, then marked. Shorter messages pass unchanged."""
    if len(message) <= OUTBOUND_SCAN_CAP:
        return message
    head = message[:OUTBOUND_SCAN_CAP]
    space = max(head.rfind(" ", -256), head.rfind("\n", -256), head.rfind("\t", -256))
    return (head[:space] if space > 0 else head[:-256]) + CAP_MARKER


def truncate_message(message: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> str:
    """
    Truncate a message to fit Telegram's character limit.

    If the message exceeds max_length, it is truncated and a marker is
    appended to indicate truncation.

    Args:
        message: The message to potentially truncate.
        max_length: Maximum character count (default: 4096 for Telegram).

    Returns:
        The message, truncated if necessary.
    """
    if not message or len(message) <= max_length:
        return message

    truncation_marker = "\n\n[...message truncated]"
    return message[: max_length - len(truncation_marker)] + truncation_marker


def sanitize_inbound(text: str) -> str:
    """
    Sanitize inbound text from Telegram messages.

    Security control: strips control characters and enforces length limits
    on text received from Telegram users. This prevents injection of
    control characters that could affect terminal rendering or log parsing.

    Args:
        text: Raw text from a Telegram message.

    Returns:
        Sanitized text with control characters removed and length enforced.
    """
    if not text:
        return ""

    # Strip control characters (keep \n, \r, \t)
    cleaned = _CONTROL_CHAR_PATTERN.sub("", text)

    # Enforce length limit
    if len(cleaned) > MAX_INBOUND_LENGTH:
        cleaned = cleaned[:MAX_INBOUND_LENGTH]

    return cleaned.strip()


def filter_and_truncate(message: str) -> str:
    """
    Apply both content filtering and truncation in the correct order.

    Convenience function that applies filter_outbound() first (to redact
    credentials before any truncation), then truncates to Telegram's limit.

    Args:
        message: The raw message to process.

    Returns:
        Filtered and truncated message ready for Telegram API.
    """
    return truncate_message(filter_outbound(message))
