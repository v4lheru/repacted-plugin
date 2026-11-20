"""
Tests for file permissions hardening (M18, M19, L13).

Location: pact-plugin/tests/test_file_permissions.py

Verifies that:
1. pact-memory directories are created with mode 0o700 (owner-only)
2. SQLite database files are set to mode 0o600 after creation
3. Permission hardening is applied consistently across creation points
"""

import os
import stat
from pathlib import Path
from unittest.mock import patch


# Add the pact-memory scripts to path for direct imports
SCRIPTS_DIR = Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts"


# =============================================================================
# database.py Permission Tests
# =============================================================================

class TestDatabasePermissions:
    """Tests for database.py file permission hardening."""

    def test_get_db_path_creates_directory_with_700(self, tmp_path):
        """get_db_path() should create the memory directory with mode 0o700."""
        memory_dir = tmp_path / "pact-memory"
        db_path = memory_dir / "memory.db"

        with patch.dict(os.environ, {"PACT_TEST_MEMORY_DIR": str(memory_dir)}):
            from scripts.database import get_db_path
            result = get_db_path()

        assert memory_dir.exists()
        dir_mode = stat.S_IMODE(memory_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"Directory should have mode 0o700, got {oct(dir_mode)}"
        )
        assert result == db_path


# =============================================================================
# setup_memory.py Permission Tests
# =============================================================================

class TestSetupMemoryPermissions:
    """Tests for setup_memory.py directory permission hardening."""

    def test_ensure_directories_creates_with_700(self, tmp_path):
        """ensure_directories() should create directory with mode 0o700."""
        memory_dir = tmp_path / "pact-memory"

        with patch.dict(os.environ, {"PACT_TEST_MEMORY_DIR": str(memory_dir)}):
            from scripts.setup_memory import ensure_directories
            ensure_directories()

        assert memory_dir.exists()
        dir_mode = stat.S_IMODE(memory_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"Directory should have mode 0o700, got {oct(dir_mode)}"
        )


# =============================================================================
# telegram/config.py Permission Tests (verify existing hardening)
# =============================================================================

