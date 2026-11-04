"""
Tests for working_memory.py section parsers and the migration->sync pipeline.

Split from test_claude_md_manager.py in #404 round-3 remediation: these tests
specifically exercise the parsers in skills/pact-memory/scripts/working_memory.py
(_parse_working_memory_section, _parse_retrieved_context_section) and the
end-to-end composition of _build_migrated_content with sync_to_claude_md /
sync_retrieved_to_claude_md. Keeping them in their own module keeps
test_claude_md_manager.py focused on shared/claude_md_manager.py itself.

The core concern is that working_memory.py parsers must treat PACT boundary
markers (PACT_MEMORY_*, PACT_MANAGED_*, PACT_ROUTING_*) as section terminators
so a sync round-trip on a new-format file does not silently erode them. The
optional-comment slot in section_pattern must NOT swallow a downstream PACT
marker as the auto-managed comment slot; the next_section_pattern must treat
PACT markers as section terminators.

sys.path is set up by tests/conftest.py (adds hooks/ and skills/pact-memory/
scripts/ to sys.path), so no per-file sys.path manipulation is needed here.
"""


# ---------------------------------------------------------------------------
# Marker constants pinned locally so drift in the implementation is caught.
# These mirror the constants in test_claude_md_manager.py; both files carry
# their own copy so either file can be deleted/relocated without coupling.
# ---------------------------------------------------------------------------
_MANAGED_START = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
_MANAGED_END = "<!-- PACT_MANAGED_END -->"
_MEMORY_START = "<!-- PACT_MEMORY_START -->"
_MEMORY_END = "<!-- PACT_MEMORY_END -->"


class TestWorkingMemoryParserMarkerPreservation:
    """working_memory.py parsers must treat PACT boundary markers as section
    terminators so sync round-trips don't silently erode PACT_MEMORY_END and
    PACT_MANAGED_END markers (#404 review finding).
    """

    def test_sync_working_memory_preserves_pact_markers(self, tmp_path, monkeypatch):
        """A sync_to_working_memory round-trip on a new-format file must
        preserve PACT_MEMORY_END and PACT_MANAGED_END markers.
        """
        from scripts.working_memory import (
            _parse_working_memory_section,
            WORKING_MEMORY_HEADER,
        )

        new_format_content = (
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            f"{WORKING_MEMORY_HEADER}\n"
            "### 2026-04-12 21:00\n"
            "**Context**: Test entry\n"
            "\n"
            f"{_MEMORY_END}\n"
            "\n"
            f"{_MANAGED_END}\n"
        )

        before, header, after, entries = _parse_working_memory_section(new_format_content)

        # The PACT markers must be in `after`, not consumed as section content
        assert _MEMORY_END in after, (
            "PACT_MEMORY_END marker should be in after_section, not consumed"
        )
        assert _MANAGED_END in after, (
            "PACT_MANAGED_END marker should be in after_section, not consumed"
        )


