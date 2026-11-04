"""
Tests for shared/claude_md_manager.py -- CLAUDE.md file manipulation.

Tests cover:

ensure_project_memory_md() — project CLAUDE.md creation:
1. Returns None when CLAUDE_PROJECT_DIR not set
2. Returns None when project CLAUDE.md already exists (legacy ./CLAUDE.md)
3. Creates project CLAUDE.md (.claude/CLAUDE.md, new default) with memory sections
4. Created file contains session markers
5. Returns None when .claude/CLAUDE.md already exists (no overwrite)
6. Returns None when only legacy ./CLAUDE.md exists (no migration)
7. .claude/CLAUDE.md takes precedence when both locations exist
8. Created .claude/CLAUDE.md has 0o600 permissions; .claude/ dir 0o700

migrate_to_managed_structure() / _build_migrated_content() — legacy
project CLAUDE.md migration to the PACT_MANAGED boundary structure:
9. Wraps user content with PACT_MANAGED_START/END outer boundary
10. Wraps memory sections with PACT_MEMORY_START/END inner boundary
11. Preserves SESSION_START/END block
12. Strips legacy stale-orchestrator-loader template lines
13. Idempotent on already-migrated input
14. Symlink guard: refuses to operate on symlinks
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Marker literals \u2014 module-level constants used as fixture inputs
# ---------------------------------------------------------------------------
# These mirror the boundary markers used in the on-disk PACT_MANAGED format
# and are referenced by `_build_migrated_content` test fixtures. The routing
# markers are kept here as input data only \u2014 `_build_migrated_content` no
# longer special-cases them, so any input containing them is passed through
# as user content within the PACT_MEMORY region.

_ROUTING_START = "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
_ROUTING_END = "<!-- PACT_ROUTING_END -->"
_MANAGED_START = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
_MANAGED_END = "<!-- PACT_MANAGED_END -->"
_MEMORY_START = "<!-- PACT_MEMORY_START -->"
_MEMORY_END = "<!-- PACT_MEMORY_END -->"
_SESSION_START = "<!-- SESSION_START -->"
_SESSION_END = "<!-- SESSION_END -->"


# ---------------------------------------------------------------------------
# Shared fixture: mock Path.home() so tests never touch real ~/.claude
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_home(tmp_path, monkeypatch):
    """Patch Path.home() to return a tempdir-backed ~/.claude.

    Required for any test that exercises strip_orphan_kernel_block() or
    other functions that read/write under Path.home() / ".claude".
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


