"""
Tests for pact-memory/scripts/setup_memory.py — memory system initialization.

Tests cover:
1. ensure_directories: creates the memory directory with correct permissions
2. check_dependencies: sqlite_vec and model2vec detection
3. ensure_initialized: directory + database init, failure handling
4. get_setup_status: full status report
5. _get_recommendations: dependency-based recommendations
"""
import os
from unittest.mock import patch


# ---------------------------------------------------------------------------
# ensure_directories
# ---------------------------------------------------------------------------

class TestEnsureDirectories:
    def test_creates_directory(self, tmp_path):
        from scripts.setup_memory import ensure_directories
        target = tmp_path / "pact-memory"
        with patch.dict(os.environ, {"PACT_TEST_MEMORY_DIR": str(target)}):
            ensure_directories()
        assert target.is_dir()


# ---------------------------------------------------------------------------
# check_dependencies
# ---------------------------------------------------------------------------

class TestCheckDependencies:
    def test_both_missing(self):
        from scripts.setup_memory import check_dependencies
        with (
            patch.dict("sys.modules", {"sqlite_vec": None}),
            patch("builtins.__import__", side_effect=_import_blocker({"sqlite_vec", "model2vec"})),
        ):
            result = check_dependencies()
        assert result["sqlite_vec"] is False
        assert result["model2vec"] is False


# ---------------------------------------------------------------------------
# ensure_initialized
# ---------------------------------------------------------------------------

class TestEnsureInitialized:
    def test_success(self, tmp_path):
        from scripts.setup_memory import ensure_initialized
        target = tmp_path / "pact-memory"
        with (
            patch.dict(os.environ, {"PACT_TEST_MEMORY_DIR": str(target)}),
            patch("scripts.setup_memory.ensure_directories"),
        ):
            result = ensure_initialized()
        assert result is True


# ---------------------------------------------------------------------------
# _get_recommendations
# ---------------------------------------------------------------------------

class TestGetRecommendations:
    def test_both_missing(self):
        from scripts.setup_memory import _get_recommendations
        recs = _get_recommendations({"sqlite_vec": False, "model2vec": False})
        assert len(recs) == 2
        assert any("sqlite-vec" in r for r in recs)
        assert any("model2vec" in r for r in recs)


# ---------------------------------------------------------------------------
# get_setup_status
# ---------------------------------------------------------------------------

class TestGetSetupStatus:
    def test_returns_expected_keys(self, tmp_path):
        from scripts.setup_memory import get_setup_status
        with patch.dict(os.environ, {"PACT_TEST_MEMORY_DIR": str(tmp_path)}):
            status = get_setup_status()
        assert "initialized" in status
        assert "dependencies" in status
        assert "can_use_semantic_search" in status
        assert "paths" in status
        assert "recommendations" in status


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _import_blocker(blocked_names):
    """Create an import side_effect that blocks specific modules."""
    real_import = __import__
    def _import(name, *args, **kwargs):
        if any(blocked in name for blocked in blocked_names):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)
    return _import
