"""Pre-send redaction for repact-distill (KB lite layers R1 and R2). Fails closed.

Location: pact-plugin/bin/_distill/redact.py. bin/repact-distill loads it as the
redaction seam: redact_chunk(chunk) -> Chunk | None and detector_fingerprint(). None (or a raise)
means `skipped_redaction`: the chunk is never sent. postcheck.py (R3) reuses residual_kinds() and
load_lists() on the model's output.

R1, per chunk: NFKC with default-ignorable characters removed; home paths to `~`; the shared
detector (shared/scrub.scrub_text: every secret class, the denylist and its word list, plus the
profile identity); PII (email, also as `at`/`dot` or `[at]`; IPv4, also a private-range or URL-host
integer; Slack id; phone); encoded tokens (URL, entity, \\u/\\x, base64, hex, char-spaced, dotted
letters) whose decoded text is sensitive are replaced whole; denylist names also in leet digits. Then a residual check runs on the
REDACTED text through the leak gate's own views (tests/contract/_purity.py: folded, joined,
decoded) and a line-joined view for split tokens: anything still detected skips the chunk.
Redact what can be located, skip what is only visible in a derived view.
R2: more than DENSITY_PER_KB redactions per KB skips the chunk (likely a data dump).

The denylist is optional (scrub.denylist_configured): none configured runs the structural
classes and says so; configured but missing, unreadable or empty skips every chunk.
Skip reasons are counted by kind (never a value) for the report: skip_counts().
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import ipaddress
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import _repact_common  # noqa: F401  # side effect: puts hooks/ on sys.path for shared.*
from shared import scrub
from shared.plugin_paths import owner_identity
from shared.scrub_values import _reportable_email

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
PURITY_PATH = PLUGIN_ROOT / "tests" / "contract" / "_purity.py"
_VIEWS_NAME = "_purity_views_for_distill"
_VIEW_API = ("fold", "_folded", "_joined", "_join_key", "_decoded", "_literal_spans",
             "_unescape", "_base64_text", "_ignorable", "_URL", "_ENTITY", "_ESCAPE", "_BASE64",
             "BASE64_MIN", "JOIN_MIN")
LAYER_VERSION = "r1r2-1"
MAX_CHUNK_BYTES = 2 * 1024 * 1024    # a chunk is ~115 KB (50K tokens); far larger is not a chunk
NAME_PASSES = 3
DENSITY_PER_KB = 5.0                 # design note §5.1 R2, BELIEVED start value
MARKER = "[REDACTED:"

STATE_NONE, STATE_LOADED, STATE_BROKEN = "none configured", "loaded", "broken"
SKIP_NOT_TEXT = "input is not text"
SKIP_TOO_LARGE = "input too large"
SKIP_BAD_UNICODE = "input is not valid unicode"
SKIP_DENYLIST = "denylist broken"
SKIP_VIEWS = "redaction views unavailable"
SKIP_DENSITY = "redaction density"
SKIP_DETECTOR = "detector error"
SKIP_RESIDUAL = "residual {}"        # filled with a residual kind

# Secret classes anchored on a fixed prefix: the ones a line break can split mid-value.
SPLIT_KINDS = frozenset({"anthropic-key", "api-key", "slack-token", "aws-key", "github-token",
                         "gitlab-token", "google-key", "jwt", "prefixed-token", "private-key"})
_LINE_JOIN = re.compile(r"(?:\\\r?\n|\\n|\r?\n)[ \t>+|*-]*")   # a break and the indent after it

HOME_PATH = re.compile(r"(?<![\w.~])(?:/(?:Users|home)/|[A-Za-z]:\\Users\\)"
                       r"[A-Za-z0-9][A-Za-z0-9._-]*")
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IPV4 = re.compile(rf"(?<![\w.]){_OCTET}(?:\.{_OCTET}){{3}}(?![\w]|\.\d)")
_IPV4_HARMLESS = re.compile(r"^(?:127\.|0\.0\.0\.0$|255\.255\.)")
SLACK_ID = re.compile(r"\b[UW]0[A-Z0-9]{7,9}\b")
PHONE = re.compile(r"(?<![\w+])(?:\+\d{1,3}[ .-]?(?:\(\d{1,4}\)[ .-]?)?\d{1,4}(?:[ .-]\d{2,4}){1,4}"
                   r"|\(\d{3}\)[ .-]?\d{3}[ .-]\d{4}|\d{3}[.-]\d{3,4}[.-]\d{4})(?![\w])")
PHONE_DIGITS = (8, 15)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")         # "+2026-10-17" in a diff line is a date
_EMAIL = next(p.regex for p in scrub.SCRUB_PATTERNS if p.kind == "email")
# An address written out: `name at host dot org`, `name[at]host.org`. The bare word form needs a
# `dot` word too, so prose such as "look at main.py" is left alone.
_AT = r"(?:\s*[\[({]\s*at\s*[\])}]\s*|\s+at\s+)"
_DOT = r"(?:\s*[\[({]\s*dot\s*[\])}]\s*|\s+dot\s+)"
SPELLED_EMAIL = re.compile(rf"(?i)(?<![\w+.-])[\w+-]+(?:(?:\.|{_DOT})[\w+-]+)*{_AT}"
                           rf"[\w-]+(?:(?:\.|{_DOT})[\w-]+)+")
# An IPv4 address as one integer: in a URL host, or bare when it falls in a private range (a
# bare public one cannot be told from a count or a timestamp).
IPV4_INT = re.compile(r"(?<![\w.])\d{8,10}(?![\w.])")
# (first octet, second octet, prefix): RFC 1918, link-local and carrier-grade NAT.
_PRIVATE_NETS = tuple(ipaddress.ip_network((bytes((a, b, 0, 0)), n))
                      for a, b, n in ((10, 0, 8), (172, 16, 12), (192, 168, 16), (169, 254, 16),
                                      (100, 64, 10)))
# Encodings the leak gate's views do not decode: hex, one character per space, dotted letters.
HEX = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}){8,}(?![0-9A-Fa-f])")
SPACED = re.compile(r"(?<!\S)\S(?: \S){7,}(?!\S)")
DOTTED = re.compile(r"(?<![^\W_])[^\W_](?:[.\-_][^\W_]){3,}(?![^\W_])")
_LEET = tuple(str.maketrans("013457@$", f"o{one}eastas") for one in "il")


class ViewsUnavailable(RuntimeError):
    """The leak gate's view helpers could not be loaded: nothing can be verified."""