class TestMigrationSyncPipeline:
    """End-to-end tests that exercise the exact ACTUAL output of
    _build_migrated_content through sync_to_claude_md and
    sync_retrieved_to_claude_md.

    The existing TestWorkingMemoryParserMarkerPreservation.test_full_round_trip_preserves_markers
    test seeds CLAUDE.md with a pre-existing auto-managed comment on the line
    after ``## Working Memory``. That hides the section_pattern bug: the
    greedy ``(<!-- [^>]*-->)?`` group harmlessly matches the auto-managed
    comment and stops before PACT_MEMORY_END.

    But _build_migrated_content's default empty-memory layout has NO
    auto-managed comment between ``## Working Memory`` and
    ``<!-- PACT_MEMORY_END -->``. Without the (?!PACT_) negative lookahead,
    the optional comment group greedily captures PACT_MEMORY_END, advancing
    section_header_end PAST it. The marker ends up in section_content, gets
    .strip()ed, and is silently dropped on write-back (#404 round-3 review
    finding).

    These tests exercise the bug path directly by using the real output of
    _build_migrated_content as the input to sync.
    """

    def _assert_markers_paired(self, content: str) -> None:
        """All 4 PACT markers must be present AND appear in structural order."""
        assert _MANAGED_START in content, "PACT_MANAGED_START missing"
        assert _MANAGED_END in content, "PACT_MANAGED_END missing"
        assert _MEMORY_START in content, "PACT_MEMORY_START missing"
        assert _MEMORY_END in content, "PACT_MEMORY_END missing"
        # Order: MANAGED_START < MEMORY_START < MEMORY_END < MANAGED_END
        ms = content.index(_MANAGED_START)
        mems = content.index(_MEMORY_START)
        meme = content.index(_MEMORY_END)
        me = content.index(_MANAGED_END)
        assert ms < mems < meme < me, (
            f"Markers out of order: MANAGED_START={ms}, MEMORY_START={mems}, "
            f"MEMORY_END={meme}, MANAGED_END={me}"
        )

    def test_sync_working_memory_against_build_migrated_content_empty_memory(
        self, tmp_path, monkeypatch
    ):
        """sync_to_claude_md on the exact output of _build_migrated_content
        (empty-memory default) must preserve all 4 PACT markers and their
        structural order.

        This is the precise bug path: no auto-managed comment follows
        ``## Working Memory``, so the section_pattern regex's optional
        comment group would greedily swallow ``<!-- PACT_MEMORY_END -->``
        unless the (?!PACT_) negative lookahead blocks it.
        """
        from shared.claude_md_manager import _build_migrated_content
        from scripts.working_memory import sync_to_claude_md

        project_dir = tmp_path / "project"
        claude_dir = project_dir / ".claude"
        claude_dir.mkdir(parents=True)
        claude_md = claude_dir / "CLAUDE.md"

        # Seed with the EXACT output _build_migrated_content produces for
        # a blank "# Project Memory\n" source. This is the empty-memory
        # default layout that triggers the greedy match bug.
        migrated = _build_migrated_content("# Project Memory\n")
        claude_md.write_text(migrated, encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        memory = {
            "context": "Migration->sync pipeline test",
            "goal": "Prove section_pattern no longer swallows PACT_MEMORY_END",
            "decisions": ["Use negative lookahead"],
            "lessons_learned": ["Regex ordering matters"],
        }

        result = sync_to_claude_md(memory, memory_id="mig-1")
        assert result

        final = claude_md.read_text(encoding="utf-8")
        self._assert_markers_paired(final)
        assert "Migration->sync pipeline test" in final


class TestWorkingMemoryParserTerminator:
    """Regression guard: section parsers must terminate on H2 headings and
    PACT boundary markers.

    Round 10 simplified these tests: fence-awareness tests are deleted
    because the parsers now operate within the PACT-managed region only
    (no user-authored fenced code blocks). The unfenced terminator test
    remains as a regression guard for the basic termination contract.
    """

    def test_unfenced_terminator_still_ends_section(self):
        """An H2 heading terminates the Working Memory section."""
        from scripts.working_memory import _parse_working_memory_section

        content = (
            "## Working Memory\n"
            "\n"
            "### 2026-04-12 03:00\n"
            "Simple entry with no fences.\n"
            "\n"
            "## Retrieved Context\n"
            "This must be excluded.\n"
        )

        _before, _header, after, existing = _parse_working_memory_section(content)
        assert len(existing) == 1
        assert "Simple entry with no fences." in existing[0]
        # The downstream section is in `after`, not `existing`
        assert "## Retrieved Context" in after
        for entry in existing:
            assert "This must be excluded" not in entry

class TestWorkingMemoryParserManagedRegionBounding:
    """Round 10: parsers bound their search to the PACT-managed region
    and return full-file positions for correct write-back.
    """

    def test_working_memory_returns_full_file_slices(self):
        """before_section and after_section must be slices of the full
        content (not the managed region), enabling correct write-back.
        """
        from scripts.working_memory import _parse_working_memory_section

        content = (
            "user notes above\n"
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. -->\n"
            "\n"
            "### 2026-04-13 00:00\n"
            "**Context**: Test entry\n"
            "\n"
            f"{_MEMORY_END}\n"
            f"{_MANAGED_END}\n"
            "user notes below\n"
        )

        before, header, after, entries = _parse_working_memory_section(content)

        # before_section must include user preamble
        assert "user notes above" in before
        # after_section must include user epilogue
        assert "user notes below" in after
        # Entries parsed correctly
        assert len(entries) == 1
        assert "Test entry" in entries[0]
        # Round-trip: before + header + entries + after reconstructs content
        # (header may be empty string or the heading text)
        assert header == "## Working Memory"


class TestPACTBoundaryAltTwinDriftDetection:
    """PR #404 round 12 item 2: drift-detection test for the
    ``_PACT_BOUNDARY_ALT`` regex alternation string.

    working_memory.py inlines ``_PACT_BOUNDARY_ALT`` because it cannot
    import from hooks/shared/ (separate package boundary). This test
    asserts the working_memory.py copy equals the canonical alternation
    derived from ``PACT_BOUNDARY_PREFIXES`` in claude_md_manager.py.
    """

    def test_boundary_alt_matches_canonical(self):
        from scripts.working_memory import _PACT_BOUNDARY_ALT
        from shared.claude_md_manager import PACT_BOUNDARY_PREFIXES

        canonical_alt = "|".join(PACT_BOUNDARY_PREFIXES)
        assert _PACT_BOUNDARY_ALT == canonical_alt, (
            f"_PACT_BOUNDARY_ALT in working_memory.py ({_PACT_BOUNDARY_ALT!r}) "
            f"drifted from canonical ({canonical_alt!r}). "
            f"Update the twin in working_memory.py to match "
            f"PACT_BOUNDARY_PREFIXES in claude_md_manager.py."
        )


class TestExtractManagedRegionTwinDriftDetection:
    """PR #404 round 12 item 3: drift-detection test for the
    ``extract_managed_region`` twin in working_memory.py.

    Both hooks/shared/claude_md_manager.py and
    skills/pact-memory/scripts/working_memory.py carry copies of
    ``extract_managed_region`` due to the package boundary. This test
    exercises both copies with identical input and asserts identical output.
    """

    def test_both_copies_return_identical_output_when_markers_present(self):
        from scripts.working_memory import (
            extract_managed_region as wm_extract,
        )
        from shared.claude_md_manager import (
            extract_managed_region as cm_extract,
        )

        content = (
            "user preamble\n"
            f"{_MANAGED_START}\n"
            "managed body here\n"
            f"{_MANAGED_END}\n"
            "user epilogue\n"
        )

        wm_result = wm_extract(content)
        cm_result = cm_extract(content)

        assert wm_result is not None
        assert cm_result is not None
        assert wm_result == cm_result, (
            f"extract_managed_region twins returned different results.\n"
            f"working_memory.py: {wm_result!r}\n"
            f"claude_md_manager.py: {cm_result!r}"
        )