class TestEnsureProjectMemoryMd:
    """Tests for ensure_project_memory_md() -- project CLAUDE.md creation."""

    def test_returns_none_when_no_project_dir(self, monkeypatch):
        """Should return None when CLAUDE_PROJECT_DIR not set."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        result = ensure_project_memory_md()

        assert result is None

    def test_returns_none_when_file_exists(self, tmp_path, monkeypatch):
        """Should return None when project CLAUDE.md already exists."""
        from shared.claude_md_manager import ensure_project_memory_md

        (tmp_path / "CLAUDE.md").write_text("existing content")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result is None
        # Content should be unchanged
        assert (tmp_path / "CLAUDE.md").read_text() == "existing content"


class TestEnsureProjectMemoryMdErrorPaths:
    """Tests for ensure_project_memory_md() exception handling."""

    def test_returns_error_message_on_write_failure(self, tmp_path, monkeypatch):
        """Should return truncated error message when write fails."""
        from shared.claude_md_manager import ensure_project_memory_md

        # A WRITABLE project dir: the failure is injected, not environmental,
        # so the test does not depend on chmod semantics (root, ACLs).
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        # Patch the write helper, not Path.write_text -- the code writes via
        # _atomic_write_text (temp + rename), so patching write_text would
        # no-op silently and the test would pass without exercising anything.
        from unittest.mock import patch

        import shared.claude_md_manager as cmm
        with patch.object(cmm, "_atomic_write_text", side_effect=OSError("No space left")):
            result = ensure_project_memory_md()

        assert result is not None
        assert "Project CLAUDE.md failed:" in result

    def test_lock_timeout_returns_skip_message(self, tmp_path, monkeypatch):
        """C: when file_lock raises TimeoutError, ensure_project_memory_md
        must return the human-readable skip message and NOT create the file.

        Coverage gap closed: ensure_project_memory_md must fail-open on
        a stuck lock (concurrent session_init hooks) so session start
        skips the project CLAUDE.md creation gracefully instead of
        crashing.

        We monkeypatch the file_lock symbol on the claude_md_manager module
        directly to raise TimeoutError on entry — simpler than spinning up a
        threaded lock holder and isolates this test from the lock
        infrastructure's own contention semantics.
        """
        from contextlib import contextmanager
        from shared import claude_md_manager as cmm

        # Fresh empty project dir so the resolver returns "new_default" and
        # ensure_project_memory_md proceeds to the file_lock branch.
        project_dir = tmp_path / "fresh_project"
        project_dir.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        # Stub file_lock to raise TimeoutError on entry, mirroring how the
        # real implementation behaves when _LOCK_TIMEOUT_SECONDS elapses
        # without acquiring the sidecar lock.
        @contextmanager
        def timing_out_lock(_path):
            raise TimeoutError(
                "Failed to acquire lock on .CLAUDE.md.lock within 5s"
            )
            yield  # pragma: no cover  -- unreachable, contextmanager requires it

        monkeypatch.setattr(cmm, "file_lock", timing_out_lock)

        result = cmm.ensure_project_memory_md()

        # Result must be the human-readable skip message routed to systemMessage.
        assert result is not None
        assert "Failed to acquire lock" in result
        assert "5s" in result
        assert "skipped" in result
        assert "next session start" in result

        # The CLAUDE.md file must NOT have been created — the timeout aborts
        # the write before any filesystem mutation.
        assert not (project_dir / ".claude" / "CLAUDE.md").exists()
        assert not (project_dir / "CLAUDE.md").exists()


class TestResolveProjectClaudeMdPath:
    """Direct tests for resolve_project_claude_md_path() helper.

    The resolver returns (path, source) where source is one of:
      - "dot_claude": existing .claude/CLAUDE.md
      - "legacy": existing ./CLAUDE.md
      - "new_default": neither exists; path points to .claude/CLAUDE.md
    """

    def test_returns_dot_claude_when_only_dot_claude_exists(self, tmp_path):
        """Returns .claude/CLAUDE.md path with 'dot_claude' source."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        dot_claude = tmp_path / ".claude" / "CLAUDE.md"
        dot_claude.parent.mkdir()
        dot_claude.write_text("# dot-claude")

        path, source = resolve_project_claude_md_path(tmp_path)

        assert path == dot_claude
        assert source == "dot_claude"

    def test_returns_legacy_when_only_legacy_exists(self, tmp_path):
        """Returns ./CLAUDE.md path with 'legacy' source."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# legacy")

        path, source = resolve_project_claude_md_path(tmp_path)

        assert path == legacy
        assert source == "legacy"


class TestEnsureDotClaudeParent:
    """Tests for ensure_dot_claude_parent() helper."""

    def test_creates_dot_claude_dir_with_secure_mode(self, tmp_path):
        """Creates the parent directory with mode 0o700."""
        import stat
        from shared.claude_md_manager import ensure_dot_claude_parent

        target = tmp_path / ".claude" / "CLAUDE.md"
        assert not target.parent.exists()

        ensure_dot_claude_parent(target)

        assert target.parent.exists()
        mode = stat.S_IMODE(target.parent.stat().st_mode)
        assert mode == 0o700, f"Expected 0o700, got {oct(mode)}"

    def test_no_op_when_parent_exists(self, tmp_path):
        """Does not raise when the parent directory already exists."""
        from shared.claude_md_manager import ensure_dot_claude_parent

        # Pre-create the parent (legacy path -- no .claude/ subdir)
        target = tmp_path / "CLAUDE.md"
        # tmp_path always exists; nothing to create
        ensure_dot_claude_parent(target)  # Should not raise

        assert tmp_path.exists()


def _lock_files(tmp_path):
    """The lock files under the per-test XDG_STATE_HOME (conftest points it at tmp_path/state)."""
    return sorted((tmp_path / "state" / "repacted" / "locks").glob("*.lock"))


class TestFileLockContextManager:
    """file_lock(target_file) acquires/releases an fcntl sidecar lock."""

    def test_sequential_acquisitions_work(self, tmp_path):
        """Acquire, release, re-acquire must succeed without blocking."""
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("content", encoding="utf-8")

        with file_lock(target):
            pass  # Exited cleanly; lock released

        with file_lock(target):
            pass  # Second acquisition must not block or raise

        # Sidecar exists and is not cleaned up (by design)
        assert len(_lock_files(tmp_path)) == 1

    def test_sidecar_lives_outside_the_project_tree(self, tmp_path):
        """F2: the lock goes to the per-user lock dir, never beside the target, so `git add -A`
        in the user's repo cannot stage it; one target maps to one lock file."""
        from shared.claude_md_manager import file_lock

        project = tmp_path / "repo" / ".claude"
        project.mkdir(parents=True)
        target = project / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        with file_lock(target):
            assert sorted(p.name for p in project.iterdir()) == ["CLAUDE.md"]
            assert len(_lock_files(tmp_path)) == 1
        with file_lock(project / ".." / ".claude" / "CLAUDE.md"):
            assert len(_lock_files(tmp_path)) == 1  # same resolved target, same lock


