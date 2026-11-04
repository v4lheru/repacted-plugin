"""
Tests for pact-plugin/telegram/config.py

Tests cover:
1. parse_env_file: KEY=VALUE parsing, quoted values, comments, blank lines, inline comments
2. check_file_permissions: secure (600) and insecure permission warnings
3. check_not_in_git: detecting .env inside/outside git repos
4. load_config: required field validation, mode validation, security checks
5. load_config_safe: graceful None return on errors
6. ensure_config_dir: directory creation with secure permissions
7. Edge cases: empty files, malformed lines, Unicode, long values
8. Security: no credentials in error messages, permission enforcement
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from telegram.config import (
    ConfigError,
    check_file_permissions,
    check_not_in_git,
    ensure_config_dir,
    load_config,
    load_config_safe,
    parse_env_file,
    DEFAULT_MODE,
)


# =============================================================================
# parse_env_file Tests
# =============================================================================

class TestParseEnvFile:
    """Tests for parse_env_file -- .env file parsing using stdlib only."""

    def test_parses_basic_key_value(self, tmp_path):
        """Should parse simple KEY=VALUE pairs."""
        env_file = tmp_path / ".env"
        env_file.write_text("BOT_TOKEN=abc123\nCHAT_ID=456\n")

        result = parse_env_file(env_file)

        assert result == {"BOT_TOKEN": "abc123", "CHAT_ID": "456"}

    def test_strips_double_quotes(self, tmp_path):
        """Should strip double quotes from values."""
        env_file = tmp_path / ".env"
        env_file.write_text('KEY="some value"\n')

        result = parse_env_file(env_file)

        assert result["KEY"] == "some value"


# =============================================================================
# check_file_permissions Tests
# =============================================================================

class TestCheckFilePermissions:
    """Tests for check_file_permissions -- security permission validation."""

    def test_no_warnings_for_secure_permissions(self, tmp_path):
        """Should return no warnings for 600 permissions."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value")
        env_file.chmod(0o600)

        warnings = check_file_permissions(env_file)

        assert warnings == []


# =============================================================================
# check_not_in_git Tests
# =============================================================================

class TestCheckNotInGit:
    """Tests for check_not_in_git -- verifying .env is not inside git repo."""

    def test_returns_true_for_nonexistent_file(self, tmp_path):
        """Should return True for non-existent file (safely outside git)."""
        env_file = tmp_path / "nonexistent.env"

        assert check_not_in_git(env_file) is True


# =============================================================================
# load_config Tests
# =============================================================================

class TestLoadConfig:
    """Tests for load_config -- full configuration loading and validation."""

    def _write_env(self, tmp_path, content):
        """Helper to write a .env file and return its path."""
        env_file = tmp_path / ".env"
        env_file.write_text(content)
        return env_file

    def test_loads_valid_config(self, tmp_path):
        """Should load a valid configuration with all required fields."""
        env_file = self._write_env(
            tmp_path,
            "TELEGRAM_BOT_TOKEN=123456789:ABCDEF\nTELEGRAM_CHAT_ID=987654321\n",
        )

        config = load_config(env_path=env_file, check_security=False)

        assert config["bot_token"] == "123456789:ABCDEF"
        assert config["chat_id"] == "987654321"
        assert config["mode"] == "passive"
        assert config["openai_api_key"] is None


# =============================================================================
# load_config_safe Tests
# =============================================================================

class TestLoadConfigSafe:
    """Tests for load_config_safe -- graceful error handling wrapper."""

    def test_returns_config_on_success(self, tmp_path):
        """Should return config dict when config is valid."""
        env_file = tmp_path / ".env"
        env_file.write_text("TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=123\n")

        result = load_config_safe(env_path=env_file)

        assert result is not None
        assert result["bot_token"] == "abc"


# =============================================================================
# ensure_config_dir Tests
# =============================================================================

class TestEnsureConfigDir:
    """Tests for ensure_config_dir -- directory creation with secure permissions."""

    def test_creates_directory_if_not_exists(self, tmp_path):
        """Should create the config directory."""
        test_dir = tmp_path / "new_config"

        with patch("telegram.config.CONFIG_DIR", test_dir):
            result = ensure_config_dir()

        assert result == test_dir
        assert test_dir.exists()
        assert test_dir.is_dir()


def test_config_and_coordinator_dirs_follow_claude_config_dir(tmp_path):
    """An isolated CLAUDE_CONFIG_DIR must not reach the owner's real Telegram config."""
    import os
    import subprocess
    import sys

    code = ("from telegram import config, routing; "
            "print(config.CONFIG_DIR); print(routing.COORDINATOR_DIR)")
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"))
    plugin = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, "-c", code], cwd=plugin, env=env,
                         capture_output=True, text=True, check=True).stdout.split("\n")
    assert out[0] == str(tmp_path / "cfg" / "pact-telegram")
    assert out[1] == str(tmp_path / "cfg" / "pact-telegram" / "coordinator")
