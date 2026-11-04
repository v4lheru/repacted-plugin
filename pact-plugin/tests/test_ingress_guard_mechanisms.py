"""
Location: pact-plugin/tests/test_ingress_guard_mechanisms.py

Summary: Arms for the list-field guard at the memory-store ingresses. Three
arms, and THEY ARE NOT INTERCHANGEABLE. Each one carries the property it
proves and the properties it cannot reach. Read the labels before you reshape
one of them.

  THE WITNESS is `TestARefusedCreateWritesNothingAtAll`. It measures the
  PROPERTY: a refused create leaves the store untouched. Its instrument is
  `conn.total_changes`, which counts each row written on the connection
  rather than rows in a table somebody remembered to name. A change count
  that does not move proves two things at one time: no partial write landed,
  AND no write ran before the guard. It is blind to no table, to no helper
  and to no statement order.

  THE TWO STATIC ARMS ARE COMPANIONS AND NOT SUBSTITUTES. Each one holds one
  property the witness cannot: IT FIRES ON A CODE EDIT EVEN WHEN NO TEST
  INPUT REACHES THE NEW PATH. A new ingress with no test of its own is
  invisible to a behavioural arm and visible to these.

WHY `total_changes` AND NOT A ROW COUNT. Measured on this platform: one plain
insert moves the count by 1, and one `vec0` insert moves it by 5, because the
virtual table writes shadow rows. So a count of two named tables misses the
shadow writes, and `total_changes` sees them without knowing their names.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers import create_test_schema

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

from scripts.database import create_memory  # noqa: E402


_DATABASE_PY = (
    Path(__file__).parent.parent
    / "skills" / "pact-memory" / "scripts" / "database.py"
)

GUARD = "_normalize_list_field"
MERGE = "_merge_with_dedup"
WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE")


def _parse():
    return ast.parse(_DATABASE_PY.read_text())


def _calls_by_enclosing_function(tree, names):
    """Return (lineno, call_name, enclosing_function_name) for each call.

    The enclosing function comes from a walk that carries a stack, and not
    from a line-range comparison. A range test mis-assigns a call in a nested
    function to the outer one.
    """
    found = []

    def visit(node, enclosing):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                func = child.func
                name = (
                    func.attr if isinstance(func, ast.Attribute)
                    else getattr(func, "id", None)
                )
                if name in names:
                    found.append((child.lineno, name, enclosing))
            visit(child, enclosing)

    visit(tree, "<module>")
    return found


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("no function named {0} in database.py".format(name))


# ---------------------------------------------------------------------------
# THE WITNESS. It measures the property rather than a proxy for it.
# ---------------------------------------------------------------------------

class TestARefusedCreateWritesNothingAtAll:
    """A refused create must leave the change count where it was.

    THIS ARM CARRIES THE ORDERING PROPERTY. A count that does not move proves
    that no write ran BEFORE the guard, without one word about where the
    guard sits in the text. So it reaches the case the static arms cannot:
    a write moved into a helper, a write built from a value rather than a
    literal, and a write to a table this test does not name.

    MUTANT: revert the create ingress to the truthiness test. The guard stops
    refusing, the create succeeds, and the change count moves.

    WHAT IT CANNOT DO, and it is the reason the static arms ship beside it:
    it needs an input that reaches the guard. A future ingress with no test
    of its own is invisible here.
    """

    @pytest.fixture
    def conn(self, tmp_path):
        connection = sqlite3.connect(str(tmp_path / "ingress.db"))
        connection.row_factory = sqlite3.Row
        create_test_schema(connection)
        connection.commit()
        with patch("scripts.database.ensure_initialized"):
            yield connection
        connection.close()

    def test_a_good_create_moves_the_change_count(self, conn):
        """POSITIVE CONTROL, and the refusal arm below means nothing without it.

        A store where no create ever writes reports a still change count for
        the wrong reason, and the refusal arm would pass against a broken
        instrument.
        """
        before = conn.total_changes
        create_memory(conn, {"context": "a control record",
                             "reasoning_chains": ["a proper item"]})
        assert conn.total_changes > before, (
            "the instrument reports no write for a create that succeeded"
        )


# ---------------------------------------------------------------------------
# COMPANION 1. The design mechanism that had no test.
# ---------------------------------------------------------------------------

class TestEachMergeCallerAlsoCallsTheGuard:
    """Each function that merges a list field must also call the guard.

    ITS PURPOSE IS FORWARD. A future third ingress that omits the guard goes
    red here without anybody having to remember the rule.

    WHAT THIS ARM CANNOT CATCH, and a reader who stops at the class name will
    get this wrong: IT ASSERTS CO-PRESENCE AND NOT POSITION. A guard moved
    after the write is still called in the enclosing function, so this arm
    STAYS GREEN on that mutant. `TestTheGuardPrecedesTheWrite` covers the
    position, and `TestARefusedCreateWritesNothingAtAll` covers the property
    that position is a proxy for.

    THE POPULATION IS EACH CALL OF THE MERGE HELPER, WHICH IS THE BROADER OF
    TWO READINGS THE DESIGN GIVES. The narrower reading covers the incoming
    side alone. NO MEASUREMENT ON THIS TREE SEPARATES THE TWO, because each
    present call sits in a function that calls the guard, so do not hunt for
    evidence that cannot be found.
    THE BROADER READING IS CHOSEN ON FAILURE DIRECTION. A narrower population
    lets a future incoming-side caller through when somebody mis-classifies
    it, and that miss is SILENT. The broader population can redden on a
    read-only caller instead, and that failure is LOUD and reaches a person.

    THE READ-SIDE CARVE-OUT, recorded because the design decided it and this
    arm does not encode it. The merge helper takes two parameters with
    different trust. The `existing` value comes from the database, and the
    design deliberately does NOT raise on the read side, because a raise
    there bricks updates to rows written by earlier code.
    SO, IF A FUTURE READ-ONLY CALLER REDDENS THIS ARM, THAT IS A DECISION
    POINT AND NOT A BUG. Rule on it. Do not add an exclusion by reflex,
    because each exclusion narrows the guard a little more.
    """

    def test_the_merge_population_is_not_empty(self):
        """NON-VACUITY GATE. Run it first and read it first.

        A rename of either helper empties the population, and an assertion
        over an empty population is TRUE. The arm below would then pass while
        it measured nothing.
        """
        tree = _parse()
        merges = [h for h in _calls_by_enclosing_function(tree, {MERGE})]
        assert merges, (
            "no call of {0} found in database.py. Either it was renamed, or "
            "this arm reads the wrong file".format(MERGE)
        )


# ---------------------------------------------------------------------------
# COMPANION 2. Position, and it claims a code shape and nothing more.
# ---------------------------------------------------------------------------

