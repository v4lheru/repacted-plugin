"""
Location: pact-plugin/tests/test_merge_token_dir_isolation.py
Summary: The merge guard's default token directory never points outside the test's tmp root.
Used by: pytest; guards conftest's `_isolate_merge_token_dir_to_tmp` and its holder list.

`TOKEN_DIR` is an import-time constant copied by value into several hooks modules. Left alone
it is the operator's real config directory, so a guard call without `token_dir` read (and could
remove or write) live approvals there, and a real approval given during a run changed test
results. A new holder that conftest does not patch fails here.
"""
import sys
from pathlib import Path

import merge_guard_pre as pre


def test_every_loaded_token_dir_is_this_tests_own(tmp_path):
    expected = tmp_path / ".claude"
    holders = {name: module.TOKEN_DIR for name, module in list(sys.modules.items())
               if isinstance(getattr(module, "TOKEN_DIR", None), Path)}
    assert {"shell.merge.tokens", "merge_guard_pre", "merge_guard_post"} <= set(holders)
    assert {name: path for name, path in holders.items() if path != expected} == {}


def test_a_default_dir_lookup_reads_only_this_tests_directory(tmp_path):
    """A live-looking token file in the test's own directory is what the default lookup
    visits: it is malformed, so the lookup removes it, which proves which directory it read."""
    stray = tmp_path / ".claude" / f"{pre.TOKEN_PREFIX}stray"
    stray.parent.mkdir()
    stray.write_text("not json", encoding="utf-8")
    assert pre.find_valid_tokens() == []
    assert not stray.exists()
