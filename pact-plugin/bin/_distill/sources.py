"""Read lite sources into segments and pack them into chunks keyed for the ledger.

Location: pact-plugin/bin/_distill/sources.py. Used by bin/repact-distill after
discover.discover(); the chunks go to estimate (dry run) or to redaction and the backend (run).

A Segment is one lesson, HANDOFF or summary with a provenance `ref` (a pointer, never a quote) and
an ISO date `ts`. A Chunk is the segments of one source file (or one pact-memory session) packed
up to CHUNK_MAX_TOKENS, keyed as the KB lite contract states:
  chunk_key = sha256(source + ref range + view_sha256 + extractor_version)
so a re-read of unchanged text gives the same key and the ledger can skip it.

Feedback loop (U5.4): a note that quotes recalled KB text or a distilled lesson must not become a
new source. filter_feedback() strips every <recalled-lesson> element (bin/_recall/vault.py
data_line) and drops a segment that carries the distiller's lesson tag "(from distill"; inside a
segment, a line starting with `---` is indented so only real segment headers carry refs.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import _repact_common  # noqa: F401  # side effect: puts hooks/ on sys.path for shared.*
from _distill import discover as D
from shared.journal_reader import read_events_from

# Version of the lite view and chunk rules: a change re-keys every chunk (re-extraction is a
# deliberate choice, so bump this only when the text sent to the model changes).
# lite-2 (U5.4): the extraction prompt, the feedback filter and header defanging.
EXTRACTOR_VERSION = "lite-2"
CHUNK_MAX_TOKENS = 50_000        # design note §4.1: well below the 100K long-prompt tier
MEMORY_LIST_LIMIT = 500          # newest pact-memory records read per run
MEMORY_CLI_TIMEOUT_S = 120
LESSON_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \| .+")   # the inbox line `date | lesson | evidence`
HANDOFF_FIELDS = ("decisions", "uncertainty")            # lead ruling amend_1 (2)
RECALLED_RE = re.compile(r"<\s*recalled-lesson\b.*?(?:</\s*recalled-lesson\s*>|$)", re.I | re.S)
RECALL_LINE_RE = re.compile(r"^[ \t]*recall: .*$\n?", re.M)     # a recall pointer line, quoted
DISTILLED_RE = re.compile(r"\(from distill\b", re.I)
FEEDBACK_RECALLED = "feedback: recalled text removed"
FEEDBACK_DISTILLED = "feedback: quotes a distilled lesson"
SKIP_BAD_UNICODE = "{}: segment is not valid unicode"


@dataclass(frozen=True)
class Segment:
    kind: str
    ref: str
    ts: str
    project: str
    session: str
    text: str
    score: int = 0       # segment.pre_score for a transcript turn; 0 for every lite source


@dataclass(frozen=True)
class Chunk:
    chunk_key: str
    source: str          # the source kind
    session: str
    project: str
    first_ref: str
    last_ref: str
    view_sha256: str
    text: str
    segments: int
    newest_ts: str
    priority: int
    score: int = 0       # the best segment's pre-score: orders chunks of the same priority

    @property
    def size(self) -> int:
        return len(self.text.encode("utf-8"))


def iso_from_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_text(item) -> str:
    """A HANDOFF list item as one line: strings as they are, anything else as compact JSON."""
    if isinstance(item, str):
        return item.strip()
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def run_memory_cli(cli: Path, limit: int = MEMORY_LIST_LIMIT) -> tuple[list, Optional[str]]:
    """(rows, None) from `cli.py list`, or ([], reason). Read verb only; the CLI resolves the
    store and withholds a row that carries a credential. Any failure skips the source.
    The CLI scopes `list` to the project of the current directory, so a run sees that
    project's records only."""
    try:
        done = subprocess.run([sys.executable, str(cli), "list", "--limit", str(limit)],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=MEMORY_CLI_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"pact_memory: the CLI did not run ({type(exc).__name__})"
    try:
        envelope = json.loads(done.stdout)
    except ValueError:
        return [], f"pact_memory: the CLI printed no JSON (exit {done.returncode})"
    if not isinstance(envelope, dict) or envelope.get("ok") is not True:
        return [], "pact_memory: the CLI reported an error"
    rows = envelope.get("result")
    return (rows, None) if isinstance(rows, list) else ([], "pact_memory: no result list")


def _inbox_segments(source: D.Source) -> list:
    out = []
    for line in source.path.read_text(encoding="utf-8").splitlines():
        match = LESSON_RE.match(line.strip())
        if match:
            digest = hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:12]
            out.append(Segment(source.kind, f"memory:{source.session}/{D.INBOX_FILE}#{digest}",
                               match.group(1), "", source.session, line.strip()))
    return out


def _handoff_segments(source: D.Source) -> list:
    out = []
    for event in read_events_from(str(source.path.parent), "agent_handoff"):
        handoff = event.get("handoff")
        if not isinstance(handoff, dict):
            continue
        lines = [f"{name}: {_as_text(item)}" for name in HANDOFF_FIELDS
                 for item in (handoff.get(name) or []) if isinstance(handoff.get(name), list)]
        lines = [line for line in lines if not line.endswith(": ")]
        if lines:
            ref = f"task:{event.get('task_id', '?')}@{source.session}"
            out.append(Segment(source.kind, ref, str(event.get("ts", "")), source.project,
                               source.session, "\n".join(lines)))
    return out


