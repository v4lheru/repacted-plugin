"""bin/_agentlinks.py: the agent and protocols links the launcher switches around a session.

Temp roots only. The end-to-end behaviour through bin/repact is in test_repact_launcher.py.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
_spec = importlib.util.spec_from_file_location("_agentlinks", BIN_DIR / "_agentlinks.py")
_agentlinks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_agentlinks)  # by path: path setup is conftest-owned (test_path_setup_pin)
_memdir = sys.modules["_memdir"]      # the module _agentlinks imported: one Refusal class


def _product(root: Path, names=("pact-a.md", "pact-b.md")) -> Path:
    (root / "agents").mkdir(parents=True)
    (root / "protocols").mkdir()
    for name in names:
        (root / "agents" / name).write_text(f"{root.name} body\n", encoding="utf-8")
    (root / "agents" / "not-an-agent.md").write_text("x\n", encoding="utf-8")
    return root


def _registry(config: Path, plugins) -> Path:
    path = config / "plugins" / "installed_plugins.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plugins if isinstance(plugins, str) else json.dumps({"version": 2, "plugins": plugins}),
                    encoding="utf-8")
    return path


@pytest.fixture
def roots(tmp_path):
    return {"config": tmp_path / "config", "home": tmp_path / "home",
            "pact": _product(tmp_path / "pact"), "repacted": _product(tmp_path / "repacted")}


# ---------- PACT's root comes from installed_plugins.json, never a guess ----------

def test_pact_root_is_the_user_scope_install_path(roots):
    other = _product(roots["config"].parent / "project-pact")
    _registry(roots["config"], {"PACT@pact-marketplace": [
        {"scope": "project", "installPath": str(other)},
        {"scope": "user", "installPath": str(roots["pact"])}]})
    assert _agentlinks.pact_install_root(roots["config"]) == roots["pact"]


def test_the_preferred_key_wins_over_another_pact_key(roots):
    _registry(roots["config"], {"PACT@pact-marketplace": [{"scope": "user", "installPath": str(roots["pact"])}],
                                "PACT@fork": [{"scope": "user", "installPath": "/nowhere"}]})
    assert _agentlinks.pact_install_root(roots["config"]) == roots["pact"]


@pytest.mark.parametrize("plugins, reason", [
    (None, "cannot read"),], ids=["missing",])
def test_an_uncertain_registry_refuses(roots, plugins, reason):
    if plugins is not None:
        _registry(roots["config"], plugins)
    with pytest.raises(_memdir.Refusal, match=reason):
        _agentlinks.pact_install_root(roots["config"])


# ---------- the link set mirrors symlinks.py ----------

def test_the_pairs_are_the_agent_files_plus_both_protocols_links(roots):
    pairs = _agentlinks.link_pairs(roots["config"], roots["home"], roots["pact"])
    assert [link for link, _ in pairs] == [
        roots["config"] / "agents" / "pact-a.md", roots["config"] / "agents" / "pact-b.md",
        roots["home"] / ".claude" / "protocols" / "pact-plugin",
        roots["config"] / "protocols" / "pact-plugin"]
    assert [target for _, target in pairs][-1] == roots["pact"] / "protocols"


# ---------- point_links ----------


# ---------- ownership ----------

