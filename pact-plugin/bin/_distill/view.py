"""The raw distillable view of a Claude Code session transcript (KB full). Fails closed.

Location: pact-plugin/bin/_distill/view.py. discover.py calls lead_transcripts() to find
the lead transcripts (paths and sizes only); segment.py calls read_view() and turns the view into
sources.Segment objects for the existing redact -> chunk -> estimate path. This is the only
distill module that imports hooks/shared/transcripts.py, and it uses that module's locators and
accessors as they are (tests/contract/test_transcript_boundary.py pins which).

R0, the view rules (R0_VERSION; a change re-keys every transcript chunk through its refs). An
ALLOWLIST: what is not named here never enters the view.
  IN   a user entry written by the human: `origin` absent or of kind "human"; not isMeta,
       isCompactSummary, isVisibleInTranscriptOnly or isSidechain; no toolUseResult or
       sourceToolUseID; content a string, or a list of plain text blocks only; and no DELIVERY
       element (DELIVERY_TAGS: teammate messages, task notifications, scheduled tasks,
       slash-command and local-command echoes, bash-mode echoes): such an entry is the harness
       delivering something, and the text around the element is harness boilerplate (measured:
       1,796 such user entries in a 40-session census). Hook-appended elements
       (APPENDED_TAGS: system reminders, prompt-submit hook output) are cut out, an unclosed one
       to the end of the text, and so are `Caveat:` and `[Request interrupted` lines.
  IN   the text blocks (prose) of an assistant entry, deduplicated per message id; not isSidechain,
       isApiErrorMessage or the harness's synthetic model.
  OUT  everything else, counted by rule and never reported: thinking, tool calls and their
       results, images and documents, attachments (queued prompts included), system entries, and
       the bookkeeping types in KNOWN_OUTSIDE_TYPES. A user entry with any non-text block is out
       whole, so a tool result cannot ride along beside a text block.
  UNKNOWN an entry type or block type R0 does not name: counted per type and reported, never in.
Fail-closed reading: a line over LINE_MAX_BYTES, invalid UTF-8, a JSON error (NaN and Infinity
are refused, an over-long integer or a deeply nested array is an error) or a non-object is skipped
and counted; a text over TEXT_MAX_BYTES (a paste, likely a log) or holding a lone surrogate is
dropped and counted; a text block carrying a tool result's keys is a disguised tool result.

PRIVACY: a View holds transcript text; only its counters may be printed.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import _repact_common  # noqa: F401  # side effect: puts hooks/ on sys.path for shared.*
from shared import transcripts as T

R0_VERSION = "r0-1"
LINE_MAX_BYTES = 1 << 20              # one JSONL line; prose lines are far below this
TEXT_MAX_BYTES = 32 * 1024            # one prompt or prose block: longer is a paste
TRANSCRIPT_MAX_BYTES = 512 << 20      # a file past this is skipped, not read
DISTILLER_SLUG = "repact-distill-"    # backend.py's temp cwd: a distiller session, never a source

# Entry types the census of real transcripts found that R0 leaves out on purpose.
KNOWN_OUTSIDE_TYPES = frozenset({
    "attachment", "system", "summary", "queue-operation", "last-prompt", "file-history-snapshot",
    "file-history-delta", "mode", "permission-mode", "agent-setting", "custom-title",
    "agent-name", "pr-link", "api-request", "api-request-blob", "api-request-shape", "cost-state",
    "atis-latch", "frame-link"})
USER_OUTSIDE_BLOCKS = frozenset({"tool_result", "image", "document"})
ASSISTANT_OUTSIDE_BLOCKS = frozenset({
    "thinking", "redacted_thinking", "tool_use", "server_tool_use", "web_search_tool_result",
    "web_fetch_tool_result", "code_execution_tool_result", "mcp_tool_use", "mcp_tool_result",
    "container_upload", "tool_result", "image", "document"})
USER_FLAGS_OUT = ("isMeta", "isCompactSummary", "isVisibleInTranscriptOnly", "isSidechain")
USER_TOOL_KEYS = ("toolUseResult", "sourceToolUseID")
TEXT_BLOCK_KEYS = frozenset({"type", "text", "cache_control", "citations"})
TOOL_RESULT_KEYS = frozenset({"tool_use_id", "content", "is_error"})
SYNTHETIC_MODEL = "<synthetic>"
DELIVERY_TAGS = ("teammate-message", "cross-session-message", "task-notification", "scheduled-task",
                 "command-name", "command-message", "command-args", "command-contents",
                 "local-command-stdout", "local-command-stderr", "local-command-caveat", "bash-input",
                 "bash-stdout", "bash-stderr")
APPENDED_TAGS = ("system-reminder", "user-prompt-submit-hook")
_DELIVERY_RE = re.compile(r"<\s*(?:%s)\b" % "|".join(DELIVERY_TAGS), re.I)
_HARNESS_RE = re.compile(r"<\s*(%s)\b[^>]*>.*?(?:<\s*/\s*\1\s*>|\Z)" % "|".join(APPENDED_TAGS),
                         re.I | re.S)
_HARNESS_LINE_RE = re.compile(r"^[ \t]*(?:Caveat:|\[Request interrupted).*$\n?", re.M)
_TYPE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

# Reported counters (reason -> count); segment.py prefixes them with the source kind.
MALFORMED_LINE = "malformed line"
LINE_OVER_CAP = "line over the size cap"
UNREADABLE = "unreadable file"
TEXT_OVER_CAP = "text over the size cap"
BAD_UNICODE = "text is not valid unicode"
MALFORMED_BLOCK = "malformed content block"
DISGUISED = "tool result disguised as text"
UNKNOWN_TYPE = "unknown entry type {}"
UNKNOWN_BLOCK = "unknown block type {}"


@dataclass(frozen=True)
class Turn:
    """One piece of the view: who wrote it, its text, and its ISO time (or None)."""
    role: str              # "user" | "assistant"
    text: str
    ts: Optional[str]


@dataclass
class View:
    turns: list = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)      # reported: reason -> count
    excluded: Counter = field(default_factory=Counter)    # R0 out-rules: rule -> count, not reported


def type_label(value) -> str:
    """A type name safe to print: identifier-shaped, else "other" (it comes from the file)."""
    return value if isinstance(value, str) and _TYPE_NAME_RE.match(value) else "other"


def lead_transcripts(projects_root: Path) -> Iterator[tuple]:
    """(lead path, teammate and subagent transcripts beside it) for each lead transcript under
    <config>/projects/<slug>/, the layout shared/transcripts.py documents. Paths only."""
    if not projects_root.is_dir():
        return
    for folder in sorted(p for p in projects_root.iterdir() if p.is_dir()):
        for lead in sorted(folder.glob("*.jsonl")):
            yield lead, len(T.session_files(lead)) - 1


def read_view(path: Path) -> View:
    """The R0 view of one lead transcript."""
    view = View()
    seen: set = set()
    for entry in _entries(path, view):
        kind = entry.get("type")
        if kind == "user":
            text = _user_text(entry, view)
            role = "user"
        elif kind == "assistant":
            text = _assistant_text(entry, view, seen)
            role = "assistant"
        elif isinstance(kind, str) and kind in KNOWN_OUTSIDE_TYPES:
            view.excluded[f"entry {kind}"] += 1
            continue
        else:
            view.counts[UNKNOWN_TYPE.format(type_label(kind))] += 1
            continue
        if text:
            view.turns.append(Turn(role, text, _iso(entry.get("timestamp"))))
    return view


def _reject_constant(name: str):
    raise ValueError(f"non-finite number {name}")


def _entries(path: Path, view: View) -> Iterator[dict]:
    """Each JSON object line, read with a byte cap per line; every bad line is counted."""
    try:
        handle = open(path, "rb")
    except OSError:
        view.counts[UNREADABLE] += 1
        return
    with handle:
        while True:
            line = handle.readline(LINE_MAX_BYTES + 1)
            if not line:
                return
            if len(line) > LINE_MAX_BYTES and not line.endswith(b"\n"):
                view.counts[LINE_OVER_CAP] += 1
                while line and not line.endswith(b"\n"):      # drain the rest of the line
                    line = handle.readline(LINE_MAX_BYTES + 1)
                continue
            if not line.strip():
                continue
            try:
                entry = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
            except (ValueError, RecursionError):   # UnicodeDecodeError is a ValueError
                view.counts[MALFORMED_LINE] += 1
                continue
            if isinstance(entry, dict):
                yield entry
            else:
                view.counts[MALFORMED_LINE] += 1


def _iso(value) -> Optional[str]:
    ts = T.parse_ts(value) if isinstance(value, str) else None
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ") if ts is not None else None


def _user_text(entry: dict, view: View) -> Optional[str]:
    """The human's words in a user entry, or None (the reason counted)."""
    origin = entry.get("origin")
    if origin is not None and not (isinstance(origin, dict) and origin.get("kind") == "human"):
        view.excluded["user not from the human"] += 1
        return None
    flag = next((key for key in USER_FLAGS_OUT if entry.get(key)), None) \
        or next((key for key in USER_TOOL_KEYS if key in entry), None)   # present at all: a tool's
    if flag:
        view.excluded[f"user {flag}"] += 1
        return None
    msg = T.message(entry)
    if msg.get("role") != "user":
        view.counts[MALFORMED_BLOCK] += 1
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return _clean(content, view, strip_harness=True)
    if not isinstance(content, list):
        view.counts[MALFORMED_BLOCK] += 1
        return None
    texts = []
    for block in content:
        text, reason = _text_block(block, USER_OUTSIDE_BLOCKS)
        if reason is not None:            # one bad or non-text block puts the whole entry out
            _count(view, reason)
            return None
        texts.append(text)
    return _clean("\n".join(texts), view, strip_harness=True)


