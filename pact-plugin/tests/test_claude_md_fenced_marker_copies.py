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


@pytest.mark.parametrize("module", ["shared.claude_md_manager",])
def test_managed_region_is_the_real_block_not_the_fenced_copy(module):
    extract = importlib.import_module(module).extract_managed_region
    content = FENCED_EXAMPLE + REAL_BLOCK
    region, start = extract(content)
    assert "real memory" in region and "example memory" not in region
    assert start > content.index("```\n\n")


