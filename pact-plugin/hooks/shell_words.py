"""
Location: pact-plugin/hooks/shell_words.py
Summary: FAÇADE for one release: the tokenizer lives in hooks/shell/words.py; this module
         re-exports it under the name every guard imports today. Retired next release,
         with the importers re-pointed in the same commit (tests/contract/budgets.json
         `retired`).
Used by: process_table_guard.py, egress_guard.py, egress_uploads.py, docker_lock_gate.py,
         background_test_gate.py, merge_guard_pre.py, git_commit_check.py and the test suite
         (hooks/shared/commit_rules.py and the shell package import shell.words directly).
"""
from __future__ import annotations

from shell.words import *  # noqa: F401,F403  # re-export: façade
