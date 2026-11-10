"""
Tests for pact-memory/scripts/database.py — SQLite database layer.

Tests cover:
1. Schema initialization: tables, indexes, migrations
2. JSON serialization/deserialization helpers
3. Memory CRUD: create, get, update, delete, list
4. Text search: substring matching, project filtering, LIKE wildcard escaping
5. Database maintenance: count, integrity check
6. generate_id: uniqueness
7. ALLOWED_COLUMNS whitelist: unknown columns filtered by update_memory
8. TOCTOU mitigation: atomic file pre-creation, race handling, WAL sidecar chmod
"""
import json
from unittest.mock import patch

import pytest

from helpers import create_test_schema

# Use the same sqlite3 module that database.py uses (pysqlite3 if available)
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3


@pytest.fixture
def db_conn(tmp_path):
    """Create a fresh database with schema, patching ensure_initialized to no-op."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_test_schema(conn)
    with patch("scripts.database.ensure_initialized"):
        yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------

class TestTimestampColumnsAreRequired:
    """Built through PRODUCTION's own `ensure_initialized`, NOT the fixture.

    `db_conn` and every other schema in this suite is a test-local copy, so
    none of them can see a change to production's CREATE TABLE. This class
    exists because that makes a green suite no evidence about this column.
    """

    def _production_schema(self, tmp_path):
        from scripts.database import ensure_initialized
        conn = sqlite3.connect(str(tmp_path / "prod.db"))
        ensure_initialized(conn)
        return conn

    @pytest.mark.parametrize("column", ["created_at",])
    def test_an_insert_omitting_a_timestamp_is_refused(self, tmp_path, column):
        conn = self._production_schema(tmp_path)
        supplied = "updated_at" if column == "created_at" else "created_at"
        with pytest.raises(sqlite3.IntegrityError, match=f"NOT NULL.*{column}"):
            conn.execute(
                f"INSERT INTO memories (id, {supplied}) VALUES ('x', '2026-01-01')"
            )

    def test_supplying_both_is_accepted(self, tmp_path):
        """Control: the refusal above is about the omission, not the insert."""
        conn = self._production_schema(tmp_path)
        conn.execute(
            "INSERT INTO memories (id, created_at, updated_at)"
            " VALUES ('x', '2026-01-01', '2026-01-01')"
        )
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 1


class TestSchemaInit:
    def test_creates_memories_table(self, db_conn):
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
        )
        assert cursor.fetchone() is not None


class TestMigrateCTFields:
    def test_adds_ct_columns(self, tmp_path):
        """CT columns should be added to existing DBs without them."""
        from scripts.database import _migrate_ct_fields
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, context TEXT, goal TEXT, created_at TEXT
            )
        """)
        conn.commit()
        _migrate_ct_fields(conn)
        conn.execute("SELECT reasoning_chains, agreements_reached, disagreements_resolved FROM memories")
        conn.close()


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

class TestJsonSerialization:
    def test_serialize_json_fields(self):
        from scripts.database import _serialize_json_fields
        data = {
            "context": "test",
            "active_tasks": [{"task": "T1"}],
            "lessons_learned": ["L1"],
        }
        result = _serialize_json_fields(data)
        assert result["context"] == "test"
        assert isinstance(result["active_tasks"], str)
        assert json.loads(result["active_tasks"]) == [{"task": "T1"}]


# ---------------------------------------------------------------------------
# Memory CRUD
# ---------------------------------------------------------------------------

class TestCreateMemory:
    def test_creates_and_returns_id(self, db_conn):
        from scripts.database import create_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        assert mem_id is not None
        assert len(mem_id) > 0


class TestGetMemory:
    def test_returns_memory(self, db_conn):
        from scripts.database import create_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Test context"})
        result = get_memory(db_conn, mem_id)
        assert result is not None
        assert result["context"] == "Test context"


class TestResolveMemoryIdPrefix:
    """Storage-layer tests for resolve_memory_id_prefix."""

    def test_unique_prefix_returns_full_id(self, db_conn):
        from scripts.database import create_memory, resolve_memory_id_prefix
        mem_id = create_memory(
            db_conn,
            {"id": "abc12345" + "0" * 24, "context": "unique"},
        )
        assert resolve_memory_id_prefix(db_conn, "abc12345") == mem_id