def _assistant_text(entry: dict, view: View, seen: set) -> Optional[str]:
    """The prose blocks of an assistant entry, or None."""
    msg = T.message(entry)
    if entry.get("isSidechain") or entry.get("isApiErrorMessage") or msg.get("model") == SYNTHETIC_MODEL:
        view.excluded["assistant not model prose"] += 1
        return None
    content = msg.get("content")
    if msg.get("role") != "assistant" or not isinstance(content, list):
        view.counts[MALFORMED_BLOCK] += 1
        return None
    texts = []
    for block in content:
        text, reason = _text_block(block, ASSISTANT_OUTSIDE_BLOCKS)
        if reason is not None:            # only this block is out: prose beside it stays
            _count(view, reason)
            continue
        key = (msg.get("id"), text)
        if key in seen:
            continue
        seen.add(key)
        cleaned = _clean(text, view, strip_harness=False)
        if cleaned:
            texts.append(cleaned)
    return "\n".join(texts) or None


def _text_block(block, outside: frozenset) -> tuple:
    """(text, None) for a plain text block, else (None, reason). Reasons that start with
    "rule " are R0 exclusions; the others are reported."""
    if not isinstance(block, dict):
        return None, MALFORMED_BLOCK
    kind = block.get("type")
    if kind != "text":
        if isinstance(kind, str) and kind in outside:
            return None, f"rule block {kind}"
        return None, UNKNOWN_BLOCK.format(type_label(kind))
    if TOOL_RESULT_KEYS & set(block):
        return None, DISGUISED
    if not set(block) <= TEXT_BLOCK_KEYS or not isinstance(block.get("text"), str):
        return None, MALFORMED_BLOCK
    return block["text"], None


def _count(view: View, reason: str) -> None:
    if reason.startswith("rule "):
        view.excluded[reason[5:]] += 1
    else:
        view.counts[reason] += 1


def _clean(text: str, view: View, strip_harness: bool) -> Optional[str]:
    """The text without harness elements, or None when nothing is left or it may not enter."""
    if strip_harness:
        if _DELIVERY_RE.search(text):
            view.excluded["user harness delivery"] += 1
            return None
        text = _HARNESS_LINE_RE.sub("", _HARNESS_RE.sub("", text))
    text = text.strip()
    if not text:
        return None
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:            # a lone surrogate from a \\udcxx escape
        view.counts[BAD_UNICODE] += 1
        return None
    if size > TEXT_MAX_BYTES:
        view.counts[TEXT_OVER_CAP] += 1
        return None
    return text
