"""
Tests for the embedding_catchup module - background embedding recovery with RAM awareness.

Tests cover:
1. get_available_ram_mb() - platform-specific RAM detection with mocked backends
2. embed_pending_memories() - RAM threshold gating, processing loop, error handling
3. MIN_CATCHUP_RAM_MB constant - value assertion and default parameter wiring
4. get_unembedded_memories() - extension check, error paths
5. embed_single_memory() - extension check, error paths

Note: embedding_catchup.py uses relative imports from its parent package (.database,
.embeddings). To test functions that depend on these imports, we mock the database and
embeddings modules in sys.modules before importing embedding_catchup. For the pure
function get_available_ram_mb(), we test it by importing the module after mocking its
package dependencies.
"""

import inspect
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

_scripts_path = str(Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts")


def _make_mock_database():
    """Create a mock standing in for the .database module."""
    mod = ModuleType("database")
    mod.db_connection = MagicMock()
    mod.ensure_initialized = MagicMock()
    mod.get_memory = MagicMock()
    mod.SQLITE_EXTENSIONS_ENABLED = True
    return mod


def _load_embedding_catchup():
    """Load embedding_catchup module with mocked database dependency.

    Returns the module object with database functions mocked.
    """
    # Clear cached
    for key in list(sys.modules.keys()):
        if "embedding_catchup" in key:
            del sys.modules[key]

    # Create a fake package that embedding_catchup thinks it belongs to
    pkg_name = "_test_scripts_pkg"
    pkg = ModuleType(pkg_name)
    pkg.__path__ = [_scripts_path]
    pkg.__package__ = pkg_name

    # Mock submodules
    mock_db = _make_mock_database()

    # The real embeddings module - import it to get the actual constant
    sys.modules.pop("scripts.embeddings", None)
    from scripts.embeddings import MIN_CATCHUP_RAM_MB, generate_embedding, generate_embedding_text
    mock_emb_mod = ModuleType(f"{pkg_name}.embeddings")
    mock_emb_mod.generate_embedding = generate_embedding
    mock_emb_mod.generate_embedding_text = generate_embedding_text
    mock_emb_mod.MIN_CATCHUP_RAM_MB = MIN_CATCHUP_RAM_MB

    mock_db_mod = _make_mock_database()
    mock_db_mod.__name__ = f"{pkg_name}.database"

    sys.modules[pkg_name] = pkg
    sys.modules[f"{pkg_name}.database"] = mock_db_mod
    sys.modules[f"{pkg_name}.embeddings"] = mock_emb_mod

    # Now load embedding_catchup as a submodule of our fake package
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.embedding_catchup",
        Path(_scripts_path) / "embedding_catchup.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.embedding_catchup"] = mod
    spec.loader.exec_module(mod)

    # Cleanup fake package from sys.modules
    for key in [pkg_name, f"{pkg_name}.database",
                f"{pkg_name}.embeddings", f"{pkg_name}.embedding_catchup"]:
        sys.modules.pop(key, None)

    return mod


@pytest.fixture
def ec_module():
    """Provide a freshly-loaded embedding_catchup module with mocked DB deps."""
    return _load_embedding_catchup()


class TestMinCatchupRamMbConstant:
    """Tests for the MIN_CATCHUP_RAM_MB constant and its wiring."""

    def test_constant_value_is_75(self):
        """The constant should be 75.0 MB - guards against unintended changes."""
        from scripts.embeddings import MIN_CATCHUP_RAM_MB
        assert MIN_CATCHUP_RAM_MB == 75.0

    def test_constant_is_positive(self):
        """MIN_CATCHUP_RAM_MB must be positive to be a meaningful threshold."""
        from scripts.embeddings import MIN_CATCHUP_RAM_MB
        assert MIN_CATCHUP_RAM_MB > 0

    def test_embed_pending_memories_default_uses_constant(self, ec_module):
        """Default min_ram_mb parameter should equal MIN_CATCHUP_RAM_MB."""
        from scripts.embeddings import MIN_CATCHUP_RAM_MB
        sig = inspect.signature(ec_module.embed_pending_memories)
        default = sig.parameters["min_ram_mb"].default
        assert default == MIN_CATCHUP_RAM_MB


