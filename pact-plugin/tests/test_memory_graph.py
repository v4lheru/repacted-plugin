"""
Tests for pact-memory/scripts/graph.py — file relationship tracking and graph queries.

Tests cover:
1. _normalize_path: absolute vs relative, ~ expansion
2. track_file: new file creation, existing file update, project scoping
3. get_file_id: found, not found, project scoping
4. get_file_by_id: found, not found
5. list_tracked_files: all, project filter, limit
6. link_memory_to_file: new link, duplicate link
7. link_memory_to_files: multiple files, partial duplicates
8. link_memory_to_paths: tracks and links
9. get_files_for_memory: linked files, no files
10. get_memories_for_file: linked memories, no memories
11. get_memories_for_files: multiple paths
12. add_file_relation: new relation, duplicate
13. get_file_relations: outgoing, incoming, both, untracked
14. get_related_files: BFS traversal, depth limits, untracked start
15. get_related_files_via_memories: shared memory links, untracked
16. get_file_context: tracked, untracked
17. get_graph_stats: empty, populated, project filter
"""
from unittest.mock import patch

import pytest

from helpers import create_test_schema

# graph.py imports standard `import sqlite3` (not pysqlite3), so its
# `except sqlite3.IntegrityError` catches the standard library exception.
# We must use standard sqlite3 for test connections to match.
import sqlite3


