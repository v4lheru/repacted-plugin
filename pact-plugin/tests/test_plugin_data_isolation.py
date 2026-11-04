"""
Location: pact-plugin/tests/test_plugin_data_isolation.py
Summary: Pins conftest's `_isolate_plugin_data_to_tmp`. Every test runs with its own CLAUDE_PLUGIN_DATA,
         so nothing a test starts (in-process, or a child that inherits the environment) can write the
         machine-local project registry the real atlas reads. The teardown guard watches the exact path
         a child writes when it inherits no CLAUDE_PLUGIN_DATA.
Used by: the plugin suite. The fixture is in tests/conftest.py; the paths and the snapshot are in
         tests/fixtures/plugin_data.py.

Why: P3's early test runs left 12 pytest temp paths in the live
~/.claude/plugins/data/repacted-repacted/projects.json. The CLI helper then set a plugin data dir
per call, but any test that started `repact-task init` without it would have leaked again. This
isolation is suite-wide, not per helper.
"""
from __future__ import annotations

import json
import os
import site
import subprocess
import sys
from pathlib import Path

from fixtures import plugin_data
from project_tasks_support import CLI, GIT_ENV, PLUGIN_ROOT, kb_profile, new_repo


def test_every_test_gets_its_own_plugin_data_dir(tmp_path):
    assert os.environ.get("CLAUDE_PLUGIN_DATA") == str(tmp_path / "plugin-data")


def _registry(path, *checkouts, last_active="2026-10-02T20:46:01Z"):
    path.write_text(json.dumps({"version": 1, "checkouts": {
        c: {"repo_path": c, "last_known": {"last_active": last_active}} for c in checkouts}}))


_CHILD_TESTS = '''
import json, os
from fixtures import plugin_data

REG = plugin_data.LIVE_REGISTRIES[0]


def _add(checkout):
    assert str(REG).startswith(os.environ["HOME"]), REG      # the scratch HOME, never the real one
    data = json.loads(REG.read_text()) if REG.exists() else {"version": 1, "checkouts": {}}
    data["checkouts"][checkout] = {"repo_path": checkout}
    REG.parent.mkdir(parents=True, exist_ok=True)
    REG.write_text(json.dumps(data))


def test_another_session_writes_mid_test():
    _add("/Users/someone/elsewhere repo")


def test_a_leak_into_the_live_registry(tmp_path):
    _add(str(tmp_path / "repo"))
'''


