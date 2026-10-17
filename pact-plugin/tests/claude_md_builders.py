"""
Location: pact-plugin/tests/claude_md_builders.py
Summary: Build a project CLAUDE.md whose boundary layout comes from the
         production emitter, for the memory-region and forged-heading tests.
Used by: tests/test_splice_window_memory_region.py,
         tests/test_session_end_survives_a_forged_heading.py.

Moved here from tests/test_pin_marker_writer.py when the 1.5.0 cut retired
the pin marker writer; the two consumers above still need the builder.
"""
from __future__ import annotations

import functools
import os
import tempfile
from pathlib import Path

from shared.claude_md_manager import (
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    ensure_project_memory_md,
)


@functools.lru_cache(maxsize=1)
def production_skeleton() -> str:
    """The canonical managed document, TAKEN FROM THE PRODUCTION EMITTER.

    `ensure_project_memory_md` is the code that creates a project CLAUDE.md,
    so driving it is what makes this corpus agree with production about the
    SHAPE OF THE DOCUMENT. It is run against a temporary project dir and the
    file it wrote is read back.

    WHY THIS IS NOT A CONVENIENCE. The builders here previously spelled the
    boundary layout by hand, and they spelled it WRONG: they emitted the outer
    managed marker and no inner memory pair, which is a shape production never
    produces. Every fixture in this corpus therefore agreed with every other
    fixture and with nothing that ships. A hand-written corpus cannot detect
    that, because the thing it would have to compare against is the very
    assumption it encodes.

    THE HEAD IS THE LOAD-BEARING PART. Production emits the managed marker,
    the title, THEN the session block, THEN the memory marker, so the session
    block sits ABOVE the memory region. That ordering is the whole subject of
    the window the memory-region writers search, and a hand-built head omits it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        previous = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        try:
            ensure_project_memory_md()
            return (Path(tmp) / ".claude" / "CLAUDE.md").read_text(
                encoding="utf-8"
            )
        finally:
            if previous is None:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = previous


def production_head_and_tail() -> tuple[str, str]:
    """Split the production skeleton at its MEMORY marker pair.

    Returns `(head, tail)` where `head` runs from the start of the document
    through the memory START marker line, and `tail` runs from the memory END
    marker line to the end. Section bodies go between them.

    So a caller varies what sits INSIDE the memory region and never restates
    the boundary layout that encloses it.
    """
    doc = production_skeleton()
    start = doc.index(MEMORY_START_MARKER) + len(MEMORY_START_MARKER)
    # Take the newline that terminates the marker line with the head, so the
    # caller's first body line begins a line of its own.
    head = doc[:start] + "\n"
    tail = doc[doc.index(MEMORY_END_MARKER):]
    return head, tail


def build_claude_md(
    pinned_body: str = "### A pin\nSome pinned prose.\n\n",
    retrieved: str = "\n### 2026-01-01\nA retrieved entry.\n\n",
    working: str = "\n### 2026-01-02\nA working entry.\n",
    user_prefix: str = "# My own heading\n\nUser prose above the block.\n\n",
    user_suffix: str = "\nUser prose below the block.\n",
    include_pinned_heading: bool = True,
    managed: bool = True,
) -> str:
    """Compose a CLAUDE.md in the canonical section order.

    Canonical order is Retrieved Context, Pinned Context, Working Memory, which
    is what puts a start marker above the pinned heading INSIDE the Retrieved
    Context span -- the reason both marker names must join the terminator
    alternation.

    THE BOUNDARY LAYOUT COMES FROM `production_head_and_tail`, NEVER FROM A
    LITERAL HERE. Only the section bodies are this function's own. Read
    `production_skeleton` for why a hand-written boundary is the defect rather
    than the shortcut.
    """
    pinned = (
        f"## Pinned Context\n\n{pinned_body}" if include_pinned_heading else ""
    )
    sections = (
        f"## Retrieved Context\n{retrieved}"
        f"{pinned}"
        f"## Working Memory\n{working}"
    )
    if not managed:
        # No managed region at all, so no memory region either. The title stays
        # because a pre-migration document still carries user headings.
        return (
            user_prefix
            + "# PACT Framework and Managed Project Memory\n\n"
            + sections
            + user_suffix
        )
    head, tail = production_head_and_tail()
    return user_prefix + head + sections + tail + user_suffix
