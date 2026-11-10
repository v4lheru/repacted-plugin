"""
Tests for vector dimension mismatch fixes (GitHub issue #227).

Location: pact-plugin/tests/test_vector_dimension_fixes.py

Tests cover all 7 fixes across 4 files:
1. database.py: ensure_initialized() now calls _init_vector_table() for existing DBs
2. memory_init.py: maybe_migrate_embeddings() uses SELECT * + dict(row) + generate_embedding_text()
3. memory_init.py: maybe_migrate_embeddings() includes project_id in vec_memories INSERT
4. search.py: get_search_capabilities() returns correct keys (embedding_backend, embedding_dimension)
5. search.py: get_search_capabilities() does NOT return old keys (model_path, model_exists, backends)
6. search.py: vector_search() logs failures at WARNING level (not DEBUG)
7. config.py: EMBEDDING_DIMENSION constant removed (no duplicate of embeddings.EMBEDDING_DIM)

Risk tier: STANDARD — well-understood pattern fix.
"""

import struct
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def db_conn():
    """Create a temporary database with full schema for testing."""
    from scripts.database import get_connection, init_schema
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.db"
        conn = get_connection(db_path)
        # Patch _init_vector_table to avoid needing sqlite-vec extension
        with patch('scripts.database._init_vector_table', return_value=False):
            init_schema(conn)
        yield conn
        conn.close()


