"""
Location: pact-plugin/hooks/shared/scrub_values.py
Summary: The value classifiers the scrub patterns apply to a candidate secret: placeholder
         detection, identifier-vs-random, password shapes, URL keys, basic-auth bodies, code
         references. Pure functions over strings; a leaf (stdlib only) so shell/ may import it.
Used by: shared/scrub.py (the pattern table), hooks/output_secret_scan.py (is_placeholder_value).
Map (file order): _VALUE, the placeholder email tables, _reportable_email, _mixed_charset,
_looks_like_identifier, _opaque_value, is_placeholder_value, _real_value,
_looks_like_secret_value, the credential-line and password classifiers, _url_key_value,
_opaque_literal, _credential_name_value, _midline_value, _code_reference, _basic_auth_value,
_real_token_body.
"""
from __future__ import annotations

import base64
import math
import re
from typing import Callable, Optional


GIT_SHA_LENGTH = 40   # a full commit id is provenance, not a secret
_VALUE = r"(?![$<{%(\[*])[^\s\"'`,;<>]"


# RFC 2606 / RFC 6761 reserved names: documentation placeholders, never a real mailbox.
# A spec'd exemption, not a tolerance: nothing else is exempt except the owner's address,
# which the pattern table (shared/scrub.py) reads from the profile identity; this module stays
# a leaf.
PLACEHOLDER_EMAIL_DOMAINS = ("example.com", "example.net", "example.org", "localhost")
PLACEHOLDER_EMAIL_TLDS = (".example", ".test", ".invalid", ".localhost")


def _reportable_email(address: str) -> bool:
    """False for RFC 2606/6761 placeholder domains."""
    address = address.lower()
    domain = address.rsplit("@", 1)[-1]
    is_reserved = any(domain == name or domain.endswith("." + name)
                      for name in PLACEHOLDER_EMAIL_DOMAINS) \
        or domain.endswith(PLACEHOLDER_EMAIL_TLDS)
    return not is_reserved


def _mixed_charset(text: str) -> bool:
    """True for opaque-looking runs: upper, lower and a digit all present."""
    return any(c.isupper() for c in text) and any(c.islower() for c in text) \
        and any(c.isdigit() for c in text)


_WORDISH = re.compile(r"[A-Z][a-z]{2,}|[a-z]{3,}")
IDENTIFIER_WORD_SHARE = 0.8


def _looks_like_identifier(text: str) -> bool:
    """True for camelCase / PascalCase names such as TestT3IsARealStopUnderNonPromptingModes.

    Measured as the share of the run covered by word-like pieces (a capital plus two or
    more lowercase letters, or three or more lowercase letters). Readable names score
    0.87 to 0.93. Random alphanumeric runs score p50 0.30; at the 0.8 threshold about
    11 in 100,000 random 40-long runs (25 of 32-long, 0 of 64-long) are exempted.
    Acronym-heavy names (HTTPServer...) can still score below and stay flagged.
    """
    covered = sum(len(piece) for piece in _WORDISH.findall(text))
    return covered / max(len(text), 1) >= IDENTIFIER_WORD_SHARE


def _opaque_value(text: str) -> bool:
    return _mixed_charset(text) and not _looks_like_identifier(text)


def _not_cidr_anchor(text: str) -> bool:
    return text != "10.0.0.0"


_LOWER_WORD_CHAIN = re.compile(r"^[a-z]+(?:[._-][a-z]+)*$")
# Obvious PLACEHOLDER values (lead ruling; a spec'd class, not a tolerance). Only these exact
# shapes pass: your_*_here / your-*-here, your-<word> (your-password), <anything>, x+ runs, the
# listed words, FAKE_*.
PLACEHOLDER_VALUE = re.compile(
    r"^(?:your[_-][A-Za-z0-9_-]*[_-]here|your[_-][a-z]+(?:[_-][a-z]+)?|<[^<>]*>|\[?REDACTED[^\s]*"
    r"|x{3,}(?:[_-]x{3,})*"
    r"|changeme|change[_-]me|placeholder|dummy|example|fake[_-][A-Za-z0-9_-]*)$", re.I)


def is_placeholder_value(value: str) -> bool:
    """True for an obvious placeholder such as your_api_key_here, <token>, xxxx, FAKE_KEY."""
    return bool(PLACEHOLDER_VALUE.match(value.strip("\"'")))


def _real_value(accept: Optional[Callable[[str], bool]] = None) -> Callable[[str], bool]:
    """An accept predicate that first rejects placeholders, then applies `accept`."""
    return lambda value: not is_placeholder_value(value) and (accept is None or accept(value))