def _summary_segments(source: D.Source) -> list:
    text = source.path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [Segment(source.kind, f"session:{source.session}/{source.path.name}",
                    iso_from_mtime(source.mtime), source.project, source.session, text)]


def _memory_segments(source: D.Source, runner: Callable) -> tuple[list, dict]:
    rows, reason = runner(source.path)
    skipped = {reason: 1} if reason else {}
    out = []
    for row in rows:
        if not isinstance(row, dict) or "withheld" in row:
            skipped["pact_memory: record withheld by the CLI"] = \
                skipped.get("pact_memory: record withheld by the CLI", 0) + 1
            continue
        lines = [f"lesson: {_as_text(x)}" for x in row.get("lessons_learned") or []]
        lines += [f"decision: {_as_text(x)}" for x in row.get("decisions") or []]
        if lines:
            out.append(Segment(source.kind, f"memory:{row.get('id', '?')}",
                               str(row.get("created_at", "")), str(row.get("project_id") or ""),
                               str(row.get("session_id") or "pact-memory"), "\n".join(lines)))
    return out, skipped


def read_source(source: D.Source, memory_runner: Callable = run_memory_cli) -> tuple[list, dict]:
    """(segments, skipped reason -> count) for one source. An unreadable file is skipped."""
    if source.kind == D.KIND_PACT_MEMORY:
        return _memory_segments(source, memory_runner)
    readers = {D.KIND_MEMORY_INBOX: _inbox_segments, D.KIND_HANDOFF: _handoff_segments,
               D.KIND_COMPACT_SUMMARY: _summary_segments}
    try:
        return readers[source.kind](source), {}
    except (OSError, UnicodeDecodeError):
        return [], {f"{source.kind}: unreadable file": 1}


def filter_feedback(segment: Segment) -> tuple:
    """(segment or None, reason or None): recalled text stripped; a segment quoting a distilled
    lesson, or left empty by the strip, is dropped."""
    if DISTILLED_RE.search(segment.text):
        return None, FEEDBACK_DISTILLED
    text = RECALL_LINE_RE.sub("", RECALLED_RE.sub("", segment.text))
    if text == segment.text:
        return segment, None
    if not text.strip():
        return None, FEEDBACK_RECALLED
    return dataclasses.replace(segment, text=text.strip()), FEEDBACK_RECALLED


def valid_unicode(segment: Segment) -> bool:
    """False when a field holds a lone surrogate (a JSON `\\udcff` escape): UTF-8 cannot carry
    it, so the segment is skipped and counted, never hashed or sent."""
    fields = (segment.ref, segment.ts, segment.project, segment.session, segment.text)
    try:
        "\n".join(fields).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def keep_segment(segment: Segment, since: Optional[str], include, exclude) -> bool:
    """The --since date (YYYY-MM-DD, compared on the ts prefix) and the project globs."""
    if since and segment.ts[:10] < since:
        return False
    return D.project_allowed(segment.project, include, exclude)


def chunk_key(source: str, first_ref: str, last_ref: str, view_sha256: str) -> str:
    material = "\n".join((source, first_ref, last_ref, view_sha256, EXTRACTOR_VERSION))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _header_shaped(line: str) -> bool:
    """True when a line reads `---` once redaction has folded it (NFKC, format characters such
    as ZWSP or the soft hyphen removed): fullwidth or small-hyphen dashes count too."""
    if line.startswith("---"):
        return True
    if line.isascii():
        return False
    folded = "".join(c for c in unicodedata.normalize("NFKC", line[:16]) if unicodedata.category(c) != "Cf")
    return folded.startswith("---")


def _render(segment: Segment) -> str:
    """The segment as the model sees it. Only the header line carries a ref: a body line that is
    or folds to `---` is indented (provenance also needs the ref in the raw chunk; schema.py)."""
    body = "\n".join(f" {line}" if _header_shaped(line) else line for line in segment.text.split("\n"))
    return f"--- {segment.ref} {segment.ts}\n{body}"


def _make_chunk(group: list) -> Chunk:
    text = "\n\n".join(_render(seg) for seg in group)
    view = hashlib.sha256(text.encode("utf-8")).hexdigest()
    first, last = group[0], group[-1]
    return Chunk(chunk_key(first.kind, first.ref, last.ref, view), first.kind, first.session,
                 first.project, first.ref, last.ref, view, text, len(group),
                 max(seg.ts for seg in group), D.PRIORITY[first.kind],
                 max(seg.score for seg in group))


def build_chunks(segments: list, max_bytes: int) -> list:
    """Pack segments of the same (kind, session) into chunks of at most `max_bytes` (a single
    larger segment stays whole in its own chunk). Order: priority, then the best pre-score,
    then newest first (lite segments all score 0, so their order is priority, then newest)."""
    groups: dict = {}
    for seg in segments:
        groups.setdefault((seg.kind, seg.session), []).append(seg)
    chunks = []
    for group in groups.values():
        current, size = [], 0
        for seg in group:
            seg_size = len(_render(seg).encode("utf-8")) + 2
            if current and size + seg_size > max_bytes:
                chunks.append(_make_chunk(current))
                current, size = [], 0
            current.append(seg)
            size += seg_size
        if current:
            chunks.append(_make_chunk(current))
    chunks.sort(key=lambda c: c.newest_ts, reverse=True)
    chunks.sort(key=lambda c: (c.priority, -c.score))
    return chunks
