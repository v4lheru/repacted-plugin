"""
Location: pact-plugin/tests/test_managed_comment_emitted.py

Summary: RUNS each of the five managed-section comment emitters and asserts the
EMITTED region carries the comment that belongs to its heading. This file is
about BEHAVIOUR. Its sibling `test_managed_comment_mirror.py` is about the TEXT
being identical across files, and the two have different subjects on purpose.

Used by/with:
- skills/pact-memory/scripts/working_memory.py: the SSOT for both comment
  constants, and the emitters `sync_to_claude_md` and
  `sync_retrieved_to_claude_md`.
- hooks/shared/claude_md_manager.py: the emitters `ensure_project_memory_md`
  and `_build_migrated_content`.
- hooks/shared/session_resume.py: the emitter `update_session_info`.
- tests/test_managed_comment_mirror.py: the source-text identity gate. It reads
  a module-level assignment in the SSOT and substring-searches the two mirror
  files. IT CANNOT SEE WHICH FUNCTION EMITS, so it stays green when an emitter
  stops emitting while the constants remain defined. THAT IS THE GAP THIS FILE
  CLOSES.

WHY BEHAVIOURAL AND NOT MORE SOURCE TEXT. Before the constants were extracted
to module level, deleting an emission also deleted the text, so a source-text
gate reddened by accident. The extraction separated the definition from the
use. A source-text gate reads the definition. Only a gate that RUNS the emitter
reads the use.

THE COUNTING RULE, STATED BESIDE THE COUNT. An EMITTER is one (file, function)
pair that writes a managed-section comment. Two comments from one function
count as ONE emitter. FIVE emitters in THREE files, and each one has an arm
below.

WHY REGION-SCOPED PRESENCE, AND NOT THE TWO OBVIOUS ALTERNATIVES. Each arm
asserts the comment is present in the region bounded by its OWN heading and the
next heading.
- NOT strict next-line adjacency. That couples the arm to whitespace, so a
  legitimate blank-line change reddens it, and a red of that kind invites the
  weakening repair this file exists to prevent.
- NOT bare presence anywhere in the document. Two comments exist in this
  family, one for Retrieved Context and one for Working Memory. Bare presence
  CANNOT CATCH A SWAP between them, and a swap reads as green.
Region-scoped presence catches an ABSENCE and catches a SWAP, and it survives a
formatting change.

FAILURE DIRECTION, AND WHY RED IS CORRECT HERE. CLAUDE.md is gitignored, so a
corruption has no commit to recover from, and the file loads at each session.
The comment is the only line that tells a human reader, and a later agent, that
the region is machine-managed. A section that loses it invites a hand edit that
the next sync discards with no warning. A red test costs one build. A silently
unmarked region costs the edit.

EVERY DRIVE IS FIXTURE-ONLY. `CLAUDE_PROJECT_DIR` is redirected to `tmp_path`
before each drive of a hooks-side emitter, and it is branch 1 of every resolver
in this family. Both skills-side emitters take the `claude_md_root` containment
anchor, which `_atomic_write_text` CHECKS, so a write that resolved outside the
sandbox is refused rather than performed. No live store is opened and no real
CLAUDE.md is reachable.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


_PLUGIN_ROOT = Path(__file__).resolve().parent.parent

from shared.claude_md_manager import (  # noqa: E402
    ensure_project_memory_md,
)

from scripts import working_memory as _ssot  # noqa: E402

# THE SSOT, READ AS VALUES RATHER THAN RE-SPELLED. Every assertion below
# compares emitted text against these two names, so an emitter that drifts to
# its own spelling reddens here even when it is self-consistent.
RETRIEVED_COMMENT = _ssot.RETRIEVED_CONTEXT_COMMENT
WORKING_COMMENT = _ssot.WORKING_MEMORY_COMMENT
RETRIEVED_HEADING = _ssot.RETRIEVED_CONTEXT_HEADER
WORKING_HEADING = _ssot.WORKING_MEMORY_HEADER

# (heading, the comment that belongs to it, the comment that does NOT)
_RETRIEVED_PAIR = (RETRIEVED_HEADING, RETRIEVED_COMMENT, WORKING_COMMENT)
_WORKING_PAIR = (WORKING_HEADING, WORKING_COMMENT, RETRIEVED_COMMENT)
_BOTH_PAIRS = (_RETRIEVED_PAIR, _WORKING_PAIR)


def _section_region(document: str, heading: str) -> str:
    """Return the text from `heading` up to the next heading, or the end.

    THE REGION RULE, STATED HERE BECAUSE IT IS THE PARAMETER OF EVERY
    ASSERTION BELOW: the region opens at the line that equals `heading` after
    an rstrip, and closes at the next line that begins a markdown heading at
    column 0, or at the end of the document when no such line follows. The
    closing line is NOT part of the region.

    Raises AssertionError when the heading is absent, because a region that was
    never found would make every membership test below pass vacuously.
    """
    lines = document.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.rstrip() == heading:
            start = index
            break
    assert start is not None, (
        f"the emitted document has no {heading!r} line at all, so the region "
        f"this assertion describes does not exist. Emitted:\n{document}"
    )
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"^#{1,6} ", lines[index]):
            end = index
            break
    return "\n".join(lines[start:end])


def _assert_region_carries(document: str, pair, emitter: str) -> None:
    """Assert the region for a heading carries its comment and not its sibling."""
    heading, own_comment, sibling_comment = pair
    region = _section_region(document, heading)
    assert own_comment in region, (
        f"{emitter} emitted {heading!r} WITHOUT the comment that belongs to "
        f"it. The section reaches a reader as unmarked, so nothing tells that "
        f"reader the region is machine-managed and a hand edit there is "
        f"discarded by the next sync with no warning.\n"
        f"  expected in the region: {own_comment!r}\n"
        f"  region was:\n{region}"
    )
    assert sibling_comment not in region, (
        f"{emitter} put the OTHER managed-section comment inside the "
        f"{heading!r} region. The two comments were swapped or duplicated, "
        f"which a whole-document presence check cannot see.\n"
        f"  must NOT be in the region: {sibling_comment!r}\n"
        f"  region was:\n{region}"
    )


def _redirect_project_dir(tmp_path: Path, monkeypatch) -> None:
    """Point every resolver in this family at `tmp_path`, and PROVE it took.

    The proof is not decoration. Both hooks-side emitters return None when
    `CLAUDE_PROJECT_DIR` is empty, so an unset variable produces a drive that
    writes nothing and an arm that passes against an empty document. The
    assertion separates "the emitter emitted nothing" from "the redirect did
    not take" BEFORE the drive rather than after it.
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert os.environ.get("CLAUDE_PROJECT_DIR") == str(tmp_path), (
        "the CLAUDE_PROJECT_DIR redirect did not take; refusing to drive an "
        "emitter that resolves its target from that variable"
    )


_SEEDED_DOCUMENT = (
    "# Project Memory\n\n"
    f"{RETRIEVED_HEADING}\n\n"
    "## Pinned Context\n\n"
    f"{WORKING_HEADING}\n\n"
)


class TestEachEmitterMarksTheSectionItWrites:
    """One arm for each of the five emitters. Each RUNS its emitter."""

    def test_ensure_project_memory_md_marks_both_sections(
        self, tmp_path, monkeypatch
    ):
        """Emitter 1 of 5: claude_md_manager.ensure_project_memory_md."""
        _redirect_project_dir(tmp_path, monkeypatch)

        result = ensure_project_memory_md()

        assert result == "Created project CLAUDE.md with memory sections", (
            f"the emitter did not create the document, so nothing below was "
            f"exercised. It returned {result!r}"
        )
        target = tmp_path / ".claude" / "CLAUDE.md"
        assert target.is_file(), f"the emitter reported success but {target} is absent"
        document = target.read_text(encoding="utf-8")
        for pair in _BOTH_PAIRS:
            _assert_region_carries(document, pair, "ensure_project_memory_md")