def _looks_like_secret_value(text: str) -> bool:
    """Heuristic for `NAME=value` hits: numbers and literals never count; a plain
    lowercase word counts only when long (prose such as `secret: redact` passes)."""
    if text.replace(".", "").isdigit() or text.lower() in ("true", "false", "null", "none"):
        return False
    if _credential_name(text):
        return False    # the NAME of a variable ("VENDOR_API_TOKEN"), not its value
    if _LOWER_WORD_CHAIN.match(text):
        return False    # placeholders and code references: your_api_key, os.environ, cfg.token
    has_signal = any(c.isdigit() or not (c.isalpha() or c == "-") for c in text)
    return has_signal or len(text) >= 12


_CREDENTIAL_LINE_WORDS = frozenset((
    "required", "optional", "none", "null", "true", "false", "yes", "n/a", "tbd", "hidden",
    "redacted", "unknown", "same", "see", "set", "unset", "empty", "omitted", "masked"))


# Values that documentation uses as example passwords (CREATE USER x WITH PASSWORD 'secure_password',
# POSTGRES_PASSWORD: test), measured on the plugin's own skill docs: a value naming a
# password, or one of these words, is an example. Used by the keyed and SQL password shapes.
_DOC_EXAMPLE_PASSWORDS = frozenset(("test", "testing", "secure", "secret", "admin", "root", "postgres",
                                    "mysql", "demo", "sample", "example"))


def _doc_example_password(value: str) -> bool:
    lowered = value.lower()
    return lowered in _DOC_EXAMPLE_PASSWORDS or "password" in lowered or "passwd" in lowered


_DOTTED_REFERENCE = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
_CREDENTIAL_NAME_WORDS = frozenset(("TOKEN", "SECRET", "KEY", "APIKEY", "PASSWORD", "PASSWD", "PWD",
                                    "PASS", "CREDENTIALS"))
_NAME_SEGMENT_MAX = 15          # AUTHENTICATION is 14; an issued key body is 16 or more
_NAME_SEGMENTS_MAX = 6          # AWS_SECRET_ACCESS_KEY is 4
_NAME_LENGTH_MAX = 48
# A type annotation after a credential-named parameter (`token: string`) is not a value.
_TYPE_WORDS = frozenset(("string", "str", "number", "boolean", "bool", "int", "float", "bytes", "any",
                         "never", "void", "object", "buffer"))


def _credential_name(value: str) -> bool:
    """An env-var NAME that names a credential (VENDOR_API_TOKEN, DB_PASSWORD_PROD): upper
    snake case with a credential word as one of its segments, every segment word-sized, and
    no longer than a name is. A name, not entropy: a long run behind a credential word
    (SECRET_<20 characters>), or a long chain of short segments, is a value."""
    value = value.strip("\"'`")
    segments = value.split("_")
    return (bool(_ENV_NAME.fullmatch(value)) and not _CREDENTIAL_NAME_WORDS.isdisjoint(segments)
            and max(map(len, segments)) <= _NAME_SEGMENT_MAX
            and len(segments) <= _NAME_SEGMENTS_MAX and len(value) <= _NAME_LENGTH_MAX)


def _type_word(value: str) -> bool:
    for suffix in ("[]", "?"):
        if value.endswith(suffix):
            value = value[:-len(suffix)]
            break
    return value.lower() in _TYPE_WORDS


def _credential_line_value(value: str) -> bool:
    """A `password: X` line's value counts unless it is a status word, a URL pointer or a
    code reference (the same label shape is an object literal in code examples):
    a trailing `,`/`;`, a dotted chain (process.env.X), a camelCase identifier
    (hashedPassword) or an env-var NAME (SESSION_SECRET)."""
    value = value.strip("\"'*_`")
    if value.lower() in _CREDENTIAL_LINE_WORDS or "://" in value or value.endswith((",", ";")):
        return False
    if _doc_example_password(value) or _type_word(value):
        return False
    if _DOTTED_REFERENCE.fullmatch(value) or _ENV_NAME.fullmatch(value):
        return False
    return not (re.search(r"[a-z][A-Z]", value) and _looks_like_identifier(value))


_PASSWORD_CLASSES = (str.islower, str.isupper, str.isdigit)


def _password_like(value: str) -> bool:
    """A standalone value that reads as a password: 6-64 characters, at least one letter, at
    least two of (lowercase, uppercase, digit, symbol), and no '/', '@' or '://' (paths,
    remotes and addresses are not passwords). Used where no label says "password"."""
    value = value.strip("\"'`")
    if not 6 <= len(value) <= 64 or any(c in value for c in "/@") or not any(c.isalpha() for c in value):
        return False
    classes = sum(any(test(c) for c in value) for test in _PASSWORD_CLASSES)
    classes += any(not c.isalnum() for c in value)
    return classes >= 2 and not _LOWER_WORD_CHAIN.match(value)


