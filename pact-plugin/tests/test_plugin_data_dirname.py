"""
Location: pact-plugin/tests/test_plugin_data_dirname.py
Summary: Pins shared.plugin_paths.data_dirname, the data dir name used when CLAUDE_PLUGIN_DATA is
         unset (a Bash call, a script): an installed copy takes <plugin>-<marketplace> from its cache
         path, so a private install and a public one (marketplace `repacted`) each find their own
         dir; any other root keeps PLUGIN_DATA_DIRNAME.
Used by: the plugin suite.
"""
from __future__ import annotations

from pathlib import Path

from shared import plugin_paths


def test_an_installed_copy_takes_its_name_from_the_cache_path(tmp_path, monkeypatch):
    cache = tmp_path / "cfg" / "plugins" / "cache"
    private = cache / "repacted-local" / "repacted" / "1.7.0"
    public = cache / "repacted" / "repacted" / "1.8.0"
    assert plugin_paths.data_dirname(private) == plugin_paths.PLUGIN_DATA_DIRNAME  # owner: unchanged
    assert plugin_paths.data_dirname(public) == "repacted-repacted"

    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(plugin_paths, "plugin_root", lambda: public)
    assert plugin_paths.plugin_data_dir(create=False) == \
        tmp_path / "cfg" / "plugins" / "data" / "repacted-repacted"


def test_a_root_outside_the_cache_keeps_the_fixed_name():
    for root in (Path("/work/rePACT/repacted/pact-plugin"), Path("/plugins/cache"), Path("/")):
        assert plugin_paths.data_dirname(root) == plugin_paths.PLUGIN_DATA_DIRNAME
    assert plugin_paths.data_dirname() == plugin_paths.PLUGIN_DATA_DIRNAME  # this checkout
