"""The core purity scan (D8-G; the design notes section 5).

Location: pact-plugin/tests/contract/_purity.py. Used by test_core_purity.py, and run as
a script to maintain the baseline (from repacted/pact-plugin):
    python3 tests/contract/_purity.py --report                   counts per class, current vs baseline
    python3 tests/contract/_purity.py --check                    exit 1 on any NEW or STALE entry (CI)
    python3 tests/contract/_purity.py --shrink                   drop entries no longer hit (tighten)
    python3 tests/contract/_purity.py --accept-new "<reason>"    take today's hits, recording the delta

SCOPE: every file tracked under the CORE ROOT (the directory holding pact-plugin/, which becomes the
root of the shareable core repo), from `git ls-files`, or a tree walk where there is no git (the
installed cache copy, design gate G3). Binary files and the baseline itself are not scanned.

CLASSES: home-path, private-ip, cloud-id, user-id, email (outside placeholder domains), personal
(the scrub denylist and its word list, plus profile.json identity and internal domains), secret/<kind>
(every D25 class in SECRET_ONLY_KINDS, read at run time so new detector shapes join with no change
here), dangling-ref (a `repacted:<x>` naming no core skill, command or agent; the region marker
words `repacted:begin` and `repacted:end` excepted) and overlay-literal.

THE BASELINE holds no values: {path, line, class, id, scope}. The id is a hash of the class and the
hit's LINE with the matched span masked, never of the value (a hash of a name or an address can be
reversed by guessing). It survives the line moving and changes when that line is edited. The gate
compares the multiset of (path, class, id): more hits than the baseline records is a NEW hit; fewer
is a STALE entry, which also fails until --shrink tightens the baseline in the same commit.

MODES: the personal class needs the denylist (REPACT_PURITY_DENYLIST_DIR, else the scrub-check
default paths). Without it the gate FAILS CLOSED, unless REPACT_PURITY_NO_DENYLIST=1 states a public
run (CI), which skips the personal class and checks everything else.

EXPORT MODE (export_scan; the public-release gate, G3): full mode only, with a denylist and a profile
identity name, else FailClosed. It walks a staged directory (untracked files and this baseline
included), scans every file and directory PATH, refuses unreadable, binary, oversize and special
files and symlinks unless allowlisted, and adds personal-class views to scan_file: folded (NFKD
without combining marks, casefold, invisible characters stripped, Cyrillic and Greek look-alikes
mapped), joined (whitespace and markup removed, for entries of JOIN_MIN+ characters) and decoded
(URL, HTML entity, \\u and \\x escapes, base64 runs). Every hit maps back to its original line;
line 0 is the path itself. Personal and export classes are zero-tolerance (export_compare).

EXPORT NAMES (export mode only): employer, client and project names live in their own lists,
export-denylist.txt (substring) and export-denylist-words.txt (whole word), in
~/.config/repacted, or $REPACT_EXPORT_DENYLIST / $REPACT_EXPORT_DENYLIST_WORDS, or the
REPACT_PURITY_DENYLIST_DIR folder. Only export_context_from_env loads them, so the private scrub
and every non-export run never see them. Their hits are class personal, run through every view,
and the D28 author allowance never exempts them. Missing or empty fails the export closed.
A release run refuses every list variable (LIST_OVERRIDE_ENV) and records each list's entry
count and sha256 in its private report (list_receipt).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import dataclasses
import datetime
import functools
import hashlib
import html
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, NamedTuple, Optional, Sequence

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = PLUGIN_ROOT.parent
BASELINE = Path(__file__).resolve().parent / "purity_baseline.json"
DENYLIST_NAMES = ("scrub-denylist.txt", "scrub-denylist-words.txt")
ENV_DIR, ENV_PUBLIC = "REPACT_PURITY_DENYLIST_DIR", "REPACT_PURITY_NO_DENYLIST"
EXPORT_LIST_NAMES = ("export-denylist.txt", "export-denylist-words.txt")
ENV_EXPORT_LISTS = ("REPACT_EXPORT_DENYLIST", "REPACT_EXPORT_DENYLIST_WORDS")
EXPORT_LIST_HOME = Path("~/.config/repacted")
PERSONAL_CLASSES = frozenset({"personal"})
OVERLAY_NAME = "repacted" + "-personal"      # built in two pieces so this file is not its own hit


def _load_common():
    """bin/_repact_common.py, by path (tests never edit sys.path for it)."""
    name = "_repact_common_for_purity"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN_ROOT / "bin" / "_repact_common.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


common = _load_common()

# ---------------------------------------------------------------------------------------------
# classes
# ---------------------------------------------------------------------------------------------

PLACEHOLDER_USERS = frozenset({"you", "me", "user", "username", "name", "example", "yourname",
                               "your-name", "runner", "shared", "someone", "alice", "bob"})
PLACEHOLDER_EMAIL = ("test.com", "company.com")
# `<!-- repacted:begin <region> -->` / `repacted:end` delimit generated regions (bin/vault-note,
# bin/_tasks/atlas.py): marker words, not references. Exact tokens only; `beginx`, `Begin` and
# `begin-foo` are still refs.
REGION_MARKERS = frozenset({"begin", "end"})
PLACEHOLDER_CLOUD = re.compile(r"example|^my-|^your-|project-id|^xxx|^proj$|^projectid")


def _email_ok(address: str) -> bool:
    """A reportable address: not a reserved or placeholder domain. The owner is NOT exempt."""
    domain = address.lower().rsplit("@", 1)[-1]
    reserved = any(domain == name or domain.endswith("." + name) for name in common.PLACEHOLDER_EMAIL_DOMAINS) \
        or domain.endswith(common.PLACEHOLDER_EMAIL_TLDS) or domain.startswith("example.") \
        or domain in PLACEHOLDER_EMAIL
    return not reserved


@dataclass(frozen=True)
class Detector:
    cls: str
    regex: re.Pattern
    accept: Optional[Callable[[str], bool]] = None


def _group(match: re.Match) -> tuple:
    if "v" in match.re.groupindex and match.group("v") is not None:
        return match.start("v"), match.end("v"), match.group("v")
    return match.start(), match.end(), match.group(0)


def _resolves(name: str, root: Path) -> bool:
    plugin = root / "pact-plugin"
    return ((plugin / "skills" / name / "SKILL.md").is_file() or (plugin / "commands" / f"{name}.md").is_file()
            or (plugin / "agents" / f"{name}.md").is_file())


def _structural(root: Path) -> List[Detector]:
    private_ip = next(p for p in common.SCRUB_PATTERNS if p.kind == "private-ip")
    email = next(p for p in common.SCRUB_PATTERNS if p.kind == "email")
    not_placeholder_user = lambda value: value.lower() not in PLACEHOLDER_USERS  # noqa: E731
    return [
        Detector("home-path", re.compile(r"(?<![\w.~])/Users/(?P<v>[A-Za-z0-9][A-Za-z0-9._-]*)"),
                 not_placeholder_user),
        Detector("home-path", re.compile(r"(?<![\w.])/home/(?P<v>[A-Za-z0-9][A-Za-z0-9._-]*)"),
                 not_placeholder_user),
        Detector("home-path", re.compile(re.escape("~/" + "Documents/"))),
        Detector("private-ip", private_ip.regex, private_ip.accept),
        Detector("cloud-id", re.compile(r"--project[= ](?P<v>[a-z][a-z0-9-]{4,28}[a-z0-9])\b"),
                 lambda value: not PLACEHOLDER_CLOUD.search(value)),
        Detector("cloud-id", re.compile(r"(?<![\w.-])projects/(?P<v>[a-z][a-z0-9-]{4,28}[a-z0-9])\b"),
                 lambda value: not PLACEHOLDER_CLOUD.search(value)),
        Detector("cloud-id", re.compile(r"\b[a-z][a-z0-9-]{4,28}@(?P<v>[a-z][a-z0-9-]{4,28}[a-z0-9])"
                                        r"\.iam\.gserviceaccount\.com"),
                 lambda value: not PLACEHOLDER_CLOUD.search(value)),
        Detector("cloud-id", re.compile(r"\barn:aws:[a-z0-9-]+:[a-z0-9-]*:(?P<v>\d{12}):")),
        Detector("user-id", re.compile(r"\b[UW]0[A-Z0-9]{7,9}\b")),
        Detector("email", email.regex, _email_ok),
        Detector("dangling-ref", re.compile(r"repacted:(?P<v>[A-Za-z0-9_][A-Za-z0-9_-]*)(?![<{A-Za-z0-9_-])"),
                 lambda value: not value.endswith("-") and value not in REGION_MARKERS
                 and not _resolves(value, root)),
        Detector("overlay-literal", re.compile(re.escape(OVERLAY_NAME))),
    ]


def known_classes() -> frozenset:
    """Every class name the scan can emit (the baseline's class vocabulary)."""
    return frozenset({d.cls for d in _structural(CORE_ROOT)} | {d.cls for d in _secrets()} | PERSONAL_CLASSES)


def _secrets() -> List[Detector]:
    """Every D25 credential class, read from the detector at run time (#205's shapes join)."""
    return [Detector(f"secret/{p.kind}", p.regex, p.accept) for p in common.SCRUB_PATTERNS
            if p.kind in common.SECRET_ONLY_KINDS and p.scan and not p.title]


# ---------------------------------------------------------------------------------------------
# context and modes
# ---------------------------------------------------------------------------------------------

class FailClosed(Exception):
    """The personal class cannot run and no public mode was stated."""


@dataclass
class Context:
    mode: str                                   # "full" (with the denylist) or "public"
    denylist: List[str] = field(default_factory=list)
    words: List[str] = field(default_factory=list)
    profile: dict = field(default_factory=dict)
    root: Path = CORE_ROOT
    sources: tuple = ()                         # (denylist path, words path) when loaded from files
    export_names: List[str] = field(default_factory=list)   # export mode only: never the scrub's
    export_words: List[str] = field(default_factory=list)
    export_sources: tuple = ()

    def __repr__(self) -> str:
        """Counts only: pytest prints a failing test's fixtures, and these lists are private words."""
        return (f"Context(mode={self.mode!r}, denylist=<{len(self.denylist)} hidden>, "
                f"words=<{len(self.words)} hidden>, export_names=<{len(self.export_names)} hidden>, "
                f"export_words=<{len(self.export_words)} hidden>, "
                f"profile=<{len(self.profile)} keys hidden>, root={self.root!r})")


def _denylist_files(env) -> tuple:
    folder = env.get(ENV_DIR)
    if folder:
        return tuple(Path(folder) / name for name in DENYLIST_NAMES)
    return common.denylist_paths()


def _export_list_files(env) -> tuple:
    """(names path, words path) of the export-only lists: the purity folder when set, else each
    file's own variable, else ~/.config/repacted."""
    folder = env.get(ENV_DIR)
    if folder:
        return tuple(Path(folder) / name for name in EXPORT_LIST_NAMES)
    return tuple(Path(env.get(var) or EXPORT_LIST_HOME / name).expanduser()
                 for var, name in zip(ENV_EXPORT_LISTS, EXPORT_LIST_NAMES))


def _load_export_list(path: Path) -> List[str]:
    """One entry per line; '#' comments and blank lines skipped; a BOM and CRLF endings dropped."""
    text = path.read_text(encoding="utf-8-sig")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


# A release run reads the name lists only from their homes (~/.config/repacted): each of these
# points an export at other lists (the tests' fixtures do), so a release refuses them all.
LIST_OVERRIDE_ENV = (ENV_DIR, *ENV_EXPORT_LISTS, common.ENV_DENYLIST, common.ENV_DENYLIST_WORDS)


def release_list_overrides(env=None) -> List[str]:
    """The LIST_OVERRIDE_ENV variables set to a non-empty value."""
    env = os.environ if env is None else env
    return [var for var in LIST_OVERRIDE_ENV if env.get(var)]


def list_receipt(ctx: Context) -> List[str]:
    """One line per name list: its entry count and the sha256 of its file, never an entry. The
    private release report records them, so a release on a swapped or shortened list shows."""
    counts = (len(ctx.denylist), len(ctx.words), len(ctx.export_names), len(ctx.export_words))
    paths = tuple(ctx.sources or ("", "")) + tuple(ctx.export_sources or ("", ""))
    lines = []
    for label, count, path in zip(("denylist", "words", "export names", "export words"),
                                  counts, paths):
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else "-"
        lines.append(f"{label}: {count} entries, sha256 {digest}, {path or '<not from files>'}")
    return lines


def denylist_available(env=None) -> bool:
    env = os.environ if env is None else env
    return bool(common.load_denylist(_denylist_files(env)[0]))


def context_from_env(env=None) -> Context:
    env = os.environ if env is None else env
    listed, words = _denylist_files(env)
    denylist = common.load_denylist(listed)
    if denylist:
        return Context("full", denylist, common.load_denylist(words), common.profile())
    if env.get(ENV_PUBLIC) == "1":
        return Context("public")
    raise FailClosed(f"no scrub denylist at {listed}: the personal class cannot run. The merge gate "
                     f"passes it through {ENV_DIR}; a public run states {ENV_PUBLIC}=1.")


def evaluated(cls: str, ctx: Context) -> bool:
    return ctx.mode == "full" or cls not in PERSONAL_CLASSES


# D28: the user chose their own name as the core's author, so profile identity.name may appear in
# exactly these fields (plugin author, marketplace owner and plugin-entry author, NOTICE modifier) (relative to the core root) and nowhere else. Each regex's group "v" is the
# field's value; a personal hit is allowed only inside a value that IS the identity name.
AUTHOR_FIELDS = {
    "pact-plugin/.claude-plugin/plugin.json": (
        re.compile(r'"author"\s*:\s*\{[^{}]*?"name"\s*:\s*"(?P<v>[^"]*)"'),
        re.compile(r'"author"\s*:\s*"(?P<v>[^"]*)"')),
    ".claude-plugin/marketplace.json": (                 # the owner, and each plugin entry's author
        re.compile(r'"owner"\s*:\s*\{[^{}]*?"name"\s*:\s*"(?P<v>[^"]*)"'),
        re.compile(r'"author"\s*:\s*\{[^{}]*?"name"\s*:\s*"(?P<v>[^"]*)"')),
    "pact-plugin/NOTICE.md": (re.compile(r"(?m)^- Modified by:?[ \t]+(?P<v>[^\n]*?)[ \t]*$"),),
}


def _author_spans(rel: str, text: str, ctx: Context) -> List[tuple]:
    """(start, end) of each author field in `rel` whose value is exactly the profile identity
    name. None without an identity name: the allowance never applies to a denylist-only name."""
    identity = ctx.profile.get("identity") if isinstance(ctx.profile, dict) else None
    name = identity.get("name") if isinstance(identity, dict) else None
    if not isinstance(name, str) or not name.strip():
        return []
    return [(match.start("v"), match.end("v")) for regex in AUTHOR_FIELDS.get(rel, ())
            for match in regex.finditer(text) if match.group("v").strip().lower() == name.strip().lower()]


def _personal_literals(ctx: Context) -> List[str]:
    profile = ctx.profile if isinstance(ctx.profile, dict) else {}
    identity = profile.get("identity") if isinstance(profile.get("identity"), dict) else {}
    hosts = profile.get("hosts") if isinstance(profile.get("hosts"), dict) else {}
    extra = [value for value in identity.values() if isinstance(value, str) and len(value) >= 3]
    extra += [value for value in hosts.get("internal_domains") or [] if isinstance(value, str) and value]
    return [*ctx.denylist, *extra]


# ---------------------------------------------------------------------------------------------
# scanning
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    cls: str
    id: str
    value: str = field(repr=False)              # in memory only: never written, never in a repr


def _scope(rel: str) -> str:
    parts = rel.split("/")
    return "test" if "tests" in parts or parts[-1].startswith(("test_", "conftest")) else "core"


def _identity(cls: str, text: str, start: int, end: int) -> tuple:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    masked = text[line_start:start] + "\x00" + text[end:line_end]
    digest = hashlib.sha256(f"{cls}\x00{masked}".encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return text.count("\n", 0, start) + 1, digest


def _literal_spans(text: str, literals: Iterable[str], whole_words: bool):
    """Case-insensitive literal matches, found with str.find: a 200-literal regex alternation
    over the tree took 35 s, this takes about one. A word must stand between non-alphanumerics."""
    lower = text.lower()
    if len(lower) != len(text):                  # a case mapping changed the length: use the regex
        regex = common._denylist_regex(literals, whole_words=whole_words)
        yield from ((m.start(), m.end()) for m in (regex.finditer(text) if regex else ()))
        return
    for literal in {item.lower() for item in literals if item}:
        start = lower.find(literal)
        while start >= 0:
            end = start + len(literal)
            if not whole_words or ((start == 0 or not lower[start - 1].isalnum())
                                   and (end == len(lower) or not lower[end].isalnum())):
                yield start, end
            start = lower.find(literal, start + 1)


def scan_file(rel: str, text: str, ctx: Context) -> List[Hit]:
    hits = set()
    for detector in _structural(ctx.root) + _secrets():
        for match in detector.regex.finditer(text):
            start, end, value = _group(match)
            if detector.accept and not detector.accept(value):
                continue
            line, digest = _identity(detector.cls, text, start, end)
            hits.add(Hit(rel, line, detector.cls, digest, value))
    if ctx.mode == "full":
        allowed = _author_spans(rel, text, ctx)
        for literals, whole_words in ((_personal_literals(ctx), False), (ctx.words, True)):
            for start, end in _literal_spans(text, literals, whole_words):
                if any(low <= start and end <= high for low, high in allowed):
                    continue                     # D28: the author's name in an author field
                line, digest = _identity("personal", text, start, end)
                hits.add(Hit(rel, line, "personal", digest, text[start:end]))
        for literals, whole_words in ((ctx.export_names, False), (ctx.export_words, True)):
            for start, end in _literal_spans(text, literals, whole_words):   # no author allowance
                line, digest = _identity("personal", text, start, end)
                hits.add(Hit(rel, line, "personal", digest, text[start:end]))
    return sorted(hits, key=lambda hit: (hit.line, hit.cls, hit.id))


def core_files(root: Path = CORE_ROOT) -> List[str]:
    """Tracked files under the core root (relative, posix), or a walk where there is no git."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        out = None
    if out is not None and out.returncode == 0:
        files = [name for name in out.stdout.decode("utf-8", "replace").split("\0") if name]
    else:
        files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
                 and not {".git", "__pycache__", ".pytest_cache"} & set(p.relative_to(root).parts)]
    skip = BASELINE.relative_to(root).as_posix() if BASELINE.is_relative_to(root) else None
    return sorted(name for name in files if name != skip)


def read_core(rel: str, root: Path = CORE_ROOT) -> Optional[str]:
    """The file's text, or None for a binary, missing or unreadable file."""
    try:
        data = (root / rel).read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def scan_tree(ctx: Context, texts: Optional[dict] = None) -> List[Hit]:
    if texts is None:
        texts = {rel: text for rel in core_files(ctx.root) if (text := read_core(rel, ctx.root)) is not None}
    return [hit for rel in sorted(texts) for hit in scan_file(rel, texts[rel], ctx)]


# ---------------------------------------------------------------------------------------------
# export mode: normalised and decoded views over a staged tree
# ---------------------------------------------------------------------------------------------

INVISIBLE = frozenset("\u00ad\u200b\u200c\u200d\u2060\ufeff")   # soft hyphen, zero-widths, BOM
CONFUSABLES = str.maketrans({                    # lowercase only: views are casefolded first
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c",
    "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ԛ": "q",
    "ԝ": "w", "ӏ": "l", "ү": "y", "α": "a", "β": "b", "ε": "e", "ζ": "z", "ι": "i", "κ": "k",
    "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "ϲ": "c", "ı": "i"})
JOIN_DROP = frozenset("*_`~-#>|")                # with whitespace: markup a wrapped name crosses
JOIN_MIN = 6                                     # shorter entries joined across text flood hits
BASE64_MIN, BASE64_LONG = 12, 64                 # a short run must decode to printable text
MAX_EXPORT_BYTES = 8 * 1024 * 1024
EXPORT_CLASSES = frozenset({"export/binary", "export/oversize", "export/special", "export/symlink",
                            "export/unreadable"})
ZERO_TOLERANCE = PERSONAL_CLASSES | EXPORT_CLASSES
DEFAULT_SYMLINKS = {"pact-plugin/LICENSE": "../LICENSE"}
_URL = re.compile(r"""[^\s"'<>()\[\]{}]*%[0-9A-Fa-f]{2}[^\s"'<>()\[\]{}]*""")
_ENTITY = re.compile(r"""[^\s"'<>]*&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);[^\s"'<>]*""")
_ESCAPE = re.compile(r"""[^\s"'<>]*\\(?:u[0-9A-Fa-f]{4}|x[0-9A-Fa-f]{2})[^\s"'<>]*""")
_ESCAPE_ONE = re.compile(r"\\u([0-9A-Fa-f]{4})|\\x([0-9A-Fa-f]{2})")
_BASE64 = re.compile(r"[A-Za-z0-9+/_-]{8,}={0,2}")


@dataclass(frozen=True)
class View:
    """A derived text and, per character, the original span it came from."""
    text: str
    starts: Sequence[int]
    ends: Sequence[int]


# Default_Ignorable_Code_Point ranges (Unicode DerivedCoreProperties) beyond category Cf: the
# combining grapheme joiner, Hangul fillers, Khmer inherent vowels, Mongolian and standard
# variation selectors, the Hangul half-width filler and the tag/variation-selector supplement.
IGNORABLE_RANGES = ((0x034F, 0x034F), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
                    (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFFA0, 0xFFA0), (0x1BCA0, 0x1BCA3),
                    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))


def _ignorable(char: str) -> bool:
    """Renders as nothing: format characters (Cf: bidi marks, invisible operators, ...) and
    every other default-ignorable code point."""
    if char in INVISIBLE or unicodedata.category(char) == "Cf":
        return True
    point = ord(char)
    return any(low <= point <= high for low, high in IGNORABLE_RANGES)


@functools.lru_cache(maxsize=None)
def _fold_char(char: str) -> str:
    if _ignorable(char):
        return ""
    plain = "".join(c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c))
    return plain.casefold().translate(CONFUSABLES)


def fold(text: str) -> str:
    """The comparison form of a text: what the folded view holds and what entries are folded to."""
    return text.lower() if text.isascii() else "".join(map(_fold_char, text))


def _folded(text: str, base: int = 0, span: Optional[tuple] = None) -> View:
    """`text` folded; each character maps to base + its index, or to the whole `span` when given."""
    if span is None and text.isascii():
        return View(text.lower(), range(base, base + len(text)), range(base + 1, base + len(text) + 1))
    chars, starts, ends = [], [], []
    for index, char in enumerate(text):
        out = _fold_char(char)
        low, high = span or (base + index, base + index + 1)
        chars.append(out)
        starts += [low] * len(out)
        ends += [high] * len(out)
    return View("".join(chars), starts, ends)


def _joined(view: View) -> View:
    keep = [k for k, c in enumerate(view.text) if not (c.isspace() or c in JOIN_DROP)]
    return View("".join(view.text[k] for k in keep), [view.starts[k] for k in keep],
                [view.ends[k] for k in keep])


def _join_key(text: str) -> str:
    return "".join(c for c in fold(text) if not (c.isspace() or c in JOIN_DROP))


def _unescape(token: str) -> str:
    """URL, HTML-entity and \\u / \\x escapes, applied until stable (double encoding)."""
    for _ in range(3):
        decoded = html.unescape(urllib.parse.unquote(token))
        decoded = _ESCAPE_ONE.sub(lambda m: chr(int(m.group(1) or m.group(2), 16)), decoded)
        decoded = decoded.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
        if decoded == token:
            break
        token = decoded
    return token


def _base64_text(run: str) -> Optional[str]:
    body = run.rstrip("=").replace("-", "+").replace("_", "/")
    body = body[:-1] if len(body) % 4 == 1 else body
    try:
        data = base64.b64decode(body + "=" * (-len(body) % 4))
    except (binascii.Error, ValueError):
        return None
    text = data.decode("utf-8", "replace")
    printable = sum(c.isprintable() or c in "\t\r\n" for c in text)
    return text if len(run) >= BASE64_LONG or printable >= 0.9 * max(len(text), 1) else None


def _decoded(text: str) -> View:
    """Every encoded token, decoded and folded, NUL-separated; each char maps to its token."""
    parts, starts, ends = [], [], []

    def add(decoded: str, low: int, high: int) -> None:
        view = _folded(decoded + "\x00", span=(low, high))
        parts.append(view.text)
        starts.extend(view.starts)
        ends.extend(view.ends)

    for regex in (_URL, _ENTITY, _ESCAPE):
        for match in regex.finditer(text):
            if (decoded := _unescape(match.group(0))) != match.group(0):
                add(decoded, match.start(), match.end())
    for match in _BASE64.finditer(text):
        if len(match.group(0)) >= BASE64_MIN and (decoded := _base64_text(match.group(0))):
            add(decoded, match.start(), match.end())
    return View("".join(parts), starts, ends)


def _view_hits(rel: str, text: str, ctx: Context) -> List[Hit]:
    """Personal hits in the folded, joined and decoded views, mapped to the original text. The
    export-only lists run the same views with no author allowance."""
    groups = ((_personal_literals(ctx), ctx.words, _author_spans(rel, text, ctx)),
              (ctx.export_names, ctx.export_words, []))
    views = [(view, _joined(view)) for view in (_folded(text), _decoded(text))]
    hits = set()
    for names, word_list, allowed in groups:
        literals = [fold(item) for item in names if fold(item)]
        words = [fold(item) for item in word_list if fold(item)]
        joined = [key for item in names if len(key := _join_key(item)) >= JOIN_MIN]
        for view, joined_view in views:
            for target, entries, whole_words in ((view, literals, False), (view, words, True),
                                                 (joined_view, joined, False)):
                for start, end in _literal_spans(target.text, entries, whole_words):
                    low, high = target.starts[start], target.ends[end - 1]
                    if any(a <= low and high <= b for a, b in allowed):
                        continue                 # D28: the author's name in an author field
                    line, digest = _identity("personal", text, low, high)
                    hits.add(Hit(rel, line, "personal", digest, text[low:high]))
    return sorted(hits, key=lambda hit: (hit.line, hit.cls, hit.id))


def scan_export_text(rel: str, text: str, ctx: Context) -> List[Hit]:
    """scan_file plus the personal views: the export-mode scan of one text."""
    return sorted(set(scan_file(rel, text, ctx)) | set(_view_hits(rel, text, ctx)),
                  key=lambda hit: (hit.line, hit.cls, hit.id))


def scan_metadata(label: str, text: str, ctx: Context) -> List[Hit]:
    """A commit message, author, tag annotation or release note, reported as `<label>`."""
    require_export(ctx)
    return scan_export_text(f"<{label}>", text, ctx)


def require_export(ctx: Context) -> None:
    """Export runs in full mode with a denylist and an identity name, or not at all."""
    identity = ctx.profile.get("identity") if isinstance(ctx.profile, dict) else None
    name = identity.get("name") if isinstance(identity, dict) else None
    if ctx.mode != "full" or not [item for item in ctx.denylist if item and item.strip()]:
        raise FailClosed(f"export needs full mode with a non-empty denylist (mode {ctx.mode}, "
                         f"{len(ctx.denylist)} entries); {ENV_PUBLIC} does not apply to an export.")
    if not isinstance(name, str) or not name.strip():
        raise FailClosed("export needs profile.json identity.name: without it the identity is unchecked.")
    for label, entries in zip(EXPORT_LIST_NAMES, (ctx.export_names, ctx.export_words)):
        if not [item for item in entries if item and item.strip()]:
            raise FailClosed(f"export needs entries from {label}: without them employer, client and "
                             f"project names are unchecked. Add one name per line to it "
                             f"(see export_context_from_env for where it is read).")


def export_context_from_env(env=None) -> Context:
    """A full context for an export: the public escape removed, BOTH the denylist and the words
    file required, read strictly and non-empty (a missing, unreadable or empty one fails closed,
    so a wrong folder cannot quietly drop the whole-word list), the same for both export-only
    name lists, then require_export."""
    env = dict(os.environ if env is None else env)
    env.pop(ENV_PUBLIC, None)
    listed, words = _denylist_files(env)
    loaded = []
    for path in (listed, words):
        if not path.is_file():
            raise FailClosed(f"export needs {path.name} at {path}: missing (set {ENV_DIR} to the "
                             f"folder holding both {DENYLIST_NAMES[0]} and {DENYLIST_NAMES[1]})")
        try:
            loaded.append(common.load_denylist(path, strict=True))
        except (OSError, UnicodeDecodeError) as error:
            raise FailClosed(f"export cannot read {path}: {type(error).__name__}") from None
        if not [item for item in loaded[-1] if item.strip()]:
            raise FailClosed(f"export needs entries in {path}: the file is empty")
    export_paths, names = _export_list_files(env), []
    for path, var in zip(export_paths, ENV_EXPORT_LISTS):
        way = (f"create it (mode 0600, one employer, client or project name per line, '#' for "
               f"comments), or point {var} or {ENV_DIR} at it")
        if not path.is_file():
            raise FailClosed(f"export needs {path.name} at {path}: missing; {way}")
        try:
            names.append(_load_export_list(path))
        except (OSError, UnicodeDecodeError) as error:
            raise FailClosed(f"export cannot read {path}: {type(error).__name__}; {way}") from None
        if not names[-1]:
            raise FailClosed(f"export needs entries in {path}: the file is empty; {way}")
    ctx = Context("full", loaded[0], loaded[1], common.profile(), sources=(str(listed), str(words)),
                  export_names=names[0], export_words=names[1],
                  export_sources=tuple(str(path) for path in export_paths))
    require_export(ctx)
    return ctx


def describe_inputs(ctx: Context) -> str:
    """Entry counts and source files of the name lists (never their entries), so a run that
    loaded the wrong folder or an empty list shows it."""
    where = ctx.sources or ("<not from files>", "<not from files>")
    export = ctx.export_sources or ("<not from files>", "<not from files>")
    return (f"denylist {len(ctx.denylist)} entries from {where[0]}, "
            f"words {len(ctx.words)} entries from {where[1]}, "
            f"export names {len(ctx.export_names)} entries from {export[0]}, "
            f"export words {len(ctx.export_words)} entries from {export[1]}")


def _finding(rel: str, cls: str) -> Hit:
    return Hit(rel, 0, cls, hashlib.sha256(f"{cls}\x00{rel}".encode("utf-8", "surrogatepass")).hexdigest()[:16], "")


def _path_hits(rel: str, ctx: Context) -> List[Hit]:
    return [dataclasses.replace(hit, line=0) for hit in scan_export_text(rel, rel, ctx)]


def _walk(root: Path, findings: List[Hit]) -> List[tuple]:
    """(rel, entry) for every directory and file under root, symlinks not followed, the top-level
    .git skipped. An unlistable directory is a finding."""
    def failed(err: OSError) -> None:
        name = Path(err.filename or root)
        findings.append(_finding(name.relative_to(root).as_posix() if name.is_relative_to(root)
                                 else str(name), "export/unreadable"))

    entries = []
    for folder, dirs, files in os.walk(root, onerror=failed):
        here = Path(folder)
        if here == root:
            dirs[:] = [d for d in dirs if d != ".git"]
            files = [f for f in files if f != ".git"]
        dirs.sort()
        for name in sorted(dirs) + sorted(files):
            entries.append(((here / name).relative_to(root).as_posix(), here / name))
    return entries


def _read_export(path: Path, rel: str, binaries: dict) -> tuple:
    """(text or None, finding or None). Not UTF-8, a NUL byte, oversize or unreadable is a finding
    unless the file's sha256 is on the binary allowlist (then it is not scanned). A file that
    decodes as UTF-8 without a NUL is text and is scanned even when allowlisted."""
    try:
        if path.stat().st_size > MAX_EXPORT_BYTES:
            return None, _finding(rel, "export/oversize")
        data = path.read_bytes()
    except OSError:
        return None, _finding(rel, "export/unreadable")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None or "\x00" in text:
        allowed = binaries.get(rel) == hashlib.sha256(data).hexdigest()
        return None, None if allowed else _finding(rel, "export/binary")
    return text, None


def export_scan(staged: Path, ctx: Context, binary_allowlist: Optional[dict] = None,
                symlink_allowlist: Optional[dict] = None) -> List[Hit]:
    """Every hit on the staged tree: contents, paths and file findings (G3, G5).

    binary_allowlist: {rel: sha256} of reviewed non-text files (empty by default).
    symlink_allowlist: {rel: link target}; a listed link must still resolve inside the tree.
    Raises FailClosed unless the context is a full one (require_export)."""
    require_export(ctx)
    staged = Path(staged).resolve()
    ctx = dataclasses.replace(ctx, root=staged)
    binaries = binary_allowlist or {}
    links = DEFAULT_SYMLINKS if symlink_allowlist is None else symlink_allowlist
    hits: List[Hit] = []
    for rel, path in _walk(staged, hits):
        hits += _path_hits(rel, ctx)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            inside = (path.parent / target).resolve().is_relative_to(staged)
            hits += [dataclasses.replace(hit, line=0) for hit in scan_export_text(rel, target, ctx)]
            if links.get(rel) != target or not inside:
                hits.append(_finding(rel, "export/symlink"))
        elif stat.S_ISREG(mode):
            text, finding = _read_export(path, rel, binaries)
            hits += [finding] if finding else scan_export_text(rel, text, ctx) if text is not None else []
        elif not stat.S_ISDIR(mode):
            hits.append(_finding(rel, "export/special"))
    return sorted(set(hits), key=lambda hit: (hit.path, hit.line, hit.cls, hit.id))


def export_compare(hits: Iterable[Hit], entries: Iterable[dict], ctx: Context) -> Result:
    """compare() for an export: a personal or export-class entry in the baseline is refused
    (zero tolerance), so every such hit is NEW."""
    entries = list(entries)
    tolerated = [e for e in entries if e.get("class") in ZERO_TOLERANCE]
    if tolerated:
        raise FailClosed(f"{len(tolerated)} baseline entr(ies) tolerate a zero-tolerance class on export")
    return compare(hits, entries, ctx)


# ---------------------------------------------------------------------------------------------
# the baseline and the ratchet
# ---------------------------------------------------------------------------------------------

class Result(NamedTuple):
    new: List[Hit]
    stale: List[dict]


def load_baseline(path: Path = BASELINE) -> List[dict]:
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("entries", []))
    except (OSError, ValueError, AttributeError):
        return []


def _key(item) -> tuple:
    if isinstance(item, Hit):
        return item.path, item.cls, item.id
    return item["path"], item["class"], item["id"]


def _selected(items: Iterable, ctx: Context, paths) -> list:
    out = []
    for item in items:
        path, cls, _ = _key(item)
        if evaluated(cls, ctx) and (paths is None or path in paths):
            out.append(item)
    return out


def compare(hits: Iterable[Hit], entries: Iterable[dict], ctx: Context, paths=None) -> Result:
    """NEW: hits beyond the baseline's count for their (path, class, id). STALE: baseline entries
    beyond today's count. Only classes this mode evaluates, and only `paths` when given."""
    hits, entries = _selected(hits, ctx, paths), _selected(entries, ctx, paths)
    have, allowed = Counter(map(_key, hits)), Counter(map(_key, entries))
    new, stale = [], []
    for key, count in have.items():
        if count > allowed[key]:
            new += [hit for hit in hits if _key(hit) == key][allowed[key]:]
    for key, count in allowed.items():
        if count > have[key]:
            stale += [entry for entry in entries if _key(entry) == key][have[key]:]
    return Result(sorted(new, key=lambda h: (h.path, h.line, h.cls)),
                  sorted(stale, key=lambda e: (e["path"], e["line"], e["class"])))


def _entry(hit: Hit) -> dict:
    return {"path": hit.path, "line": hit.line, "class": hit.cls, "id": hit.id, "scope": _scope(hit.path)}


def shrunk(entries: List[dict], hits: Iterable[Hit], ctx: Context, paths=None) -> List[dict]:
    """The baseline tightened: each evaluated key keeps min(baseline, today) entries, at today's
    lines. Never adds a key; entries this mode cannot evaluate are kept as they are."""
    hits = _selected(hits, ctx, paths)
    allowed = Counter(map(_key, _selected(entries, ctx, paths)))
    kept = [e for e in entries if not (evaluated(e["class"], ctx) and (paths is None or e["path"] in paths))]
    taken: Counter = Counter()
    for hit in hits:
        key = _key(hit)
        if taken[key] < allowed[key]:
            taken[key] += 1
            kept.append(_entry(hit))
    return sorted(kept, key=lambda e: (e["path"], e["line"], e["class"], e["id"]))


def write_baseline(entries: List[dict], refresh: Optional[dict] = None, path: Path = BASELINE) -> None:
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    log = list(previous.get("refreshes", [])) + ([refresh] if refresh else [])
    doc = {"_doc": "D8-G purity ratchet: hits allowed on the core tree today, which may only shrink. "
                   "NO VALUES: path, line, class, id (a hash of the class and the line with the hit "
                   "masked) and scope. Maintain with tests/contract/_purity.py (--shrink, --accept-new).",
           "version": 1, "refreshes": log,
           "entries": sorted(entries, key=lambda e: (e["path"], e["line"], e["class"], e["id"]))}
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _counts(items: Iterable) -> Counter:
    return Counter(_key(item)[1] for item in items)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="_purity.py", description="Maintain the D8-G purity baseline.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--report", action="store_true")
    action.add_argument("--check", action="store_true")
    action.add_argument("--shrink", action="store_true")
    action.add_argument("--accept-new", metavar="REASON")
    args = parser.parse_args(argv)
    try:
        ctx = context_from_env()
    except FailClosed as err:
        print(f"_purity: {err}", file=sys.stderr)
        return 3
    entries, hits = load_baseline(), scan_tree(ctx)
    before, now = _counts(_selected(entries, ctx, None)), _counts(_selected(hits, ctx, None))
    if args.report:
        for cls in sorted(set(before) | set(now)):
            print(f"{cls:32s} baseline {before[cls]:5d}  today {now[cls]:5d}")
        print(f"mode {ctx.mode}; {len(entries)} baseline entries")
        return 0
    if args.check:
        # CI's named purity job: the workflow may carry one pytest invocation only, so this is the
        # gate without pytest. Classes and counts, never a value.
        result = compare(hits, entries, ctx)
        for label, items in (("NEW", result.new), ("STALE", result.stale)):
            for cls, count in sorted(_counts(items).items()):
                print(f"{label} {cls} {count}")
        if result.new or result.stale:
            print(f"purity: {len(result.new)} new, {len(result.stale)} stale (mode {ctx.mode}); "
                  "remove the hit, or --shrink / --accept-new with a reason")
            return 1
        print(f"purity: clean against {len(entries)} baseline entries (mode {ctx.mode})")
        return 0
    if args.shrink:
        tightened = shrunk(entries, hits, ctx)
        write_baseline(tightened)
        print(f"shrink: {len(entries)} -> {len(tightened)} entries (mode {ctx.mode})")
        return 0
    kept = [e for e in entries if not evaluated(e["class"], ctx)]
    taken = kept + [_entry(hit) for hit in hits if evaluated(hit.cls, ctx)]
    delta = {cls: now[cls] - before[cls] for cls in sorted(set(before) | set(now)) if now[cls] != before[cls]}
    write_baseline(taken, {"date": datetime.date.today().isoformat(), "mode": ctx.mode,
                           "reason": args.accept_new, "delta": delta})
    print(f"accept-new: {len(entries)} -> {len(taken)} entries; delta {delta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