def _standalone_password(value: str) -> bool:
    """A token beside an address with no label: password-like AND carrying a digit or a symbol
    (a mixed-case word next to an address is a name in a table, measured on transcripts)."""
    stripped = value.strip("\"'`")
    if {c.lower() for c in stripped if c.isalpha()} <= {"x"}:
        return False                    # an x-masked value ("xxxxxxxx!"), shown instead of a secret
    return _password_like(value) and any(c.isdigit() or not c.isalnum() for c in stripped)


def _labelled_password_value(value: str) -> bool:
    """A value after a password label ("pwd=X", "password X"): it counts unless it is a status
    word, has no letter, is a code reference or is a plain lowercase word (prose such as
    "password: field" or "pass: 12")."""
    value = value.strip("\"'`*_.,;:!?()-")
    if value.startswith(("/", "~")):
        return False                    # a path: "PWD: /Users/..." is the working directory
    if len(value) < LABELLED_PASSWORD_MIN or _LOWER_WORD_CHAIN.match(value):
        return False                    # "needed.", "\\(.*", "yes": prose and regex, not a value
    if value.lower() in _CREDENTIAL_LINE_WORDS or value.lower() in _PASSWORD_WORDS or value.isdigit():
        return False
    if _credential_name(value) or _DOTTED_REFERENCE.fullmatch(value) or _ENV_NAME.fullmatch(value):
        return False
    if re.search(r"[a-z][A-Z]", value) and _looks_like_identifier(value):
        return False
    if not any(c.isalpha() for c in value):
        return False                    # "6-64 characters", "***", version numbers
    return _looks_like_secret_value(value) or _password_like(value)


# A labelled value shorter than this is prose or a regex fragment, measured on the live KB and
# agent memory (FIX-D25-SHAPES): every hit under 6 characters there was a false positive.
LABELLED_PASSWORD_MIN = 6
def _sql_password_value(value: str) -> bool:
    """The literal after SQL PASSWORD / IDENTIFIED BY counts unless it is a status or password word
    ('password', 'none'), a documentation example ('secure_password') or an x-masked value."""
    lowered = value.lower()
    if lowered in _CREDENTIAL_LINE_WORDS or lowered in _PASSWORD_WORDS or _doc_example_password(value):
        return False
    return not {c for c in lowered if c.isalpha()} <= {"x"}


_UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
URL_KEY_MIN_BITS = 3.0          # Shannon bits per character; random keys measure 3.6 to 5.5


def _bits_per_char(text: str) -> float:
    counts = {c: text.count(c) for c in set(text)}
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _url_key_value(min_length: int) -> Callable[[str], bool]:
    """A key carried in a URL (the hardening pass amend_1): at least min_length characters, a letter and a
    digit, high entropy, and not a UUID, a readable name or a slug. The key-ish path segment or
    parameter name is the regex's job; this is the length/entropy half of the rule."""
    def accept(value: str) -> bool:
        value = value.strip("\"'`.,;)")
        if len(value) < min_length or _UUID.fullmatch(value) or _LOWER_WORD_CHAIN.match(value):
            return False
        if not (any(c.isalpha() for c in value) and any(c.isdigit() for c in value)):
            return False
        if _looks_like_identifier(value.replace("-", "").replace("_", "")):
            return False
        return _bits_per_char(value) >= URL_KEY_MIN_BITS
    return accept


def _opaque_literal(value: str) -> bool:
    """A quoted literal that reads as a generated secret: 16+ characters, at least two of
    (lowercase, uppercase, digit), not a placeholder and not an identifier-like name."""
    classes = sum(any(test(c) for c in value) for test in _PASSWORD_CLASSES)
    return len(value) >= 16 and classes >= 2 and not _looks_like_identifier(value)


def _credential_name_value(value: str) -> bool:
    """The literal given to a credential variable (CSEC=..., CLIENT_ID = '...'): 8+ characters,
    not a lowercase word, a code reference or an env NAME, and holding both a letter and a digit
    (an all-digit id is a zero placeholder; an all-letter one is a word, not an issued credential)."""
    value = value.strip("\"'`")
    if len(value) < 8 or _LOWER_WORD_CHAIN.match(value) or value.endswith("="):
        return False                    # "NEXT_VAR=": an empty assignment reads the next token
    if _DOTTED_REFERENCE.fullmatch(value) or _ENV_NAME.fullmatch(value):
        return False
    return any(c.isalpha() for c in value) and any(c.isdigit() for c in value)


# Keys that name a secret when a variable flows into them ('client_secret': CSEC, api_key=KEY).
_SECRET_KEY_WORDS = r"(?:secret|password|passwd|token|api[_-]?key|access[_-]?key|private[_-]?key)"
# Words that stand where a value would, in prose about passwords (not values).
_PASSWORD_WORDS = frozenset(("password", "passwd", "pwd", "pass", "field", "env-get", "prompt",
                             "manager", "reset", "policy", "hash", "hashed"))
