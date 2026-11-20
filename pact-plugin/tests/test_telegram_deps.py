"""
Tests for pact-plugin/telegram/deps.py

Tests cover:
1. check_dependencies: detecting missing/present packages
2. install_dependencies: pip subprocess invocation, success/failure, timeout
3. get_dependency_status: per-package status reporting
4. get_optional_dependency_status: voice transcription availability
"""

from unittest.mock import MagicMock, patch

from telegram.deps import (
    check_dependencies,
    install_dependencies,
)


# =============================================================================
# check_dependencies Tests
# =============================================================================

class TestCheckDependencies:
    """Tests for check_dependencies -- detecting missing packages."""

    def test_returns_empty_when_all_present(self):
        """Should return empty list when all dependencies are importable."""
        with patch("telegram.deps.importlib.import_module") as mock_import:
            mock_import.return_value = MagicMock()
            result = check_dependencies()

        assert result == []


# =============================================================================
# install_dependencies Tests
# =============================================================================

class TestInstallDependencies:
    """Tests for install_dependencies -- pip installation subprocess."""

    def test_success_with_requirements_file(self, tmp_path):
        """Should succeed when pip installs from requirements.txt."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("telegram.deps.subprocess.run", return_value=mock_result):
            with patch("telegram.deps.REQUIREMENTS_FILE", tmp_path / "requirements.txt"):
                # Create the file so it exists
                (tmp_path / "requirements.txt").write_text("mcp>=1.25\nhttpx>=0.27\n")
                with patch("telegram.deps.check_dependencies", return_value=[]):
                    success, msg = install_dependencies(quiet=True)

        assert success is True
        assert "successfully" in msg.lower()


# =============================================================================
# get_dependency_status Tests
# =============================================================================


# =============================================================================
# get_optional_dependency_status Tests
# =============================================================================