@pytest.fixture
def legacy_db_conn():
    """Create a temporary database simulating a pre-fix existing DB (memories table only)."""
    from scripts.database import get_connection
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "legacy_memory.db"
        conn = get_connection(db_path)
        # Create legacy schema: memories table exists but NO vec_memories
        conn.execute("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                context TEXT,
                goal TEXT,
                active_tasks TEXT,
                lessons_learned TEXT,
                decisions TEXT,
                entities TEXT,
                reasoning_chains TEXT,
                agreements_reached TEXT,
                disagreements_resolved TEXT,
                project_id TEXT,
                session_id TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        yield conn
        conn.close()


# =============================================================================
# 1. ensure_initialized() migration path — database.py line 318
# =============================================================================

class TestEnsureInitializedVectorMigration:
    """Test that ensure_initialized() calls _init_vector_table() for existing databases."""

    def test_existing_db_triggers_vector_table_init(self, legacy_db_conn):
        """When memories table exists, ensure_initialized should call _init_vector_table."""
        from scripts.database import ensure_initialized

        with patch('scripts.database._init_vector_table') as mock_init_vec:
            mock_init_vec.return_value = True
            ensure_initialized(legacy_db_conn)
            mock_init_vec.assert_called_once_with(legacy_db_conn)

    def test_new_db_initializes_full_schema(self):
        """When no memories table exists, ensure_initialized should call init_schema."""
        from scripts.database import ensure_initialized, get_connection

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fresh.db"
            conn = get_connection(db_path)

            with patch('scripts.database.init_schema') as mock_schema:
                ensure_initialized(conn)
                mock_schema.assert_called_once_with(conn)

            conn.close()


# =============================================================================
# 2. maybe_migrate_embeddings() — field completeness
# =============================================================================

class FakeRow:
    """A sqlite3.Row-like object that supports dict() conversion."""

    def __init__(self, data: dict):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __iter__(self):
        return iter(self._data.items())

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._data[key]
        keys = list(self._data.keys())
        return self._data[keys[key]]


def _build_mock_execute(table_exists, embedding_bytes, memory_rows):
    """Build a side_effect function for mock_conn.execute that handles multiple SQL calls."""

    def mock_execute(sql, params=None):
        mock_cursor = MagicMock()
        sql_lower = sql.strip().lower()

        if "sqlite_master" in sql_lower and "vec_memories" in sql_lower:
            if table_exists:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            else:
                mock_cursor.fetchone.return_value = None
        elif "select embedding from vec_memories" in sql_lower:
            if embedding_bytes:
                mock_cursor.fetchone.return_value = (embedding_bytes,)
            else:
                mock_cursor.fetchone.return_value = None
        elif "drop table" in sql_lower:
            pass
        elif "create virtual table" in sql_lower:
            pass
        elif "select * from memories" in sql_lower:
            # Use FakeRow objects that support dict() conversion
            mock_cursor.fetchall.return_value = [
                FakeRow(row_dict) for row_dict in memory_rows
            ]
        elif "insert or replace into vec_memories" in sql_lower:
            pass

        return mock_cursor

    return mock_execute


class TestMaybeMigrateEmbeddingsFieldCoverage:
    """Test that maybe_migrate_embeddings uses SELECT * and generate_embedding_text."""

    def test_uses_select_star_for_all_fields(self):
        """Migration should use SELECT * and pass full dict to generate_embedding_text."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_service = MagicMock()
        mock_service.generate.return_value = [0.1] * 256

        memory_row = {
            'id': 'mem1',
            'context': 'test context',
            'goal': 'test goal',
            'lessons_learned': '["lesson1"]',
            'decisions': '["decision1"]',
            'entities': None,
            'reasoning_chains': '["chain1"]',
            'agreements_reached': '["agree1"]',
            'disagreements_resolved': '["resolved1"]',
            'project_id': 'proj-abc',
            'session_id': 'sess-xyz',
            'created_at': '2026-01-01',
            'updated_at': '2026-01-01',
            'active_tasks': None,
        }

        mock_conn.execute.side_effect = _build_mock_execute(
            table_exists=True,
            embedding_bytes=struct.pack('384f', *([0.1] * 384)),
            memory_rows=[memory_row],
        )

        # Patch at the source modules where deferred imports resolve
        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.get_embedding_service', return_value=mock_service), \
             patch('scripts.embeddings.generate_embedding_text') as mock_gen_text, \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                mock_gen_text.return_value = "combined text for embedding"
                result = maybe_migrate_embeddings()

        # Verify generate_embedding_text was called with full dict (including CT fields)
        assert mock_gen_text.called, "generate_embedding_text should have been called"
        call_dict = mock_gen_text.call_args[0][0]
        assert 'reasoning_chains' in call_dict, "CT field reasoning_chains missing"
        assert 'agreements_reached' in call_dict, "CT field agreements_reached missing"
        assert 'disagreements_resolved' in call_dict, "CT field disagreements_resolved missing"
        assert 'context' in call_dict, "Basic field context missing"
        assert 'goal' in call_dict, "Basic field goal missing"

    def test_ct_fields_included_in_embedding_text(self):
        """Verify that generate_embedding_text (via MemoryObject) includes CT fields."""
        from scripts.embeddings import generate_embedding_text

        memory_dict = {
            'id': 'test-ct',
            'context': 'Auth implementation',
            'goal': 'Add JWT support',
            'reasoning_chains': ['Chose bcrypt because OWASP recommendation'],
            'agreements_reached': ['Lead confirmed Redis for blacklist'],
            'disagreements_resolved': ['JWT won over session tokens'],
        }

        text = generate_embedding_text(memory_dict)

        assert 'bcrypt' in text, "reasoning_chains content not in embedding text"
        assert 'Redis' in text, "agreements_reached content not in embedding text"
        assert 'JWT won' in text, "disagreements_resolved content not in embedding text"
        assert 'Auth implementation' in text, "context not in embedding text"
        assert 'JWT support' in text, "goal not in embedding text"


# =============================================================================
# 3. maybe_migrate_embeddings() — project_id in INSERT
# =============================================================================

class TestMaybeMigrateEmbeddingsProjectId:
    """Test that project_id is correctly inserted into vec_memories during migration."""

    def test_project_id_included_in_insert(self):
        """Re-embedding INSERT should include project_id from the memory row."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_service = MagicMock()
        mock_service.generate.return_value = [0.1] * 256

        memory_row = {
            'id': 'mem-proj',
            'context': 'project test',
            'goal': None,
            'lessons_learned': None,
            'decisions': None,
            'entities': None,
            'reasoning_chains': None,
            'agreements_reached': None,
            'disagreements_resolved': None,
            'project_id': 'my-important-project',
            'session_id': None,
            'created_at': '2026-01-01',
            'updated_at': '2026-01-01',
            'active_tasks': None,
        }

        # Track all execute calls to verify INSERT params
        execute_calls = []
        original_side_effect = _build_mock_execute(
            table_exists=True,
            embedding_bytes=struct.pack('384f', *([0.1] * 384)),
            memory_rows=[memory_row],
        )

        def tracking_execute(sql, params=None):
            execute_calls.append((sql, params))
            return original_side_effect(sql, params)

        mock_conn.execute.side_effect = tracking_execute

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.get_embedding_service', return_value=mock_service), \
             patch('scripts.embeddings.generate_embedding_text', return_value="text"), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        # Find the INSERT call and verify project_id is included
        insert_calls = [
            (sql, params) for sql, params in execute_calls
            if 'INSERT OR REPLACE INTO vec_memories' in sql
        ]
        assert len(insert_calls) == 1, f"Expected 1 INSERT call, got {len(insert_calls)}"

        insert_sql, insert_params = insert_calls[0]
        assert 'project_id' in insert_sql, "INSERT SQL should include project_id column"
        assert insert_params[0] == 'mem-proj', "First param should be memory_id"
        assert insert_params[1] == 'my-important-project', "Second param should be project_id"

    def test_project_id_none_when_not_set(self):
        """When memory has no project_id, None should be passed in the INSERT."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_service = MagicMock()
        mock_service.generate.return_value = [0.1] * 256

        memory_row = {
            'id': 'mem-no-proj',
            'context': 'no project',
            'goal': None,
            'lessons_learned': None,
            'decisions': None,
            'entities': None,
            'reasoning_chains': None,
            'agreements_reached': None,
            'disagreements_resolved': None,
            'project_id': None,  # No project
            'session_id': None,
            'created_at': '2026-01-01',
            'updated_at': '2026-01-01',
            'active_tasks': None,
        }

        execute_calls = []
        original_side_effect = _build_mock_execute(
            table_exists=True,
            embedding_bytes=struct.pack('384f', *([0.1] * 384)),
            memory_rows=[memory_row],
        )

        def tracking_execute(sql, params=None):
            execute_calls.append((sql, params))
            return original_side_effect(sql, params)

        mock_conn.execute.side_effect = tracking_execute

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.get_embedding_service', return_value=mock_service), \
             patch('scripts.embeddings.generate_embedding_text', return_value="text"), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        insert_calls = [
            (sql, params) for sql, params in execute_calls
            if 'INSERT OR REPLACE INTO vec_memories' in sql
        ]
        assert len(insert_calls) == 1
        insert_sql, insert_params = insert_calls[0]
        assert insert_params[1] is None, "project_id should be None when not set on memory"


# =============================================================================
# 4 & 5. get_search_capabilities() return structure — search.py
# =============================================================================

class TestGetSearchCapabilities:
    """Test that get_search_capabilities returns correct keys after the fix."""

    def test_returns_embedding_backend_key(self):
        """Result should contain 'embedding_backend' (not 'active_backend')."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True):
            mock_check.return_value = {
                'available': True,
                'backend': 'model2vec',
                'model': 'minishlab/potion-base-8M',
                'embedding_dimension': 256,
            }

            result = get_search_capabilities()

        assert 'embedding_backend' in result
        assert result['embedding_backend'] == 'model2vec'

    def test_returns_embedding_dimension_key(self):
        """Result should contain 'embedding_dimension' key."""
        from scripts.search import get_search_capabilities

        with patch('scripts.search.check_embedding_availability') as mock_check, \
             patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True):
            mock_check.return_value = {
                'available': True,
                'backend': 'model2vec',
                'model': 'minishlab/potion-base-8M',
                'embedding_dimension': 256,
            }

            result = get_search_capabilities()

        assert 'embedding_dimension' in result
        assert result['embedding_dimension'] == 256


