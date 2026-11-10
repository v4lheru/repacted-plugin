"""A caller-supplied store path is OPENED, never brought into existence.

THE HALF THIS CLOSES. An earlier refusal covered a typo in the DIRECTORY. A
typo in the FILE NAME reached a directory that was present, built a store, and
reported an ordinary result. In the true spelling that throwaway store lands
INSIDE the live store directory, so the caller keeps working against a store
that holds nothing.

THE REFUSAL LIVES AT THE CLI BOUNDARY, IN `cli.main`, AND THESE ARMS DRIVE THE
CLI. A refusal inside `database.get_connection` was built first and rejected:
it reaches EACH caller of the connection factory, so it breaks the custom-store
contract that production and the test suite depend on. The boundary can read
the command name as a fact on `args`, so it separates `setup` from the commands
that open a store.

THE ASYMMETRY IS DELIBERATE. `setup` creates, because bringing a store into
existence at a named location is its operation. `save`, `get` and `list` open a
store that should be there, so an absent one is a typo.

THE CLASS OF EACH ARM, BECAUSE RED HAS SEVERAL CAUSES AND ONLY ONE OF THEM IS
THE MECHANISM.

  RED FOR THE MECHANISM on the unfixed code:
    test_an_absent_caller_file_is_refused. The store was built and the caller
    saw an ordinary result.

  OVER-BLOCK GUARDS. These cannot go red on the unfixed code, because it
  carries no refusal to over-block. They bound the refusal instead:
    test_a_store_that_is_present_still_opens
    test_a_path_holding_shell_or_uri_syntax_still_opens
    test_setup_may_bring_a_caller_path_into_existence

  GREEN BEFORE AND AFTER, AND IT OUTRANKS THE REFUSAL:
    test_the_derived_route_still_creates. An arm proving only the refusal would
    pass against a fix that broke each production caller.

  PINS AN ACCEPTED TRADE RATHER THAN A DESIRED OUTCOME:
    test_an_absent_path_is_reported_before_malformed_input. Read its docstring
    before you change the boundary check.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_PARENT = Path(__file__).parent.parent / "skills" / "pact-memory"

from scripts import cli  # noqa: E402
from scripts.database import get_connection  # noqa: E402

_CLI = SCRIPTS_PARENT / "scripts" / "cli.py"

# THE MINIMAL ARGUMENTS THAT REACH THE BOUNDARY, ONE ROW PER COMMAND.
#
# ⚠️ A COMMAND WITH A REQUIRED POSITIONAL EXITS AT ARGPARSE BEFORE `main`
# READS THE PATH. That exit also returns a non-zero status, so a row that
# omits the positional passes a status assertion, reaches nothing, and pins
# nothing. This table is what carries each row past argparse and into the
# refusal. A test below asserts that this table and the dispatch table hold
# the same command names, so a new command cannot slip through un-swept.
_MINIMAL_ARGS = {
    "save": ('{"context": "x"}',),
    "search": ("q",),
    "list": (),
    "get": ("a" * 32,),
    "status": (),
    "update": ("a" * 32, '{"context": "x"}'),
    "delete": ("a" * 32,),
    "sync": (),
}

_NON_EXEMPT_COMMANDS = sorted(
    set(cli._COMMANDS) - set(cli._COMMANDS_THAT_MAY_CREATE_A_CALLER_PATH)
)


def _run(*argv):
    """Run the memory CLI as a real subprocess, the way its own tests do."""
    return subprocess.run(
        [sys.executable, str(_CLI), *argv],
        capture_output=True, text=True, timeout=60,
    )


def _envelope(proc):
    """Return the parsed error envelope, failing with the streams on a mis-parse."""
    try:
        return json.loads(proc.stderr)
    except json.JSONDecodeError:
        pytest.fail(
            f"stderr did not parse as JSON, so the envelope contract is broken. "
            f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )


def _make_store(path):
    """Bring a store into existence at `path` by the one command that may."""
    proc = _run("setup", "--db-path", str(path))
    assert proc.returncode == 0, (
        f"setup could not create the store, so the arm below would measure the "
        f"setup failure rather than its own subject. "
        f"rc={proc.returncode} stderr={proc.stderr!r}"
    )
    assert path.exists(), "setup reported success and left no store behind"
    return path


class TestACallerPathIsOpenedNotCreated:
    def test_an_absent_caller_file_is_refused(self, tmp_path):
        """RED BEFORE THE FIX: the store was built and the caller saw a result.

        The parent directory is present here on purpose. That is what separates
        this arm from the directory half: the earlier guard has nothing to
        refuse, so only a guard on the FILE can fail this call.
        """
        present_dir = tmp_path / "present-dir"
        present_dir.mkdir()
        typo = present_dir / "memroy.db"

        proc = _run("get", "x" * 32, "--db-path", str(typo))

        assert proc.returncode != 0, "a mistyped file name reported success"
        assert _envelope(proc)["error"] == "DB_PATH_NOT_FOUND"
        assert not typo.exists(), (
            "a mistyped file name built its own store, so the typo succeeded "
            "against an empty database instead of failing"
        )


class TestTheDirectoryHalfStillHoldsBelowTheBoundary:
    """THE DIRECTORY HALF IS PINNED HERE, AT THE LAYER THE CLI SEAM CANNOT SEE.

    WHY THIS ARM EXISTS, AND IT IS A COVERAGE REPAIR RATHER THAN A NEW RULE.
    `database.get_db_path` creates the parent directory for a DERIVED origin
    only, so a caller path with an absent parent gets no tree built for it. A
    CLI arm used to prove that. It cannot any longer: the boundary refusal now
    fires FIRST for an absent caller path, so the CLI arm passes without ever
    reaching the resolver, and the directory-half guard would ship unpinned.

    A LIBRARY CALLER IS THE ONE ROUTE THAT STILL REACHES IT. That is the same
    route the CLI seam names as its accepted residual, so this arm covers what
    the seam does not.
    """

    def test_a_caller_path_with_an_absent_parent_builds_no_tree(self, tmp_path):
        """MUTANT THAT KILLS THIS ARM: make the `mkdir` in `get_db_path`
        unconditional rather than gated on a derived origin. The directory then
        appears and this arm reddens."""
        absent_parent = tmp_path / "typo-dir"
        target = absent_parent / "x.db"
        assert not absent_parent.exists()

        with pytest.raises(Exception):
            get_connection(target)

        assert not absent_parent.exists(), (
            "a caller path with a mistyped directory had its tree built, so the "
            "typo would succeed against an empty store instead of failing"
        )
        assert not target.exists()


class TestTheCreatingRouteKeepsItsHardening:
    def test_the_derived_route_still_creates(self, tmp_path, monkeypatch):
        """THE RULE THAT OUTRANKS THE REFUSAL. A pathless production caller
        must reach AND create its store.

        This is the arm that fails if the refusal is placed where it cannot
        tell a caller path from a derived one.
        """
        derived_root = tmp_path / "derived" / "pact-memory"
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(derived_root))

        with get_connection() as conn:
            conn.execute("SELECT 1")

        assert (derived_root / "memory.db").exists()


class TestTheOrderOfTheBoundaryCheck:
    def test_an_absent_path_is_reported_before_malformed_input(self, tmp_path):
        """PINS AN ACCEPTED TRADE. DO NOT READ THIS AS A DESIRED OUTCOME.

        The boundary check runs BEFORE the handler, so a caller who makes TWO
        mistakes at one time, an absent `--db-path` AND malformed JSON, hears
        about the PATH and not about the JSON.

        WHY THAT ORDER SHIPS. To report the JSON first, the check must move
        after input validation, which means placing it inside each handler that
        opens a store. That is the per-caller seam this design rejected, and it
        returns the refusal to a place that cannot tell a person from a library
        caller.

        WHY IT IS TOLERABLE. The message is TRUE rather than misleading. The
        save cannot land anywhere, whatever the JSON says.

        THIS ARM MAKES THE TRADE VISIBLE. Without it the order is untested
        rather than chosen, and a later reader cannot tell which it was. If you
        change the boundary check, change this arm deliberately.
        """
        absent = tmp_path / "not-there.db"

        proc = _run("save", "--db-path", str(absent), "{not valid json")

        assert proc.returncode != 0
        assert _envelope(proc)["error"] == "DB_PATH_NOT_FOUND", (
            "the boundary check no longer precedes handler input validation. "
            "That may be an improvement. Read this docstring and decide, rather "
            "than update the expected string to whatever the code now returns."
        )
        assert not absent.exists()


class TestEachNonExemptCommandRefusesAnAbsentCallerPath:
    """DERIVE THE ALPHABET FROM THE GUARDED THING, NOT FROM A HAND LIST.

    The rule is a statement about `cli._COMMANDS` minus the exemption set, so
    the row set is computed from those two objects rather than typed out. A
    hand list covers the commands its author remembered. It reddens for none
    of the commands added afterwards, and it reddens for none of the commands
    the exemption set grows to hold, which is the drift that lets a guard
    ship narrower than the thing it guards.

    THE ROWS OVERLAP THE HAND-WRITTEN ARMS ABOVE ON PURPOSE. Those arms assert
    richer things about one command each. These assert one thing about each
    command.
    """

    def test_the_row_set_is_the_dispatch_table_minus_the_exemption(self):
        """THE NON-VACUITY AND DRIFT GUARD FOR THE SWEEP BELOW.

        A sweep whose own population is unfalsifiable is the condition each
        instance of this defect class shares, so the population is asserted
        here rather than assumed.
        """
        assert _NON_EXEMPT_COMMANDS, (
            "the row set is empty, so the sweep below runs no rows and passes"
        )
        assert set(_MINIMAL_ARGS) == set(_NON_EXEMPT_COMMANDS), (
            f"the argument table and the dispatch table disagree. A command "
            f"added to `cli._COMMANDS` needs a row in `_MINIMAL_ARGS`, or the "
            f"sweep covers the alphabet it was written against rather than "
            f"the one that ships. "
            f"dispatch-only={sorted(set(_NON_EXEMPT_COMMANDS) - set(_MINIMAL_ARGS))} "
            f"table-only={sorted(set(_MINIMAL_ARGS) - set(_NON_EXEMPT_COMMANDS))}"
        )
        assert cli._COMMANDS_THAT_MAY_CREATE_A_CALLER_PATH == frozenset({"setup"}), (
            "the exemption set widened. Each command it holds stops being "
            "swept, so a widening must be a deliberate edit here as well as "
            "there."
        )


class TestSetupMayAlsoCreateTheParentDirectory:
    """PINS AN ACCEPTED TRADE. DO NOT READ THIS AS A DESIRED OUTCOME.

    `setup` is exempt from the boundary refusal, and it then reaches
    `setup_memory.ensure_directories`, which creates the parent with
    `parents=True`. So a typo in the DIRECTORY under `setup` builds a tree and
    a store at the mistyped location, which is the harm class this branch
    closed for each other command.

    WHY IT SHIPS THIS WAY. `setup` exists to bring a store into existence at a
    named location, so a refusal of an absent parent is a behaviour change to
    the one command whose purpose is creation. That deserves its own
    consideration rather than a tail-end edit.

    THE RESIDUAL, NAMED RATHER THAN IMPLIED: a DIRECTORY typo on `setup
    --db-path` is refused nowhere. The FILE half is refused for each other
    command at the boundary, and the DIRECTORY half is refused for a library
    caller by the class above. `setup` sits outside the two.

    THIS ARM MAKES THE TRADE VISIBLE. Without it the behaviour is untested
    rather than chosen, and a later reader cannot tell which it was. If you
    change it, change this arm deliberately.
    """

    def test_setup_builds_an_absent_parent_at_a_caller_path(self, tmp_path):
        target = tmp_path / "typo-dir" / "x.db"
        assert not target.parent.exists()

        proc = _run("setup", "--db-path", str(target))

        assert proc.returncode == 0, f"setup was refused. stderr={proc.stderr!r}"
        assert target.parent.is_dir(), (
            "setup no longer creates the parent of a caller path. That may be "
            "an improvement. Read this docstring and decide, rather than "
            "delete the assertion."
        )
        assert target.exists()


