"""The KB lesson: THE shared unit definition (KB-CONTRACT; the design notes
sections 4.1 to 4.4).

Location: pact-plugin/bin/_kb/lessons.py. Imported (by path, stdlib only) by the curation
apply, KB-REACH and KB-GATE, so every reader counts the same units the KB census counted.

parse(text) is unit 14's units3 v3, unchanged in behaviour (a frozen copy in
tests/user_layer/fixtures/units3_v3.py checks parity):
  - units are `### title *(from ctx)*` blocks, top-level `- ` bullets and paragraphs; a bare
    `- [[link]]` bullet is a `link` unit, not a lesson;
  - `## ` headings named in KB_SECTIONS start a section; any other `##`/`###` inside a block is
    content, and a plain `### ` line after an open unit is content too;
  - v3: a block CLOSES at the next top-level KB bullet (`- ... *(from ctx)*` at line end) or a bare
    link bullet;
  - the body ends at the LAST `## Related`.
lessons(text, relpath) adds, per non-link unit:
  - id = `<relpath>#<sha256 of the whitespace-normalised first line>[:12]`, with `~2`, `~3` ... for
    a repeated first line, in document order. Stable under whitespace-only edits and additive appends;
  - title, provenance context and the new grammar's date from `*(from ctx[, YYYY-MM-DD])*`;
  - the section 4.3 status markers (`> Superseded|Dated|Status|Verified|Conflicts|Unverified
    [YYYY-MM-DD]: ...`): the latest state marker gives the state (Status is an alias of Dated),
    Conflicts and Unverified are flags that leave the state alone.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional, Tuple

KB_SECTIONS = {"Key Decisions", "Key Components", "Lessons Learned", "Patterns", "Gotchas", "API Quirks",
               "Workflows", "Design Reasoning", "Notes"}
BLOCK_RE = re.compile(r'^### .+\*\(from [^)]*\)\*\s*$')
KB_BULLET_RE = re.compile(r'^- .*\*\(from [^)]*\)\*\s*$')
LINK_BULLET_RE = re.compile(r'^- \[\[[^\]]+\]\]\s*$')
PROVENANCE_RE = re.compile(r'\s*\*\(from (?P<ctx>[^),]*?)(?:,\s*(?P<date>\d{4}-\d{2}-\d{2}))?\)\*\s*$')
MARKER_RE = re.compile(r'^> (?P<kind>Superseded|Dated|Status|Verified|Conflicts|Unverified)'
                       r'(?: (?P<date>\d{4}-\d{2}-\d{2}))?: ')
STATE_OF = {"Superseded": "superseded", "Dated": "dated", "Status": "dated", "Verified": "current"}
FLAG_OF = {"Conflicts": "conflicts", "Unverified": "unverified"}


def _body_start(lines: List[str]) -> int:
    """The index of the first line after the frontmatter (0 when there is none)."""
    i = 0
    if lines and lines[0].strip() == '---':
        i = 1
        while i < len(lines) and lines[i].strip() != '---':
            i += 1
        i += 1
    return i


def parse(text: str) -> Tuple[List[dict], int]:
    """(units, end): units as {kind, sec, start, end} with 1-based inclusive line numbers; `end` is
    the 0-based index of the last `## Related` line (or the line count). units3 v3, unchanged."""
    lines = text.split('\n')
    i = _body_start(lines)
    related = [n for n in range(i, len(lines)) if lines[n].strip() == '## Related']
    end = related[-1] if related else len(lines)
    units: List[dict] = []
    cur: Optional[dict] = None
    sec: Optional[str] = None
    in_block = False

    def close(e: int) -> None:
        nonlocal cur
        if cur:
            while e > cur['start'] and not lines[e - 1].strip():
                e -= 1
            cur['end'] = e
            units.append(cur)
            cur = None

    for n in range(i, end):
        line = lines[n]
        ln = n + 1
        if line.startswith('# ') and sec is None:
            continue
        if line.startswith('## ') and line[3:].strip() in KB_SECTIONS:
            close(ln - 1)
            sec = line[3:].strip()
            in_block = False
            continue
        if BLOCK_RE.match(line) or (line.startswith('### ') and cur is None):
            close(ln - 1)
            cur = {'kind': 'block', 'sec': sec, 'start': ln}
            in_block = True
            continue
        if in_block and (KB_BULLET_RE.match(line) or LINK_BULLET_RE.match(line)):
            in_block = False
        if line.startswith('- ') and not in_block:
            close(ln - 1)
            cur = {'kind': 'link' if LINK_BULLET_RE.match(line) else 'bullet', 'sec': sec, 'start': ln}
            continue
        if cur is None and line.strip() and not line.startswith('#'):
            cur = {'kind': 'para', 'sec': sec, 'start': ln}
    close(end)
    return units, end


def coverage(text: str, units: List[dict], end: int) -> List[int]:
    """Line numbers of non-blank body lines that are not in exactly one unit (section headers and
    the H1 may be in none). units3 v3, unchanged."""
    lines = text.split('\n')
    i = _body_start(lines)
    covered = [0] * (len(lines) + 2)
    for unit in units:
        for n in range(unit['start'], unit['end'] + 1):
            covered[n] += 1
    bad = []
    for n in range(i + 1, end + 1):
        line = lines[n - 1]
        if not line.strip():
            continue
        if line.startswith('# ') or (line.startswith('## ') and line[3:].strip() in KB_SECTIONS):
            if covered[n] == 0:
                continue
        if covered[n] != 1:
            bad.append(n)
    return bad


@dataclass(frozen=True)
class Marker:
    kind: str                       # Superseded, Dated, Status, Verified, Conflicts or Unverified
    date: Optional[str]
    line: int                       # 1-based line number in the page


@dataclass(frozen=True)
class Lesson:
    id: str
    kind: str                       # block, bullet or para
    sec: Optional[str]
    start: int
    end: int
    first_line: str                 # whitespace-normalised
    title: str
    ctx: Optional[str]
    date: Optional[str]
    markers: Tuple[Marker, ...]
    state: str                      # current, dated or superseded
    flags: Tuple[str, ...]          # conflicts, unverified


def normalise(line: str) -> str:
    return " ".join(line.split())


def _title_and_provenance(first_line: str, kind: str) -> Tuple[str, Optional[str], Optional[str]]:
    text = first_line
    if kind == "block" and text.startswith("### "):
        text = text[4:]
    elif kind == "bullet" and text.startswith("- "):
        text = text[2:]
    match = PROVENANCE_RE.search(text)
    if not match:
        return text.strip(), None, None
    ctx = match.group("ctx").strip() or None
    return text[:match.start()].strip(), ctx, match.group("date")


def _markers(lines: List[str], start: int, end: int) -> Tuple[Marker, ...]:
    found = []
    for number in range(start + 1, end + 1):
        match = MARKER_RE.match(lines[number - 1])
        if match:
            found.append(Marker(match.group("kind"), match.group("date"), number))
    return tuple(found)


def lessons(text: str, relpath: str) -> List[Lesson]:
    """Every lesson on the page (link units excluded), with ids, provenance and status."""
    lines = text.split('\n')
    units, _end = parse(text)
    seen: Counter = Counter()
    out = []
    for unit in units:
        if unit['kind'] == 'link':
            continue
        first = normalise(lines[unit['start'] - 1])
        digest = hashlib.sha256(first.encode("utf-8")).hexdigest()[:12]
        seen[digest] += 1
        lesson_id = f"{relpath}#{digest}" + (f"~{seen[digest]}" if seen[digest] > 1 else "")
        title, ctx, date = _title_and_provenance(first, unit['kind'])
        markers = _markers(lines, unit['start'], unit['end'])
        states = [STATE_OF[m.kind] for m in markers if m.kind in STATE_OF]
        flags = tuple(dict.fromkeys(FLAG_OF[m.kind] for m in markers if m.kind in FLAG_OF))
        out.append(Lesson(lesson_id, unit['kind'], unit['sec'], unit['start'], unit['end'], first, title,
                          ctx, date, markers, states[-1] if states else "current", flags))
    return out