class TestUpdateMemory:
    def test_updates_fields(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Old"})
        assert update_memory(db_conn, mem_id, {"context": "New"}) is True
        assert get_memory(db_conn, mem_id)["context"] == "New"


class TestDeleteMemory:
    def test_deletes_existing(self, db_conn):
        from scripts.database import create_memory, delete_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        assert delete_memory(db_conn, mem_id) is True
        assert get_memory(db_conn, mem_id) is None


class TestPACTMemoryDeleteVecTableHandling:
    """Locks the narrowed-except contract on PACTMemory.delete's vec_memories cleanup.

    PACTMemory.delete attempts `DELETE FROM vec_memories WHERE memory_id = ?`
    AFTER deleting the row from the primary memories table. The vec_memories
    table only exists when sqlite-vec is loaded; absence is the legitimate
    common case. A bare `except Exception: pass` here would also swallow
    "database is locked", "disk I/O error", and other surface-or-surface-this
    failures. The narrowed `except sqlite3.OperationalError if "no such table"
    in str(e)` lets the legitimate-FTS-absent path stay silent while
    surfacing every other operational error.
    """

    def test_delete_silently_handles_missing_vec_memories_table(self, tmp_path):
        """Real-DB path: schema has no vec_memories table; delete must succeed."""
        from scripts.memory_api import PACTMemory

        db_path = tmp_path / "vec_absent.db"
        conn = sqlite3.connect(str(db_path))
        create_test_schema(conn)
        conn.close()
        # Schema does NOT include vec_memories — DELETE FROM vec_memories will
        # raise sqlite3.OperationalError("no such table: vec_memories"), which
        # the narrowed except must swallow.

        with patch("scripts.memory_api._ensure_ready"), \
             patch("scripts.memory_api.sync_to_claude_md"), \
             patch.object(PACTMemory, "_store_embedding", autospec=True):
            memory = PACTMemory(
                project_id="vec-absent-test",
                session_id="s1",
                db_path=db_path,
            )
            mem_id = memory.save({"context": "row to delete"})
            # Delete must succeed despite vec_memories being absent.
            resolved = memory.delete(mem_id)
            assert resolved == mem_id
            # Verify the primary-table delete actually landed.
            assert memory.get(mem_id) is None


class TestListMemories:
    def test_lists_all(self, db_conn):
        from scripts.database import create_memory, list_memories
        create_memory(db_conn, {"context": "A"})
        create_memory(db_conn, {"context": "B"})
        assert len(list_memories(db_conn)) == 2


# ---------------------------------------------------------------------------
# Text search
# ---------------------------------------------------------------------------

class TestSearchMemoriesByText:
    def test_finds_by_context(self, db_conn):
        from scripts.database import create_memory, search_memories_by_text
        create_memory(db_conn, {"context": "Working on authentication"})
        create_memory(db_conn, {"context": "Working on payments"})
        assert len(search_memories_by_text(db_conn, "authentication")) == 1


class TestTheTiebreakReachesTheEngine:
    """Both ordering queries ASK the engine for `rowid DESC`, on any plan.

    PLAN-INDEPENDENT BY CONSTRUCTION: these read the statement the driver is
    handed and never look at rows, so they hold under every plan -- including
    the ascending-index plan on which deleting the tiebreak returns identical
    rows, where no output-based check can see the deletion at all. That is the
    gap these close, and it is why the arms above cannot close it themselves.

    WHAT THIS PROVES: the clause is asked for. WHAT IT DOES NOT: that the
    clause has any effect, or that the ordering is correct. Those remain with
    the output arms, which are not redundant -- they pin what the ordering
    DOES under today's plan, which this cannot.

    ITS OWN CONDITION, stated because removing a contingency must not add a
    quieter one: this pins the clause AS TEXT, whitespace-normalised. A
    deliberate rewrite of the ordering into an equivalent form must update
    these arms; they will redden on such a rewrite though behaviour is right.
    """

    def _order_by_of_the_one_ordering_statement(self, tmp_path, call):
        """The ORDER BY tail of the single ordering statement `call` issued.

        Asserting the count is what makes the result attributable: with two
        sites carrying the identical clause, a check that searched everything
        traced could pass on evidence produced by the other query.
        """
        from scripts.database import ensure_initialized
        conn = sqlite3.connect(str(tmp_path / "traced.db"))
        ensure_initialized(conn)
        seen = []
        conn.set_trace_callback(seen.append)
        call(conn)
        conn.set_trace_callback(None)
        ordered = [" ".join(s.split()) for s in seen if "ORDER BY" in s]
        assert len(ordered) == 1, (
            "this call must issue exactly one ordering statement or the "
            f"assertion cannot be attributed to it; got {len(ordered)}"
        )
        return ordered[0].split("ORDER BY", 1)[1].strip()

    def test_the_paged_list_asks_for_the_tiebreak(self, tmp_path):
        from scripts.database import list_memories
        clause = self._order_by_of_the_one_ordering_statement(
            tmp_path, lambda c: list_memories(c, project_id="p", limit=3)
        )
        assert clause.startswith("created_at DESC, rowid DESC"), clause


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

class TestMaintenance:
    def test_get_memory_count(self, db_conn):
        from scripts.database import create_memory, get_memory_count
        assert get_memory_count(db_conn) == 0
        create_memory(db_conn, {"context": "A"})
        create_memory(db_conn, {"context": "B"})
        assert get_memory_count(db_conn) == 2


# ---------------------------------------------------------------------------
# Bug 2 fix (#374) — unknown column keys raise ValueError instead of silent drop
# ---------------------------------------------------------------------------
#
# Before PR #374's commit 1, update_memory silently filtered unknown dict keys
# via a hardcoded ALLOWED_COLUMNS set. A typo in the payload (e.g. `descrption`
# instead of `description`, or `description` when the real field is `notes`)
# would disappear with no warning — the CLI returned {"ok": true} on silent
# data loss.
#
# New contract: unknown top-level keys raise ValueError BEFORE any DB write,
# with an error message that names the offending key AND the allowed-field set.
# The raise happens outside any transaction so no partial state is left
# behind (atomicity).

class TestUnknownColumnsRaise:
    """Verify update_memory and save_memory (create_memory) raise on unknown keys."""

    def test_update_raises_on_unknown_key(self, db_conn):
        """Unknown key in update payload must raise ValueError (bug 2 fix)."""
        from scripts.database import create_memory, update_memory
        mem_id = create_memory(db_conn, {"context": "Original"})
        with pytest.raises(ValueError, match="Unknown memory field"):
            update_memory(db_conn, mem_id, {"bogus_field": "x"})


# ---------------------------------------------------------------------------
# Bug 3 part 2 (#374) — save-path strict-on-ingress for sub-object keys
# ---------------------------------------------------------------------------
#
# The first bug-3 fix wired strict=True into _canonicalize_dict_item but
# only called it from update_memory's merge path. The save ingress
# (create_memory) bypassed it entirely — _serialize_json_fields was a raw
# json.dumps passthrough, so dict-list items with unknown sub-object keys
# (e.g. active_tasks=[{"id":"abc","subject":"foo"}]) were silently stored
# as verbatim junk that read back as empty dataclass instances via the
# lenient read path. Discovered by the post-CODE harvester dogfooding the
# new contract.
#
# Fix: create_memory now runs _canonicalize_dict_item over DICT_LIST_FIELDS
# BEFORE _serialize_json_fields. Validation-before-write invariant applies
# symmetrically to save and update: a raise leaves the DB untouched.


class TestBug3SavePathStrict:
    def test_save_raises_on_unknown_subobject_key_in_entities(self, db_conn):
        """Symmetric with the existing update-path test. The save ingress
        must raise ValueError on unknown Entity sub-object keys."""
        from scripts.database import create_memory
        with pytest.raises(ValueError, match="Unknown keys for Entity"):
            create_memory(db_conn, {
                "context": "A",
                "entities": [{"name": "Redis", "description": "cache"}],
            })


# ---------------------------------------------------------------------------
# Bug 1 fix (#374) — additive-by-default list merge with content-hash dedup
# ---------------------------------------------------------------------------
#
# Before PR #374, updating a list field replaced it wholesale: passing
# {"lessons_learned": ["c"]} to a memory whose lessons_learned was
# ["a", "b"] would overwrite it to ["c"], silently losing the two existing
# items. The pinned recovery protocol told every session to
# read-merge-write-back, which was a clunky workaround.
#
# New default behavior: list fields append with content-hash dedup.
# Repeat calls are idempotent. The legacy wholesale-replace is available
# via replace=True.

class TestBug1AdditiveListMerge:
    """Additive merge + dedup for list fields. Default behavior."""

    # --- String list fields -----------------------------------------------

    def test_append_string_list_to_existing(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"lessons_learned": ["a", "b"]})
        update_memory(db_conn, mem_id, {"lessons_learned": ["c"]})
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["a", "b", "c"]


    # --- Dict list fields -------------------------------------------------