class TestBuildMigratedContentCurrentFormat:
    """_build_migrated_content() with the standard pre-#404 CLAUDE.md layout.

    This is the most common input shape: has the # Project Memory heading,
    routing block, session block, and all three memory sections with real
    content under them.
    """

    CURRENT_FORMAT = (
        "# Project Memory\n"
        "\n"
        "This file contains project-specific memory managed by the PACT framework.\n"
        "\n"
        f"{_ROUTING_START}\n"
        "## PACT Routing\n"
        "\n"
        "Some routing instructions here.\n"
        f"{_ROUTING_END}\n"
        "\n"
        f"{_SESSION_START}\n"
        "## Current Session\n"
        "- Team: pact-abc123\n"
        f"{_SESSION_END}\n"
        "\n"
        "## Retrieved Context\n"
        "Some retrieved context.\n"
        "\n"
        "## Pinned Context\n"
        "\n"
        "### Important pin\n"
        "Pin content here.\n"
        "\n"
        "## Working Memory\n"
        "### 2026-04-12 21:00\n"
        "Some working memory entry.\n"
    )

    def test_output_has_managed_boundary(self):
        """Migrated output must start with PACT_MANAGED_START and contain PACT_MANAGED_END."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert result.startswith(_MANAGED_START)
        assert _MANAGED_END in result

    def test_output_has_memory_boundary(self):
        """Migrated output must contain PACT_MEMORY_START and PACT_MEMORY_END."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert _MEMORY_START in result
        assert _MEMORY_END in result


class TestBuildMigratedContentMissingSections:
    """_build_migrated_content() with various sections absent."""

    def test_no_routing_block(self):
        """File with no routing block should still produce valid structure."""
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "Some context.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _MEMORY_START in result
        assert _MEMORY_END in result
        # No routing markers should appear
        assert _ROUTING_START not in result
        assert "Some context." in result

    def test_no_session_block(self):
        """File with no session block should still produce valid structure."""
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            f"{_ROUTING_START}\n"
            "## PACT Routing\n"
            "Routing content.\n"
            f"{_ROUTING_END}\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _ROUTING_START in result
        assert _SESSION_START not in result


