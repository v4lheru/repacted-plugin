"""
Tests for CT-aware memory fields (v3.8.0 — CT Phase 2 Extract, item 2G).

Location: pact-plugin/tests/test_memory_ct_fields.py

Verifies that reasoning_chains, agreements_reached, and disagreements_resolved
fields are stored, retrieved, serialized, and searchable.

Used by: pytest test suite to validate CT field implementation across
database.py, models.py, and working_memory.py.
"""

import tempfile
from pathlib import Path

import pytest

from scripts.database import (
    get_connection,
    init_schema,
    create_memory,
    get_memory,
    update_memory,
    delete_memory,
    ensure_initialized,
    _migrate_ct_fields,
    JSON_FIELDS,
)
from scripts.models import MemoryObject, _parse_string_list
from scripts.working_memory import _format_memory_entry, sync_to_claude_md


@pytest.fixture
def db_conn():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.db"
        conn = get_connection(db_path)
        init_schema(conn)
        yield conn
        conn.close()


class TestDatabaseCTFields:
    """Test CT fields in database layer."""

    def test_ct_fields_in_json_fields_set(self):
        """New CT fields must be in JSON_FIELDS for serialization."""
        assert "reasoning_chains" in JSON_FIELDS
        assert "agreements_reached" in JSON_FIELDS
        assert "disagreements_resolved" in JSON_FIELDS

    def test_create_memory_with_ct_fields(self, db_conn):
        """Create a memory with all three CT fields and verify round-trip."""
        memory = {
            "context": "Implementing auth module",
            "goal": "Add JWT refresh tokens",
            "reasoning_chains": [
                "Chose Redis because TTL support → needed for token expiry → simpler than DB cleanup jobs"
            ],
            "agreements_reached": [
                "Lead and architect agree: Redis for token blacklist (verified via teachback)"
            ],
            "disagreements_resolved": [
                "Backend wanted session tokens, architect wanted stateless JWT — resolved: stateless JWT with Redis blacklist for revocation"
            ],
        }
        memory_id = create_memory(db_conn, memory)
        retrieved = get_memory(db_conn, memory_id)

        assert retrieved is not None
        assert retrieved["reasoning_chains"] == memory["reasoning_chains"]
        assert retrieved["agreements_reached"] == memory["agreements_reached"]
        assert retrieved["disagreements_resolved"] == memory["disagreements_resolved"]


class TestModelCTFields:
    """Test CT fields in MemoryObject model."""

    def test_memory_object_with_ct_fields(self):
        """MemoryObject should accept and round-trip CT fields."""
        data = {
            "id": "test123",
            "context": "Test context",
            "reasoning_chains": ["A because B, which required C"],
            "agreements_reached": ["Verified via teachback: use Redis"],
            "disagreements_resolved": ["Resolved: JWT over sessions"],
        }
        obj = MemoryObject.from_dict(data)

        assert obj.reasoning_chains == ["A because B, which required C"]
        assert obj.agreements_reached == ["Verified via teachback: use Redis"]
        assert obj.disagreements_resolved == ["Resolved: JWT over sessions"]

        # Round-trip through to_dict
        d = obj.to_dict()
        assert d["reasoning_chains"] == ["A because B, which required C"]
        assert d["agreements_reached"] == ["Verified via teachback: use Redis"]
        assert d["disagreements_resolved"] == ["Resolved: JWT over sessions"]

    def test_memory_object_without_ct_fields(self):
        """MemoryObject without CT fields defaults to empty lists."""
        data = {"id": "test456", "context": "Minimal"}
        obj = MemoryObject.from_dict(data)

        assert obj.reasoning_chains == []
        assert obj.agreements_reached == []
        assert obj.disagreements_resolved == []


class TestWorkingMemoryFormatting:
    """Test CT fields in working memory display."""

    def test_format_entry_with_ct_fields(self):
        """Working memory entry should display CT fields."""
        memory = {
            "context": "Auth implementation",
            "reasoning_chains": ["Chose bcrypt because OWASP recommends it"],
            "agreements_reached": ["Lead confirmed: Redis for blacklist"],
            "disagreements_resolved": ["JWT won over sessions — stateless preferred"],
        }
        formatted = _format_memory_entry(memory)

        assert "**Reasoning chains**:" in formatted
        assert "bcrypt" in formatted
        assert "**Agreements**:" in formatted
        assert "Redis" in formatted
        assert "**Disagreements resolved**:" in formatted
        assert "JWT" in formatted

    def test_format_entry_without_ct_fields(self):
        """Working memory entry without CT fields omits those lines."""
        memory = {"context": "Simple task"}
        formatted = _format_memory_entry(memory)

        assert "**Reasoning chains**:" not in formatted
        assert "**Agreements**:" not in formatted
        assert "**Disagreements resolved**:" not in formatted
        assert "Simple task" in formatted