class TestBug1ReplaceKwarg:
    """replace=True restores legacy wholesale-replace behavior."""

    def test_replace_clobbers_string_list(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"lessons_learned": ["a", "b", "c"]})
        update_memory(
            db_conn, mem_id,
            {"lessons_learned": ["new"]},
            replace=True,
        )
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["new"]


class TestBug1ScalarVsListSemantics:
    """Scalar fields still replace even when list fields merge."""

    def test_scalar_fields_replace_not_merge(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "old", "goal": "old goal"})
        update_memory(db_conn, mem_id, {"context": "new"})
        result = get_memory(db_conn, mem_id)
        assert result["context"] == "new"
        assert result["goal"] == "old goal"


class TestBug1HashStability:
    """Content-hash key properties."""

    def test_hash_stable_across_calls(self):
        from scripts.database import _content_hash
        h1 = _content_hash("lessons_learned", "abc")
        h2 = _content_hash("lessons_learned", "abc")
        assert h1 == h2


class TestBug1TransactionalAtomicity:
    """Rejected updates must leave the row unchanged."""

    def test_sub_object_validation_failure_rolls_back(self, db_conn):
        """
        Passing a mix of a valid list item and an invalid one (unknown
        sub-object key) must raise ValueError WITHOUT writing ANY items
        to the row. Validation runs outside the BEGIN IMMEDIATE so the
        transaction is never started.
        """
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"entities": [{"name": "Existing"}]})
        with pytest.raises(ValueError):
            update_memory(db_conn, mem_id, {
                "entities": [
                    {"name": "Good"},
                    {"name": "Bad", "bogus": "y"},
                ],
            })
        result = get_memory(db_conn, mem_id)
        assert result["entities"] == [{"name": "Existing"}]


