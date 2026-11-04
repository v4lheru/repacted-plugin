"""Tests for the `repact` shim on PATH: hooks/shared/repact_shim.py and its doctor line
(_doctor_state.check_launcher).

Location: pact-plugin/tests/test_repact_shim.py. HOME and the config dir are built under
tmp_path; nothing reads or writes the real ~/.local/bin or ~/.claude. One regression test per
behaviour (fresh write, survives an update, foreign file untouched, off switch, the doctor's
not-on-PATH line), then the resolver at its boundary: it parses installed_plugins.json.
Run: tcount pytest -q tests/test_repact_shim.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared import repact_shim

BIN = Path(__file__).resolve().parent.parent / "bin"


def _load_doctor():
    spec = importlib.util.spec_from_file_location("_doctor_for_shim", BIN / "_doctor.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


context_from_env = _load_doctor().context_from_env
import _doctor_state  # noqa: E402  # bin/ is on sys.path once _doctor loaded


def fake_plugin(root: Path) -> Path:
    """A plugin root whose bin/repact prints its root and its arguments, one per line."""
    (root / "bin").mkdir(parents=True)
    launcher = root / "bin" / "repact"
    launcher.write_text(f'#!/bin/sh\necho "root={root}"\nfor a in "$@"; do echo "arg=$a"; done\n')
    launcher.chmod(0o755)
    return root


def register(config: Path, entries: dict, enabled=None, env_block=None) -> None:
    (config / "plugins").mkdir(parents=True, exist_ok=True)
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": entries}))
    settings = {}
    if enabled is not None:
        settings["enabledPlugins"] = enabled
    if env_block is not None:
        settings["env"] = env_block
    (config / "settings.json").write_text(json.dumps(settings))


def user(path: Path, scope="user") -> list:
    return [{"scope": scope, "installPath": str(path), "version": "2.0.0"}]


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".claude"
    config.mkdir(parents=True)
    for name in ("CLAUDE_CONFIG_DIR", repact_shim.OPTION):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{home}/.local/bin{os.pathsep}/usr/bin{os.pathsep}/bin")
    return home


def env(home: Path, **extra) -> dict:
    out = {"HOME": str(home), "PATH": f"{home}/.local/bin{os.pathsep}/usr/bin{os.pathsep}/bin",
           "SHELL": "/bin/zsh"}
    out.update(extra)
    return out


def run_shim(home: Path, *args: str) -> subprocess.CompletedProcess:
    run_env = {"HOME": str(home), "PATH": os.environ.get("PATH", "") + os.pathsep + "/usr/bin:/bin"}
    run_env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + run_env["PATH"]
    return subprocess.run([str(repact_shim.shim_path(home)), *args], env=run_env,
                          capture_output=True, text=True, timeout=30)


# ─── one regression test per behaviour ────────────────────────────────────────────────────

@pytest.mark.slow
def test_a_fresh_home_gets_a_working_shim_that_passes_arguments_unchanged(home, tmp_path):
    root = fake_plugin(tmp_path / "cache" / "repacted" / "2.0.0")
    register(home / ".claude", {"repacted@repacted": user(root)})
    note = repact_shim.ensure_shim(home, home / ".claude", env(home))
    path = repact_shim.shim_path(home)
    assert note and note.startswith(f"Wrote {path}")
    assert path.read_text().splitlines()[1].startswith(repact_shim.MARKER)
    assert os.access(path, os.X_OK)
    proc = run_shim(home, "--resume", "a b", "it's")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [f"root={root}", "arg=--resume", "arg=a b", "arg=it's"]
    assert repact_shim.ensure_shim(home, home / ".claude", env(home)) is None  # unchanged: no write
    result = _doctor_state.check_launcher(context_from_env(env(home)))
    assert result.state == "green" and str(root / "bin" / "repact") in result.summary


def test_the_shim_follows_a_plugin_update_without_being_rewritten(home, tmp_path):
    old = fake_plugin(tmp_path / "cache" / "repacted" / "2.0.0")
    register(home / ".claude", {"repacted@repacted": user(old)})
    repact_shim.ensure_shim(home, home / ".claude", env(home))
    before = repact_shim.shim_path(home).read_bytes()
    new = fake_plugin(tmp_path / "cache" / "repacted" / "2.0.1")
    register(home / ".claude", {"repacted@repacted": user(new)})
    (old / "bin" / "repact").unlink()                       # the update removed the old cache dir
    proc = run_shim(home)
    assert proc.returncode == 0 and proc.stdout.splitlines() == [f"root={new}"]
    assert repact_shim.shim_path(home).read_bytes() == before


def test_a_foreign_repact_is_never_overwritten_or_deleted_and_the_doctor_names_it(home, tmp_path):
    register(home / ".claude", {"repacted@repacted": user(fake_plugin(tmp_path / "root"))})
    path = repact_shim.shim_path(home)
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\necho mine\n")
    assert repact_shim.ensure_shim(home, home / ".claude", env(home)) is None
    assert repact_shim.ensure_shim(home, home / ".claude", env(home, **{repact_shim.OPTION: "0"})) is None
    assert path.read_text() == "#!/bin/sh\necho mine\n"
    result = _doctor_state.check_launcher(context_from_env(env(home)))
    assert result.state == "amber" and "is not rePACTed's shim" in result.summary


# ─── the resolver at its boundary: installed_plugins.json is outside input ────────────────