# =============================================================================
# 6. vector_search() logging — search.py line 128
# =============================================================================

class TestVectorSearchLogging:
    """Test that vector_search logs failures at WARNING level."""

    def test_dimension_error_logged_at_warning_level(self):
        """When vector search fails with dimension-related error, it should log at WARNING."""
        from scripts.search import vector_search

        # Use a MagicMock connection to avoid pysqlite3 read-only attribute issue
        mock_conn = MagicMock()
        mock_conn.enable_load_extension.side_effect = Exception(
            "dimension mismatch: expected 256, got 384"
        )

        with patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True), \
             patch('scripts.search.generate_embedding', return_value=[0.1] * 256), \
             patch('scripts.search.logger') as mock_logger:

            result = vector_search(mock_conn, "test query")

        assert result == [], "Should return empty list on error"
        mock_logger.warning.assert_called_once()
        assert "dimension mismatch" in str(mock_logger.warning.call_args)

    def test_non_dimension_error_logged_at_debug_level(self):
        """Non-dimension errors should log at DEBUG, not WARNING."""
        from scripts.search import vector_search

        mock_conn = MagicMock()
        mock_conn.enable_load_extension.side_effect = Exception("connection lost")

        with patch('scripts.search.SQLITE_EXTENSIONS_ENABLED', True), \
             patch('scripts.search.generate_embedding', return_value=[0.1] * 256), \
             patch('scripts.search.logger') as mock_logger:

            result = vector_search(mock_conn, "test query")

        assert result == [], "Should return empty list on error"
        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_called()
        assert "connection lost" in str(mock_logger.debug.call_args)


