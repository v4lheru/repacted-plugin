"""
Location: pact-plugin/hooks/shared/marked_regions.py
Summary: Parser and replacer for marked regions: the lines between
         `<!-- protocol:NAME -->` and `<!-- /protocol -->` (each marker on a line
         of its own, optionally behind a `# ` so it can sit in Python source or a
         fenced code block). Inside a region the renderer owns every byte; the
         prose around it is the author's.
Used by: dev/protocol/render.py (write or check the regions),
         tests/contract/test_protocol_rendered.py (pin the regions). Not imported
         by any hook at runtime.

Contract: pure; stdlib only; no I/O. `find_regions` raises RegionError on a
malformed file (unterminated, nested or duplicated region), because a silent
skip would let a region drift unseen.

Example:
    text = replace_region(text, "teachback", render_skill_teachback())
"""

from __future__ import annotations

import re
from dataclasses import dataclass

OPEN = re.compile(r"^\s*(?:#\s*)?<!--\s*([a-z]+):([a-z0-9-]+)\s*-->\s*$")
CLOSE = re.compile(r"^\s*(?:#\s*)?<!--\s*/([a-z]+)\s*-->\s*$")


class RegionError(ValueError):
    """A marker sequence the parser refuses: nested, unterminated, or repeated."""


@dataclass(frozen=True)
class Region:
    kind: str
    name: str
    start: int   # index of the first body line
    end: int     # index of the closing marker line (exclusive end of the body)
    body: str


def find_regions(text: str, kind: str = "protocol") -> dict[str, Region]:
    """Every `<!-- kind:name -->` ... `<!-- /kind -->` region in `text`, by name.

    Lines are split on "\\n" only, so a CRLF file keeps its "\\r" inside the body and
    a byte-for-byte comparison with LF-rendered text fails loudly instead of passing.
    """
    lines = text.split("\n")
    regions: dict[str, Region] = {}
    open_name: str | None = None
    open_at = 0
    for i, line in enumerate(lines):
        opened = OPEN.match(line)
        closed = CLOSE.match(line)
        if opened and opened.group(1) == kind:
            if open_name is not None:
                raise RegionError(f"region {opened.group(2)!r} opens inside {open_name!r} at line {i + 1}")
            if opened.group(2) in regions:
                raise RegionError(f"region {opened.group(2)!r} appears twice (second at line {i + 1})")
            open_name, open_at = opened.group(2), i + 1
        elif closed and closed.group(1) == kind:
            if open_name is None:
                raise RegionError(f"closing marker without an open region at line {i + 1}")
            regions[open_name] = Region(kind, open_name, open_at, i, "\n".join(lines[open_at:i]))
            open_name = None
    if open_name is not None:
        raise RegionError(f"region {open_name!r} opened at line {open_at} is never closed")
    return regions


def replace_region(text: str, name: str, body: str, kind: str = "protocol") -> str:
    """`text` with the body of region `name` replaced by `body`; the markers stay."""
    region = find_regions(text, kind).get(name)
    if region is None:
        raise RegionError(f"no region {name!r} in the text")
    lines = text.split("\n")
    return "\n".join(lines[:region.start] + body.split("\n") + lines[region.end:])