class TestGetAvailableRamMb:
    """Tests for get_available_ram_mb() - platform-specific RAM detection."""

    def test_psutil_path_returns_available_mb(self, ec_module):
        """When psutil is available, should return available memory in MB."""
        mock_psutil = MagicMock()
        mock_psutil.virtual_memory.return_value = MagicMock(
            available=512 * 1024 * 1024  # 512 MB in bytes
        )

        with patch.dict(sys.modules, {"psutil": mock_psutil}):
            result = ec_module.get_available_ram_mb()
            assert result == pytest.approx(512.0)

    def test_psutil_import_error_falls_through(self, ec_module):
        """When psutil is not available, should fall through to platform methods."""
        with patch.dict(sys.modules, {"psutil": None}), \
             patch.object(ec_module.platform, "system", return_value="Unknown"):
            result = ec_module.get_available_ram_mb()
            assert result == -1.0

    def test_darwin_vm_stat_fallback(self, ec_module):
        """On macOS without psutil, should parse vm_stat output."""
        vm_stat_output = (
            "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
            "Pages free:                               10000.\n"
            "Pages active:                             50000.\n"
            "Pages inactive:                           30000.\n"
            "Pages speculative:                         5000.\n"
            "Pages wired down:                         20000.\n"
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = vm_stat_output

        with patch.dict(sys.modules, {"psutil": None}), \
             patch.object(ec_module.platform, "system", return_value="Darwin"), \
             patch.object(ec_module.subprocess, "run", return_value=mock_result):
            result = ec_module.get_available_ram_mb()
            # (free + speculative + inactive) * page_size / (1024 * 1024)
            # (10000 + 5000 + 30000) * 16384 / (1024 * 1024) = 703.125
            assert result == pytest.approx(703.125)


class TestEmbedPendingMemories:
    """Tests for embed_pending_memories() - RAM threshold and processing logic."""

    def test_skips_when_ram_below_threshold(self, ec_module):
        """Should set skipped_ram=True when available RAM is below min_ram_mb."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=50.0):
            result = ec_module.embed_pending_memories(min_ram_mb=75.0)
            assert result["skipped_ram"] is True
            assert result["processed"] == 0

    def test_proceeds_when_ram_above_threshold(self, ec_module):
        """Should not skip when available RAM exceeds min_ram_mb."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=100.0), \
             patch.object(ec_module, "get_unembedded_memories", return_value=[]):
            result = ec_module.embed_pending_memories(min_ram_mb=75.0)
            assert result["skipped_ram"] is False

    def test_proceeds_when_ram_exactly_at_threshold(self, ec_module):
        """At exactly the threshold, should proceed (condition is strictly less than)."""
        with patch.object(ec_module, "get_available_ram_mb", return_value=75.0), \
             patch.object(ec_module, "get_unembedded_memories", return_value=[]):
            result = ec_module.embed_pending_memories(min_ram_mb=75.0)
            assert result["skipped_ram"] is False


class TestGetUnembeddedMemories:
    """Tests for get_unembedded_memories() - finding memories without embeddings."""

    def test_returns_empty_when_extensions_disabled(self, ec_module):
        """Should return empty list when SQLite extensions are not available."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = False
        result = ec_module.get_unembedded_memories()
        assert result == []

    def test_returns_empty_on_db_exception(self, ec_module):
        """Should return empty list on any database error."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = True
        ec_module.db_connection = MagicMock(side_effect=Exception("DB error"))
        result = ec_module.get_unembedded_memories()
        assert result == []


class TestEmbedSingleMemory:
    """Tests for embed_single_memory() - embedding a single memory by ID."""

    def test_returns_false_when_extensions_disabled(self, ec_module):
        """Should return False when SQLite extensions are not available."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = False
        result = ec_module.embed_single_memory("test-id")
        assert result is False

    def test_returns_false_on_exception(self, ec_module):
        """Should return False on any error."""
        ec_module.SQLITE_EXTENSIONS_ENABLED = True
        ec_module.db_connection = MagicMock(side_effect=Exception("DB error"))
        result = ec_module.embed_single_memory("test-id")
        assert result is False


class TestInabilityIsDistinguishableFromEmptiness:
    """`get_unembedded_memories` used to report INABILITY AS EMPTINESS.

    Three of its four exits return no IDs because it COULD NOT LOOK -- the
    vector extension is unavailable, the vector table is absent, or the query
    raised -- and one returns none because there are none. All four were an
    identical `[]`, and the caller reads empty as nothing-to-do. So in the
    degraded state, which is the exact condition the recovery sweep exists for,
    the sweep reported nothing to do precisely when it could do nothing.

    THE SAME GENUS AS THE REST OF THIS FOLD: a signal true about a narrower
    proposition than the one being read for. `[]` was true about "no ids
    returned" and was being read as "no work outstanding".
    """

    def test_the_four_outcomes_are_distinguishable(self, ec_module):
        ec = ec_module
        R = ec.UnembeddedResult
        answered_empty = R()
        answered_full = R(["a"])
        no_ext = R(reason=R.NO_EXTENSIONS)
        no_tbl = R(reason=R.NO_VECTOR_TABLE)
        failed = R(reason=R.QUERY_FAILED)

        # All four inability/answered reasons are distinct values.
        assert len({no_ext.reason, no_tbl.reason, failed.reason,
                    answered_empty.reason}) == 4

        # ONLY the answered ones may be read as an answer.
        assert answered_empty.answerable is True
        assert answered_full.answerable is True
        assert no_ext.answerable is False
        assert no_tbl.answerable is False
        assert failed.answerable is False

    def test_every_existing_list_use_is_unchanged(self, ec_module):
        """The shape is a `list` so callers keep working.

        This is the property that made the subclass the right choice over a
        None-or-list return: iteration, len, truthiness and indexing are all
        exactly what they were.
        """
        ec = ec_module
        r = ec.UnembeddedResult(["a", "b"])
        assert list(r) == ["a", "b"]
        assert len(r) == 2
        assert r[0] == "a"
        assert bool(r) is True
        assert bool(ec.UnembeddedResult()) is False
        assert isinstance(r, list)