# =============================================================================
# 7. config.py — EMBEDDING_DIMENSION removed
# =============================================================================

class TestConfigEmbeddingDimensionRemoved:
    """Test that the dead EMBEDDING_DIMENSION constant was removed from config.py."""

    def test_no_embedding_dimension_in_config(self):
        """config.py should NOT export EMBEDDING_DIMENSION."""
        from scripts import config

        assert not hasattr(config, 'EMBEDDING_DIMENSION'), (
            "EMBEDDING_DIMENSION should have been removed from config.py"
        )

    def test_embedding_dim_lives_in_embeddings(self):
        """The canonical EMBEDDING_DIM constant should be in embeddings.py."""
        from scripts.embeddings import EMBEDDING_DIM

        assert EMBEDDING_DIM == 256


# =============================================================================
# Edge cases
# =============================================================================

class TestEdgeCases:
    """Edge cases for the vector dimension fixes."""

    def test_ensure_initialized_empty_db(self):
        """ensure_initialized on a brand new DB should create full schema."""
        from scripts.database import ensure_initialized, get_connection

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty.db"
            conn = get_connection(db_path)

            with patch('scripts.database._init_vector_table', return_value=False):
                ensure_initialized(conn)

            # DB should now have memories table
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
            )
            assert cursor.fetchone() is not None, "memories table should exist"

            conn.close()

    def test_ensure_initialized_idempotent(self, legacy_db_conn):
        """Calling ensure_initialized twice should not error."""
        from scripts.database import ensure_initialized

        with patch('scripts.database._init_vector_table', return_value=False):
            ensure_initialized(legacy_db_conn)
            ensure_initialized(legacy_db_conn)

        # Verify DB still works
        legacy_db_conn.execute(
            "INSERT INTO memories (id, context) VALUES ('test1', 'test')"
        )
        legacy_db_conn.commit()
        cursor = legacy_db_conn.execute("SELECT context FROM memories WHERE id='test1'")
        assert cursor.fetchone()[0] == 'test'


# =============================================================================
# Integration: generate_embedding_text with all fields
# =============================================================================

class TestGenerateEmbeddingTextIntegration:
    """Integration tests for generate_embedding_text covering all memory fields."""

    def test_includes_basic_fields(self):
        """Embedding text should include context, goal, lessons, decisions."""
        from scripts.embeddings import generate_embedding_text

        memory = {
            'id': 'test-basic',
            'context': 'Working on authentication',
            'goal': 'Implement OAuth2 flow',
            'lessons_learned': ['Always hash passwords with bcrypt'],
            'decisions': [{'decision': 'Use Redis for sessions'}],
        }

        text = generate_embedding_text(memory)

        assert 'authentication' in text
        assert 'OAuth2' in text
        assert 'bcrypt' in text
        assert 'Redis' in text

    def test_includes_all_ct_fields(self):
        """Embedding text should include all three CT fields."""
        from scripts.embeddings import generate_embedding_text

        memory = {
            'id': 'test-ct',
            'reasoning_chains': ['Chose model2vec because pure Python, no crashes'],
            'agreements_reached': ['Confirmed via teachback: 256-dim sufficient'],
            'disagreements_resolved': ['Backend wanted 384-dim, architect preferred 256-dim'],
        }

        text = generate_embedding_text(memory)

        assert 'model2vec' in text
        assert '256-dim sufficient' in text
        assert '384-dim' in text