class TestBuildMigratedContentUserContent:
    """_build_migrated_content() must preserve user content outside PACT sections."""

    def test_user_content_after_memory_sections(self):
        """User-owned sections after the last memory section must appear
        after PACT_MANAGED_END.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
            "\n"
            "## My Custom Section\n"
            "User's custom notes here.\n"
        )

        result = _build_migrated_content(content)

        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## My Custom Section" in after_managed
        assert "User's custom notes here." in after_managed

    def test_user_content_between_memory_sections(self):
        """A non-memory heading between memory sections splits into user content."""
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "Some context.\n"
            "\n"
            "## User Notes\n"
            "Private user notes.\n"
            "\n"
            "## Working Memory\n"
            "Working memory data.\n"
        )

        result = _build_migrated_content(content)

        # User notes should be outside managed block
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## User Notes" in after_managed
        assert "Private user notes." in after_managed

        # Memory sections should be inside memory boundary
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "## Retrieved Context" in memory_region
        assert "## Working Memory" in memory_region
        assert "Working memory data." in memory_region


class TestBuildMigratedContentAdversarial:
    """Adversarial and edge-case inputs for _build_migrated_content().

    Tests the MEDIUM uncertainty flagged by the coder: user headings that
    match memory section names could be mis-classified.
    """

    def test_user_heading_matching_memory_name_exact(self):
        """A user heading that exactly matches a memory section name
        (e.g., '## Retrieved Context') is classified as a memory section.

        This is the expected behavior — the classifier uses heading text
        to identify memory sections. Users should not have headings with
        these exact names outside the memory area.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "PACT-managed retrieval data.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "PACT-managed retrieval data." in memory_region

    def test_similar_but_different_heading_not_captured(self):
        """Headings that are similar but not exact matches should NOT be
        classified as memory sections (e.g., '## Retrieved Context (old)').
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context (old)\n"
            "User's old retrieval notes.\n"
            "\n"
            "## Retrieved Context\n"
            "Actual PACT context.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        # The exact-match section should be in memory
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "Actual PACT context." in memory_region

        # The near-match should be user content
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## Retrieved Context (old)" in after_managed
        assert "User's old retrieval notes." in after_managed


class TestBuildMigratedContentIndentedHeadingEntry:
    """The entry axis of the section-boundary test: leading spaces."""

    # The band each row of which the boundary test must cover. ZERO IS THE
    # CONTROL ROW. With the right-stripped boundary test, zero is the one
    # indent that keeps a foreign heading out, so a band that omitted it
    # would report one verdict for all rows and separate nothing.
    _INDENTS = (0, 1, 2, 3, 4, 5)

    def test_indented_foreign_heading_stays_out_of_the_memory_region(self):
        """An indented foreign heading must not enter the managed region.

        The section-boundary test reads the LEFT-stripped line, and the fence
        test beside it in the same loop reads the same form. Change the
        boundary test back to the right-stripped line and leading spaces hide
        the heading from it. The heading then does not close the memory
        section, and the plugin adopts the heading and its body into the
        PACT_MEMORY region of a file that git does not track.

        THE PAYLOAD IS A HEADING THAT THE EXACT-MATCH TEST REJECTS, and that
        choice is load-bearing. A payload that agrees with a memory heading of
        the plugin enters at zero spaces through the legitimate exact-match
        route, so it gives the same answer at each indent and cannot separate
        the two states.

        MEASURED. With the right-stripped boundary test, zero spaces keeps the
        heading out and one through five spaces put it in. With the
        left-stripped test, zero through five all keep it out.

        EACH ROW CARRIES TWO LEGS, and the negative leg alone is not
        sufficient: a document that loses the payload also satisfies it.
        """
        from shared.claude_md_manager import _build_migrated_content

        checked = 0
        for spaces in self._INDENTS:
            content = (
                "# Project Memory\n"
                "\n"
                "## Retrieved Context\n"
                "A memory line.\n"
                "\n"
                f"{' ' * spaces}## Zzforeign Heading\n"
                "ZZFOREIGN-BODY-TOKEN\n"
            )

            result = _build_migrated_content(content)

            memory_region = result[
                result.index(_MEMORY_START):result.index(_MEMORY_END)
            ]
            after_managed = result[result.index(_MANAGED_END):]

            # NEGATIVE LEG FIRST, because it is the property itself. Order
            # matters here: the positive leg below also goes red when the
            # heading enters the memory region, and its message would name
            # the wrong defect.
            assert "Zzforeign Heading" not in memory_region, (
                f"at an indent of {spaces} a foreign heading entered the "
                f"PACT-managed memory region"
            )
            assert "ZZFOREIGN-BODY-TOKEN" not in memory_region, (
                f"at an indent of {spaces} the body below a foreign heading "
                f"entered the PACT-managed memory region"
            )

            # POSITIVE LEG. Without it, a document that dropped the payload
            # satisfies the two assertions above and reads as a pass.
            assert "Zzforeign Heading" in after_managed, (
                f"at an indent of {spaces} the foreign heading is in no "
                f"region of the two, so it left the document"
            )
            assert "ZZFOREIGN-BODY-TOKEN" in after_managed, (
                f"at an indent of {spaces} the body below a foreign heading "
                f"is in no region of the two, so it left the document"
            )
            checked += 1

        # A band that shrinks to nothing satisfies each assertion above and
        # reads as a pass, so gate on the row count.
        assert checked == len(self._INDENTS) == 6, (
            f"the indent band ran {checked} rows, and 6 are necessary"
        )


class TestBuildMigratedContentIdempotent:
    """_build_migrated_content() has its own idempotency guard (round 5 item 2).

    The guard checks for MANAGED_START_MARKER at the top of the function and
    returns the content unchanged if already migrated. The integration wrapper
    migrate_to_managed_structure also has this guard, so the two layers provide
    belt-and-suspenders protection. Duplicating it at the pure-function layer
    means any caller (including tests and future consumers) gets the safety
    for free and double-passes can never double-wrap.
    """

    def test_double_pass_is_idempotent(self):
        """Calling _build_migrated_content twice returns the same content.

        Round 5, item 2: _build_migrated_content now guards on
        MANAGED_START_MARKER presence and returns unchanged content on the
        second call. The prior behavior was to double-wrap; that contract
        was intentional documentation, not a design goal.
        """
        from shared.claude_md_manager import _build_migrated_content

        original = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )
        first_pass = _build_migrated_content(original)

        # Second pass: the guard returns first_pass unchanged
        second_pass = _build_migrated_content(first_pass)

        assert second_pass.count(_MANAGED_START) == 1
        assert second_pass == first_pass

    def test_already_migrated_input_returns_unchanged(self):
        """Passing already-wrapped content returns byte-identical output."""
        from shared.claude_md_manager import _build_migrated_content

        already_managed = (
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            "## Retrieved Context\n"
            "## Pinned Context\n"
            "## Working Memory\n"
            f"{_MEMORY_END}\n"
            f"{_MANAGED_END}\n"
        )

        result = _build_migrated_content(already_managed)

        assert result == already_managed


class TestMigrateToManagedStructure:
    """Integration tests for migrate_to_managed_structure() — the wrapper
    that does file I/O around _build_migrated_content().
    """

    def test_migrates_existing_file(self, tmp_path, monkeypatch):
        """migrate_to_managed_structure() rewrites an old-format file."""
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = migrate_to_managed_structure()

        assert result is not None
        assert "Migrated" in result
        content = claude_md.read_text(encoding="utf-8")
        assert _MANAGED_START in content
        assert _MANAGED_END in content
        assert _MEMORY_START in content
        assert _MEMORY_END in content

    def test_idempotent_noop_when_already_migrated(self, tmp_path, monkeypatch):
        """Second call returns None (no-op) when PACT_MANAGED_START is present."""
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        # First call migrates
        first = migrate_to_managed_structure()
        assert first is not None

        content_after_first = claude_md.read_text(encoding="utf-8")

        # Second call is no-op
        second = migrate_to_managed_structure()
        assert second is None

        # File unchanged
        assert claude_md.read_text(encoding="utf-8") == content_after_first


class TestEnsureProjectMemoryMdNewMarkers:
    """Verify that ensure_project_memory_md() template includes #404 markers."""

    def test_created_file_has_managed_boundary(self, tmp_path, monkeypatch):
        """Newly created project CLAUDE.md must have PACT_MANAGED markers."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        assert _MANAGED_START in content
        assert _MANAGED_END in content


class TestManagedMarkerConstants:
    """Verify the new marker constants match expected values.

    Same pattern as TestPactRoutingBlock — pin the exact values here
    so accidental drift in the implementation is caught.
    """

    def test_managed_start_marker_value(self):
        from shared.claude_md_manager import MANAGED_START_MARKER
        assert MANAGED_START_MARKER == _MANAGED_START


class TestSessionInitMigrationIntegration:
    """Verify that session_init.py calls migrate_to_managed_structure()
    and routes its return value correctly.
    """

    def test_session_init_calls_migration(self):
        """session_init.py must contain a call to migrate_to_managed_structure."""
        # Steps 1-4c moved to the common frame module (A4b).
        session_init_path = (
            Path(__file__).parent.parent / "hooks" / "shared" / "session_init_common.py"
        )
        source = session_init_path.read_text(encoding="utf-8")
        assert "migrate_to_managed_structure()" in source


class TestStripLegacyLines:
    """Direct unit tests for `_strip_legacy_lines`.

    Direct coverage so a regression in the legacy-stripping logic
    produces a targeted failure rather than masked signal from the
    indirect call site in `_build_migrated_content` (the one-time
    PACT_MANAGED migration).
    """

    # The exact stale line the v3.16.2 template carried. Pinned here as a
    # fixture constant so drift in _STALE_ORCHESTRATOR_LINE_RE is caught.
    STALE_LINE = (
        "The global PACT Orchestrator is loaded from `~/.claude/CLAUDE.md`."
    )

    def test_strips_exact_stale_line_with_trailing_period(self):
        """Canonical form: stale line with trailing period and newline."""
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            f"{self.STALE_LINE}\n"
            "\n"
            "## Retrieved Context\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE not in result
        # Surrounding content survives
        assert "# Project Memory" in result
        assert "## Retrieved Context" in result


    # Round 8 item 5: direct fence unit tests for `_strip_legacy_lines`.
    # Prior coverage exercised the walker's fence branches through
    # `_build_migrated_content` and `test_fenced_stale_orchestrator_line_preserved`
    # (an end-to-end driver). Direct unit tests produce targeted failures when
    # the walker's fence-state tracking regresses, without the downstream
    # migration-pipeline assertions masking the signal.


class TestEnsureProjectMemoryMdOSErrorOnMkdir:
    """PR #404 round 12 item 6: OSError during .claude/ directory creation
    in ``ensure_project_memory_md``.

    Covers lines 823-824: the ``except OSError`` branch where
    ``target_dir.mkdir`` fails (e.g., read-only filesystem).
    """

    def test_oserror_during_mkdir_returns_failure_message(self, tmp_path, monkeypatch):
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Make the .claude directory creation fail
        dot_claude = tmp_path / ".claude"
        # Create a file at .claude to block mkdir
        dot_claude.write_text("blocker")

        result = ensure_project_memory_md()

        assert result is not None
        assert "failed" in result.lower() or "skipped" in result.lower()


class TestExtractManagedRegion:
    """Round 10: tests for extract_managed_region helper."""

    def test_returns_region_and_offset(self):
        """When both markers are present, returns (region_text, offset)."""
        from shared.claude_md_manager import (
            MANAGED_START_MARKER,
            MANAGED_END_MARKER,
            extract_managed_region,
        )

        content = (
            "user preamble\n"
            f"{MANAGED_START_MARKER}\n"
            "managed content here\n"
            f"{MANAGED_END_MARKER}\n"
            "user epilogue\n"
        )

        result = extract_managed_region(content)
        assert result is not None
        region_text, offset = result
        assert "managed content here" in region_text
        assert "user preamble" not in region_text
        assert "user epilogue" not in region_text
        # offset should point to just after MANAGED_START_MARKER
        assert content[offset:].startswith("\nmanaged")


class _StripOrphanBlockTestBase:
    """Shared mixin for SUNSET-BEFORE-v5.0.0 orphan-block strippers.

    Subclasses configure:
      - START_MARKER, END_MARKER (the marker pair the stripper hunts)
      - target_file fixture (Path to the file the stripper operates on)
      - call_stripper(self) -> str | None (invokes the stripper)

    Each subclass exercises the same behavior matrix:
      - both markers present + properly ordered → strip + return success status
      - neither marker present → no-op (None)
      - only START present → defensive no-op + skip status
      - only END present → defensive no-op + skip status
      - END before START → defensive no-op + skip status
      - symlink → defensive no-op + skip status
      - read OSError → fail-open (None)

    Concrete classes follow at module bottom: TestStripOrphanKernelBlock
    (home-dir kernel block) and TestStripOrphanRoutingMarkers
    (project-dir routing block).
    """

    START_MARKER: str
    END_MARKER: str

    def _write_target(self, target_file: Path, content: str) -> None:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")

    def call_stripper(self) -> str | None:
        raise NotImplementedError

    def get_target_file(self) -> Path:
        raise NotImplementedError


class TestStripOrphanKernelBlock(_StripOrphanBlockTestBase):
    """SUNSET-BEFORE-v5.0.0 migration helper: strips the obsolete v3.x
    PACT_START/PACT_END block from ~/.claude/CLAUDE.md.

    Uses the _StripOrphanBlockTestBase mixin to share the behavior matrix
    with TestStripOrphanRoutingMarkers (project-dir variant).
    """

    START_MARKER = "<!-- PACT_START:v3.16 -->"
    END_MARKER = "<!-- PACT_END -->"

    @pytest.fixture(autouse=True)
    def _setup(self, mock_home):
        self.target_file = mock_home / ".claude" / "CLAUDE.md"

    def call_stripper(self) -> str | None:
        from shared.claude_md_manager import strip_orphan_kernel_block
        return strip_orphan_kernel_block()

    def test_proper_pair_strips_block_and_returns_success(self):
        self._write_target(
            self.target_file,
            f"# user content\n{self.START_MARKER}\nstale prose\n{self.END_MARKER}\nmore user content\n",
        )
        result = self.call_stripper()
        assert result is not None
        assert "Removed obsolete PACT kernel block" in result
        new_content = self.target_file.read_text(encoding="utf-8")
        assert self.START_MARKER not in new_content
        assert self.END_MARKER not in new_content
        assert "user content" in new_content
        assert "more user content" in new_content
        assert "stale prose" not in new_content