class ListsBroken(RuntimeError):
    """A configured denylist is missing, unreadable or empty."""


@dataclass(frozen=True)
class Lists:
    """What names are checked: substring literals and whole-word entries (identity included)."""
    state: str
    literals: tuple
    words: tuple
    detail: str


_SKIPS: Counter = Counter()
_VIEWS: dict = {}


def skip_counts() -> dict:
    """Skip reason -> chunks skipped by this process. Reasons are kind names, never values."""
    return dict(_SKIPS)


def views():
    """The _purity module (the leak gate's fold and view helpers), loaded once by path."""
    if "module" in _VIEWS:
        return _VIEWS["module"]
    try:
        spec = importlib.util.spec_from_file_location(_VIEWS_NAME, PURITY_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_VIEWS_NAME] = module          # its dataclasses resolve by module name
        spec.loader.exec_module(module)
        for name in _VIEW_API:
            getattr(module, name)
    except Exception as exc:  # any load failure: nothing can be verified, so nothing is sent
        sys.modules.pop(_VIEWS_NAME, None)
        raise ViewsUnavailable(type(exc).__name__) from None
    _VIEWS["module"] = module
    return module


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _identity_entries() -> tuple:
    """(literals, words) from profile identity: the email and Slack id as literals, the name
    whole and each part of 3+ characters as whole words."""
    identity = owner_identity()
    literals = tuple(identity[key] for key in ("email", "slack_id") if identity.get(key))
    name = identity.get("name", "")
    words = tuple({name, *(part for part in re.split(r"[\s.,-]+", name) if len(part) >= 3)}
                  - {""})
    return literals, words