# ---------------------------------------------------------------------------
# TOCTOU mitigation in get_connection (Item 11)
# ---------------------------------------------------------------------------

class TestTOCTOUMitigation:
    """Verify atomic file pre-creation and WAL sidecar permission hardening."""

    def test_new_db_file_created_with_600(self, tmp_path):
        """New database file should be created with mode 0o600 via O_CREAT|O_EXCL."""
        import stat
        import sqlite3 as stdlib_sqlite3

        db_path = tmp_path / "new.db"
        assert not db_path.exists()

        with patch("scripts.database.sqlite3", stdlib_sqlite3):
            from scripts.database import get_connection
            conn = get_connection(db_path=db_path)
            conn.close()

        assert db_path.exists()
        file_mode = stat.S_IMODE(db_path.stat().st_mode)
        assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"


# ---------------------------------------------------------------------------
# Bug 1 (#374) — COMPREHENSIVE TEST LAYER (TEST phase, beyond smoke tests)
# ---------------------------------------------------------------------------
#
# These tests complement the 46 smoke + unit tests that shipped alongside the
# seven-commit fix. They target coder-flagged uncertainties the smoke layer
# could not cover:
#
#   1. BEGIN IMMEDIATE concurrent-writer race (architect §7.3, MEDIUM)
#   2. Legacy-row graceful degradation on read + update merge (MEDIUM)
#   3. Content-hash stability under non-JSON-serializable values (LOW)
#   4. Validation-before-transaction invariant (architect §1.3, NON-NEGOTIABLE)
#      — strengthened with conn.total_changes instrumentation to prove no
#      write was attempted, not merely rolled back.
#
# Each test class here is self-contained and does NOT use the shared `db_conn`
# fixture where real-database semantics matter (concurrency tests need a real
# on-disk WAL-mode database with `ensure_initialized` actually running, not
# patched out).