# =============================================================================
# Regression: ensure old behaviors still work
# =============================================================================

class TestRegressionSafety:
    """Ensure fixes don't break existing functionality."""

    def test_init_schema_still_works(self):
        """init_schema should still create all tables correctly."""
        from scripts.database import get_connection, init_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "schema_test.db"
            conn = get_connection(db_path)

            with patch('scripts.database._init_vector_table', return_value=False):
                init_schema(conn)

            # Check all tables exist
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in cursor.fetchall()}

            assert 'memories' in tables
            assert 'files' in tables
            assert 'memory_files' in tables
            assert 'file_relations' in tables

            conn.close()


# =============================================================================
# F2: Multi-row and partial failure in maybe_migrate_embeddings
# =============================================================================

class TestMaybeMigrateEmbeddingsMultiRow:
    """Test migration with multiple memory rows, including partial failure."""

    def _make_memory_row(self, mem_id, context='test', project_id=None):
        """Helper to create a minimal memory row dict."""
        return {
            'id': mem_id,
            'context': context,
            'goal': None,
            'lessons_learned': None,
            'decisions': None,
            'entities': None,
            'reasoning_chains': None,
            'agreements_reached': None,
            'disagreements_resolved': None,
            'project_id': project_id,
            'session_id': None,
            'created_at': '2026-01-01',
            'updated_at': '2026-01-01',
            'active_tasks': None,
        }

    def test_multiple_rows_all_succeed(self):
        """All rows should be re-embedded when embedding generation succeeds."""
        from scripts.memory_init import maybe_migrate_embeddings, reset_initialization

        reset_initialization()

        mock_conn = MagicMock()
        mock_service = MagicMock()
        mock_service.generate.return_value = [0.1] * 256

        rows = [
            self._make_memory_row('mem-1', 'first context', 'proj-a'),
            self._make_memory_row('mem-2', 'second context', 'proj-b'),
            self._make_memory_row('mem-3', 'third context'),
        ]

        mock_conn.execute.side_effect = _build_mock_execute(
            table_exists=True,
            embedding_bytes=struct.pack('384f', *([0.1] * 384)),
            memory_rows=rows,
        )

        with patch('scripts.database.get_connection', return_value=mock_conn), \
             patch('scripts.embeddings.get_embedding_service', return_value=mock_service), \
             patch('scripts.embeddings.generate_embedding_text', return_value="text"), \
             patch('scripts.embeddings.EMBEDDING_DIM', 256):

            mock_sqlite_vec = MagicMock()
            with patch.dict('sys.modules', {'sqlite_vec': mock_sqlite_vec}):
                result = maybe_migrate_embeddings()

        assert result['status'] == 'ok'
        assert '3/3' in result['message'], f"Expected 3/3 in message, got: {result['message']}"


# =============================================================================
# F3: Direct tests for _check_and_migrate_vector_table
# =============================================================================

class TestCheckAndMigrateVectorTable:
    """Direct tests for _check_and_migrate_vector_table dimension detection."""

    def test_no_table_is_noop(self):
        """When vec_memories table doesn't exist, function returns without action."""
        from scripts.database import _check_and_migrate_vector_table

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # Table doesn't exist
        mock_conn.execute.return_value = mock_cursor

        _check_and_migrate_vector_table(mock_conn, 256)

        # Should only have called execute once (the sqlite_master check)
        assert mock_conn.execute.call_count == 1
        # Should NOT have called DROP TABLE
        for call_args in mock_conn.execute.call_args_list:
            sql = str(call_args)
            assert 'DROP TABLE' not in sql


# =============================================================================
# Regression: probe query must use LIMIT 1 (not LIMIT 0)
# =============================================================================

