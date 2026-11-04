"""The memory store must never resolve to the operator's real store under test.

WHAT THESE TESTS ARE FOR, because a passing suite is not evidence here. The
suite passed with the leak wide open: 13,812 tests green while 60 of them
opened write-capable connections to the live store. So "the tests pass" cannot
distinguish an isolated system from a leaking one, and every assertion below is
written to FAIL on the pre-fix code rather than to describe the post-fix code.

THE TWO MECHANISMS THAT LEAKED, each with a test here:
  1. IMPORT-TIME BINDING. `config` computed its paths from `Path.home()` when
     the module first imported, so a later redirect reached a value that no
     longer existed to change. Protection depended on import ORDER.
  2. THE PROCESS BOUNDARY. An in-process patch does not survive `exec`, so a
     spawned CLI child resolved the real home however carefully the parent had
     been redirected.

WHAT MUST KEEP WORKING, and it outranks the isolation. Production entry points
that pass no path SHOULD reach the real store: that is what `archive_pin
--index N` does, and `/repacted:init prune` keys its refuse-or-proceed decision
on it. A fix that isolates tests by making the default unreachable would refuse
an honest user command, which damages real work rather than merely risking it.
`TestProductionStillResolvesTheRealStore` is that guarantee.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_PARENT = Path(__file__).parent.parent / "skills" / "pact-memory"

from scripts.config import (  # noqa: E402
    MEMORY_DIR_ENV,
    compute_db_path,
    get_memory_dir,
)

REAL_STORE_SUFFIX = os.path.join(".claude", "pact-memory")

# THE OPERATOR'S TRUE HOME, captured at MODULE IMPORT.
#
# Deliberately bound early, which is the opposite of what the fix does, and for
# a reason that does not apply there: this must be the home as it was BEFORE any
# fixture ran. The sibling autouse fixture `_isolate_config_root_to_tmp` patches
# `Path.home()` to a tmp tree for every test, so calling `Path.home()` inside a
# test yields the REDIRECTED home and an assertion built on it compares a tmp
# path against itself. Module import happens at collection, before any fixture,
# so this is the last honest reading available. `os.path.expanduser` is used
# because it is not the attribute the fixture replaces.
_REAL_HOME = Path(os.path.expanduser("~"))

# THE STORE OVERRIDE AS IT STOOD AT COLLECTION, captured at MODULE IMPORT.
#
# Bound early for the same reason as `_REAL_HOME` above, against a different
# axis. Two isolation layers set this variable: `pytest_configure` assigns a
# session-wide store BEFORE collection, and the autouse fixture
# `_isolate_memory_store_to_tmp` then overrides it per test. The fixture runs
# after collection, so BY THE TIME ANY TEST BODY EXECUTES THE SESSION VALUE IS
# GONE -- overwritten, not merely shadowed.
#
# That is why removing the session layer changes NOTHING a test body can see:
# the in-test state is identical either way, and no assertion over identical
# state can distinguish them. Module import is the only moment inside the
# session at which the session layer's own value is still readable, so this
# capture is what makes `TestEachIsolationLayerHasAWitness` possible at all.
_STORE_AT_COLLECTION = os.environ.get(MEMORY_DIR_ENV)


def _session_floor_at_collection():
    """The session store installed by `conftest.pytest_configure`, read now.

    RESOLVED AT COLLECTION FOR TWO SEPARATE REASONS, both measured.

    1. NOT VIA `import conftest`. That statement builds a SECOND module object
       under its own `sys.modules` key and RE-EXECUTES the module body, which
       calls `tempfile.mkdtemp` again. The value it reports is a directory no
       test ever used, so an equality check against it can never pass -- and it
       would fail for a reason that has nothing to do with the layer under test.
       It also leaks an empty directory on every call.
    2. AT COLLECTION, FOR TWO STRUCTURAL REASONS. The per-test fixture overwrites
       this variable before any test body runs, so collection is the last moment
       the session layer's value is readable at all (see `_STORE_AT_COLLECTION`).
       And this helper cannot use `_loaded_conftest` below even if it wanted to:
       `request` is a fixture, so it does not exist at module import, which is
       why the path scan survives beside the deterministic lookup.

       An earlier version of this note justified collection time by the absence
       of a duplicate module, which a re-import in
       `TestTheConftestLiteralMatchesItsSource` used to create. That re-import is
       gone and the justification went with it: collection excludes a duplicate
       made by a test BODY, but not one made at another module's IMPORT time by a
       module collected earlier. No module does that today, which is contingent
       rather than structural. The `len(found) != 1` check below is the detector
       either way.

    Match on the resolved file path rather than on a module name, so this stays
    correct however pytest keys the module.
    """
    target = str((Path(__file__).parent / "conftest.py").resolve())
    found = [
        m for m in list(sys.modules.values())
        if getattr(m, "__file__", None)
        and str(Path(m.__file__).resolve()) == target
    ]
    if len(found) != 1:
        return None
    return getattr(found[0], "_SESSION_MEMORY_DIR", None)


_SESSION_FLOOR_AT_COLLECTION = _session_floor_at_collection()


def _run_child(code: str, env: dict) -> str:
    """Run `code` in a FRESH interpreter and return its stdout, stripped.

    A fresh process is mandatory rather than tidy. The binding under repair is
    evaluated at import, so an in-process check performed after redirecting
    confirms only that the redirect took effect; it cannot observe the defect,
    which is that the value was already fixed before the redirect happened.
    """
    child_env = dict(os.environ)
    child_env.update(env)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=child_env, timeout=60,
    )
    assert result.returncode == 0, f"child failed: {result.stderr}"
    return result.stdout.strip()


_RESOLVE_IN_CHILD = (
    "import sys; sys.path.insert(0, %r)\n"
    "from scripts.config import compute_db_path\n"
    "print(compute_db_path())\n" % str(SCRIPTS_PARENT)
)


def _loaded_conftest(request):
    """Return the conftest module pytest LOADED, never a fresh import.

    A bare `import conftest` does NOT return that module. It builds a SECOND
    object from the same file and re-executes the body, leaving two entries in
    `sys.modules` for one `__file__`. That duplication has already cost twice:
    it contaminated another test's module lookup order-dependently, and it made
    a live detector look dead under mutation, because patching one object left
    the other untouched.

    THE LOOKUP FAILS LOUDLY ON PURPOSE. Every caller compares against the module
    this returns, so a silent `None` would turn those assertions vacuous while
    every arm stayed green -- strictly worse than the fresh import it replaces.
    """
    path = Path(__file__).parent / "conftest.py"
    plugin = request.config.pluginmanager.get_plugin(str(path))
    assert plugin is not None, (
        f"pytest has no conftest plugin registered for {path}, so this helper "
        "cannot reach the harness. Failing here is deliberate: the assertions "
        "that call it would otherwise pass while comparing nothing."
    )
    return plugin


class TestTheConftestLiteralMatchesItsSource:
    """conftest duplicates the variable name; a rename must not un-isolate."""

    def test_conftest_env_name_equals_config_env_name(self, request):
        """The duplicated literal is pinned to its source.

        `conftest.py` cannot import `scripts.config` at load time without making
        the whole suite depend on that package importing cleanly, so it carries
        its own copy of the name. This test is what makes that copy safe: rename
        `MEMORY_DIR_ENV` without updating conftest and this fails loudly, rather
        than every test silently resolving the operator's real store again.

        IT INSPECTS THE LOADED MODULE, AND THE MESSAGE BELOW IS WHY. That message
        asserts a property of THIS RUN -- that every test is resolving the real
        store. Only the object whose `pytest_configure` set the variable, and
        whose autouse fixture sets it per test, can support that claim. A fresh
        `import conftest` proves a fact about the FILE instead, from a copy with
        no causal role in the isolation it is reporting on.
        """
        conftest = _loaded_conftest(request)

        assert conftest._MEMORY_DIR_ENV == MEMORY_DIR_ENV, (
            f"conftest isolates on {conftest._MEMORY_DIR_ENV!r} but the resolver "
            f"honours {MEMORY_DIR_ENV!r}. Every test is running against the real "
            f"store. Update conftest._MEMORY_DIR_ENV to match."
        )


class TestTheSessionDefaultIsUnconditional:
    """The collection-time assignment must not honour an inherited value.

    ADDED BECAUSE A MUTATION FOUND THE GAP, not because it was designed in.
    Changing `pytest_configure`'s assignment to `setdefault` and pre-setting
    `PACT_TEST_MEMORY_DIR` to the real store left every other test in this file
    GREEN, because the per-test autouse fixture overrides the variable anyway.
    The exposure that survives is narrower and still real: anything resolving
    OUTSIDE a fixture's reach — collection-time imports, session-scoped setup —
    would read the operator's own value and point at the store this exists to
    protect. A contributor who has relocated their store exports that variable.

    STRUCTURAL, AND SAID SO PLAINLY. This inspects source text rather than
    behaviour, because the assignment happens before collection and no test can
    observe the pre-collection state from inside the session. It is a guard
    against reintroducing a known fail-open, not a proof of the runtime property.
    """

    def test_pytest_configure_assigns_rather_than_setdefaults(self, request):
        import inspect

        # THIS ASSERTION IS INDIFFERENT TO WHICH OBJECT IT GETS, because
        # `inspect.getsource` resolves through `__file__` and both objects share
        # one. The helper is used anyway: the duplication comes from the IMPORT
        # STATEMENT, not from the assertion, so leaving a bare import here would
        # keep building the second module and removing it from the other test
        # would remove none of the duplication.
        conftest = _loaded_conftest(request)

        source = inspect.getsource(conftest.pytest_configure)
        assert "os.environ[_MEMORY_DIR_ENV] = " in source, (
            "the session-wide memory-store redirect is no longer an "
            "unconditional assignment"
        )
        assert "setdefault(_MEMORY_DIR_ENV" not in source, (
            "`setdefault` honours an inherited PACT_TEST_MEMORY_DIR, so a contributor "
            "who has relocated their own store would have collection-time "
            "resolution point at it. The fail direction is ALLOW and it is silent."
        )


class TestResolutionIsLateNotImportTime:
    """Mechanism 1: the value must not be fixed at import."""

    def test_env_change_after_import_still_moves_the_store(self, tmp_path):
        """FAILS PRE-FIX. `config.DB_PATH` was a constant, so a change made
        after import moved nothing."""
        first = compute_db_path()
        moved = tmp_path / "relocated"
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(MEMORY_DIR_ENV, str(moved))
            second = compute_db_path()
        assert second == moved / "memory.db"
        assert second != first


class TestRedirectCrossesTheProcessBoundary:
    """Mechanism 2: a child interpreter must inherit the redirect."""

    def test_child_process_honours_the_override(self, tmp_path):
        """FAILS PRE-FIX for every spawned CLI test, which was the larger half
        of the exposure: no in-process patch survives `exec`."""
        target = tmp_path / "child-store"
        out = _run_child(_RESOLVE_IN_CHILD, {MEMORY_DIR_ENV: str(target)})
        assert out == str(target / "memory.db")


class TestProductionStillResolvesTheRealStore:
    """CARDINAL. Over-block is worse than the leak it would prevent.

    The leak has damaged nothing to date. A refused good-faith command damages
    the user's work immediately, so these tests rank above the isolation ones.
    """

    def test_no_override_resolves_under_the_real_home(self):
        """A production process sets nothing, and must reach the real store."""
        out = _run_child(
            "import sys; sys.path.insert(0, %r)\n"
            "import os\n"
            "os.environ.pop('PACT_TEST_MEMORY_DIR', None)\n"
            "from scripts.config import compute_db_path\n"
            "print(compute_db_path())\n" % str(SCRIPTS_PARENT),
            {},
        )
        expected = _REAL_HOME / ".claude" / "pact-memory" / "memory.db"
        assert out == str(expected), (
            "a production caller that sets no override must still resolve the "
            "real store; refusing it would break `archive_pin --index N`, which "
            "passes no --db-path on purpose"
        )


_LEFT_THE_FLAG_SET = False