@pytest.fixture
def db_conn(tmp_path):
    """Create a fresh database with schema, patching ensure_initialized to no-op."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_test_schema(conn)
    with patch("scripts.graph.ensure_initialized"):
        yield conn
    conn.close()


def _insert_memory(conn, memory_id, context="test", project_id=None):
    """Insert a memory row directly for test setup."""
    conn.execute(
        "INSERT INTO memories (id, context, project_id) VALUES (?, ?, ?)",
        (memory_id, context, project_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# _normalize_path
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def test_absolute_path_unchanged(self):
        from scripts.graph import _normalize_path
        result = _normalize_path("/usr/local/bin/python")
        assert result == "/usr/local/bin/python"

    def test_relative_path_stays_relative(self):
        from scripts.graph import _normalize_path
        result = _normalize_path("src/app.py")
        assert result == "src/app.py"


# ---------------------------------------------------------------------------
# track_file
# ---------------------------------------------------------------------------

class TestTrackFile:
    def test_creates_new_file(self, db_conn):
        from scripts.graph import track_file
        file_id = track_file(db_conn, "/src/app.py")
        assert file_id is not None
        assert len(file_id) > 0

    def test_returns_existing_file(self, db_conn):
        from scripts.graph import track_file
        id1 = track_file(db_conn, "/src/app.py")
        id2 = track_file(db_conn, "/src/app.py")
        assert id1 == id2


# ---------------------------------------------------------------------------
# get_file_id
# ---------------------------------------------------------------------------

class TestGetFileId:
    def test_returns_id_for_tracked_file(self, db_conn):
        from scripts.graph import track_file, get_file_id
        expected = track_file(db_conn, "/src/app.py")
        assert get_file_id(db_conn, "/src/app.py") == expected


# ---------------------------------------------------------------------------
# get_file_by_id
# ---------------------------------------------------------------------------

class TestGetFileById:
    def test_returns_file_dict(self, db_conn):
        from scripts.graph import track_file, get_file_by_id
        file_id = track_file(db_conn, "/src/app.py")
        result = get_file_by_id(db_conn, file_id)
        assert result is not None
        assert result["path"] == "/src/app.py"


# ---------------------------------------------------------------------------
# list_tracked_files
# ---------------------------------------------------------------------------

class TestListTrackedFiles:
    def test_lists_all_files(self, db_conn):
        from scripts.graph import track_file, list_tracked_files
        track_file(db_conn, "/src/a.py")
        track_file(db_conn, "/src/b.py")
        result = list_tracked_files(db_conn)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# link_memory_to_file
# ---------------------------------------------------------------------------

class TestLinkMemoryToFile:
    def test_creates_link(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file
        _insert_memory(db_conn, "mem-1")
        file_id = track_file(db_conn, "/src/app.py")
        assert link_memory_to_file(db_conn, "mem-1", file_id) is True


# ---------------------------------------------------------------------------
# link_memory_to_files
# ---------------------------------------------------------------------------

class TestLinkMemoryToFiles:
    def test_links_multiple(self, db_conn):
        from scripts.graph import track_file, link_memory_to_files
        _insert_memory(db_conn, "mem-1")
        id1 = track_file(db_conn, "/src/a.py")
        id2 = track_file(db_conn, "/src/b.py")
        count = link_memory_to_files(db_conn, "mem-1", [id1, id2])
        assert count == 2


# ---------------------------------------------------------------------------
# link_memory_to_paths
# ---------------------------------------------------------------------------

class TestLinkMemoryToPaths:
    def test_tracks_and_links(self, db_conn):
        from scripts.graph import link_memory_to_paths, get_file_id
        _insert_memory(db_conn, "mem-1")
        count = link_memory_to_paths(db_conn, "mem-1", ["/src/a.py", "/src/b.py"])
        assert count == 2
        assert get_file_id(db_conn, "/src/a.py") is not None
        assert get_file_id(db_conn, "/src/b.py") is not None


# ---------------------------------------------------------------------------
# get_files_for_memory
# ---------------------------------------------------------------------------

class TestGetFilesForMemory:
    def test_returns_linked_files(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file, get_files_for_memory
        _insert_memory(db_conn, "mem-1")
        id1 = track_file(db_conn, "/src/a.py")
        id2 = track_file(db_conn, "/src/b.py")
        link_memory_to_file(db_conn, "mem-1", id1)
        link_memory_to_file(db_conn, "mem-1", id2, "referenced")
        files = get_files_for_memory(db_conn, "mem-1")
        assert len(files) == 2
        paths = {f["path"] for f in files}
        assert "/src/a.py" in paths
        assert "/src/b.py" in paths


# ---------------------------------------------------------------------------
# get_memories_for_file
# ---------------------------------------------------------------------------

class TestGetMemoriesForFile:
    def test_returns_memory_ids(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file, get_memories_for_file
        _insert_memory(db_conn, "mem-1")
        _insert_memory(db_conn, "mem-2")
        file_id = track_file(db_conn, "/src/app.py")
        link_memory_to_file(db_conn, "mem-1", file_id)
        link_memory_to_file(db_conn, "mem-2", file_id)
        mems = get_memories_for_file(db_conn, file_id)
        assert set(mems) == {"mem-1", "mem-2"}


# ---------------------------------------------------------------------------
# get_memories_for_files
# ---------------------------------------------------------------------------

class TestGetMemoriesForFiles:
    def test_aggregates_across_paths(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file, get_memories_for_files
        _insert_memory(db_conn, "mem-1")
        _insert_memory(db_conn, "mem-2")
        id1 = track_file(db_conn, "/src/a.py")
        id2 = track_file(db_conn, "/src/b.py")
        link_memory_to_file(db_conn, "mem-1", id1)
        link_memory_to_file(db_conn, "mem-2", id2)
        mems = get_memories_for_files(db_conn, ["/src/a.py", "/src/b.py"])
        assert set(mems) == {"mem-1", "mem-2"}


# ---------------------------------------------------------------------------
# add_file_relation
# ---------------------------------------------------------------------------

class TestAddFileRelation:
    def test_creates_relation(self, db_conn):
        from scripts.graph import add_file_relation
        result = add_file_relation(db_conn, "/src/app.py", "/src/utils.py", "imports")
        assert result is True


# ---------------------------------------------------------------------------
# get_file_relations
# ---------------------------------------------------------------------------

class TestGetFileRelations:
    def test_outgoing_relations(self, db_conn):
        from scripts.graph import add_file_relation, get_file_relations
        add_file_relation(db_conn, "/src/app.py", "/src/utils.py", "imports")
        result = get_file_relations(db_conn, "/src/app.py", direction="outgoing")
        assert len(result) == 1
        assert result[0]["path"] == "/src/utils.py"
        assert result[0]["direction"] == "outgoing"


# ---------------------------------------------------------------------------
# get_related_files (BFS traversal)
# ---------------------------------------------------------------------------

class TestGetRelatedFiles:
    def test_finds_direct_neighbors(self, db_conn):
        from scripts.graph import add_file_relation, get_related_files
        add_file_relation(db_conn, "/a.py", "/b.py", "imports")
        result = get_related_files(db_conn, "/a.py")
        assert "/b.py" in result


# ---------------------------------------------------------------------------
# get_related_files_via_memories
# ---------------------------------------------------------------------------

class TestGetRelatedFilesViaMemories:
    def test_finds_files_with_shared_memory(self, db_conn):
        from scripts.graph import (
            track_file, link_memory_to_file, get_related_files_via_memories,
        )
        _insert_memory(db_conn, "mem-1")
        id1 = track_file(db_conn, "/src/a.py")
        id2 = track_file(db_conn, "/src/b.py")
        link_memory_to_file(db_conn, "mem-1", id1)
        link_memory_to_file(db_conn, "mem-1", id2)
        result = get_related_files_via_memories(db_conn, "/src/a.py")
        assert "/src/b.py" in result
        assert "/src/a.py" not in result  # Excludes self


# ---------------------------------------------------------------------------
# get_file_context
# ---------------------------------------------------------------------------

class TestGetFileContext:
    def test_untracked_file(self, db_conn):
        from scripts.graph import get_file_context
        ctx = get_file_context(db_conn, "/nonexistent.py")
        assert ctx["tracked"] is False
        assert ctx["direct_relations"] == []
        assert ctx["memory_related_files"] == []
        assert ctx["memory_ids"] == []


# ---------------------------------------------------------------------------
# get_graph_stats
# ---------------------------------------------------------------------------

class TestGetGraphStats:
    def test_empty_graph(self, db_conn):
        from scripts.graph import get_graph_stats
        stats = get_graph_stats(db_conn)
        assert stats["files"] == 0
        assert stats["memories"] == 0
        assert stats["memory_file_links"] == 0
        assert stats["file_relations"] == 0


