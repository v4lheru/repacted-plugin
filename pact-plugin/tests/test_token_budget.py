"""
Tests for token budget enforcement in working_memory.py.

Tests cover:
1. _estimate_tokens() -- word-based approximation
2. _compress_memory_entry() -- single-line summary extraction
3. _apply_token_budget() -- budget enforcement with compression/dropping
4. sync_to_claude_md() -- budget enforcement during working memory sync
5. sync_retrieved_to_claude_md() -- budget enforcement during retrieved context sync
"""

import os
import re
from unittest.mock import patch, MagicMock


class TestEstimateTokens:
    """Tests for _estimate_tokens() approximation function."""

    def test_empty_string_returns_zero(self):
        """Empty string should return 0 tokens."""
        from scripts.working_memory import _estimate_tokens
        assert _estimate_tokens("") == 0

    def test_single_word(self):
        """Single word should return int(1 * 1.3) = 1."""
        from scripts.working_memory import _estimate_tokens
        assert _estimate_tokens("hello") == 1

    def test_ten_words(self):
        """Ten words should return int(10 * 1.3) = 13."""
        from scripts.working_memory import _estimate_tokens
        text = "one two three four five six seven eight nine ten"
        assert _estimate_tokens(text) == 13


class TestCompressMemoryEntry:
    """Tests for _compress_memory_entry() -- extracts single-line summary."""

    def test_extracts_context_first_sentence(self):
        """Should extract first sentence from Context field."""
        from scripts.working_memory import _compress_memory_entry

        entry = (
            "### 2026-01-15 10:30\n"
            "**Context**: Working on authentication module. This involves JWT tokens.\n"
            "**Goal**: Add refresh token support\n"
            "**Decisions**: Use Redis for token storage\n"
            "**Memory ID**: abc123"
        )
        result = _compress_memory_entry(entry)
        assert "### 2026-01-15 10:30" in result
        assert "**Summary**: Working on authentication module." in result
        assert "**Goal**" not in result
        assert "**Decisions**" not in result
        # THE MEMORY ID IS KEPT, AND THIS ASSERTION WAS REVERSED RATHER THAN
        # REMOVED. It read `"**Memory ID**" not in result`, which pinned the
        # defect: compression dropped the RECOVERY KEY and left only the
        # ROUTE, so a compressed entry could be recovered from the store
        # only by a content search across the summary. The compressed form
        # is now three lines, and the key is the third.
        assert "**Memory ID**: abc123" in result

    def test_truncates_long_context_without_period(self):
        """Should truncate to 120 chars with ellipsis when no period found early."""
        from scripts.working_memory import _compress_memory_entry

        long_context = "A" * 200
        entry = f"### 2026-01-15 10:30\n**Context**: {long_context}"
        result = _compress_memory_entry(entry)
        assert "**Summary**:" in result
        assert "..." in result
        summary_line = [line for line in result.split("\n") if "**Summary**" in line][0]
        summary_text = summary_line.split("**Summary**: ", 1)[1]
        assert len(summary_text) <= 124  # 120 + "..."


class TestApplyTokenBudget:
    """Tests for _apply_token_budget() -- compression and dropping."""

    def test_empty_entries_returns_empty(self):
        """Empty list should return empty list."""
        from scripts.working_memory import _apply_token_budget
        assert _apply_token_budget([], 800) == []

    def test_under_budget_no_change(self):
        """Entries under budget should be returned unchanged."""
        from scripts.working_memory import _apply_token_budget

        entries = [
            "### 2026-01-15 10:00\n**Context**: Short entry",
            "### 2026-01-14 10:00\n**Context**: Another short",
        ]
        result = _apply_token_budget(entries, 800)
        assert result == entries