class TestParseStringList:
    """Test the _parse_string_list helper used by all CT fields."""

    def test_none_returns_empty(self):
        assert _parse_string_list(None) == []

    def test_empty_string_returns_empty(self):
        assert _parse_string_list("") == []


class TestMigrateCTFields:
    """Test the _migrate_ct_fields database migration."""

    def test_adds_columns_to_existing_db(self):
        """Migration adds CT columns to a database without them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "old_db.db"
            conn = get_connection(db_path)
            # Create a minimal table without CT columns
            conn.execute("""
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    context TEXT,
                    goal TEXT,
                    active_tasks TEXT,
                    lessons_learned TEXT,
                    decisions TEXT,
                    entities TEXT,
                    project_id TEXT,
                    session_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.commit()

            # Run migration
            _migrate_ct_fields(conn)

            # Verify columns exist by inserting data with CT fields
            conn.execute(
                "INSERT INTO memories (id, reasoning_chains, agreements_reached, disagreements_resolved) "
                "VALUES (?, ?, ?, ?)",
                ("test1", '["chain"]', '["agree"]', '["resolved"]')
            )
            conn.commit()

            cursor = conn.execute("SELECT reasoning_chains, agreements_reached, disagreements_resolved FROM memories WHERE id = ?", ("test1",))
            row = cursor.fetchone()
            assert row[0] == '["chain"]'
            assert row[1] == '["agree"]'
            assert row[2] == '["resolved"]'
            conn.close()

    def test_idempotent_migration(self):
        """Running migration twice should not error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_db.db"
            conn = get_connection(db_path)
            init_schema(conn)  # Already has CT columns

            # Running migration again should be safe
            _migrate_ct_fields(conn)
            _migrate_ct_fields(conn)

            # Verify DB still works
            memory_id = create_memory(conn, {"context": "test"})
            assert get_memory(conn, memory_id) is not None
            conn.close()


class TestEnsureInitializedMigration:
    """Test that ensure_initialized runs migration for existing databases."""

    def test_existing_db_gets_migration(self):
        """An existing database without CT columns gets them via ensure_initialized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            conn = get_connection(db_path)
            # Create legacy schema (no CT columns)
            conn.execute("""
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    context TEXT,
                    goal TEXT,
                    active_tasks TEXT,
                    lessons_learned TEXT,
                    decisions TEXT,
                    entities TEXT,
                    project_id TEXT,
                    session_id TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()

            # ensure_initialized should detect existing table and run migration
            ensure_initialized(conn)

            # Verify CT columns exist
            conn.execute(
                "INSERT INTO memories (id, reasoning_chains) VALUES (?, ?)",
                ("test1", '["migrated"]')
            )
            conn.commit()

            cursor = conn.execute("SELECT reasoning_chains FROM memories WHERE id = ?", ("test1",))
            assert cursor.fetchone()[0] == '["migrated"]'
            conn.close()


class TestDatabaseCTFieldsEdgeCases:
    """Edge cases for CT fields in the database layer."""

    def test_create_with_empty_ct_lists(self, db_conn):
        """Empty lists should round-trip through database."""
        memory = {
            "context": "Edge case test",
            "reasoning_chains": [],
            "agreements_reached": [],
            "disagreements_resolved": [],
        }
        memory_id = create_memory(db_conn, memory)
        retrieved = get_memory(db_conn, memory_id)

        assert retrieved is not None
        assert retrieved["reasoning_chains"] == []
        assert retrieved["agreements_reached"] == []
        assert retrieved["disagreements_resolved"] == []


class TestModelCTFieldsEdgeCases:
    """Edge cases for CT fields in the MemoryObject model."""

    def test_searchable_text_omits_empty_ct_fields(self):
        """get_searchable_text should not include labels for empty CT fields."""
        obj = MemoryObject.from_dict({"id": "test", "context": "Only context"})
        text = obj.get_searchable_text()

        assert "Reasoning:" not in text
        assert "Agreements:" not in text
        assert "Disagreements resolved:" not in text


class TestUpdateCTFieldsAdditiveAndReplace:
    """
    Bug 1 (#374): CT fields now append-with-dedup by default. The legacy
    wholesale-replace behavior is available via replace=True.
    """

    def test_append_ct_fields_by_default(self, db_conn):
        """Default update appends new items to existing CT lists."""
        memory_id = create_memory(db_conn, {
            "context": "Original context",
            "reasoning_chains": ["original chain"],
            "agreements_reached": ["original agreement"],
            "disagreements_resolved": ["original resolution"],
        })

        assert update_memory(db_conn, memory_id, {
            "reasoning_chains": ["new chain A", "new chain B"],
            "agreements_reached": ["new agreement"],
            "disagreements_resolved": ["new resolution"],
        }) is True

        retrieved = get_memory(db_conn, memory_id)
        assert retrieved["reasoning_chains"] == [
            "original chain", "new chain A", "new chain B",
        ]
        assert retrieved["agreements_reached"] == [
            "original agreement", "new agreement",
        ]
        assert retrieved["disagreements_resolved"] == [
            "original resolution", "new resolution",
        ]
        # Scalar fields still replace (context is unchanged here only because
        # we didn't pass it in this update).
        assert retrieved["context"] == "Original context"


class TestFormatEntryFieldOrder:
    """Test that CT field rendering order is correct in _format_memory_entry."""

    def test_ct_field_order_when_all_present(self):
        """reasoning_chains before agreements_reached before disagreements_resolved."""
        memory = {
            "context": "All fields present",
            "lessons_learned": ["lesson one"],
            "reasoning_chains": ["chain one"],
            "agreements_reached": ["agreement one"],
            "disagreements_resolved": ["resolution one"],
        }
        formatted = _format_memory_entry(memory)

        rc_pos = formatted.index("**Reasoning chains**:")
        ag_pos = formatted.index("**Agreements**:")
        dr_pos = formatted.index("**Disagreements resolved**:")

        assert rc_pos < ag_pos, "reasoning_chains should appear before agreements_reached"
        assert ag_pos < dr_pos, "agreements_reached should appear before disagreements_resolved"


class TestContentFieldsSetInAPI:
    """Test that CONTENT_FIELDS in memory_api.py includes CT fields."""

    def test_content_fields_includes_ct_fields(self):
        """The CONTENT_FIELDS set used for embedding regeneration must include CT fields."""
        from scripts.memory_api import CONTENT_FIELDS

        expected_ct_fields = {"reasoning_chains", "agreements_reached", "disagreements_resolved"}
        assert expected_ct_fields.issubset(CONTENT_FIELDS)


class TestDeleteMemoryWithCTFields:
    """Test delete_memory round-trip with CT fields."""

    def test_delete_removes_memory_with_ct_fields(self, db_conn):
        """Create a memory with CT fields, delete it, verify it's gone."""
        memory = {
            "context": "Memory to delete",
            "reasoning_chains": ["chain to delete"],
            "agreements_reached": ["agreement to delete"],
            "disagreements_resolved": ["resolution to delete"],
        }
        memory_id = create_memory(db_conn, memory)

        # Verify it exists
        assert get_memory(db_conn, memory_id) is not None

        # Delete it
        deleted = delete_memory(db_conn, memory_id)
        assert deleted is True

        # Verify it's gone
        assert get_memory(db_conn, memory_id) is None

        # Verify double-delete returns False
        assert delete_memory(db_conn, memory_id) is False


class TestSyncToClaudeMdWithCTFields:
    """End-to-end integration test for sync_to_claude_md with CT-enriched memories."""

    def test_sync_writes_ct_fields_to_claude_md(self, tmp_path):
        """Create a CT-enriched memory, sync to CLAUDE.md, verify CT content appears."""
        from unittest.mock import patch

        # Create a minimal CLAUDE.md with Working Memory section
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. "
            "Full history searchable via pact-memory skill. -->\n",
            encoding="utf-8"
        )

        # CT-enriched memory
        memory = {
            "context": "Implementing CT Phase 2 extract",
            "goal": "Add teachback fields to pact-memory",
            "reasoning_chains": ["Redis chosen because TTL support needed for token expiry"],
            "agreements_reached": ["Lead and architect confirmed Redis for blacklist"],
            "disagreements_resolved": ["JWT won over session tokens for statelessness"],
            "lessons_learned": ["Always validate refresh token rotation"],
        }

        # Mock the display-target resolver to return our temp file
        with patch("scripts.working_memory._resolve_display_claude_md_with_base", return_value=(claude_md, claude_md.parent)):
            result = sync_to_claude_md(memory, memory_id="test-ct-sync")

        # Precondition, not the subject: this test is about the CT fields in the
        # file below. A truthiness read says "the write happened" without tying
        # the test to a classification it does not care about; the repr names the
        # reason if it ever fails.
        assert result

        # Read back and verify CT field content appears
        content = claude_md.read_text(encoding="utf-8")

        assert "**Reasoning chains**:" in content
        assert "Redis chosen because TTL" in content
        assert "**Agreements**:" in content
        assert "Lead and architect confirmed Redis" in content
        assert "**Disagreements resolved**:" in content
        assert "JWT won over session tokens" in content
        # Non-CT fields should also be present
        assert "**Context**:" in content
        assert "CT Phase 2 extract" in content
        assert "**Lessons**:" in content
        assert "**Memory ID**: test-ct-sync" in content
