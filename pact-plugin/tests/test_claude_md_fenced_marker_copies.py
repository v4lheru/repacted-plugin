"""
Location: pact-plugin/tests/test_claude_md_fenced_marker_copies.py
Summary: A copy of a PACT marker inside a fenced code block in a CLAUDE.md is
         not the real marker (upstream PACT #1656). One row per reader or
         writer that located a marker by substring: the managed-region readers
         (hook copy and working_memory twin), the Current Session writer, the
         managed-structure migration and the kernel-block strip.
Used by: pytest.
"""

import importlib

import pytest

from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    SESSION_END_MARKER,
    SESSION_START_MARKER,
)

FENCED_EXAMPLE = (
    "# Notes\n"
    "\n"
    "An example of the managed layout:\n"
    "\n"
    "```markdown\n"
    f"{MANAGED_START_MARKER}\n"
    f"{SESSION_START_MARKER}\n"
    "- Resume: `claude --resume example`\n"
    f"{SESSION_END_MARKER}\n"
    f"{MEMORY_START_MARKER}\n"
    "example memory\n"
    f"{MEMORY_END_MARKER}\n"
    f"{MANAGED_END_MARKER}\n"
    "```\n"
    "\n"
)

REAL_BLOCK = (
    f"{MANAGED_START_MARKER}\n"
    "# PACT Framework and Managed Project Memory\n"
    "\n"
    f"{SESSION_START_MARKER}\n"
    "## Current Session\n"
    "- Resume: `claude --resume old`\n"
    f"{SESSION_END_MARKER}\n"
    "\n"
    f"{MEMORY_START_MARKER}\n"
    "## Working Memory\n"
    "real memory\n"
    f"{MEMORY_END_MARKER}\n"
    "\n"
    f"{MANAGED_END_MARKER}\n"
)


@pytest.mark.parametrize("module", ["shared.claude_md_manager", "working_memory"])
def test_managed_region_is_the_real_block_not_the_fenced_copy(module):
    extract = importlib.import_module(module).extract_managed_region
    content = FENCED_EXAMPLE + REAL_BLOCK
    region, start = extract(content)
    assert "real memory" in region and "example memory" not in region
    assert start > content.index("```\n\n")


def test_session_block_write_leaves_the_fenced_example_alone(tmp_path, monkeypatch):
    from shared.session_resume import update_session_info

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir()
    target.write_text(FENCED_EXAMPLE + REAL_BLOCK, encoding="utf-8")

    update_session_info("new-session", "session-new")

    written = target.read_text(encoding="utf-8")
    assert written.startswith(FENCED_EXAMPLE)
    assert "claude --resume new-session" in written[len(FENCED_EXAMPLE):]
    assert "claude --resume old" not in written


def test_migration_wraps_a_file_whose_only_managed_marker_is_fenced(tmp_path, monkeypatch):
    from shared.claude_md_manager import extract_managed_region, migrate_to_managed_structure

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    target = tmp_path / "CLAUDE.md"
    target.write_text(FENCED_EXAMPLE, encoding="utf-8")

    assert migrate_to_managed_structure() is not None

    written = target.read_text(encoding="utf-8")
    assert FENCED_EXAMPLE.strip() in written
    assert extract_managed_region(written) is not None


def test_kernel_strip_leaves_a_fenced_kernel_example_alone(tmp_path, monkeypatch):
    from shared.claude_md_manager import strip_orphan_kernel_block

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    target = tmp_path / "CLAUDE.md"
    content = (
        "# Mine\n"
        "\n"
        "```\n"
        "<!-- PACT_START: v3 -->\n"
        "old kernel example\n"
        "<!-- PACT_END -->\n"
        "```\n"
        "\n"
        "my notes\n"
    )
    target.write_text(content, encoding="utf-8")

    assert strip_orphan_kernel_block() is None
    assert target.read_text(encoding="utf-8") == content
