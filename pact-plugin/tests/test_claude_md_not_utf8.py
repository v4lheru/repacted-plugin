"""
Location: pact-plugin/tests/test_claude_md_not_utf8.py
Summary: A project CLAUDE.md that is not valid UTF-8 neither breaks a session
         start nor gets rewritten (upstream PACT #1650): every writer leaves it
         byte-identical and says so, and the reader returns without raising.
Used by: pytest.
"""

import pytest

from shared.claude_md_manager import migrate_to_managed_structure
from shared.session_init_resume import _extract_prev_session_dir
from shared.session_resume import update_session_info

NOT_UTF8 = b"# My notes\n\nna\xefve caf\xe9\n"


@pytest.mark.parametrize("call", [
    migrate_to_managed_structure,
    lambda: update_session_info("sid", "session-sid"),
], ids=["migration", "session block"])
def test_a_writer_leaves_a_non_utf8_claude_md_byte_identical(tmp_path, monkeypatch, call):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    target = tmp_path / "CLAUDE.md"
    target.write_bytes(NOT_UTF8)

    status = call()

    assert "skipped" in status and "UTF-8" in status
    assert target.read_bytes() == NOT_UTF8
    assert _extract_prev_session_dir(str(tmp_path)) is None