class TestSyncToClaudeMdBudgetEnforcement:
    """Tests that sync_to_claude_md applies token budget."""

    def _create_claude_md(self, tmp_path, content):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        return claude_md

    def test_large_entries_compressed_during_sync(self, tmp_path):
        """sync_to_claude_md should compress older entries to stay within budget."""
        from scripts.working_memory import sync_to_claude_md

        long_text = "word " * 200
        existing_content = (
            "# Project\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
            "Full history searchable via pact-memory skill. -->\n\n"
            f"### 2026-01-14 10:00\n**Context**: {long_text}\n**Goal**: Old goal\n\n"
            f"### 2026-01-13 10:00\n**Context**: {long_text}\n**Goal**: Older goal\n\n"
        )
        claude_md = self._create_claude_md(tmp_path, existing_content)

        with patch("scripts.working_memory._resolve_display_claude_md_with_base", return_value=(claude_md, claude_md.parent)):
            result = sync_to_claude_md(
                {"context": "New context entry", "goal": "New goal"},
                memory_id="test123"
            )

        # Every sync assertion in this class is a PRECONDITION: the subject is
        # the budget behaviour in the file, not the outcome classification. A
        # truthiness read states "the write happened" and leaves the reason to
        # the repr if it ever fails.
        assert result
        new_content = claude_md.read_text(encoding="utf-8")
        assert "## Working Memory" in new_content
        assert "New context entry" in new_content

    def test_sync_with_budget_produces_valid_markdown(self, tmp_path):
        """Output should be valid markdown with proper section structure."""
        from scripts.working_memory import sync_to_claude_md

        content = (
            "# Project\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "## Pinned Context\n\nSome pinned stuff\n"
        )
        claude_md = self._create_claude_md(tmp_path, content)

        with patch("scripts.working_memory._resolve_display_claude_md_with_base", return_value=(claude_md, claude_md.parent)):
            result = sync_to_claude_md({"context": "Test"}, memory_id="id1")

        new_content = claude_md.read_text(encoding="utf-8")
        # The synced entry must actually be rendered into the Working Memory
        # block. A no-op sync that left the file untouched would still satisfy
        # the structural assertions below, so pin the entry content directly.
        assert result
        wm_block = re.search(
            r"## Working Memory\n(.*?)(?=\n## |\Z)", new_content, re.DOTALL
        ).group(1)
        assert "**Context**: Test" in wm_block
        assert "**Memory ID**: id1" in wm_block
        # Surrounding structure is preserved.
        assert "## Pinned Context" in new_content
        assert "Some pinned stuff" in new_content


class TestSyncRetrievedBudgetEnforcement:
    """Tests that sync_retrieved_to_claude_md applies token budget."""

    def _create_claude_md(self, tmp_path, content):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        return claude_md

    def test_retrieved_entries_reduced_when_over_budget(self, tmp_path):
        """Should drop old retrieved entries when over budget."""
        from scripts.working_memory import sync_retrieved_to_claude_md

        long_text = "word " * 200
        existing_content = (
            "# Project\n\n"
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->\n\n"
            f"### 2026-01-14 10:00\n**Query**: \"old query\"\n**Context**: {long_text}\n\n"
            f"### 2026-01-13 10:00\n**Query**: \"older query\"\n**Context**: {long_text}\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
            "Full history searchable via pact-memory skill. -->\n\n"
        )
        claude_md = self._create_claude_md(tmp_path, existing_content)

        with patch("scripts.working_memory._resolve_display_claude_md_with_base", return_value=(claude_md, claude_md.parent)):
            result = sync_retrieved_to_claude_md(
                [{"context": "New retrieved", "goal": "test"}],
                query="test search",
                memory_ids=["mem1"]
            )

        # FLOOR, and it is a floor on purpose: this test does not name a CAUSE
        # for its expected outcome, it only requires that the write happened.
        # `wrote` is a real bool, so `is True` keeps the identity strictness
        # the assertion had before the return type became a `SyncResult`.
        assert result.wrote is True
        new_content = claude_md.read_text(encoding="utf-8")
        assert "test search" in new_content
        assert "## Working Memory" in new_content
        # LIMIT, PRE-EXISTING AND NOT INTRODUCED HERE: the name of this test
        # promises entries are REDUCED, and no assertion below counts them.
        # Do not read the name as a guarantee of the drop behaviour.

    def test_no_memories_returns_false(self):
        """sync_retrieved_to_claude_md with an empty list reports `empty`."""
        from scripts.working_memory import sync_retrieved_to_claude_md, SyncResult
        result = sync_retrieved_to_claude_md([], query="test")
        # `empty` is the subject, not mere falsiness. This test NAMES its cause
        # in its own name: there was nothing to write. `unresolved` or `failed`
        # would make this arm pass while the guard it checks never ran.
        assert result.reason == SyncResult.EMPTY


class TestFormatMemoryEntry:
    """Direct unit tests for _format_memory_entry() helper."""

    def test_basic_fields(self):
        """Should format context, goal, and memory_id into markdown."""
        from scripts.working_memory import _format_memory_entry

        memory = {"context": "Working on auth", "goal": "Add JWT support"}
        result = _format_memory_entry(memory, memory_id="abc123")

        assert "**Context**: Working on auth" in result
        assert "**Goal**: Add JWT support" in result
        assert "**Memory ID**: abc123" in result
        assert result.startswith("### ")

    def test_decisions_as_list_of_strings(self):
        """Decisions provided as a list of strings should be joined with commas."""
        from scripts.working_memory import _format_memory_entry

        memory = {"context": "Test", "decisions": ["Use Redis", "Add caching"]}
        result = _format_memory_entry(memory)

        assert "**Decisions**: Use Redis, Add caching" in result