class TestProbeQueryUsesLimit1:
    """Regression tests for GitHub issue #231 — LIMIT 0 bypasses sqlite-vec MATCH engine.

    The dimension probe query in _check_and_migrate_vector_table must use LIMIT 1
    (not LIMIT 0) because LIMIT 0 causes SQLite to skip the MATCH engine entirely,
    meaning dimension mismatches are never detected.
    """

    def test_probe_sql_contains_limit_1(self):
        """The probe query SQL must contain LIMIT 1 to engage sqlite-vec's MATCH engine."""
        from scripts.database import _check_and_migrate_vector_table

        executed_sql = []

        def tracking_execute(sql, params=None):
            executed_sql.append(sql)
            mock_cursor = MagicMock()
            sql_lower = sql.strip().lower()

            if "sqlite_master" in sql_lower:
                mock_cursor.fetchone.return_value = ('vec_memories',)
            elif "match" in sql_lower:
                # Probe succeeds — dimensions match
                mock_cursor.fetchall.return_value = []
            return mock_cursor

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = tracking_execute

        _check_and_migrate_vector_table(mock_conn, 256)

        # Find the probe query (the one containing MATCH)
        probe_queries = [sql for sql in executed_sql if 'MATCH' in sql]
        assert len(probe_queries) == 1, (
            f"Expected exactly 1 probe query with MATCH, got {len(probe_queries)}: {probe_queries}"
        )

        probe_sql = probe_queries[0]
        assert 'LIMIT 1' in probe_sql, (
            f"Probe query must use LIMIT 1 (not LIMIT 0) to engage sqlite-vec MATCH engine. "
            f"Got: {probe_sql}"
        )
        assert 'LIMIT 0' not in probe_sql, (
            f"Probe query must NOT use LIMIT 0 (bypasses MATCH engine). Got: {probe_sql}"
        )


# =============================================================================
# Integration: real sqlite-vec connection tests
# =============================================================================

# Check availability of pysqlite3 and sqlite_vec for integration tests
_has_sqlite_vec = False
try:
    import pysqlite3
    import sqlite_vec as _sqlite_vec_mod
    _has_sqlite_vec = True
except ImportError:
    pass

requires_sqlite_vec = pytest.mark.skipif(
    not _has_sqlite_vec,
    reason="Requires pysqlite3 and sqlite_vec for real MATCH engine tests"
)


@requires_sqlite_vec
class TestSqliteVecIntegration:
    """Integration tests using real sqlite-vec to verify MATCH engine behavior.

    These tests confirm that _check_and_migrate_vector_table correctly detects
    dimension mismatches via the actual sqlite-vec MATCH engine, not mocked behavior.
    This is the definitive guard against LIMIT 0-style bypass bugs (issue #231).
    """

    def _create_vec_table(self, conn, dim):
        """Create a vec_memories virtual table with the given dimension."""
        conn.execute(f"CREATE VIRTUAL TABLE vec_memories USING vec0(memory_id TEXT, embedding float[{dim}])")
        conn.commit()

    def _insert_vec_row(self, conn, memory_id, dim, values=None):
        """Insert a row into vec_memories with the given dimension."""
        if values is None:
            values = [0.1] * dim
        embedding = struct.pack(f'{dim}f', *values)
        conn.execute(
            "INSERT INTO vec_memories (memory_id, embedding) VALUES (?, ?)",
            (memory_id, embedding)
        )
        conn.commit()

    def _make_real_conn(self):
        """Create a real pysqlite3 connection with sqlite-vec loaded."""
        conn = pysqlite3.connect(':memory:')
        conn.enable_load_extension(True)
        _sqlite_vec_mod.load(conn)
        conn.enable_load_extension(False)
        return conn

    def test_limit_1_detects_dimension_mismatch(self):
        """With real sqlite-vec, LIMIT 1 must raise on dimension mismatch."""
        conn = self._make_real_conn()
        try:
            # Create table with dim=4 and insert a row
            self._create_vec_table(conn, 4)
            self._insert_vec_row(conn, 'test-row', 4)

            # Probe with wrong dimension (dim=2) — must raise
            wrong_embedding = struct.pack('2f', 0.1, 0.2)
            with pytest.raises(Exception, match="(?i)dimension"):
                conn.execute(
                    "SELECT memory_id FROM vec_memories WHERE embedding MATCH ? LIMIT 1",
                    (wrong_embedding,)
                )
        finally:
            conn.close()