def load_lists() -> Lists:
    """The denylist, its word list and the identity. Raises ListsBroken when a list is
    configured but missing, unreadable or empty (callers fail closed). The word list is
    optional, but one named by its own variable must exist like the denylist."""
    id_literals, id_words = _identity_entries()
    named_words = os.environ.get(scrub.ENV_DENYLIST_WORDS)
    if named_words and not Path(named_words).expanduser().is_file():
        raise ListsBroken("the configured denylist word file is missing")
    if not scrub.denylist_configured():
        return Lists(STATE_NONE, _norm(id_literals), _norm(id_words), "names are not checked")
    listed, word_file = scrub.denylist_paths()
    if not listed.is_file():
        raise ListsBroken("the configured denylist file is missing")
    try:
        literals = scrub.load_denylist(listed, strict=True)
        words = scrub.load_denylist(word_file, strict=True) if word_file.exists() else []
    except (OSError, UnicodeDecodeError) as exc:
        raise ListsBroken(f"a denylist file is unreadable ({type(exc).__name__})") from None
    if not literals:
        raise ListsBroken("the configured denylist is empty")
    return Lists(STATE_LOADED, _norm(literals + list(id_literals)), _norm(words + list(id_words)),
                 f"{len(literals)} entries, {len(words)} word entries")


def _norm(entries) -> tuple:
    return tuple(sorted({_nfkc(entry).strip() for entry in entries if entry and entry.strip()}))


def denylist_status() -> str:
    """One line for the report: none configured, loaded (counts) or broken (why). No values."""
    try:
        lists = load_lists()
    except ListsBroken as exc:
        return f"{STATE_BROKEN}: {exc} (every chunk is skipped)"
    except Exception as exc:  # report it; redact_chunk fails closed on the same error
        return f"{STATE_BROKEN}: {type(exc).__name__} (every chunk is skipped)"
    return f"{lists.state} ({lists.detail})"


def _phone(value: str) -> bool:
    digits = sum(c.isdigit() for c in value)
    return PHONE_DIGITS[0] <= digits <= PHONE_DIGITS[1] and not _DATE.search(value)


def _ip(value: str) -> bool:
    return not _IPV4_HARMLESS.match(value)


def _ip_int(value: str, in_url: bool) -> bool:
    number = int(value)
    if not 2 ** 24 <= number < 2 ** 32:
        return False
    address = ipaddress.IPv4Address(number)
    return _ip(str(address)) and (in_url or any(address in net for net in _PRIVATE_NETS))


def _spelled_email(value: str) -> bool:
    if not re.search(r"[\[({]\s*at\b|\bdot\b", value, re.I):
        return False
    plain = re.sub(_DOT, ".", re.sub(_AT, "@", value, count=1, flags=re.I), flags=re.I)
    return bool(_EMAIL.fullmatch(plain)) and _reportable_email(plain)


# (kind, regex, accept): the PII classes R1 redacts and every residual check looks for.
PII_PATTERNS = (("email", _EMAIL, _reportable_email), ("email", SPELLED_EMAIL, _spelled_email),
                ("ipv4", IPV4, _ip), ("ipv4", re.compile(r"(?<=//|.@)\d{8,10}(?![\w.])"),
                                       lambda v: _ip_int(v, True)),
                ("ipv4", IPV4_INT, lambda v: _ip_int(v, False)),
                ("slack-id", SLACK_ID, None), ("phone", PHONE, _phone))