# An address at a reserved documentation domain (RFC 2606/6761) is an example, never a login.
_RESERVED_EMAIL_DOMAIN = (r"(?!(?:[A-Za-z0-9-]+\.)*(?:example\.(?:com|net|org)|localhost"
                          r"|[A-Za-z0-9-]+\.(?:example|test|invalid|localhost))\b)")


def _midline_value(value: str) -> bool:
    """A value behind a spaced key label mid-line ("the api key: X header", "pass: X"): a labelled
    password value in mixed case with a digit or symbol, or with both a digit and a symbol. Slugs,
    hex ids and single-case words are not (measured on agent memory: "pass=harvest-2", "PASS:
    7fc8d6ab", "the distill pass: Extract ..." are labels and commit ids, not passwords)."""
    if value.startswith(("\\", "?", "#")):
        return False                    # an escaped newline ("\\n\\n**Done") or a query fragment ("?v=1#x")
    value = value.strip("\"'`*_.,;:!?()-")
    if "://" in value or not _labelled_password_value(value):
        return False
    upper, lower = any(c.isupper() for c in value), any(c.islower() for c in value)
    digit = any(c.isdigit() for c in value)
    symbol = any(not c.isalnum() and c not in "-_." for c in value)
    return (upper and lower and (digit or symbol)) or (symbol and digit and (upper or lower))


# An env read: its value lives outside the text, so the key it is assigned to holds no literal.
_ENV_READ = re.compile(r"(?:process\.env|import\.meta\.env|os\.environ|Deno\.env|ENV)\.")
# What may follow a whole expression on its line: closing punctuation, then the end or a comment.
_EXPRESSION_END = re.compile(r"[ \t]*([,;)\]}]*)[ \t]*(?:$|//|#|/\*)", re.M)
CODE_SEGMENT_WORD_SHARE = 0.6   # RequestId 0.78, authToken 1.0; random runs (Kq9z, vLm2pXr4tw) score under 0.4


def _word_like(segment: str) -> bool:
    """A member name: one-case letters (os, props, DB_PASSWORD) or mostly word-like pieces."""
    letters = segment.replace("_", "").replace("$", "")
    if not letters:
        return False
    if letters.isalpha() and (letters.islower() or letters.isupper()):
        return True
    return sum(len(piece) for piece in _WORDISH.findall(letters)) / len(letters) >= CODE_SEGMENT_WORD_SHARE


def _code_reference(match: re.Match) -> bool:
    """True when a credential-named key is given a code reference, not a literal: an
    env read (process.env.X) or a member access (request.BackfillRequestId, settings.DB_X).
    The value must be unquoted, a dotted reference, and the whole expression: only closing
    punctuation, then the line end or a comment, may follow, and no quote may follow on that
    line (an env read joined to a literal with + or || still counts). An env read always passes;
    any other reference needs word-like member names, a separator other than a bare `=` (in an
    env file a dotted value is a literal), and, with no `,` `;` `)` after it, a lowercase
    receiver (a capitalised dotted value at a line end reads as a YAML passphrase)."""
    text, start = match.string, match.start("v")
    value = match.group("v")
    if start and text[start - 1] in "\"'`" or not _DOTTED_REFERENCE.fullmatch(value):
        return False
    tail = _EXPRESSION_END.match(text, match.end("v"))
    line_end = text.find("\n", match.end("v"))
    if not tail or any(q in text[match.end("v"):None if line_end < 0 else line_end] for q in "\"'`"):
        return False                    # a quoted literal later on the line, even in a comment, counts
    if _ENV_READ.match(value):
        return True
    if re.search(r"\w=$", text[match.start():start]):
        return False
    if not tail.group(1) and not (value[0].islower() or value[0] in "_$"):
        return False
    return all(_word_like(segment) for segment in value.split("."))


def _basic_auth_value(value: str) -> bool:
    """The credential after an HTTP Basic label: `user:password` with a password-like part, or a
    base64 blob that decodes to printable `user:password` with one (the Authorization header form;
    a decoded placeholder pair such as user:pass is not a credential)."""
    value = value.strip("\"'`.,;")
    if ":" in value:
        user, _, password = value.partition(":")
        return bool(user) and "/" not in user and _labelled_password_value(password)
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return False
    user, sep, password = decoded.partition(":")
    return bool(sep and user) and decoded.isprintable() and _labelled_password_value(password)


def _real_token_body(body: str) -> bool:
    """A prefixed token's body counts unless it is an obvious fake (FAKE..., 0000...)."""
    lowered = body.lower()
    return not is_placeholder_value(body) and len(set(lowered)) > 2 \
        and not lowered.startswith(("fake", "dummy", "example", "placeholder", "xxxx"))


# Page-kind prefixes of wiki stems (gotcha-, topic-, arch-, ...): not part of the title's words.
