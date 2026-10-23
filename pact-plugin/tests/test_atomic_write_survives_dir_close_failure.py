"""
Location: pact-plugin/tests/test_atomic_write_survives_dir_close_failure.py
Summary: A CLAUDE.md write that landed is reported as landed (upstream PACT
         #1650): a failure to close the parent directory after the rename no
         longer turns it into a failed write, in both copies of the writer.
Used by: pytest.
"""

import importlib
import os
import stat

import pytest


@pytest.mark.parametrize("module", ["shared.claude_md_manager", "working_memory"])
def test_a_dir_close_failure_after_the_rename_is_not_a_failed_write(tmp_path, monkeypatch, module):
    writer = importlib.import_module(module)
    target = tmp_path / "CLAUDE.md"
    target.write_text("old\n", encoding="utf-8")
    real_close = os.close

    def close(fd):
        is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        real_close(fd)
        if is_dir:
            raise OSError("close failed")

    monkeypatch.setattr(writer.os, "close", close)
    writer._atomic_write_text(target, "new\n", tmp_path)

    assert target.read_text(encoding="utf-8") == "new\n"