class TestBug1ConcurrentWriterRace:
    """
    Architect §7.3 / coder [MEDIUM] uncertainty — verify BEGIN IMMEDIATE
    serialization of two concurrent update_memory calls on the same row.

    Why this test matters: the merge algorithm reads the existing list, appends
    the incoming list, dedups, and writes. If two writers interleave between
    each other's SELECT and UPDATE, one merge can clobber the other. BEGIN
    IMMEDIATE takes the sqlite write lock at BEGIN (not at first write), so
    the second writer blocks until the first commits — guaranteeing each
    merge sees the previous merge's committed state.

    Fixture: real on-disk database with WAL mode, two live connections on
    separate threads, threading.Barrier for simultaneous kick-off. A 5-second
    join timeout prevents runaway hangs.
    """

    def _make_real_db(self, tmp_path):
        """Build a real WAL-mode DB and seed one memory row."""
        from scripts import database as db_mod
        db_path = tmp_path / "concurrent.db"
        # Use the module's own sqlite3 binding (pysqlite3 if present) — no
        # patching, because unittest.mock.patch is NOT thread-safe and
        # cross-thread teardown can corrupt the module state for other tests.
        conn = db_mod.get_connection(db_path=db_path)
        db_mod.init_schema(conn)
        mem_id = db_mod.create_memory(conn, {"lessons_learned": ["seed"]})
        conn.close()
        return db_path, mem_id

    def test_two_writers_both_merges_preserved(self, tmp_path):
        """
        Two threads each add a disjoint pair of lessons. After both finish,
        the row must contain ALL items (seed + 4 added), proving neither
        thread's merge clobbered the other's.
        """
        import threading
        from scripts import database as db_mod

        db_path, mem_id = self._make_real_db(tmp_path)

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def writer(items: list[str]) -> None:
            try:
                conn = db_mod.get_connection(db_path=db_path)
                try:
                    barrier.wait(timeout=5)
                    db_mod.update_memory(
                        conn, mem_id, {"lessons_learned": items},
                    )
                finally:
                    conn.close()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t_a = threading.Thread(target=writer, args=(["a1", "a2"],))
        t_b = threading.Thread(target=writer, args=(["b1", "b2"],))
        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        assert not t_a.is_alive(), "writer A stalled (BEGIN IMMEDIATE deadlock?)"
        assert not t_b.is_alive(), "writer B stalled (BEGIN IMMEDIATE deadlock?)"
        assert not errors, f"writers raised: {errors!r}"

        # Reopen and verify union is preserved.
        conn = db_mod.get_connection(db_path=db_path)
        try:
            row = db_mod.get_memory(conn, mem_id)
        finally:
            conn.close()

        lessons = row["lessons_learned"]
        assert "seed" in lessons, "initial seed was clobbered"
        assert "a1" in lessons and "a2" in lessons, f"writer A lost: {lessons}"
        assert "b1" in lessons and "b2" in lessons, f"writer B lost: {lessons}"
        assert len(lessons) == 5, f"expected 5 unique items, got {lessons}"


class TestBug1LegacyRowGracefulDegradation:
    """
    Coder [MEDIUM] uncertainty — legacy DB rows with stray sub-object keys
    must remain readable AND remain updateable via the additive merge path.

    - Read path: MemoryObject.from_dict calls Entity.from_dict with strict=False,
      which drops unknown keys and emits a logger.warning.
    - Update merge path: _merge_with_dedup's try/except→repr() fallback fires
      when the existing row contains an un-canonicalizable item, keeping the
      legacy item verbatim rather than raising.
    """

    def test_legacy_row_with_stray_key_still_readable(self, db_conn, caplog):
        """
        Inject a row directly via raw SQL with a stray sub-object key in the
        entities JSON. PACTMemory.get should succeed, the stray key should be
        dropped, and a WARNING log should be emitted.
        """
        import logging
        from scripts.database import get_memory
        from scripts.models import MemoryObject

        # Raw insert bypassing create_memory (which would strict-validate).
        payload_entities = json.dumps(
            [{"name": "LegacyEntity", "legacy_field": "should-be-dropped"}]
        )
        db_conn.execute(
            """
            INSERT INTO memories (id, entities, created_at, updated_at)
            VALUES (?, ?, datetime('now'), datetime('now'))
            """,
            ("legacy-1", payload_entities),
        )
        db_conn.commit()

        # Read path: _deserialize_json_fields runs, then whatever calls
        # MemoryObject.from_dict. get_memory itself returns a raw dict, so
        # exercise the full model path by calling MemoryObject.from_dict.
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="scripts.models"):
            raw = get_memory(db_conn, "legacy-1")
            memory_obj = MemoryObject.from_dict(raw)

        assert memory_obj.entities, "entities list empty after legacy read"
        assert memory_obj.entities[0].name == "LegacyEntity"
        assert not hasattr(memory_obj.entities[0], "legacy_field"), (
            "stray key leaked onto Entity instance"
        )
        assert any(
            "legacy_field" in rec.getMessage() or "Dropping unknown keys" in rec.getMessage()
            for rec in caplog.records
        ), f"expected WARNING about stray key, got records: {[r.getMessage() for r in caplog.records]}"


