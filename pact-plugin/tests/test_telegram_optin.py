"""
Location: pact-plugin/tests/test_telegram_optin.py
Summary: Regression tests for the opt-in Telegram bridge (U1.4): the quiet start
         without dependencies, the private dependency directory, the
         plugin-declared flow, and the user-scope registration in a scratch config.
Used by: pytest.

Each server start runs in a subprocess with `python3 -S` (no site-packages), so
mcp/httpx are absent whatever the machine has installed.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from telegram import deps

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _link_config(tmp_path: Path) -> Path:
    """A scratch config dir whose protocols/pact-plugin link points at this plugin."""
    config_dir = tmp_path / "cfg"
    (config_dir / "protocols").mkdir(parents=True)
    (config_dir / "protocols" / "pact-plugin").symlink_to(PLUGIN_ROOT / "protocols")
    return config_dir


def _clean_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("PACT_TELEGRAM_DEPS", "CLAUDE_PLUGIN_DATA", "PYTHONPATH")}
    env["HOME"] = str(tmp_path / "home")
    env.update(extra)
    return env


def test_missing_deps_start_is_one_quiet_line_through_the_stable_link(tmp_path):
    """Launched by the user-scope path with no deps: one stderr line, no traceback."""
    script = deps.server_script_path(_link_config(tmp_path))
    result = subprocess.run(
        [sys.executable, "-S", str(script)], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30,
        env=_clean_env(tmp_path, PACT_TELEGRAM_DEPS=str(tmp_path / "absent")),
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.strip().count("\n") == 0
    assert "/repacted:init telegram" in result.stderr


def test_deps_in_private_dir_are_found(tmp_path):
    """PACT_TELEGRAM_DEPS puts the private directory on sys.path before the check. The child
    finds the plugin through PYTHONPATH, which `-S` still honours."""
    target = tmp_path / "telegram-deps"
    for name in ("mcp", "httpx"):
        (target / name).mkdir(parents=True)
        (target / name / "__init__.py").write_text("")
    code = ("from telegram.deps import activate_deps_dir, check_dependencies; "
            "activate_deps_dir(); print(check_dependencies())")
    result = subprocess.run(
        [sys.executable, "-S", "-c", code],
        capture_output=True, text=True, timeout=30,
        env=_clean_env(tmp_path, PACT_TELEGRAM_DEPS=str(target), PYTHONPATH=str(PLUGIN_ROOT)),
    )
    assert result.stdout.strip() == "[]", result.stderr




