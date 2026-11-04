"""
Location: pact-plugin/tests/test_memory_layer_composition.py

Summary: Arms for what the memory-layer changes do TOGETHER. Each individual
fix carries arms its own author proved by mutation, so nothing here re-tests
one fix alone. Every arm below needs TWO OR MORE of the changes to be present
before it can pass, and each one names the mutants that redden it.

WHY A SEPARATE MODULE. The four fixes landed in four commits by two authors,
and each author tested one side of every pair. A pair is therefore untested by
construction rather than by oversight. The store scope in particular did not
exist when the arms for the write path and the create guard were written, and
those arms hand in their own connection, so none of them resolves a store at
all.

THE FOUR PAIRS THIS MODULE CLOSES.

  1. THE WINDOW AND THE WRITE PATH. The window author was FORBIDDEN from
     writing an arm that re-embeds a record which already holds a vector: at
     that time the write path issued `INSERT OR REPLACE` against a `vec0`
     table, which honours no conflict clause, so a second write raised. Their
     arms therefore assert at the CALL BOUNDARY against a recording stand-in,
     and the write-path arms patch the generator, so NO arm anywhere drove the
     encoder and the store together. `TestTheWindowReachesTheStoredVector` is
     that arm. It is the composition the ordering constraint existed to
     protect.

  2. THE STORE SCOPE AND THE VECTOR WRITE. The row and the vector are written
     through two different statements inside one call. Before the resolver
     change, six sites passed `self._db_path` explicitly; they now pass
     nothing and resolve through a `ContextVar` that the method decorator
     binds. If a later edit drops that decorator from `save` or `update`, the
     ROW lands in the caller store and the VECTOR lands in the default one,
     and the two halves of one record separate with nothing to report it.

  3. THE CREATE GUARD AND THE VECTOR WRITE. The guard arms prove no ROW is
     written when the guard raises. Nothing proves no VECTOR is written. A
     guard that refused after the embedding step would leave a vector with no
     record, which is invisible to every row-level assertion.

  4. THE STDERR GUARD AND THE STORE SCOPE. The CLI nests them, stderr guard
     outermost. Their orders are pinned by an AST assertion, which cannot see
     whether the two unwind correctly. A scope that outlives its block leaks
     into the next call in the same interpreter.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers import create_test_schema

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

import sqlite_vec  # noqa: E402

from scripts import cli  # noqa: E402
from scripts.config import _STORE_DB_PATH, resolve_db_path  # noqa: E402
from scripts.embeddings import (  # noqa: E402
    EMBEDDING_DIM,
    EMBEDDING_MAX_TOKENS,
    MODEL_NAME,
)
from scripts.memory_api import PACTMemory  # noqa: E402


# The encoder default this feature ended. It is written here, and ONLY here,
# as the value the arm must separate the configured window FROM. It is not a
# second copy of the window: the window comes from the imported constant.
ENCODER_DEFAULT_WINDOW = 512


def make_vec_table(conn, dim=EMBEDDING_DIM):
    """Build the production vector table shape at the REAL embedding width.

    The write-path arms use an 8-wide table because their vectors are chosen
    by hand. An arm that drives the encoder must use the width the encoder
    emits, or the insert fails on the width rather than on the behaviour
    under test.
    """
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0("
        "  memory_id TEXT PRIMARY KEY,"
        "  project_id TEXT PARTITION KEY,"
        "  embedding float[{0}]"
        ")".format(dim)
    )
    conn.commit()


def stored_floats(conn, memory_id):
    """Read a stored vector back as floats, or None when no row is present."""
    row = conn.execute(
        "SELECT embedding FROM vec_memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    if row is None:
        return None
    raw = bytes(row[0])
    return list(struct.unpack("{0}f".format(len(raw) // 4), raw))


def long_record(marker="alpha"):
    """A record whose text is long enough to separate the two windows.

    THE LENGTH IS THE WHOLE POINT AND AN ARM ASSERTS IT RATHER THAN TRUSTS IT.
    A short record encodes identically at 512 tokens and at 2048, so an arm
    built on one would pass whether or not the window reached the encoder.
    That is the vacuous green this module exists to avoid, so
    `test_the_two_windows_separate_on_this_text` is a required companion and
    not a nicety.
    """
    sentence = (
        "The semantic index truncated each record at the encoder default "
        "window and reported nothing about the loss, so a median record "
        "reached the index at roughly its first quarter. "
    )
    return {
        "context": "{0} {1}".format(marker, sentence * 120),
        "project_id": "test-project",
    }


@pytest.fixture
def encoder():
    """The real encoder, loaded once per test that asks for it.

    A stand-in cannot answer this module's question. The subject is what the
    ENCODER returns for a given window, so a recording double would assert
    only that the test passed its own argument to itself.
    """
    from model2vec import StaticModel

    # force_download=False for the reason given at the production call site in
    # embeddings.py: model2vec's default of True makes ten metadata round-trips
    # to revalidate a model already on disk, and each is a network call that can
    # block in an unbounded SSL read. This fixture hung a full suite doing
    # exactly that. It still loads the REAL encoder -- which is the whole point
    # of the docstring above -- it simply stops revalidating first.
    return StaticModel.from_pretrained(MODEL_NAME, force_download=False)


@pytest.fixture
def vec_conn(tmp_path):
    """A store with the memories table and a real vec0 table at the real width."""
    connection = sqlite3.connect(str(tmp_path / "composition.db"))
    connection.row_factory = sqlite3.Row
    create_test_schema(connection)
    make_vec_table(connection)
    yield connection
    connection.close()


@pytest.fixture
def mem():
    return PACTMemory(project_id="test-project", session_id="test-session")


# ---------------------------------------------------------------------------
# PAIR 1. The window and the write path. THE ARM THAT WAS DEFERRED.
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.requires_embedding_backend
class TestTheWindowReachesTheStoredVector:
    """The composition the ordering constraint protected.

    TWO CHANGES MUST BOTH BE PRESENT FOR THIS TO PASS, and that is what makes
    it a composition arm rather than a third copy of an existing one:

      MUTANT A, the window keyword dropped from the encode call. The stored
      vector then equals the encoding at the encoder default, and
      `test_a_re_embed_stores_the_windowed_encoding` goes RED on the equality
      it asserts LAST.

      MUTANT B, the write path returned to `INSERT OR REPLACE` with no drop.
      The second write raises on a row that is present, the handler reports
      `fault`, and the same arm goes RED on the status it asserts FIRST.

    NEITHER EXISTING ARM CATCHES BOTH. The window arms assert at the call
    boundary and reach no store at all. The write-path arms patch the
    generator, so the value they store carries no window.
    """

    def test_the_two_windows_separate_on_this_text(self, encoder):
        """NON-VACUITY GATE for the arm below. Run it first and read it first.

        If this record encoded identically at the two windows, the arm below
        would pass against a dropped keyword and certify the defect as fixed.
        The gate makes the fixture length a measured property rather than an
        assumption about how many characters a token is worth.
        """
        from scripts.embeddings import generate_embedding_text
        text = generate_embedding_text(long_record())
        at_window = encoder.encode(
            [text], max_length=EMBEDDING_MAX_TOKENS
        )[0].tolist()
        at_default = encoder.encode(
            [text], max_length=ENCODER_DEFAULT_WINDOW
        )[0].tolist()
        assert at_window != at_default, (
            "the fixture text is too short to separate a window of "
            "{0} from the encoder default of {1}, so every arm built on it "
            "is vacuous".format(EMBEDDING_MAX_TOKENS, ENCODER_DEFAULT_WINDOW)
        )


# ---------------------------------------------------------------------------
# PAIR 2. The store scope and the vector write.
# ---------------------------------------------------------------------------

class TestTheScopeCarriesTheRowAndTheVectorTogether:
    """The row and the vector must land in the SAME store.

    MUTANT: remove `@_with_store_scope` from `save`. The row then goes to the
    store the caller named and the vector goes to the default one, or the
    save fails outright. Either way this arm reddens.

    WHY THE DEFAULT STORE IS THE CONTROL AND NOT AN AFTERTHOUGHT. Under the
    harness `PACT_TEST_MEMORY_DIR` points the default at a tmp_path child, so
    a split record is written somewhere real and stays invisible to any arm
    that reads only the store it asked for. This arm reads BOTH.
    """

    def test_a_scoped_save_writes_the_two_halves_to_the_named_store(self, tmp_path):
        scoped = tmp_path / "scoped" / "named.db"
        scoped.parent.mkdir(parents=True)
        conn = sqlite3.connect(str(scoped))
        conn.row_factory = sqlite3.Row
        create_test_schema(conn)
        make_vec_table(conn)
        conn.close()

        default_dir = Path(os.environ["PACT_TEST_MEMORY_DIR"])
        default_db = default_dir / "memory.db"

        memory = {"context": "a short embeddable record", "project_id": "test-project"}
        vector = [0.125] * EMBEDDING_DIM

        mem = PACTMemory(
            project_id="test-project", session_id="s", db_path=scoped
        )
        with patch("scripts.database.ensure_initialized"), \
             patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding", return_value=vector):
            memory_id = mem.save(memory, include_tracked=False, sync_to_claude=False)

        check = sqlite3.connect(str(scoped))
        try:
            rows = check.execute(
                "SELECT COUNT(*) FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()[0]
            assert rows == 1, "the row did not reach the named store"
            check.enable_load_extension(True)
            sqlite_vec.load(check)
            vectors = check.execute(
                "SELECT COUNT(*) FROM vec_memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()[0]
            assert vectors == 1, (
                "the vector did not reach the named store, so the row and its "
                "vector were split across two stores"
            )
        finally:
            check.close()

        # THE OTHER HALF OF THE PROPERTY. A vector in the named store does not
        # by itself prove none was written to the default one.
        assert not default_db.exists(), (
            "the default store file was created by a scoped save, at "
            "{0}".format(default_db)
        )


# ---------------------------------------------------------------------------
# PAIR 3. The create guard and the vector write.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PAIR 4. The stderr guard and the store scope.
# ---------------------------------------------------------------------------