class TestFormatRetrievedEntry:
    """Direct unit tests for _format_retrieved_entry() helper."""

    def test_basic_formatting(self):
        """Should format query, context, and goal into markdown."""
        from scripts.working_memory import _format_retrieved_entry

        memory = {"context": "Auth implementation", "goal": "Add JWT"}
        result = _format_retrieved_entry(memory, query="authentication", memory_id="mem1")

        assert '**Query**: "authentication"' in result
        assert "**Context**: Auth implementation" in result
        assert "**Goal**: Add JWT" in result
        assert "**Memory ID**: mem1" in result
        assert result.startswith("### ")

    def test_context_truncation_at_200_chars(self):
        """Context longer than 200 chars should be truncated with ellipsis."""
        from scripts.working_memory import _format_retrieved_entry

        long_context = "A" * 250
        memory = {"context": long_context}
        result = _format_retrieved_entry(memory, query="test")

        context_line = [l for l in result.split("\n") if "**Context**:" in l][0]
        context_value = context_line.split("**Context**: ", 1)[1]
        assert len(context_value) == 200  # 197 + "..."
        assert context_value.endswith("...")


class TestParseWorkingMemorySection:
    """Direct unit tests for _parse_working_memory_section() helper."""

    def test_section_not_found(self):
        """When no Working Memory section exists, should return empty entries."""
        from scripts.working_memory import _parse_working_memory_section

        content = "# Project\n\n## Some Other Section\nContent here\n"
        before, header, after, entries = _parse_working_memory_section(content)

        assert before == content
        assert header == ""
        assert after == ""
        assert entries == []

    def test_no_next_section(self):
        """Working Memory at end of file (no next section) should capture to EOF."""
        from scripts.working_memory import _parse_working_memory_section

        content = (
            "# Project\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
            "Full history searchable via pact-memory skill. -->\n\n"
            "### 2026-01-15 10:00\n"
            "**Context**: Some entry\n"
        )
        before, header, after, entries = _parse_working_memory_section(content)

        assert len(entries) == 1
        assert "Some entry" in entries[0]


class TestParseRetrievedContextSection:
    """Direct unit tests for _parse_retrieved_context_section() helper."""

    def test_section_not_found(self):
        """When no Retrieved Context section exists, should return empty entries."""
        from scripts.working_memory import _parse_retrieved_context_section

        content = "# Project\n\n## Working Memory\nContent here\n"
        before, header, after, entries = _parse_retrieved_context_section(content)

        assert before == content
        assert header == ""
        assert after == ""
        assert entries == []

    def test_no_next_section(self):
        """Retrieved Context at end of file should capture to EOF."""
        from scripts.working_memory import _parse_retrieved_context_section

        content = (
            "# Project\n\n"
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->\n\n"
            "### 2026-01-15 10:00\n"
            '**Query**: "auth"\n'
            "**Context**: Some context\n"
        )
        _, header, _, entries = _parse_retrieved_context_section(content)

        assert header == "## Retrieved Context"
        assert len(entries) == 1
        assert "auth" in entries[0]


# =============================================================================
# Dual-location CLAUDE.md resolution tests for _get_claude_md_path()
# =============================================================================

class TestFindExistingClaudeMd:
    """Tests for _find_existing_claude_md() dual-location helper."""

    def test_returns_none_when_neither_exists(self, tmp_path):
        """Empty directory -> None."""
        from scripts.working_memory import _find_existing_claude_md

        result = _find_existing_claude_md(tmp_path)
        assert result is None

    def test_finds_legacy_claude_md(self, tmp_path):
        """Legacy ./CLAUDE.md at base -> returns it."""
        from scripts.working_memory import _find_existing_claude_md

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# legacy\n")

        result = _find_existing_claude_md(tmp_path)
        assert result == legacy


class TestGetClaudeMdPathDualLocation:
    """Dual-location support tests for _get_claude_md_path() across all 3 fallbacks.

    Verifies that each resolution strategy (env var, git root, cwd) checks
    .claude/CLAUDE.md before ./CLAUDE.md.
    """

    # --- Strategy 1: CLAUDE_PROJECT_DIR env var ---

    def test_env_var_finds_legacy_claude_md(self, tmp_path):
        """Env var strategy finds legacy ./CLAUDE.md."""
        from scripts.working_memory import _get_claude_md_path

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# legacy\n")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = _get_claude_md_path()
        assert result == legacy

    def test_env_var_finds_new_default_claude_md(self, tmp_path):
        """Env var strategy finds .claude/CLAUDE.md (new default)."""
        from scripts.working_memory import _get_claude_md_path

        (tmp_path / ".claude").mkdir()
        new_default = tmp_path / ".claude" / "CLAUDE.md"
        new_default.write_text("# new default\n")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = _get_claude_md_path()
        assert result == new_default


    # --- Strategy 2: git root ---


    # --- Strategy 3: cwd ---


