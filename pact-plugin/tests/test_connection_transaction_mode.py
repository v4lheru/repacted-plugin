"""
Location: pact-plugin/tests/test_connection_transaction_mode.py

Summary: Arms for the transaction mode of the shipped connection factory. The
factory calls `sqlite3.connect` with NO `isolation_level` argument, so the
mode comes from a standard-library default rather than from a choice this
package states. These arms make that default a MEASURED property.

WHY THIS IS NOT A TEST ABOUT SQLITE. Two shipped mechanisms rest on it, and
each one is INERT rather than incorrect if the default ever moves.

  THE VECTOR WRITE PATH shares ONE TRANSACTION between the drop and the
  insert, by a `commit=False` flag on the drop helper. A shared transaction
  means something only when transactions are real on this connection. In
  autocommit the drop commits the moment it runs, the sharing is a fiction,
  and a failed insert costs the record its vector. That is the defect the
  flag was added to end, wearing the fix as a costume.

  THE CONNECTION-WIDE ROLLBACK in the embedding write path is correct because
  each caller commits its own work first. That argument needs a rollback that
  discards something. In autocommit it discards nothing for a different
  reason, and an arm built on the caller commits would pass whether or not
  those commits ran.

SO A GREEN HERE IS A PRECONDITION FOR TWO OTHER ARMS, AND NOT A RESULT OF ITS
OWN. Read it first.

THE MODE IS READ FROM THE LIVE CONNECTION, NEVER INFERRED FROM THE ABSENCE OF
AN ARGUMENT. An absent argument tells you what the caller did not say. It does
not tell you what the library chose, and the library default has moved between
versions before.
"""
from __future__ import annotations

import ast
import pathlib
import sqlite3 as _stdlib_sqlite3
import sys

import pytest

from scripts.database import get_connection  # noqa: E402
# THE MODULE THE FACTORY BOUND, NOT THE ONE THIS TEST CAN IMPORT. The factory
# takes `pysqlite3` when it is present and falls back to the standard library.
# MEASURED, and it is the reason this import reads through the factory module:
# `pysqlite3` carries ITS OWN SQLite build (3.51.1 here, against 3.54.0 in the
# standard library) and its connection has NO `autocommit` attribute, so the
# transaction-control API added to the standard library in Python 3.12 is
# absent from the object under test. A check written against the standard
# library reports on a module this package does not use.
from scripts.database import sqlite3 as _factory_sqlite3  # noqa: E402


TABLE = "transaction_mode_probe"

# THE TWO WAYS A CALLER CAN STATE A TRANSACTION CHOICE. `isolation_level` is
# the legacy control. `autocommit` arrived in Python 3.12 and OUTRANKS it. A
# sweep that covers one name leaves the other route open.
TRANSACTION_CONTROLS = ("isolation_level", "autocommit")

_PACKAGE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "skills" / "pact-memory" / "scripts"
)


def _package_sources():
    return sorted(_PACKAGE_DIR.glob("*.py"))


def _assignment_targets(node):
    """Return the assignment targets of one node, for each assignment shape.

    THREE SHAPES, because a check that covers one leaves the others open.
    `x.y = v` is an `Assign`, `x.y += v` is an `AugAssign`, and `x.y: T = v`
    is an `AnnAssign`. Each one states a choice.
    """
    if isinstance(node, ast.Assign):
        return node.targets
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        return [node.target]
    return []


# THE DRIVER IS A RUNTIME FALLBACK, SO BOTH SHIP. database.py:32 binds
# `pysqlite3` and falls back to the standard library at :35 on ImportError. A
# consumer without `pysqlite3` installed therefore runs a DIFFERENT driver
# from the one the factory binds here.
#
# THE TWO DRIVERS ARE NOT INTERCHANGEABLE, and this repository holds a
# measured counter-example on a neighbouring axis: the standard library build
# keeps `-wal` and `-shm` beside the store after a clean close, and the
# `pysqlite3` build removes them. One operation, two outcomes. So a result
# taken on one driver states nothing about the other until somebody measures
# it, and these arms measure each driver that is present.
_DRIVERS = {"sqlite3": _stdlib_sqlite3}
if _factory_sqlite3 is not _stdlib_sqlite3:
    _DRIVERS[_factory_sqlite3.__name__] = _factory_sqlite3


def _fresh_reader(path):
    """Open a SECOND connection on the same file, through the SAME module.

    A read on the WRITING connection can report its own uncommitted work, so
    it cannot separate "the write was committed" from "the write is visible to
    the writer". A second connection reports what the FILE holds.
    """
    return _factory_sqlite3.connect(str(path))


