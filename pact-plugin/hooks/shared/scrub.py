"""
Location: pact-plugin/hooks/shared/scrub.py
Summary: The one secret detector: a pattern table (SCRUB_PATTERNS) to redact secrets before
         egress (scrub_text / scrub_value), scan text that must not enter git (scan_text,
         scan_added_lines with the allowlist pragma), name the kind of a secret (secret_kind),
         keep credential notes out of recall (credential_reason, D25) and fingerprint the
         detector for caches. Imports only shared.scrub_values, so shell/ may import it.
Used by: hooks/git_commit_check.py, hooks/output_secret_scan.py, hooks/egress_guard.py,
         hooks/env_guard.py (git egress), telegram/content_filter.py, bin/scrub-check,
         bin/vault-find, bin/_recall/*, bin/_kb/*, shared/evidence/* (the bin ones via the
         bin/_repact_common shim). Doc: the design notes.
Map (file order): denylist paths, denylist_configured, normalise_title, ScrubPattern and
SCRUB_PATTERNS, SECRET_ONLY_KINDS, _matched_value, _hit, _first_heading, _code_fingerprint,
detector_fingerprint, credential_reason, ALLOWLIST_PRAGMA, _secret_value_spans,
_distinct_values, pragma_covers, scan_added_lines, _HUNK,
_diff_path, pragma_notice, secret_kind, _denylist_regex, scrub_text, scrub_value, Finding,
load_denylist, scan_text.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from shared.plugin_paths import owner_identity
from shared.scrub_values import (  # noqa: F401  (re-exported for the bin shim)
    GIT_SHA_LENGTH, _RESERVED_EMAIL_DOMAIN, _SECRET_KEY_WORDS,
    _VALUE, _basic_auth_value, _basic_header_value, _code_reference, _credential_line_value,
    _credential_name_value, _labelled_password_value, _looks_like_secret_value,
    _midline_value, _not_cidr_anchor, _opaque_literal, _opaque_value, _real_token_body,
    _real_value, _reportable_email, _sql_password_value, _standalone_password,
    _url_key_value, _webhook_value, is_placeholder_value)


DEFAULT_DENYLIST = Path("~/.config/repacted/scrub-denylist.txt")
# Optional second list, matched as WHOLE WORDS: common first names that would substring-hit
# ordinary words (measured: 457 false hits across 28 names with substring matching).
DEFAULT_DENYLIST_WORDS = Path("~/.config/repacted/scrub-denylist-words.txt")
ENV_DENYLIST = "REPACT_SCRUB_DENYLIST"
ENV_DENYLIST_WORDS = "REPACT_SCRUB_DENYLIST_WORDS"


def denylist_paths() -> tuple[Path, Path]:
    """(denylist, word list): $REPACT_SCRUB_DENYLIST / $REPACT_SCRUB_DENYLIST_WORDS, else defaults."""
    return (Path(os.environ.get(ENV_DENYLIST) or DEFAULT_DENYLIST).expanduser(),
            Path(os.environ.get(ENV_DENYLIST_WORDS) or DEFAULT_DENYLIST_WORDS).expanduser())


def denylist_configured() -> bool:
    """True when this machine has a denylist set up: $REPACT_SCRUB_DENYLIST is set, or the
    default file exists (even empty). False is the documented stranger default ("denylist:
    none configured"): the structural classes still run. A configured list that is missing or
    empty is a different state, and callers that need names checked still fail closed on it.
    """
    return bool(os.environ.get(ENV_DENYLIST)) or os.path.lexists(DEFAULT_DENYLIST.expanduser())

# A value body never starts with a placeholder sigil, so prose such as
_TITLE_KIND_PREFIXES = frozenset(("gotcha", "topic", "arch", "synthesis", "compare", "workflow"))


def normalise_title(title: str) -> str:
    """A path stem or H1 as lowercase words, with leading page-kind prefixes dropped."""
    words = [word for word in re.split(r"[^a-z0-9]+", title.lower()) if word]
    while words and words[0] in _TITLE_KIND_PREFIXES:
        words = words[1:]
    return " ".join(words)


@dataclass(frozen=True)
class ScrubPattern:
    """One detector.

    kind: the label printed by scrub-check and used in [REDACTED:<kind>].
    regex: the detector; group "v", when present, is the part that gets redacted.
    secret: redact it before sending text off-machine (scrub_text/scrub_value).
    scan: report it in scrub-check (content that must not enter git).
    accept: optional predicate on the matched value; False means "not a hit".
    skip: optional predicate on the whole match, for the context around the value that accept
        does not see; True means "not a hit". _hit applies both.
    title: the regex applies to a note's PATH parts and H1 (normalise_title), never to body
        text; only credential_reason uses it, so scrub_text and scan_text skip it.
    """
    kind: str
    regex: re.Pattern
    secret: bool = True
    scan: bool = True
    accept: Optional[Callable[[str], bool]] = None
    title: bool = False
    skip: Optional[Callable[[re.Match], bool]] = None


SCRUB_PATTERNS: tuple[ScrubPattern, ...] = (
    # A note titled as a credential store (Passwords, Credentials, API Keys, Access Tokens,
    # Secrets, with at most two qualifier words either side). Path and H1 only.
    ScrubPattern("credential-title", re.compile(
        r"^(?:[a-z0-9]+ ){0,2}(?:passwords?|credentials|api keys?|access tokens?|secrets)"
        r"(?: [a-z0-9]+){0,2}$"), scan=False, title=True),
    # The inert marker every integration test plants instead of anything real-shaped.
    ScrubPattern("test-secret", re.compile(r"\bREPACT_TEST_SECRET_[A-Z0-9]{8,}\b"), scan=False),
    ScrubPattern("private-key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----)?", re.S)),
    # Bare prefixed tokens: Supabase access tokens and secret keys, underscore-style live keys.
    ScrubPattern("prefixed-token", re.compile(
        r"\b(?:sbp_(?=[0-9a-f]{40}\b)|sb_secret_(?=[A-Za-z0-9_-]{20,})|(?:sk|rk)_live_(?=[A-Za-z0-9]{16,}))"
        r"(?P<v>[A-Za-z0-9_-]+)"),
        accept=_real_token_body),
    # Webhook URLs whose path is the credential: anyone holding one can post. A host
    # alone, or a path of placeholders (T000/B000/XXXX), is documentation, not a hit.
    ScrubPattern("webhook-url", re.compile(
        r"(?i)\bhooks\.slack\.com/(?:services|workflows|triggers)/(?:[A-Za-z0-9]+/){1,3}"
        r"(?P<v>[A-Za-z0-9]{16,})"), accept=_webhook_value),
    ScrubPattern("webhook-url", re.compile(
        r"(?i)\b(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\d{15,22}/"
        r"(?P<v>[A-Za-z0-9_-]{40,})"), accept=_webhook_value),
    ScrubPattern("webhook-url", re.compile(
        r"(?i)\b[A-Za-z0-9-]+\.webhook\.office\.com/(?P<v>webhookb2/[A-Za-z0-9@/_-]{40,})"),
        accept=_webhook_value),
    ScrubPattern("webhook-url", re.compile(
        r"(?i)\bchat\.googleapis\.com/v1/spaces/[^\s/?]+/messages\?"
        r"(?P<v>[^\s\"'<>`]*?\bkey=[^\s\"'<>`]{20,})"), accept=_webhook_value),
    # A GCP service-account key file's private_key_id (40 hex): named by its key, never a bare
    # 40-hex run (git SHAs are 40 hex too).
    ScrubPattern("gcp-key-id", re.compile(
        r"(?i)\bprivate_key_id[\"']?[ \t]*[:=][ \t]*[\"']?(?P<v>[0-9a-f]{40})(?![0-9a-f])"),
        accept=_real_token_body),
    # A note line that labels a value as a password, key, token or secret, the value being
    # the rest of the line (plaintext passwords have no token shape to match). A password KEY
    # may carry a prefix: camelCase (adminPassword, smtpPass) or UPPER_SNAKE (DB_PASSWORD,
    # MYSQL_ROOT_PWD); the password word must end the key, so passwordHash is not one.
    ScrubPattern("credential-line", re.compile(
        r"(?im)^[ \t]*(?:[-*+>][ \t]+)?[*_]{0,2}(?:password|passwd|pass|pwd|api[ _-]?key|token|secret"
        r"|(?-i:[a-z][a-zA-Z0-9]*(?:Password|Passwd|Pass|Pwd)|(?:[A-Z][A-Z0-9]*_)+(?:PASSWORD|PASSWD|PASS|PWD)))"
        r"[*_]{0,2}[ \t]*:[*_]{0,2}[ \t]*`?(?P<v>" + _VALUE + r"[^\s`]{3,})`?[ \t]*$"),
        accept=_real_value(_credential_line_value)),
    # Plaintext logins (FIX-D25-SHAPES, after HALT #203): an address paired with a password-like
    # token through a spaced '/': "(user@example.com / <pw>)" (at a real domain; a reserved
    # documentation domain never counts). Measured on the transcript corpus: '|' is a table
    # column and an unspaced '/' a URL path, and a comma starts the next list item, so none of
    # them is a separator. The address is not redacted, only the token.
    ScrubPattern("email-password", re.compile(
        r"(?<![A-Za-z0-9._%+:/\[\]-])[A-Za-z0-9][A-Za-z0-9._%+-]*@" + _RESERVED_EMAIL_DOMAIN +
        r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b[ \t]+/[ \t]+"
        r"(?:(?:pass(?:word)?|pwd)[ \t]*[:=][ \t]*)?(?P<v>" + _VALUE + r"[^\s)\]\"'`,;]{5,})"),
        accept=_real_value(_standalone_password)),
    # A password label anywhere in a line ("the admin pwd=X", "use password: X"); credential-line
    # covers the whole-line form, secret-assignment the UPPER_CASE names. A bare "pass:" is not a
    # label here (prose: "the distill pass: ..."); it counts only inside a login pair below.
    ScrubPattern("password-inline", re.compile(
        r"(?i)(?<![\w-])(?:password|passwd|pwd)[*_]{0,2}[ \t]*[:=][*_]{0,2}[ \t]*[\"'`]?"
        r"(?P<v>" + _VALUE + r"[^\s\"'`,;()\[\]{}]{2,})"),
        accept=_real_value(_labelled_password_value)),
    # A SQL password literal: CREATE/ALTER USER|ROLE ... PASSWORD 'x', MySQL IDENTIFIED BY 'x'.
    # Bind parameters ($1), expansions ('${X}'), empty strings and placeholders are not values.
    ScrubPattern("sql-password", re.compile(
        r"(?i)(?<![\w-])(?:password|identified[ \t]+by)[ \t]+'(?P<v>" + _VALUE + r"[^'\s]{3,})'"),
        accept=_real_value(_sql_password_value)),
    # Prose pairs: "username X password Y", "login as user X and password Y".
    ScrubPattern("login-password", re.compile(
        r"(?i)\b(?:user(?:name)?|login)\b[ \t]*[:=]?[ \t]*(?!-)\S+[ \t]*[,;/]?[ \t]*(?:and[ \t]+)?"
        r"(?:password|passwd|pwd|pass)\b(?:[ \t]*[:=][ \t]*|[ \t]+(?:is[ \t]+)?)[\"'`]?"
        r"(?P<v>" + _VALUE + r"[^\s\"'`,;()\[\]{}]{3,})"),
        accept=_real_value(_labelled_password_value)),
    # A secret in a variable whose NAME gives no hint (CSEC = '...'), caught by where it flows:
    # the same name used later as the value of a secret key ('client_secret': CSEC, api_key=KEY).
    # Only the literal is redacted. An id flowing into client_id, or a hash, is not a hit.
    ScrubPattern("credential-flow", re.compile(
        r"(?m)^[ \t]*(?P<name>[A-Za-z_][A-Za-z0-9_]{1,30})[ \t]*=[ \t]*(?P<q>['\"])"
        r"(?P<v>[A-Za-z0-9._~+/=-]{16,})(?P=q)"
        r"(?=[\s\S]{0,3000}?['\"]?[A-Za-z_]*" + _SECRET_KEY_WORDS + r"[A-Za-z_]*['\"]?[ \t]*[:=][ \t]*(?P=name)\b)"),
        accept=_real_value(_opaque_literal)),
    # A short credential VARIABLE given a literal in shell or code (lead rejection 1 on #205):
    # CSEC=..., export CID="...", CLIENT_SECRET = '...', API_KEY: ... (optionally PREFIX_-ed).
    # A shell '=' takes no spaces (`X= next` is an empty assignment); a code '=' takes a quoted value.
    # `${API_KEY:-default}` is a shell expansion, not an assignment.
    ScrubPattern("credential-name", re.compile(
        r"(?<![\w${])(?:export[ \t]+)?(?:[A-Z][A-Z0-9]*_)?(?:CID|CSEC|CLIENT_ID|CLIENT_SECRET|CLIENT_SEC|API_?KEY)"
        r"(?![\w])[\"']?(?:[ \t]*=[ \t]*(?=[\"'])|=(?=\S)|[ \t]*:[ \t]*)[\"']?"
        r"(?P<v>" + _VALUE + r"[^\s\"'`,;()\[\]{}]{7,})"),
        accept=_real_value(_credential_name_value)),
    # A `user:password` CLI flag: --basic-auth="u:p", --http-auth u:p, curl -u u:p. Variables
    # ($USER:$PASS), uid:gid pairs, plain-word user:group pairs and URLs (sqlmap -u "http://...")
    # are not credentials.
    ScrubPattern("cli-auth-flag", re.compile(
        r"(?i)(?:(?<![\w-])--[a-z-]*auth[a-z-]*|(?<![\w-])-u)(?:=|[ \t]+)[\"']?"
        r"[^\s:\"'@/$<{\[]+:(?!//)(?P<v>" + _VALUE + r"[^\s\"'@]{2,})"),
        accept=_real_value(_labelled_password_value)),
    # A key in a URL (the hardening pass amend_1): a share/report/access path segment holding a long
    # random value (host/report/<id>/<key>), or a key-named query parameter (?key=, share=,
    # token=, access_key=, X-Amz-Signature=) with one. Context AND length/entropy are both
    # required, and the path form needs a dotted host (a local ~/.local/share/<hash> is not a URL).
    ScrubPattern("url-key-path", re.compile(
        r"(?i)(?<![\w./-])(?:https?://)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::\d+)?(?:/[^\s/\"'<>`]*)*?"
        r"/(?:reports?|share[sd]?|embed|invite|access)/(?:[A-Za-z0-9_-]{1,40}/)?"
        r"(?P<v>[A-Za-z0-9_-]{32,})(?![A-Za-z0-9_-])"),
        accept=_real_value(_url_key_value(32))),
    ScrubPattern("url-key-param", re.compile(
        r"(?i)[?&;#](?:x-amz-signature|signature|sig|share(?:[_-]?(?:key|token))?|access[_-]?(?:key|token)"
        r"|api[_-]?key|auth(?:[_-]?token)?|token|key|secret)=(?P<v>" + _VALUE + r"[A-Za-z0-9._~%+/-]{19,})"),
        accept=_real_value(_url_key_value(20))),
    # The same key named in prose or config (the #207 miss: "report 1127 (share key <key>)").
    ScrubPattern("share-key", re.compile(
        r"(?i)(?<![\w-])share[ _-]?(?:key|token)[*_]{0,2}[ \t]*[:=]?[*_]{0,2}[ \t]*[\"'`]?"
        r"(?P<v>" + _VALUE + r"[A-Za-z0-9_-]{15,})"),
        accept=_real_value(_url_key_value(16))),
    ScrubPattern("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ScrubPattern("api-key", re.compile(r"\b(?:sk|pk|rk)-(?:live-|test-|proj-)?[A-Za-z0-9_-]{20,}")),
    ScrubPattern("slack-token", re.compile(r"\bxox[abprs]-[0-9]{6,}[A-Za-z0-9-]*")),
    ScrubPattern("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ScrubPattern("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}")),
    ScrubPattern("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ScrubPattern("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ScrubPattern("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")),
    ScrubPattern("bearer-token", re.compile(r"(?i)\bbearer\s+(?P<v>" + _VALUE + r"[A-Za-z0-9._~+/=-]{15,})"),
                 accept=_real_value()),
    # A host:port userinfo (http://localhost:3000@evil.test) is a port. pragma: allowlist secret
    # The user may be empty (a redis URL with nothing before the colon), but then a bare port is not.
    ScrubPattern("password-in-url", re.compile(
        r"\b[a-z][a-z0-9+.-]*://(?!(?:localhost|\d{1,3}(?:\.\d{1,3}){3}|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)"
        r":\d{1,5}@)(?:[^\s:/@]+|(?=:(?!\d{1,5}@))):(?P<v>" + _VALUE + r"[^\s@/]{2,})@"),
        accept=_real_value()),
    # A MySQL/MariaDB client password joined to the -p flag with no space; `-p` alone prompts and
    # `-p NAME` names a database.
    ScrubPattern("mysql-password", re.compile(
        r"(?<![\w-])(?:mysql(?:dump|admin|import|show|check|pump|binlog|sh)?|mariadb(?:-dump|-admin)?)\b"
        r"[^\n;&|]{0,300}?(?<![\w-])-p[\"']?(?P<v>" + _VALUE + r"[^\s\"'`;&|]{2,})"),
        accept=_real_value()),
    # The client's password variable: MYSQL_PWD=X mysql ..., export MYSQL_PWD='X'.
    ScrubPattern("mysql-password", re.compile(
        r"(?<![\w$])(?:MYSQL|MARIADB)_PWD[\"']?[ \t]*[:=][ \t]*[\"']?(?P<v>" + _VALUE + r"[^\s\"'`;&|]{2,})"),
        accept=_real_value()),
    ScrubPattern("secret-assignment", re.compile(
        r"(?<![$\w])[A-Za-z0-9_]*(?i:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY|ACCESS_KEY)"
        r"(?:_[A-Za-z0-9_]*)?[\"']?[ \t]*(?::=|[:=])[ \t]*[\"']?(?P<v>" + _VALUE + r"[^\s\"'`,;()\[\]{}]{5,})"
        r"(?![(\[.\w])"),
        accept=_real_value(_looks_like_secret_value), skip=_code_reference),
    ScrubPattern("api-key-header", re.compile(
        r"(?i)\b(?:x-n8n-api-key|x-api-key)[\"']?[ \t]*:[ \t]*[\"']?(?P<v>" + _VALUE + r"[A-Za-z0-9._~+/=-]{11,})"),
        accept=_real_value()),
    # A value behind a SPACED key label anywhere in a line (KB-GATE-FIX K1): "the api key: X
    # header", "secret key = X", "pass: X". credential-line covers only the whole-line form and
    # secret-assignment only joined names (API_KEY, SECRET_KEY); password/pwd are password-inline's.
    ScrubPattern("labelled-value", re.compile(
        r"(?<![\w-])(?:(?i:(?:api|secret|access|private|client|app|master|signing)[ \t-]keys?"
        r"|client[ \t-]secret)[*_]{0,2}[ \t]*[:=]|[Pp]ass(?:code|phrase)?[*_]{0,2}[ \t]*:)"
        r"[*_]{0,2}[ \t]*[\"'`]?(?P<v>" + _VALUE + r"[^\s\"'`,;()\[\]{}]{5,})"),
        accept=_real_value(_midline_value)),
    # HTTP Basic credentials after their label: "HTTP Basic admin:X", "basic auth: u:p",
    # "Authorization: Basic <base64 of user:pass>".
    ScrubPattern("basic-auth", re.compile(
        r"(?i)(?<![\w-])(?:authorization[ \t]*:[ \t]*basic|http[ \t-]basic(?:[ \t-]auth(?:entication)?)?"
        r"|basic[ \t-]auth(?:entication)?)[*_]{0,2}[ \t]*[:=]?[*_]{0,2}[ \t]*[\"'`]?"
        r"(?P<v>" + _VALUE + r"[^\s\"'`,;()\[\]{}]{5,})"),
        accept=_real_value(_basic_auth_value)),
    # The header form whose base64 is not a user:password pair basic-auth can read: an API key
    # as the user with an empty password, or an opaque blob.
    ScrubPattern("basic-auth", re.compile(
        r"(?i)(?<![\w-])authorization[\"']?[ \t]*[:=][ \t]*[\"']?basic[ \t]+"
        r"(?P<v>[A-Za-z0-9+/]{16,}={0,2})(?![A-Za-z0-9+/=])"),
        accept=_real_value(_basic_header_value)),
    # A run labelled `sha256:` is a digest of the text it sits in (a generated view's render
    # hash, D10 P5), never an identifier; an unlabelled run of any length still reports.
    ScrubPattern("hex-run", re.compile(r"(?<!sha256:)\b[A-Fa-f0-9]{32,}\b"), secret=False,
                 accept=lambda value: len(value) != GIT_SHA_LENGTH),
    ScrubPattern("opaque-run", re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+_-]{32,}={0,2}(?![A-Za-z0-9+/_-])"),
                 accept=_real_value(_opaque_value)),
    ScrubPattern("email", re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9][A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
                 secret=False, accept=lambda value: _reportable_email(value)
                 and value.lower() != owner_identity().get("email", "").lower()),
    ScrubPattern("private-ip", re.compile(
        r"(?<![\d.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.]*\d)"),
        secret=False, accept=_not_cidr_anchor),
    ScrubPattern("slack-user-id", re.compile(r"\b[UW]0[A-Z0-9]{7,9}\b"),
                 secret=False, accept=lambda value: value != owner_identity().get("slack_id")),
)
# The owner's address and Slack id are the profile's identity.email and identity.slack_id (D8a):
# no literal here, so an unconfigured profile exempts nothing.

# Credentials only: every secret=True TEXT class except opaque-run (too noisy on prose copies).
# No PII classes, no denylist, no title-only class (those never apply to text). Used by
# `scrub-check --secrets-only` on project-note copies and by credential_reason.
SECRET_ONLY_KINDS = frozenset(pattern.kind for pattern in SCRUB_PATTERNS
                              if pattern.secret and not pattern.title and pattern.kind != "opaque-run")


def _matched_value(pattern: ScrubPattern, match: re.Match) -> tuple[int, int, str]:
    if "v" in pattern.regex.groupindex and match.group("v") is not None:
        return match.start("v"), match.end("v"), match.group("v")
    return match.start(), match.end(), match.group(0)


def _hit(pattern: ScrubPattern, match: re.Match) -> bool:
    """True when a regex match is a hit: its value passes accept and its context passes skip."""
    if pattern.accept and not pattern.accept(_matched_value(pattern, match)[2]):
        return False
    return not (pattern.skip and pattern.skip(match))


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


_FINGERPRINT_CACHE: dict = {}
_FINGERPRINT_DEPTH = 4


# The detector spans two modules since the layering pass; a helper from either is walked.
_DETECTOR_MODULES = frozenset({__name__, __name__.rsplit(".", 1)[0] + ".scrub_values"})


def _code_fingerprint(fn, depth: int = 0) -> str:
    """A stable description of what a detector callable does: its bytecode, constants, nested code,
    closure contents and the module-level values and helpers it names (recursively, bounded)."""
    code = getattr(fn, "__code__", None)
    if code is None or depth > _FINGERPRINT_DEPTH:
        return repr(fn)

    def describe(obj, level: int) -> str:
        if hasattr(obj, "co_code"):
            nested = ",".join(describe(c, level + 1) for c in obj.co_consts)
            return f"<{obj.co_code.hex()}|{nested}|{obj.co_names}>"
        if isinstance(obj, re.Pattern):
            return f"re({obj.pattern!r},{obj.flags})"
        if callable(obj) and getattr(obj, "__module__", None) in _DETECTOR_MODULES:
            return _code_fingerprint(obj, level + 1)
        if isinstance(obj, (frozenset, set)):
            return repr(sorted(map(repr, obj)))
        return repr(obj)

    parts = [describe(code, depth)]
    for cell in fn.__closure__ or ():
        parts.append(describe(cell.cell_contents, depth))
    scope = fn.__globals__   # the helper's own module: a classifier in scrub_values names its constants there
    for name in code.co_names + tuple(n for c in code.co_consts if hasattr(c, "co_names") for n in c.co_names):
        if name in scope and name != fn.__name__:
            value = scope[name]
            if isinstance(value, (re.Pattern, frozenset, set, str, int, tuple)) or \
                    (callable(value) and getattr(value, "__module__", None) in _DETECTOR_MODULES):
                parts.append(f"{name}={describe(value, depth)}")
    return "|".join(parts)


def detector_fingerprint() -> str:
    """A hash of the whole credential detector (every pattern, flag and accept predicate). Caches
    that decide withholding at build time (vault-find's index, recall's memory index) store it and
    rebuild when it changes, so a detector change can never leave a stale index serving a note it
    would now withhold (FIX-D25-SHAPES; replaces hand-bumped index versions for this purpose)."""
    key = id(SCRUB_PATTERNS)
    cached = _FINGERPRINT_CACHE.get(key)
    if cached and cached[0] is SCRUB_PATTERNS:
        return cached[1]
    digest = hashlib.sha256()
    for pattern in SCRUB_PATTERNS:
        digest.update(repr((pattern.kind, pattern.regex.pattern, pattern.regex.flags, pattern.secret,
                            pattern.scan, pattern.title)).encode())
        digest.update(_code_fingerprint(pattern.accept).encode() if pattern.accept else b"-")
        digest.update(_code_fingerprint(pattern.skip).encode() if pattern.skip else b"-")
    digest.update(repr(sorted(SECRET_ONLY_KINDS)).encode())
    value = digest.hexdigest()[:16]
    _FINGERPRINT_CACHE[key] = (SCRUB_PATTERNS, value)
    return value


def credential_reason(path: os.PathLike | str, text: str) -> Optional[str]:
    """The first credential kind a note carries, or None: the one exclusion test every
    recall and write surface calls (the design notes D9.12, D25).

    A credential-title on any path part or on the H1 is checked first; then every secret
    class of SECRET_ONLY_KINDS over the WHOLE text. opaque-run is not a reason (lead ruling):
    a long hash or base64 blob in prose is not a credential. Placeholder values never count.
    """
    titles = [normalise_title(part) for part in Path(str(path)).with_suffix("").parts]
    titles.append(normalise_title(_first_heading(text)))
    for pattern in SCRUB_PATTERNS:
        if pattern.title and any(title and pattern.regex.match(title) for title in titles):
            return pattern.kind
    return secret_kind(text)


ALLOWLIST_PRAGMA = "pragma: allowlist secret"
# A key block's first line is all a line scan sees of it (its body lines match nothing alone),
# so a pragma there would hide the whole key: no pragma ever covers one.
UNCOVERABLE_KINDS = frozenset(("private-key",))


def _secret_value_spans(text: str, uncoverable: Optional[list] = None) -> list[tuple[int, int]]:
    """(start, end) of every SECRET_ONLY_KINDS value the detector finds in text; the kind of
    each UNCOVERABLE_KINDS hit is appended to `uncoverable` when given."""
    spans = set()
    for pattern in SCRUB_PATTERNS:
        if pattern.kind not in SECRET_ONLY_KINDS:
            continue
        for match in pattern.regex.finditer(text):
            if _hit(pattern, match):
                start, end, _ = _matched_value(pattern, match)
                spans.add((start, end))
                if uncoverable is not None and pattern.kind in UNCOVERABLE_KINDS:
                    uncoverable.append(pattern.kind)
    return sorted(spans)


def _distinct_values(spans: Iterable[tuple[int, int]]) -> int:
    """How many values the spans mark. Two classes matching one value overlap and count once;
    a span that holds a narrower one is dropped first, so a wide match over two tokens counts
    as the two tokens it holds. O(n log n): walked by start from the right, a span holds a
    narrower one exactly when some span starting at or after it ends at or before its end."""
    by_start = sorted(set(spans), key=lambda span: (-span[0], span[1]))
    inner, nearest_end = [], None
    for start, end in by_start:          # every span seen so far starts at or after this one
        if nearest_end is None or nearest_end > end:
            inner.append((start, end))
        nearest_end = end if nearest_end is None else min(nearest_end, end)
    inner.reverse()
    count, reach = 0, -1
    for start, end in inner:
        if start >= reach:
            count += 1
        reach = max(reach, end)
    return count


def pragma_covers(line: str, pragma: str = ALLOWLIST_PRAGMA,
                  extra_spans: Iterable[tuple[int, int]] = ()) -> bool:
    """True when the pragma on this line covers what the line holds: at most ONE secret value
    (the detector's, plus extra_spans from a caller's own shapes), and the pragma text itself
    sits outside every value. A line comment cannot say which value it vouches for, so a line
    with two is scanned whole; the export gate binds its pragma by value hash instead.
    A line holding an UNCOVERABLE_KINDS value (a private-key block's first line) is never covered.
    No parser runs: a pragma inside an unrelated string literal counts like a comment."""
    if pragma not in line:
        return False
    uncoverable: list = []
    spans = _secret_value_spans(line, uncoverable) + list(extra_spans)
    if uncoverable or _distinct_values(spans) > 1:
        return False
    at, size = line.find(pragma), len(pragma)
    while at != -1:
        if all(at + size <= start or end <= at for start, end in spans):
            return True
        at = line.find(pragma, at + 1)
    return False


def scan_added_lines(patch: str) -> tuple[list[tuple[str, str, int]], list[str]]:
    """(hits, skipped) over the ADDED lines of a unified diff (`git diff -U0` / `git log -p`).

    hits are (path, kind, line) for each added line whose value is in a secret class, where line
    is its number in the new file (from the hunk header); skipped holds one path per added line
    that WOULD hit but itself carries ALLOWLIST_PRAGMA covering it (pragma_covers: one value per
    pragma line). The pragma counts only on the matched line: a pragma on a neighbouring line
    covers nothing. Lines added to /dev/null (a deletion) are not read. Inside a hunk every `+`
    line is content, even one that starts `++ `.
    Values never leave this function.
    Used by the commit check and the push scan, so both agree on what a secret line is."""
    hits: list[tuple[str, str, int]] = []
    skipped: list[str] = []
    path: Optional[str] = None
    number = new_left = old_left = 0
    # git ends a diff line with \n only; splitlines() would also split one added line at \x0c,
    # \x85 or U+2028, leave the text after it unread and desync the hunk counters.
    for line in patch.split("\n"):
        if new_left > 0 or old_left > 0:
            if line.startswith("+"):
                new_left -= 1
                kind = secret_kind(line[1:]) if path else None
                if kind:
                    (skipped.append(path) if pragma_covers(line[1:]) else hits.append((path, kind, number)))
                number += 1
            elif line.startswith("-"):
                old_left -= 1
            elif line.startswith(" "):
                new_left, old_left, number = new_left - 1, old_left - 1, number + 1
            continue
        if line.startswith("+++ "):
            target = line[4:].rstrip("\r")
            path = None if target == "/dev/null" else _diff_path(target)
        elif line.startswith("@@"):
            hunk = _HUNK.match(line)
            if hunk:
                old_left = int(hunk.group(2) or 1)
                number, new_left = int(hunk.group(3)), int(hunk.group(4) or 1)
        elif path and line.startswith("+"):                    # a hunk without a header: still read
            kind = secret_kind(line[1:])
            if kind:
                (skipped.append(path) if pragma_covers(line[1:]) else hits.append((path, kind, 0)))
    return hits, skipped


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def _diff_path(target: str) -> str:
    """The new-file path of a `+++ ` header as a person reads it: git's C-quoting of a spaced or
    non-ASCII name undone, the trailing tab and the b/ prefix dropped. A character that cannot
    be printed stays escaped, so a file name never writes control text into a refusal."""
    if target.startswith('"'):
        raw, i = bytearray(), 1
        while i < len(target) and target[i] != '"':
            if target[i] == "\\" and i + 1 < len(target):
                octal = re.match(r"[0-7]{3}", target[i + 1:i + 4])
                if octal:
                    raw.append(int(octal.group(), 8) & 0xFF)
                    i += 4
                    continue
                raw.append(_C_ESCAPES.get(target[i + 1], ord("?")))
                i += 2
                continue
            raw += target[i].encode("utf-8", "replace")
            i += 1
        name = raw.decode("utf-8", "replace")
    else:
        name = target.split("\t", 1)[0]
    if name.startswith("b/"):
        name = name[2:]
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in name)


def pragma_notice(skipped: Iterable[str]) -> Optional[str]:
    """The line every allowed commit or push prints when the pragma skipped something (the
    escape hatch is never silent): the count and the paths, never a value."""
    skipped = list(skipped)
    if not skipped:
        return None
    paths = ", ".join(sorted(set(skipped)))
    return f"{len(skipped)} added line(s) skipped by `{ALLOWLIST_PRAGMA}`: {paths}"


def secret_kind(text: str) -> Optional[str]:
    """The first SECRET_ONLY_KINDS class found in text, or None: credential_reason without the
    note-title check, for text that is not a note (tool inputs leaving the machine)."""
    for pattern in SCRUB_PATTERNS:
        if pattern.kind not in SECRET_ONLY_KINDS:
            continue
        for match in pattern.regex.finditer(text):
            if _hit(pattern, match):
                return pattern.kind
    return None


def _denylist_regex(denylist: Iterable[str], whole_words: bool = False) -> Optional[re.Pattern]:
    """One case-insensitive alternation; whole_words bounds it by non-alphanumerics."""
    literals = sorted({literal for literal in denylist if literal}, key=len, reverse=True)
    if not literals:
        return None
    body = "|".join(map(re.escape, literals))
    # Boundaries are NON-ALPHANUMERICS, so `_` and `-` separate words: a name inside a
    # snake_case or kebab-case stem is caught, while letters inside a longer word are not.
    return re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])" if whole_words else body, re.I)


def scrub_text(text: str, denylist: Iterable[str] = (), words: Iterable[str] = (),
               kinds: Optional[Iterable[str]] = None) -> str:
    """Replace secret VALUES with [REDACTED:<kind>], keeping the surrounding words.

    Only patterns with secret=True apply: emails, IPs and ids are allowed off-machine
    (D14); credentials are not. Denylist literals (secret values and de-anonymisation
    keys kept outside git) are replaced with [REDACTED:denylist], case-insensitively;
    `words` entries only where they stand as whole words. `kinds`, when given, limits the
    classes to those names (e.g. SECRET_ONLY_KINDS). Title-only classes never apply to text.
    """
    kinds = set(kinds) if kinds is not None else None
    for regex in (_denylist_regex(denylist), _denylist_regex(words, whole_words=True)):
        if regex:
            text = regex.sub("[REDACTED:denylist]", text)
    for pattern in SCRUB_PATTERNS:
        if not pattern.secret or pattern.title or (kinds is not None and pattern.kind not in kinds):
            continue

        def _replace(match: re.Match, pattern: ScrubPattern = pattern) -> str:
            if not _hit(pattern, match):
                return match.group(0)
            start, end, _value = _matched_value(pattern, match)
            offset = match.start()
            whole = match.group(0)
            return whole[: start - offset] + f"[REDACTED:{pattern.kind}]" + whole[end - offset:]

        text = pattern.regex.sub(_replace, text)
    return text


def scrub_value(value, denylist: Iterable[str] = (), words: Iterable[str] = ()):
    """scrub_text applied to every string inside a JSON-shaped value (keys included)."""
    denylist, words = tuple(denylist), tuple(words)
    if isinstance(value, str):
        return scrub_text(value, denylist, words)
    if isinstance(value, dict):
        return {scrub_text(str(key), denylist, words): scrub_value(item, denylist, words)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_value(item, denylist, words) for item in value]
    return value


@dataclass(frozen=True)
class Finding:
    """One scan hit. It never carries the matched text."""
    line: int
    kind: str


def load_denylist(path: Optional[os.PathLike | str] = None, strict: bool = False) -> list[str]:
    """Literal strings kept outside git, one per line; '#' starts a comment line.

    A missing file yields an empty list (the regex classes still run). strict: a file that
    exists but cannot be read raises instead (an egress path then sends nothing).
    """
    file_path = Path(path or DEFAULT_DENYLIST).expanduser()
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        if strict:
            raise
        return []
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def scan_text(text: str, denylist: Iterable[str] = (), regex: bool = True,
              words: Iterable[str] = (), kinds: Optional[Iterable[str]] = None) -> list[Finding]:
    """Find every scan=True pattern hit and every denylist literal, by line number.

    regex=False runs the denylist only (the fork-origin scan: names must never ship,
    while the imported PACT fixtures hold placeholder secrets by design). `words` hit only
    as whole words, case-insensitively (kind "denylist-word"). `kinds`, when given, limits
    the regex classes to those names (e.g. SECRET_ONLY_KINDS).
    """
    kinds = set(kinds) if kinds is not None else None
    findings: set[Finding] = set()
    line_starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]

    def line_of(offset: int) -> int:
        low, high = 0, len(line_starts) - 1
        while low < high:
            mid = (low + high + 1) // 2
            if line_starts[mid] <= offset:
                low = mid
            else:
                high = mid - 1
        return low + 1

    for pattern in SCRUB_PATTERNS if regex else ():
        if not pattern.scan or pattern.title or (kinds is not None and pattern.kind not in kinds):
            continue
        for match in pattern.regex.finditer(text):
            if not _hit(pattern, match):
                continue
            findings.add(Finding(line_of(_matched_value(pattern, match)[0]), pattern.kind))
    for kind, regex_ in (("denylist", _denylist_regex(denylist)),
                         ("denylist-word", _denylist_regex(words, whole_words=True))):
        for match in regex_.finditer(text) if regex_ else ():
            findings.add(Finding(line_of(match.start()), kind))
    return sorted(findings, key=lambda finding: (finding.line, finding.kind))