class TestBug1ContentHashEdgeCases:
    """
    Coder [LOW] uncertainty — the `default=str` fallback in the canonical JSON
    encoder for `_content_hash` must produce stable hex output even when the
    payload contains non-JSON-native types.

    Because Entity/Decision/TaskItem dataclasses only accept string-valued
    fields, the dict-list path canonicalizes through dataclass.to_dict() before
    json.dumps ever sees it — exotic types cannot reach the encoder through
    that path. We instead exercise:
      (a) the string-list hash path with exotic string reprs (stable under
          repeated calls);
      (b) the dict-list path via a stray-key dict that survives lenient
          canonicalization;
      (c) a direct json.dumps call matching the encoder parameters, proving
          default=str is in effect as documented.
    """

    def test_string_list_hash_stable_for_unicode(self):
        from scripts.database import _content_hash
        s = "café — Übung / 日本語 / emoji 🦀"
        assert _content_hash("lessons_learned", s) == _content_hash(
            "lessons_learned", s,
        )


class TestBug1ValidationBeforeTransactionInvariant:
    """
    Architect §1.3 NON-NEGOTIABLE — validation must run BEFORE the transaction
    opens. Three properties to verify for every rejected update:

      1. ValueError is raised.
      2. The row is byte-identical to its pre-call state.
      3. conn.total_changes is unchanged post-call, proving no write was even
         attempted (strictly stronger than "the transaction rolled back").
    """

    def _snapshot_row(self, conn, mem_id):
        cursor = conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,))
        row = cursor.fetchone()
        assert row is not None
        return tuple(row)  # sqlite3.Row is not directly hashable but indexable

    def test_unknown_top_level_key_no_transaction(self, db_conn):
        from scripts.database import create_memory, update_memory

        mem_id = create_memory(
            db_conn, {"context": "stable", "lessons_learned": ["a", "b"]}
        )
        pre_snapshot = self._snapshot_row(db_conn, mem_id)
        pre_changes = db_conn.total_changes

        with pytest.raises(ValueError, match="Unknown memory field"):
            update_memory(db_conn, mem_id, {"context": "new", "bogus": "x"})

        post_snapshot = self._snapshot_row(db_conn, mem_id)
        post_changes = db_conn.total_changes

        assert post_snapshot == pre_snapshot, (
            "row mutated despite ValueError — validation ran inside transaction?"
        )
        assert post_changes == pre_changes, (
            f"conn.total_changes moved ({pre_changes}→{post_changes}) — a write "
            "was attempted before validation rejected the payload"
        )


class TestBug1EmptyListBranches:
    """Empty list-field update branches (#374 R2-3c).

    Two distinct documented branches in update_memory's list-field loop —
    neither had direct coverage in round 1:

    1. replace=True + empty list  → field is CLEARED (load-bearing contract
       at database.py:866-869). This is the "clear this field" semantic.
    2. replace=False + empty list → SHORT-CIRCUIT (F6 at database.py:874-876).
       The loop `continue`s, nothing enters normalized[], and if only that
       key was touched the whole update becomes a no-op that returns True
       without bumping updated_at.
    """

    def _get_updated_at(self, db_conn, mem_id):
        """Read updated_at directly (get_memory strips it via MemoryObject)."""
        row = db_conn.execute(
            "SELECT updated_at FROM memories WHERE id = ?", (mem_id,),
        ).fetchone()
        return row["updated_at"] if row else None

    def test_replace_true_with_empty_list_clears_field(self, db_conn):
        """replace=True + [] clears the field (docstring:866-869)."""
        from scripts.database import create_memory, update_memory, get_memory

        mem_id = create_memory(
            db_conn, {"lessons_learned": ["item1", "item2"]},
        )
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["item1", "item2"]

        result = update_memory(
            db_conn, mem_id, {"lessons_learned": []}, replace=True,
        )

        assert result is True
        assert get_memory(db_conn, mem_id)["lessons_learned"] == [], (
            "replace=True with empty list must CLEAR the field — this is "
            "the documented 'clear this field' contract at database.py:866-869"
        )


