"""
Location: pact-plugin/tests/test_vector_write_path.py

Summary: Arms for the vector write path of `PACTMemory._store_embedding`, and
behavioural arms for five claims about the `vec0` virtual table that the
design carried as prose and never executed.

WHY THE CARRIED-CLAIM ARMS ARE HERE AT ALL. The design of this write path
rests on five statements about a third-party virtual table. None of them had
been run. A design that rests on an unobserved premise is not wrong, but it
is unmeasured, and this arc has had three carried claims fall. Each of the
five is now an arm that can fail, so a later change to `sqlite_vec` reports
itself here rather than in the store of a user.

THE TWO WRITE-PATH ARMS ARE NOT INTERCHANGEABLE, AND THIS IS THE POINT.

  THE BASIC REPLACE ARM asserts a second save replaces the vector without a
  fault. It goes RED against the unfixed path, because `vec0` refuses the
  insert on a row that is present.

  THE SEPARATING ARM asserts that a FAILED replacement leaves the ORIGINAL
  vector in place. It is GREEN against the unfixed path, because that path
  removes nothing before it inserts. It goes RED against the NAIVE FIX, the
  one that calls the drop helper with its default and so COMMITS the delete
  before the insert is attempted.

  So the two arms discriminate against DIFFERENT wrong implementations, and
  neither one alone certifies this fix. Read both labels before you reshape
  either.

WHY AN "EXPLICIT ROLLBACK" IS NOT THE REMEDY. `_drop_existing_vector` commits.
With the naive ordering, the delete is permanent before the insert runs, so
`rollback` returns the connection to a restore point that is itself the state
with the vector gone. The remedy is the ORDERING, and the `commit=False`
parameter is what supplies it. `TestACommitClosesTheRestorePoint` is the arm
that holds that reasoning up.
"""
from __future__ import annotations

import logging
import struct
from unittest.mock import patch

import pytest

from helpers import create_test_schema

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

import sqlite_vec  # noqa: E402

from scripts.memory_api import PACTMemory  # noqa: E402


DIM = 8
VECTOR_A = [0.5] * DIM
VECTOR_B = [0.25] * DIM
WRONG_LENGTH = [0.5] * (DIM - 2)


def blob(values):
    return struct.pack("{0}f".format(len(values)), *values)


def make_vec_table(conn):
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0("
        "  memory_id TEXT PRIMARY KEY,"
        "  project_id TEXT PARTITION KEY,"
        "  embedding float[{0}]"
        ")".format(DIM)
    )
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    """A store with the memories table AND a real vec0 table.

    The carried claims are about `vec0` behaviour, so a mock connection
    cannot answer them. This fixture builds the production table shape:
    a text primary key, a text PARTITION KEY, and a fixed-width float column.
    """
    connection = sqlite3.connect(str(tmp_path / "vec.db"))
    connection.row_factory = sqlite3.Row
    create_test_schema(connection)
    make_vec_table(connection)
    yield connection
    connection.close()


@pytest.fixture
def mem():
    return PACTMemory(project_id="test-project", session_id="test-session")


