"""Split a transcript view into segments and pre-score each one (KB full). No model call.

Location: pact-plugin/bin/_distill/segment.py. bin/repact-distill calls
read_transcript() for a `transcript` source (discover.KIND_TRANSCRIPT) in place of
sources.read_source(); the segments then take the same path as every lite source:
valid_unicode -> keep_segment -> filter_feedback -> build_chunks -> estimate (dry run) or
redact_chunk -> backend (run).

A segment is one turn: a human prompt and the assistant prose after it, up to the next prompt
(prose before the first prompt is a turn of its own). A turn past SEGMENT_MAX_BYTES is split at a
piece boundary. Each segment's ref is `session:<session>/<R0_VERSION>#t<n>` (schema.py's evidence
type `session`), so a change of the view rules re-keys every transcript chunk.

The pre-score is a cheap, deterministic count of lesson cues (pre_score): a correction in the
human's words, a cause-or-fix phrase in the prose, and a failure followed by a fix. build_chunks
orders transcript chunks by their best segment's score, so a capped run reads likely lessons
first; packing stays in turn order, so the chunk keys of a finished session do not move.
"""
from __future__ import annotations

import re

from _distill import discover as D
from _distill import sources as S
from _distill import view as V

SEGMENT_MAX_BYTES = 48 * 1024
CORRECTION_RE = re.compile(
    r"\b(?:no|nope|wrong|don'?t|do not|stop|instead|actually|why did you|that'?s not|"
    r"not what|you forgot|again|revert|undo)\b", re.I)
LESSON_RE = re.compile(
    r"\b(?:root cause|the fix|fixed by|turned out|the (?:issue|problem|bug) was|because|lesson|"
    r"gotcha|mistake|i was wrong|workaround|regression|caveat|pitfall|the trap)\b", re.I)
FAILURE_RE = re.compile(r"\b(?:error|failed|fails|failing|exception|traceback|refused|denied)\b",
                        re.I)
FIX_RE = re.compile(r"\b(?:fixed|fix|works now|passes now|resolved|solved)\b", re.I)
CORRECTION_POINTS, LESSON_POINTS, LESSON_CAP, FAIL_FIX_POINTS = 3, 2, 6, 2
SUBSTANTIAL_BYTES, SUBSTANTIAL_POINTS = 400, 1


def pre_score(turns: list) -> int:
    """Lesson cues in one segment, as a whole number (0 = no cue)."""
    prompts = [t.text for t in turns if t.role == "user"]
    prose = "\n".join(t.text for t in turns if t.role == "assistant")
    score = CORRECTION_POINTS * sum(1 for text in prompts if CORRECTION_RE.search(text))
    cues = {match.lower() for match in LESSON_RE.findall(prose)}
    score += min(LESSON_CAP, LESSON_POINTS * len(cues))
    whole = "\n".join(prompts) + "\n" + prose
    failure = FAILURE_RE.search(whole)
    if failure and FIX_RE.search(whole, failure.end()):
        score += FAIL_FIX_POINTS
    if len(whole.encode("utf-8")) >= SUBSTANTIAL_BYTES:
        score += SUBSTANTIAL_POINTS
    return score


def split_turns(turns: list) -> list:
    """[[Turn]] per segment: a user turn opens one; a segment past SEGMENT_MAX_BYTES closes
    before the piece that would overflow it (one piece larger than that stays whole)."""
    segments, current, size = [], [], 0
    for turn in turns:
        piece = len(turn.text.encode("utf-8"))
        if current and (turn.role == "user" or size + piece > SEGMENT_MAX_BYTES):
            segments.append(current)
            current, size = [], 0
        current.append(turn)
        size += piece
    if current:
        segments.append(current)
    return segments


def render(turns: list) -> str:
    return "\n".join(f"{turn.role}: {turn.text}" for turn in turns)


def read_transcript(source: D.Source) -> tuple:
    """(segments, skipped reason -> count) for one lead transcript; the view's counters are
    reported as `transcript: <reason>`."""
    view = V.read_view(source.path)
    fallback_ts = S.iso_from_mtime(source.mtime)
    out = []
    for index, turns in enumerate(split_turns(view.turns), start=1):
        ts = next((t.ts for t in turns if t.ts), fallback_ts)
        out.append(S.Segment(source.kind, f"session:{source.session}/{V.R0_VERSION}#t{index}", ts,
                             source.project, source.session, render(turns), pre_score(turns)))
    skipped = {f"{source.kind}: {reason}": count for reason, count in view.counts.items()}
    return out, skipped