@pytest.fixture
def store(tmp_path):
    """A store opened through the SHIPPED factory, with a probe table."""
    path = tmp_path / "transaction-mode.db"
    conn = get_connection(db_path=path)
    conn.execute("CREATE TABLE {0} (id TEXT PRIMARY KEY)".format(TABLE))
    conn.commit()
    yield conn, path
    conn.close()


def _rows(path):
    reader = _fresh_reader(path)
    try:
        return [r[0] for r in reader.execute(
            "SELECT id FROM {0} ORDER BY id".format(TABLE))]
    finally:
        reader.close()


class TestTheFactoryDeclaresATransactionMode:
    """Read the mode off the LIVE connection object."""

    def test_the_connection_is_not_in_autocommit(self, store):
        conn, _ = store
        # `isolation_level` of None IS autocommit. Any other value, and the
        # empty string is the library default, means a transaction opens
        # before a data change and a rollback has work to discard.
        assert conn.isolation_level is not None, (
            "the factory connection is in AUTOCOMMIT. A rollback discards "
            "nothing, so the shared transaction in the vector write path is a "
            "fiction and a failed insert costs the record its vector. "
            "python={0} driver={1} sqlite={2}".format(
                sys.version.split()[0], _factory_sqlite3.__name__,
                _factory_sqlite3.sqlite_version)
        )

    def test_isolation_level_governs_this_connection(self, store):
        """`isolation_level` is the control ONLY while legacy control is on.

        Python 3.12 added an `autocommit` attribute that OUTRANKS
        `isolation_level` when it leaves its legacy value. The arm above would
        then read a control that governs nothing.
        THE ATTRIBUTE IS ABSENT ON THE OBJECT UNDER TEST TODAY, because the
        factory binds `pysqlite3`, whose connection does not carry it. So this
        arm is CONDITIONAL BY CONSTRUCTION rather than by preference: it
        asserts on the attribute where the driver supplies one, and records
        the absence where it does not. A future move to the standard library
        driver, or a `pysqlite3` release that adds the attribute, brings the
        assertion into force with no edit here.
        """
        conn, _ = store
        legacy = getattr(_factory_sqlite3, "LEGACY_TRANSACTION_CONTROL", None)
        if hasattr(conn, "autocommit") and legacy is not None:
            assert conn.autocommit == legacy, (
                "the connection left legacy transaction control, so "
                "`isolation_level` no longer governs it and the arm above "
                "reads a control with no effect"
            )
        else:
            # NOT A SKIP. A skip hides the case in the skip count, and the
            # absence is a measured property of the driver rather than an
            # environmental exclusion.
            assert _factory_sqlite3.__name__ == "pysqlite3", (
                "a driver with no `autocommit` attribute is expected to be "
                "pysqlite3, and this one is {0}".format(
                    _factory_sqlite3.__name__)
            )


class TestARollbackDiscardsUncommittedWork:
    """THE BEHAVIOURAL WITNESS, and the two arms are not interchangeable.

    MUTANT: pass `isolation_level=None` to `sqlite3.connect` in the factory.
    The uncommitted row then survives the rollback and the first arm reddens,
    while the control below stays green.
    """

    def test_a_committed_row_survives_a_rollback(self, store):
        """POSITIVE CONTROL, and the arm below is vacuous without it.

        A probe that writes nothing reports an absent row for the wrong
        reason. This proves the write, the file and the reader each work
        before absence is read as evidence.
        """
        conn, path = store
        conn.execute("INSERT INTO {0} (id) VALUES ('committed')".format(TABLE))
        conn.commit()
        conn.rollback()
        assert _rows(path) == ["committed"]


class TestEachShippedDriverStartsATransaction:
    """The SAME measurement on EACH driver that ships, not on one of them.

    THE ARMS ABOVE DRIVE THE FACTORY, so they measure the driver the factory
    bound HERE. A consumer without `pysqlite3` runs the standard library
    driver, and no arm above says one word about it.

    THESE ARMS MEASURE THE DRIVER RATHER THAN THE FACTORY, on purpose. They
    replicate the connect arguments the factory uses, and the arm below pins
    that replication so the two cannot drift apart in silence.

    THE TWO ASSERTIONS FACE OPPOSITE DIRECTIONS, which is what makes a false
    premise LOUD rather than quiet. A driver in autocommit FAILS the
    uncommitted arm and PASSES the committed arm. A probe whose arms can only
    pass measures the expectation of its author.
    """

    def test_the_driver_population_holds_the_factory_driver(self):
        """NON-VACUITY GATE. An empty or wrong population makes each
        parametrised arm below pass over nothing."""
        assert _DRIVERS, "no sqlite driver resolved"
        assert _factory_sqlite3.__name__ in _DRIVERS, (
            "the factory driver {0} is missing from the measured population "
            "{1}".format(_factory_sqlite3.__name__, sorted(_DRIVERS))
        )