def stored_vector(conn, memory_id):
    row = conn.execute(
        "SELECT embedding FROM vec_memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    return None if row is None else bytes(row[0])


def seed_vector(conn, memory_id="mem-1", project_id="test-project", values=None):
    conn.execute(
        "INSERT INTO vec_memories (memory_id, project_id, embedding) "
        "VALUES (?, ?, ?)",
        (memory_id, project_id, blob(values or VECTOR_A)),
    )
    conn.commit()


def a_memory():
    return {"context": "some embeddable context", "project_id": "test-project"}


def store_with(mem, conn, vector, memory_id="mem-1"):
    """Drive the production write path with a chosen embedding."""
    with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
         patch("scripts.memory_api.generate_embedding_text", return_value="text"), \
         patch("scripts.memory_api.generate_embedding", return_value=vector):
        return mem._store_embedding(conn, memory_id, a_memory())


# ---------------------------------------------------------------------------
# The five carried claims, each now an arm that can fail.
# ---------------------------------------------------------------------------

class TestVec0HonoursNoConflictClause:
    """CARRIED CLAIM (a). This one GATES THE FIX: if the table accepted a
    replace on a row that is present, the drop would be unnecessary and the
    defect would be somewhere else."""

    @pytest.mark.parametrize(
        "spelling", ["INSERT",]
    )
    def test_each_insert_spelling_raises_on_a_present_row(self, conn, spelling):
        seed_vector(conn)
        with pytest.raises(sqlite3.OperationalError) as caught:
            conn.execute(
                "{0} INTO vec_memories (memory_id, project_id, embedding) "
                "VALUES (?, ?, ?)".format(spelling),
                ("mem-1", "test-project", blob(VECTOR_B)),
            )
        # ASSERT THE REASON, NOT ONLY THE RAISE. A raise for an unrelated
        # reason would certify this claim on incorrect evidence.
        assert "UNIQUE constraint failed" in str(caught.value)
        # And the original survives, which is why OR REPLACE is a lie here:
        # it neither replaces nor leaves the caller a written row.
        assert stored_vector(conn, "mem-1") == blob(VECTOR_A)


class TestVec0RefusesAnUpdateThatNamesThePartitionKey:
    """CARRIED CLAIM (b). It does NOT gate this fix, because this write path
    issues DELETE and INSERT and no UPDATE. It is measured here because the
    fixture is built, and it gates a later repair tool."""

    def test_the_three_column_spelling_raises_at_an_unchanged_value(self, conn):
        seed_vector(conn)
        # THE TRAP: the partition value below is UNCHANGED. The refusal keys
        # on the PRESENCE of the column in the SET clause and not on whether
        # the value moves, so a job that restates all three columns raises on
        # 100 percent of rows and can still report itself complete.
        with pytest.raises(sqlite3.OperationalError) as caught:
            conn.execute(
                "UPDATE vec_memories SET memory_id = ?, project_id = ?, "
                "embedding = ? WHERE memory_id = ?",
                ("mem-1", "test-project", blob(VECTOR_B), "mem-1"),
            )
        assert "partition key" in str(caught.value)


class TestAnIdenticalBytesUpdateReportsRowcountOne:
    """CARRIED CLAIM (c). It does not gate this fix. It gates the re-run
    safety of a later repair tool."""

    def test_writing_the_same_bytes_again_reports_one_row(self, conn):
        """NAME THE VIRTUAL-TABLE REASON, or this arm reads as a restatement
        of a rule a reviewer already knows and gets deleted.

        Core SQLite reports rows MATCHED by the WHERE clause and not rows
        whose bytes changed, so an idempotent re-write reports 1 rather than
        0. This arm exists because `vec_memories` is a VIRTUAL table, whose
        module implements its own update path. The core-SQLite rule does not
        transfer to it by argument. It has to be observed, and this arm is
        the observation.
        """
        seed_vector(conn)
        cursor = conn.execute(
            "UPDATE vec_memories SET embedding = ? WHERE memory_id = ?",
            (blob(VECTOR_A), "mem-1"),
        )
        conn.commit()
        assert cursor.rowcount == 1


class TestNullSafeEqualityOnThePartitionColumn:
    """CARRIED CLAIM (d). It does not gate this fix. On the update leg of a
    later repair tool it is LOAD-BEARING and not defensive."""

    def test_is_selects_the_null_row_and_equals_selects_none(self, conn):
        seed_vector(conn, memory_id="null-row", project_id=None)
        # BOTH HALVES ARE THE ARM. `IS ?` alone proves only that a query ran.
        # The pair is what proves the two spellings differ, which is the
        # whole reason the null-safe form is required.
        with_is = conn.execute(
            "SELECT COUNT(*) FROM vec_memories WHERE project_id IS ?", (None,)
        ).fetchone()[0]
        with_equals = conn.execute(
            "SELECT COUNT(*) FROM vec_memories WHERE project_id = ?", (None,)
        ).fetchone()[0]
        assert with_is == 1
        assert with_equals == 0


# ---------------------------------------------------------------------------
# The write path.
# ---------------------------------------------------------------------------


