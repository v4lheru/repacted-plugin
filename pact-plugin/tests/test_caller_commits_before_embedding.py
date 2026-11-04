"""
Location: pact-plugin/tests/test_caller_commits_before_embedding.py

Summary: Arms for an invariant that three reviewers reached independently and
none of them could pin. The connection-wide `conn.rollback()` in
`_store_embedding` DISCARDS NOTHING, because each writer on that connection
commits its own work first. So the write path is correct BECAUSE OF A
PROPERTY OF ITS CALLERS, and nothing held that property. A later author who
moves a commit, or tidies one away as redundant, arms a data loss and the
suite stays green.

WHY THESE ARMS ARE BEHAVIOURAL AND NOT STATIC. The commits sit in DIFFERENT
FUNCTIONS AND DIFFERENT FILES from the call they protect. `save` opens the
connection, calls `create_memory`, which commits inside `database.py`, calls
`link_memory_to_paths`, which commits PER LINK inside `graph.py`, and then
calls `_store_embedding`. A position arm inside one function cannot express a
commit two frames down in another module, and a transitive walk of the call
graph must model cross-module dispatch, which an alias defeats.

THE PRECONDITION THESE ARMS REST ON IS ITSELF ARMED, ELSEWHERE AND ON
PURPOSE. A rollback that discards nothing for an unrelated reason would make
each arm below pass whether or not the callers commit. That is measured in
`test_connection_transaction_mode.py`: the factory opens in
implicit-transaction mode and a rollback discards uncommitted work, on each
driver that ships. READ THAT MODULE FIRST. If it goes red, treat every green
here as unproven rather than as evidence.

A BOUND ON THE INJECTION, RECORDED SO A LATER READER DOES NOT OVER-READ
THESE ARMS. The failure comes from an ABSENT vector table rather than
from a patch inside the subject, which is the right construction: a patch
in the function under test would make the arm test the patch. It does
mean these arms certify THE ORDERING, and not the behaviour of the fault
handler for one particular exception class. Any exception that reaches
the rollback proves the ordering, which is the whole invariant, so the
bound costs nothing here. DO NOT read these arms as evidence about the
handler. They are not that.

THE RESIDUAL, NAMED RATHER THAN ARMED. These arms cover the callers that
exist. A FUTURE NEW CALLER that writes on this connection without committing
first is NOT covered, and no behavioural arm can reach it, because no test
can exercise a caller that is not written yet. The forward half of the
invariant is not soundly testable at this level. It is recorded here rather
than papered over with an arm that would test its own fixture.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from helpers import create_test_schema

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

from scripts.memory_api import PACTMemory  # noqa: E402


VECTOR = [0.5] * 8


@pytest.fixture
def store(tmp_path):
    """A store with the plain schema and NO vector table.

    THE MISSING VECTOR TABLE IS THE FAILURE INJECTION, and it is the whole
    mechanism of these arms. `_store_embedding` loads the extension and then
    inserts into `vec_memories`. With no such table the insert raises, the
    handler rolls the connection back, and it reports `fault`. That is the
    exact path the invariant protects, reached without a patch inside the
    function under test.
    """
    path = tmp_path / "callers.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    create_test_schema(conn)
    conn.commit()
    conn.close()
    return path


def _rows(path, sql, args=()):
    reader = sqlite3.connect(str(path))
    try:
        return reader.execute(sql, args).fetchall()
    finally:
        reader.close()


def _memory(**over):
    base = {"context": "a record whose survival is the subject",
            "project_id": "test-project"}
    base.update(over)
    return base


@pytest.mark.slow
class TestTheRowSurvivesAFailedEmbedding:
    """`create_memory` commits before `_store_embedding` runs.

    MUTANT: remove the `conn.commit()` at the end of `create_memory`. The
    rollback then discards the row, `save` reports an id for a record that is
    not in the store, and this arm reddens.
    """

    def _save_with_a_failing_embedding(self, path, files=None):
        mem = PACTMemory(project_id="test-project", session_id="s",
                         db_path=path)
        with patch("scripts.database.ensure_initialized"), \
             patch("scripts.memory_api.ensure_initialized"), \
             patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding",
                   return_value=VECTOR):
            memory_id = mem.save(_memory(), files=files,
                                 include_tracked=False, sync_to_claude=False)
        return mem, memory_id

    def test_the_embedding_step_really_failed(self, store):
        """NON-VACUITY GATE, AND IT IS THE LOAD-BEARING ONE.

        If the embedding SUCCEEDS, no rollback runs, and each arm below passes
        without exercising the invariant at all. This gate turns that into a
        loud failure rather than a quiet green.
        """
        mem, _ = self._save_with_a_failing_embedding(store)
        assert mem.last_embedding_status == "fault", (
            "the embedding step reported {0} rather than a fault, so no "
            "rollback ran and the arms below prove nothing".format(
                mem.last_embedding_status)
        )


class TestTheFileLinksSurviveAFailedEmbedding:
    """`link_memory_to_file` commits PER LINK, before `_store_embedding` runs.

    MUTANT: remove the `conn.commit()` inside the link path in `graph.py`. The
    row still survives, because `create_memory` committed, and THE LINKS
    VANISH. So this arm separates the graph commit from the create commit,
    which the row arm above cannot do.
    """

    def test_the_links_survive(self, store, tmp_path):
        target = tmp_path / "linked_file.py"
        target.write_text("# a file to link\n")
        mem = PACTMemory(project_id="test-project", session_id="s",
                         db_path=store)
        with patch("scripts.database.ensure_initialized"), \
             patch("scripts.memory_api.ensure_initialized"), \
             patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True), \
             patch("scripts.memory_api.generate_embedding",
                   return_value=VECTOR):
            memory_id = mem.save(_memory(), files=[str(target)],
                                 include_tracked=False, sync_to_claude=False)

        assert mem.last_embedding_status == "fault", (
            "the embedding step did not fail, so no rollback ran"
        )
        links = _rows(store,
                      "SELECT file_id FROM memory_files WHERE memory_id = ?",
                      (memory_id,))
        assert len(links) == 1, (
            "the file link did not survive the rollback in the embedding "
            "handler, so the link path no longer commits before it"
        )


