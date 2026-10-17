"""bin/_agentlinks.py: the agent and protocols links the launcher switches around a session.

Temp roots only. The end-to-end behaviour through bin/repact is in test_repact_launcher.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
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
    (None, "cannot read"),
    ("{not json", "cannot read"),
    ({"other@x": []}, "no PACT@"),
    ({"PACT@a": [{"scope": "user", "installPath": "/x"}], "PACT@b": []}, "several PACT@"),
    ({"PACT@pact-marketplace": []}, "0 usable"),
    ({"PACT@pact-marketplace": [{"scope": "user", "installPath": "/x"},
                                {"scope": "user", "installPath": "/y"}]}, "2 usable"),
    ({"PACT@pact-marketplace": [{"scope": "user", "installPath": "/nowhere"}]}, "no agents/"),
], ids=["missing", "malformed", "no-key", "two-keys", "no-entry", "two-user-entries", "empty-path"])
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


def test_one_protocols_link_when_the_config_root_is_home_dot_claude(roots):
    config = roots["home"] / ".claude"
    pairs = _agentlinks.link_pairs(config, roots["home"], roots["pact"])
    assert sum(1 for link, _ in pairs if link.name == "pact-plugin") == 1


# ---------- point_links ----------

def test_links_are_created_then_swapped_and_verified(roots, capsys):
    to_pact = _agentlinks.link_pairs(roots["config"], roots["home"], roots["pact"])
    assert _agentlinks.point_links(to_pact, apply=True, label="pact") == 0
    assert all(os.path.realpath(link) == os.path.realpath(target) for link, target in to_pact)

    to_repacted = _agentlinks.link_pairs(roots["config"], roots["home"], roots["repacted"])
    _agentlinks.point_links(to_repacted, apply=True, label="repacted")
    assert all(os.path.realpath(link) == os.path.realpath(target) for link, target in to_repacted)
    assert not list(roots["config"].rglob("*" + _agentlinks.SWAP_SUFFIX))
    assert "4 to point" in capsys.readouterr().out


def test_a_second_run_changes_nothing(roots, capsys):
    pairs = _agentlinks.link_pairs(roots["config"], roots["home"], roots["pact"])
    _agentlinks.point_links(pairs, apply=True, label="pact")
    capsys.readouterr()
    _agentlinks.point_links(pairs, apply=True, label="pact")
    assert "0 to point, 4 already there" in capsys.readouterr().out


def test_a_real_file_or_dir_is_never_replaced(roots, capsys):
    pairs = _agentlinks.link_pairs(roots["config"], roots["home"], roots["repacted"])
    override = roots["config"] / "agents" / "pact-a.md"
    override.parent.mkdir(parents=True)
    override.write_text("override\n", encoding="utf-8")
    real_dir = roots["home"] / ".claude" / "protocols" / "pact-plugin"
    real_dir.mkdir(parents=True)
    (real_dir / "keep.md").write_text("keep\n", encoding="utf-8")

    assert _agentlinks.point_links(pairs, apply=True, label="repacted") == 2

    assert override.read_text(encoding="utf-8") == "override\n" and not override.is_symlink()
    assert (real_dir / "keep.md").is_file() and not real_dir.is_symlink()
    assert "real file" in capsys.readouterr().err


def test_the_dry_run_writes_nothing(roots):
    pairs = _agentlinks.link_pairs(roots["config"], roots["home"], roots["pact"])
    _agentlinks.point_links(pairs, apply=False, label="pact")
    assert not any(os.path.lexists(link) for link, _ in pairs)


def test_a_link_that_does_not_resolve_after_the_write_fails_loudly(roots, monkeypatch):
    pairs = _agentlinks.link_pairs(roots["config"], roots["home"], roots["pact"])
    real_points_at = _agentlinks._points_at
    calls = {"n": 0}

    def lying(link, target):
        calls["n"] += 1
        return False if calls["n"] > len(pairs) else real_points_at(link, target)

    monkeypatch.setattr(_agentlinks, "_points_at", lying)
    with pytest.raises(_memdir.VerifyFailed, match="do not resolve to pact"):
        _agentlinks.point_links(pairs, apply=True, label="pact")


# ---------- ownership ----------

def test_ownership_counts_each_root_and_names_the_odd_ones(roots):
    pairs = _agentlinks.link_pairs(roots["config"], roots["home"], roots["pact"])
    _agentlinks.point_links(pairs, apply=True, label="pact")
    (roots["config"] / "agents" / "pact-a.md").unlink()
    (roots["config"] / "agents" / "pact-a.md").symlink_to(roots["repacted"] / "agents" / "pact-a.md")
    os.unlink(roots["config"] / "protocols" / "pact-plugin")
    lines = _agentlinks.ownership(pairs, {"PACT": roots["pact"], "repacted": roots["repacted"]})
    assert lines[0] == "  links (4): 2 -> PACT, 1 -> missing, 1 -> repacted"
    assert any("pact-plugin: missing" in line for line in lines[1:])