class TestBug1ContentNoopSuppressesUpdatedAt:
    """Content no-op suppresses updated_at bump (#374 R2-4).

    When an additive update's incoming items are ALL already-present
    duplicates, the content-hash dedup reduces the merge result to the
    existing list. The current round-1 behavior still writes the row
    (bumping updated_at) because the short-circuit only skipped the empty
    incoming list, not the empty-merge-diff case.

    This test expresses the DESIRED behavior after task #10's fix: when
    the additive merge produces no new content, skip the UPDATE entirely
    so updated_at stays stable.

    If task #10 has not yet landed, this test WILL fail. That's expected —
    it's the red-before-green for R2-4.
    """

    def _get_updated_at(self, db_conn, mem_id):
        row = db_conn.execute(
            "SELECT updated_at FROM memories WHERE id = ?", (mem_id,),
        ).fetchone()
        return row["updated_at"] if row else None

    def test_additive_all_duplicates_suppresses_updated_at(self, db_conn):
        """Calling update with an item that already exists is a true no-op."""
        from scripts.database import create_memory, update_memory, get_memory

        mem_id = create_memory(db_conn, {"lessons_learned": ["item1"]})
        pre_updated_at = self._get_updated_at(db_conn, mem_id)

        # Send the same item again — after dedup, merge result == existing.
        result = update_memory(db_conn, mem_id, {"lessons_learned": ["item1"]})

        assert result is True, (
            "semantically successful no-op update must return True"
        )

        post = get_memory(db_conn, mem_id)
        assert post["lessons_learned"] == ["item1"], (
            "no duplication: dedup must collapse the incoming duplicate"
        )

        post_updated_at = self._get_updated_at(db_conn, mem_id)
        assert post_updated_at == pre_updated_at, (
            f"updated_at must NOT be bumped when the additive merge "
            f"produces no new content (pre={pre_updated_at!r}, "
            f"post={post_updated_at!r}). Task #10 should skip the UPDATE "
            "statement when the merged result equals the existing list."
        )


class TestBug1EmbeddingSkipOnIdempotentUpdate:
    """M7 idempotent embedding-skip (#374 R2-3b).

    PACTMemory.update snapshots CONTENT_FIELDS before calling update_memory.
    If the additive merge produces no diff (repeat call with identical
    payload), _content_fields_changed returns False and _store_embedding is
    NOT called a second time.

    Regression-guards commit 2336d2d: "fix(pact-memory): skip embedding
    regen on idempotent updates".
    """

    def test_repeat_update_with_same_payload_skips_embedding_regen(
        self, tmp_path,
    ):
        """Second identical update must NOT call _store_embedding."""
        from scripts.memory_api import PACTMemory

        db_path = tmp_path / "embed_skip_test.db"
        conn = sqlite3.connect(str(db_path))
        create_test_schema(conn)
        conn.close()

        with patch("scripts.memory_api._ensure_ready"), \
             patch("scripts.memory_api.sync_to_claude_md"), \
             patch.object(
                 PACTMemory, "_store_embedding", autospec=True,
             ) as mock_store:
            memory = PACTMemory(
                project_id="test-project",
                session_id="test-session",
                db_path=db_path,
            )

            # Create a memory with CONTENT_FIELDS content. The save() path
            # also calls _store_embedding — record that baseline so we can
            # assert the *update*-side call count.
            memory_id = memory.save({
                "context": "embedding skip test",
                "lessons_learned": ["lesson-A"],
            })
            save_side_calls = mock_store.call_count

            # First update: adds a new item — merge produces a diff, so
            # _store_embedding SHOULD be called.
            memory.update(memory_id, {"lessons_learned": ["lesson-B"]})
            first_update_calls = mock_store.call_count - save_side_calls
            assert first_update_calls == 1, (
                f"first update with new content should regen embedding "
                f"(got {first_update_calls} calls)"
            )

            # Second update with the SAME payload: all items already exist,
            # merge result equals existing, _content_fields_changed returns
            # False, and _store_embedding MUST NOT be called again.
            memory.update(memory_id, {"lessons_learned": ["lesson-B"]})
            second_update_calls = (
                mock_store.call_count - save_side_calls - first_update_calls
            )
            assert second_update_calls == 0, (
                f"repeat update with identical payload must skip embedding "
                f"regen (got {second_update_calls} extra calls — M7 "
                "optimization is not firing)"
            )