def _redact_pii(text: str) -> str:
    for kind, regex, accept in PII_PATTERNS:
        text = regex.sub(lambda m, k=kind, a=accept: m.group(0) if a and not a(m.group(0))
                         else f"{MARKER}{k}]", text)
    return text


def _pii_kind(text: str) -> Optional[str]:
    if HOME_PATH.search(text):
        return "home-path"
    for kind, regex, accept in PII_PATTERNS:
        if any(not accept or accept(m.group(0)) for m in regex.finditer(text)):
            return kind
    return None


def _name_spans(text: str, lists: Lists) -> list:
    """(start, end) in text of every denylist or identity entry seen in its folded, joined or
    decoded views (a decoded hit spans the whole encoded token)."""
    v = views()
    literals = [v.fold(item) for item in lists.literals if v.fold(item)]
    words = [v.fold(item) for item in lists.words if v.fold(item)]
    joined = [key for key in map(v._join_key, lists.literals + lists.words)
              if len(key) >= v.JOIN_MIN]
    if not (literals or words):
        return []
    spans = []
    leet = [v._folded(form) for form in {text.translate(t) for t in _LEET} - {text}]
    for view in (v._folded(text), v._decoded(text), *leet):     # translate keeps every offset
        joined_view = v._joined(view)
        for target, entries, whole in ((view, literals, False), (view, words, True),
                                       (joined_view, joined, False)):
            for start, end in v._literal_spans(target.text, entries, whole) if entries else ():
                spans.append((target.starts[start], target.ends[end - 1]))
    return spans


def _name_hit(text: str, lists: Lists) -> bool:
    return bool(_name_spans(text, lists))


def _redact_names(text: str, lists: Lists) -> str:
    """Replace every name the views locate (an ASCII-folded, wrapped or encoded form that
    scrub_text's literal match does not see). Bounded passes; the residual check decides."""
    for _ in range(NAME_PASSES):
        spans = _merge(_name_spans(text, lists))
        if not spans:
            break
        for start, end in reversed(spans):
            text = text[:start] + f"{MARKER}denylist]" + text[end:]
    return text


def _decoded_tokens(text: str):
    """(start, end, decoded) for every encoded token in text, decoded but not folded."""
    v = views()
    for regex in (v._URL, v._ENTITY, v._ESCAPE):
        for match in regex.finditer(text):
            decoded = v._unescape(match.group(0))
            if decoded != match.group(0):
                yield match.start(), match.end(), decoded
    for match in v._BASE64.finditer(text):
        if len(match.group(0)) >= v.BASE64_MIN:
            decoded = v._base64_text(match.group(0))
            if decoded:
                yield match.start(), match.end(), decoded
    for regex, decode in ((HEX, _hex_text), (SPACED, lambda t: t.replace(" ", "")),
                          (DOTTED, lambda t: re.sub(r"[.\-_]", "", t))):
        for match in regex.finditer(text):
            decoded = decode(match.group(0))
            if decoded:
                yield match.start(), match.end(), decoded


def _hex_text(token: str) -> Optional[str]:
    """The text a hex token spells, or None when it is not printable UTF-8 (a hash, an id)."""
    try:
        decoded = bytes.fromhex(token).decode("utf-8")
    except ValueError:
        return None
    return decoded if decoded.isprintable() else None


def _sensitive(decoded: str, lists: Lists) -> bool:
    return bool(scrub.secret_kind(decoded) or _pii_kind(decoded) or _name_hit(decoded, lists))


def _redact_encoded(text: str, lists: Lists) -> str:
    spans = [(start, end) for start, end, decoded in _decoded_tokens(text)
             if _sensitive(decoded, lists)]
    for start, end in sorted(_merge(spans), reverse=True):
        text = text[:start] + f"{MARKER}encoded]" + text[end:]
    return text


