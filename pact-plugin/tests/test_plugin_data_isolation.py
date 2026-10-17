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


def test_an_init_that_inherits_the_environment_registers_only_in_this_tests_dir(tmp_path, monkeypatch):
    kb_profile(tmp_path, monkeypatch)
    repo = new_repo(tmp_path / "repo")
    before = plugin_data.snapshot(plugin_data.LIVE_REGISTRIES)
    # The child's environment is inherited as it is, with no CLAUDE_PLUGIN_DATA added here:
    # this is exactly the call shape that leaked.
    result = subprocess.run([sys.executable, str(CLI), "init"], cwd=str(repo), capture_output=True,
                            text=True, env={**os.environ, **GIT_ENV})
    assert result.returncode == 0, result.stderr
    registry = json.loads((tmp_path / "plugin-data" / "projects.json").read_text(encoding="utf-8"))
    assert str(repo.resolve()) in registry["checkouts"]
    assert plugin_data.leaked(before, (tmp_path,)) == ()


def test_the_guard_watches_the_registry_a_child_without_plugin_data_would_write():
    """The guard is only as good as its path: ask the real resolver, in a child, where it writes."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_DATA"}
    env["PYTHONPATH"] = str(PLUGIN_ROOT / "bin")     # the child imports the resolver itself
    code = "import _repact_common as c; print(c.plugin_data_dir(create=False))"
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert Path(out) / plugin_data.REGISTRY_FILENAME in plugin_data.LIVE_REGISTRIES


def test_the_snapshot_sees_a_created_a_rewritten_and_a_removed_registry(tmp_path):
    created, rewritten, removed = (tmp_path / name for name in ("a.json", "b.json", "c.json"))
    rewritten.write_text("{}")
    removed.write_text("{}")
    before = plugin_data.snapshot((created, rewritten, removed))
    created.write_text("{}")
    rewritten.write_text('{"checkouts": {}}')
    removed.unlink()
    assert set(plugin_data.changed(before)) == {str(created), str(rewritten), str(removed)}
    assert plugin_data.changed(plugin_data.snapshot((created, rewritten, removed))) == ()


def _registry(path, *checkouts, last_active="2026-10-02T20:46:01Z"):
    path.write_text(json.dumps({"version": 1, "checkouts": {
        c: {"repo_path": c, "last_known": {"last_active": last_active}} for c in checkouts}}))


def test_leaked_reports_only_a_path_from_the_run_tmp_tree(tmp_path):
    """T-1f0a70f2: another live session's write (a real checkout, a fresh timestamp) is not a leak;
    a path under the run's tmp tree is, in any spelling of it."""
    real = tmp_path / "base é"                       # unicode and a space, like a real temp root
    real.mkdir()
    base = tmp_path / "alias"                        # /var vs /private/var on macOS
    base.symlink_to(real)
    reg = tmp_path / "projects.json"
    _registry(reg, "/Users/someone/real repo")
    before = plugin_data.snapshot((reg,))
    _registry(reg, "/Users/someone/real repo", "/Users/someone/other repo", last_active="2026-10-02T21:34:20Z")
    assert plugin_data.changed(before) == (str(reg),) and plugin_data.leaked(before, (base,)) == ()
    for planted in (str(real / "t0" / "repo"), str(base) + "/t1", str(base)):
        _registry(reg, "/Users/someone/real repo", planted)
        assert plugin_data.leaked(before, (base,)) == (str(reg),), planted
    _registry(reg, "/Users/someone/real repo", str(base) + "-sibling/repo")     # a shared prefix only
    assert plugin_data.leaked(before, (base,)) == ()
    reg.write_text('{"checkouts": ')                  # no real writer leaves a torn file
    assert plugin_data.leaked(before, (base,)) == (str(reg),)
    _registry(reg, str(base / "repo"))
    assert plugin_data.leaked(plugin_data.snapshot((reg,)), (base,)) == ()   # already there before


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


def test_an_outside_registry_write_does_not_fail_the_teardown(tmp_path):
    """T-1f0a70f2, end to end: the real conftest guard in a child pytest whose live registry is in a
    scratch HOME. A write a test did not make passes; a write of the test's own tmp path errors."""
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "child").mkdir()
    (tmp_path / "child" / "test_child.py").write_text(_CHILD_TESTS)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PLUGIN_DATA", "CLAUDE_CONFIG_DIR")}
    env.update(HOME=str(home), PYTHONPATH=str(PLUGIN_ROOT / "tests"))
    # The scratch HOME moves the user site, so a pytest installed with `pip install --user` would
    # vanish from this interpreter's child: keep the parent's user base.
    env.setdefault("PYTHONUSERBASE", site.getuserbase())
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "conftest",
         "--rootdir", str(tmp_path / "child"), "--basetemp", str(tmp_path / "child-base"),
         str(tmp_path / "child" / "test_child.py")],
        cwd=str(tmp_path / "child"), env=env, capture_output=True, text=True, timeout=300)
    tail = result.stdout[-2000:]
    assert "2 passed, 1 error" in tail, tail + result.stderr[-2000:]   # the leak's call passes; its teardown errors
    assert "ERROR at teardown of test_a_leak_into_the_live_registry" in result.stdout, tail