def _merge(spans: list) -> list:
    merged: list = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _split_secret(text: str) -> bool:
    """A prefix-anchored secret that only appears once line breaks are removed."""
    joined = _LINE_JOIN.sub("", text)
    if joined == text:
        return False
    for pattern in scrub.SCRUB_PATTERNS:
        if pattern.kind in SPLIT_KINDS and any(scrub._hit(pattern, m)
                                               for m in pattern.regex.finditer(joined)):
            return True
    return False


def residual_kinds(text: str, lists: Lists) -> list:
    """What is still detectable in text: [] when clean. Kinds only, never values. Raises
    ViewsUnavailable when the views cannot load (callers fail closed)."""
    found = []
    if scrub.secret_kind(text):
        found.append("secret")
    if _split_secret(text):
        found.append("split secret")
    if _pii_kind(text):
        found.append("pii")
    if _name_hit(text, lists):
        found.append("denylist")
    if any(_sensitive(decoded, lists) for _start, _end, decoded in _decoded_tokens(text)):
        found.append("encoded")
    return found


def _strip_ignorable(text: str) -> str:
    if text.isascii():
        return text
    ignorable = views()._ignorable
    return "".join(char for char in text if not ignorable(char))


def redact_text(text: str, lists: Lists) -> str:
    """R1 over one text: normalise, redact, return. No residual or density check here."""
    text = _strip_ignorable(_nfkc(text))
    # Home paths go first: a denylist name inside /Users/<name> would otherwise break the path
    # shape. Emails wait until after scrub_text, whose email-password class needs the address.
    text = HOME_PATH.sub("~", text)
    text = scrub.scrub_text(text, lists.literals, lists.words)
    text = _redact_pii(text)
    return _redact_names(_redact_encoded(text, lists), lists)


def _skip(reason: str) -> None:
    _SKIPS[reason] += 1
    return None


def _check_input(chunk) -> Optional[str]:
    """A skip reason for input that cannot be redacted safely, else None."""
    text = getattr(chunk, "text", None)
    if not isinstance(text, str):
        return SKIP_NOT_TEXT
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return SKIP_BAD_UNICODE
    return SKIP_TOO_LARGE if size > MAX_CHUNK_BYTES else None


def redact_chunk(chunk):
    """The chunk with its text redacted, or None (skipped_redaction: never sent).

    chunk_key and view_sha256 keep the values of the text as read, so the ledger key does not
    move when a detector changes (the ledger stores detector_fingerprint for that)."""
    try:
        reason = _check_input(chunk)
        if reason:
            return _skip(reason)
        lists = load_lists()
        clean = redact_text(chunk.text, lists)
        residual = residual_kinds(clean, lists)
        if residual:
            return _skip(SKIP_RESIDUAL.format(residual[0]))
        added = clean.count(MARKER) - chunk.text.count(MARKER)
        if added / max(len(clean.encode("utf-8")) / 1024, 1.0) > DENSITY_PER_KB:
            return _skip(SKIP_DENSITY)
        return dataclasses.replace(chunk, text=clean)
    except ListsBroken:
        return _skip(SKIP_DENYLIST)
    except ViewsUnavailable:
        return _skip(SKIP_VIEWS)
    except Exception as exc:  # a detector that raises skips the chunk, never sends it
        return _skip(f"{SKIP_DETECTOR} ({type(exc).__name__})")


def detector_fingerprint() -> str:
    """The shared detector's fingerprint plus this layer's code, the views' code and the
    thresholds: any change re-fingerprints the ledger rows. Never raises."""
    parts = [LAYER_VERSION, repr((DENSITY_PER_KB, MAX_CHUNK_BYTES))]
    for label, part in (("scrub", scrub.detector_fingerprint), ("layer", _file_hash(__file__)),
                        ("views", _file_hash(PURITY_PATH))):
        try:
            parts.append(f"{label}={part() if callable(part) else part}")
        except Exception as exc:  # recorded, not raised: redact_chunk fails closed on its own
            parts.append(f"{label}=error:{type(exc).__name__}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _file_hash(path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return "unavailable"
